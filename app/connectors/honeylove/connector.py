"""Connecteur Honeylove — moteur shopify_json."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.honeylove.mappings import extract_best_seller_hl, map_category_hl
from app.connectors.spanx.mappings import (
    clean_description, extract_colors, extract_materials,
    extract_rating_and_reviews, extract_sizes, extract_variants_detailed,
    normalize_availability, normalize_price,
)
from app.core.exceptions import ConnectorParseError
from app.core.logger import get_logger

log = get_logger(__name__)
_CONFIG_PATH = Path(__file__).parent / "config.yml"


class HoneyloveConnector(BaseConnector):
    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(name="Honeylove", slug="honeylove", version="1.0",
                             engine="shopify_json", base_url=self.base_url)

    def get_categories(self) -> list[Category]:
        return [
            Category(slug=s, name=s.replace("-", " ").title(),
                     url=f"{self.base_url}/collections/{s}", brand_slug="honeylove")
            for s in self._config.get("target_collections", [])
        ]

    def get_product_urls(self, category: Category) -> list[str]:
        from app.scraping.http_client import HttpClient
        from app.scraping.pagination import PaginationHandler
        client = HttpClient(delay_min=self.delay_min, delay_max=self.delay_max,
                            headers=self._config.get("headers", {}))
        pg = self._config.get("pagination", {})
        paginator = PaginationHandler(pagination_type=pg.get("type", "offset"),
                                      page_size=pg.get("page_size", 250),
                                      max_pages=pg.get("max_pages", 100))
        base = f"{self.base_url}/collections/{category.slug}/products.json"
        handles: list[str] = []
        for url in paginator.iter_pages(base):
            try:
                r = client.get(url)
                if r.status_code != 200: break
                products = r.json().get("products", [])
                if not products: break
                handles.extend(p["handle"] for p in products if p.get("handle"))
                if len(products) < pg.get("page_size", 250): break
            except Exception as exc:
                log.error("Erreur pagination Honeylove", url=url, error=str(exc)); break
        urls = [f"{self.base_url}/products/{h}.json" for h in handles]
        log.info("URLs Honeylove", category=category.slug, count=len(urls))
        return urls

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        if not isinstance(data, dict):
            raise ConnectorParseError("dict attendu", context={"url": url})
        try:
            return self._parse(url, data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorParseError(f"Erreur Honeylove: {exc}", context={"url": url}) from exc

    def _parse(self, url: str, p: dict[str, Any]) -> RawProduct:
        variants = p.get("variants", [])
        options  = p.get("options", [])
        tags_raw = p.get("tags", [])
        tags = [t.strip() for t in tags_raw.split(",")] if isinstance(tags_raw, str) else list(tags_raw)
        fv = variants[0] if variants else {}
        price   = normalize_price(fv.get("price"))
        compare = normalize_price(fv.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)
        category_raw = p.get("product_type") or next((t for t in tags if map_category_hl(t)), None)
        materials = extract_materials(p.get("body_html"))
        rating, review_count = extract_rating_and_reviews(p.get("metafields"))
        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="honeylove",
            price=price,
            original_price=compare if on_sale else None,
            currency="USD", on_sale=on_sale,
            category_raw=category_raw,
            description=clean_description(p.get("body_html")),
            images=[img["src"] for img in p.get("images", []) if img.get("src")],
            sizes=extract_sizes(variants),
            colors=extract_colors(variants),
            variants=extract_variants_detailed(variants, options),
            availability=normalize_availability(variants),
            rating=rating, review_count=review_count,
            extra={
                "handle": p.get("handle"), "tags": tags, "vendor": p.get("vendor"),
                "is_best_seller": extract_best_seller_hl(tags, self._config.get("best_seller_tags")),
                "materials": materials,
                "detailed_variants": extract_variants_detailed(variants, options),
            },
        )