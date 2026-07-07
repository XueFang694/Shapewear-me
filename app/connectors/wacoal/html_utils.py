"""
Fonctions d'extraction HTML spécifiques aux pages produit Wacoal America.

Wacoal est un site Shopify standard dont les pages HTML contiennent :
  1. Matières — dans <span class="metafield-single_line_text_field"> à l'intérieur
     de la section "fabric/care" du composant ProductDescList.
     Exemple : "Lace: 88% Nylon/ 12% Spandex; Body: 65% Nylon/ 35% Spandex;
                Panel Lining: 100% Cotton"

  2. Disponibilité par variante — dans le JSON Shopify embarqué `mntn_product_data`
     (ou `Shop._PRODUCT_JSON_`). Wacoal masque inventory_quantity et inventory_policy ;
     le champ "available" (boolean) est la seule source fiable par variante.

  3. Note et avis — via BazaarVoice. Les divs data-bv-show="rating_summary" et
     data-bv-show="reviews" sont vides dans le HTML statique (chargement JS asynchrone).
     Les metafields Yotpo/Loox sont null. On récupère la note via l'API BV Statistics :
       GET https://api.bazaarvoice.com/data/statistics.json
           ?passKey=<BV_PASS_KEY>
           &productId=<shopify_product_id>
           &stats=Reviews
     Le passkey BV public de Wacoal est stable et extrait du fichier bv.js déployé.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constantes BazaarVoice
# ---------------------------------------------------------------------------

# Passkey public BazaarVoice pour wacoal/main_site/production/en_US.
# Extrait du fichier deployé :
#   apps.bazaarvoice.com/deployments/wacoal/main_site/production/en_US/bv.js
# Ce passkey est public (visible dans le JS client) et donne accès en lecture
# aux statistiques agrégées uniquement (note, nombre d'avis). Aucune donnée
# personnelle n'est exposée.
_BV_PASS_KEY = "caElc2g6VBb1LfIMOqRFWr0S0QGKuoiiu61f4RAlnKH7k"
_BV_STATS_URL = "https://api.bazaarvoice.com/data/statistics.json"

# ---------------------------------------------------------------------------
# Patterns de fibres (identiques à shopify_utils mais avec variantes Wacoal)
# ---------------------------------------------------------------------------

_FIBER_PATTERNS: list[tuple[str, str]] = [
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
    (r"(\d+(?:\.\d+)?)\s*%\s*recycled",    "recycled"),
    (r"(\d+(?:\.\d+)?)\s*%\s*bamboo",      "bamboo"),
    (r"(\d+(?:\.\d+)?)\s*%\s*acrylic",     "acrylic"),
]

_LINING_KEYWORDS = frozenset({"lining", "liner", "lined", "gusset", "crotch"})

# ---------------------------------------------------------------------------
# 1. Matières
# ---------------------------------------------------------------------------

def extract_materials_from_wacoal_html(html: str) -> dict:
    """
    Extrait la composition textile depuis le HTML d'une page produit Wacoal.

    Source : <span class="metafield-single_line_text_field"> dans la section
    "fabric/care" du composant ProductDescList.

    Format attendu (exemples réels) :
      "Lace: 88% Nylon/ 12% Spandex; Body: 65% Nylon/ 35% Spandex;
       Panel Lining: 100% Cotton"
      "Lace: 75% Nylon/25% Spandex; Back: 67% Nylon/33% Spandex;
       Crotch Lining: 100% Cotton"

    Retourne un dict avec :
        material_raw                : texte brut complet
        material_main               : composition hors doublure
        material_lining             : doublure/gusset/crotch (si présent)
        material_composition_json   : {"nylon": 73.5, "elastane": 26.5}

    Retourne {} si aucune composition n'est trouvée.
    """
    if not html:
        return {}

    # Chercher le span metafield dans la section fabric/care
    # Pattern : ProductDescList-copy > Fabric content > metafield-single_line_text_field
    raw_text: str | None = None

    # Pattern principal : après "Fabric content:" dans la section ProductDescList-copy
    m = re.search(
        r'[Ff]abric content[^<]{0,50}'
        r'metafield-single_line_text_field["\s>]+([^<]{5,400})</span>',
        html,
        re.DOTALL,
    )
    if m:
        raw_text = m.group(1).strip()

    # Fallback : tout metafield-single_line_text_field contenant un "%"
    if not raw_text:
        candidates = re.findall(
            r'metafield-single_line_text_field["\s>]+([^<]{5,400})</span>',
            html,
        )
        for c in candidates:
            if "%" in c and any(f in c.lower() for f in ("nylon", "spandex", "cotton", "polyester", "elastane")):
                raw_text = c.strip()
                break

    if not raw_text:
        return {}

    result: dict = {"material_raw": raw_text[:500]}

    # Séparer les sections en utilisant ";" comme délimiteur
    # Exemple : "Lace: 88% Nylon/ 12% Spandex; Body: 65% Nylon/ 35% Spandex; Panel Lining: 100% Cotton"
    sections = [s.strip() for s in re.split(r";", raw_text) if s.strip()]

    main_parts: list[str] = []
    lining_parts: list[str] = []

    for section in sections:
        low = section.lower()
        if any(kw in low for kw in _LINING_KEYWORDS):
            lining_parts.append(section)
        elif re.search(r"\d+\s*%", section):
            main_parts.append(section)
        else:
            # Section sans %, pas de lining → ajouter à main quand même
            # (ex: section introductive comme "Lace:")
            if section:
                main_parts.append(section)

    if main_parts:
        result["material_main"] = "; ".join(main_parts)[:255]
    if lining_parts:
        result["material_lining"] = "; ".join(lining_parts)[:255]

    # Extraction des pourcentages par fibre depuis le texte complet
    comp_text = raw_text.lower()
    composition: dict[str, float] = {}
    for pattern, fiber in _FIBER_PATTERNS:
        matches = re.findall(pattern, comp_text, re.IGNORECASE)
        if matches and fiber not in composition:
            try:
                composition[fiber] = float(matches[0])
            except ValueError:
                pass

    if composition:
        result["material_composition_json"] = json.dumps(composition)

    log.debug(
        "Wacoal matières extraites",
        main=result.get("material_main"),
        lining=result.get("material_lining"),
        comp=list(composition.keys()),
    )
    return result


# ---------------------------------------------------------------------------
# 2. Disponibilité par variante (depuis le JSON embarqué)
# ---------------------------------------------------------------------------

def extract_variant_availability_from_html(html: str) -> dict[str, bool]:
    """
    Extrait un mapping {sku: available} depuis le JSON Shopify embarqué dans
    la page HTML produit Wacoal.

    Wacoal masque inventory_quantity et inventory_policy dans son JSON Shopify.
    Le champ "available" (boolean) est la seule source fiable par variante et
    il est exposé dans les deux objets JSON inline :
      - mntn_product_data
      - Shop._PRODUCT_JSON_

    Retourne un dict {sku: bool}, ex : {"841341.437.M": False, "841341.437.L": True}
    Retourne {} si aucune donnée n'est trouvée.
    """
    if not html:
        return {}

    variants_data: list[dict] = []

    # Priorité 1 : mntn_product_data (données complètes)
    m = re.search(r"mntn_product_data\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            variants_data = d.get("variants", [])
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback : Shop._PRODUCT_JSON_
    if not variants_data:
        m2 = re.search(r"Shop\._PRODUCT_JSON_\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
        if m2:
            try:
                d2 = json.loads(m2.group(1))
                variants_data = d2.get("variants", [])
            except (json.JSONDecodeError, ValueError):
                pass

    if not variants_data:
        return {}

    result: dict[str, bool] = {}
    for v in variants_data:
        sku = v.get("sku", "")
        available = bool(v.get("available", False))
        if sku:
            result[sku] = available

    log.debug(
        "Wacoal disponibilité variantes extraite",
        total=len(result),
        unavailable=sum(1 for a in result.values() if not a),
    )
    return result


def apply_html_availability_to_variants(
    variants: list[dict],
    sku_availability: dict[str, bool],
) -> list[dict]:
    """
    Applique le mapping {sku: available} extrait de la page HTML aux variantes
    déjà parsées depuis le JSON Shopify.

    Modifie chaque variante en place (copie) et retourne la liste mise à jour.
    Les variantes sans SKU dans le mapping ne sont pas modifiées.
    """
    if not sku_availability:
        return variants

    updated = []
    for v in variants:
        sku = v.get("sku", "")
        if sku and sku in sku_availability:
            v = dict(v)  # copie pour ne pas muter l'original
            v["available"] = sku_availability[sku]
        updated.append(v)
    return updated


# ---------------------------------------------------------------------------
# 3. Note et avis — API BazaarVoice Statistics
# ---------------------------------------------------------------------------

def fetch_bv_rating(
    product_id: str,
    delay_min: float = 1.0,
    delay_max: float = 3.0,
    headers: dict | None = None,
) -> tuple[float | None, int | None]:
    """
    Récupère la note moyenne et le nombre d'avis via l'API BazaarVoice Statistics.

    Endpoint :
        GET https://api.bazaarvoice.com/data/statistics.json
            ?passKey=<BV_PASS_KEY>
            &productId=<shopify_product_id>
            &stats=Reviews

    Le `product_id` est l'ID Shopify numérique du produit (ex: "9149775315160"),
    visible dans la page HTML dans `data-bv-product-id`.

    Retourne (rating, review_count) ou (None, None) en cas d'échec.
    """
    if not product_id:
        return None, None

    try:
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers or {},
        )
        response = client.get(
            _BV_STATS_URL,
            params={
                "passKey":   _BV_PASS_KEY,
                "productId": product_id,
                "stats":     "Reviews",
            },
            timeout=15,
        )

        if response.status_code != 200:
            log.debug(
                "BazaarVoice Statistics : réponse non-200",
                product_id=product_id,
                status=response.status_code,
            )
            return None, None

        data = response.json()
        results = data.get("Results", [])
        if not results:
            return None, None

        review_stats = results[0].get("ReviewStatistics", {})
        rating = review_stats.get("AverageOverallRating")
        count  = review_stats.get("TotalReviewCount")

        rating = round(float(rating), 1) if rating is not None else None
        count  = int(count) if count is not None else None

        log.debug(
            "BazaarVoice Statistics OK",
            product_id=product_id,
            rating=rating,
            count=count,
        )
        return rating, count

    except Exception as exc:
        log.debug(
            "BazaarVoice Statistics : erreur",
            product_id=product_id,
            error=str(exc),
        )
        return None, None


def fetch_bv_reviews(
    product_id: str,
    limit: int = 100,
    delay_min: float = 1.0,
    delay_max: float = 3.0,
    headers: dict | None = None,
) -> list[dict]:
    """
    Récupère les avis texte via l'API BazaarVoice Reviews.

    Endpoint :
        GET https://api.bazaarvoice.com/data/reviews.json
            ?passKey=<BV_PASS_KEY>
            &ProductId=<shopify_product_id>
            &Limit=<limit>
            &Offset=0
            &Sort=SubmissionTime:desc

    Retourne une liste de dicts normalisés compatibles avec le format
    utilisé dans NormalizedProduct.reviews_text_json :
        [{"rating": 5, "title": "...", "body": "...", "date": "YYYY-MM-DD",
          "variant": "...", "author": "..."}]

    Retourne [] en cas d'échec ou si aucun avis.
    """
    if not product_id:
        return []

    _BV_REVIEWS_URL = "https://api.bazaarvoice.com/data/reviews.json"

    try:
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers or {},
        )
        response = client.get(
            _BV_REVIEWS_URL,
            params={
                "passKey":   _BV_PASS_KEY,
                "ProductId": product_id,
                "Limit":     str(min(limit, 100)),  # BV max = 100 par page
                "Offset":    "0",
                "Sort":      "SubmissionTime:desc",
            },
            timeout=20,
        )

        if response.status_code != 200:
            log.debug(
                "BazaarVoice Reviews : réponse non-200",
                product_id=product_id,
                status=response.status_code,
            )
            return []

        data = response.json()
        raw_reviews = data.get("Results", [])

        reviews: list[dict] = []
        for r in raw_reviews:
            rating = r.get("Rating")
            title  = (r.get("Title") or "").strip()
            text   = (r.get("ReviewText") or "").strip()
            date_raw = r.get("SubmissionTime", "")[:10]  # "YYYY-MM-DDT..." → "YYYY-MM-DD"
            author   = (r.get("UserNickname") or "").strip()

            # Extraire la variante si disponible dans ContextDataValues
            ctx_data = r.get("ContextDataValues", {})
            variant_parts: list[str] = []
            for key in ("Size", "Color", "Shade"):
                val = ctx_data.get(key, {})
                if isinstance(val, dict):
                    v = val.get("Value") or val.get("DimensionLabel", "")
                else:
                    v = str(val) if val else ""
                if v:
                    variant_parts.append(v)
            variant = " / ".join(variant_parts)

            if title or text:
                reviews.append({
                    "rating":  int(rating) if rating is not None else None,
                    "title":   title,
                    "body":    text,
                    "date":    date_raw,
                    "variant": variant,
                    "author":  author,
                })

        log.debug(
            "BazaarVoice Reviews OK",
            product_id=product_id,
            count=len(reviews),
        )
        return reviews

    except Exception as exc:
        log.debug(
            "BazaarVoice Reviews : erreur",
            product_id=product_id,
            error=str(exc),
        )
        return []