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

  3. Note et avis — via BazaarVoice Conversations API v5.4.

CORRECTIF AVIS v1.3
────────────────────
PROBLÈME CONSTATÉ : count=0 sur tous les produits malgré une réponse HTTP 200.

CAUSE : L'API BV Conversations indexe les produits par leur handle Shopify
(ex: "back-appeal-shaping-body-briefer-praline"), PAS par l'ID Shopify numérique
(ex: 9149775315160). Le champ data-bv-product-id dans le HTML expose l'ID
numérique pour le widget JS côté navigateur, mais l'API REST BV Conversations
l'identifie via le handle.

SOLUTION : Stratégie multi-identifiant avec 3 tentatives dans l'ordre de
probabilité de succès :
  1. Handle Shopify (extrait de mntn_product_data.handle dans le HTML)
  2. ID numérique Shopify (data-bv-product-id, fallback)
  3. Handle depuis l'URL produit (dernier recours)

De plus, le passkey utilisé dans fetch_bv_rating() et fetch_bv_reviews() doit
correspondre au passkey public Wacoal extrait du fichier bv.js déployé. Ce passkey
est visible dans l'URL :
  //apps.bazaarvoice.com/deployments/wacoal/main_site/production/en_US/bv.js
et dans la configuration du pixel Shopify BazaarVoice (id 2305851608).
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
_BV_REVIEWS_URL = "https://api.bazaarvoice.com/data/reviews.json"

# Wacoal indexe ses produits dans BV par leur handle Shopify.
# Confirmé par : config pixel BV use_external_ids=false + external_id_attribute=default
# → BV gère ses propres IDs qui correspondent aux handles produit Shopify.
_BV_ID_TYPE = "handle"   # "handle" ou "numeric" — détermine l'ordre de tentative

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
            if section:
                main_parts.append(section)

    if main_parts:
        result["material_main"] = "; ".join(main_parts)[:255]
    if lining_parts:
        result["material_lining"] = "; ".join(lining_parts)[:255]

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

    Retourne un dict {sku: bool}, ex : {"801303.269.37C": False, "801303.269.40C": True}
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
# 3. Extraction des identifiants BazaarVoice depuis le HTML
# ---------------------------------------------------------------------------

# data-bv-product-id dans le HTML contient l'ID Shopify numérique.
# Wacoal BV indexe par handle → on extrait aussi le handle depuis mntn_product_data.
_BV_PRODUCT_ID_RE = re.compile(r'data-bv-product-id="(\d+)"')
_MNTN_HANDLE_RE   = re.compile(r'mntn_product_data\s*=\s*\{[^}]*"handle"\s*:\s*"([^"]+)"')


def extract_bv_identifiers(html: str) -> tuple[str | None, str | None]:
    """
    Extrait les deux identifiants BazaarVoice depuis le HTML d'une page produit Wacoal :
      1. handle Shopify   (ex: "back-appeal-shaping-body-briefer-praline")
      2. ID numérique     (ex: "9149775315160")

    Wacoal BV indexe ses produits par handle (use_external_ids=false dans la config
    pixel BazaarVoice). Le handle est donc à tester en premier.

    Retourne (handle, numeric_id) — l'un ou l'autre peut être None.
    """
    if not html:
        return None, None

    # Handle depuis mntn_product_data (le plus fiable)
    handle: str | None = None
    m_handle = _MNTN_HANDLE_RE.search(html)
    if m_handle:
        handle = m_handle.group(1).strip()

    # Fallback handle : depuis l'URL og:url ou canonical
    if not handle:
        m_og = re.search(r'og:url"[^>]+content="[^"]+/products/([^"]+)"', html)
        if m_og:
            handle = m_og.group(1).strip().rstrip("/")

    # ID numérique depuis data-bv-product-id
    numeric_id: str | None = None
    m_id = _BV_PRODUCT_ID_RE.search(html)
    if m_id:
        numeric_id = m_id.group(1)

    log.debug(
        "Wacoal BV identifiants extraits",
        handle=handle,
        numeric_id=numeric_id,
    )
    return handle, numeric_id


# ---------------------------------------------------------------------------
# 4. Note et avis — API BazaarVoice Statistics + Reviews
# ---------------------------------------------------------------------------

