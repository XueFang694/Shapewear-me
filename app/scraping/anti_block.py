"""
AntiBlockManager — Gestion des protections anti-scraping.

Fonctionnalités :
  - Pool de 60+ User-Agents réels rotatifs (Chrome, Firefox, Safari, Edge)
  - Délais aléatoires entre requêtes (min/max configurables par connecteur)
  - Détection de blocage : 403, 429, page CAPTCHA, page vide
  - Backoff exponentiel en cas de détection (60–300s + reprise progressive)
  - Support proxy HTTP/SOCKS (configurable via settings)
  - Session fingerprint cohérente (même UA pour toute une session connecteur)
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pool de User-Agents réalistes
# ---------------------------------------------------------------------------

_USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    # Mobile Chrome
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.6367.88 Mobile/15E148 Safari/604.1",
]

# Accept-Language variants cohérents avec le marché US
_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.8",
    "en-US;q=0.9,en;q=0.8",
]

# Referers plausibles pour un site e-commerce
_REFERERS = [
    "https://www.google.com/",
    "https://www.google.com/search?q=spanx+bodysuits",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
    "",  # Accès direct
    "",  # Accès direct (plus probable)
]


class BlockStatus(Enum):
    OK        = "ok"
    SOFT_BLOCK = "soft_block"   # 429 ou délai excessif → attendre et réessayer
    HARD_BLOCK = "hard_block"   # 403 permanent → changer UA / proxy
    CAPTCHA   = "captcha"
    EMPTY     = "empty_response"


@dataclass
class SessionFingerprint:
    """Profil cohérent d'un navigateur simulé pour toute une session."""
    user_agent: str
    accept_language: str
    referer: str = ""

    @classmethod
    def random(cls) -> "SessionFingerprint":
        return cls(
            user_agent=random.choice(_USER_AGENTS),
            accept_language=random.choice(_ACCEPT_LANGUAGES),
            referer=random.choice(_REFERERS),
        )


@dataclass
class BlockingStats:
    """Statistiques de blocage pour un connecteur."""
    total_requests: int = 0
    blocked_requests: int = 0
    captcha_count: int = 0
    backoff_count: int = 0
    current_backoff_s: float = 0.0
    last_block_at: float = 0.0


