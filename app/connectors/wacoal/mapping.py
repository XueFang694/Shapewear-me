"""
Mappings Wacoal America.

Wacoal propose des sous-marques distinctes :
  - Wacoal       : lingerie et shapewear haut de gamme
  - b.tempt'd    : lingerie jeune et accessible
  - Wacoal Sport : soutiens-gorge sport

Les catégories Shopify de Wacoal ne correspondent pas toujours aux slugs
standards ; ce fichier assure la normalisation vers la taxonomie commune.
"""
from __future__ import annotations

import re

from app.connectors.spanx.mappings import (
    clean_description,
    extract_colors,
    extract_materials,
    extract_rating_and_reviews,
    extract_sizes,
    extract_variants_detailed,
    normalize_availability,
    normalize_price,
)

# ---------------------------------------------------------------------------
# Correspondance catégories Wacoal → famille taxonomique commune
# ---------------------------------------------------------------------------

CATEGORY_MAPPINGS: dict[str, str] = {
    # Shapewear bodysuits
    "shapewear-bodysuits": "Bodysuit",
    "shapewear bodysuits": "Bodysuit",
    "bodysuit":            "Bodysuit",
    "bodysuits":           "Bodysuit",
    "body briefer":        "Bodysuit",
    "body briefers":       "Bodysuit",
    "all-in-one":          "Bodysuit",
    # Shapers génériques
    "shapewear-shapers":   "Bodysuit",
    "shapewear shapers":   "Bodysuit",
    "shapers":             "Bodysuit",
    "shapewear":           "Bodysuit",
    # Soutiens-gorge
    "shapewear-bras":      "Bra",
    "bras":                "Bra",
    "bra":                 "Bra",
    "sports-bras":         "Bra",
    "sports bras":         "Bra",
    "sport bra":           "Bra",
    "bralette":            "Bra",
    "bralettes":           "Bra",
    "minimizer bra":       "Bra",
    "full figure bra":     "Bra",
    "nursing bra":         "Bra",
    # Shorts & cuisse
    "shapewear-shorts-and-thigh-slimmers": "Shaper Short",
    "shorts and thigh slimmers":           "Shaper Short",
    "thigh slimmer":                       "Shaper Short",
    "thigh slimmers":                      "Shaper Short",
    "bike shorts":                         "Shaper Short",
    "bike short":                          "Shaper Short",
    "shorts":                              "Shaper Short",
    # Cintureurs
    "shapewear-waist-cinchers": "Bodysuit",
    "waist cinchers":           "Bodysuit",
    "waist cincher":            "Bodysuit",
    "waist nipper":             "Bodysuit",
    # Culottes & bas
    "underwear":  "Panty",
    "panties":    "Panty",
    "panty":      "Panty",
    "thongs":     "Panty",
    "thong":      "Panty",
    "bikinis":    "Panty",
    "bikini":     "Panty",
    "briefs":     "Panty",
    "brief":      "Panty",
    "hipster":    "Panty",
    # Camisoles & débardeurs
    "shapewear-camisoles-and-tanks": "Tank & Cami",
    "camisoles and tanks":           "Tank & Cami",
    "camisoles":                     "Tank & Cami",
    "camisole":                      "Tank & Cami",
    "tanks":                         "Tank & Cami",
    "tank":                          "Tank & Cami",
    "shapewear tank":                "Tank & Cami",
    # Leggings (rare chez Wacoal mais possible)
    "leggings":   "Shaper Legging",
    "legging":    "Shaper Legging",
}

# ---------------------------------------------------------------------------
# Tags signalant un Best Seller chez Wacoal
# ---------------------------------------------------------------------------

_WACOAL_BS_TAGS: frozenset[str] = frozenset({
    "best seller",
    "bestseller",
    "best-seller",
    "top seller",
    "fan favorite",
    "fan-favorite",
    "top rated",
    "editor's pick",
    "editors pick",
    "award winner",
    "award-winning",
})

# Tags identifiant la sous-marque b.tempt'd
_BTEMPTD_TAGS: frozenset[str] = frozenset({
    "b.tempt'd",
    "btemptd",
    "b temptd",
})


# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------

def extract_best_seller_wacoal(
    tags: list[str] | str,
    config_tags: list[str] | None = None,
) -> bool:
    """Détecte le statut Best Seller depuis les tags Shopify Wacoal."""
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    check = _WACOAL_BS_TAGS
    if config_tags:
        check = check | {t.lower() for t in config_tags}
    return any(t.strip().lower() in check for t in tags)


def map_category_wacoal(raw: str | None) -> str | None:
    """
    Normalise une catégorie brute Wacoal vers la famille taxonomique commune.
    Accepte les slugs Shopify (avec tirets) et les libellés lisibles.
    """
    if not raw:
        return None
    key = raw.lower().strip()
    # Essai direct
    if key in CATEGORY_MAPPINGS:
        return CATEGORY_MAPPINGS[key]
    # Essai avec normalisation des tirets → espaces
    key_normalized = key.replace("-", " ").replace("_", " ")
    return CATEGORY_MAPPINGS.get(key_normalized)


def extract_sub_brand_wacoal(
    vendor: str | None,
    tags: list[str],
    title: str | None,
) -> str:
    """
    Détecte la sous-marque Wacoal (Wacoal / b.tempt'd / Wacoal Sport).
    Utilisé pour enrichir le champ extra["sub_brand"].
    """
    if vendor:
        vendor_low = vendor.lower()
        if "b.tempt" in vendor_low or "btempt" in vendor_low:
            return "b.tempt'd"
        if "sport" in vendor_low:
            return "Wacoal Sport"
    tags_lower = {t.lower() for t in tags}
    if tags_lower & _BTEMPTD_TAGS:
        return "b.tempt'd"
    if title and "sport" in title.lower():
        return "Wacoal Sport"
    return "Wacoal"


def extract_cup_size_wacoal(sizes: list[str]) -> list[str]:
    """
    Filtre les tailles pour ne retourner que les tailles bonnet (30A, 32B…).
    Utile pour les soutiens-gorge Wacoal qui ont des gammes de tailles étendues.
    """
    cup_pattern = re.compile(r"^\d{2}[A-K]{1,2}$")
    return [s for s in sizes if cup_pattern.match(s.strip().upper())]