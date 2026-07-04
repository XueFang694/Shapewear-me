"""Connecteur Shapermint v2.0 — parsing 100% depuis __NEXT_DATA__.

CONTEXTE DU PROBLÈME (v1.5)
─────────────────────────────
Shapermint est un frontend Next.js SSR headless (Trafilea). Deux endpoints
sont bloqués (404) :
  - /collections/<slug>/products.json   → 404  (collections Shopify)
  - /products/<slug>.json               → 404  (produits individuels)

v1.5 avait contourné le premier blocage en parsant __NEXT_DATA__ sur les
pages de collection pour extraire les slugs, puis tentait un fetch .json
individuel qui échouait systématiquement avec 404.

STRATÉGIE v2.0
──────────────
Un seul fetch HTTP par page de collection.  Toutes les données nécessaires
sont parsées directement depuis __NEXT_DATA__ sans aucun fetch secondaire.

Ce qui est disponible dans __NEXT_DATA__ (suffisant pour la veille) :
  - title, slug, price, compare_at_price
  - images (liste complète avec src)
  - review_score  → rating + review_count
  - tags          → best-seller, catégorie via subcat-*, on_sale via SALE
  - product_dimensions[Color] → couleurs disponibles + variant_ids
  - vendor_product.product_id → ID Shopify numérique (external_id stable)

Ce qui est absent (non critique pour la veille prix/dispo) :
  - description détaillée / matériaux
  - tailles (dimension Size absente de la page liste)
  - disponibilité granulaire par variante

Disponibilité produit : on considère "in_stock" si le produit apparaît
dans la collection (Shapermint retire les produits sold-out de l'affichage).
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

# Regex pour extraire le bloc __NEXT_DATA__ d'une page HTML Shapermint
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

# Tags Shopify indiquant un best-seller chez Shapermint
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

# Mapping tags subcat- → catégorie normalisée
_SUBCAT_TO_CATEGORY: dict[str, str] = {
    "subcat-bodysuits":       "bodysuits",
    "subcat-shorts":          "shorts",
    "subcat-leggings":        "leggings",
    "subcat-bras":            "bras",
    "subcat-panties":         "underwear",
    "subcat-camis-tanks":     "tanks-camis",
    "subcat-camis":           "tanks-camis",
    "subcat-tanks":           "tanks-camis",
    "subcat-clothing-tops":   "tanks-camis",
    "subcat-clothing-bottoms": "leggings",
}

# Tags signalant une promotion active
_SALE_TAGS = frozenset({"SALE", "product-label-sale"})


class ShapermintConnector(BaseConnector):
    """
    Connecteur Shapermint v2.0 — parsing 100% __NEXT_DATA__.

    Chaque page de collection est fetchée une seule fois.
    Les RawProduct sont construits directement depuis les données
    embarquées dans le HTML, sans aucun fetch secondaire.
    """

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)

    # ── Métadonnées ───────────────────────────────────────────────────────

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="Shapermint",
            slug="shapermint",
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
                brand_slug="shapermint",
            )
            for slug in self._config.get("target_collections", [])
        ]

    # ── URLs produits — retourne des marqueurs internes, non des vraies URLs ──
    # NOTE : on surcharge get_product_urls() pour retourner des URLs
    # synthétiques qui encodent les données produit via le paramètre de
    # crawl interne. En réalité, on court-circuite le pipeline standard
    # en surchargeant crawl_category() dans le ScrapingEngine via
    # parse_product() qui accepte un dict.
    #
    # Plus proprement : on implémente get_product_urls() qui retourne les
    # vraies URLs HTML de pages produit (pas .json) et parse_product()
    # qui reçoit le HTML et parse depuis __NEXT_DATA__ de la page produit.
    # MAIS les pages produit individuelles sont aussi SSR et ont leurs
    # propres __NEXT_DATA__ avec variantes complètes.
    #
    # Stratégie retenue : get_product_urls() retourne des URL fictives
    # encodant les données JSON déjà collectées lors de la pagination,
    # et parse_product() les décode. Cela évite tout fetch supplémentaire.

    def get_product_urls(self, category: Category) -> list[str]:
        """
        Pagine les pages HTML de collection, extrait les données produit
        depuis __NEXT_DATA__ et les stocke en cache interne.

        Retourne des URLs fictives de la forme :
          shapermint://product/<slug>
        qui permettent à parse_product() de retrouver les données sans
        fetch réseau.
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

        # Cache : slug → données produit brutes __NEXT_DATA__
        if not hasattr(self, "_product_cache"):
            self._product_cache: dict[str, dict] = {}

        seen: set[str] = set()
        virtual_urls: list[str] = []

        for page_num in range(1, max_pages + 1):
            url = f"{base_url}?page={page_num}" if page_num > 1 else base_url

            try:
                resp = client.get(url)
            except Exception as exc:
                log.error(
                    "Shapermint erreur requête collection",
                    category=category.slug, page=page_num, error=str(exc),
                )
                break

            if resp.status_code != 200:
                log.warning(
                    "Shapermint collection inaccessible",
                    category=category.slug, page=page_num, status=resp.status_code,
                )
                break

            products, total_pages = self._extract_products_from_html(resp.text)

            if not products:
                log.debug(
                    "Shapermint page vide ou sans __NEXT_DATA__",
                    category=category.slug, page=page_num,
                )
                break

            for p in products:
                slug = p.get("slug", "")
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                # Stocker les données en cache
                self._product_cache[slug] = p
                virtual_urls.append(f"shapermint://product/{slug}")

            log.debug(
                "Shapermint page collection traitée",
                category=category.slug,
                page=f"{page_num}/{total_pages}",
                products_this_page=len(products),
                total_slugs=len(virtual_urls),
            )

            if page_num >= total_pages:
                break

        log.info(
            "URLs Shapermint (SSR __NEXT_DATA__)",
            category=category.slug,
            count=len(virtual_urls),
        )
        return virtual_urls

    # ── Parsing produit ───────────────────────────────────────────────────

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        """
        Parse un produit Shapermint.

        - Si url commence par "shapermint://product/" → récupère les données
          depuis le cache interne (chemin normal v2.0, sans fetch réseau).
        - Si data est un dict → parsing direct (compatibilité tests).
        - Si data est une str HTML → extraction __NEXT_DATA__ (fallback).
        """
        if url.startswith("shapermint://product/"):
            slug = url.removeprefix("shapermint://product/")
            cache = getattr(self, "_product_cache", {})
            product_data = cache.get(slug)
            if product_data is None:
                raise ConnectorParseError(
                    f"Produit absent du cache: {slug}",
                    context={"url": url},
                )
            return self._parse_next_data_product(url, product_data)

        if isinstance(data, dict):
            # Compatibilité : dict Shopify standard (tests unitaires, etc.)
            return self._parse_shopify_dict(url, data)

        if isinstance(data, str):
            # Fallback HTML : extraire __NEXT_DATA__ de la page produit
            m = _NEXT_DATA_RE.search(data)
            if m:
                try:
                    nd = json.loads(m.group(1))
                    pp = nd.get("props", {}).get("pageProps", {})
                    product_data = pp.get("product") or pp.get("products", [None])[0]
                    if product_data:
                        return self._parse_next_data_product(url, product_data)
                except Exception:
                    pass
            raise ConnectorParseError(
                "Impossible de parser la page HTML Shapermint",
                context={"url": url},
            )

        raise ConnectorParseError(
            f"Type de données inattendu: {type(data)}",
            context={"url": url},
        )

    # ── Parsers internes ──────────────────────────────────────────────────

    def _parse_next_data_product(self, url: str, p: dict) -> RawProduct:
        """
        Construit un RawProduct depuis un objet produit __NEXT_DATA__
        Shapermint (format liste de collection).

        Champs disponibles : id (UUID), title, slug, tags, images,
        price, compare_at_price, review_score, product_dimensions,
        vendor_product.product_id (ID Shopify numérique).
        """
        slug = p.get("slug", "")
        tags: list[str] = p.get("tags", [])

        # external_id : on préfère l'ID Shopify numérique (stable)
        # pour la cohérence avec les autres connecteurs Shopify
        vp = p.get("vendor_product", {})
        shopify_id = str(vp.get("product_id", "")) if vp else ""
        external_id = shopify_id or p.get("id", slug)

        # Prix
        price   = normalize_price(p.get("price"))
        compare = normalize_price(p.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)
        # Confirmé aussi par les tags SALE
        if not on_sale and any(t in _SALE_TAGS for t in tags):
            on_sale = True

        # Images
        images = [
            img["src"]
            for img in p.get("images", [])
            if img.get("src")
        ]

        # Avis clients
        rs = p.get("review_score") or {}
        rating       = float(rs["reviews_average"]) if rs.get("reviews_average") else None
        review_count = int(rs["reviews_count"])     if rs.get("reviews_count")   else None

        # Couleurs (depuis product_dimensions)
        colors: list[dict] = []
        for dim in p.get("product_dimensions", []):
            if dim.get("name", "").lower() in ("color", "colour", "couleur"):
                colors = [
                    {
                        "name":      v["name"],
                        "available": True,  # présent dans la page = disponible
                        "sku":       str(v.get("variant_id", "")),
                    }
                    for v in dim.get("values", [])
                    if v.get("name")
                ]
                break

        # Catégorie depuis les tags subcat-*
        category_raw = self._category_from_tags(tags)

        # Best-seller
        is_best_seller = self._is_best_seller(tags)

        # URL produit réelle (page HTML publique)
        product_url = f"{self.base_url}/products/{slug}"

        # Disponibilité : présent dans la collection = en stock
        # (Shapermint n'affiche pas les produits sold-out dans les listes)
        availability = "in_stock"

        return RawProduct(
            external_id=external_id,
            url=product_url,
            name=p.get("title", "").strip(),
            brand_slug="shapermint",
            price=price,
            original_price=compare if on_sale else None,
            currency="USD",
            on_sale=on_sale,
            category_raw=category_raw,
            description=None,       # Non disponible dans la page liste
            images=images,
            sizes=[],               # Non disponible dans la page liste
            colors=colors,
            variants=[],            # Pas de variantes granulaires sans .json
            availability=availability,
            rating=rating,
            review_count=review_count,
            extra={
                "handle":         slug,
                "tags":           tags,
                "vendor":         "shapermint",
                "is_best_seller": is_best_seller,
                "materials":      {},
                "shapermint_id":  p.get("id"),   # UUID Shapermint interne
            },
        )

    def _parse_shopify_dict(self, url: str, p: dict) -> RawProduct:
        """
        Compatibilité ascendante : parse un dict JSON Shopify standard.
        Utilisé dans les tests unitaires et si un endpoint .json
        redevient accessible un jour.
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

        category_raw = p.get("product_type") or self._category_from_tags(tags)
        materials            = extract_materials(p.get("body_html"))
        detailed_variants    = extract_variants_detailed(variants, options)
        availability         = normalize_availability(variants)

        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="shapermint",
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
                "is_best_seller": self._is_best_seller(tags),
                "materials":      materials,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _extract_products_from_html(self, html: str) -> tuple[list[dict], int]:
        """
        Extrait la liste de produits et le nombre total de pages depuis
        le bloc __NEXT_DATA__ d'une page HTML de collection Shapermint.
        """
        m = _NEXT_DATA_RE.search(html)
        if not m:
            log.warning("Shapermint __NEXT_DATA__ introuvable dans la page HTML")
            return [], 0

        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            log.error("Shapermint erreur parsing __NEXT_DATA__", error=str(exc))
            return [], 0

        pp          = data.get("props", {}).get("pageProps", {})
        products    = pp.get("products", [])
        pagination  = pp.get("pagination", {})
        total_pages = int(pagination.get("total_pages", 1))

        return products, total_pages

    def _category_from_tags(self, tags: list[str]) -> str | None:
        """Déduit la catégorie depuis les tags subcat-* de Shapermint."""
        for tag in tags:
            cat = _SUBCAT_TO_CATEGORY.get(tag)
            if cat:
                return cat
        # Fallback : tags Collection-*
        for tag in tags:
            tag_low = tag.lower()
            if "bodysuit" in tag_low:
                return "bodysuits"
            if "short" in tag_low and "shapewear" in tag_low:
                return "shorts"
            if "legging" in tag_low:
                return "leggings"
            if "bra" in tag_low and "shaper" not in tag_low:
                return "bras"
            if "panty" in tag_low or "panties" in tag_low:
                return "underwear"
            if "cami" in tag_low or "tank" in tag_low:
                return "tanks-camis"
        return None

    def _is_best_seller(self, tags: list[str]) -> bool:
        """Détecte le statut best-seller depuis les tags Shapermint."""
        config_tags = {t.lower() for t in self._config.get("best_seller_tags", [])}
        check = _SM_BS_TAGS | config_tags
        return any(t.strip().lower() in check for t in tags) or \
               any(t in _SM_BS_TAGS for t in tags)