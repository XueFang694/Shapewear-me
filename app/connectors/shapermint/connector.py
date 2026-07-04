"""Connecteur Shapermint v1.5 — stratégie HTML SSR (__NEXT_DATA__).

Shapermint est un frontend Next.js SSR custom (Trafilea) qui bloque
TOUS les endpoints JSON Shopify publics :
  - /products.json                     → 404
  - /collections/<slug>/products.json  → 404

Stratégie fonctionnelle en 2 étapes :

  Étape 1 — Pages de collection HTML (extraction __NEXT_DATA__)
    GET shapermint.com/collections/<slug>?page=N
    → HTML contenant <script id="__NEXT_DATA__" type="application/json">
    → pageProps.products  : liste de 20 produits avec prix, tags, images, couleurs
    → pageProps.pagination: {total_products, total_pages, page_number, page_size}

  Étape 2 — JSON produit individuel (variants + description)
    GET shapermint.com/products/<slug>.json
    → variants complets avec tailles, prix par variante, disponibilité
    → body_html pour extraction des matériaux

Les données produit issues de __NEXT_DATA__ contiennent :
  - id (UUID Shapermint interne)
  - title, slug (= handle Shopify)
  - tags (liste de strings Shopify)
  - images (liste avec src, width, height)
  - price, compare_at_price
  - review_score {reviews_count, reviews_average}
  - product_dimensions [{name, values: [{name, variant_id, image_url}]}]
  - vendor_product {vendor_id, product_id (= ID Shopify numérique)}

Détection best-seller via tags : "product-label-best-seller",
"section-best-seller", "winning-product".
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.shapermint.mappings import extract_best_seller_sm, map_category_sm
from app.scraping.shopify_utils import (
    clean_description,
    extract_materials,
    extract_variants_detailed,
    normalize_availability,
    normalize_price,
)
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
    "best seller",
    "bestseller",
    "top seller",
    "popular",
})


class ShapermintConnector(BaseConnector):

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)
        raw_types: list[str] = self._config.get("target_product_types", [])
        self._target_types: frozenset[str] = frozenset(t.lower() for t in raw_types)

    # ── Métadonnées ───────────────────────────────────────────────────────

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="Shapermint",
            slug="shapermint",
            version="1.5",
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

    # ── URLs produits — via pages de collection HTML ───────────────────────

    def get_product_urls(self, category: Category) -> list[str]:
        """
        Pagine les pages HTML de la collection et extrait les slugs produits
        depuis __NEXT_DATA__. Retourne les URLs .json individuelles.
        """
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )

        pg_cfg     = self._config.get("pagination", {})
        max_pages  = pg_cfg.get("max_pages", 20)
        base_url   = f"{self.base_url}/collections/{category.slug}"

        slugs: list[str] = []
        seen:  set[str]  = set()

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
                if slug and slug not in seen:
                    slugs.append(slug)
                    seen.add(slug)

            log.debug(
                "Shapermint page collection traitée",
                category=category.slug,
                page=f"{page_num}/{total_pages}",
                products_this_page=len(products),
                total_slugs=len(slugs),
            )

            if page_num >= total_pages:
                break

        urls = [f"{self.base_url}/products/{s}.json" for s in slugs]
        log.info(
            "URLs Shapermint (HTML SSR)",
            category=category.slug,
            slugs=len(slugs),
        )
        return urls

    # ── Parsing produit — JSON individuel ─────────────────────────────────

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        if not isinstance(data, dict):
            raise ConnectorParseError("dict attendu", context={"url": url})
        try:
            return self._parse(url, data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorParseError(
                f"Erreur Shapermint: {exc}", context={"url": url}
            ) from exc

    def _parse(self, url: str, p: dict[str, Any]) -> RawProduct:
        """Parse un produit depuis son JSON Shopify individuel (/products/<slug>.json)."""
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

        category_raw = p.get("product_type") or next(
            (t for t in tags if map_category_sm(t)), None
        )

        materials            = extract_materials(p.get("body_html"))
        detailed_variants    = extract_variants_detailed(variants, options)
        availability         = normalize_availability(variants)

        # Avis clients (ratings dans les metafields Shopify si présents)
        rating = review_count = None
        for mf in (p.get("metafields") or []):
            key = (mf.get("key") or "").lower()
            if "rating" in key and "count" not in key:
                try:
                    rating = float(mf.get("value", 0))
                except (ValueError, TypeError):
                    pass
            elif "count" in key or "reviews" in key:
                try:
                    review_count = int(mf.get("value", 0))
                except (ValueError, TypeError):
                    pass

        # Tailles : extraire depuis les options (option "Size")
        sizes: list[str] = []
        colors: list[dict] = []
        for opt in options:
            opt_name = (opt.get("name") or "").lower()
            if "size" in opt_name or "taille" in opt_name:
                sizes = [v for v in opt.get("values", []) if v]
            elif "color" in opt_name or "colour" in opt_name:
                colors = [{"name": v, "canonical_name": v} for v in opt.get("values", []) if v]

        is_best_seller = self._is_best_seller(tags)

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
            sizes=sizes,
            colors=colors,
            variants=detailed_variants,
            availability=availability,
            rating=rating,
            review_count=review_count,
            extra={
                "handle":            p.get("handle"),
                "tags":              tags,
                "vendor":            p.get("vendor"),
                "is_best_seller":    is_best_seller,
                "materials":         materials,
                "detailed_variants": detailed_variants,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _extract_products_from_html(
        self, html: str
    ) -> tuple[list[dict], int]:
        """
        Extrait la liste de produits et le nombre total de pages depuis
        le bloc __NEXT_DATA__ d'une page HTML de collection Shapermint.

        Retourne (products, total_pages).
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

    def _is_best_seller(self, tags: list[str]) -> bool:
        """Détecte le statut best-seller depuis les tags Shopify Shapermint."""
        config_tags = {t.lower() for t in self._config.get("best_seller_tags", [])}
        check = _SM_BS_TAGS | config_tags
        return any(t.strip().lower() in check for t in tags)