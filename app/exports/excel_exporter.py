"""
Exporteur Excel — feuille « Produits » triée par Marque, Nom, puis Taille.

Ordre de tri :
  1. Marque       — alphabétique
  2. Nom          — alphabétique
  3. Taille       — ordre vêtement standard (XS < S < M < L < XL < XXL …
                    puis numériques 0-30+, puis alpha résiduels)

Interface identique à CsvExporter et JsonExporter :
  exporter = ExcelExporter()
  path = exporter.export_from_db(brand_slugs=["spanx"], session_id=42)
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Ordre de tri des tailles
# ---------------------------------------------------------------------------

_ALPHA_SIZE_ORDER: list[str] = [
    "XXXS", "XXS", "XS", "XS/S",
    "S", "S/M",
    "M", "M/L",
    "L", "L/XL",
    "XL", "XL/XXL",
    "XXL", "2XL", "1X",
    "XXXL", "3XL", "2X",
    "4XL", "3X",
    "5XL", "4X",
    "6XL", "5X",
]

_ALPHA_SIZE_RANK: dict[str, int] = {
    s.upper(): i for i, s in enumerate(_ALPHA_SIZE_ORDER)
}

_NUMERIC_RE = re.compile(r"^(\d+(?:\.\d+)?)([A-Z]*)$", re.IGNORECASE)


def _size_sort_key(size: Any) -> tuple[int, float, str]:
    if pd.isna(size) or size == "":
        return (3, 0.0, "")
    s = str(size).strip().upper()
    if s in _ALPHA_SIZE_RANK:
        return (0, float(_ALPHA_SIZE_RANK[s]), "")
    m = _NUMERIC_RE.match(s)
    if m:
        return (1, float(m.group(1)), m.group(2))
    return (2, 0.0, s)


def _sort_products_df(df: pd.DataFrame) -> pd.DataFrame:
    sort_cols = [c for c in ("Marque", "Nom", "Couleur") if c in df.columns]
    if not sort_cols and "Taille" not in df.columns:
        return df
    if sort_cols:
        df = df.sort_values(sort_cols, kind="stable", ignore_index=True)
    if "Taille" in df.columns and sort_cols:
        df = df.sort_values(
            sort_cols + ["Taille"],
            key=lambda col: col.map(_size_sort_key) if col.name == "Taille" else col,
            kind="stable",
            ignore_index=True,
        )
    return df


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

def _b(v) -> str:
    if v is None:
        return ""
    return "Yes" if v else "No"


def _d(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def _comp(json_str: str | None) -> dict:
    if not json_str:
        return {}
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Exporter principal
# ---------------------------------------------------------------------------

class ExcelExporter:
    """
    Génère un fichier Excel depuis la base de données.

    Interface identique à CsvExporter :
        exporter = ExcelExporter()
        path = exporter.export_from_db(brand_slugs=["spanx"])
    """

    def __init__(self, export_dir: Path | None = None) -> None:
        from app.core.config import settings
        self._export_dir = export_dir or settings.EXPORT_DIR
        self._export_dir.mkdir(parents=True, exist_ok=True)

    # ── Interface publique ────────────────────────────────────────────────

    def export_from_db(
        self,
        brand_slugs: list[str] | None = None,
        session_id: int | None = None,
        filename: str | None = None,
    ) -> Path:
        """
        Lit la base de données et génère un fichier Excel.

        Args:
            brand_slugs : filtrer par marques (None = toutes).
            session_id  : utilisé uniquement pour le suffixe du nom de fichier.
            filename    : nom de fichier optionnel (sinon généré automatiquement).

        Returns:
            Chemin du fichier .xlsx créé.
        """
        rows = self._load_rows(brand_slugs)
        return self._write_excel(rows, session_id=session_id, filename=filename)

    # ── Chargement depuis la DB ───────────────────────────────────────────

    def _load_rows(self, brand_slugs: list[str] | None) -> list[dict]:
        from app.storage.database import get_db
        from app.storage.models import Product
        from app.storage.repository import BrandRepository, SnapshotRepository, VariantRepository

        rows: list[dict] = []

        with get_db() as db:
            brands = BrandRepository(db).list_active()
            if brand_slugs:
                brands = [b for b in brands if b.slug in brand_slugs]

            snap_repo    = SnapshotRepository(db)
            variant_repo = VariantRepository(db)

            for brand in brands:
                products = db.query(Product).filter(Product.brand_id == brand.id).all()

                for product in products:
                    snapshot = snap_repo.get_latest(product.id)
                    variants = variant_repo.get_by_product(product.id)
                    comp     = _comp(product.material_composition_json)

                    prod_price = snapshot.price if snapshot else None
                    prod_orig  = snapshot.original_price if snapshot else None
                    prod_disc  = snapshot.discount_pct if snapshot else None
                    on_sale    = snapshot.on_sale if snapshot else False
                    avail      = snapshot.availability if snapshot else "unknown"
                    currency   = snapshot.currency if snapshot else "USD"

                    # Zones en texte lisible
                    try:
                        zones = json.loads(product.target_zones) if product.target_zones else []
                        zones_str = ", ".join(zones)
                    except Exception:
                        zones_str = product.target_zones or ""

                    base = {
                        "Marque":                brand.slug,
                        "Nom":                   product.name,
                        "ID Externe":            product.external_id,
                        "URL":                   product.url,
                        "Catégorie brute":       product.category_raw or "",
                        "Famille":               product.family or "",
                        "Sous-famille":          product.subfamily or "",
                        "Compression":           product.compression_level or "",
                        "Zones ciblées":         zones_str,
                        "Actif":                 _b(product.is_active),
                        "Best Seller":           _b(product.is_best_seller),
                        "BS depuis":             _d(product.best_seller_first_seen),
                        "1ère vue":              _d(product.first_seen),
                        "Dernière vue":          _d(product.last_seen),
                        "Supprimé le":           _d(product.removed_at),
                        "Retour stock":          _d(product.back_in_stock_at),
                        "Note":                  product.rating or "",
                        "Nb avis":               product.review_count or "",
                        "Matière principale":    product.material_main or "",
                        "Doublure":              product.material_lining or "",
                        "% Nylon":               comp.get("nylon", ""),
                        "% Elastane":            comp.get("elastane", ""),
                        "% Polyester":           comp.get("polyester", ""),
                        "% Cotton":              comp.get("cotton", ""),
                        "Matière brute":         product.material_raw or "",
                        "Devise":                currency,
                        "Prix":                  prod_price or "",
                        "Prix original":         prod_orig or "",
                        "Remise %":              prod_disc or "",
                        "En promo":              _b(on_sale),
                        "Disponibilité":         avail,
                    }

                    if variants:
                        for v in variants:
                            v_price = v.price if v.price is not None else prod_price
                            v_orig  = v.original_price if v.original_price is not None else prod_orig
                            v_disc  = ""
                            if v.on_sale and v_price and v_orig:
                                v_disc = round((1 - v_price / v_orig) * 100, 1)
                            elif prod_disc:
                                v_disc = prod_disc

                            row = dict(base)
                            row.update({
                                "Couleur":           v.color or "",
                                "Couleur canonique": v.color_canonical or v.color or "",
                                "Taille":            v.size or "",
                                "SKU":               v.sku or "",
                                "Prix":              v_price if v_price is not None else "",
                                "Prix original":     v_orig if (v.on_sale and v_orig) else "",
                                "Remise %":          v_disc,
                                "En promo":          _b(v.on_sale),
                                "Dispo variante":    _b(v.available),
                                "Variante 1ère vue": _d(v.first_seen),
                                "Variante der. vue": _d(v.last_seen),
                                "Variante supp.":    _d(v.removed_at),
                                "Variante retour":   _d(v.back_in_stock_at),
                            })
                            rows.append(row)
                    else:
                        row = dict(base)
                        row.update({
                            "Couleur": "", "Couleur canonique": "", "Taille": "", "SKU": "",
                            "Dispo variante": "",
                            "Variante 1ère vue": "", "Variante der. vue": "",
                            "Variante supp.": "", "Variante retour": "",
                        })
                        rows.append(row)

        return rows

    # ── Écriture du fichier ───────────────────────────────────────────────

    def _write_excel(
        self,
        rows: list[dict],
        session_id: int | None,
        filename: str | None,
    ) -> Path:
        from app.core.logger import get_logger
        log = get_logger(__name__)

        if not filename:
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            sfx = f"_session{session_id}" if session_id else ""
            filename = f"export_{ts}{sfx}.xlsx"

        path = self._export_dir / filename

        df = pd.DataFrame(rows)
        df = _sort_products_df(df)

        with pd.ExcelWriter(str(path), engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Produits", index=False)

        _apply_formatting(str(path), "Produits")

        log.info("Export Excel créé", path=str(path), rows=len(rows))
        return path


# ---------------------------------------------------------------------------
# Formatage openpyxl
# ---------------------------------------------------------------------------

def _apply_formatting(path: str, products_sheet: str) -> None:
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        wb = load_workbook(path)
        for ws in wb.worksheets:
            header_fill = PatternFill("solid", start_color="D9D9D9")
            for cell in ws[1]:
                cell.font = Font(bold=True, name="Arial", size=10)
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")
            for col_idx, col_cells in enumerate(ws.columns, start=1):
                max_len = max(
                    (len(str(c.value)) for c in col_cells if c.value is not None),
                    default=8,
                )
                ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 50)
            ws.freeze_panes = "A2"
        wb.save(path)
    except Exception:
        pass