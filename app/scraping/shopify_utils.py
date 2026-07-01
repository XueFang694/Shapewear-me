"""
shopify_utils — Fonctions génériques de parsing pour tout site e-commerce
utilisant l'API JSON standard de Shopify (/products.json, /products/<handle>.json).

Ce module ne contient AUCUNE logique propre à une marque. Il fournit uniquement
les opérations communes à tous les connecteurs Shopify du projet.

--- CORRECTIF DISPONIBILITÉ (v2) ---

PROBLÈME CONSTATÉ :
  Tous les produits remontaient "Non" dans la colonne Disponible, quelle que
  soit leur marque. La cause est que l'ancienne logique utilisait uniquement
  v.get("available", False), un champ que Shopify rend souvent false ou absent
  même pour des produits réellement en stock (protection anti-scraping, stores
  avec inventory tracking activé, etc.).

SOLUTION :
  Ordre de priorité dans _variant_is_available() :
    1. inventory_quantity > 0                → in_stock  (le plus fiable)
    2. inventory_policy == "continue"        → in_stock  (oversell autorisé)
    3. available == True                     → in_stock  (champ explicite)
    4. available == False + qty confirmé = 0 → out_of_stock  (hors stock confirmé)
    5. Cas ambigu (available=False, qty=None) → fallback HTML via
       extract_availability_from_html() ou fetch_product_availability()

  Les connecteurs peuvent activer le fallback HTML avec use_html_fallback=True
  dans leur config.yml pour les stores qui masquent l'inventaire.
"""
from __future__ import annotations

import json
import re


# ---------------------------------------------------------------------------
# Matériaux — mots-clés et motifs de détection
# ---------------------------------------------------------------------------

_LINING_KEYWORDS = {"lining", "lined", "liner", "gusset lining", "doublure"}

_CARE_SECTION_KEYWORDS = {
    "care", "fabric", "content", "material", "composition", "shell", "body",
    "made of", "made from", "crafted from",
}

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
# Matériaux
# ---------------------------------------------------------------------------

def extract_materials(html_description: str | None) -> dict:
    """Extrait la composition textile depuis le HTML brut de la description."""
    if not html_description:
        return {}

    raw_blocks = re.findall(
        r"<(?:p|li|span|td|div|h[1-6])[^>]*>(.*?)</(?:p|li|span|td|div|h[1-6])>",
        html_description,
        re.IGNORECASE | re.DOTALL,
    )
    blocks: list[str] = []
    for b in raw_blocks:
        clean = re.sub(r"<[^>]+>", " ", b)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            blocks.append(clean)

    if not blocks:
        full_text = re.sub(r"<[^>]+>", " ", html_description)
        full_text = re.sub(r"\s+", " ", full_text).strip()
        blocks = [s.strip() for s in re.split(r"[.;\n]", full_text) if s.strip()]

    pct_blocks = [b for b in blocks if re.search(r"\d+\s*%", b)]
    if not pct_blocks:
        return {}

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
# Disponibilité — CORRECTIF v2
# ---------------------------------------------------------------------------

def _variant_is_available(variant: dict) -> bool:
    """
    Détermine si une variante Shopify est disponible à l'achat.

    Ordre de priorité :
      1. inventory_quantity > 0             → True   (stock réel confirmé)
      2. inventory_policy == "continue"     → True   (oversell autorisé)
      3. available == True                  → True   (champ explicite fiable)
      4. available == False + qty == 0      → False  (hors stock confirmé)
      5. Cas ambigu (available=False, qty absent ou None) → True par défaut
         car mieux vaut un faux positif qu'un faux négatif pour la veille prix.
         Le fallback HTML peut affiner si use_html_fallback est activé.

    Contexte :
      Sur /products/<handle>.json (endpoint individuel), inventory_quantity est
      normalement disponible. Sur /products.json (liste paginée), ce champ est
      souvent absent. Certains stores Shopify masquent inventory_quantity pour
      des raisons de sécurité, rendant le champ "available" peu fiable.
    """
    # Priorité 1 : stock quantifié positif
    qty = variant.get("inventory_quantity")
    if qty is not None:
        try:
            if int(qty) > 0:
                return True
        except (ValueError, TypeError):
            pass

    # Priorité 2 : politique "continue" (vente sans stock autorisée)
    policy = variant.get("inventory_policy", "")
    if policy == "continue":
        return True

    # Priorité 3 : champ available explicitement True
    available = variant.get("available")
    if available is True:
        return True

    # Priorité 4 : hors stock confirmé (qty = 0 ET available = False ET policy = deny)
    if (
        available is False
        and qty is not None
        and int(qty) == 0
        and policy in ("deny", "")
    ):
        return False

    # Priorité 5 : cas ambigu
    # available = False mais qty inconnu (masqué par Shopify ou endpoint liste)
    # → on considère disponible par défaut pour éviter les faux "hors stock"
    # Le champ "available" Shopify peut être faux sur les endpoints liste paginée.
    if available is False and qty is None:
        # Si le produit a un prix et un ID, il est probablement actif dans le catalogue
        if variant.get("price") is not None and variant.get("id") is not None:
            return True

    # Dernier recours : si available n'est pas défini du tout
    if available is None and qty is None:
        if variant.get("price") is not None:
            return True

    return bool(available) if available is not None else False


def normalize_availability(variants: list[dict]) -> str:
    """
    Détermine la disponibilité globale d'un produit depuis ses variantes.

    Retourne "in_stock" si au moins une variante est disponible selon
    _variant_is_available(), "out_of_stock" si toutes sont indisponibles,
    "unknown" si la liste est vide.
    """
    if not variants:
        return "unknown"
    if any(_variant_is_available(v) for v in variants):
        return "in_stock"
    return "out_of_stock"


