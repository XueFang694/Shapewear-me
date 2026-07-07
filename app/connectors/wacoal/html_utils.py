"""
Fonctions d'extraction HTML spécifiques aux pages produit Wacoal America.

REFONTE v2.0 — Abandon de BazaarVoice, passage à Okendo
────────────────────────────────────────────────────────

PROBLÈME RACINE (v1.x) :
  L'API REST BazaarVoice exige un passKey délivré par Bazaarvoice directement
  aux partenaires du programme. La `storefront_api_key` présente dans le HTML
  (pixel Shopify id=2305851608) est une clé d'initialisation du widget JS
  côté navigateur — elle n'est PAS valide pour l'API REST. Toutes les clés
  testées (`caElc2g6...`, `8aa6f989...`) retournent ERROR_PARAM_INVALID_API_KEY
  car elles ne correspondent pas à un compte REST BazaarVoice.

ARCHITECTURE RÉELLE DE WACOAL :
  • Ratings inline : BazaarVoice widget JS  (lazy-loaded, inaccessible en scraping)
  • Ratings dans le HTML : `data-bv-product-id` → widget vide sans JS
  • Vrai moteur d'avis : **Okendo** (révélé par `okendoProduct.reviewCount`
    et `okendoProductReviewAverageValue` dans le JS de la page)
  • Rating + count disponibles : dans le JSON Shopify embarqué dans le HTML
    (`Shop._PRODUCT_JSON_`) via les métafields Okendo, ET directement dans
    le bloc `okendo: { rating, count }` de la configuration analytics

SOLUTION v2.0 :
  1. `extract_rating_from_html()` : lit la note et le nombre d'avis depuis
     les deux sources HTML statiques (JSON Shopify embarqué + balises meta).
     Zéro requête supplémentaire — les données sont déjà dans le HTML fetchée.

  2. `fetch_okendo_reviews()` : API Okendo publique, sans authentification.
     URL : https://api.okendo.io/v1/stores/<subscriber_id>/products/<product_gid>/reviews
     Le subscriber_id est le shopId Shopify : 80710533336 (stable, extrait
     une fois et stocké en constante).

  3. `extract_bv_identifiers()` et `extract_materials_from_wacoal_html()` :
     conservées à l'identique (non impactées).

  4. `extract_variant_availability_from_html()` et son helper :
     conservés à l'identique.

  5. Toutes les fonctions BazaarVoice (`fetch_bv_rating*`, `fetch_bv_reviews*`,
     `extract_bv_pass_key`) sont supprimées.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constantes Okendo
# ---------------------------------------------------------------------------

# Subscriber ID Okendo = shopId Shopify de Wacoal America (stable)
# Visible dans le HTML : shopId: 80710533336
# Format URL Okendo : /v1/stores/<shopId>/products/<product_gid>/reviews
_OKENDO_SUBSCRIBER_ID = "80710533336"

# L'ID produit Okendo est le GID Shopify sous la forme :
#   gid://shopify/Product/<numeric_id>
# encodé en base64 pour certains endpoints, ou passé tel quel.
# L'API publique Okendo accepte directement le GID Shopify.
_OKENDO_REVIEWS_BASE = "https://api.okendo.io/v1/stores"


# ---------------------------------------------------------------------------
# Patterns de fibres (inchangé)
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
# 1. Rating depuis le HTML statique (sans requête supplémentaire)
# ---------------------------------------------------------------------------

# Pattern pour extraire le JSON Shopify embarqué dans `Shop._PRODUCT_JSON_`
_PRODUCT_JSON_RE = re.compile(
    r"Shop\._PRODUCT_JSON_\s*=\s*(\{.*?\});\s*\n",
    re.DOTALL,
)

# Pattern alternatif : mntn_product_data (aussi présent dans la page)
_MNTN_PRODUCT_RE = re.compile(
    r"let\s+mntn_product_data\s*=\s*(\{.*?\});\s*\n",
    re.DOTALL,
)

# Pattern pour extraire le bloc okendo depuis la config analytics
# Format : "okendo":{"rating":<float>,"count":<int>}
_OKENDO_RATING_RE = re.compile(
    r'"okendo"\s*:\s*\{\s*"rating"\s*:\s*([0-9.]+|null)\s*,'
    r'\s*"count"\s*:\s*([0-9]+|null)\s*\}',
)

# Pattern pour les balises meta og:rating (certains thèmes les exposent)
_META_RATING_RE   = re.compile(r'<meta[^>]+name=["\']rating["\'][^>]+content=["\']([0-9.]+)["\']')
_META_REVIEWS_RE  = re.compile(r'<meta[^>]+name=["\']reviewCount["\'][^>]+content=["\']([0-9]+)["\']')


def extract_rating_from_html(html: str) -> tuple[float | None, int | None]:
    """
    Extrait la note moyenne et le nombre d'avis depuis le HTML statique.

    Sources consultées dans l'ordre de priorité :
      1. Bloc `okendo: { rating, count }` dans la config analytics Wacoal
      2. Balises <meta name="rating"> / <meta name="reviewCount">
      3. JSON-LD AggregateRating

    Retourne (None, None) si aucune donnée trouvée.
    Aucune requête HTTP supplémentaire n'est effectuée.
    """
    if not html:
        return None, None

    # ── 1. Bloc okendo dans la config analytics ───────────────────────────
    m = _OKENDO_RATING_RE.search(html)
    if m:
        try:
            raw_rating = m.group(1)
            raw_count  = m.group(2)
            rating = round(float(raw_rating), 1) if raw_rating != "null" else None
            count  = int(raw_count) if raw_count != "null" else None
            if rating is not None or count is not None:
                log.debug(
                    "Wacoal rating extrait (okendo bloc analytics)",
                    rating=rating,
                    count=count,
                )
                return rating, count
        except (ValueError, TypeError):
            pass

    # ── 2. Balises meta ───────────────────────────────────────────────────
    rating = count = None
    m_r = _META_RATING_RE.search(html)
    m_c = _META_REVIEWS_RE.search(html)
    if m_r:
        try:
            rating = round(float(m_r.group(1)), 1)
        except (ValueError, TypeError):
            pass
    if m_c:
        try:
            count = int(m_c.group(1))
        except (ValueError, TypeError):
            pass
    if rating is not None or count is not None:
        log.debug("Wacoal rating extrait (meta tags)", rating=rating, count=count)
        return rating, count

    # ── 3. JSON-LD AggregateRating ────────────────────────────────────────
    jsonld_re = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for m_jld in jsonld_re.finditer(html):
        try:
            data = json.loads(m_jld.group(1))
            agg = None
            if isinstance(data, dict):
                agg = data.get("aggregateRating")
            if agg:
                r = agg.get("ratingValue")
                c = agg.get("reviewCount")
                if r is not None:
                    log.debug(
                        "Wacoal rating extrait (JSON-LD AggregateRating)",
                        rating=r, count=c,
                    )
                    return (
                        round(float(r), 1),
                        int(c) if c is not None else None,
                    )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    log.debug("Wacoal rating : aucune donnée trouvée dans le HTML statique")
    return None, None


# ---------------------------------------------------------------------------
# 2. Avis texte — API Okendo publique (sans authentification)
# ---------------------------------------------------------------------------

def fetch_okendo_reviews(
    product_id: str,
    subscriber_id: str = _OKENDO_SUBSCRIBER_ID,
    limit: int = 100,
    delay_min: float = 1.0,
    delay_max: float = 3.0,
    headers: dict | None = None,
) -> list[dict]:
    """
    Récupère les avis texte via l'API publique Okendo.

    L'API Okendo est publique et ne nécessite pas d'authentification.
    Elle retourne les avis au format JSON paginé.

    URL : GET https://api.okendo.io/v1/stores/<subscriber_id>/products/
              gid%3A%2F%2Fshopify%2FProduct%2F<product_numeric_id>/reviews
              ?limit=<limit>&start=0&sort=date_desc

    Args:
        product_id    : ID numérique Shopify du produit (ex: "9149775315160")
                        OU GID complet (ex: "gid://shopify/Product/9149775315160")
        subscriber_id : shopId Shopify de Wacoal (défaut: 80710533336)
        limit         : nombre max d'avis par requête (max Okendo: 100)
        delay_min     : délai min entre requêtes
        delay_max     : délai max entre requêtes
        headers       : en-têtes HTTP supplémentaires

    Returns:
        Liste de dicts normalisés { rating, title, body, date, variant, author }
    """
    if not product_id:
        return []

    # Construire le GID Shopify si nécessaire
    if product_id.startswith("gid://"):
        product_gid = product_id
        numeric_id  = product_id.split("/")[-1]
    else:
        numeric_id  = str(product_id)
        product_gid = f"gid://shopify/Product/{numeric_id}"

    # Encoder le GID pour l'URL (/ → %2F, : → %3A)
    from urllib.parse import quote
    encoded_gid = quote(product_gid, safe="")

    url = f"{_OKENDO_REVIEWS_BASE}/{subscriber_id}/products/{encoded_gid}/reviews"

    reviews:  list[dict] = []
    start     = 0
    page_size = min(limit, 100)
    max_pages = (limit // page_size) + 1

    try:
        from app.scraping.http_client import HttpClient

        client = HttpClient(
            delay_min=delay_min,
            delay_max=delay_max,
            headers=headers or {},
        )

        for page in range(max_pages):
            try:
                response = client.get(
                    url,
                    params={
                        "limit": page_size,
                        "start": start,
                        "sort":  "date_desc",
                    },
                    timeout=20,
                )
            except Exception as exc:
                log.debug(
                    "Okendo reviews : erreur réseau",
                    product_id=numeric_id,
                    page=page,
                    error=str(exc),
                )
                break

            if response.status_code == 404:
                # Produit sans avis ou ID incorrect — silencieux
                log.debug(
                    "Okendo reviews : 404 (produit sans avis ou GID invalide)",
                    product_id=numeric_id,
                )
                break

            if response.status_code != 200:
                log.debug(
                    "Okendo reviews : réponse non-200",
                    product_id=numeric_id,
                    status=response.status_code,
                )
                break

            try:
                data = response.json()
            except Exception as exc:
                log.debug(
                    "Okendo reviews : JSON invalide",
                    product_id=numeric_id,
                    error=str(exc),
                )
                break

            # Format de réponse Okendo :
            # { "reviews": [...], "reviewAggregate": {...}, "hasMore": bool }
            raw_reviews = data.get("reviews", [])
            if not raw_reviews:
                break

            for r in raw_reviews:
                reviews.append(_normalize_okendo_review(r))

            # Pagination
            has_more = data.get("hasMore", False)
            if not has_more or len(reviews) >= limit:
                break

            start += page_size

    except Exception as exc:
        log.warning(
            "Okendo reviews : erreur inattendue",
            product_id=numeric_id,
            error=str(exc),
        )

    log.debug(
        "Okendo reviews collectés",
        product_id=numeric_id,
        total=len(reviews),
    )
    return reviews


def _normalize_okendo_review(r: dict) -> dict:
    """Normalise un avis Okendo vers le format interne du projet."""
    # Extraire la variante depuis les attributs produit
    variant_parts: list[str] = []
    for attr in r.get("reviewer", {}).get("verifiedBuyer", {}).get("orderLineItems", []):
        v = attr.get("variantTitle", "").strip()
        if v:
            variant_parts.append(v)
            break

    # Format alternatif : attributs dans reviewAttributes
    if not variant_parts:
        for attr in r.get("reviewAttributes", []):
            if attr.get("attributeType") in ("size", "color", "variant"):
                v = str(attr.get("value", "")).strip()
                if v:
                    variant_parts.append(v)

    reviewer = r.get("reviewer", {})
    author   = (
        reviewer.get("displayName")
        or reviewer.get("name")
        or reviewer.get("firstName", "")
    ).strip()

    date_raw = (
        r.get("dateCreated")
        or r.get("createdAt")
        or ""
    )
    # Tronquer à YYYY-MM-DD si ISO complet
    date = date_raw[:10] if date_raw else ""

    return {
        "id":      str(r.get("reviewId", r.get("id", ""))),
        "rating":  r.get("rating"),
        "title":   (r.get("headline") or r.get("title") or "").strip(),
        "body":    (r.get("body") or r.get("content") or "").strip(),
        "date":    date,
        "author":  author,
        "variant": " / ".join(variant_parts),
    }


# ---------------------------------------------------------------------------
# 3. Matières (inchangé)
# ---------------------------------------------------------------------------

def extract_materials_from_wacoal_html(html: str) -> dict:
    """
    Extrait la composition textile depuis le HTML d'une page produit Wacoal.

    Les données sont dans un span avec classe `metafield-single_line_text_field`
    précédé d'un libellé "Fabric content".

    Retourne {} si aucune composition n'est trouvée.
    """
    if not html:
        return {}

    raw_text: str | None = None

    # Pattern 1 : "Fabric content:" suivi du metafield
    m = re.search(
        r'[Ff]abric content[^<]{0,50}'
        r'metafield-single_line_text_field["\s>]+([^<]{5,400})</span>',
        html,
        re.DOTALL,
    )
    if m:
        raw_text = m.group(1).strip()

    # Pattern 2 : n'importe quel metafield avec des %
    if not raw_text:
        candidates = re.findall(
            r'metafield-single_line_text_field["\s>]+([^<]{5,400})</span>',
            html,
        )
        for c in candidates:
            if "%" in c and any(
                f in c.lower()
                for f in ("nylon", "spandex", "cotton", "polyester", "elastane")
            ):
                raw_text = c.strip()
                break

    if not raw_text:
        return {}

    result: dict = {"material_raw": raw_text[:500]}

    sections = [s.strip() for s in re.split(r";", raw_text) if s.strip()]

    main_parts:   list[str] = []
    lining_parts: list[str] = []

    for section in sections:
        low = section.lower()
        if any(kw in low for kw in _LINING_KEYWORDS):
            lining_parts.append(section)
        elif re.search(r"\d+\s*%", section):
            main_parts.append(section)
        elif section:
            main_parts.append(section)

    if main_parts:
        result["material_main"] = "; ".join(main_parts)[:255]
    if lining_parts:
        result["material_lining"] = "; ".join(lining_parts)[:255]

    comp_text   = raw_text.lower()
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
# 4. Disponibilité par variante depuis le HTML (inchangé)
# ---------------------------------------------------------------------------

def extract_variant_availability_from_html(html: str) -> dict[str, bool]:
    """
    Extrait un mapping {sku: available} depuis le JSON Shopify embarqué.

    Cherche dans `mntn_product_data` ou `Shop._PRODUCT_JSON_`.
    Retourne {} si aucune donnée n'est trouvée.
    """
    if not html:
        return {}

    variants_data: list[dict] = []

    # Source 1 : mntn_product_data (Mountain)
    m = re.search(r"mntn_product_data\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            variants_data = d.get("variants", [])
        except (json.JSONDecodeError, ValueError):
            pass

    # Source 2 : Shop._PRODUCT_JSON_
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
        sku       = v.get("sku", "")
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
# 5. Identifiants BazaarVoice (conservé pour compatibilité, non utilisé)
# ---------------------------------------------------------------------------

_BV_PRODUCT_ID_RE = re.compile(r'data-bv-product-id="(\d+)"')
_MNTN_HANDLE_RE   = re.compile(r'mntn_product_data\s*=\s*\{[^}]*"handle"\s*:\s*"([^"]+)"')


def extract_bv_identifiers(html: str) -> tuple[str | None, str | None]:
    """
    Extrait handle Shopify et ID numérique depuis le HTML.

    Conservé pour compatibilité ascendante mais non utilisé pour les avis
    depuis la v2.0 (BV REST API abandonnée).

    Returns:
        (handle, numeric_id)
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

    # Fallback : extraire l'ID numérique depuis le handle ou le JSON embarqué
    if not numeric_id:
        m_rid = re.search(r'"rid"\s*:\s*(\d+)', html)
        if m_rid:
            numeric_id = m_rid.group(1)

    log.debug(
        "Wacoal identifiants extraits",
        handle=handle,
        numeric_id=numeric_id,
    )
    return handle, numeric_id