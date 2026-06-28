"""
Mappings SPANX v3 — extraction matériaux corrigée + géolocalisation.
"""
from __future__ import annotations

import re
import json


FIELD_MAPPINGS: dict[str, str] = {
    "title":        "name",
    "handle":       "external_id",
    "body_html":    "description",
    "vendor":       "brand_slug",
    "product_type": "category_raw",
}

CATEGORY_MAPPINGS: dict[str, str] = {
    "bodysuits": "Bodysuit", "bodysuit": "Bodysuit", "body": "Bodysuit",
    "shorts": "Shaper Short", "short": "Shaper Short",
    "leggings": "Shaper Legging", "legging": "Shaper Legging", "pants": "Shaper Legging",
    "bras": "Bra", "bra": "Bra", "bralette": "Bra",
    "panties": "Panty", "panty": "Panty", "underwear": "Panty",
    "tanks": "Tank", "tank": "Tank", "cami": "Tank",
    "swim": "Swimwear", "swimwear": "Swimwear",
}

_BEST_SELLER_TAGS = {"best seller", "bestseller", "best-seller", "top seller"}

# Mots-clés annonçant une doublure
_LINING_KEYWORDS = {"lining", "lined", "liner", "gusset lining", "doublure"}

# Mots-clés qui signalent une section "composition" dans la description
_CARE_SECTION_KEYWORDS = {
    "care", "fabric", "content", "material", "composition", "shell", "body",
    "made of", "made from", "crafted from",
}

# Fibres textiles — (regex_pattern, nom_canonique)
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
    (r"(\d+(?:\.\d+)?)\s*%\s*silk",        "silk"),
    (r"(\d+(?:\.\d+)?)\s*%\s*wool",        "wool"),
    (r"(\d+(?:\.\d+)?)\s*%\s*acrylic",     "acrylic"),
    (r"(\d+(?:\.\d+)?)\s*%\s*bamboo",      "bamboo"),
    (r"(\d+(?:\.\d+)?)\s*%\s*recycled",    "recycled"),
]


# ---------------------------------------------------------------------------
# Best Seller
# ---------------------------------------------------------------------------

def extract_best_seller(tags: list[str] | str) -> bool:
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    return any(tag.strip().lower() in _BEST_SELLER_TAGS for tag in tags)


# ---------------------------------------------------------------------------
# Matériaux — approche bloc par bloc sur le HTML brut
# ---------------------------------------------------------------------------

def extract_materials(html_description: str | None) -> dict:
    """
    Extrait la composition textile depuis le HTML brut de la description.

    Stratégie :
      1. Extraire les blocs atomiques <p>, <li>, <span>, <td> du HTML brut
         → chaque bloc est une phrase courte, sans pollution du texte voisin
      2. Identifier les blocs contenant des pourcentages (composition)
      3. Séparer composition principale vs doublure
      4. Parser les fibres individuellement par regex directe

    Retourne :
        {
          "material_raw":              str,   # texte brut de composition
          "material_main":             str,   # composition principale
          "material_lining":           str,   # doublure si présente
          "material_composition_json": str,   # JSON {"nylon": 67, "elastane": 33}
        }
    """
    if not html_description:
        return {}

    # ── Étape 1 : extraire les blocs atomiques ───────────────────────────────
    # Capturer le contenu de chaque balise inline/block sans imbrication
    raw_blocks = re.findall(
        r"<(?:p|li|span|td|div|h[1-6])[^>]*>(.*?)</(?:p|li|span|td|div|h[1-6])>",
        html_description,
        re.IGNORECASE | re.DOTALL,
    )
    # Nettoyer les sous-balises internes et les espaces
    blocks: list[str] = []
    for b in raw_blocks:
        clean = re.sub(r"<[^>]+>", " ", b)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            blocks.append(clean)

    # Fallback si pas de blocs HTML détectés (texte brut)
    if not blocks:
        full_text = re.sub(r"<[^>]+>", " ", html_description)
        full_text = re.sub(r"\s+", " ", full_text).strip()
        blocks = [s.strip() for s in re.split(r"[.;\n]", full_text) if s.strip()]

    # ── Étape 2 : identifier les blocs de composition ────────────────────────
    pct_blocks = [b for b in blocks if re.search(r"\d+\s*%", b)]

    if not pct_blocks:
        return {}

    # ── Étape 3 : séparer composition principale et doublure ─────────────────
    main_blocks: list[str] = []
    lining_blocks: list[str] = []

    for block in pct_blocks:
        lower = block.lower()
        if any(kw in lower for kw in _LINING_KEYWORDS):
            lining_blocks.append(block)
        else:
            main_blocks.append(block)

    result: dict = {}

    if main_blocks:
        result["material_main"] = "; ".join(main_blocks)[:255]
    if lining_blocks:
        result["material_lining"] = "; ".join(lining_blocks)[:255]

    all_comp_blocks = main_blocks + lining_blocks
    result["material_raw"] = " | ".join(all_comp_blocks)[:500] if all_comp_blocks else None

    # ── Étape 4 : parser les fibres ──────────────────────────────────────────
    # Chercher dans le texte complet de composition (pas dans la description entière)
    comp_text = " ".join(all_comp_blocks).lower()
    composition: dict[str, float] = {}

    for pattern, fiber in _FIBER_PATTERNS:
        matches = re.findall(pattern, comp_text, re.IGNORECASE)
        if matches and fiber not in composition:
            composition[fiber] = float(matches[0])

    if composition:
        result["material_composition_json"] = json.dumps(composition)

    return result


