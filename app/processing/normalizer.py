"""
Normalizer v3 — intègre best_seller, matériaux, avis clients et support multi-marché.

Nouveautés v3 :
  - La devise du RawProduct est préservée en priorité (le connecteur la renseigne
    depuis le MarketConfig de son marché actif).
  - Fallback sur settings.market_config.currency si la devise est absente.
  - Aucun autre comportement ne change : la normalisation reste indépendante du marché.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from app.connectors.base import RawProduct
from app.core.config import settings
from app.core.exceptions import NormalizationError
from app.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class NormalizedProduct:
    # Identité
    external_id: str
    url: str
    name: str
    brand_slug: str

    # Prix
    price: float | None = None
    original_price: float | None = None
    currency: str = "USD"
    on_sale: bool = False
    discount_pct: float | None = None

    # Classification
    category_raw: str | None = None
    family: str | None = None
    subfamily: str | None = None
    compression_level: str | None = None
    target_zones: list[str] = field(default_factory=list)

    # Contenu
    description: str | None = None
    images: list[str] = field(default_factory=list)

    # Variantes
    sizes: list[str] = field(default_factory=list)
    colors: list[dict] = field(default_factory=list)
    variants: list[dict] = field(default_factory=list)   # variantes détaillées

    # Disponibilité
    availability: str = "unknown"

    # Avis
    rating: float | None = None
    review_count: int | None = None

    # Best Seller
    is_best_seller: bool = False

    # Matériaux
    material_main: str | None = None
    material_lining: str | None = None
    material_composition_json: str | None = None
    material_raw: str | None = None

    # Méta
    crawled_at: datetime = field(default_factory=datetime.utcnow)
    classification_manual_review: bool = False

    def to_product_dict(self) -> dict:
        import json
        return {
            "external_id":   self.external_id,
            "url":           self.url,
            "name":          self.name,
            "category_raw":  self.category_raw,
            "family":        self.family,
            "subfamily":     self.subfamily,
            "compression_level": self.compression_level,
            "target_zones":  json.dumps(self.target_zones) if self.target_zones else None,
            "is_active":     True,
            "is_best_seller": self.is_best_seller,
            "rating":        self.rating,
            "review_count":  self.review_count,
            "material_main": self.material_main,
            "material_lining": self.material_lining,
            "material_composition_json": self.material_composition_json,
            "material_raw":  self.material_raw,
            "classification_manual_review": self.classification_manual_review,
        }

    def to_snapshot_dict(self) -> dict:
        return {
            "price":          self.price,
            "original_price": self.original_price,
            "on_sale":        self.on_sale,
            "discount_pct":   self.discount_pct,
            "currency":       self.currency,
            "availability":   self.availability,
            "is_best_seller": self.is_best_seller,
            "crawled_at":     self.crawled_at,
        }


class Normalizer:
    """
    Transforme un RawProduct en NormalizedProduct.

    Multi-marché : la devise est déterminée dans l'ordre de priorité suivant :
      1. raw.currency si non vide (renseigné par le connecteur d'après MarketConfig)
      2. settings.market_config.currency (marché actif global)
      3. "USD" (fallback historique)
    """

    def __init__(self) -> None:
        self._color_map = self._load_yaml_map("color_normalization.yml")
        self._size_map  = self._load_yaml_map("size_normalization.yml")
        # Devise de fallback depuis le marché actif
        try:
            self._default_currency = settings.market_config.currency
        except Exception:
            self._default_currency = "USD"

    def process(self, raw: RawProduct) -> NormalizedProduct:
        self._validate(raw)

        price          = self._clean_price(raw.price)
        original_price = self._clean_price(raw.original_price)
        on_sale, discount_pct = self._sale_info(price, original_price)

        # Devise : priorité au connecteur, puis marché actif, puis fallback
        currency = (raw.currency or "").strip() or self._default_currency or "USD"

        # Extraire best_seller et matériaux depuis extra{}
        extra = raw.extra or {}
        is_best_seller = bool(extra.get("is_best_seller", False))
        materials: dict = extra.get("materials", {})

        normalized = NormalizedProduct(
            external_id   = str(raw.external_id).strip(),
            url           = raw.url.strip(),
            name          = self._clean(raw.name),
            brand_slug    = raw.brand_slug.strip().lower(),
            price         = price,
            original_price= original_price,
            currency      = currency,
            on_sale       = on_sale,
            discount_pct  = discount_pct,
            category_raw  = self._clean(raw.category_raw),
            description   = self._clean_html(raw.description),
            images        = [i.strip() for i in raw.images if i and i.strip()],
            sizes         = self._norm_sizes(raw.sizes),
            colors        = self._norm_colors(raw.colors),
            variants      = raw.variants,
            availability  = raw.availability or "unknown",
            rating        = raw.rating,
            review_count  = raw.review_count,
            is_best_seller         = is_best_seller,
            material_main          = materials.get("material_main"),
            material_lining        = materials.get("material_lining"),
            material_composition_json = materials.get("material_composition_json"),
            material_raw           = materials.get("material_raw"),
            crawled_at    = raw.crawled_at,
        )
        return normalized

    # ── helpers ──────────────────────────────────────────────────────────

    def _validate(self, raw: RawProduct) -> None:
        errors = [f for f in ("external_id", "url", "name", "brand_slug") if not getattr(raw, f)]
        if errors:
            raise NormalizationError(f"Champs obligatoires manquants : {errors}", context={"url": raw.url})

    def _clean_price(self, p) -> float | None:
        if p is None: return None
        if isinstance(p, (int, float)): return round(float(p), 2)
        cleaned = re.sub(r"[^\d.]", "", str(p))
        try: return round(float(cleaned), 2) if cleaned else None
        except ValueError: return None

    def _sale_info(self, price, original_price):
        if price and original_price and original_price > price:
            return True, round((1 - price / original_price) * 100, 1)
        return False, None

    def _clean(self, text) -> str | None:
        return text.strip() or None if text else None

    def _clean_html(self, html) -> str | None:
        if not html: return None
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text or None

    def _norm_sizes(self, sizes: list[str]) -> list[str]:
        result, seen = [], set()
        for s in sizes:
            n = self._size_map.get(s.strip().upper(), s.strip())
            if n and n not in seen:
                result.append(n)
                seen.add(n)
        return result

    def _norm_colors(self, colors: list[dict]) -> list[dict]:
        result = []
        for c in colors:
            raw_name = c.get("name", "")
            canonical = self._find_canonical_color(raw_name)
            result.append({**c, "canonical_name": canonical or raw_name})
        return result

    def _find_canonical_color(self, raw_name: str) -> str | None:
        lower = raw_name.lower().strip()
        if lower in self._color_map: return self._color_map[lower]
        for k, v in self._color_map.items():
            if k in lower: return v
        return None

    def _load_yaml_map(self, filename: str) -> dict:
        path = settings.TAXONOMIES_DIR / filename
        if not path.exists(): return {}
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("mappings", {})