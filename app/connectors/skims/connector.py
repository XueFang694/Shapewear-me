"""
Connecteur SKIMS v2.0 — parsing HTML SSR (pages de collection).

CONTEXTE
────────
SKIMS utilise un frontend headless (Shopify Hydrogen ou Next.js) qui bloque
tous les endpoints Shopify publics :
  - /collections/<slug>/products.json  → 403/404
  - /products.json                     → 403/404
  - /products/<handle>.json            → 403 (Cloudflare)

STRATÉGIE v2.0
──────────────
Identique à ShapermintConnector v2.0 :
  1. GET /collections/<slug>?page=N  → HTML SSR
  2. Extraction du payload JSON embarqué depuis plusieurs sources possibles :
     a. __NEXT_DATA__  (Next.js)
     b. window.__remixContext  (Hydrogen/Remix)
     c. <script type="application/json"> avec données produit
     d. JSON-LD  (<script type="application/ld+json">)
  3. Les données sont mises en cache interne (slug → données produit).
  4. get_product_urls() retourne des URLs virtuelles skims://product/<slug>.
  5. parse_product() récupère depuis le cache, sans fetch supplémentaire.

Ce qui est disponible dans les pages HTML SKIMS :
  - title, handle, price, compare_at_price
  - images, colors/sizes (si exposées dans le payload)
  - tags / best-seller status
  - ID produit (si disponible)

Disponibilité : tout produit visible dans la collection = in_stock
(SKIMS masque les produits sold-out dans les listes de collection).
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

# ── Patterns d'extraction du payload JSON embarqué ────────────────────────

# Next.js
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

# Hydrogen / Remix : window.__remixContext = {...}
_REMIX_CONTEXT_RE = re.compile(
    r'window\.__remixContext\s*=\s*(\{.*?\});\s*(?:window\.|</script>)',
    re.DOTALL,
)

# Script application/json générique (Shopify sections JSON)
_APP_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

# JSON-LD Product
_JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

# Shopify state embarqué (Hydrogen)
_SHOPIFY_STATE_RE = re.compile(
    r'<script[^>]+data-shopify[^>]*>(.*?)</script>',
    re.DOTALL,
)

# Produits dans un tableau JS inline (pattern fréquent sur les headless Shopify)
_PRODUCTS_ARRAY_RE = re.compile(
    r'"products"\s*:\s*(\[.*?\])\s*[,}]',
    re.DOTALL,
)

# Tags best-seller SKIMS
_SKIMS_BS_TAGS = frozenset({
    "best seller", "bestseller", "best-seller",
    "top rated", "fan favorite", "fan-favourite",
    "best-seller-collection",
})


class SkimsConnector(BaseConnector):
    """
    Connecteur SKIMS v2.0 — extraction depuis pages HTML SSR.
    """

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)
        # Cache interne : slug → dict données produit
        self._product_cache: dict[str, dict] = {}

    # ── Métadonnées ───────────────────────────────────────────────────────

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="SKIMS",
            slug="skims",
            version="2.0",
            engine="shopify_json",
            base_url=self.base_url,
        )

    # ── Catégories ────────────────────────────────────────────────────────

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
        Pagine les pages HTML de collection SKIMS, extrait les produits
        depuis le payload embarqué, les met en cache et retourne des URLs
        virtuelles skims://product/<slug>.
        """
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )

        pg_cfg    = self._config.get("pagination", {})
        max_pages = pg_cfg.get("max_pages", 50)
        page_size = pg_cfg.get("page_size", 24)
        base_url  = f"{self.base_url}/collections/{category.slug}"

        seen: set[str] = set()
        virtual_urls: list[str] = []

        for page_num in range(1, max_pages + 1):
            url = f"{base_url}?page={page_num}" if page_num > 1 else base_url

            try:
                resp = client.get(url)
            except Exception as exc:
                log.error(
                    "SKIMS erreur requête collection",
                    category=category.slug, page=page_num, error=str(exc),
                )
                break

            if resp.status_code == 404:
                log.warning(
                    "SKIMS collection introuvable (404)",
                    category=category.slug,
                    url=url,
                )
                break

            if resp.status_code != 200:
                log.warning(
                    "SKIMS collection inaccessible",
                    category=category.slug, page=page_num,
                    status=resp.status_code,
                )
                break

            products, total_pages = self._extract_products_from_html(
                resp.text, category.slug
            )

            if not products:
                log.debug(
                    "SKIMS page vide ou payload non reconnu",
                    category=category.slug, page=page_num,
                )
                break

            new_this_page = 0
            for p in products:
                slug = p.get("slug") or p.get("handle", "")
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                self._product_cache[slug] = p
                virtual_urls.append(f"skims://product/{slug}")
                new_this_page += 1

            log.debug(
                "SKIMS page collection traitée",
                category=category.slug,
                page=f"{page_num}/{total_pages}",
                new_products=new_this_page,
                total_slugs=len(virtual_urls),
            )

            # Arrêt si dernière page ou page sans nouveaux produits
            if page_num >= total_pages or new_this_page == 0:
                break

        log.info(
            "URLs SKIMS (SSR HTML)",
            category=category.slug,
            count=len(virtual_urls),
        )
        return virtual_urls

    # ── Parsing produit ───────────────────────────────────────────────────

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        """
        Parse un produit SKIMS.

        - URL virtuelle skims://product/<slug> → cache interne (chemin normal)
        - dict Shopify standard → parsing direct (tests unitaires)
        - str HTML → extraction depuis le payload embarqué (fallback)
        """
        # Chemin normal v2.0 : URL virtuelle + cache
        if url.startswith("skims://product/"):
            slug = url.removeprefix("skims://product/")
            product_data = self._product_cache.get(slug)
            if product_data is None:
                raise ConnectorParseError(
                    f"Produit absent du cache SKIMS : {slug}",
                    context={"url": url},
                )
            return self._parse_product_data(url, product_data)

        # Compatibilité : dict Shopify standard (tests)
        if isinstance(data, dict):
            return self._parse_shopify_dict(url, data)

        # Fallback HTML
        if isinstance(data, str):
            products, _ = self._extract_products_from_html(data, "unknown")
            if products:
                return self._parse_product_data(url, products[0])
            raise ConnectorParseError(
                "Impossible de parser la page HTML SKIMS",
                context={"url": url},
            )

        raise ConnectorParseError(
            f"Type de données inattendu : {type(data)}",
            context={"url": url},
        )

    # ── Extraction du payload HTML ────────────────────────────────────────

    def _extract_products_from_html(
        self, html: str, category_slug: str
    ) -> tuple[list[dict], int]:
        """
        Tente d'extraire une liste de produits depuis le HTML d'une page
        de collection SKIMS en essayant plusieurs formats dans l'ordre.

        Retourne (products, total_pages).
        """
        # 1. __NEXT_DATA__ (Next.js)
        result = self._try_next_data(html, category_slug)
        if result[0]:
            return result

        # 2. window.__remixContext (Hydrogen/Remix)
        result = self._try_remix_context(html, category_slug)
        if result[0]:
            return result

        # 3. Shopify state embarqué
        result = self._try_shopify_state(html)
        if result[0]:
            return result

        # 4. JSON-LD Product (données structurées SEO)
        result = self._try_json_ld(html)
        if result[0]:
            return result

        # 5. Tableau products[] inline dans un script quelconque
        result = self._try_products_array(html)
        if result[0]:
            return result

        log.warning(
            "SKIMS : aucun format de payload reconnu dans la page HTML",
            category=category_slug,
            html_size=len(html),
        )
        return [], 0

    def _try_next_data(
        self, html: str, category_slug: str
    ) -> tuple[list[dict], int]:
        """Extraction depuis __NEXT_DATA__ (Next.js)."""
        m = _NEXT_DATA_RE.search(html)
        if not m:
            return [], 0
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return [], 0

        pp = data.get("props", {}).get("pageProps", {})

        # Pattern Shapermint-style
        products = pp.get("products", [])
        pagination = pp.get("pagination", {})
        total_pages = int(pagination.get("total_pages", 1))
        if products:
            log.debug("SKIMS __NEXT_DATA__ (Shapermint-style)", count=len(products))
            return self._normalize_next_products(products), total_pages

        # Pattern SKIMS spécifique : collection.products.nodes
        collection = pp.get("collection") or pp.get("data", {}).get("collection", {})
        if collection:
            nodes = (
                collection.get("products", {}).get("nodes", [])
                or collection.get("products", {}).get("edges", [])
            )
            if nodes:
                # edges : chaque élément a un champ "node"
                if nodes and "node" in nodes[0]:
                    nodes = [n["node"] for n in nodes]
                page_info = (
                    collection.get("products", {})
                    .get("pageInfo", {})
                )
                has_next = page_info.get("hasNextPage", False)
                total_pages = 999 if has_next else 1
                log.debug(
                    "SKIMS __NEXT_DATA__ (Shopify Storefront nodes)",
                    count=len(nodes),
                )
                return self._normalize_storefront_products(nodes), total_pages

        # Pattern générique : chercher "products" à n'importe quel niveau
        products = self._deep_find_products(pp)
        if products:
            log.debug(
                "SKIMS __NEXT_DATA__ (deep search)", count=len(products)
            )
            return self._normalize_generic_products(products), 1

        return [], 0

    def _try_remix_context(
        self, html: str, category_slug: str
    ) -> tuple[list[dict], int]:
        """Extraction depuis window.__remixContext (Hydrogen/Remix)."""
        m = _REMIX_CONTEXT_RE.search(html)
        if not m:
            return [], 0
        try:
            # Le JSON Remix peut être tronqué par la regex → tenter quand même
            raw = m.group(1)
            # Trouver la fin du JSON par comptage d'accolades
            raw = self._extract_balanced_json(raw)
            if not raw:
                return [], 0
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [], 0

        # Chercher les produits dans loaderData
        loader = data.get("state", {}).get("loaderData", {}) or data.get("loaderData", {})
        for key, val in loader.items():
            if not isinstance(val, dict):
                continue
            products = self._deep_find_products(val)
            if products:
                log.debug(
                    "SKIMS __remixContext", loader_key=key, count=len(products)
                )
                return self._normalize_storefront_products(products), 1

        return [], 0

    def _try_shopify_state(self, html: str) -> tuple[list[dict], int]:
        """Extraction depuis les scripts data-shopify (Hydrogen)."""
        for m in _SHOPIFY_STATE_RE.finditer(html):
            try:
                data = json.loads(m.group(1))
                products = self._deep_find_products(data)
                if products:
                    log.debug(
                        "SKIMS data-shopify script", count=len(products)
                    )
                    return self._normalize_generic_products(products), 1
            except (json.JSONDecodeError, Exception):
                continue
        return [], 0

    def _try_json_ld(self, html: str) -> tuple[list[dict], int]:
        """Extraction depuis les blocs JSON-LD (données structurées SEO)."""
        products: list[dict] = []
        for m in _JSON_LD_RE.finditer(html):
            try:
                data = json.loads(m.group(1))
                # Peut être un objet unique ou une liste
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") in ("Product", "ItemList"):
                        if item.get("@type") == "ItemList":
                            for el in item.get("itemListElement", []):
                                if isinstance(el, dict) and el.get("@type") == "Product":
                                    products.append(self._from_json_ld(el))
                        else:
                            products.append(self._from_json_ld(item))
            except (json.JSONDecodeError, Exception):
                continue
        if products:
            log.debug("SKIMS JSON-LD", count=len(products))
        return products, 1

    def _try_products_array(self, html: str) -> tuple[list[dict], int]:
        """Extraction depuis un tableau products[] inline dans un script."""
        m = _PRODUCTS_ARRAY_RE.search(html)
        if not m:
            return [], 0
        try:
            products = json.loads(m.group(1))
            if products and isinstance(products, list):
                log.debug(
                    "SKIMS products[] inline", count=len(products)
                )
                return self._normalize_generic_products(products), 1
        except (json.JSONDecodeError, Exception):
            pass
        return [], 0

    # ── Normaliseurs de format produit ───────────────────────────────────

    def _normalize_next_products(self, products: list[dict]) -> list[dict]:
        """Normalise les produits au format Shapermint-style __NEXT_DATA__."""
        result = []
        for p in products:
            if not isinstance(p, dict):
                continue
            slug = p.get("slug") or p.get("handle") or p.get("url", "").split("/")[-1]
            if not slug:
                continue
            result.append({
                "slug":             slug,
                "title":            p.get("title") or p.get("name", ""),
                "price":            p.get("price") or p.get("min_price"),
                "compare_at_price": p.get("compare_at_price"),
                "images":           p.get("images", []),
                "tags":             p.get("tags", []),
                "id":               p.get("id") or p.get("vendor_product", {}).get("product_id"),
                "product_type":     p.get("product_type") or p.get("type"),
                "review_score":     p.get("review_score"),
                "product_dimensions": p.get("product_dimensions", []),
                "_raw":             p,
            })
        return result

    def _normalize_storefront_products(self, nodes: list[dict]) -> list[dict]:
        """
        Normalise les produits au format Shopify Storefront API (GraphQL nodes).
        Structure : { id, title, handle, priceRange, variants, images, tags, ... }
        """
        result = []
        for p in nodes:
            if not isinstance(p, dict):
                continue
            handle = p.get("handle", "")
            if not handle:
                continue

            # Prix depuis priceRange ou variants
            price = None
            compare = None
            price_range = p.get("priceRange") or p.get("compareAtPriceRange", {})
            if price_range:
                min_price = price_range.get("minVariantPrice") or {}
                price = normalize_price(min_price.get("amount"))
            if not price:
                # Fallback : première variante
                variants = (
                    p.get("variants", {}).get("nodes")
                    or p.get("variants", {}).get("edges", [])
                    or []
                )
                if variants:
                    fv = variants[0]
                    if "node" in fv:
                        fv = fv["node"]
                    price = normalize_price(
                        fv.get("price", {}).get("amount") if isinstance(fv.get("price"), dict)
                        else fv.get("price")
                    )
                    compare = normalize_price(
                        fv.get("compareAtPrice", {}).get("amount")
                        if isinstance(fv.get("compareAtPrice"), dict)
                        else fv.get("compareAtPrice")
                    )

            # Images
            images_raw = (
                p.get("images", {}).get("nodes")
                or p.get("images", {}).get("edges", [])
                or p.get("images", [])
            )
            images = []
            for img in images_raw:
                if isinstance(img, dict):
                    src = img.get("url") or img.get("src") or (img.get("node") or {}).get("url")
                    if src:
                        images.append({"src": src})
                elif isinstance(img, str):
                    images.append({"src": img})

            # Tags
            tags = p.get("tags", [])

            result.append({
                "slug":             handle,
                "title":            p.get("title", ""),
                "price":            price,
                "compare_at_price": compare,
                "images":           images,
                "tags":             tags,
                "id":               p.get("id", ""),
                "product_type":     p.get("productType") or p.get("product_type"),
                "_raw":             p,
            })
        return result

    def _normalize_generic_products(self, products: list[dict]) -> list[dict]:
        """
        Normalisation générique : tente de détecter le format et délègue
        au bon normaliseur.
        """
        if not products:
            return []
        sample = products[0]
        # Storefront API : a "handle" et potentiellement "priceRange"
        if "handle" in sample and ("priceRange" in sample or "variants" in sample):
            return self._normalize_storefront_products(products)
        # Shapermint-style : a "slug"
        if "slug" in sample:
            return self._normalize_next_products(products)
        # Format Shopify JSON standard (title + variants)
        if "variants" in sample and isinstance(sample.get("variants"), list):
            return self._normalize_shopify_json_products(products)
        # Dernier recours
        return self._normalize_next_products(products)

    def _normalize_shopify_json_products(
        self, products: list[dict]
    ) -> list[dict]:
        """Normalise les produits au format JSON Shopify standard."""
        result = []
        for p in products:
            handle = p.get("handle", "")
            if not handle:
                continue
            variants = p.get("variants", [])
            fv = variants[0] if variants else {}
            price = normalize_price(fv.get("price"))
            compare = normalize_price(fv.get("compare_at_price"))
            images = [
                {"src": img["src"]}
                for img in p.get("images", [])
                if img.get("src")
            ]
            result.append({
                "slug":             handle,
                "title":            p.get("title", ""),
                "price":            price,
                "compare_at_price": compare,
                "images":           images,
                "tags":             p.get("tags", []),
                "id":               str(p.get("id", "")),
                "product_type":     p.get("product_type"),
                "_raw":             p,
            })
        return result

    def _from_json_ld(self, item: dict) -> dict:
        """Convertit un objet JSON-LD Product en format normalisé interne."""
        slug = item.get("url", "").split("/products/")[-1].strip("/") or ""
        offers = item.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = normalize_price(offers.get("price"))
        images = []
        img = item.get("image")
        if isinstance(img, list):
            images = [{"src": i} if isinstance(i, str) else {"src": i.get("url", "")} for i in img]
        elif isinstance(img, str):
            images = [{"src": img}]
        return {
            "slug":             slug,
            "title":            item.get("name", ""),
            "price":            price,
            "compare_at_price": None,
            "images":           images,
            "tags":             [],
            "id":               item.get("productID", ""),
            "product_type":     item.get("category"),
            "_raw":             item,
        }

    # ── Constructeur RawProduct ───────────────────────────────────────────

    def _parse_product_data(self, virtual_url: str, p: dict) -> RawProduct:
        """Construit un RawProduct depuis le dict normalisé en cache."""
        slug = p.get("slug", "")
        tags = p.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]

        price   = normalize_price(p.get("price"))
        compare = normalize_price(p.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)

        # Images
        images_raw = p.get("images", [])
        images: list[str] = []
        for img in images_raw:
            if isinstance(img, dict):
                src = img.get("src") or img.get("url", "")
                if src:
                    images.append(src)
            elif isinstance(img, str) and img.startswith("http"):
                images.append(img)

        # Avis clients (format Shapermint-style)
        rs = p.get("review_score") or {}
        rating       = float(rs["reviews_average"]) if rs.get("reviews_average") else None
        review_count = int(rs["reviews_count"])     if rs.get("reviews_count")   else None

        # Couleurs depuis product_dimensions (Shapermint-style)
        colors: list[dict] = []
        for dim in p.get("product_dimensions", []):
            if dim.get("name", "").lower() in ("color", "colour", "couleur", "shade"):
                colors = [
                    {
                        "name":      v["name"],
                        "available": True,
                        "sku":       str(v.get("variant_id", "")),
                    }
                    for v in dim.get("values", [])
                    if v.get("name")
                ]
                break

        # Catégorie
        category_raw = (
            p.get("product_type")
            or self._category_from_tags(tags)
        )

        # ID externe : préférer l'ID numérique Shopify
        raw_id = p.get("id", "")
        # Les IDs Storefront API sont de la forme "gid://shopify/Product/123456"
        if isinstance(raw_id, str) and "gid://" in raw_id:
            raw_id = raw_id.split("/")[-1]
        external_id = str(raw_id) if raw_id else slug

        is_best_seller = self._is_best_seller(tags)
        product_url = f"{self.base_url}/products/{slug}"

        return RawProduct(
            external_id=external_id,
            url=product_url,
            name=p.get("title", "").strip(),
            brand_slug="skims",
            price=price,
            original_price=compare if on_sale else None,
            currency="USD",
            on_sale=on_sale,
            category_raw=category_raw,
            description=None,
            images=images,
            sizes=[],
            colors=colors,
            variants=[],
            availability="in_stock",
            rating=rating,
            review_count=review_count,
            extra={
                "handle":         slug,
                "tags":           tags,
                "vendor":         "skims",
                "is_best_seller": is_best_seller,
                "materials":      {},
            },
        )

    def _parse_shopify_dict(self, url: str, p: dict) -> RawProduct:
        """
        Compatibilité ascendante : parse un dict JSON Shopify standard.
        Utilisé dans les tests unitaires.
        """
        from app.scraping.shopify_utils import (
            clean_description, extract_colors, extract_materials,
            extract_variants_detailed, normalize_availability,
        )

        variants = p.get("variants", [])
        options  = p.get("options", [])
        tags_raw = p.get("tags", [])
        tags: list[str] = (
            [t.strip() for t in tags_raw.split(",")]
            if isinstance(tags_raw, str)
            else list(tags_raw)
        )

        fv      = variants[0] if variants else {}
        price   = normalize_price(fv.get("price"))
        compare = normalize_price(fv.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)

        category_raw = (
            p.get("product_type")
            or next((t for t in tags if map_category_skims(t)), None)
        )
        materials         = extract_materials(p.get("body_html"))
        detailed_variants = extract_variants_detailed(variants, options)
        availability      = normalize_availability(variants)

        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="skims",
            price=price,
            original_price=compare if on_sale else None,
            currency="USD",
            on_sale=on_sale,
            category_raw=category_raw,
            description=clean_description(p.get("body_html")),
            images=[img["src"] for img in p.get("images", []) if img.get("src")],
            sizes=[],
            colors=extract_colors(variants),
            variants=detailed_variants,
            availability=availability,
            extra={
                "handle":         p.get("handle"),
                "tags":           tags,
                "vendor":         p.get("vendor"),
                "is_best_seller": extract_best_seller_skims(
                    tags, self._config.get("best_seller_tags")
                ),
                "materials":      materials,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _category_from_tags(self, tags: list[str]) -> str | None:
        """Déduit la catégorie depuis les tags SKIMS."""
        for tag in tags:
            mapped = map_category_skims(tag)
            if mapped:
                return tag
        return None

    def _is_best_seller(self, tags: list[str]) -> bool:
        """Détecte le statut best-seller depuis les tags SKIMS."""
        config_tags = {t.lower() for t in self._config.get("best_seller_tags", [])}
        check = _SKIMS_BS_TAGS | config_tags
        return any(t.strip().lower() in check for t in tags)

    @staticmethod
    def _deep_find_products(data: Any, depth: int = 0) -> list[dict]:
        """
        Cherche récursivement une clé "products" contenant une liste
        de dicts dans un objet JSON imbriqué.
        """
        if depth > 6 or not isinstance(data, (dict, list)):
            return []

        if isinstance(data, dict):
            # Clé "products" directe
            val = data.get("products") or data.get("productList")
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val
            # Chercher dans les sous-objets
            for v in data.values():
                result = SkimsConnector._deep_find_products(v, depth + 1)
                if result:
                    return result

        elif isinstance(data, list):
            for item in data:
                result = SkimsConnector._deep_find_products(item, depth + 1)
                if result:
                    return result

        return []

    @staticmethod
    def _extract_balanced_json(text: str) -> str | None:
        """
        Extrait un objet JSON complet depuis une chaîne en comptant
        les accolades ouvrantes/fermantes.
        """
        if not text or not text.startswith("{"):
            return None
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[: i + 1]
        return None