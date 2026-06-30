"""
Mappings SPANX — logique spécifique à la marque SPANX uniquement.

Le parsing JSON Shopify générique (prix, disponibilité, variantes, tailles,
couleurs, matériaux, avis) vit dans app/scraping/shopify_utils.py et est
partagé à égalité par tous les connecteurs. Ce fichier ne contient que ce
qui est propre au catalogue SPANX : ses libellés de catégories et ses tags
"best seller".
"""
from __future__ import annotations

# Champs bruts Shopify → champs du modèle normalisé (référence documentaire,
# le mapping réel est fait directement dans connector.py)
FIELD_MAPPINGS: dict[str, str] = {
    "title":        "name",
    "handle":       "external_id",
    "body_html":    "description",
    "vendor":       "brand_slug",
    "product_type": "category_raw",
}

# Catégories shapewear SPANX → famille taxonomique commune
CATEGORY_MAPPINGS: dict[str, str] = {
    "bodysuits": "Bodysuit", "bodysuit": "Bodysuit", "body": "Bodysuit",
    "shorts": "Shaper Short", "short": "Shaper Short",
    "leggings": "Shaper Legging", "legging": "Shaper Legging", "pants": "Shaper Legging",
    "bras": "Bra", "bra": "Bra", "bralette": "Bra",
    "panties": "Panty", "panty": "Panty", "underwear": "Panty",
    "tanks": "Tank", "tank": "Tank", "cami": "Tank",
    "swim": "Swimwear", "swimwear": "Swimwear",
}

# Tags Shopify signalant un Best Seller chez SPANX
_BEST_SELLER_TAGS = {"best seller", "bestseller", "best-seller", "top seller"}


def extract_best_seller(tags: list[str] | str) -> bool:
    """Détecte le statut Best Seller depuis les tags Shopify SPANX."""
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    return any(tag.strip().lower() in _BEST_SELLER_TAGS for tag in tags)


def map_category(raw_category: str | None) -> str | None:
    """Normalise une catégorie brute SPANX vers la famille taxonomique commune."""
    if not raw_category:
        return None
    return CATEGORY_MAPPINGS.get(raw_category.lower().strip())