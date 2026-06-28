"""
CsvExporter v3 — 1 ligne par variante (couleur × taille).

Changements v3 :
  - Suppression de la colonne on_sale
  - Renommage des colonnes variant_* pour clarté
  - Colonnes matériaux corrigées
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

COLUMNS = [
    # ── Identité produit ──────────────────────────────────────────────
    "brand",
    "product_name",
    "external_id",
    "url",

    # ── Variante ──────────────────────────────────────────────────────
    "color",
    "color_canonical",
    "size",
    "sku",

    # ── Prix ──────────────────────────────────────────────────────────
    "price",
    "original_price",   # prix barré si en promotion
    "currency",
    "discount_pct",     # % de réduction (vide si pas de promo)

    # ── Disponibilité variante ────────────────────────────────────────
    # Disponibilité de CETTE taille/couleur spécifique
    "size_color_available",
    # Dates de première et dernière observation de cette variante
    "size_color_first_seen",
    "size_color_last_seen",
    # Date à laquelle cette taille/couleur a disparu du site
    "size_color_removed_date",
    # Date à laquelle cette taille/couleur est revenue en stock
    "size_color_back_in_stock_date",

    # ── Cycle de vie produit ──────────────────────────────────────────
    "product_is_active",
    "product_first_seen",
    "product_last_seen",
    # Date approximative de disparition du produit entier du site
    "product_removed_date",
    # Date de retour du produit après disparition
    "product_back_in_stock_date",

    # ── Best Seller ───────────────────────────────────────────────────
    "is_best_seller",
    "best_seller_since",     # date d'obtention du badge
    "best_seller_last_seen", # dernière fois observé avec le badge

    # ── Avis ─────────────────────────────────────────────────────────
    "rating",
    "review_count",

    # ── Matériaux ─────────────────────────────────────────────────────
    "material_main",         # ex: "73% Nylon, 27% Elastane"
    "material_lining",       # ex: "100% Cotton"
    "material_nylon_pct",
    "material_elastane_pct",
    "material_polyester_pct",
    "material_cotton_pct",
    "material_viscose_pct",
    "material_modal_pct",
    "material_bamboo_pct",
    "material_recycled_pct",
    "material_raw",          # texte brut complet

    # ── Classification ────────────────────────────────────────────────
    "category_raw",
    "family",
    "subfamily",
    "compression_level",
    "target_zones",
]

_DATE_FMT = "%Y-%m-%d %H:%M"


def _d(dt) -> str:
    return dt.strftime(_DATE_FMT) if dt else ""


def _b(v) -> str:
    if v is None: return ""
    return "Yes" if v else "No"


def _comp(json_str: str | None) -> dict:
    if not json_str: return {}
    try: return json.loads(json_str)
    except (json.JSONDecodeError, TypeError): return {}


class CsvExporter:

    def __init__(self, export_dir: Path | None = None) -> None:
        self._export_dir = export_dir or settings.EXPORT_DIR
        self._export_dir.mkdir(parents=True, exist_ok=True)

    def export_from_db(
        self,
        brand_slugs: list[str] | None = None,
        session_id: int | None = None,
        filename: str | None = None,
    ) -> Path:
        from app.storage.database import get_db
        from app.storage.repository import BrandRepository, SnapshotRepository, VariantRepository
        from app.storage.models import Product

        rows: list[dict] = []

        with get_db() as db:
            brands = BrandRepository(db).list_active()
            if brand_slugs:
                brands = [b for b in brands if b.slug in brand_slugs]

            for brand in brands:
                products = db.query(Product).filter(Product.brand_id == brand.id).all()
                snap_repo    = SnapshotRepository(db)
                variant_repo = VariantRepository(db)

                for product in products:
                    snapshot = snap_repo.get_latest(product.id)
                    variants = variant_repo.get_by_product(product.id)
                    comp     = _comp(product.material_composition_json)

                    # Prix produit (depuis dernier snapshot si variante sans prix)
                    prod_price    = snapshot.price if snapshot else None
                    prod_orig     = snapshot.original_price if snapshot else None
                    prod_disc     = snapshot.discount_pct if snapshot else None
                    currency      = snapshot.currency if snapshot else "USD"

                    base = {
                        "brand":                  brand.slug,
                        "product_name":           product.name,
                        "external_id":            product.external_id,
                        "url":                    product.url,
                        "currency":               currency,
                        "product_is_active":      _b(product.is_active),
                        "product_first_seen":     _d(product.first_seen),
                        "product_last_seen":      _d(product.last_seen),
                        "product_removed_date":   _d(product.removed_at),
                        "product_back_in_stock_date": _d(product.back_in_stock_at),
                        "is_best_seller":         _b(product.is_best_seller),
                        "best_seller_since":      _d(product.best_seller_first_seen),
                        "best_seller_last_seen":  _d(product.best_seller_last_seen),
                        "rating":                 product.rating or "",
                        "review_count":           product.review_count or "",
                        "material_main":          product.material_main or "",
                        "material_lining":        product.material_lining or "",
                        "material_nylon_pct":     comp.get("nylon", ""),
                        "material_elastane_pct":  comp.get("elastane", ""),
                        "material_polyester_pct": comp.get("polyester", ""),
                        "material_cotton_pct":    comp.get("cotton", ""),
                        "material_viscose_pct":   comp.get("viscose", ""),
                        "material_modal_pct":     comp.get("modal", ""),
                        "material_bamboo_pct":    comp.get("bamboo", ""),
                        "material_recycled_pct":  comp.get("recycled", ""),
                        "material_raw":           product.material_raw or "",
                        "category_raw":           product.category_raw or "",
                        "family":                 product.family or "",
                        "subfamily":              product.subfamily or "",
                        "compression_level":      product.compression_level or "",
                        "target_zones":           product.target_zones or "",
                    }

                    if variants:
                        for v in variants:
                            # Prix : priorité à la variante, sinon snapshot produit
                            v_price  = v.price if v.price is not None else prod_price
                            v_orig   = v.original_price if v.original_price is not None else prod_orig
                            v_disc   = ""
                            if v.on_sale and v_price and v_orig:
                                v_disc = round((1 - v_price / v_orig) * 100, 1)
                            elif prod_disc:
                                v_disc = prod_disc

                            row = dict(base)
                            row.update({
                                "color":                      v.color or "",
                                "color_canonical":            v.color_canonical or v.color or "",
                                "size":                       v.size or "",
                                "sku":                        v.sku or "",
                                "price":                      v_price if v_price is not None else "",
                                "original_price":             v_orig if (v.on_sale and v_orig) else "",
                                "discount_pct":               v_disc,
                                "size_color_available":       _b(v.available),
                                "size_color_first_seen":      _d(v.first_seen),
                                "size_color_last_seen":       _d(v.last_seen),
                                "size_color_removed_date":    _d(v.removed_at),
                                "size_color_back_in_stock_date": _d(v.back_in_stock_at),
                            })
                            rows.append(row)
                    else:
                        row = dict(base)
                        row.update({
                            "color": "", "color_canonical": "", "size": "", "sku": "",
                            "price": prod_price or "", "original_price": prod_orig or "",
                            "discount_pct": prod_disc or "",
                            "size_color_available": "",
                            "size_color_first_seen": "", "size_color_last_seen": "",
                            "size_color_removed_date": "", "size_color_back_in_stock_date": "",
                        })
                        rows.append(row)

        return self._write_csv(rows, session_id=session_id, filename=filename)

    def export(self, products, session_id=None, filename=None) -> Path:
        return self._write_csv(products, session_id=session_id, filename=filename)

    def _write_csv(self, rows, session_id, filename) -> Path:
        if not filename:
            ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
            sfx    = f"_session{session_id}" if session_id else ""
            filename = f"export_{ts}{sfx}.csv"

        path = self._export_dir / filename
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_ALL)
            writer.writeheader()
            for row in rows:
                writer.writerow({col: row.get(col, "") for col in COLUMNS})

        log.info("Export CSV créé", path=str(path), rows=len(rows))
        return path