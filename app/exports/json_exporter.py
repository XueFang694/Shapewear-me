"""
JsonExporter — Export JSON structuré, formats flat et nested.

Flat   : liste de dicts produit, une entrée par variante.
Nested : liste de produits avec snapshots et variants imbriqués.

Usage :
    exporter = JsonExporter()
    path = exporter.export_from_db(brand_slugs=["spanx"], fmt="nested")
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

ExportFormat = Literal["flat", "nested"]

_DATE_FMT = "%Y-%m-%d %H:%M"


def _d(dt) -> str | None:
    return dt.strftime(_DATE_FMT) if dt else None


def _b(v) -> bool | None:
    return bool(v) if v is not None else None


def _comp(json_str: str | None) -> dict:
    if not json_str:
        return {}
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return {}


class JsonExporter:
    """Génère des exports JSON depuis la base de données."""

    def __init__(self, export_dir: Path | None = None) -> None:
        self._export_dir = export_dir or settings.EXPORT_DIR
        self._export_dir.mkdir(parents=True, exist_ok=True)

    # ── Interface publique ────────────────────────────────────────────────

    def export_from_db(
        self,
        brand_slugs: list[str] | None = None,
        session_id: int | None = None,
        fmt: ExportFormat = "flat",
        filename: str | None = None,
        pretty: bool = True,
    ) -> Path:
        """
        Exporte la base en JSON.

        Args:
            brand_slugs : filtrer par marques (None = toutes).
            session_id  : suffixe du nom de fichier.
            fmt         : "flat" (une ligne par variante) ou "nested" (produits imbriqués).
            filename    : nom de fichier optionnel.
            pretty      : indenter le JSON (True) ou compact (False).

        Returns:
            Chemin du fichier créé.
        """
        from app.storage.database import get_db
        from app.storage.models import Product
        from app.storage.repository import (
            BrandRepository, SnapshotRepository, VariantRepository,
        )

        payload: list[dict] = []

        with get_db() as db:
            brands = BrandRepository(db).list_active()
            if brand_slugs:
                brands = [b for b in brands if b.slug in brand_slugs]

            snap_repo    = SnapshotRepository(db)
            variant_repo = VariantRepository(db)

            for brand in brands:
                products = db.query(Product).filter(
                    Product.brand_id == brand.id
                ).all()

                for product in products:
                    snapshot = snap_repo.get_latest(product.id)
                    variants = variant_repo.get_by_product(product.id)
                    comp     = _comp(product.material_composition_json)

                    if fmt == "nested":
                        payload.append(
                            self._build_nested(brand, product, snapshot, variants, comp)
                        )
                    else:
                        payload.extend(
                            self._build_flat(brand, product, snapshot, variants, comp)
                        )

        if not filename:
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            sfx = f"_session{session_id}" if session_id else ""
            filename = f"export_{fmt}_{ts}{sfx}.json"

        path = self._export_dir / filename
        indent = 2 if pretty else None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "generated_at": datetime.now().strftime(_DATE_FMT),
                    "format":       fmt,
                    "brand_slugs":  brand_slugs,
                    "count":        len(payload),
                    "data":         payload,
                },
                f,
                ensure_ascii=False,
                indent=indent,
                default=str,
            )

        log.info("Export JSON créé", path=str(path), fmt=fmt, records=len(payload))
        return path

    # ── Builders ─────────────────────────────────────────────────────────

    def _build_nested(self, brand, product, snapshot, variants, comp) -> dict:
        """Une entrée par produit, variantes et snapshots imbriqués."""
        return {
            # Identité
            "brand":       brand.slug,
            "external_id": product.external_id,
            "name":        product.name,
            "url":         product.url,
            # Classification
            "category_raw":      product.category_raw,
            "family":            product.family,
            "subfamily":         product.subfamily,
            "compression_level": product.compression_level,
            "target_zones":      json.loads(product.target_zones) if product.target_zones else [],
            # Statut
            "is_active":              product.is_active,
            "is_best_seller":         product.is_best_seller,
            "best_seller_since":      _d(product.best_seller_first_seen),
            "best_seller_last_seen":  _d(product.best_seller_last_seen),
            # Cycle de vie
            "first_seen":         _d(product.first_seen),
            "last_seen":          _d(product.last_seen),
            "removed_at":         _d(product.removed_at),
            "back_in_stock_at":   _d(product.back_in_stock_at),
            # Prix (dernier snapshot)
            "price":          snapshot.price if snapshot else None,
            "original_price": snapshot.original_price if snapshot else None,
            "on_sale":        snapshot.on_sale if snapshot else False,
            "discount_pct":   snapshot.discount_pct if snapshot else None,
            "currency":       snapshot.currency if snapshot else "USD",
            "availability":   snapshot.availability if snapshot else "unknown",
            # Avis
            "rating":       product.rating,
            "review_count": product.review_count,
            # Matériaux
            "material_main":         product.material_main,
            "material_lining":       product.material_lining,
            "material_composition":  comp,
            "material_raw":          product.material_raw,
            # Variantes imbriquées
            "variants": [
                {
                    "color":            v.color,
                    "color_canonical":  v.color_canonical or v.color,
                    "size":             v.size,
                    "sku":              v.sku,
                    "price":            v.price,
                    "original_price":   v.original_price,
                    "on_sale":          v.on_sale,
                    "available":        v.available,
                    "first_seen":       _d(v.first_seen),
                    "last_seen":        _d(v.last_seen),
                    "removed_at":       _d(v.removed_at),
                    "back_in_stock_at": _d(v.back_in_stock_at),
                }
                for v in variants
            ],
        }

    def _build_flat(self, brand, product, snapshot, variants, comp) -> list[dict]:
        """Une entrée par variante (ou une entrée produit si pas de variantes)."""
        base = {
            "brand":                  brand.slug,
            "external_id":            product.external_id,
            "name":                   product.name,
            "url":                    product.url,
            "category_raw":           product.category_raw,
            "family":                 product.family,
            "subfamily":              product.subfamily,
            "compression_level":      product.compression_level,
            "target_zones":           json.loads(product.target_zones) if product.target_zones else [],
            "is_active":              product.is_active,
            "is_best_seller":         product.is_best_seller,
            "best_seller_since":      _d(product.best_seller_first_seen),
            "product_first_seen":     _d(product.first_seen),
            "product_last_seen":      _d(product.last_seen),
            "product_removed_at":     _d(product.removed_at),
            "product_back_in_stock":  _d(product.back_in_stock_at),
            "rating":                 product.rating,
            "review_count":           product.review_count,
            "material_main":          product.material_main,
            "material_lining":        product.material_lining,
            "material_nylon_pct":     comp.get("nylon"),
            "material_elastane_pct":  comp.get("elastane"),
            "material_polyester_pct": comp.get("polyester"),
            "material_cotton_pct":    comp.get("cotton"),
            "material_raw":           product.material_raw,
            "currency":               snapshot.currency if snapshot else "USD",
            "price":                  snapshot.price if snapshot else None,
            "original_price":         snapshot.original_price if snapshot else None,
            "on_sale":                snapshot.on_sale if snapshot else False,
            "discount_pct":           snapshot.discount_pct if snapshot else None,
            "availability":           snapshot.availability if snapshot else "unknown",
        }

        if not variants:
            entry = dict(base)
            entry.update({
                "color": None, "color_canonical": None, "size": None, "sku": None,
                "variant_available": None,
                "variant_first_seen": None, "variant_last_seen": None,
                "variant_removed_at": None, "variant_back_in_stock": None,
            })
            return [entry]

        rows = []
        for v in variants:
            entry = dict(base)
            # Priorité au prix de la variante
            v_price = v.price if v.price is not None else base["price"]
            v_orig  = v.original_price if v.original_price is not None else base["original_price"]
            v_disc  = None
            if v.on_sale and v_price and v_orig:
                v_disc = round((1 - v_price / v_orig) * 100, 1)
            entry.update({
                "price":          v_price,
                "original_price": v_orig if v.on_sale else None,
                "on_sale":        v.on_sale,
                "discount_pct":   v_disc or base["discount_pct"],
                "color":              v.color,
                "color_canonical":    v.color_canonical or v.color,
                "size":               v.size,
                "sku":                v.sku,
                "variant_available":  v.available,
                "variant_first_seen": _d(v.first_seen),
                "variant_last_seen":  _d(v.last_seen),
                "variant_removed_at": _d(v.removed_at),
                "variant_back_in_stock": _d(v.back_in_stock_at),
            })
            rows.append(entry)
        return rows

    # ── Export mémoire (sans base de données) ────────────────────────────

    def export_products(
        self,
        products: list[dict],
        session_id: int | None = None,
        filename: str | None = None,
    ) -> Path:
        """Exporte une liste de dicts produit directement."""
        if not filename:
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            sfx = f"_session{session_id}" if session_id else ""
            filename = f"export_flat_{ts}{sfx}.json"

        path = self._export_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "generated_at": datetime.now().strftime(_DATE_FMT),
                    "count": len(products),
                    "data":  products,
                },
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        log.info("Export JSON (mémoire) créé", path=str(path), records=len(products))
        return path