# ---------------------------------------------------------------------------
# Variantes granulaires
# ---------------------------------------------------------------------------

def extract_variants_detailed(variants: list[dict], options: list[dict]) -> list[dict]:
    if not variants:
        return []

    color_pos, size_pos = _identify_option_positions(options, variants)
    detailed: list[dict] = []

    for v in variants:
        color   = _get_option(v, color_pos)
        size    = _get_option(v, size_pos)
        price   = normalize_price(v.get("price"))
        compare = normalize_price(v.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)

        detailed.append({
            "color":          color,
            "size":           size,
            "sku":            v.get("sku") or "",
            "price":          price,
            "original_price": compare if on_sale else None,
            "on_sale":        on_sale,
            "available":      bool(v.get("available", False)),
            "variant_id":     v.get("id"),
        })

    return detailed


def _identify_option_positions(options, variants) -> tuple[int | None, int | None]:
    color_pos = size_pos = None
    for opt in options:
        pos    = opt.get("position")
        values = opt.get("values", [])
        name   = (opt.get("name") or "").lower()
        if any(kw in name for kw in ("color", "colour", "couleur")):
            color_pos = pos
        elif any(kw in name for kw in ("size", "taille")):
            size_pos = pos
        else:
            color_count = sum(1 for v in values if _looks_like_color(v))
            if color_count > len(values) * 0.5:
                color_pos = pos
            else:
                size_pos = pos
    return color_pos, size_pos


def _get_option(variant: dict, position: int | None) -> str | None:
    if position is None:
        return None
    val = variant.get(f"option{position}")
    return val.strip() if val else None


# ---------------------------------------------------------------------------
# Prix / dispo / avis
# ---------------------------------------------------------------------------

def normalize_price(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return round(val, 2) if val > 0 else None
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    try:
        val = float(cleaned)
        return round(val, 2) if val > 0 else None
    except ValueError:
        return None


def normalize_availability(variants: list[dict]) -> str:
    if not variants:
        return "unknown"
    return "in_stock" if any(v.get("available", False) for v in variants) else "out_of_stock"


def extract_rating_and_reviews(metafields: list[dict] | None) -> tuple[float | None, int | None]:
    if not metafields:
        return None, None
    rating = count = None
    for mf in metafields:
        key = (mf.get("key") or "").lower()
        if "rating" in key and "count" not in key:
            try:
                rating = float(mf.get("value", 0))
            except (ValueError, TypeError):
                pass
        elif "count" in key or "reviews" in key:
            try:
                count = int(mf.get("value", 0))
            except (ValueError, TypeError):
                pass
    return rating, count


def clean_description(html: str | None) -> str | None:
    if not html:
        return None
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def map_category(raw_category: str | None) -> str | None:
    if not raw_category:
        return None
    return CATEGORY_MAPPINGS.get(raw_category.lower().strip())


# Compat
def extract_sizes(variants: list[dict]) -> list[str]:
    sizes, seen = [], set()
    for v in variants:
        for key in ("option1", "option2", "option3"):
            val = v.get(key)
            if val and val not in seen and not _looks_like_color(val):
                sizes.append(val); seen.add(val)
    return sizes


def extract_colors(variants: list[dict]) -> list[dict]:
    colors, seen = [], set()
    for v in variants:
        for key in ("option1", "option2", "option3"):
            val = v.get(key)
            if val and _looks_like_color(val) and val not in seen:
                colors.append({"name": val, "available": v.get("available", False), "sku": v.get("sku", "")})
                seen.add(val)
    return colors


_COLOR_HINTS = {
    "black", "white", "nude", "beige", "ivory", "pink", "red", "blue",
    "navy", "green", "grey", "gray", "brown", "tan", "camel", "blush",
    "champagne", "leopard", "floral", "stripe", "print", "multi",
    "natural", "cinnamon", "cocoa", "espresso", "warm", "cool",
    "cafe", "toasted", "coconut", "oatmeal", "chestnut", "soft",
    "classic", "very", "vivid", "naked", "rose", "coral", "dune",
    "anemone", "powder", "zest", "aster", "cassis", "current",
    "persimmon", "verbena", "eucalyptus", "fuchsia", "maritime",
    "cosmo", "nightshade", "caraway", "hibiscus", "sandbar", "clay",
    "sage", "timber", "summit", "granite", "honey", "ganache",
    "chai", "rosebud", "vintage", "hyacinth", "blackberry", "tidepool",
    "anthracite", "cacao", "petal", "indigo", "wave", "aurora",
}


def _looks_like_color(value: str) -> bool:
    lower = value.lower()
    return any(hint in lower for hint in _COLOR_HINTS)