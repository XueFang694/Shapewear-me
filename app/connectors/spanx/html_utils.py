"""
Fonctions d'extraction HTML spécifiques aux pages produit Spanx.

Spanx utilise React Server Components (RSC) : les données produit sont
sérialisées dans des blocs <script> au format RSC. Le niveau d'échappement
des guillemets varie selon le contexte :
  - Requête HTTP directe (scraper)  : \\"valeur\\"       (1 backslash)
  - Fichier HTML sauvegardé         : \\\\\"valeur\\\\\" (4 backslashes)

Les regex n'utilisent pas de délimiteur basé sur les backslashes pour la
compression (le motif SPANX[a-z]+ suffit à ancrer la recherche) et utilisent
\\{1,4} uniquement là où le délimiteur est nécessaire.
"""
from __future__ import annotations

import json
import re


_LINING_KEYWORDS = frozenset({"lining", "gusset", "doublure"})

_FIBER_PATTERNS = [
    (r"(\d+(?:\.\d+)?)\s*%\s*nylon",       "nylon"),
    (r"(\d+(?:\.\d+)?)\s*%\s*polyamide",   "nylon"),
    (r"(\d+(?:\.\d+)?)\s*%\s*elastane",    "elastane"),
    (r"(\d+(?:\.\d+)?)\s*%\s*spandex",     "elastane"),
    (r"(\d+(?:\.\d+)?)\s*%\s*lycra",       "elastane"),
    (r"(\d+(?:\.\d+)?)\s*%\s*polyester",   "polyester"),
    (r"(\d+(?:\.\d+)?)\s*%\s*cotton",      "cotton"),
    (r"(\d+(?:\.\d+)?)\s*%\s*viscose",     "viscose"),
    (r"(\d+(?:\.\d+)?)\s*%\s*rayon",       "viscose"),
    (r"(\d+(?:\.\d+)?)\s*%\s*modal",       "modal"),
    (r"(\d+(?:\.\d+)?)\s*%\s*bamboo",      "bamboo"),
    (r"(\d+(?:\.\d+)?)\s*%\s*recycled",    "recycled"),
    (r"(\d+(?:\.\d+)?)\s*%\s*silk",        "silk"),
]

# Délimiteur RSC : 1 à 4 backslashes suivis d'un guillemet.
_Q = r'\\{1,4}"'

# Mapping niveau de compression textuel → valeur normalisée
_COMPRESSION_LEVEL_MAP = {
    "no":         "none",
    "light":      "light",
    "medium":     "medium",
    "moderate":   "medium",
    "strong":     "strong",
    "firm":       "strong",
    "extra firm": "extra_strong",
    "extra-firm": "extra_strong",
}


def extract_materials_from_spanx_html(html: str) -> dict:
    """
    Extrait la composition textile depuis le HTML d'une page produit Spanx.

    Les données sont dans le payload RSC embarqué dans des <script>.
    Fonctionne avec les deux niveaux d'échappement (scraper direct et fichier
    navigateur).

    Retourne un dict avec les clés :
        material_raw               : texte brut complet
        material_main              : composition principale
        material_lining            : doublure / gusset (si présent)
        material_composition_json  : {"nylon": 90.0, "elastane": 10.0}

    Retourne {} si aucune composition n'est trouvée.
    """
    if not html:
        return {}

    raw_text: str | None = None

    # Priorité 1 : avec préfixe Body / Gusset / Shell / Lining / Fabric
    complex_pat = (
        _Q
        + r'([^"\\]*?(?:Body|Gusset|Shell|Lining|Fabric)'
        + r'[^"\\]*?\d+%[^"\\]{5,250}?)'
        + _Q
    )
    m = re.search(complex_pat, html, re.IGNORECASE)
    if m:
        raw_text = m.group(1)

    # Priorité 2 : composition simple "90% Nylon, 10% Elastane."
    if not raw_text:
        simple_pat = (
            _Q
            + r'(\d+(?:\.\d+)?%\s*'
            + r'(?:Nylon|Elastane|Polyester|Cotton|Spandex|Lycra|Viscose|Rayon|Modal|Bamboo|Recycled|Silk)'
            + r'[^"\\]{0,200}?)'
            + _Q
        )
        m = re.search(simple_pat, html, re.IGNORECASE)
        if m:
            raw_text = m.group(1)

    if not raw_text:
        return {}

    raw_text = raw_text.strip().rstrip(".")
    result: dict = {"material_raw": raw_text[:500]}

    # Séparation main / lining
    if any(k in raw_text.lower() for k in _LINING_KEYWORDS):
        parts = re.split(r"\.\s+", raw_text)
        main_parts: list[str] = []
        lining_parts: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if any(k in part.lower() for k in _LINING_KEYWORDS):
                lining_parts.append(part)
            elif re.search(r"\d+%", part):
                main_parts.append(part)
        if main_parts:
            result["material_main"] = ". ".join(main_parts)
        if lining_parts:
            result["material_lining"] = ". ".join(lining_parts)
    else:
        result["material_main"] = raw_text

    # Pourcentages par fibre
    comp: dict[str, float] = {}
    for pat, fiber in _FIBER_PATTERNS:
        matches = re.findall(pat, raw_text, re.IGNORECASE)
        if matches and fiber not in comp:
            try:
                comp[fiber] = float(matches[0])
            except ValueError:
                pass
    if comp:
        result["material_composition_json"] = json.dumps(comp)

    return result


def extract_is_best_seller_from_spanx_html(html: str) -> bool:
    """
    Détecte le badge « Best Seller » depuis le HTML d'une page produit Spanx.

    Fonctionne avec les deux niveaux d'échappement RSC.
    """
    if not html:
        return False

    if re.search(_Q + r"Best Seller" + _Q, html):
        return True
    if re.search(_Q + r"Shapewear Best Sellers" + _Q, html):
        return True

    return False


def extract_compression_from_spanx_html(html: str) -> str | None:
    """
    Extrait le niveau de compression depuis le HTML d'une page produit Spanx.

    Spanx embarque dans le payload RSC une phrase descriptive propre à chaque
    ligne produit, par exemple :
      - "SPANXsculpt® provides strong compression for a sculpting effect"
      - "SPANXshape® provides medium compression for a shaping effect"
      - "SPANXsmooth® provides light compression for barely there smoothing"

    Cette phrase est product-specific (elle décrit la LIGNE du produit affiché),
    contrairement aux trois descriptions du widget de navigation "Shop by
    Compression" qui sont présentes sur toutes les pages.

    Retourne une valeur normalisée parmi :
        "none" | "light" | "medium" | "strong" | "extra_strong"
    ou None si non détecté.
    """
    if not html:
        return None

    # Pattern : "SPANXxxx® provides <level> compression ..."
    # Le motif r[^"]{0,25} tolère ® et des espaces autour, sans dépendre du
    # niveau d'échappement des guillemets.
    m = re.search(
        r'SPANX\w+[^"]{0,25}provides\s+'
        r'(light|medium|moderate|strong|firm|extra[- ]?firm|no)\s+compression',
        html,
        re.IGNORECASE,
    )
    if not m:
        return None

    raw_level = m.group(1).lower().strip()
    return _COMPRESSION_LEVEL_MAP.get(raw_level)