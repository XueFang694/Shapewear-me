"""
Connecteur SPANX v3.1 — matières, Best Seller et compression depuis le HTML.

CORRECTIF v3.1 (ajout) :
  - Compression : niveau de compression extrait depuis le payload RSC HTML
    ("light" | "medium" | "strong" | "extra_strong") et stocké dans
    extra["compression_level"] pour transmission au Normalizer / classifier.

CORRECTIF v3 (conservé) :
  - Matières : extraites depuis le payload RSC (non disponibles via l'API JSON).
  - Best Seller : badge détecté depuis le payload RSC.
  - Un seul fetch HTML par produit (mutualisé pour les trois champs).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.spanx.mappings import extract_best_seller, map_category
from app.connectors.spanx.html_utils import (
    extract_materials_from_spanx_html,
    extract_is_best_seller_from_spanx_html,
    extract_compression_from_spanx_html,
)
from app.scraping.shopify_utils import (
    clean_description, extract_colors,
    extract_rating_and_reviews, extract_sizes, extract_variants_detailed,
    normalize_availability, extract_availability_from_html,
    normalize_price,
)
from app.core.exceptions import ConnectorParseError
from app.core.logger import get_logger

log = get_logger(__name__)
_CONFIG_PATH = Path(__file__).parent / "config.yml"


class SpanxConnector(BaseConnector):

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__(config_path=config_path or _CONFIG_PATH)

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="SPANX", slug="spanx", version="3.1",
            engine="shopify_json", base_url=self.base_url,
        )

    def get_categories(self) -> list[Category]:
        targets: list[str] = self._config.get("target_collections", [])
        return [
            Category(
                slug=slug,
                name=slug.replace("-", " ").title(),
                url=f"{self.base_url}/collections/{slug}",
                brand_slug="spanx",
            )
            for slug in targets
        ]

    def get_product_urls(self, category: Category) -> list[str]:
        from app.scraping.http_client import HttpClient
        from app.scraping.pagination import PaginationHandler

        client = HttpClient(
            delay_min=self.delay_min, delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )
        pg_cfg = self._config.get("pagination", {})
        paginator = PaginationHandler(
            pagination_type=pg_cfg.get("type", "offset"),
            page_size=pg_cfg.get("page_size", 250),
            max_pages=pg_cfg.get("max_pages", 100),
        )
        base_endpoint = f"{self.base_url}/collections/{category.slug}/products.json"
        handles: list[str] = []

        for page_url in paginator.iter_pages(base_endpoint):
            try:
                response = client.get(page_url)
                if response.status_code != 200:
                    break
                products = response.json().get("products", [])
                if not products:
                    break
                handles.extend(p["handle"] for p in products if p.get("handle"))
                if len(products) < pg_cfg.get("page_size", 250):
                    break
            except Exception as exc:
                log.error("Erreur pagination", url=page_url, error=str(exc))
                break

        urls = [f"{self.base_url}/products/{h}.json" for h in handles]
        log.info("URLs produits SPANX", category=category.slug, count=len(urls))
        return urls

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        if not isinstance(data, dict):
            raise ConnectorParseError("parse_product attend un dict", context={"url": url})
        try:
            return self._parse_shopify_product(url, data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorParseError(
                f"Erreur parsing SPANX : {exc}",
                context={"url": url, "product_id": data.get("id")},
            ) from exc

    def _parse_shopify_product(self, url: str, p: dict[str, Any]) -> RawProduct:
        variants: list[dict] = p.get("variants", [])
        options: list[dict]  = p.get("options", [])
        tags_raw = p.get("tags", [])
        tags: list[str] = (
            [t.strip() for t in tags_raw.split(",")]
            if isinstance(tags_raw, str) else list(tags_raw)
        )

        price = original_price = None
        on_sale = False
        if variants:
            fv = variants[0]
            price   = normalize_price(fv.get("price"))
            compare = normalize_price(fv.get("compare_at_price"))
            if compare and price and compare > price:
                original_price = compare
                on_sale = True

        category_raw = p.get("product_type") or None
        if not category_raw:
            for tag in tags:
                if map_category(tag):
                    category_raw = tag
                    break

        rating, review_count = extract_rating_and_reviews(p.get("metafields"))
        detailed_variants = extract_variants_detailed(variants, options)
        images = [img["src"] for img in p.get("images", []) if img.get("src")]

        # ── Un seul fetch HTML — mutualisé pour tous les champs HTML ─────
        html_content = self._fetch_product_html(url)

        # ── Disponibilité ─────────────────────────────────────────────────
        availability = normalize_availability(variants)
        if (availability == "out_of_stock" and self._all_qty_missing(variants)
                and html_content):
            html_avail = extract_availability_from_html(html_content)
            if html_avail != "unknown":
                log.debug(
                    "Disponibilité corrigée via HTML",
                    brand="spanx", url=url, availability=html_avail,
                )
                availability = html_avail

        # ── Matières ──────────────────────────────────────────────────────
        materials = extract_materials_from_spanx_html(html_content)
        if materials:
            log.debug(
                "Matières extraites via HTML",
                brand="spanx", url=url, main=materials.get("material_main"),
            )
        else:
            from app.scraping.shopify_utils import extract_materials as _json_mat
            materials = _json_mat(p.get("body_html"))

        # ── Best Seller ───────────────────────────────────────────────────
        is_best_seller = extract_is_best_seller_from_spanx_html(html_content)
        if is_best_seller:
            log.debug("Best Seller détecté via HTML", brand="spanx", url=url)
        else:
            is_best_seller = extract_best_seller(tags)

        # ── Niveau de compression ─────────────────────────────────────────
        compression_level = extract_compression_from_spanx_html(html_content)
        if compression_level:
            log.debug(
                "Compression extraite via HTML",
                brand="spanx", url=url, level=compression_level,
            )

        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="spanx",
            price=price,
            original_price=original_price,
            currency="USD",
            on_sale=on_sale,
            category_raw=category_raw,
            description=clean_description(p.get("body_html")),
            images=images,
            sizes=extract_sizes(variants),
            colors=extract_colors(variants),
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
                "compression_level": compression_level,
                "detailed_variants": detailed_variants,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _fetch_product_html(self, json_url: str) -> str:
        """
        Fetch la page HTML d'un produit Spanx depuis son URL JSON.
        Retourne une chaîne vide en cas d'échec (non bloquant).
        Ce fetch unique est mutualisé pour disponibilité + matières +
        best seller + compression.
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
                "Fetch HTML produit échoué",
                brand="spanx", url=html_url, status=response.status_code,
            )
        except Exception as exc:
            log.debug(
                "Fetch HTML produit exception",
                brand="spanx", url=html_url, error=str(exc),
            )
        return ""

    @staticmethod
    def _all_qty_missing(variants: list[dict]) -> bool:
        if not variants:
            return False
        return all(v.get("inventory_quantity") is None for v in variants)