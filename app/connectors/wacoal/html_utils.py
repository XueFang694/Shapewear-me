"""
Fonctions d'extraction HTML spécifiques aux pages produit Wacoal America.

CORRECTIF PASSKEY v1.4
───────────────────────
PROBLÈME (v1.3) : Le passKey BazaarVoice `caElc2g6VBb1LfIMOqRFWr0S0QGKuoiiu61f4RAlnKH7k`
codé en dur était invalide (ERROR_PARAM_INVALID_API_KEY systématique).

CAUSE : Le vrai passKey est la `storefront_api_key` injectée dans chaque page produit
Wacoal par le pixel Shopify BazaarVoice (id 2305851608) dans le bloc
`webPixelsConfigList`. Sa valeur actuelle est `8aa6f9892dc1113e84be4fe1f3d29c49`.

SOLUTION :
  1. `extract_bv_pass_key(html)` extrait dynamiquement la clé depuis le HTML de
     la page produit → résistant aux rotations de clé par Wacoal.
  2. `_BV_PASS_KEY` est mis à jour comme fallback statique avec la vraie valeur.
  3. `fetch_bv_rating()` et `fetch_bv_reviews()` acceptent un paramètre `pass_key`
     optionnel ; les fonctions `_with_fallback` le transmettent depuis le HTML.
  4. `fetch_bv_rating_with_fallback()` et `fetch_bv_reviews_with_fallback()` ont
     un nouveau paramètre `pass_key` qui, s'il est fourni (extrait du HTML), est
     préféré à la constante statique.

Toutes les autres fonctionnalités sont inchangées.
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
# CORRECTIF v1.4 : valeur corrigée à partir de storefront_api_key extraite
# du pixel Shopify BazaarVoice (webPixelsConfigList id=2305851608) présent
# dans chaque page produit Wacoal.
# L'ancienne valeur `caElc2g6VBb1LfIMOqRFWr0S0QGKuoiiu61f4RAlnKH7k` était
# invalide et provoquait ERROR_PARAM_INVALID_API_KEY sur toutes les requêtes.
_BV_PASS_KEY = "8aa6f9892dc1113e84be4fe1f3d29c49"

_BV_STATS_URL   = "https://api.bazaarvoice.com/data/statistics.json"
_BV_REVIEWS_URL = "https://api.bazaarvoice.com/data/reviews.json"

_BV_ID_TYPE = "handle"

# ---------------------------------------------------------------------------
# Pattern d'extraction du passKey depuis le HTML
# ---------------------------------------------------------------------------

# Le passKey est dans la configuration du pixel BazaarVoice Shopify :
#   "id":"2305851608","configuration":"{...\"storefront_api_key\":\"<KEY>\"...}"
# Cette regex extrait la valeur directement depuis le JSON de configuration.
_BV_PASSKEY_RE = re.compile(
    r'"id"\s*:\s*"2305851608"[^}]*?"configuration"\s*:\s*"[^"]*?'
    r'storefront_api_key\\*":\s*\\*"([a-f0-9]{32})',
    re.DOTALL,
)

# Fallback : extraction directe depuis la chaîne storefront_api_key
_BV_PASSKEY_FALLBACK_RE = re.compile(
    r'storefront_api_key\\*["\':\s]+\\*["\']([a-f0-9]{32})'
)


def extract_bv_pass_key(html: str) -> str | None:
    """
    Extrait dynamiquement le passKey BazaarVoice depuis le HTML d'une page produit Wacoal.

    Le passKey est la `storefront_api_key` injectée par le pixel Shopify BazaarVoice
    (id 2305851608) dans le bloc `webPixelsConfigList`. Cette extraction garantit
    que la clé reste valide même si Wacoal la fait tourner.

    Retourne None si la clé n'est pas trouvée (le fallback statique sera utilisé).

    Exemple de structure HTML ciblée :
        {"id":"2305851608","configuration":"{...\"storefront_api_key\":\"8aa6f...\"}"}
    """
    if not html:
        return None

    # Tentative 1 : extraction ciblée via l'id du pixel BV
    m = _BV_PASSKEY_RE.search(html)
    if m:
        key = m.group(1)
        log.debug("PassKey BV extrait du pixel 2305851608", key=key[:8] + "…")
        return key

    # Tentative 2 : extraction directe de storefront_api_key (toute occurrence)
    m2 = _BV_PASSKEY_FALLBACK_RE.search(html)
    if m2:
        key = m2.group(1)
        log.debug("PassKey BV extrait (fallback storefront_api_key)", key=key[:8] + "…")
        return key

    log.debug("PassKey BV non trouvé dans le HTML, utilisation du fallback statique")
    return None


# ---------------------------------------------------------------------------
# Patterns de fibres
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
    (Inchangé par rapport à v1.3)
    """
    if not html:
        return {}

    raw_text: str | None = None

    m = re.search(
        r'[Ff]abric content[^<]{0,50}'
        r'metafield-single_line_text_field["\s>]+([^<]{5,400})</span>',
        html,
        re.DOTALL,
    )
    if m:
        raw_text = m.group(1).strip()

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
# 2. Disponibilité par variante
# ---------------------------------------------------------------------------

