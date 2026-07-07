"""
Connecteur Wacoal America v1.4 — passKey BazaarVoice extrait dynamiquement.

CORRECTIF v1.4
───────────────

PROBLÈME (v1.3) :
  Le passKey BazaarVoice codé en dur (`caElc2g6VBb1LfIMOqRFWr0S0QGKuoiiu61f4RAlnKH7k`)
  était invalide pour tous les produits → ERROR_PARAM_INVALID_API_KEY systématique
  → count=0 et reviews=[] sur tous les produits.

CAUSE IDENTIFIÉE :
  Le vrai passKey est la `storefront_api_key` injectée dans chaque page produit
  par le pixel Shopify BazaarVoice (id 2305851608). Sa valeur est
  `8aa6f9892dc1113e84be4fe1f3d29c49` (visible dans le bloc webPixelsConfigList).
  Ce champ n'avait jamais été utilisé — l'ancienne clé provenait d'une source
  incorrecte.

SOLUTION :
  1. `extract_bv_pass_key(html)` (html_utils.py) extrait la clé depuis le HTML
     du fetch HTML déjà effectué (pas de requête supplémentaire).
  2. La clé est transmise via le paramètre `pass_key` des fonctions BV.
  3. La constante statique `_BV_PASS_KEY` dans html_utils.py est aussi corrigée
     comme fallback en cas d'échec d'extraction.

AUCUN AUTRE CHANGEMENT par rapport à v1.3.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.wacoal.html_utils import (
    apply_html_availability_to_variants,
    extract_bv_identifiers,
    extract_bv_pass_key,
    extract_materials_from_wacoal_html,
    extract_variant_availability_from_html,
    fetch_bv_rating_with_fallback,
    fetch_bv_reviews_with_fallback,
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
    extract_sizes,
    extract_variants_detailed,
    normalize_availability,
    normalize_price,
    extract_rating_and_reviews,
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
            name="Wacoal America", slug="wacoal", version="1.4",
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

        # ── Fetch HTML unique (mutualisé pour tous les champs HTML) ───────
        html_content = self._fetch_product_html(url)

        # ── CORRECTIF v1.4 : extraire le passKey BV depuis le HTML ────────
        bv_pass_key = extract_bv_pass_key(html_content) if html_content else None
        if bv_pass_key:
            log.debug("PassKey BV extrait du HTML", url=url, key_prefix=bv_pass_key[:8])
        else:
            log.debug("PassKey BV fallback statique utilisé", url=url)

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
                unavailable=sum(1 for v in detailed_variants if not v.get("available")),
                total=len(detailed_variants),
            )
        else:
            availability = self._resolve_availability(variants, url)

        # ── 3. Note et avis BazaarVoice ───────────────────────────────────
        #
        # CORRECTIF v1.4 : `pass_key=bv_pass_key` transmis à toutes les
        # fonctions BV. Le passKey est maintenant extrait depuis le HTML de la
        # page produit (storefront_api_key du pixel 2305851608), ce qui garantit
        # l'utilisation de la clé valide pour chaque requête.
        #
        # Avant v1.4 : _BV_PASS_KEY = "caElc2g6VBb1LfIMOqRFWr0S0QGKuoiiu61f4RAlnKH7k"
        #              → ERROR_PARAM_INVALID_API_KEY → count=0 systématique
        # Après v1.4  : pass_key = "8aa6f9892dc1113e84be4fe1f3d29c49" (depuis HTML)
        #              → requêtes BV fonctionnelles

        rating, review_count = extract_rating_and_reviews(p.get("metafields"))

        bv_handle, bv_numeric_id = extract_bv_identifiers(html_content)

        if not bv_handle and not bv_numeric_id:
            bv_numeric_id = str(p.get("id", "")) or None

        log.debug(
            "Wacoal BV identifiants",
            url=url,
            handle=bv_handle,
            numeric_id=bv_numeric_id,
        )

        if (rating is None or review_count is None) and (bv_handle or bv_numeric_id):
            bv_rating, bv_count = fetch_bv_rating_with_fallback(
                handle=bv_handle,
                numeric_id=bv_numeric_id,
                delay_min=self.delay_min,
                delay_max=self.delay_max,
                headers=self._config.get("headers", {}),
                pass_key=bv_pass_key,  # CORRECTIF v1.4
            )
            if rating is None:
                rating = bv_rating
            if review_count is None:
                review_count = bv_count

        reviews_data: list[dict] = []
        if bv_handle or bv_numeric_id:
            reviews_data = fetch_bv_reviews_with_fallback(
                handle=bv_handle,
                numeric_id=bv_numeric_id,
                limit=100,
                delay_min=self.delay_min,
                delay_max=self.delay_max,
                headers=self._config.get("headers", {}),
                pass_key=bv_pass_key,  # CORRECTIF v1.4
            )
            if reviews_data:
                log.debug(
                    "Wacoal avis BV récupérés",
                    url=url,
                    count=len(reviews_data),
                    rating=rating,
                    via_handle=bool(bv_handle),
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

    # ── Helpers ───────────────────────────────────────────────────────────

    def _fetch_product_html(self, json_url: str) -> str:
        """
        Fetche la page HTML d'un produit Wacoal depuis son URL JSON.
        Ce fetch est mutualisé : matières, disponibilité, passKey BV et avis
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