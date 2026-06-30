"""Mappings SKIMS — logique spécifique à la marque SKIMS uniquement.

Le parsing JSON Shopify générique vit dans app/scraping/shopify_utils.py
et est partagé à égalité par tous les connecteurs.
"""
from __future__ import annotations
from app.scraping.shopify_utils import (
    normalize_price, normalize_availability, extract_variants_detailed,
    extract_sizes, extract_colors, extract_materials, clean_description,
)
import re

CATEGORY_MAPPINGS: dict[str, str] = {
    "bodywear":   "Bodysuit",
    "body":       "Bodysuit",
    "bras":       "Bra",
    "bra":        "Bra",
    "underwear":  "Panty",
    "swim":       "Swimwear",
    "shorts":     "Shaper Short",
    "leggings":   "Shaper Legging",
    "loungewear": "Tank",
    "sleep":      "Tank",
}

_SKIMS_BS_TAGS = {"best seller", "bestseller", "top rated", "fan favorite", "fan-favorite"}

def extract_best_seller_skims(tags: list[str] | str, config_tags: list[str] | None = None) -> bool:
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    check_tags = _SKIMS_BS_TAGS
    if config_tags:
        check_tags = check_tags | {t.lower() for t in config_tags}
    return any(t.strip().lower() in check_tags for t in tags)

def map_category_skims(raw: str | None) -> str | None:
    if not raw:
        return None
    return CATEGORY_MAPPINGS.get(raw.lower().strip())