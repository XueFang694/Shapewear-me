"""Connecteur Shapermint v3.0 — parsing 100% depuis le __NEXT_DATA__ des pages produit.

CHANGEMENTS v3.0 (refonte complète du parsing produit)
────────────────────────────────────────────────────────
v2.0 parsait __NEXT_DATA__ sur les pages de COLLECTION, qui ne contient que des
données résumées (pas de variantes, pas de matériaux, pas de tailles détaillées,
pas de disponibilité granulaire).

STRATÉGIE v3.0
──────────────
1. get_product_urls() :
   Identique à v2.0 — pagine les pages de collection pour collecter les slugs.
   Retourne maintenant les vraies URLs HTML produit (pas de cache intermédiaire).
   Format : https://shapermint.com/products/<slug>

2. parse_product() reçoit le HTML de la PAGE PRODUIT et extrait depuis __NEXT_DATA__ :
   - variations_definition.product_variations[] → variantes complètes avec
     inventory_quantity, continue_selling_when_oos, variation_attributes (Color+Size),
     price, compare_at_price, sku
   - Disponibilité variante : inventory_quantity > 0 OR continue_selling_when_oos
   - product.material → {compression, composition, style_number}
   - product.composition → texte brut de composition
   - product.compression → niveau de compression
   - ssrReviews → premier lot de reviews (avec images) + metadata pagination
   - Reviews texte supplémentaires : API https://api.shapermint.com/reviews

3. Reviews (textes sans images) :
   - ssrReviews.reviews contient les 4 premières reviews (avec images) du SSR
   - Pour les suivantes : GET https://api.shapermint.com/reviews?product_uuid=<uuid>
     &page=N&without_images=true
   - Nombre de pages à fetcher configurable (max_review_pages dans config.yml)
   - Reviews stockées dans extra["reviews"]
"""
from __future__ import annotations

import json
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