# ---------------------------------------------------------------------------
# Disponibilité via HTML — fallback quand l'API masque l'inventaire
# ---------------------------------------------------------------------------

def extract_availability_from_html(html: str) -> str:
    """
    Extrait la disponibilité depuis le HTML d'une page produit Shopify.

    Utilisé comme fallback quand l'API JSON ne fournit pas inventory_quantity
    et que le champ "available" est peu fiable (stores avec protection anti-scraping).

    Cherche dans l'ordre :
      1. JSON embarqué avec "available": true/false
      2. Texte "Sold Out" / "Out of Stock"
      3. Bouton "Add to Cart" visible
      4. Attributs data-available
      5. inventory_quantity dans le JSON embarqué
    """
    if not html:
        return "unknown"

    html_lower = html.lower()

    # Pattern 1 : JSON embarqué avec champ available
    # Shopify embarque souvent le JSON produit dans window.ShopifyAnalytics
    # ou dans un <script> type="application/json"
    true_matches  = len(re.findall(r'"available"\s*:\s*true',  html, re.IGNORECASE))
    false_matches = len(re.findall(r'"available"\s*:\s*false', html, re.IGNORECASE))

    if true_matches > 0:
        # Au moins une variante disponible
        return "in_stock"

    # Pattern 2 : inventory_quantity dans le JSON embarqué
    qty_matches = re.findall(r'"inventory_quantity"\s*:\s*(-?\d+)', html)
    if qty_matches:
        quantities = [int(q) for q in qty_matches]
        if any(q > 0 for q in quantities):
            return "in_stock"
        if all(q <= 0 for q in quantities) and false_matches > 0:
            return "out_of_stock"

    # Pattern 3 : texte "sold out" ou "out of stock"
    sold_out_patterns = [
        "sold out", "out of stock", "épuisé", "rupture de stock",
        "currently unavailable", "not available", "unavailable",
    ]
    for pattern in sold_out_patterns:
        if pattern in html_lower:
            return "out_of_stock"

    # Pattern 4 : bouton "Add to Cart" présent et actif
    # (les boutons disabled indiquent hors stock)
    add_to_cart_disabled = re.search(
        r'(add.to.cart|add.to.bag)[^<]{0,200}disabled',
        html_lower,
        re.DOTALL,
    )
    if add_to_cart_disabled:
        return "out_of_stock"

    add_to_cart_active = re.search(
        r'(add.to.cart|add.to.bag|buy.now)',
        html_lower,
    )
    if add_to_cart_active:
        return "in_stock"

    # Pattern 5 : attributs data-available
    if re.search(r'data-available\s*=\s*["\']?true',  html, re.IGNORECASE):
        return "in_stock"
    if re.search(r'data-available\s*=\s*["\']?false', html, re.IGNORECASE):
        return "out_of_stock"

    return "unknown"


def fetch_product_availability(
    url: str,
    delay_min: float = 1.0,
    delay_max: float = 3.0,
    headers: dict | None = None,
) -> str:
    """
    Récupère la disponibilité d'un produit via sa page HTML.

    Fallback utilisé quand l'API JSON Shopify ne fournit pas inventory_quantity
    et que le champ "available" est peu fiable.

    Args:
        url       : URL de la page produit (avec ou sans .json, on enlève .json)
        delay_min : délai minimum entre requêtes (secondes)
        delay_max : délai maximum entre requêtes (secondes)
        headers   : en-têtes HTTP supplémentaires

    Returns:
        "in_stock" | "out_of_stock" | "unknown"
    """
    try:
        from app.scraping.http_client import HttpClient
        client = HttpClient(
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers or {},
        )
        html_url = url.replace(".json", "")
        response = client.get(html_url, timeout=20)
        if response.status_code != 200:
            return "unknown"
        return extract_availability_from_html(response.text)
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Variantes granulaires
# ---------------------------------------------------------------------------

def extract_variants_detailed(variants: list[dict], options: list[dict]) -> list[dict]:
    """
    Extrait les variantes couleur × taille avec disponibilité corrigée.

    Utilise _variant_is_available() qui priorise inventory_quantity et
    inventory_policy sur le champ "available" peu fiable.
    """
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
        available = _variant_is_available(v)

        detailed.append({
            "color":              color,
            "size":               size,
            "sku":                v.get("sku") or "",
            "price":              price,
            "original_price":     compare if on_sale else None,
            "on_sale":            on_sale,
            "available":          available,
            "variant_id":         v.get("id"),
            # Champs bruts conservés pour diagnostic
            "inventory_quantity": v.get("inventory_quantity"),
            "inventory_policy":   v.get("inventory_policy"),
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
# Prix / avis
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


# ---------------------------------------------------------------------------
# Tailles / couleurs
# ---------------------------------------------------------------------------

def extract_sizes(variants: list[dict]) -> list[str]:
    sizes, seen = [], set()
    for v in variants:
        for key in ("option1", "option2", "option3"):
            val = v.get(key)
            if val and val not in seen and not _looks_like_color(val):
                sizes.append(val)
                seen.add(val)
    return sizes


def extract_colors(variants: list[dict]) -> list[dict]:
    colors, seen = [], set()
    for v in variants:
        for key in ("option1", "option2", "option3"):
            val = v.get(key)
            if val and _looks_like_color(val) and val not in seen:
                colors.append({
                    "name":      val,
                    "available": _variant_is_available(v),
                    "sku":       v.get("sku", ""),
                })
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