def fetch_bv_rating(
    product_id: str,
    delay_min: float = 1.0,
    delay_max: float = 3.0,
    headers: dict | None = None,
) -> tuple[float | None, int | None]:
    """
    Récupère la note moyenne et le nombre d'avis via l'API BazaarVoice Statistics.

    CORRECTIF v1.3 : cette fonction accepte maintenant directement un product_id
    (handle ou ID numérique). Elle est appelée depuis fetch_bv_rating_with_fallback()
    qui gère les tentatives multiples.

    Endpoint :
        GET https://api.bazaarvoice.com/data/statistics.json
            ?passKey=<BV_PASS_KEY>
            &productId=<product_id>
            &stats=Reviews

    Retourne (rating, review_count) ou (None, None) en cas d'échec ou si non trouvé.
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


def fetch_bv_rating_with_fallback(
    handle: str | None,
    numeric_id: str | None,
    delay_min: float = 1.0,
    delay_max: float = 3.0,
    headers: dict | None = None,
) -> tuple[float | None, int | None]:
    """
    Récupère la note BV avec stratégie multi-identifiant.

    Wacoal BV indexe par handle Shopify en priorité. Si le handle échoue
    (Results vide), on tente avec l'ID numérique.

    Args:
        handle     : handle Shopify (ex: "back-appeal-shaping-body-briefer-praline")
        numeric_id : ID Shopify numérique en string (ex: "9149775315160")

    Retourne (rating, review_count) ou (None, None) si aucun résultat.
    """
    # Tentative 1 : handle Shopify (identifiant BV privilégié pour Wacoal)
    if handle:
        rating, count = fetch_bv_rating(
            handle,
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers,
        )
        if rating is not None or count is not None:
            log.debug("BazaarVoice Stats trouvé via handle", handle=handle)
            return rating, count

    # Tentative 2 : ID numérique (fallback)
    if numeric_id and numeric_id != handle:
        rating, count = fetch_bv_rating(
            numeric_id,
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers,
        )
        if rating is not None or count is not None:
            log.debug("BazaarVoice Stats trouvé via numeric_id", numeric_id=numeric_id)
            return rating, count

    log.debug(
        "BazaarVoice Stats : aucun résultat",
        handle=handle,
        numeric_id=numeric_id,
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

    CORRECTIF v1.3 : product_id doit être le handle Shopify, pas l'ID numérique.
    Appelée depuis fetch_bv_reviews_with_fallback() qui gère les tentatives.

    Endpoint :
        GET https://api.bazaarvoice.com/data/reviews.json
            ?passKey=<BV_PASS_KEY>
            &ProductId=<product_id>
            &Limit=<limit>
            &Offset=0
            &Sort=SubmissionTime:desc

    Retourne [] en cas d'échec ou si aucun avis.
    """
    if not product_id:
        return []

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


def fetch_bv_reviews_with_fallback(
    handle: str | None,
    numeric_id: str | None,
    limit: int = 100,
    delay_min: float = 1.0,
    delay_max: float = 3.0,
    headers: dict | None = None,
) -> list[dict]:
    """
    Récupère les avis BV avec stratégie multi-identifiant.

    Tente d'abord avec le handle Shopify, puis avec l'ID numérique en fallback.

    Args:
        handle     : handle Shopify (ex: "back-appeal-shaping-body-briefer-praline")
        numeric_id : ID Shopify numérique en string (ex: "9149775315160")

    Retourne la liste des avis (peut être vide si aucun n'est trouvé).
    """
    # Tentative 1 : handle Shopify (identifiant BV privilégié pour Wacoal)
    if handle:
        reviews = fetch_bv_reviews(
            handle,
            limit=limit,
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers,
        )
        if reviews:
            log.debug(
                "BazaarVoice Reviews trouvés via handle",
                handle=handle,
                count=len(reviews),
            )
            return reviews

    # Tentative 2 : ID numérique (fallback)
    if numeric_id and numeric_id != handle:
        reviews = fetch_bv_reviews(
            numeric_id,
            limit=limit,
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers,
        )
        if reviews:
            log.debug(
                "BazaarVoice Reviews trouvés via numeric_id",
                numeric_id=numeric_id,
                count=len(reviews),
            )
            return reviews

    log.debug(
        "BazaarVoice Reviews : aucun résultat",
        handle=handle,
        numeric_id=numeric_id,
    )
    return []