# Mapping des niveaux de compression Shapermint → valeur normalisée
_COMPRESSION_MAP = {
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


class ShapermintConnector(BaseConnector):
    """
    Connecteur Shapermint v3.0 — parsing complet depuis les pages produit HTML.

    Chaque page produit est fetchée individuellement (engine="html") pour
    extraire depuis __NEXT_DATA__ les variantes complètes, matériaux,
    disponibilité granulaire et avis clients.
    """

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)

    # ── Métadonnées ───────────────────────────────────────────────────────

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="Shapermint",
            slug="shapermint",
            version="3.0",
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
                brand_slug="shapermint",
            )
            for slug in self._config.get("target_collections", [])
        ]

    # ── URLs produits ─────────────────────────────────────────────────────

    def get_product_urls(self, category: Category) -> list[str]:
        """
        Pagine les pages HTML de collection, extrait les slugs depuis __NEXT_DATA__,
        et retourne les vraies URLs HTML des pages produit.

        ScrapingEngine fetchera chacune en mode HTML (engine != shopify_json)
        car la page produit n'a pas d'endpoint .json opérationnel.
        """
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
                log.error(
                    "Shapermint erreur collection",
                    category=category.slug, page=page_num, error=str(exc),
                )
                break

            if resp.status_code != 200:
                log.warning(
                    "Shapermint collection inaccessible",
                    category=category.slug, page=page_num, status=resp.status_code,
                )
                break

            products_raw, total_pages = self._extract_collection_products(resp.text)

            if not products_raw:
                log.debug("Shapermint page vide", category=category.slug, page=page_num)
                break

            new_this_page = 0
            for p in products_raw:
                slug = p.get("slug", "")
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                urls.append(f"{self.base_url}/products/{slug}")
                new_this_page += 1

            log.debug(
                "Shapermint page collection",
                category=category.slug,
                page=f"{page_num}/{total_pages}",
                new=new_this_page,
                total=len(urls),
            )

            if page_num >= total_pages:
                break

        log.info("URLs Shapermint", category=category.slug, count=len(urls))
        return urls

    # ── Parsing produit ───────────────────────────────────────────────────

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        """
        Parse une page produit Shapermint.

        - data = str HTML  → extrait __NEXT_DATA__ depuis la page produit (chemin normal)
        - data = dict      → compatibilité tests (dict Shopify standard)
        """
        if isinstance(data, str):
            return self._parse_from_html(url, data)
        if isinstance(data, dict):
            return self._parse_shopify_dict(url, data)
        raise ConnectorParseError(
            f"Type de données inattendu : {type(data)}",
            context={"url": url},
        )

    # ── Parser HTML principal ─────────────────────────────────────────────

    def _parse_from_html(self, url: str, html: str) -> RawProduct:
        """Extrait __NEXT_DATA__ depuis le HTML de la page produit et construit le RawProduct."""
        m = _NEXT_DATA_RE.search(html)
        if not m:
            raise ConnectorParseError(
                "Shapermint __NEXT_DATA__ introuvable sur la page produit",
                context={"url": url},
            )
        try:
            nd_data = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            raise ConnectorParseError(
                f"Shapermint JSON invalide : {exc}",
                context={"url": url},
            ) from exc

        pp      = nd_data.get("props", {}).get("pageProps", {})
        product = pp.get("product")
        if not product:
            raise ConnectorParseError(
                "Shapermint product absent de pageProps",
                context={"url": url},
            )

        ssr_reviews = pp.get("ssrReviews", {}) or {}
        return self._build_raw_product(url, product, ssr_reviews)

    def _build_raw_product(
        self,
        url: str,
        product: dict,
        ssr_reviews: dict,
    ) -> RawProduct:
        """Construit le RawProduct depuis le dict produit extrait de __NEXT_DATA__."""
        slug = product.get("slug", "")
        tags: list[str] = product.get("tags", []) or []

        # ── external_id : ID Shopify numérique ────────────────────────────
        vp = product.get("vendor_product") or {}
        shopify_id  = str(vp.get("product_id", "")) if vp else ""
        external_id = shopify_id or str(product.get("id", slug) or slug)

        # ── Variantes depuis variations_definition ────────────────────────
        vd  = product.get("variations_definition") or {}
        pvs = vd.get("product_variations") or []

        detailed_variants, sizes, colors = self._extract_variants(pvs)

        # ── Prix (première variante disponible, ou à défaut première) ─────
        price: float | None = None
        compare_at: float | None = None
        on_sale = False

        if pvs:
            ref_v = next((v for v in pvs if self._is_available(v)), pvs[0])
            price      = normalize_price(ref_v.get("price"))
            compare_at = normalize_price(ref_v.get("compare_at_price"))
            # Fallback sur prices.US si les champs plats sont absents
            if price is None:
                price = normalize_price((ref_v.get("prices") or {}).get("US", {}).get("price"))
            if compare_at is None:
                compare_at = normalize_price(
                    (ref_v.get("prices") or {}).get("US", {}).get("compare_at_price")
                )
            if compare_at and price and compare_at > price:
                on_sale = True
        else:
            price      = normalize_price(product.get("price"))
            compare_at = normalize_price(product.get("compare_at_price"))
            if compare_at and price and compare_at > price:
                on_sale = True

        if not on_sale and any(t in _SALE_TAGS for t in tags):
            on_sale = True

        # ── Disponibilité produit ─────────────────────────────────────────
        if pvs:
            availability = (
                "in_stock"
                if any(self._is_available(v) for v in pvs)
                else "out_of_stock"
            )
        else:
            availability = "unknown"

        # ── Matériaux ─────────────────────────────────────────────────────
        materials = self._extract_materials(product)

        # ── Niveau de compression ─────────────────────────────────────────
        compression_raw = (
            product.get("compression")
            or (product.get("material") or {}).get("compression", "")
        )
        compression_normalized = _COMPRESSION_MAP.get(
            str(compression_raw).strip().upper(), compression_raw or None
        )

        # ── Catégorie ─────────────────────────────────────────────────────
        category_raw = (
            product.get("category", {}).get("name")
            if isinstance(product.get("category"), dict)
            else None
        ) or self._category_from_tags(tags)

        # ── Best-seller ───────────────────────────────────────────────────
        is_best_seller = self._is_best_seller(tags)

        # ── Avis clients ──────────────────────────────────────────────────
        rs           = product.get("review_score") or {}
        rating       = float(rs["reviews_average"]) if rs.get("reviews_average") else None
        review_count = int(rs["total_reviews"])     if rs.get("total_reviews")   else None

        reviews_data = self._collect_reviews(product, ssr_reviews)

        # ── Images ────────────────────────────────────────────────────────
        images = [
            img["src"]
            for img in (product.get("images") or [])
            if isinstance(img, dict) and img.get("src")
        ]

        # ── Description ───────────────────────────────────────────────────
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
            },
        )

    # ── Extraction des variantes ──────────────────────────────────────────

    def _extract_variants(
        self, pvs: list[dict]
    ) -> tuple[list[dict], list[str], list[dict]]:
        """
        Construit les variantes détaillées, tailles et couleurs depuis
        variations_definition.product_variations[].

        Disponibilité : inventory_quantity > 0  OU  continue_selling_when_oos = True.
        """
        detailed: list[dict] = []
        sizes_seen:  list[str]  = []
        colors_seen: list[str]  = []
        colors_list: list[dict] = []

        for v in pvs:
            attrs = {
                a["name"]: a["value"]
                for a in (v.get("variation_attributes") or [])
            }
            color = (
                attrs.get("Color")
                or attrs.get("Colour")
                or attrs.get("color")
            )
            size = attrs.get("Size") or attrs.get("size")

            # Prix plat ou via prices.US
            price      = normalize_price(v.get("price"))
            compare_at = normalize_price(v.get("compare_at_price"))
            prices_us  = (v.get("prices") or {}).get("US") or {}
            if price is None:
                price = normalize_price(prices_us.get("price"))
            if compare_at is None:
                compare_at = normalize_price(prices_us.get("compare_at_price"))

            on_sale   = bool(compare_at and price and compare_at > price)
            available = self._is_available(v)

            detailed.append({
                "color":              color,
                "size":               size,
                "sku":                v.get("sku", ""),
                "price":              price,
                "original_price":     compare_at if on_sale else None,
                "on_sale":            on_sale,
                "available":          available,
                "variant_id":         v.get("id"),
                "inventory_quantity": v.get("inventory_quantity"),
                "continue_selling":   v.get("continue_selling_when_oos", False),
            })

            if size and size not in sizes_seen:
                sizes_seen.append(size)

            if color and color not in colors_seen:
                colors_seen.append(color)
                colors_list.append({
                    "name":      color,
                    "available": available,
                    "sku":       v.get("sku", ""),
                })

        return detailed, sizes_seen, colors_list

    @staticmethod
    def _is_available(v: dict) -> bool:
        """
        Disponibilité d'une variante Shapermint.
        Règle : inventory_quantity > 0  OU  continue_selling_when_oos = True.
        """
        iq   = v.get("inventory_quantity") or 0
        cont = v.get("continue_selling_when_oos", False)
        try:
            return int(iq) > 0 or bool(cont)
        except (ValueError, TypeError):
            return bool(cont)

    # ── Extraction des matériaux ──────────────────────────────────────────

    def _extract_materials(self, product: dict) -> dict:
        """
        Extrait la composition textile depuis les champs Shapermint :
          - product.composition  → texte brut (ex: "100% Nylon / Spandex")
          - product.material     → dict {compression, composition, style_number}

        Retourne un dict compatible avec le Normalizer.
        """
        result: dict = {}

        mat_dict       = product.get("material") or {}
        composition_str = (
            product.get("composition")
            or (mat_dict.get("composition") if isinstance(mat_dict, dict) else None)
            or ""
        )

        if not composition_str:
            return result

        composition_str = composition_str.strip()
        result["material_main"] = composition_str
        result["material_raw"]  = composition_str

        # Extraire les pourcentages fibre par fibre
        from app.scraping.shopify_utils import _FIBER_PATTERNS
        comp: dict[str, float] = {}
        text_lower = composition_str.lower()
        for pattern, fiber in _FIBER_PATTERNS:
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            if matches and fiber not in comp:
                try:
                    comp[fiber] = float(matches[0])
                except ValueError:
                    pass
        if comp:
            result["material_composition_json"] = json.dumps(comp)

        return result

    # ── Collecte des avis ─────────────────────────────────────────────────

    def _collect_reviews(self, product: dict, ssr_reviews: dict) -> list[dict]:
        """
        Collecte les avis depuis deux sources :

        1. ssrReviews.reviews (SSR, avec images) — toujours inclus,
           présents dans le __NEXT_DATA__ de la page produit.

        2. API Shapermint paginée (https://api.shapermint.com/reviews)
           avec without_images=true, pour les reviews texte.
           Nombre de pages limité par max_review_pages en config.yml.

        Retourne une liste de dicts :
          {id, rating, title, author, body, date_created, has_image, variant_name}
        """
        max_review_pages = int(self._config.get("max_review_pages", 3))

        # 1. Reviews SSR (avec images éventuellement)
        ssr_list  = ssr_reviews.get("reviews") or []
        reviews:   list[dict] = []
        seen_ids:  set[str]   = set()

        for r in ssr_list:
            rid = r.get("id", "")
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            reviews.append(self._normalize_review(r, has_image=bool(r.get("images_url"))))

        if max_review_pages <= 0:
            return reviews

        # 2. API Shapermint pour les reviews texte (sans images)
        product_uuid = product.get("uuid") or product.get("id")
        if not product_uuid:
            return reviews

        meta            = ssr_reviews.get("metadata") or {}
        total_pages_api = int(meta.get("total_pages", 1))
        pages_to_fetch  = min(max_review_pages, total_pages_api)

        from app.scraping.http_client import HttpClient
        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )

        for page_num in range(1, pages_to_fetch + 1):
            try:
                resp = client.get(
                    "https://api.shapermint.com/reviews",
                    params={
                        "product_uuid":   product_uuid,
                        "page":           page_num,
                        "page_size":      20,
                        "without_images": "true",
                    },
                    timeout=15,
                )
                if resp.status_code != 200:
                    log.debug(
                        "Shapermint reviews API non-200",
                        uuid=product_uuid, page=page_num, status=resp.status_code,
                    )
                    break

                body = resp.json()
                api_reviews = (
                    body.get("reviews")
                    or body.get("data")
                    or (body if isinstance(body, list) else [])
                )
                if not api_reviews:
                    break

                for r in api_reviews:
                    rid = r.get("id", "")
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    reviews.append(self._normalize_review(r, has_image=False))

            except Exception as exc:
                log.debug(
                    "Shapermint reviews API erreur",
                    uuid=product_uuid, page=page_num, error=str(exc),
                )
                break

        return reviews

    @staticmethod
    def _normalize_review(r: dict, has_image: bool) -> dict:
        return {
            "id":           r.get("id", ""),
            "rating":       r.get("rating"),
            "title":        r.get("title", ""),
            "author":       r.get("author", ""),
            "body":         r.get("body", ""),
            "date_created": r.get("date_created", ""),
            "has_image":    has_image,
            "variant_name": r.get("vendor_variant_name"),
        }

    # ── Extraction depuis page de collection ──────────────────────────────

    def _extract_collection_products(self, html: str) -> tuple[list[dict], int]:
        """
        Extrait la liste de produits et le nombre total de pages depuis
        __NEXT_DATA__ d'une page de collection Shapermint.
        """
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
        """
        Compatibilité ascendante : parse un dict JSON Shopify standard
        (utilisé dans les tests unitaires existants).
        """
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
            if "bodysuit" in tl:
                return "bodysuits"
            if "short" in tl and "shapewear" in tl:
                return "shorts"
            if "legging" in tl:
                return "leggings"
            if "bra" in tl and "shaper" not in tl:
                return "bras"
            if "panty" in tl or "panties" in tl:
                return "underwear"
            if "cami" in tl or "tank" in tl:
                return "tanks-camis"
        return None

    def _is_best_seller(self, tags: list[str]) -> bool:
        config_tags = {t.lower() for t in self._config.get("best_seller_tags", [])}
        check = _SM_BS_TAGS | config_tags
        return any(t.strip().lower() in check for t in tags) or \
               any(t in _SM_BS_TAGS for t in tags)

    @staticmethod
    def _clean_html(html: str | None) -> str | None:
        if not html:
            return None
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text or None