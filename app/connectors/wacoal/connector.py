"""
Connecteur Wacoal America v1.2 — matières, disponibilité et avis.

CORRECTIFS v1.2
───────────────

1. MATIÈRES (nouveau)
   Le JSON Shopify /products/<handle>.json de Wacoal n'expose pas la composition
   textile. Elle est dans le HTML de la page produit, dans un span
   <span class="metafield-single_line_text_field"> au sein de la section
   "fabric/care" du composant ProductDescList.
   → Un fetch HTML est effectué une fois par produit (mutualisé avec la
     disponibilité et les avis) via _fetch_product_html().
   → extract_materials_from_wacoal_html() extrait et normalise la composition.

2. DISPONIBILITÉ VARIANTE (correctif)
   Wacoal masque inventory_quantity et inventory_policy dans son JSON Shopify.
   La logique générique _variant_is_available() (shopify_utils.py) considère
   comme "cas ambigu" toute variante dont available=False et qty est absent,
   et retourne True par défaut — ce qui est incorrect pour Wacoal.
   → Le HTML de la page produit expose le même JSON Shopify (mntn_product_data
     ou Shop._PRODUCT_JSON_) avec le champ "available" (boolean) par variante.
   → extract_variant_availability_from_html() lit directement ce boolean.
   → apply_html_availability_to_variants() l'applique aux variantes parsées.
   → La disponibilité globale du produit est recalculée à partir des variantes
     corrigées.

3. NOTE ET AVIS (nouveau)
   BazaarVoice est chargé de façon asynchrone (JS) ; les divs data-bv-show
   sont vides dans le HTML statique. Les metafields Yotpo/Loox sont null.
   → fetch_bv_rating() appelle l'API BazaarVoice Statistics avec le passkey
     public Wacoal pour récupérer (rating, review_count).
   → fetch_bv_reviews() appelle l'API BazaarVoice Reviews pour récupérer
     les avis texte (titre, corps, note, variante, date), stockés en JSON.
   → Le product_id BazaarVoice est l'ID Shopify numérique du produit,
     exposé dans la page HTML via data-bv-product-id.
   → Si l'API BV échoue (réseau, rate-limit), on tombe silencieusement sur
     (None, None) et la suite du pipeline n'est pas impactée.

ARCHITECTURE
────────────
  _fetch_product_html()          : un seul fetch HTML par produit (mutualisé)
  extract_materials_from_wacoal_html()      : matières depuis le HTML
  extract_variant_availability_from_html()  : disponibilité depuis le HTML
  apply_html_availability_to_variants()     : correction des variantes
  fetch_bv_rating()                         : note via API BazaarVoice
  fetch_bv_reviews()                        : avis via API BazaarVoice

  Tout cela est importé depuis app/connectors/wacoal/html_utils.py.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.wacoal.html_utils import (
    apply_html_availability_to_variants,
    extract_materials_from_wacoal_html,
    extract_variant_availability_from_html,
    fetch_bv_rating,
    fetch_bv_reviews,
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

# Regex pour extraire l'identifiant BazaarVoice depuis la page HTML produit
_BV_PRODUCT_ID_RE = re.compile(r'data-bv-product-id="(\d+)"')


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
            name="Wacoal America", slug="wacoal", version="1.2",
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
        # Fusionner les en-têtes marché avant pagination
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

        # Catégorie brute
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

        # Variantes Shopify
        detailed_variants = extract_variants_detailed(variants, options)

        # ── Fetch HTML unique (mutualisé pour matières, dispo, BV) ────────
        html_content = self._fetch_product_html(url)

        # ── 1. Matières ───────────────────────────────────────────────────
        materials = extract_materials_from_wacoal_html(html_content)
        if not materials:
            # Fallback : tentative d'extraction depuis le body_html JSON
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
        # Wacoal masque inventory_quantity dans son JSON Shopify.
        # Le champ "available" (boolean) dans le HTML est la source fiable.
        sku_availability = extract_variant_availability_from_html(html_content)
        if sku_availability:
            detailed_variants = apply_html_availability_to_variants(
                detailed_variants, sku_availability
            )
            # Recalculer la disponibilité globale depuis les variantes corrigées
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
            # Fallback : logique générique (moins fiable pour Wacoal)
            availability = self._resolve_availability(variants, url)

        # ── 3. Note et avis BazaarVoice ───────────────────────────────────
        # La note n'est pas dans le JSON Shopify ni dans le HTML statique.
        # On utilise d'abord le produit JSON (metafields si disponibles),
        # puis l'API BazaarVoice en fallback.
        rating, review_count = extract_rating_and_reviews(p.get("metafields"))

        # Extraire le product_id BazaarVoice depuis le HTML
        bv_product_id = _extract_bv_product_id(html_content) or str(p.get("id", ""))

        if (rating is None or review_count is None) and bv_product_id:
            bv_rating, bv_count = fetch_bv_rating(
                bv_product_id,
                delay_min=self.delay_min,
                delay_max=self.delay_max,
                headers=self._config.get("headers", {}),
            )
            if rating is None:
                rating = bv_rating
            if review_count is None:
                review_count = bv_count

        # Avis texte BazaarVoice
        reviews_data: list[dict] = []
        if bv_product_id:
            reviews_data = fetch_bv_reviews(
                bv_product_id,
                limit=100,
                delay_min=self.delay_min,
                delay_max=self.delay_max,
                headers=self._config.get("headers", {}),
            )
            if reviews_data:
                log.debug(
                    "Wacoal avis BV",
                    url=url,
                    count=len(reviews_data),
                    rating=rating,
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
                "bv_product_id":     bv_product_id,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _fetch_product_html(self, json_url: str) -> str:
        """
        Fetche la page HTML d'un produit Wacoal depuis son URL JSON.

        Ce fetch est mutualisé : les matières, la disponibilité et les avis
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


def _extract_bv_product_id(html: str) -> str | None:
    """Extrait l'identifiant BazaarVoice (= ID Shopify) depuis le HTML."""
    if not html:
        return None
    m = _BV_PRODUCT_ID_RE.search(html)
    return m.group(1) if m else None