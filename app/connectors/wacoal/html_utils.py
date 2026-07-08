"""
Fonctions d'extraction HTML spécifiques aux pages produit Wacoal America.

v3.0 — Abandon définitif de l'API REST BazaarVoice, passage à l'endpoint SEO BV
──────────────────────────────────────────────────────────────────────────────────

DIAGNOSTIC
──────────
• BazaarVoice est le vrai moteur d'avis sur wacoal-america.com.
• Le script bv.js est chargé via proxy Shopify :
    //cdn.shopify.com/proxy/<hash>/apps.bazaarvoice.com/deployments/wacoal/main_site/production/en_US/bv.js
• Le passKey BV REST API EST UNIQUEMENT dans bv.js — jamais dans le HTML statique.
  La `storefront_api_key` extractible du HTML est une clé d'initialisation du
  widget JS côté navigateur et est INVALIDE pour l'API REST BV.
• Okendo, Yotpo et Loox sont référencés dans le code d'analytics de Klaviyo mais
  leurs variables sont toutes null — ils ne sont pas actifs sur ce store.

SOLUTION v3.0
─────────────
1. Rating + count    : extraits depuis le HTML statique sans requête supplémentaire.
   Sources (par priorité) : bloc `okendo:` dans la config analytics, balises meta,
   JSON-LD AggregateRating.

2. Avis texte        : endpoint SEO BazaarVoice public (aucun passKey requis).
   URL : https://seo.bazaarvoice.com/wacoal-1038-en_US/product/<product_id>/reviews.djs
   Le suffixe `wacoal-1038-en_US` est le `seo_key` BV extrait du déploiement.
   Format de réponse : JSONP enveloppe → on parse le JSON brut intégré.
   Si le SEO endpoint échoue, la liste d'avis reste vide (dégradation gracieuse).

3. Matières, disponibilité variante : inchangés depuis v2.0.

FONCTIONS EXPORTÉES
───────────────────
  extract_rating_from_html(html)              → (rating, count)
  fetch_bv_seo_reviews(product_id, ...)       → list[dict]
  extract_bv_identifiers(html)                → (handle, numeric_id)
  extract_materials_from_wacoal_html(html)    → dict
  extract_variant_availability_from_html(html)→ dict[str, bool]
  apply_html_availability_to_variants(variants, sku_map) → list[dict]
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constantes BazaarVoice SEO
# ---------------------------------------------------------------------------

# Clé SEO BV extraite du déploiement wacoal / main_site / production / en_US.
# Format : <client>-<numeric_id>-<locale>
# Visible dans la réponse du bv.js ou sur le portail BV.
# Cette valeur est stable tant que Wacoal ne change pas de déploiement BV.
_BV_SEO_KEY = "wacoal-1038-en_US"

# Endpoint SEO BazaarVoice — public, sans passKey
_BV_SEO_BASE = "https://seo.bazaarvoice.com"

# ---------------------------------------------------------------------------
# Patterns extraction matières (inchangé)
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
# Patterns extraction rating (inchangé depuis v2.0)
# ---------------------------------------------------------------------------

_OKENDO_RATING_RE = re.compile(
    r'"okendo"\s*:\s*\{\s*"rating"\s*:\s*([0-9.]+|null)\s*,'
    r'\s*"count"\s*:\s*([0-9]+|null)\s*\}',
)
_META_RATING_RE  = re.compile(r'<meta[^>]+name=["\']rating["\'][^>]+content=["\']([0-9.]+)["\']')
_META_REVIEWS_RE = re.compile(r'<meta[^>]+name=["\']reviewCount["\'][^>]+content=["\']([0-9]+)["\']')
_JSONLD_RE       = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 1. Rating depuis le HTML statique
# ---------------------------------------------------------------------------

def extract_rating_from_html(html: str) -> tuple[float | None, int | None]:
    """
    Extrait la note moyenne et le nombre d'avis depuis le HTML statique.

    Sources consultées dans l'ordre de priorité :
      1. Bloc `okendo: { rating, count }` dans la config analytics Klaviyo
      2. Balises <meta name="rating"> / <meta name="reviewCount">
      3. JSON-LD AggregateRating

    Aucune requête HTTP supplémentaire n'est effectuée.
    Retourne (None, None) si aucune donnée trouvée.
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
                log.debug("Wacoal rating extrait (analytics)", rating=rating, count=count)
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
    for m_jld in _JSONLD_RE.finditer(html):
        try:
            data = json.loads(m_jld.group(1))
            agg  = data.get("aggregateRating") if isinstance(data, dict) else None
            if agg:
                r = agg.get("ratingValue")
                c = agg.get("reviewCount")
                if r is not None:
                    log.debug("Wacoal rating extrait (JSON-LD)", rating=r, count=c)
                    return (
                        round(float(r), 1),
                        int(c) if c is not None else None,
                    )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    log.debug("Wacoal rating : aucune donnée dans le HTML statique")
    return None, None


