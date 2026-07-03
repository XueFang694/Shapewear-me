"""
Exporteur Excel — feuille « Produits » triée par Marque, Nom, puis Taille.

Ordre de tri :
  1. Marque       — alphabétique
  2. Nom          — alphabétique
  3. Taille       — ordre vêtement standard (XS < S < M < L < XL < XXL …
                    puis numériques 0-30+, puis alpha résiduels)
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

# Feuilles à ne jamais exporter
_EXCLUDED_SHEETS = {"Synthèse", "Nouveautés", "Promotions", "Suppressions"}

# ---------------------------------------------------------------------------
# Ordre de tri des tailles
# ---------------------------------------------------------------------------

# Tailles alphabétiques standard dans l'ordre croissant
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

# Tailles numériques (tours de taille pantalon, bra band, cup sizes, etc.)
_NUMERIC_RE = re.compile(r"^(\d+(?:\.\d+)?)([A-Z]*)$", re.IGNORECASE)


def _size_sort_key(size: Any) -> tuple[int, float, str]:
    """
    Clé de tri universelle pour les tailles vêtement.

    Retourne un tuple (bucket, valeur_numérique, valeur_alpha) :
      bucket 0 → tailles alpha connues (XS, S, M, L … 1X, 2X …)
      bucket 1 → tailles numériques (0, 2, 4, 28, 32C, 34B …)
      bucket 2 → tout le reste (tri alpha simple)
    """
    if pd.isna(size) or size == "":
        return (3, 0.0, "")

    s = str(size).strip().upper()

    # Taille alpha connue
    if s in _ALPHA_SIZE_RANK:
        return (0, float(_ALPHA_SIZE_RANK[s]), "")

    # Taille numérique pure ou mixte (ex: "28", "32C", "34B", "10.5")
    m = _NUMERIC_RE.match(s)
    if m:
        return (1, float(m.group(1)), m.group(2))

    # Fallback alpha
    return (2, 0.0, s)


# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------

def _sort_products_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Trie le DataFrame produits par : Marque → Nom → Taille.

    Colonnes attendues (noms français tels qu'utilisés dans l'export) :
        Marque, Nom, Taille
    Les colonnes manquantes sont ignorées silencieusement.
    """
    sort_cols = []
    if "Marque" in df.columns:
        sort_cols.append("Marque")
    if "Nom" in df.columns:
        sort_cols.append("Nom")

    if not sort_cols and "Taille" not in df.columns:
        return df

    # Tri principal : Marque + Nom (tri lexicographique pandas standard)
    if sort_cols:
        df = df.sort_values(sort_cols, kind="stable", ignore_index=True)

    # Tri secondaire : Taille avec clé personnalisée
    if "Taille" in df.columns:
        df = df.iloc[
            pd.Series(df["Taille"].values)
            .map(_size_sort_key)
            .argsort(stable=True)
            .values
        ].reset_index(drop=True)

        # Re-appliquer le tri Marque+Nom après le tri Taille
        # (argsort sur Taille seule peut casser l'ordre des groupes)
        if sort_cols:
            df = df.sort_values(
                sort_cols + ["Taille"],
                key=lambda col: col.map(_size_sort_key) if col.name == "Taille" else col,
                kind="stable",
                ignore_index=True,
            )

    return df


# ---------------------------------------------------------------------------
# Exporter principal
# ---------------------------------------------------------------------------

class ExcelExporter:
    """
    Génère le fichier Excel de veille concurrentielle.

    Feuilles produites :
        Produits   — catalogue complet, trié Marque / Nom / Taille
    """

    def export(
        self,
        rows: list[dict],
        output_path: str,
        *,
        sheet_name: str = "Produits",
        extra_sheets: dict[str, list[dict]] | None = None,
    ) -> None:
        df_main = pd.DataFrame(rows)
        df_main = _sort_products_df(df_main)

        # Filtrer les feuilles exclues
        filtered_extras = {
            name: sheet_rows
            for name, sheet_rows in (extra_sheets or {}).items()
            if name not in _EXCLUDED_SHEETS
        }

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df_main.to_excel(writer, sheet_name=sheet_name, index=False)

            for name, sheet_rows in filtered_extras.items():
                pd.DataFrame(sheet_rows).to_excel(
                    writer, sheet_name=name, index=False
                )

        _apply_formatting(output_path, sheet_name)

    # Alias pour compatibilité avec les appels existants à export_from_db()
    def export_from_db(self, *args, **kwargs) -> None:
        return self.export(*args, **kwargs)

def _apply_formatting(path: str, products_sheet: str) -> None:
    """Applique le formatage de base (largeurs de colonnes, en-têtes gras)."""
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        wb = load_workbook(path)

        for ws in wb.worksheets:
            # En-têtes en gras avec fond gris clair
            header_fill = PatternFill("solid", start_color="D9D9D9")
            for cell in ws[1]:
                cell.font = Font(bold=True, name="Arial", size=10)
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")

            # Ajustement automatique de la largeur des colonnes
            for col_idx, col_cells in enumerate(ws.columns, start=1):
                max_len = max(
                    (len(str(c.value)) for c in col_cells if c.value is not None),
                    default=8,
                )
                ws.column_dimensions[get_column_letter(col_idx)].width = min(
                    max_len + 2, 50
                )

            # Figer la première ligne
            ws.freeze_panes = "A2"

        wb.save(path)
    except Exception:
        # Le formatage est optionnel : on ne bloque pas l'export si openpyxl échoue
        pass