def extract_variant_availability_from_html(html: str) -> dict[str, bool]:
    """
    Extrait un mapping {sku: available} depuis le JSON Shopify embarqué.
    (Inchangé par rapport à v1.3)
    """
    if not html:
        return {}

    variants_data: list[dict] = []

    m = re.search(r"mntn_product_data\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            variants_data = d.get("variants", [])
        except (json.JSONDecodeError, ValueError):
            pass

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
    Applique le mapping {sku: available} aux variantes parsées depuis l'API JSON.
    (Inchangé par rapport à v1.3)
    """
    if not sku_availability:
        return variants

    updated = []
    for v in variants:
        sku = v.get("sku", "")
        if sku and sku in sku_availability:
            v = dict(v)
            v["available"] = sku_availability[sku]
        updated.append(v)
    return updated


# ---------------------------------------------------------------------------
# 3. Extraction des identifiants BazaarVoice
# ---------------------------------------------------------------------------

_BV_PRODUCT_ID_RE = re.compile(r'data-bv-product-id="(\d+)"')
_MNTN_HANDLE_RE   = re.compile(r'mntn_product_data\s*=\s*\{[^}]*"handle"\s*:\s*"([^"]+)"')


def extract_bv_identifiers(html: str) -> tuple[str | None, str | None]:
    """
    Extrait handle Shopify et ID numérique BazaarVoice depuis le HTML.
    (Inchangé par rapport à v1.3)
    """
    if not html:
        return None, None

    handle: str | None = None
    m_handle = _MNTN_HANDLE_RE.search(html)
    if m_handle:
        handle = m_handle.group(1).strip()

    if not handle:
        m_og = re.search(r'og:url"[^>]+content="[^"]+/products/([^"]+)"', html)
        if m_og:
            handle = m_og.group(1).strip().rstrip("/")

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
# 4. Note et avis — API BazaarVoice
# ---------------------------------------------------------------------------

def fetch_bv_rating(
    product_id: str,
    delay_min: float = 1.0,
    delay_max: float = 3.0,
    headers: dict | None = None,
    pass_key: str | None = None,
) -> tuple[float | None, int | None]:
    """
    Récupère la note moyenne et le nombre d'avis via l'API BazaarVoice Statistics.

    CORRECTIF v1.4 : paramètre `pass_key` ajouté. Si fourni (extrait dynamiquement
    du HTML), il est utilisé à la place de la constante `_BV_PASS_KEY`.
    """
    if not product_id:
        return None, None

    key = pass_key or _BV_PASS_KEY

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
                "passKey":   key,
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

        # Détecter une clé invalide sans déclencher d'exception
        errors = data.get("Errors", [])
        if errors:
            for err in errors:
                if "invalid" in (err.get("Message") or "").lower():
                    log.warning(
                        "BazaarVoice passKey invalide",
                        product_id=product_id,
                        key_prefix=key[:8],
                        error=err.get("Message"),
                    )
                    return None, None

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
    pass_key: str | None = None,
) -> tuple[float | None, int | None]:
    """
    Récupère la note BV avec stratégie multi-identifiant.

    CORRECTIF v1.4 : paramètre `pass_key` propagé à `fetch_bv_rating()`.
    Utilise le passKey extrait dynamiquement du HTML si fourni.
    """
    # Tentative 1 : handle Shopify
    if handle:
        rating, count = fetch_bv_rating(
            handle,
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers,
            pass_key=pass_key,
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
            pass_key=pass_key,
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
    pass_key: str | None = None,
) -> list[dict]:
    """
    Récupère les avis texte via l'API BazaarVoice Reviews.

    CORRECTIF v1.4 : paramètre `pass_key` ajouté. Si fourni (extrait dynamiquement
    du HTML), il est utilisé à la place de la constante `_BV_PASS_KEY`.
    """
    if not product_id:
        return []

    key = pass_key or _BV_PASS_KEY

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
                "passKey":   key,
                "ProductId": product_id,
                "Limit":     str(min(limit, 100)),
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

        # Détecter une clé invalide
        errors = data.get("Errors", [])
        if errors:
            for err in errors:
                if "invalid" in (err.get("Message") or "").lower():
                    log.warning(
                        "BazaarVoice passKey invalide (reviews)",
                        product_id=product_id,
                        key_prefix=key[:8],
                        error=err.get("Message"),
                    )
                    return []

        raw_reviews = data.get("Results", [])

        reviews: list[dict] = []
        for r in raw_reviews:
            rating   = r.get("Rating")
            title    = (r.get("Title") or "").strip()
            text     = (r.get("ReviewText") or "").strip()
            date_raw = r.get("SubmissionTime", "")[:10]
            author   = (r.get("UserNickname") or "").strip()

            ctx_data = r.get("ContextDataValues", {})
            variant_parts: list[str] = []
            for k in ("Size", "Color", "Shade"):
                val = ctx_data.get(k, {})
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
    pass_key: str | None = None,
) -> list[dict]:
    """
    Récupère les avis BV avec stratégie multi-identifiant.

    CORRECTIF v1.4 : paramètre `pass_key` propagé à `fetch_bv_reviews()`.
    Utilise le passKey extrait dynamiquement du HTML si fourni.
    """
    # Tentative 1 : handle Shopify
    if handle:
        reviews = fetch_bv_reviews(
            handle,
            limit=limit,
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers,
            pass_key=pass_key,
        )
        if reviews:
            log.debug(
                "BazaarVoice Reviews trouvés via handle",
                handle=handle,
                count=len(reviews),
            )
            return reviews

    # Tentative 2 : ID numérique
    if numeric_id and numeric_id != handle:
        reviews = fetch_bv_reviews(
            numeric_id,
            limit=limit,
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers,
            pass_key=pass_key,
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