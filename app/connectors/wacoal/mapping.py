"""
Mappings Wacoal America — logique spécifique à la marque Wacoal uniquement
(sous-marques, tailles bonnet, tags best-seller).

Le parsing JSON Shopify générique vit dans app/scraping/shopify_utils.py
et est partagé à égalité par tous les connecteurs.
"""
from __future__ import annotations

import re

from app.scraping.shopify_utils import (
    clean_description,
    extract_colors,
    extract_materials,
    extract_rating_and_reviews,
    extract_sizes,
    extract_variants_detailed,
    normalize_availability,
    normalize_price,
)

CATEGORY_MAPPINGS: dict[str, str] = {
    "shapewear-bodysuits": "Bodysuit",
    "shapewear bodysuits": "Bodysuit",
    "bodysuit":            "Bodysuit",
    "bodysuits":           "Bodysuit",
    "body briefer":        "Bodysuit",
    "body briefers":       "Bodysuit",
    "all-in-one":          "Bodysuit",
    "shapewear-shapers":   "Bodysuit",
    "shapewear shapers":   "Bodysuit",
    "shapers":             "Bodysuit",
    "shapewear":           "Bodysuit",
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
    "shapewear-shorts-and-thigh-slimmers": "Shaper Short",
    "shorts and thigh slimmers":           "Shaper Short",
    "thigh slimmer":                       "Shaper Short",
    "thigh slimmers":                      "Shaper Short",
    "bike shorts":                         "Shaper Short",
    "bike short":                          "Shaper Short",
    "shorts":                              "Shaper Short",
    "shapewear-waist-cinchers": "Bodysuit",
    "waist cinchers":           "Bodysuit",
    "waist cincher":            "Bodysuit",
    "waist nipper":             "Bodysuit",
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
    "shapewear-camisoles-and-tanks": "Tank & Cami",
    "camisoles and tanks":           "Tank & Cami",
    "camisoles":                     "Tank & Cami",
    "camisole":                      "Tank & Cami",
    "tanks":                         "Tank & Cami",
    "tank":                          "Tank & Cami",
    "shapewear tank":                "Tank & Cami",
    "leggings":   "Shaper Legging",
    "legging":    "Shaper Legging",
}

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

_BTEMPTD_TAGS: frozenset[str] = frozenset({
    "b.tempt'd",
    "btemptd",
    "b temptd",
})


def extract_best_seller_wacoal(
    tags: list[str] | str,
    config_tags: list[str] | None = None,
) -> bool:
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    check = _WACOAL_BS_TAGS
    if config_tags:
        check = check | {t.lower() for t in config_tags}
    return any(t.strip().lower() in check for t in tags)


def map_category_wacoal(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.lower().strip()
    if key in CATEGORY_MAPPINGS:
        return CATEGORY_MAPPINGS[key]
    key_normalized = key.replace("-", " ").replace("_", " ")
    return CATEGORY_MAPPINGS.get(key_normalized)


def extract_sub_brand_wacoal(
    vendor: str | None,
    tags: list[str],
    title: str | None,
) -> str:
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
    cup_pattern = re.compile(r"^\d{2}[A-K]{1,2}$")
    return [s for s in sizes if cup_pattern.match(s.strip().upper())]