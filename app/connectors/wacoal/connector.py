"""
Connecteur Wacoal America v1.1 — avec fallback HTML pour la disponibilité.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.wacoal.mapping import (
    extract_best_seller_wacoal, extract_cup_size_wacoal,
    extract_sub_brand_wacoal, map_category_wacoal,
)
from app.scraping.shopify_connector_mixin import ShopifyConnectorMixin
from app.scraping.shopify_utils import (
    clean_description, extract_colors, extract_materials,
    extract_rating_and_reviews, extract_sizes, extract_variants_detailed,
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

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="Wacoal America", slug="wacoal", version="1.1",
            engine="shopify_json", base_url=self.base_url,
        )

    def get_categories(self) -> list[Category]:
        return [
            Category(slug=s, name=s.replace("-", " ").title(),
                     url=f"{self.base_url}/collections/{s}", brand_slug="wacoal")
            for s in self._config.get("target_collections", [])
        ]

    def get_product_urls(self, category: Category) -> list[str]:
        # Wacoal fusionne les en-têtes marché dans la config avant pagination
        if self._market:
            market_headers = self._market.get_http_headers()
            original_headers = self._config.get("headers", {})
            self._config["headers"] = {**original_headers, **market_headers}
        return self._shopify_get_product_urls(category)

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

        materials = extract_materials(p.get("body_html"))
        rating, review_count = extract_rating_and_reviews(p.get("metafields"))
        detailed_variants = extract_variants_detailed(variants, options)
        all_sizes = extract_sizes(variants)
        cup_sizes = extract_cup_size_wacoal(all_sizes)
        vendor    = p.get("vendor", "")
        sub_brand = extract_sub_brand_wacoal(vendor, tags, p.get("title", ""))

        # Disponibilité avec fallback HTML
        availability = self._resolve_availability(variants, url)

        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="wacoal",
            price=price,
            original_price=compare if on_sale else None,
            currency=currency, on_sale=on_sale,
            category_raw=category_raw,
            description=clean_description(p.get("body_html")),
            images=[img["src"] for img in p.get("images", []) if img.get("src")],
            sizes=all_sizes,
            colors=extract_colors(variants),
            variants=detailed_variants,
            availability=availability,
            rating=rating, review_count=review_count,
            extra={
                "handle":      p.get("handle"), "tags": tags, "vendor": vendor,
                "sub_brand":   sub_brand, "cup_sizes": cup_sizes,
                "is_best_seller": extract_best_seller_wacoal(
                    tags, self._config.get("best_seller_tags")
                ),
                "materials":         materials,
                "detailed_variants": detailed_variants,
            },
        )