"""Mappings Shapermint — logique spécifique à la marque Shapermint uniquement.

Le parsing JSON Shopify générique vit dans app/scraping/shopify_utils.py
et est partagé à égalité par tous les connecteurs.
"""
from __future__ import annotations
from app.scraping.shopify_utils import (
    normalize_price, normalize_availability, extract_variants_detailed,
    extract_sizes, extract_colors, extract_materials, clean_description,
)

CATEGORY_MAPPINGS: dict[str, str] = {
    "shapewear":      "Bodysuit",
    "bodysuits":      "Bodysuit",
    "bras":           "Bra",
    "briefs":         "Panty",
    "thighs-slimmer": "Shaper Short",
    "waist-trainers": "Bodysuit",
    "leggings":       "Shaper Legging",
}

_SM_BS_TAGS = {"best seller", "bestseller", "top seller", "popular", "best-seller"}

def extract_best_seller_sm(tags: list[str] | str, config_tags: list[str] | None = None) -> bool:
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    check = _SM_BS_TAGS
    if config_tags:
        check = check | {t.lower() for t in config_tags}
    return any(t.strip().lower() in check for t in tags)

def map_category_sm(raw: str | None) -> str | None:
    if not raw:
        return None
    return CATEGORY_MAPPINGS.get(raw.lower().strip())