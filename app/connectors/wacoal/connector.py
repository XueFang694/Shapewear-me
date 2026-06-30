"""
Connecteur Wacoal America — moteur shopify_json.

Wacoal America (wacoal-america.com) est un site Shopify.
Ce connecteur cible spécifiquement la collection /collections/shapewear
ainsi que les catégories lingerie adjacentes.

Sous-marques couvertes :
  - Wacoal       : shapewear et lingerie premium
  - b.tempt'd    : lingerie lifestyle (vendor différent dans le JSON Shopify)
  - Wacoal Sport : soutiens-gorge sport

Support marché :
  Le connecteur lit le marché actif depuis settings.MARKET et adapte
  les en-têtes HTTP (Accept-Language) en conséquence.
  Pour cibler un autre domaine Wacoal (wacoal.co.uk, wacoal.fr, etc.),
  il suffit de surcharger base_url dans config.yml ou via settings.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.wacoal.connector import (
    extract_best_seller_wacoal,
    extract_cup_size_wacoal,
    extract_sub_brand_wacoal,
    map_category_wacoal,
)
from app.core.exceptions import ConnectorParseError
from app.core.logger import get_logger

log = get_logger(__name__)
_CONFIG_PATH = Path(__file__).parent / "config.yml"


class WacoalConnector(BaseConnector):
    """Connecteur pour Wacoal America (et variantes locales via config)."""

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__(config_path=config_path or _CONFIG_PATH)
        # Lire le marché actif pour adapter les en-têtes HTTP
        try:
            from app.core.market import get_market
            from app.core.config import settings
            self._market = get_market(settings.MARKET)
        except Exception:
            self._market = None

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="Wacoal America",
            slug="wacoal",
            version="1.0",
            engine="shopify_json",
            base_url=self.base_url,
        )

    def get_categories(self) -> list[Category]:
        """Retourne les collections shapewear configurées dans config.yml."""
        return [
            Category(
                slug=s,
                name=s.replace("-", " ").title(),
                url=f"{self.base_url}/collections/{s}",
                brand_slug="wacoal",
            )
            for s in self._config.get("target_collections", [])
        ]

    def get_product_urls(self, category: Category) -> list[str]:
        """
        Pagine l'endpoint Shopify pour récupérer tous les handles de produits
        d'une collection, puis construit les URLs .json individuelles.
        """
        from app.scraping.http_client import HttpClient
        from app.scraping.pagination import PaginationHandler

        # Fusionner les en-têtes du config avec ceux du marché actif
        base_headers = self._config.get("headers", {})
        if self._market:
            base_headers = {**base_headers, **self._market.get_http_headers()}

        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=base_headers,
        )
        pg = self._config.get("pagination", {})
        paginator = PaginationHandler(
            pagination_type=pg.get("type", "offset"),
            page_size=pg.get("page_size", 250),
            max_pages=pg.get("max_pages", 100),
        )
        base = f"{self.base_url}/collections/{category.slug}/products.json"
        handles: list[str] = []

        for url in paginator.iter_pages(base):
            try:
                r = client.get(url)
                if r.status_code != 200:
                    break
                products = r.json().get("products", [])
                if not products:
                    break
                handles.extend(p["handle"] for p in products if p.get("handle"))
                if len(products) < pg.get("page_size", 250):
                    break
            except Exception as exc:
                log.error("Erreur pagination Wacoal", url=url, error=str(exc))
                break

        urls = [f"{self.base_url}/products/{h}.json" for h in handles]
        log.info("URLs Wacoal", category=category.slug, count=len(urls))
        return urls

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

        # ── Prix (depuis la première variante disponible) ─────────────────
        fv      = variants[0] if variants else {}
        price   = normalize_price(fv.get("price"))
        compare = normalize_price(fv.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)

        # ── Devise adaptée au marché actif ────────────────────────────────
        currency = self._market.currency if self._market else "USD"

        # ── Catégorie ─────────────────────────────────────────────────────
        # Wacoal met souvent la catégorie dans product_type
        category_raw = p.get("product_type") or None
        if not category_raw:
            # Chercher dans les tags et dans le titre
            for tag in tags:
                mapped = map_category_wacoal(tag)
                if mapped:
                    category_raw = tag
                    break
        # Dernier recours : inférer depuis l'URL de la collection
        if not category_raw and "/collections/" in url:
            coll_slug = url.split("/collections/")[-1].split("/")[0]
            category_raw = coll_slug or None

        # ── Matériaux ────────────────────────────────────────────────────
        materials = extract_materials(p.get("body_html"))

        # ── Avis ─────────────────────────────────────────────────────────
        rating, review_count = extract_rating_and_reviews(p.get("metafields"))

        # ── Variantes détaillées ─────────────────────────────────────────
        detailed_variants = extract_variants_detailed(variants, options)

        # ── Tailles bonnet (spécifique Wacoal) ───────────────────────────
        all_sizes = extract_sizes(variants)
        cup_sizes = extract_cup_size_wacoal(all_sizes)

        # ── Sous-marque ──────────────────────────────────────────────────
        vendor    = p.get("vendor", "")
        sub_brand = extract_sub_brand_wacoal(vendor, tags, p.get("title", ""))

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
            availability=normalize_availability(variants),
            rating=rating,
            review_count=review_count,
            extra={
                "handle":      p.get("handle"),
                "tags":        tags,
                "vendor":      vendor,
                "sub_brand":   sub_brand,
                "cup_sizes":   cup_sizes,
                "is_best_seller": extract_best_seller_wacoal(
                    tags, self._config.get("best_seller_tags")
                ),
                "materials":          materials,
                "detailed_variants":  detailed_variants,
            },
        )