class AntiBlockManager:
    """
    Gère la rotation des identités et les stratégies de backoff.

    Usage :
        manager = AntiBlockManager(connector_slug="spanx")
        headers = manager.get_headers()
        manager.handle_response(response)   # ajuste si blocage détecté
        manager.polite_delay()              # attend le délai configuré
    """

    # Seuils de backoff (secondes)
    SOFT_BLOCK_INITIAL = 60.0
    SOFT_BLOCK_MAX     = 300.0
    HARD_BLOCK_PAUSE   = 120.0
    BACKOFF_MULTIPLIER = 1.5

    def __init__(
        self,
        connector_slug: str = "unknown",
        delay_min: float = 1.5,
        delay_max: float = 4.0,
        rotate_ua_every: int = 50,   # Changer UA toutes les N requêtes
    ) -> None:
        self._slug         = connector_slug
        self._delay_min    = delay_min
        self._delay_max    = delay_max
        self._rotate_every = rotate_ua_every
        self._stats        = BlockingStats()
        self._fingerprint  = SessionFingerprint.random()
        self._last_req_ts  = 0.0

        log.debug(
            "AntiBlockManager initialisé",
            brand=connector_slug,
            ua=self._fingerprint.user_agent[:60],
        )

    # ── Interface principale ──────────────────────────────────────────────

    def get_headers(self, extra: dict | None = None) -> dict:
        """Retourne les en-têtes HTTP du profil courant."""
        headers = {
            "User-Agent":      self._fingerprint.user_agent,
            "Accept-Language": self._fingerprint.accept_language,
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection":      "keep-alive",
            "Cache-Control":   "no-cache",
        }
        if self._fingerprint.referer:
            headers["Referer"] = self._fingerprint.referer
        if extra:
            headers.update(extra)
        return headers

    def get_json_headers(self, extra: dict | None = None) -> dict:
        """En-têtes optimisés pour les requêtes JSON (API Shopify)."""
        headers = self.get_headers(extra)
        headers["Accept"] = "application/json, text/plain, */*"
        return headers

    def detect_block(self, status_code: int, response_text: str = "") -> BlockStatus:
        """Détecte si une réponse indique un blocage."""
        if status_code == 429:
            return BlockStatus.SOFT_BLOCK
        if status_code == 403:
            if any(kw in response_text.lower() for kw in ["captcha", "robot", "challenge"]):
                return BlockStatus.CAPTCHA
            return BlockStatus.HARD_BLOCK
        if status_code == 200 and len(response_text.strip()) < 100:
            return BlockStatus.EMPTY
        return BlockStatus.OK

    def handle_block(self, block_status: BlockStatus) -> float:
        """
        Applique la stratégie de backoff selon le type de blocage.
        Retourne le temps d'attente en secondes.
        """
        self._stats.blocked_requests += 1
        self._stats.last_block_at = time.monotonic()

        if block_status == BlockStatus.SOFT_BLOCK:
            wait = min(
                self.SOFT_BLOCK_INITIAL * (self.BACKOFF_MULTIPLIER ** self._stats.backoff_count),
                self.SOFT_BLOCK_MAX,
            )
            wait += random.uniform(0, 30)   # Jitter
            self._stats.backoff_count += 1
            self._stats.current_backoff_s = wait
            log.warning(
                "Soft block détecté — backoff",
                brand=self._slug,
                wait_s=round(wait),
                attempt=self._stats.backoff_count,
            )
            time.sleep(wait)
            return wait

        elif block_status in (BlockStatus.HARD_BLOCK, BlockStatus.CAPTCHA):
            wait = self.HARD_BLOCK_PAUSE + random.uniform(0, 60)
            log.warning(
                "Hard block / CAPTCHA — changement UA + pause",
                brand=self._slug,
                wait_s=round(wait),
            )
            self._rotate_fingerprint()
            time.sleep(wait)
            return wait

        elif block_status == BlockStatus.EMPTY:
            wait = random.uniform(10, 30)
            log.debug("Réponse vide — courte pause", brand=self._slug, wait_s=round(wait))
            time.sleep(wait)
            return wait

        return 0.0

    def polite_delay(self) -> None:
        """
        Applique un délai poli entre les requêtes.
        Respecte le délai minimum même si la requête précédente était rapide.
        """
        # Délai aléatoire configuré
        base_delay = random.uniform(self._delay_min, self._delay_max)

        # Vérifier le temps écoulé depuis la dernière requête
        elapsed = time.monotonic() - self._last_req_ts
        remaining = base_delay - elapsed

        if remaining > 0:
            time.sleep(remaining)

        self._last_req_ts = time.monotonic()
        self._stats.total_requests += 1

        # Rotation UA périodique
        if self._stats.total_requests % self._rotate_every == 0:
            self._rotate_fingerprint()

    def reset_backoff(self) -> None:
        """Réinitialise le compteur de backoff après une série de succès."""
        if self._stats.backoff_count > 0:
            log.debug("Backoff réinitialisé", brand=self._slug)
            self._stats.backoff_count = 0
            self._stats.current_backoff_s = 0.0

    def get_stats(self) -> dict:
        """Retourne les statistiques de blocage."""
        total = self._stats.total_requests or 1
        return {
            "total_requests":   self._stats.total_requests,
            "blocked_requests": self._stats.blocked_requests,
            "block_rate_pct":   round(self._stats.blocked_requests / total * 100, 1),
            "backoff_count":    self._stats.backoff_count,
        }

    def get_proxy(self) -> dict | None:
        """Retourne la config proxy depuis les settings (si configuré)."""
        proxy_url = getattr(settings, "PROXY_URL", "")
        if not proxy_url:
            return None
        return {"http://": proxy_url, "https://": proxy_url}

    # ── Helpers internes ──────────────────────────────────────────────────

    def _rotate_fingerprint(self) -> None:
        """Change le profil du navigateur simulé."""
        old_ua = self._fingerprint.user_agent[:40]
        self._fingerprint = SessionFingerprint.random()
        log.debug(
            "Rotation UA",
            brand=self._slug,
            old=old_ua,
            new=self._fingerprint.user_agent[:40],
        )