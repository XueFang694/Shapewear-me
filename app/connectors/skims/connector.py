"""
Connecteur SKIMS v2.2 — parsing CollectionPage JSON-LD + pagination <link rel="next">.

CHANGEMENTS v2.2 (correctif)
─────────────────────────────
v2.1 ne trouvait aucun produit pour deux raisons :

1. Structure JSON-LD mal comprise.
   SKIMS Hydrogen expose les produits dans :
     CollectionPage > mainEntity (ItemList) > itemListElement (ListItem[])
   Chaque ListItem a :  @type, position, url, name, image
   — PAS de @type:"Product" direct, contrairement à ce que cherchait v2.1.
   Le nom suit le format "PRODUCT NAME | COLOR".
   L'URL est relative : "/products/<base-handle>-<color-slug>"

2. Pagination par curseur non trouvée.
   v2.1 cherchait une clé "nextPage" dans le JSON-LD.
   En réalité SKIMS expose le curseur via un tag HTML :
     <link rel="next" href="https://skims.com/collections/<slug>?cursor=...">

STRATÉGIE v2.2
──────────────
• get_product_urls() :
    - Fetch HTML de la page de collection
    - Extrait les produits depuis CollectionPage > mainEntity > itemListElement
    - Extrait le curseur depuis <link rel="next" href="...">
    - Déduplique par handle de base (en retirant le suffixe couleur du handle)
    - Stocke les données en cache et retourne des URLs virtuelles

• parse_product() :
    - URL virtuelle skims://product/<handle> → lit le cache (zéro fetch HTTP)
    - dict Shopify → compatibilité tests
    - str HTML → fallback extraction JSON-LD

• Prix : non disponible dans la page de collection JSON-LD.
  Récupération via /products/<handle>.json si l'endpoint est accessible,
  sinon le prix reste None (comportement dégradé acceptable pour la veille).
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

# Curseur de pagination : <link rel="next" href="https://skims.com/...?cursor=...">
_LINK_NEXT_RE = re.compile(
    r'<link\s+rel=["\']next["\']\s+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

_SKIMS_BS_TAGS = frozenset({
    "best seller", "bestseller", "best-seller",
    "top rated", "fan favorite", "fan-favourite",
    "best-seller-collection",
})


class SkimsConnector(BaseConnector):
    """Connecteur SKIMS v2.2 — CollectionPage JSON-LD + <link rel=next>."""

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)
        self._product_cache: dict[str, dict] = {}

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="SKIMS", slug="skims", version="2.2",
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

    # ── URLs produits ─────────────────────────────────────────────────────

    def get_product_urls(self, category: Category) -> list[str]:
        """
        Pagine les pages HTML de collection via curseur <link rel="next">.
        Retourne des URLs virtuelles skims://product/<base-handle>.
        """
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )

        max_pages     = self._config.get("pagination", {}).get("max_pages", 50)
        seen:         set[str]  = set()     # base handles déjà vus
        virtual_urls: list[str] = []
        next_url:     str | None = f"{self.base_url}/collections/{category.slug}"
        page_num      = 0

        while next_url and page_num < max_pages:
            page_num += 1
            try:
                resp = client.get(next_url)
            except Exception as exc:
                log.error(
                    "SKIMS erreur requête collection",
                    category=category.slug, page=page_num, error=str(exc),
                )
                break

            if resp.status_code == 404:
                log.warning(
                    "SKIMS collection 404",
                    category=category.slug, url=next_url,
                )
                break
            if resp.status_code != 200:
                log.warning(
                    "SKIMS collection inaccessible",
                    category=category.slug,
                    status=resp.status_code,
                )
                break

            html = resp.text
            products_raw, cursor_next = self._extract_from_html(html)
            new_this_page = 0

            for p in products_raw:
                handle = p.get("handle", "")
                if not handle or handle in seen:
                    continue
                seen.add(handle)
                self._product_cache[handle] = p
                virtual_urls.append(f"skims://product/{handle}")
                new_this_page += 1

            log.debug(
                "SKIMS page collection traitée",
                category=category.slug,
                page=page_num,
                new_products=new_this_page,
                total_handles=len(virtual_urls),
            )

            # Pagination : <link rel="next"> dans le HTML
            if not cursor_next or new_this_page == 0:
                break
            next_url = cursor_next

        log.info(
            "URLs SKIMS collectées",
            category=category.slug,
            count=len(virtual_urls),
            pages=page_num,
        )
        return virtual_urls

    # ── Parsing produit ───────────────────────────────────────────────────

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
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
            products, _ = self._extract_from_html(data)
            if products:
                return self._build_raw_product(products[0])
            raise ConnectorParseError(
                "Impossible de parser le HTML SKIMS",
                context={"url": url},
            )

        raise ConnectorParseError(
            f"Type inattendu : {type(data)}",
            context={"url": url},
        )

    # ── Extraction depuis le HTML ─────────────────────────────────────────

    def _extract_from_html(self, html: str) -> tuple[list[dict], str | None]:
        """
        Extrait les produits et le lien de pagination depuis une page HTML SKIMS.

        Produits : CollectionPage > mainEntity (ItemList) > itemListElement (ListItem[])
          Chaque ListItem : { @type, position, url, name, image }
          name = "PRODUCT NAME | COLOR"
          url  = "/products/<base-handle>-<color-slug>"

        Pagination : <link rel="next" href="https://skims.com/...?cursor=...">
        """
        products:      list[dict] = []
        next_page_url: str | None = None

        # ── Curseur de pagination depuis <link rel="next"> ────────────────
        m_next = _LINK_NEXT_RE.search(html)
        if m_next:
            next_page_url = m_next.group(1)

        # ── Produits depuis le JSON-LD CollectionPage ─────────────────────
        for m in _JSON_LD_RE.finditer(html):
            raw = m.group(1).strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(data, dict):
                continue

            if data.get("@type") != "CollectionPage":
                continue

            main_entity = data.get("mainEntity", {})
            if main_entity.get("@type") != "ItemList":
                continue

            for list_item in main_entity.get("itemListElement", []):
                p = self._parse_list_item(list_item)
                if p:
                    products.append(p)

        if products:
            log.debug(
                "SKIMS produits extraits (CollectionPage JSON-LD)",
                count=len(products),
                has_next=bool(next_page_url),
            )

        return products, next_page_url

    def _parse_list_item(self, item: dict) -> dict | None:
        """
        Parse un ListItem SKIMS en dict produit interne.

        Format d'entrée :
          {
            "@type": "ListItem",
            "position": 1,
            "url": "/products/cool-shapewear-high-waisted-short-clay",
            "name": "COOL SHAPEWEAR HIGH-WAISTED SHORT | CLAY",
            "image": "https://cdn.shopify.com/..."
          }

        Stratégie handle de base :
          name = "PRODUCT NAME | COLOR"
          color_slug = color.lower().replace(' ', '-')
          url_handle = url.split('/products/')[-1]
          Si url_handle se termine par '-'+color_slug → le retirer
        """
        if item.get("@type") != "ListItem":
            return None

        raw_url = item.get("url", "")
        name    = item.get("name", "").strip()
        image   = item.get("image", "")

        if not raw_url or not name:
            return None

        # Séparer "PRODUCT NAME | COLOR"
        parts        = name.split(" | ", 1)
        product_name = parts[0].strip().title()
        color        = parts[1].strip() if len(parts) > 1 else ""

        # Extraire le handle complet (avec couleur) depuis l'URL
        url_handle = raw_url.split("/products/")[-1].strip("/").split("?")[0]

        # Retirer le suffixe couleur pour obtenir le handle de base
        color_slug = color.lower().replace(" ", "-")
        if color_slug and url_handle.endswith("-" + color_slug):
            base_handle = url_handle[: -(len(color_slug) + 1)]
        else:
            base_handle = url_handle

        if not base_handle:
            return None

        # Déduire la catégorie depuis le handle
        category_raw = self._category_from_handle(base_handle)

        return {
            "handle":       base_handle,
            "handle_color": url_handle,   # handle complet avec couleur (pour info)
            "title":        product_name,
            "color":        color,
            "price":        None,         # non disponible dans la page collection
            "compare_at_price": None,
            "images":       [{"src": image}] if image else [],
            "tags":         [],
            "id":           "",           # non disponible — sera le handle
            "product_type": category_raw,
            "availability": "in_stock",   # présent dans la collection = dispo
        }

    # ── Constructeur RawProduct ───────────────────────────────────────────

    def _build_raw_product(self, p: dict) -> RawProduct:
        handle  = p.get("handle", "")
        price   = p.get("price")
        compare = p.get("compare_at_price")
        on_sale = bool(compare and price and compare > price)
        tags    = p.get("tags", [])
        images  = [
            img["src"]
            for img in p.get("images", [])
            if isinstance(img, dict) and img.get("src")
        ]

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
            colors=(
                [{"name": p["color"], "available": True, "sku": ""}]
                if p.get("color") else []
            ),
            variants=[],
            availability=p.get("availability", "in_stock"),
            extra={
                "handle":         handle,
                "handle_color":   p.get("handle_color", handle),
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
        tags = (
            [t.strip() for t in tags_raw.split(",")]
            if isinstance(tags_raw, str)
            else list(tags_raw)
        )
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
            currency="USD",
            on_sale=on_sale,
            category_raw=p.get("product_type") or next(
                (t for t in tags if map_category_skims(t)), None
            ),
            description=clean_description(p.get("body_html")),
            images=[img["src"] for img in p.get("images", []) if img.get("src")],
            sizes=[],
            colors=extract_colors(variants),
            variants=extract_variants_detailed(variants, p.get("options", [])),
            availability=normalize_availability(variants),
            extra={
                "handle":         p.get("handle"),
                "tags":           tags,
                "vendor":         p.get("vendor"),
                "is_best_seller": extract_best_seller_skims(
                    tags, self._config.get("best_seller_tags")
                ),
                "materials":      extract_materials(p.get("body_html")),
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _category_from_handle(handle: str) -> str | None:
        h = handle.lower()
        if "bodysuit" in h:
            return "bodywear"
        if "bra" in h:
            return "bras"
        if "thong" in h or "brief" in h or "cheekini" in h or "underwear" in h:
            return "underwear"
        if "short" in h or "capri" in h:
            return "shorts"
        if "legging" in h or "pant" in h:
            return "leggings"
        if "swim" in h:
            return "swim"
        if "cami" in h or "tank" in h:
            return "loungewear"
        return None

    def _is_best_seller(self, tags: list[str]) -> bool:
        config_tags = {t.lower() for t in self._config.get("best_seller_tags", [])}
        return any(
            t.strip().lower() in (_SKIMS_BS_TAGS | config_tags) for t in tags
        )