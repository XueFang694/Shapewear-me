"""
ShopifyConnectorMixin — Mixin partagé pour tous les connecteurs Shopify.

Centralise la logique commune de get_product_urls et le fallback HTML
de disponibilité, afin d'éviter la duplication entre les connecteurs
SPANX, SKIMS, Honeylove, Shapermint et Wacoal.

Usage :
    class MyConnector(ShopifyConnectorMixin, BaseConnector):
        def get_product_urls(self, category):
            return self._shopify_get_product_urls(category)

        def _parse(self, url, p):
            ...
            availability = self._resolve_availability(variants, url)
            ...
"""
from __future__ import annotations

from typing import Any

from app.core.logger import get_logger
from app.scraping.shopify_utils import (
    extract_availability_from_html,
    normalize_availability,
)

log = get_logger(__name__)


class ShopifyConnectorMixin:
    """
    Mixin pour les connecteurs Shopify.

    Requiert que la classe concrète hérite de BaseConnector et ait accès à :
      self._config, self.base_url, self.delay_min, self.delay_max
    """

    def _shopify_get_product_urls(self, category: Any) -> list[str]:
        """
        Pagine l'endpoint Shopify pour récupérer les handles de produits
        d'une collection, puis construit les URLs .json individuelles.
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
        brand_slug = self._config.get("slug", "unknown")
        base = f"{self.base_url}/collections/{category.slug}/products.json"
        handles: list[str] = []

        for page_url in paginator.iter_pages(base):
            try:
                from app.scraping.http_client import HttpClient as HC
                r = client.get(page_url)
                if r.status_code != 200:
                    break
                products = r.json().get("products", [])
                if not products:
                    break
                handles.extend(p["handle"] for p in products if p.get("handle"))
                if len(products) < pg.get("page_size", 250):
                    break
            except Exception as exc:
                log.error(
                    f"Erreur pagination {brand_slug}",
                    url=page_url,
                    error=str(exc),
                )
                break

        urls = [f"{self.base_url}/products/{h}.json" for h in handles]
        log.info(
            f"URLs {brand_slug}",
            category=category.slug,
            count=len(urls),
        )
        return urls

    def _resolve_availability(self, variants: list[dict], url: str) -> str:
        """
        Résout la disponibilité d'un produit.

        1. Utilise normalize_availability() (logique enrichie v2).
        2. Si le résultat est "out_of_stock" et que inventory_quantity est
           absent sur toutes les variantes → fallback HTML.

        Args:
            variants : liste des variantes Shopify JSON
            url      : URL de l'endpoint JSON (.json sera retiré pour le HTML)

        Returns:
            "in_stock" | "out_of_stock" | "unknown"
        """
        availability = normalize_availability(variants)

        # Fallback HTML si la disponibilité semble faussement "out_of_stock"
        # (cas où inventory_quantity est masqué par le store Shopify)
        log.debug("_resolve_availability()", url=url, availability=availability)
        if (availability == "out_of_stock" or availability == "Non") and _all_qty_missing(variants):
            html_url = url.replace(".json", "")
            try:
                from app.scraping.http_client import HttpClient
                client = HttpClient(
                    delay_min=getattr(self, "delay_min", 1.5),
                    delay_max=getattr(self, "delay_max", 4.0),
                    headers=self._config.get("headers", {}),
                )
                response = client.get(html_url, timeout=20)
                if response.status_code == 200:
                    html_avail = extract_availability_from_html(response.text)
                    if html_avail != "unknown":
                        brand = self._config.get("slug", "?")
                        log.debug(
                            "Disponibilité corrigée via HTML",
                            brand=brand,
                            url=url,
                            old="out_of_stock",
                            new=html_avail,
                        )
                        return html_avail
            except Exception as exc:
                log.debug(
                    "Fallback HTML disponibilité échoué",
                    url=url,
                    error=str(exc),
                )

        return availability


def _all_qty_missing(variants: list[dict]) -> bool:
    """Vérifie si inventory_quantity est absent sur TOUTES les variantes."""
    if not variants:
        return False
    return all(v.get("inventory_quantity") is None for v in variants)