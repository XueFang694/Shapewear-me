"""
Connecteur SKIMS v2.1 — parsing JSON-LD depuis pages HTML de collection.

CONTEXTE
────────
SKIMS utilise Shopify Hydrogen (frontend headless) qui bloque tous les
endpoints Shopify publics :
  - /collections/<slug>/products.json  → 403/404
  - /products/<handle>.json            → 404

STRATÉGIE
─────────
1. GET /collections/<slug>  → HTML avec JSON-LD embarqué
2. Extraction des produits depuis les blocs <script application/ld+json>
3. Pagination cursor : le JSON-LD expose une propriété "nextPage" avec
   l'URL de la page suivante (ex: /en-fr/collections/shapewear?cursor=eyJ...)
4. Cache interne handle → données produit
5. get_product_urls() retourne des URLs virtuelles skims://product/<handle>
6. ScrapingEngine détecte le schéma non-HTTP et appelle parse_product()
   directement sans fetch réseau (cf. engine.py)
7. Zéro fetch produit individuel : toutes les données viennent du JSON-LD

STRUCTURE JSON-LD SKIMS (Shopify Hydrogen) :
  {
    "@type": "Product",
    "name":  "Seamless Sculpt Thong Bodysuit",
    "url":   "https://skims.com/en-fr/products/seamless-sculpt-thong-bodysuit",
    "image": ["https://cdn.shopify.com/..."],
    "offers": {
      "@type": "AggregateOffer",
      "lowPrice": "88.00",
      "priceCurrency": "USD",
      "offers": [
        {
          "@type": "Offer",
          "url": "https://skims.com/en-fr/products/seamless-sculpt-thong-bodysuit-onyx",
          "price": "88.00",
          "availability": "http://schema.org/InStock"
        }
      ]
    }
  }
  + bloc separé { "@type": "CollectionPage", "nextPage": "https://..." }

Le handle produit propre (sans couleur) est dans item["url"],
PAS dans offers[i]["url"] qui contient le slug de variante avec coloris.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.skims.mappings import extract_best_seller_skims, map_category_skims
from app.scraping.shopify_utils import normalize_price
from app.core.exceptions import ConnectorParseError
from app.core.logger import get_logger

log = get_logger(__name__)
_CONFIG_PATH = Path(__file__).parent / "config.yml"

_JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_SKIMS_BS_TAGS = frozenset({
    "best seller", "bestseller", "best-seller",
    "top rated", "fan favorite", "fan-favourite",
    "best-seller-collection",
})


class SkimsConnector(BaseConnector):
    """Connecteur SKIMS v2.1 — JSON-LD + URLs virtuelles, zéro fetch .json."""

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)
        self._product_cache: dict[str, dict] = {}

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="SKIMS", slug="skims", version="2.1",
            engine="shopify_json", base_url=self.base_url,
        )

    def get_categories(self) -> list[Category]:
        return [
            Category(
                slug=slug,
                name=slug.replace("-", " ").title(),
                url=f"{self.base_url}/collections/{slug}",
                brand_slug="skims",
            )
            for slug in self._config.get("target_collections", [])
        ]

    def get_product_urls(self, category: Category) -> list[str]:
        """
        Pagine les pages HTML via cursor JSON-LD.
        Retourne des URLs virtuelles skims://product/<handle>.
        """
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )

        max_pages     = self._config.get("pagination", {}).get("max_pages", 50)
        seen:         set[str]  = set()
        virtual_urls: list[str] = []
        next_url:     str | None = f"{self.base_url}/collections/{category.slug}"
        page_num      = 0

        while next_url and page_num < max_pages:
            page_num += 1
            try:
                resp = client.get(next_url)
            except Exception as exc:
                log.error("SKIMS erreur requête", category=category.slug, page=page_num, error=str(exc))
                break

            if resp.status_code == 404:
                log.warning("SKIMS collection 404", category=category.slug, url=next_url)
                break
            if resp.status_code != 200:
                log.warning("SKIMS collection inaccessible", category=category.slug, status=resp.status_code)
                break

            products, next_page_url = self._extract_from_json_ld(resp.text)
            new_this_page = 0

            for p in products:
                handle = p.get("handle", "")
                if not handle or handle in seen:
                    continue
                seen.add(handle)
                self._product_cache[handle] = p
                virtual_urls.append(f"skims://product/{handle}")
                new_this_page += 1

            log.debug(
                "SKIMS page collection traitée",
                category=category.slug, page=page_num,
                new_products=new_this_page, total_handles=len(virtual_urls),
            )

            if not next_page_url or new_this_page == 0:
                break
            next_url = next_page_url

        log.info(
            "URLs SKIMS collectées",
            category=category.slug, count=len(virtual_urls), pages=page_num,
        )
        return virtual_urls

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        """
        - skims://product/<handle> → cache (chemin normal, zéro HTTP)
        - dict Shopify → parsing direct (tests)
        - str HTML → extraction JSON-LD (fallback)
        """
        if url.startswith("skims://product/"):
            handle = url.removeprefix("skims://product/")
            product_data = self._product_cache.get(handle)
            if product_data is None:
                raise ConnectorParseError(
                    f"Produit absent du cache SKIMS : {handle}",
                    context={"url": url},
                )
            return self._build_raw_product(product_data)

        if isinstance(data, dict):
            return self._parse_shopify_dict(url, data)

        if isinstance(data, str):
            products, _ = self._extract_from_json_ld(data)
            if products:
                return self._build_raw_product(products[0])
            raise ConnectorParseError("Impossible de parser le HTML SKIMS", context={"url": url})

        raise ConnectorParseError(f"Type inattendu : {type(data)}", context={"url": url})

    # ── Extraction JSON-LD ────────────────────────────────────────────────

    def _extract_from_json_ld(self, html: str) -> tuple[list[dict], str | None]:
        """
        Parse tous les blocs JSON-LD de la page.
        Retourne (products, next_page_url).
        """
        products:      list[dict] = []
        next_page_url: str | None = None

        for m in _JSON_LD_RE.finditer(html):
            raw = m.group(1).strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            items = data if isinstance(data, list) else [data]

            for item in items:
                if not isinstance(item, dict):
                    continue

                dtype = item.get("@type", "")

                if dtype == "Product":
                    p = self._parse_json_ld_product(item)
                    if p:
                        products.append(p)

                elif dtype == "ItemList":
                    for el in item.get("itemListElement", []):
                        node = el.get("item", el) if isinstance(el, dict) else el
                        if isinstance(node, dict) and node.get("@type") == "Product":
                            p = self._parse_json_ld_product(node)
                            if p:
                                products.append(p)

                # nextPage peut être sur n'importe quel bloc JSON-LD
                np = item.get("nextPage") or item.get("next")
                if isinstance(np, str) and np.startswith("http"):
                    next_page_url = np

        if products:
            log.debug("SKIMS handles extraits via JSON-LD", count=len(products), has_next=bool(next_page_url))

        return products, next_page_url

    def _parse_json_ld_product(self, item: dict) -> dict | None:
        """
        Extrait les données d'un objet JSON-LD Product SKIMS.

        IMPORTANT : le handle est extrait depuis item["url"] (URL produit
        canonique, sans couleur), jamais depuis les URLs d'offres/variantes
        qui contiennent le coloris (ex: ...-onyx, ...-clay).
        """
        product_url = item.get("url", "")
        if not product_url:
            return None

        handle = self._url_to_handle(product_url)
        if not handle:
            return None

        title = item.get("name", "").strip()
        if not title:
            return None

        # ── Prix et disponibilité ─────────────────────────────────────────
        price        = None
        compare      = None
        availability = "in_stock"
        offers_raw   = item.get("offers", {})

        if isinstance(offers_raw, dict):
            otype = offers_raw.get("@type", "")
            if otype == "AggregateOffer":
                price = normalize_price(
                    offers_raw.get("lowPrice") or offers_raw.get("price")
                )
                sub = offers_raw.get("offers", [])
                if sub and isinstance(sub, list) and isinstance(sub[0], dict):
                    avail = sub[0].get("availability", "")
                    if "OutOfStock" in avail or "Discontinued" in avail:
                        availability = "out_of_stock"
            elif otype == "Offer":
                price = normalize_price(offers_raw.get("price"))
                compare = normalize_price(offers_raw.get("highPrice"))
                if "OutOfStock" in offers_raw.get("availability", ""):
                    availability = "out_of_stock"

        elif isinstance(offers_raw, list) and offers_raw:
            first = offers_raw[0]
            if isinstance(first, dict):
                price   = normalize_price(first.get("price"))
                compare = normalize_price(first.get("highPrice") or first.get("compareAtPrice"))
                if "OutOfStock" in first.get("availability", ""):
                    availability = "out_of_stock"

        # ── Images ───────────────────────────────────────────────────────
        images: list[dict] = []
        for img in (item.get("image", []) if isinstance(item.get("image"), list) else [item.get("image", "")]):
            if isinstance(img, str) and img.startswith("http"):
                images.append({"src": img})
            elif isinstance(img, dict):
                src = img.get("url") or img.get("src", "")
                if src:
                    images.append({"src": src})

        # ── ID produit ───────────────────────────────────────────────────
        product_id = item.get("productID") or item.get("sku") or ""
        if not product_id:
            gid = str(item.get("@id") or item.get("identifier", ""))
            if "gid://shopify/Product/" in gid:
                product_id = gid.split("/")[-1]

        return {
            "handle":           handle,
            "title":            title,
            "price":            price,
            "compare_at_price": compare if (compare and price and compare > price) else None,
            "images":           images,
            "tags":             [],
            "id":               str(product_id),
            "product_type":     item.get("category") or item.get("productType"),
            "availability":     availability,
            "_raw":             item,
        }

    # ── Constructeur RawProduct ───────────────────────────────────────────

    def _build_raw_product(self, p: dict) -> RawProduct:
        handle  = p.get("handle", "")
        price   = p.get("price")
        compare = p.get("compare_at_price")
        on_sale = bool(compare and price and compare > price)
        tags    = p.get("tags", [])
        images  = [img["src"] for img in p.get("images", []) if isinstance(img, dict) and img.get("src")]

        return RawProduct(
            external_id=p.get("id") or handle,
            url=f"{self.base_url}/products/{handle}",
            name=p.get("title", "").strip(),
            brand_slug="skims",
            price=price,
            original_price=compare if on_sale else None,
            currency="USD",
            on_sale=on_sale,
            category_raw=p.get("product_type") or self._category_from_handle(handle),
            description=None,
            images=images,
            sizes=[],
            colors=[],
            variants=[],
            availability=p.get("availability", "in_stock"),
            extra={
                "handle":         handle,
                "tags":           tags,
                "vendor":         "skims",
                "is_best_seller": self._is_best_seller(tags),
                "materials":      {},
            },
        )

    def _parse_shopify_dict(self, url: str, p: dict) -> RawProduct:
        """Compatibilité tests : parse un dict JSON Shopify standard."""
        from app.scraping.shopify_utils import (
            clean_description, extract_colors, extract_materials,
            extract_variants_detailed, normalize_availability,
        )
        variants = p.get("variants", [])
        tags_raw = p.get("tags", [])
        tags = [t.strip() for t in tags_raw.split(",")] if isinstance(tags_raw, str) else list(tags_raw)
        fv      = variants[0] if variants else {}
        price   = normalize_price(fv.get("price"))
        compare = normalize_price(fv.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)
        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="skims",
            price=price,
            original_price=compare if on_sale else None,
            currency="USD", on_sale=on_sale,
            category_raw=p.get("product_type") or next((t for t in tags if map_category_skims(t)), None),
            description=clean_description(p.get("body_html")),
            images=[img["src"] for img in p.get("images", []) if img.get("src")],
            sizes=[], colors=extract_colors(variants),
            variants=extract_variants_detailed(variants, p.get("options", [])),
            availability=normalize_availability(variants),
            extra={
                "handle": p.get("handle"), "tags": tags, "vendor": p.get("vendor"),
                "is_best_seller": extract_best_seller_skims(tags, self._config.get("best_seller_tags")),
                "materials": extract_materials(p.get("body_html")),
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _url_to_handle(url: str) -> str:
        """
        Extrait le handle depuis une URL SKIMS, en retirant le locale.
        https://skims.com/en-fr/products/seamless-sculpt-thong-bodysuit
          → seamless-sculpt-thong-bodysuit
        """
        if "/products/" not in url:
            return ""
        return url.split("/products/")[-1].strip("/").split("?")[0]

    @staticmethod
    def _category_from_handle(handle: str) -> str | None:
        """Déduit la catégorie depuis le handle produit."""
        h = handle.lower()
        if "bodysuit" in h:               return "bodywear"
        if "bra" in h:                    return "bras"
        if "thong" in h or "brief" in h or "cheekini" in h or "underwear" in h:
                                          return "underwear"
        if "short" in h:                  return "shorts"
        if "legging" in h or "pant" in h: return "leggings"
        if "swim" in h:                   return "swim"
        if "cami" in h or "tank" in h:    return "loungewear"
        return None

    def _is_best_seller(self, tags: list[str]) -> bool:
        config_tags = {t.lower() for t in self._config.get("best_seller_tags", [])}
        return any(t.strip().lower() in (_SKIMS_BS_TAGS | config_tags) for t in tags)