# ---------------------------------------------------------------------------
# 2. Avis texte — endpoint SEO BazaarVoice (sans passKey)
# ---------------------------------------------------------------------------

def fetch_bv_seo_reviews(
    product_id: str,
    seo_key: str = _BV_SEO_KEY,
    limit: int = 100,
    delay_min: float = 1.0,
    delay_max: float = 3.0,
    headers: dict | None = None,
) -> list[dict]:
    """
    Récupère les avis depuis l'endpoint SEO public de BazaarVoice.

    L'endpoint SEO BV est destiné à l'indexation par les moteurs de recherche
    et ne nécessite pas de passKey. Il retourne une réponse JSONP.

    URL : GET https://seo.bazaarvoice.com/<seo_key>/product/<product_id>/reviews.djs
              ?format=embeddedhtml&rating=0&limit=<limit>

    La réponse est un JSONP du type :
        bvCallback({"jsonFeed": { ... "reviews": [...] ... }})
    On extrait le JSON brut depuis l'enveloppe JSONP.

    Args:
        product_id  : ID numérique Shopify du produit (ex: "9149775315160")
        seo_key     : clé de déploiement BV SEO (format: client-id-locale)
        limit       : nombre max d'avis à récupérer
        delay_min   : délai minimum entre requêtes (secondes)
        delay_max   : délai maximum entre requêtes (secondes)
        headers     : en-têtes HTTP supplémentaires

    Returns:
        Liste de dicts normalisés { rating, title, body, date, author, variant }
        Liste vide si l'endpoint est inaccessible (dégradation gracieuse).
    """
    if not product_id:
        return []

    from app.scraping.http_client import HttpClient

    client = HttpClient(
        delay_min=delay_min,
        delay_max=delay_max,
        headers=headers or {},
    )

    url = f"{_BV_SEO_BASE}/{seo_key}/product/{product_id}/reviews.djs"
    params = {
        "format": "embeddedhtml",
        "rating": "0",          # tous les avis quelle que soit la note
        "limit":  str(limit),
        "offset": "0",
        "sort":   "submissionTime:desc",
    }

    reviews: list[dict] = []

    try:
        response = client.get(url, params=params, timeout=20)
    except Exception as exc:
        log.warning(
            "Wacoal BV SEO : erreur réseau",
            product_id=product_id,
            error=str(exc),
        )
        return []

    if response.status_code == 404:
        log.debug("Wacoal BV SEO : produit sans avis ou ID invalide", product_id=product_id)
        return []

    if response.status_code != 200:
        log.warning(
            "Wacoal BV SEO : réponse non-200",
            product_id=product_id,
            status=response.status_code,
        )
        return []

    raw = response.text
    if not raw.strip():
        return []

    # ── Extraire le JSON depuis l'enveloppe JSONP ─────────────────────────
    # Format : bvCallback({...}) ou var bvData = {...};
    json_data = _extract_json_from_bv_response(raw, product_id)
    if json_data is None:
        return []

    # ── Naviguer dans la structure BV SEO ─────────────────────────────────
    # Plusieurs structures possibles selon la version BV
    raw_reviews = (
        json_data.get("reviews")
        or json_data.get("Results")
        or _deep_get(json_data, "jsonFeed", "reviews")
        or _deep_get(json_data, "jsonFeed", "Results")
        or []
    )

    if not isinstance(raw_reviews, list):
        log.debug(
            "Wacoal BV SEO : structure de réponse inattendue",
            product_id=product_id,
            keys=list(json_data.keys())[:10],
        )
        return []

    for r in raw_reviews:
        normalized = _normalize_bv_seo_review(r)
        if normalized:
            reviews.append(normalized)

    log.info(
        "Wacoal BV SEO reviews collectés",
        product_id=product_id,
        total=len(reviews),
    )
    return reviews


def _extract_json_from_bv_response(raw: str, product_id: str) -> dict | None:
    """
    Extrait le JSON depuis différents formats de réponse BV SEO.

    Formats possibles :
      1. JSONP : bvCallback({...})
      2. JS var : var bvData = {...};
      3. JSON pur : {...}
    """
    # Format 1 : JSONP — bvCallback({...}) ou BVRRSourceID({...})
    jsonp_match = re.search(r'[a-zA-Z_$][a-zA-Z0-9_$]*\s*\(\s*(\{.*\})\s*\)\s*;?\s*$', raw, re.DOTALL)
    if jsonp_match:
        try:
            return json.loads(jsonp_match.group(1))
        except json.JSONDecodeError:
            pass

    # Format 2 : var bvData = {...};
    var_match = re.search(r'var\s+\w+\s*=\s*(\{.*\})\s*;', raw, re.DOTALL)
    if var_match:
        try:
            return json.loads(var_match.group(1))
        except json.JSONDecodeError:
            pass

    # Format 3 : JSON pur
    stripped = raw.strip()
    if stripped.startswith('{'):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # Format 4 : réponse HTML avec du JSON embarqué
    json_embedded = re.search(r'\{["\']Results["\']:\s*\[', raw)
    if json_embedded:
        try:
            start = json_embedded.start()
            return json.loads(raw[start:])
        except json.JSONDecodeError:
            pass

    log.debug(
        "Wacoal BV SEO : impossible de parser la réponse",
        product_id=product_id,
        preview=raw[:200],
    )
    return None


