"""
Market — Gestion des marchés géographiques.

Chaque marché définit :
  - slug            : identifiant court (us, fr, it, es, zh, ...)
  - name            : nom lisible
  - locale          : code BCP-47 (en-US, fr-FR, it-IT, es-ES, zh-CN, ...)
  - currency        : code ISO 4217 (USD, EUR, GBP, JPY, ...)
  - currency_symbol : symbole affiché ($, €, £, ¥, ...)
  - currency_format : "prefix" | "suffix" (€ vient après en fr, avant en us)
  - decimal_sep     : séparateur décimal ("." ou ",")
  - thousands_sep   : séparateur de milliers ("," ou "." ou " ")
  - language_codes  : liste de codes Accept-Language prioritaires
  - date_format     : format strftime de la date courte

Usage :
    from app.core.market import get_market, MarketConfig

    market = get_market("fr")          # MarketConfig pour la France
    market = get_market()              # Marché actif (depuis settings.MARKET)

    price_str = market.format_price(68.00)  # "$68.00" ou "68,00 €"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketConfig:
    """Décrit un marché géographique de manière immuable."""

    slug: str
    name: str
    locale: str
    currency: str
    currency_symbol: str
    currency_format: str          # "prefix" | "suffix"
    decimal_sep: str
    thousands_sep: str
    language_codes: list[str]
    date_format: str              # strftime pattern

    # Optionnel : surcharge du User-Agent Accept-Language header
    accept_language: str = ""

    def __post_init__(self) -> None:
        if not self.accept_language:
            # Construire depuis language_codes si non fourni (frozen → object.__setattr__)
            al = ",".join(
                f"{code};q={round(1.0 - i * 0.1, 1)}"
                if i > 0 else code
                for i, code in enumerate(self.language_codes)
            )
            object.__setattr__(self, "accept_language", al)

    def format_price(self, amount: float | None, show_currency: bool = True) -> str:
        """Formate un montant selon les conventions du marché."""
        if amount is None:
            return "—"
        # Formatage décimal
        int_part = int(amount)
        dec_part = round((amount - int_part) * 100)
        # Séparateur de milliers
        int_str = ""
        s = str(int_part)
        for i, ch in enumerate(reversed(s)):
            if i > 0 and i % 3 == 0:
                int_str = self.thousands_sep + int_str
            int_str = ch + int_str
        formatted = f"{int_str}{self.decimal_sep}{dec_part:02d}"
        if not show_currency:
            return formatted
        if self.currency_format == "prefix":
            return f"{self.currency_symbol}{formatted}"
        return f"{formatted} {self.currency_symbol}"

    def format_date(self, dt) -> str:
        """Formate une datetime selon le marché."""
        if dt is None:
            return "—"
        return dt.strftime(self.date_format)

    def get_http_headers(self, extra: dict | None = None) -> dict:
        """Retourne les en-têtes HTTP adaptés au marché."""
        headers = {"Accept-Language": self.accept_language}
        if extra:
            headers.update(extra)
        return headers

    def __repr__(self) -> str:
        return f"MarketConfig(slug={self.slug!r}, locale={self.locale!r}, currency={self.currency!r})"


# ---------------------------------------------------------------------------
# Catalogue des marchés supportés
# ---------------------------------------------------------------------------

_MARKETS: dict[str, MarketConfig] = {

    # ── Amériques ──────────────────────────────────────────────────────────
    "us": MarketConfig(
        slug="us", name="United States",
        locale="en-US", currency="USD", currency_symbol="$",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["en-US", "en"],
        date_format="%m/%d/%Y",
    ),
    "ca": MarketConfig(
        slug="ca", name="Canada",
        locale="en-CA", currency="CAD", currency_symbol="CA$",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["en-CA", "en", "fr-CA"],
        date_format="%Y-%m-%d",
    ),
    "mx": MarketConfig(
        slug="mx", name="Mexico",
        locale="es-MX", currency="MXN", currency_symbol="$",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["es-MX", "es"],
        date_format="%d/%m/%Y",
    ),
    "br": MarketConfig(
        slug="br", name="Brazil",
        locale="pt-BR", currency="BRL", currency_symbol="R$",
        currency_format="prefix", decimal_sep=",", thousands_sep=".",
        language_codes=["pt-BR", "pt"],
        date_format="%d/%m/%Y",
    ),

    # ── Europe ────────────────────────────────────────────────────────────
    "fr": MarketConfig(
        slug="fr", name="France",
        locale="fr-FR", currency="EUR", currency_symbol="€",
        currency_format="suffix", decimal_sep=",", thousands_sep=" ",
        language_codes=["fr-FR", "fr"],
        date_format="%d/%m/%Y",
    ),
    "de": MarketConfig(
        slug="de", name="Germany",
        locale="de-DE", currency="EUR", currency_symbol="€",
        currency_format="suffix", decimal_sep=",", thousands_sep=".",
        language_codes=["de-DE", "de"],
        date_format="%d.%m.%Y",
    ),
    "it": MarketConfig(
        slug="it", name="Italy",
        locale="it-IT", currency="EUR", currency_symbol="€",
        currency_format="suffix", decimal_sep=",", thousands_sep=".",
        language_codes=["it-IT", "it"],
        date_format="%d/%m/%Y",
    ),
    "es": MarketConfig(
        slug="es", name="Spain",
        locale="es-ES", currency="EUR", currency_symbol="€",
        currency_format="suffix", decimal_sep=",", thousands_sep=".",
        language_codes=["es-ES", "es"],
        date_format="%d/%m/%Y",
    ),
    "gb": MarketConfig(
        slug="gb", name="United Kingdom",
        locale="en-GB", currency="GBP", currency_symbol="£",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["en-GB", "en"],
        date_format="%d/%m/%Y",
    ),
    "nl": MarketConfig(
        slug="nl", name="Netherlands",
        locale="nl-NL", currency="EUR", currency_symbol="€",
        currency_format="suffix", decimal_sep=",", thousands_sep=".",
        language_codes=["nl-NL", "nl"],
        date_format="%d-%m-%Y",
    ),
    "be": MarketConfig(
        slug="be", name="Belgium",
        locale="fr-BE", currency="EUR", currency_symbol="€",
        currency_format="suffix", decimal_sep=",", thousands_sep=".",
        language_codes=["fr-BE", "fr", "nl-BE"],
        date_format="%d/%m/%Y",
    ),
    "ch": MarketConfig(
        slug="ch", name="Switzerland",
        locale="fr-CH", currency="CHF", currency_symbol="CHF",
        currency_format="prefix", decimal_sep=".", thousands_sep="'",
        language_codes=["fr-CH", "de-CH", "it-CH"],
        date_format="%d.%m.%Y",
    ),
    "pt": MarketConfig(
        slug="pt", name="Portugal",
        locale="pt-PT", currency="EUR", currency_symbol="€",
        currency_format="suffix", decimal_sep=",", thousands_sep=".",
        language_codes=["pt-PT", "pt"],
        date_format="%d/%m/%Y",
    ),
    "pl": MarketConfig(
        slug="pl", name="Poland",
        locale="pl-PL", currency="PLN", currency_symbol="zł",
        currency_format="suffix", decimal_sep=",", thousands_sep=" ",
        language_codes=["pl-PL", "pl"],
        date_format="%d.%m.%Y",
    ),
    "se": MarketConfig(
        slug="se", name="Sweden",
        locale="sv-SE", currency="SEK", currency_symbol="kr",
        currency_format="suffix", decimal_sep=",", thousands_sep=" ",
        language_codes=["sv-SE", "sv"],
        date_format="%Y-%m-%d",
    ),
    "no": MarketConfig(
        slug="no", name="Norway",
        locale="nb-NO", currency="NOK", currency_symbol="kr",
        currency_format="suffix", decimal_sep=",", thousands_sep=" ",
        language_codes=["nb-NO", "no"],
        date_format="%d.%m.%Y",
    ),
    "dk": MarketConfig(
        slug="dk", name="Denmark",
        locale="da-DK", currency="DKK", currency_symbol="kr",
        currency_format="suffix", decimal_sep=",", thousands_sep=".",
        language_codes=["da-DK", "da"],
        date_format="%d.%m.%Y",
    ),

    # ── Asie-Pacifique ────────────────────────────────────────────────────
    "zh": MarketConfig(
        slug="zh", name="China",
        locale="zh-CN", currency="CNY", currency_symbol="¥",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["zh-CN", "zh"],
        date_format="%Y/%m/%d",
    ),
    "tw": MarketConfig(
        slug="tw", name="Taiwan",
        locale="zh-TW", currency="TWD", currency_symbol="NT$",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["zh-TW", "zh"],
        date_format="%Y/%m/%d",
    ),
    "jp": MarketConfig(
        slug="jp", name="Japan",
        locale="ja-JP", currency="JPY", currency_symbol="¥",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["ja-JP", "ja"],
        date_format="%Y/%m/%d",
    ),
    "kr": MarketConfig(
        slug="kr", name="South Korea",
        locale="ko-KR", currency="KRW", currency_symbol="₩",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["ko-KR", "ko"],
        date_format="%Y.%m.%d",
    ),
    "au": MarketConfig(
        slug="au", name="Australia",
        locale="en-AU", currency="AUD", currency_symbol="A$",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["en-AU", "en"],
        date_format="%d/%m/%Y",
    ),
    "in": MarketConfig(
        slug="in", name="India",
        locale="en-IN", currency="INR", currency_symbol="₹",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=["en-IN", "hi"],
        date_format="%d/%m/%Y",
    ),
}


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def get_market(slug: str | None = None) -> MarketConfig:
    """
    Retourne un MarketConfig par son slug.

    Si slug est None, utilise le marché configuré dans settings.MARKET.
    Lève KeyError si le slug est inconnu.

    Exemple :
        market = get_market("fr")
        market = get_market()   # depuis settings.MARKET (défaut: "us")
    """
    if slug is None:
        try:
            from app.core.config import settings
            slug = getattr(settings, "MARKET", "us")
        except Exception:
            slug = "us"

    slug = slug.lower().strip()
    if slug not in _MARKETS:
        raise KeyError(
            f"Marché inconnu : {slug!r}. "
            f"Marchés disponibles : {sorted(_MARKETS.keys())}"
        )
    return _MARKETS[slug]


def list_markets() -> list[MarketConfig]:
    """Retourne la liste de tous les marchés supportés, triés par slug."""
    return [_MARKETS[k] for k in sorted(_MARKETS.keys())]


def list_market_slugs() -> list[str]:
    """Retourne les slugs de tous les marchés supportés."""
    return sorted(_MARKETS.keys())


def market_display_name(slug: str) -> str:
    """Retourne le nom lisible d'un marché."""
    return _MARKETS.get(slug.lower(), MarketConfig(
        slug=slug, name=slug.upper(), locale="en", currency="", currency_symbol="",
        currency_format="prefix", decimal_sep=".", thousands_sep=",",
        language_codes=[], date_format="%Y-%m-%d",
    )).name