"""
Connecteur Wacoal America v2.0 — passage à l'endpoint SEO BazaarVoice.

CHANGEMENTS v2.0
────────────────
Suppression de toute dépendance à l'API REST BazaarVoice et au passKey.

• fetch_bv_rating_with_fallback  → extract_rating_from_html()
  Le rating est extrait directement depuis le HTML statique déjà fetchée
  (bloc analytics Klaviyo / meta tags / JSON-LD). Zéro requête supplémentaire.

• fetch_bv_reviews_with_fallback → fetch_bv_seo_reviews()
  Les avis texte sont récupérés via l'endpoint SEO public de BazaarVoice
  (seo.bazaarvoice.com/<seo_key>/product/<id>/reviews.djs).
  Cet endpoint est public, sans passKey, conçu pour l'indexation SEO.

• extract_bv_pass_key            → supprimé (inutile)
  Le passKey BV REST API est exclusivement dans bv.js (chargé en JS dynamique)
  et n'est pas accessible sans exécuter le JS. Il est de toute façon invalide
  pour l'API REST car c'est une clé d'initialisation widget navigateur.

Toutes les autres fonctionnalités sont inchangées.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.wacoal.html_utils import (
    apply_html_availability_to_variants,
    extract_bv_identifiers,
    extract_materials_from_wacoal_html,
    extract_rating_from_html,
    extract_variant_availability_from_html,
    fetch_bv_seo_reviews,
)
from app.connectors.wacoal.mapping import (
    extract_best_seller_wacoal,
    extract_cup_size_wacoal,
    extract_sub_brand_wacoal,
    map_category_wacoal,
)
from app.scraping.shopify_connector_mixin import ShopifyConnectorMixin
from app.scraping.shopify_utils import (
    clean_description,
    extract_colors,
    extract_rating_and_reviews,
    extract_sizes,
    extract_variants_detailed,
    normalize_price,
)
from app.core.exceptions import ConnectorParseError
from app.core.logger import get_logger

log = get_logger(__name__)
_CONFIG_PATH = Path(__file__).parent / "config.yml"


class WacoalConnector(ShopifyConnectorMixin, BaseConnector):

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__(config_path=config_path or _CONFIG_PATH)
        try:
            from app.core.market import get_market
            from app.core.config import settings
            self._market = get_market(settings.MARKET)
        except Exception:
            self._market = None

    # ── Métadonnées ───────────────────────────────────────────────────────

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="Wacoal America", slug="wacoal", version="2.0",
            engine="shopify_json", base_url=self.base_url,
        )

    # ── Catégories ────────────────────────────────────────────────────────

    def get_categories(self) -> list[Category]:
        return [
            Category(
                slug=s,
                name=s.replace("-", " ").title(),
                url=f"{self.base_url}/collections/{s}",
                brand_slug="wacoal",
            )
            for s in self._config.get("target_collections", [])
        ]

    # ── URLs produits ─────────────────────────────────────────────────────

    def get_product_urls(self, category: Category) -> list[str]:
        if self._market:
            market_headers = self._market.get_http_headers()
            original_headers = self._config.get("headers", {})
            self._config["headers"] = {**original_headers, **market_headers}
        return self._shopify_get_product_urls(category)

    # ── Parsing produit ───────────────────────────────────────────────────

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        if not isinstance(data, dict):
            raise ConnectorParseError("dict attendu", context={"url": url})
        try:
            return self._parse(url, data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorParseError(
                f"Erreur Wacoal : {exc}", context={"url": url}
            ) from exc

    def _parse(self, url: str, p: dict[str, Any]) -> RawProduct:
        variants: list[dict] = p.get("variants", [])
        options: list[dict]  = p.get("options", [])
        tags_raw = p.get("tags", [])
        tags: list[str] = (
            [t.strip() for t in tags_raw.split(",")]
            if isinstance(tags_raw, str) else list(tags_raw)
        )

        fv      = variants[0] if variants else {}
        price   = normalize_price(fv.get("price"))
        compare = normalize_price(fv.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)
        currency = self._market.currency if self._market else "USD"

        category_raw = p.get("product_type") or None
        if not category_raw:
            for tag in tags:
                if map_category_wacoal(tag):
                    category_raw = tag
                    break
        if not category_raw and "/collections/" in url:
            coll_slug = url.split("/collections/")[-1].split("/")[0]
            category_raw = coll_slug or None

        vendor    = p.get("vendor", "")
        sub_brand = extract_sub_brand_wacoal(vendor, tags, p.get("title", ""))
        all_sizes = extract_sizes(variants)
        cup_sizes = extract_cup_size_wacoal(all_sizes)

        detailed_variants = extract_variants_detailed(variants, options)

        # ── Fetch HTML unique (mutualisé pour tous les champs) ────────────
        html_content = self._fetch_product_html(url)

        # ── 1. Matières ───────────────────────────────────────────────────
        materials = extract_materials_from_wacoal_html(html_content)
        if not materials:
            from app.scraping.shopify_utils import extract_materials as _json_mats
            materials = _json_mats(p.get("body_html"))
        if materials:
            log.debug(
                "Wacoal matières",
                url=url,
                main=materials.get("material_main"),
                lining=materials.get("material_lining"),
            )

        # ── 2. Disponibilité variante corrigée ────────────────────────────
        sku_availability = extract_variant_availability_from_html(html_content)
        if sku_availability:
            detailed_variants = apply_html_availability_to_variants(
                detailed_variants, sku_availability
            )
            availability = (
                "in_stock"
                if any(v.get("available", False) for v in detailed_variants)
                else "out_of_stock"
            )
            log.debug(
                "Wacoal disponibilité corrigée via HTML",
                url=url,
                availability=availability,
            )
        else:
            availability = self._resolve_availability(variants, url)

        # ── 3. Rating depuis le HTML statique (sans requête supplémentaire)
        #
        # v2.0 : extract_rating_from_html() lit la note directement depuis
        # le HTML déjà fetchée. Sources consultées :
        #   - Bloc analytics Klaviyo `okendo: { rating, count }` (si disponible)
        #   - Balises <meta name="rating"> / <meta name="reviewCount">
        #   - JSON-LD AggregateRating
        # Zéro requête HTTP supplémentaire.
        #
        # Fallback : métafields Shopify (extract_rating_and_reviews)
        rating, review_count = extract_rating_from_html(html_content)

        if rating is None or review_count is None:
            mf_rating, mf_count = extract_rating_and_reviews(p.get("metafields"))
            if rating is None:
                rating = mf_rating
            if review_count is None:
                review_count = mf_count

        # ── 4. Identifiants pour les avis ─────────────────────────────────
        bv_handle, bv_numeric_id = extract_bv_identifiers(html_content)

        if not bv_numeric_id:
            bv_numeric_id = str(p.get("id", "")) or None

        log.debug(
            "Wacoal BV identifiants",
            url=url,
            handle=bv_handle,
            numeric_id=bv_numeric_id,
        )

        # ── 5. Avis texte — endpoint SEO BazaarVoice (sans passKey) ───────
        #
        # v2.0 : fetch_bv_seo_reviews() utilise l'endpoint public BV SEO :
        #   https://seo.bazaarvoice.com/wacoal-1038-en_US/product/<id>/reviews.djs
        # Aucun passKey requis. Dégradation gracieuse si inaccessible.
        reviews_data: list[dict] = []
        if bv_numeric_id:
            reviews_data = fetch_bv_seo_reviews(
                product_id=bv_numeric_id,
                limit=self._config.get("max_reviews", 100),
                delay_min=self.delay_min,
                delay_max=self.delay_max,
                headers=self._config.get("headers", {}),
            )
            if reviews_data:
                log.info(
                    "Wacoal avis BV SEO récupérés",
                    url=url,
                    count=len(reviews_data),
                )

        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="wacoal",
            price=price,
            original_price=compare if on_sale else None,
            currency=currency,
            on_sale=on_sale,
            category_raw=category_raw,
            description=clean_description(p.get("body_html")),
            images=[img["src"] for img in p.get("images", []) if img.get("src")],
            sizes=all_sizes,
            colors=extract_colors(variants),
            variants=detailed_variants,
            availability=availability,
            rating=rating,
            review_count=review_count,
            extra={
                "handle":       p.get("handle"),
                "tags":         tags,
                "vendor":       vendor,
                "sub_brand":    sub_brand,
                "cup_sizes":    cup_sizes,
                "is_best_seller": extract_best_seller_wacoal(
                    tags, self._config.get("best_seller_tags")
                ),
                "materials":         materials,
                "detailed_variants": detailed_variants,
                "reviews":           reviews_data,
                "bv_handle":         bv_handle,
                "bv_numeric_id":     bv_numeric_id,
            },
        )

    # ── Helper ────────────────────────────────────────────────────────────

    def _fetch_product_html(self, json_url: str) -> str:
        """
        Fetche la page HTML d'un produit Wacoal depuis son URL JSON.
        Ce fetch est mutualisé : matières, disponibilité, rating et avis
        sont tous extraits depuis ce HTML unique (un seul appel HTTP).
        Retourne "" en cas d'échec (non bloquant).
        """
        html_url = json_url.replace(".json", "")
        try:
            from app.scraping.http_client import HttpClient
            client = HttpClient(
                delay_min=self.delay_min,
                delay_max=self.delay_max,
                headers=self._config.get("headers", {}),
            )
            response = client.get(html_url, timeout=30)
            if response.status_code == 200:
                return response.text
            log.debug(
                "Wacoal fetch HTML échoué",
                url=html_url,
                status=response.status_code,
            )
        except Exception as exc:
            log.debug(
                "Wacoal fetch HTML exception",
                url=html_url,
                error=str(exc),
            )
        return ""