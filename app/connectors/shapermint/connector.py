"""Connecteur Shapermint v1.2 — endpoint global /products.json.

Shapermint bloque les endpoints par collection
(/collections/<slug>/products.json → 404). Ce connecteur utilise donc
/products.json pour récupérer l'ensemble du catalogue, puis filtre les
produits par product_type pour ne conserver que les catégories shapewear
définies dans target_product_types du config.yml.

La méthode get_categories() retourne des catégories "virtuelles" basées
sur les product_types cibles ; get_product_urls() délègue la pagination
à _paginate_global() qui appelle /products.json directement.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.shapermint.mappings import extract_best_seller_sm, map_category_sm
from app.scraping.shopify_utils import (
    clean_description,
    extract_colors,
    extract_materials,
    extract_rating_and_reviews,
    extract_sizes,
    extract_variants_detailed,
    normalize_availability,
    normalize_price,
)
from app.core.exceptions import ConnectorParseError
from app.core.logger import get_logger

log = get_logger(__name__)
_CONFIG_PATH = Path(__file__).parent / "config.yml"

# Catégorie virtuelle unique utilisée pour le crawl global
_GLOBAL_CATEGORY_SLUG = "_all"


class ShapermintConnector(BaseConnector):

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)

        # Ensemble des product_types à conserver (insensible à la casse)
        raw_types: list[str] = self._config.get("target_product_types", [])
        self._target_types: frozenset[str] = frozenset(t.lower() for t in raw_types)

    # ── Métadonnées ───────────────────────────────────────────────────────

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="Shapermint",
            slug="shapermint",
            version="1.2",
            engine="shopify_json",
            base_url=self.base_url,
        )

    # ── Catégories ────────────────────────────────────────────────────────

    def get_categories(self) -> list[Category]:
        """
        Retourne une catégorie virtuelle unique "_all".
        Shapermint ne supporte pas les endpoints par collection ;
        le filtrage par type se fait après parsing.
        """
        return [
            Category(
                slug=_GLOBAL_CATEGORY_SLUG,
                name="All Shapewear",
                url=f"{self.base_url}/products.json",
                brand_slug="shapermint",
            )
        ]

    # ── URLs produits ─────────────────────────────────────────────────────

    def get_product_urls(self, category: Category) -> list[str]:
        """
        Pagine /products.json et retourne les URLs des produits
        dont le product_type est dans target_product_types.
        """
        from app.scraping.http_client import HttpClient
        from app.scraping.pagination import PaginationHandler

        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )
        pg = self._config.get("pagination", {})
        paginator = PaginationHandler(
            pagination_type=pg.get("type", "offset"),
            page_size=pg.get("page_size", 250),
            max_pages=pg.get("max_pages", 100),
        )

        base_url = f"{self.base_url}/products.json"
        handles: list[str] = []
        total_seen = 0
        total_kept = 0

        for page_url in paginator.iter_pages(base_url):
            try:
                r = client.get(page_url)
                if r.status_code != 200:
                    log.warning(
                        "Shapermint /products.json — statut inattendu",
                        status=r.status_code,
                        url=page_url,
                    )
                    break

                products = r.json().get("products", [])
                if not products:
                    break

                total_seen += len(products)

                for p in products:
                    handle = p.get("handle")
                    ptype  = (p.get("product_type") or "").lower()
                    # Filtre : conserver uniquement les types shapewear
                    if handle and (not self._target_types or ptype in self._target_types):
                        handles.append(handle)
                        total_kept += 1

                if len(products) < pg.get("page_size", 250):
                    break  # Dernière page

            except Exception as exc:
                log.error(
                    "Erreur pagination Shapermint",
                    url=page_url,
                    error=str(exc),
                )
                break

        urls = [f"{self.base_url}/products/{h}.json" for h in handles]
        log.info(
            "URLs Shapermint (endpoint global)",
            seen=total_seen,
            kept=total_kept,
            urls=len(urls),
        )
        return urls

    # ── Parsing produit ───────────────────────────────────────────────────

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
        variants = p.get("variants", [])
        options  = p.get("options", [])
        tags_raw = p.get("tags", [])
        tags = (
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
        materials          = extract_materials(p.get("body_html"))
        rating, review_count = extract_rating_and_reviews(p.get("metafields"))
        detailed_variants  = extract_variants_detailed(variants, options)
        availability       = normalize_availability(variants)

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
            sizes=extract_sizes(variants),
            colors=extract_colors(variants),
            variants=detailed_variants,
            availability=availability,
            rating=rating,
            review_count=review_count,
            extra={
                "handle":           p.get("handle"),
                "tags":             tags,
                "vendor":           p.get("vendor"),
                "is_best_seller":   extract_best_seller_sm(
                    tags, self._config.get("best_seller_tags")
                ),
                "materials":        materials,
                "detailed_variants": detailed_variants,
            },
        )