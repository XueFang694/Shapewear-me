"""Connecteur Honeylove v1.2 — filtre les variantes géographiques.

Honeylove publie chaque produit en plusieurs handles Shopify distincts selon
le marché cible :
    cami-bodysuit          (générique / UK par défaut)
    cami-bodysuit-us       (US)
    cami-bodysuit-ca       (Canada)
    cami-bodysuit-au       (Australie)
    cami-bodysuit-gb       (Grande-Bretagne)

Stratégie de déduplication dans _filter_handles() :
  1. Rejeter tout handle dont le suffixe final figure dans config
     market_suffixes.excluded.
  2. Pour chaque handle de base (ex: "cami-bodysuit"), si une version
     preferred (ex: "cami-bodysuit-us") existe dans la liste, ne garder
     que celle-là et supprimer le générique.
  3. Si aucune version preferred n'existe pour un handle générique, le
     conserver tel quel.

Résultat : une seule URL par produit, toujours la version US quand elle
est disponible.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.honeylove.mappings import extract_best_seller_hl, map_category_hl
from app.scraping.shopify_connector_mixin import ShopifyConnectorMixin
from app.scraping.shopify_utils import (
    clean_description,
    extract_colors,
    extract_materials,
    extract_rating_and_reviews,
    extract_sizes,
    extract_variants_detailed,
    normalize_price,
)
from app.core.exceptions import ConnectorParseError
from app.core.logger import get_logger

log = get_logger(__name__)
_CONFIG_PATH = Path(__file__).parent / "config.yml"


class HoneyloveConnector(ShopifyConnectorMixin, BaseConnector):

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)

        # Construire les ensembles de suffixes depuis la config
        ms = self._config.get("market_suffixes", {})
        self._preferred_suffix: str = ms.get("preferred", "us")
        self._excluded_suffixes: frozenset[str] = frozenset(
            ms.get("excluded", ["ca", "au", "gb", "uk", "eu", "nz", "ie"])
        )

    # ── Métadonnées ───────────────────────────────────────────────────────

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="Honeylove",
            slug="honeylove",
            version="1.2",
            engine="shopify_json",
            base_url=self.base_url,
        )

    # ── Catégories ────────────────────────────────────────────────────────

    def get_categories(self) -> list[Category]:
        return [
            Category(
                slug=s,
                name=s.replace("-", " ").title(),
                url=f"{self.base_url}/collections/{s}",
                brand_slug="honeylove",
            )
            for s in self._config.get("target_collections", [])
        ]

    # ── URLs produits ─────────────────────────────────────────────────────

    def get_product_urls(self, category: Category) -> list[str]:
        raw_urls = self._shopify_get_product_urls(category)
        handles  = [self._handle_from_url(u) for u in raw_urls]
        filtered = self._filter_handles(handles)
        urls     = [f"{self.base_url}/products/{h}.json" for h in filtered]

        log.info(
            "URLs Honeylove après déduplication géographique",
            category=category.slug,
            before=len(raw_urls),
            after=len(urls),
            removed=len(raw_urls) - len(urls),
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
                f"Erreur Honeylove: {exc}", context={"url": url}
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
            (t for t in tags if map_category_hl(t)), None
        )
        materials          = extract_materials(p.get("body_html"))
        rating, review_count = extract_rating_and_reviews(p.get("metafields"))
        detailed_variants  = extract_variants_detailed(variants, options)
        availability       = self._resolve_availability(variants, url)

        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="honeylove",
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
                "is_best_seller":   extract_best_seller_hl(
                    tags, self._config.get("best_seller_tags")
                ),
                "materials":        materials,
                "detailed_variants": detailed_variants,
            },
        )

    # ── Filtrage géographique ─────────────────────────────────────────────

    def _filter_handles(self, handles: list[str]) -> list[str]:
        """
        Déduplique les handles selon la stratégie de marché :

        1. Construire l'ensemble des handles qui ont une version preferred
           (ex: "cami-bodysuit-us" → base "cami-bodysuit" a un preferred).
        2. Pour chaque handle :
           - Si son suffixe est dans excluded → rejeter.
           - Si c'est un générique (sans suffixe de marché) ET qu'un
             preferred existe pour ce même base → rejeter (doublon).
           - Sinon → conserver.
        """
        handle_set = set(handles)

        # Index : base_slug → True si la version preferred existe dans la liste
        preferred_exists: set[str] = set()
        for h in handles:
            base, suffix = self._split_market_suffix(h)
            if suffix == self._preferred_suffix:
                preferred_exists.add(base)

        result: list[str] = []
        seen_bases: set[str] = set()

        for h in handles:
            base, suffix = self._split_market_suffix(h)

            # 1. Rejeter les marchés exclus explicitement
            if suffix in self._excluded_suffixes:
                log.debug("Handle exclu (marché)", handle=h, suffix=suffix)
                continue

            # 2. Rejeter le générique si une version preferred existe
            if suffix is None and base in preferred_exists:
                log.debug(
                    "Handle générique écarté au profit de la version US",
                    handle=h,
                    preferred=f"{base}-{self._preferred_suffix}",
                )
                continue

            # 3. Dédupliquer : ne garder qu'un handle par base
            #    (au cas où plusieurs versions preferred coexistent)
            if base in seen_bases:
                log.debug("Handle dupliqué ignoré", handle=h)
                continue

            seen_bases.add(base)
            result.append(h)

        return result

    def _split_market_suffix(self, handle: str) -> tuple[str, str | None]:
        """
        Sépare un handle en (base, suffixe_de_marché).

        Exemples :
            "cami-bodysuit-us"  → ("cami-bodysuit", "us")
            "cami-bodysuit-ca"  → ("cami-bodysuit", "ca")
            "cami-bodysuit"     → ("cami-bodysuit", None)
            "crossover-bra"     → ("crossover-bra", None)
            "softform-cotton-high-rise-brief-us" → ("softform-cotton-high-rise-brief", "us")

        Un suffixe est reconnu uniquement s'il figure dans excluded OU s'il
        est égal à preferred. Cela évite de couper à tort des handles comme
        "x-balance-bra" (le "bra" final n'est pas un code marché).
        """
        known_suffixes = self._excluded_suffixes | {self._preferred_suffix}
        parts = handle.rsplit("-", 1)
        if len(parts) == 2 and parts[1].lower() in known_suffixes:
            return parts[0], parts[1].lower()
        return handle, None

    @staticmethod
    def _handle_from_url(url: str) -> str:
        """Extrait le handle depuis une URL .json."""
        # https://honeylove.com/products/cami-bodysuit-us.json → cami-bodysuit-us
        return url.rstrip("/").split("/")[-1].removesuffix(".json")