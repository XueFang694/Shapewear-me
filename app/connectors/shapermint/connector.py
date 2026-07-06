"""Connecteur Shapermint v5.2 — collecte des avis via product_id + page_size=1000.

CHANGEMENTS v5.2
────────────────

SIMPLIFICATION DE _collect_reviews()
--------------------------------------
La logique de pagination multi-pages (uuid + ~5 avis/page + fallback Stamped)
est remplacée par une seule requête :

    GET https://api.shapermint.com/reviews
        ?product_id=<shopify_product_id>
        &page_size=1000

Cette URL retourne jusqu'à 1 000 avis en une passe, ce qui couvre la quasi-
totalité des catalogues. Le paramètre `max_review_pages` du config.yml n'est
plus utilisé pour la pagination mais reste lu pour activer/désactiver la
collecte (0 = désactivé).

Le paramètre `product_id` est l'ID Shopify numérique du produit
(ex: 7252332773510), exposé dans __NEXT_DATA__ sous la clé product.id.

La déduplification par ID reste en place pour fusionner proprement les
éventuels avis SSR pré-rendus avec les avis retournés par l'API.

Toutes les autres fonctionnalités (parsing collection, variantes, matériaux,
best-seller, etc.) sont inchangées par rapport à v5.1.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.shapermint.mappings import extract_best_seller_sm, map_category_sm
from app.scraping.shopify_utils import normalize_price
from app.core.exceptions import ConnectorParseError
from app.core.logger import get_logger

log = get_logger(__name__)
_CONFIG_PATH = Path(__file__).parent / "config.yml"

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

_SM_BS_TAGS = frozenset({
    "product-label-best-seller",
    "section-best-seller",
    "winning-product",
    "Collection-Best-Sellers",
    "best seller",
    "bestseller",
    "top seller",
    "popular",
})

_SUBCAT_TO_CATEGORY: dict[str, str] = {
    "subcat-bodysuits":        "bodysuits",
    "subcat-shorts":           "shorts",
    "subcat-leggings":         "leggings",
    "subcat-bras":             "bras",
    "subcat-panties":          "underwear",
    "subcat-camis-tanks":      "tanks-camis",
    "subcat-camis":            "tanks-camis",
    "subcat-tanks":            "tanks-camis",
    "subcat-clothing-tops":    "tanks-camis",
    "subcat-clothing-bottoms": "leggings",
}

_SALE_TAGS = frozenset({"SALE", "product-label-sale"})

_COMPRESSION_MAP: dict[str, str] = {
    "LOW":        "Légère",
    "LIGHT":      "Légère",
    "MEDIUM":     "Moyenne",
    "MODERATE":   "Moyenne",
    "HIGH":       "Forte",
    "FIRM":       "Forte",
    "EXTRA HIGH": "Extra-forte",
    "EXTRA FIRM": "Extra-forte",
    "MAXIMUM":    "Extra-forte",
}

# Endpoint API Shapermint — collecte des avis par product_id
_SM_REVIEWS_API = "https://api.shapermint.com/reviews"

# Nombre maximum d'avis à demander en une requête
_REVIEWS_PAGE_SIZE = 1000


class ShapermintConnector(BaseConnector):
    """Connecteur Shapermint v5.2."""

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)

    # ── Métadonnées ───────────────────────────────────────────────────────

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="Shapermint",
            slug="shapermint",
            version="5.2",
            engine="html",
            base_url=self.base_url,
        )

    # ── Catégories ────────────────────────────────────────────────────────

    def get_categories(self) -> list[Category]:
        return [
            Category(
                slug=slug,
                name=slug.replace("-", " ").title(),
                url=f"{self.base_url}/collections/{slug}",
                brand_slug="shapermint",
            )
            for slug in self._config.get("target_collections", [])
        ]

    # ── URLs produits ─────────────────────────────────────────────────────

    def get_product_urls(self, category: Category) -> list[str]:
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )

        pg_cfg    = self._config.get("pagination", {})
        max_pages = pg_cfg.get("max_pages", 20)
        base_url  = f"{self.base_url}/collections/{category.slug}"

        seen: set[str]  = set()
        urls: list[str] = []

        for page_num in range(1, max_pages + 1):
            page_url = f"{base_url}?page={page_num}" if page_num > 1 else base_url
            try:
                resp = client.get(page_url)
            except Exception as exc:
                log.error("Shapermint erreur collection", category=category.slug, page=page_num, error=str(exc))
                break

            if resp.status_code != 200:
                log.warning("Shapermint collection inaccessible", category=category.slug, page=page_num, status=resp.status_code)
                break

            products_raw, total_pages = self._extract_collection_products(resp.text)
            if not products_raw:
                break

            new_this_page = 0
            for p in products_raw:
                slug = p.get("slug", "")
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                urls.append(f"{self.base_url}/products/{slug}")
                new_this_page += 1

            if page_num >= total_pages:
                break

        log.info("URLs Shapermint", category=category.slug, count=len(urls))
        return urls

    # ── Parsing produit ───────────────────────────────────────────────────

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        if isinstance(data, str):
            return self._parse_from_html(url, data)
        if isinstance(data, dict):
            return self._parse_shopify_dict(url, data)
        raise ConnectorParseError(f"Type inattendu : {type(data)}", context={"url": url})

    # ── Parser HTML principal ─────────────────────────────────────────────

    def _parse_from_html(self, url: str, html: str) -> RawProduct:
        m = _NEXT_DATA_RE.search(html)
        if not m:
            raise ConnectorParseError("Shapermint __NEXT_DATA__ introuvable", context={"url": url})
        try:
            nd_data = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            raise ConnectorParseError(f"Shapermint JSON invalide : {exc}", context={"url": url}) from exc

        pp = nd_data.get("props", {}).get("pageProps", {})
        product = pp.get("product")
        if not product:
            raise ConnectorParseError("Shapermint product absent de pageProps", context={"url": url})

        ssr_reviews = pp.get("ssrReviews") or {}
        return self._build_raw_product(url, product, ssr_reviews)

    # ── Construction du RawProduct ────────────────────────────────────────

    def _build_raw_product(self, url: str, product: dict, ssr_reviews: dict) -> RawProduct:
        slug = product.get("slug", "")
        tags: list[str] = product.get("tags", []) or []
        external_id = str(product.get("id", slug) or slug)

        vd  = product.get("variations_definition") or {}
        pvs = vd.get("product_variations") or []

        detailed_variants, sizes, colors = self._extract_variants(pvs, vd)

        price:      float | None = None
        compare_at: float | None = None
        on_sale = False

        if pvs:
            ref_v = next((v for v in pvs if self._is_available(v)), pvs[0])
            price      = normalize_price(ref_v.get("price"))
            compare_at = normalize_price(ref_v.get("compare_at_price"))
            if price is None:
                price = normalize_price((ref_v.get("prices") or {}).get("US", {}).get("price"))
            if compare_at is None:
                compare_at = normalize_price((ref_v.get("prices") or {}).get("US", {}).get("compare_at_price"))
            if compare_at and price and compare_at > price:
                on_sale = True
        else:
            price      = normalize_price(product.get("price"))
            compare_at = normalize_price(product.get("compare_at_price"))
            if compare_at and price and compare_at > price:
                on_sale = True

        if not on_sale and any(t in _SALE_TAGS for t in tags):
            on_sale = True

        if pvs:
            availability = "in_stock" if any(self._is_available(v) for v in pvs) else "out_of_stock"
        else:
            availability = "unknown"

        materials = self._extract_materials(product)

        compression_raw = (
            product.get("compression")
            or (product.get("material") or {}).get("compression", "")
        )
        compression_normalized = _COMPRESSION_MAP.get(str(compression_raw).strip().upper()) if compression_raw else None

        category_raw = (
            product.get("category", {}).get("name")
            if isinstance(product.get("category"), dict)
            else None
        ) or self._category_from_tags(tags)

        is_best_seller = self._is_best_seller(tags)

        rs           = product.get("review_score") or {}
        rating       = float(rs["reviews_average"]) if rs.get("reviews_average") else None
        review_count = int(rs["total_reviews"])     if rs.get("total_reviews")   else None

        # ── Collecte des avis (v5.2 : requête unique product_id + page_size=1000)
        reviews_data = self._collect_reviews(product, ssr_reviews)

        images = [
            img["src"]
            for img in (product.get("images") or [])
            if isinstance(img, dict) and img.get("src")
        ]

        description = self._clean_html(
            product.get("description_html") or product.get("description_tag", "")
        )

        return RawProduct(
            external_id=external_id,
            url=url.rstrip("/"),
            name=product.get("title", "").strip(),
            brand_slug="shapermint",
            price=price,
            original_price=compare_at if on_sale else None,
            currency="USD",
            on_sale=on_sale,
            category_raw=category_raw,
            description=description,
            images=images,
            sizes=sizes,
            colors=colors,
            variants=detailed_variants,
            availability=availability,
            rating=rating,
            review_count=review_count,
            extra={
                "handle":            slug,
                "tags":              tags,
                "vendor":            "shapermint",
                "is_best_seller":    is_best_seller,
                "materials":         materials,
                "compression_level": compression_normalized,
                "reviews":           reviews_data,
                "detailed_variants": detailed_variants,
                "shapermint_uuid":   product.get("uuid"),
                "shopify_product_id": str(product.get("id", "")),
            },
        )

    # ── Collecte des avis (v5.2) ──────────────────────────────────────────

    def _collect_reviews(self, product: dict, ssr_reviews: dict) -> list[dict]:
        """
        Collecte les avis en deux étapes :

        1. Avis SSR pré-rendus dans __NEXT_DATA__ (ssrReviews.reviews).
           Souvent vide ou limité à ~4 avis avec images. Toujours récupérés.

        2. Requête unique vers l'API Shapermint :
               GET https://api.shapermint.com/reviews
                   ?product_id=<shopify_product_id>
                   &page_size=1000

           Retourne jusqu'à 1 000 avis en une seule passe, ce qui couvre
           la quasi-totalité des produits du catalogue.
           Désactivé si max_review_pages = 0 dans config.yml.

        Les avis des deux sources sont dédupliqués par ID.
        """
        max_review_pages = int(self._config.get("max_review_pages", 3))

        # ── 1. Avis SSR ───────────────────────────────────────────────────
        reviews_raw = ssr_reviews.get("reviews") or []
        if isinstance(reviews_raw, dict):
            reviews_list_raw: list = list(reviews_raw.values())
        else:
            reviews_list_raw = list(reviews_raw)

        reviews:  list[dict] = []
        seen_ids: set[str]   = set()

        for r in reviews_list_raw:
            rid = str(r.get("id", ""))
            if rid and rid in seen_ids:
                continue
            seen_ids.add(rid)
            has_img = bool(r.get("images_url") or r.get("images_file_name"))
            reviews.append(self._normalize_review(r, has_image=has_img))

        # ── 2. Vérifier si l'API est activée ─────────────────────────────
        # max_review_pages = 0 → désactiver complètement l'appel API
        if max_review_pages <= 0:
            log.debug(
                "Shapermint reviews : API désactivée (max_review_pages=0)",
                slug=product.get("slug"),
                ssr_count=len(reviews),
            )
            return reviews

        shopify_id = str(product.get("id", "") or "")
        if not shopify_id:
            log.debug(
                "Shapermint reviews : product_id absent, skip API",
                slug=product.get("slug"),
            )
            return reviews

        # ── 3. Requête unique API : product_id + page_size=1000 ───────────
        self._fetch_reviews_by_product_id(
            product_id=shopify_id,
            reviews=reviews,
            seen_ids=seen_ids,
            product_slug=product.get("slug", "?"),
        )

        log.debug(
            "Shapermint reviews collectés",
            slug=product.get("slug"),
            total=len(reviews),
        )
        return reviews

    def _fetch_reviews_by_product_id(
        self,
        product_id: str,
        reviews: list[dict],
        seen_ids: set[str],
        product_slug: str,
    ) -> None:
        """
        Fetche tous les avis d'un produit en une seule requête :

            GET https://api.shapermint.com/reviews
                ?product_id=<shopify_product_id>
                &page_size=1000

        Le format de réponse attendu est identique au format ssrReviews :
            { "reviews": [...], "metadata": { "total_reviews": N, ... } }

        En cas d'échec réseau ou de format inattendu, la méthode logge un
        avertissement et retourne silencieusement (les avis SSR sont conservés).
        """
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )

        try:
            resp = client.get(
                _SM_REVIEWS_API,
                params={
                    "product_id": product_id,
                    "page_size":  _REVIEWS_PAGE_SIZE,
                },
                timeout=30,
            )
        except Exception as exc:
            log.warning(
                "Shapermint reviews API : erreur réseau",
                slug=product_slug,
                product_id=product_id,
                error=str(exc),
            )
            return

        if resp.status_code != 200:
            log.warning(
                "Shapermint reviews API : réponse non-200",
                slug=product_slug,
                product_id=product_id,
                status=resp.status_code,
            )
            return

        try:
            body = resp.json()
        except Exception as exc:
            log.warning(
                "Shapermint reviews API : JSON invalide",
                slug=product_slug,
                product_id=product_id,
                error=str(exc),
            )
            return

        # Extraire la liste des avis selon le format de réponse
        if isinstance(body, dict):
            api_reviews = body.get("reviews") or []
            if isinstance(api_reviews, dict):
                api_reviews = list(api_reviews.values())
        elif isinstance(body, list):
            api_reviews = body
        else:
            log.warning(
                "Shapermint reviews API : format de réponse inattendu",
                slug=product_slug,
                product_id=product_id,
                body_type=type(body).__name__,
            )
            return

        added = 0
        for r in api_reviews:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id", "") or r.get("review_id", ""))
            if rid and rid in seen_ids:
                continue
            seen_ids.add(rid)
            has_img = bool(r.get("images_url") or r.get("image_url") or r.get("images_file_name"))
            reviews.append(self._normalize_review(r, has_image=has_img))
            added += 1

        log.info(
            "Shapermint reviews API",
            slug=product_slug,
            product_id=product_id,
            fetched=len(api_reviews),
            added=added,
            total=len(reviews),
        )

    # ── Normalisation d'un avis ───────────────────────────────────────────

    @staticmethod
    def _normalize_review(r: dict, has_image: bool) -> dict:
        """Normalise un avis depuis l'API Shapermint."""
        author = r.get("author") or r.get("author_name") or ""
        if isinstance(author, dict):
            author = author.get("name") or author.get("displayName") or ""
        return {
            "id":           str(r.get("id", "") or r.get("review_id", "")),
            "rating":       r.get("rating"),
            "title":        (r.get("title") or "").strip(),
            "author":       str(author).strip(),
            "body":         (r.get("body") or r.get("content") or "").strip(),
            "date_created": r.get("date_created") or r.get("created_at") or "",
            "has_image":    has_image,
            "variant_name": r.get("vendor_variant_name") or r.get("variant_name") or "",
        }

    # ── Extraction des variantes ──────────────────────────────────────────

    def _extract_variants(self, pvs, vd):
        vs   = vd.get("variatons_summary") or {}
        dims = vs.get("product_dimensions") or []

        ordered_colors: list[str] = []
        ordered_sizes:  list[str] = []
        for dim in dims:
            name   = (dim.get("name") or dim.get("display_name") or "").lower()
            values = [v for v in (dim.get("values") or []) if v]
            if "color" in name or "colour" in name:
                ordered_colors = values
            elif "size" in name:
                ordered_sizes = values

        avail_map:  dict[tuple, bool]        = {}
        price_map:  dict[tuple, float|None]  = {}
        comp_map:   dict[tuple, float|None]  = {}
        sku_map:    dict[tuple, str]         = {}
        onsale_map: dict[tuple, bool]        = {}
        detailed: list[dict] = []

        for v in pvs:
            attrs = {a["name"]: a["value"] for a in (v.get("variation_attributes") or [])}
            color = attrs.get("Color") or attrs.get("Colour") or attrs.get("color") or ""
            size  = attrs.get("Size") or attrs.get("size") or ""
            key   = (color, size)

            price_raw   = v.get("price")
            compare_raw = v.get("compare_at_price")
            prices_us   = (v.get("prices") or {}).get("US") or {}
            if price_raw is None:
                price_raw   = prices_us.get("price")
            if compare_raw is None:
                compare_raw = prices_us.get("compare_at_price")

            price      = normalize_price(price_raw)
            compare_at = normalize_price(compare_raw)
            on_sale    = bool(compare_at and price and compare_at > price)
            available  = self._is_available(v)

            avail_map[key]  = avail_map.get(key, False) or available
            price_map[key]  = price_map.get(key) or price
            comp_map[key]   = comp_map.get(key) or compare_at
            sku_map[key]    = sku_map.get(key) or (v.get("sku") or "")
            onsale_map[key] = onsale_map.get(key, False) or on_sale

            detailed.append({
                "color":              color,
                "size":               size,
                "sku":                v.get("sku") or "",
                "price":              price,
                "original_price":     compare_at if on_sale else None,
                "on_sale":            on_sale,
                "available":          available,
                "variant_id":         v.get("id"),
                "inventory_quantity": v.get("inventory_quantity"),
                "continue_selling":   v.get("continue_selling_when_oos", False),
            })

        if not ordered_colors:
            seen: set[str] = set()
            for v in pvs:
                attrs = {a["name"]: a["value"] for a in (v.get("variation_attributes") or [])}
                c = attrs.get("Color") or attrs.get("Colour") or attrs.get("color") or ""
                if c and c not in seen:
                    ordered_colors.append(c)
                    seen.add(c)

        if not ordered_sizes:
            seen_s: set[str] = set()
            for v in pvs:
                attrs = {a["name"]: a["value"] for a in (v.get("variation_attributes") or [])}
                s = attrs.get("Size") or attrs.get("size") or ""
                if s and s not in seen_s:
                    ordered_sizes.append(s)
                    seen_s.add(s)

        colors_list: list[dict] = []
        for color in ordered_colors:
            color_available = (
                any(avail_map.get((color, s), False) for s in ordered_sizes)
                if ordered_sizes
                else any(v for k, v in avail_map.items() if k[0] == color)
            )
            ref_sku = next(
                (sku_map.get((color, s), "") for s in ordered_sizes if (color, s) in sku_map),
                "",
            )
            colors_list.append({"name": color, "available": color_available, "sku": ref_sku})

        return detailed, ordered_sizes, colors_list

    @staticmethod
    def _is_available(v: dict) -> bool:
        cont     = bool(v.get("continue_selling_when_oos", False))
        disabled = bool(v.get("disabled", False))
        try:
            qty = int(v.get("inventory_quantity") or 0)
        except (ValueError, TypeError):
            qty = 0
        if disabled and qty <= 0 and not cont:
            return False
        return qty > 0 or cont

    # ── Matériaux ─────────────────────────────────────────────────────────

    def _extract_materials(self, product: dict) -> dict:
        result: dict = {}
        mat_dict        = product.get("material") or {}
        composition_str = (
            product.get("composition")
            or (mat_dict.get("composition") if isinstance(mat_dict, dict) else None)
            or ""
        ).strip()
        if not composition_str:
            return result
        result["material_main"] = composition_str
        result["material_raw"]  = composition_str
        from app.scraping.shopify_utils import _FIBER_PATTERNS
        comp: dict[str, float] = {}
        for pattern, fiber in _FIBER_PATTERNS:
            matches = re.findall(pattern, composition_str, re.IGNORECASE)
            if matches and fiber not in comp:
                try:
                    comp[fiber] = float(matches[0])
                except ValueError:
                    pass
        if comp:
            result["material_composition_json"] = json.dumps(comp)
        return result

    # ── Extraction depuis page de collection ──────────────────────────────

    def _extract_collection_products(self, html: str) -> tuple[list[dict], int]:
        m = _NEXT_DATA_RE.search(html)
        if not m:
            return [], 0
        try:
            nd = json.loads(m.group(1))
        except json.JSONDecodeError:
            return [], 0
        pp          = nd.get("props", {}).get("pageProps", {})
        products    = pp.get("products") or []
        pagination  = pp.get("pagination") or {}
        total_pages = int(pagination.get("total_pages", 1))
        return products, total_pages

    # ── Compatibilité tests ───────────────────────────────────────────────

    def _parse_shopify_dict(self, url: str, p: dict) -> RawProduct:
        from app.scraping.shopify_utils import (
            clean_description, extract_colors, extract_materials,
            extract_variants_detailed, normalize_availability,
        )
        variants = p.get("variants", [])
        tags_raw = p.get("tags", [])
        tags: list[str] = (
            [t.strip() for t in tags_raw.split(",")]
            if isinstance(tags_raw, str) else list(tags_raw)
        )
        fv      = variants[0] if variants else {}
        price   = normalize_price(fv.get("price"))
        compare = normalize_price(fv.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)
        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="shapermint",
            price=price,
            original_price=compare if on_sale else None,
            currency="USD",
            on_sale=on_sale,
            category_raw=p.get("product_type") or self._category_from_tags(tags),
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
                "is_best_seller": self._is_best_seller(tags),
                "materials":      extract_materials(p.get("body_html")),
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _category_from_tags(self, tags: list[str]) -> str | None:
        for tag in tags:
            cat = _SUBCAT_TO_CATEGORY.get(tag)
            if cat:
                return cat
        for tag in tags:
            tl = tag.lower()
            if "bodysuit" in tl:  return "bodysuits"
            if "short" in tl and "shapewear" in tl: return "shorts"
            if "legging" in tl:   return "leggings"
            if "bra" in tl and "shaper" not in tl: return "bras"
            if "panty" in tl or "panties" in tl: return "underwear"
            if "cami" in tl or "tank" in tl: return "tanks-camis"
        return None

    def _is_best_seller(self, tags: list[str]) -> bool:
        config_tags = {t.lower() for t in self._config.get("best_seller_tags", [])}
        check = _SM_BS_TAGS | config_tags
        return any(t.strip().lower() in check for t in tags) or any(t in _SM_BS_TAGS for t in tags)

    @staticmethod
    def _clean_html(html: str | None) -> str | None:
        if not html:
            return None
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text or None