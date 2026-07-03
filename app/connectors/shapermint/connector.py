"""Connecteur Shapermint v1.3 — fallback multi-endpoints.

Shapermint bloque /products.json à la racine (HTTP 404).
Ce connecteur essaie les endpoints dans l'ordre défini par product_list_endpoints
dans config.yml, puis bascule en mode par-collection si tous échouent.

Ordre de tentative :
  1. /en-US/products.json  (endpoint localisé — peut fonctionner selon la config Shopify)
  2. /products.json         (endpoint standard Shopify)
  3. /collections/<slug>/products.json  pour chaque slug de target_collections (fallback)

Changements v1.3 :
  - Logique de découverte d'endpoint avec fallback automatique.
  - Warning clair sur chaque endpoint qui échoue.
  - Mode par-collection activé si aucun endpoint global ne répond.
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

_GLOBAL_CATEGORY_SLUG = "_all"


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
            version="1.3",
            engine="shopify_json",
            base_url=self.base_url,
        )

    # ── Catégories ────────────────────────────────────────────────────────

    def get_categories(self) -> list[Category]:
        """
        Retourne une catégorie virtuelle unique "_all".
        get_product_urls() tente d'abord les endpoints globaux, puis bascule
        en mode par-collection si aucun endpoint global ne répond.
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
        Tente les endpoints globaux dans l'ordre, puis bascule
        sur la pagination par collection si tous échouent.
        """
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {}),
        )

        # Essayer les endpoints globaux dans l'ordre
        global_endpoints: list[str] = self._config.get("product_list_endpoints", [
            "/en-US/products.json",
            "/products.json",
        ])

        for endpoint in global_endpoints:
            probe_url = f"{self.base_url}{endpoint}?page=1&limit=1"
            try:
                r = client.get(probe_url)
                if r.status_code == 200:
                    data = r.json()
                    if "products" in data:
                        log.info(
                            "Shapermint endpoint global trouvé",
                            endpoint=endpoint,
                        )
                        return self._paginate_global(client, endpoint)
                    else:
                        log.warning(
                            "Shapermint endpoint répond 200 mais sans 'products'",
                            endpoint=endpoint,
                            keys=list(data.keys())[:5],
                        )
                else:
                    log.warning(
                        "Shapermint endpoint global indisponible",
                        endpoint=endpoint,
                        status=r.status_code,
                    )
            except Exception as exc:
                log.warning(
                    "Shapermint erreur sonde endpoint",
                    endpoint=endpoint,
                    error=str(exc),
                )

        # Fallback : pagination par collection
        log.warning(
            "Shapermint — aucun endpoint global disponible, bascule sur mode par-collection",
            tried=global_endpoints,
            fallback_collections=self._config.get("target_collections", []),
        )
        return self._paginate_by_collections(client)

    # ── Pagination globale ────────────────────────────────────────────────

    def _paginate_global(self, client: Any, endpoint: str) -> list[str]:
        """Pagine l'endpoint global et retourne les handles filtrés."""
        from app.scraping.pagination import PaginationHandler

        pg = self._config.get("pagination", {})
        paginator = PaginationHandler(
            pagination_type=pg.get("type", "offset"),
            page_size=pg.get("page_size", 250),
            max_pages=pg.get("max_pages", 100),
        )

        base_url = f"{self.base_url}{endpoint}"
        handles: list[str] = []
        total_seen = total_kept = page_count = 0

        for page_url in paginator.iter_pages(base_url):
            try:
                r = client.get(page_url)
                page_count += 1

                if r.status_code != 200:
                    log.error(
                        "Shapermint pagination globale — statut inattendu",
                        status=r.status_code, url=page_url,
                    )
                    break

                products = r.json().get("products", [])
                if not products:
                    break

                total_seen += len(products)
                for p in products:
                    handle = p.get("handle")
                    ptype  = (p.get("product_type") or "").lower().strip()
                    if handle and (not self._target_types or ptype in self._target_types):
                        handles.append(handle)
                        total_kept += 1

                if page_count % 5 == 0:
                    log.debug(
                        "Shapermint pagination globale en cours",
                        page=page_count, seen=total_seen, kept=total_kept,
                    )

                if len(products) < pg.get("page_size", 250):
                    break

            except Exception as exc:
                log.error(
                    "Shapermint erreur pagination globale",
                    url=page_url, error=str(exc),
                )
                break

        urls = [f"{self.base_url}/products/{h}.json" for h in handles]
        log.info(
            "URLs Shapermint (global)",
            endpoint=endpoint, pages=page_count,
            seen=total_seen, kept=total_kept, urls=len(urls),
        )
        return urls

    # ── Pagination par collection (fallback) ──────────────────────────────

    def _paginate_by_collections(self, client: Any) -> list[str]:
        """
        Pagination par collection — fallback si l'endpoint global est indisponible.
        Tente chaque slug de target_collections.
        """
        from app.scraping.pagination import PaginationHandler

        pg = self._config.get("pagination", {})
        paginator = PaginationHandler(
            pagination_type=pg.get("type", "offset"),
            page_size=pg.get("page_size", 250),
            max_pages=pg.get("max_pages", 100),
        )

        target_collections: list[str] = self._config.get("target_collections", [])
        all_handles: list[str] = []
        seen_handles: set[str] = set()

        for slug in target_collections:
            base_url = f"{self.base_url}/collections/{slug}/products.json"
            found_any = False

            for page_url in paginator.iter_pages(base_url):
                try:
                    r = client.get(page_url)

                    if r.status_code == 404:
                        log.warning(
                            "Shapermint collection introuvable (404)",
                            slug=slug, url=page_url,
                        )
                        break

                    if r.status_code != 200:
                        log.error(
                            "Shapermint collection — statut inattendu",
                            slug=slug, status=r.status_code,
                        )
                        break

                    products = r.json().get("products", [])
                    if not products:
                        break

                    found_any = True
                    for p in products:
                        handle = p.get("handle")
                        if handle and handle not in seen_handles:
                            ptype = (p.get("product_type") or "").lower().strip()
                            if not self._target_types or ptype in self._target_types:
                                all_handles.append(handle)
                                seen_handles.add(handle)

                    if len(products) < pg.get("page_size", 250):
                        break

                except Exception as exc:
                    log.error(
                        "Shapermint erreur pagination collection",
                        slug=slug, url=page_url, error=str(exc),
                    )
                    break

            if found_any:
                log.info("Shapermint collection crawlée", slug=slug)

        urls = [f"{self.base_url}/products/{h}.json" for h in all_handles]
        log.info(
            "URLs Shapermint (fallback par-collection)",
            collections=len(target_collections), urls=len(urls),
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
        materials            = extract_materials(p.get("body_html"))
        rating, review_count = extract_rating_and_reviews(p.get("metafields"))
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
                "is_best_seller":    extract_best_seller_sm(
                    tags, self._config.get("best_seller_tags")
                ),
                "materials":         materials,
                "detailed_variants": detailed_variants,
            },
        )