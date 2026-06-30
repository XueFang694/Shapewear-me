"""
CsvExporter v4 — support multi-marché.

Changements v4 :
  - Nouvelle colonne "market" (slug du marché actif)
  - Formatage des prix selon les conventions du marché (format_price)
  - Formatage des dates selon le marché (format_date)
  - Suppression de la colonne on_sale (inchangé vs v3)
  - Rétrocompatible : si market = "us", comportement identique à v3
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.logger import get_logger
from app.core.market import MarketConfig, get_market

log = get_logger(__name__)

COLUMNS = [
    # ── Marché ────────────────────────────────────────────────────────────
    "market",

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
    "price_formatted",      # prix formaté selon la locale du marché
    "original_price",
    "original_price_formatted",
    "currency",
    "discount_pct",

    # ── Disponibilité variante ────────────────────────────────────────
    "size_color_available",
    "size_color_first_seen",
    "size_color_last_seen",
    "size_color_removed_date",
    "size_color_back_in_stock_date",

    # ── Cycle de vie produit ──────────────────────────────────────────
    "product_is_active",
    "product_first_seen",
    "product_last_seen",
    "product_removed_date",
    "product_back_in_stock_date",

    # ── Best Seller ───────────────────────────────────────────────────
    "is_best_seller",
    "best_seller_since",
    "best_seller_last_seen",

    # ── Avis ─────────────────────────────────────────────────────────
    "rating",
    "review_count",

    # ── Matériaux ─────────────────────────────────────────────────────
    "material_main",
    "material_lining",
    "material_nylon_pct",
    "material_elastane_pct",
    "material_polyester_pct",
    "material_cotton_pct",
    "material_viscose_pct",
    "material_modal_pct",
    "material_bamboo_pct",
    "material_recycled_pct",
    "material_raw",

    # ── Classification ────────────────────────────────────────────────
    "category_raw",
    "family",
    "subfamily",
    "compression_level",
    "target_zones",
]

_DATE_FMT_ISO = "%Y-%m-%d %H:%M"


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
        market_slug: str | None = None,
    ) -> Path:
        """
        Exporte la base en CSV.

        Args:
            brand_slugs : filtrer par marques (None = toutes).
            session_id  : suffixe du nom de fichier.
            filename    : nom de fichier optionnel.
            market_slug : marché pour le formatage (None = settings.MARKET).
        """
        from app.storage.database import get_db
        from app.storage.repository import BrandRepository, SnapshotRepository, VariantRepository
        from app.storage.models import Product

        # Marché actif
        market = self._resolve_market(market_slug)
        market_slug_val = market.slug

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

                    prod_price = snapshot.price if snapshot else None
                    prod_orig  = snapshot.original_price if snapshot else None
                    prod_disc  = snapshot.discount_pct if snapshot else None
                    currency   = snapshot.currency if snapshot else market.currency

                    base = {
                        "market":                 market_slug_val,
                        "brand":                  brand.slug,
                        "product_name":           product.name,
                        "external_id":            product.external_id,
                        "url":                    product.url,
                        "currency":               currency,
                        "product_is_active":      _b(product.is_active),
                        "product_first_seen":     self._fmt_date(product.first_seen, market),
                        "product_last_seen":      self._fmt_date(product.last_seen, market),
                        "product_removed_date":   self._fmt_date(product.removed_at, market),
                        "product_back_in_stock_date": self._fmt_date(product.back_in_stock_at, market),
                        "is_best_seller":         _b(product.is_best_seller),
                        "best_seller_since":      self._fmt_date(product.best_seller_first_seen, market),
                        "best_seller_last_seen":  self._fmt_date(product.best_seller_last_seen, market),
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
                                "price_formatted":            market.format_price(v_price) if v_price is not None else "",
                                "original_price":             v_orig if (v.on_sale and v_orig) else "",
                                "original_price_formatted":   market.format_price(v_orig) if (v.on_sale and v_orig) else "",
                                "discount_pct":               v_disc,
                                "size_color_available":       _b(v.available),
                                "size_color_first_seen":      self._fmt_date(v.first_seen, market),
                                "size_color_last_seen":       self._fmt_date(v.last_seen, market),
                                "size_color_removed_date":    self._fmt_date(v.removed_at, market),
                                "size_color_back_in_stock_date": self._fmt_date(v.back_in_stock_at, market),
                            })
                            rows.append(row)
                    else:
                        row = dict(base)
                        row.update({
                            "color": "", "color_canonical": "", "size": "", "sku": "",
                            "price":                    prod_price or "",
                            "price_formatted":          market.format_price(prod_price) if prod_price else "",
                            "original_price":           prod_orig or "",
                            "original_price_formatted": market.format_price(prod_orig) if prod_orig else "",
                            "discount_pct":             prod_disc or "",
                            "size_color_available": "",
                            "size_color_first_seen": "", "size_color_last_seen": "",
                            "size_color_removed_date": "", "size_color_back_in_stock_date": "",
                        })
                        rows.append(row)

        return self._write_csv(rows, session_id=session_id, filename=filename, market=market)

    def export(self, products, session_id=None, filename=None, market_slug=None) -> Path:
        market = self._resolve_market(market_slug)
        return self._write_csv(products, session_id=session_id, filename=filename, market=market)

    def _write_csv(self, rows, session_id, filename, market: MarketConfig | None = None) -> Path:
        if not filename:
            ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
            sfx     = f"_session{session_id}" if session_id else ""
            mkt_sfx = f"_{market.slug}" if market and market.slug != "us" else ""
            filename = f"export_{ts}{sfx}{mkt_sfx}.csv"

        path = self._export_dir / filename
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_ALL
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({col: row.get(col, "") for col in COLUMNS})

        log.info("Export CSV créé", path=str(path), rows=len(rows), market=market.slug if market else "?")
        return path

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_market(market_slug: str | None) -> MarketConfig:
        try:
            return get_market(market_slug)
        except Exception:
            try:
                return get_market()
            except Exception:
                from app.core.market import _MARKETS
                return _MARKETS["us"]

    @staticmethod
    def _fmt_date(dt, market: MarketConfig) -> str:
        """Formate une date selon le marché (format court + heure)."""
        if dt is None:
            return ""
        # Format : date locale + heure ISO (toujours utile pour les données brutes)
        return dt.strftime(market.date_format + " %H:%M")