def _normalize_bv_seo_review(r: dict) -> dict | None:
    """Normalise un avis depuis la réponse BV SEO vers le format interne."""
    if not isinstance(r, dict):
        return None

    # Rating (peut être int ou float selon la version BV)
    rating = r.get("rating") or r.get("Rating") or r.get("BVRRRating")
    if rating is not None:
        try:
            rating = float(rating)
        except (ValueError, TypeError):
            rating = None

    # Titre
    title = (
        r.get("title") or r.get("Title")
        or r.get("BVRRReviewTitle") or ""
    ).strip()

    # Corps
    body = (
        r.get("ReviewText") or r.get("reviewText")
        or r.get("body") or r.get("Body")
        or r.get("BVRRReviewText") or ""
    ).strip()

    # Date
    date_raw = (
        r.get("SubmissionTime") or r.get("submissionTime")
        or r.get("date") or r.get("Date") or ""
    )
    date = str(date_raw)[:10] if date_raw else ""

    # Auteur
    author = (
        r.get("UserNickname") or r.get("userNickname")
        or r.get("AuthorId") or r.get("author") or ""
    ).strip()

    # Variante (ContextDataValues chez BV)
    variant = ""
    cdv = r.get("ContextDataValues") or r.get("contextDataValues") or {}
    if isinstance(cdv, dict):
        size   = cdv.get("Size",  {}).get("Value", "")
        color  = cdv.get("Color", {}).get("Value", "")
        parts  = [p for p in [size, color] if p]
        variant = " / ".join(parts)

    # Ignorer les avis sans contenu
    if not title and not body:
        return None

    return {
        "id":      str(r.get("Id") or r.get("id") or ""),
        "rating":  rating,
        "title":   title,
        "body":    body,
        "date":    date,
        "author":  author,
        "variant": variant,
    }


def _deep_get(d: dict, *keys: str) -> Any:
    """Accès sécurisé à un chemin de clés imbriquées."""
    current = d
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
    return current


# ---------------------------------------------------------------------------
# 3. Identifiants BazaarVoice depuis le HTML (inchangé)
# ---------------------------------------------------------------------------

_BV_PRODUCT_ID_RE = re.compile(r'data-bv-product-id=["\'](\d+)["\']')
_MNTN_HANDLE_RE   = re.compile(r'mntn_product_data\s*=\s*\{[^}]*"handle"\s*:\s*"([^"]+)"')


def extract_bv_identifiers(html: str) -> tuple[str | None, str | None]:
    """
    Extrait le handle Shopify et l'ID numérique du produit depuis le HTML.

    Le `data-bv-product-id` est l'ID numérique Shopify, utilisé comme
    `product_id` pour l'endpoint SEO BazaarVoice.

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

    # Fallback handle depuis canonical
    if not handle:
        m_can = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href="[^"]+/products/([^"?]+)"', html)
        if m_can:
            handle = m_can.group(1).strip().rstrip("/")

    numeric_id: str | None = None
    m_id = _BV_PRODUCT_ID_RE.search(html)
    if m_id:
        numeric_id = m_id.group(1)

    # Fallback numeric_id depuis JSON
    if not numeric_id:
        m_rid = re.search(r'"rid"\s*:\s*(\d+)', html)
        if m_rid:
            numeric_id = m_rid.group(1)

    log.debug("Wacoal BV identifiants", handle=handle, numeric_id=numeric_id)
    return handle, numeric_id


# ---------------------------------------------------------------------------
# 4. Matières (inchangé depuis v2.0)
# ---------------------------------------------------------------------------

def extract_materials_from_wacoal_html(html: str) -> dict:
    """
    Extrait la composition textile depuis le HTML d'une page produit Wacoal.

    Les données sont dans un span avec classe `metafield-single_line_text_field`
    précédé d'un libellé "Fabric content".
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

    main_parts: list[str]   = []
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
# 5. Disponibilité par variante depuis le HTML (inchangé depuis v2.0)
# ---------------------------------------------------------------------------

def extract_variant_availability_from_html(html: str) -> dict[str, bool]:
    """
    Extrait un mapping {sku: available} depuis le JSON Shopify embarqué.
    Cherche dans `mntn_product_data` ou `Shop._PRODUCT_JSON_`.
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
    """Applique le mapping {sku: available} aux variantes parsées depuis l'API JSON."""
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