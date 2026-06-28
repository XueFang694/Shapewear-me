"""
HttpClient — Client HTTP avec retry exponentiel, rate-limiting et logging.

Encapsule httpx avec :
- Sessions persistantes (cookies, keep-alive)
- Retry exponentiel : 3 tentatives, délai × 2 à chaque échec
- Timeout configurable (connect: 10s, read: 30s par défaut)
- Logging automatique de chaque requête
- Délais polis entre requêtes
"""
from __future__ import annotations

import random
import time
from typing import Any

import httpx

from app.core.exceptions import ConnectorBlockedError, NetworkError, RateLimitError
from app.core.logger import get_logger

log = get_logger(__name__)

# Pool de User-Agents réalistes
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]


class HttpClient:
    """
    Client HTTP résilient pour le scraping.
    Thread-safe : chaque instance maintient sa propre session.
    """

    def __init__(
        self,
        delay_min: float = 0.0,
        delay_max: float = 0.0,
        timeout_connect: int = 10,
        timeout_read: int = 30,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        headers: dict[str, str] | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._last_request_time: float = 0.0

        # En-têtes par défaut
        default_headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        if headers:
            default_headers.update(headers)

        # Configuration du client httpx
        client_kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(
                connect=float(timeout_connect),
                read=float(timeout_read),
                write=10.0,
                pool=10.0,
            ),
            "headers": default_headers,
            "follow_redirects": True,
        }
        if proxy_url:
            client_kwargs["proxies"] = {"http://": proxy_url, "https://": proxy_url}

        self._client = httpx.Client(**client_kwargs)

    def get(
        self,
        url: str,
        params: dict | None = None,
        timeout: int | None = None,
    ) -> httpx.Response:
        """
        Effectue une requête GET avec retry exponentiel.
        Lève une exception métier en cas d'échec définitif.
        """
        self._respect_rate_limit()

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                kwargs: dict[str, Any] = {}
                if params:
                    kwargs["params"] = params
                if timeout:
                    kwargs["timeout"] = timeout

                t0 = time.monotonic()
                response = self._client.get(url, **kwargs)
                duration_ms = int((time.monotonic() - t0) * 1000)

                log.debug(
                    "Requête HTTP",
                    method="GET",
                    url=url,
                    status=response.status_code,
                    duration_ms=duration_ms,
                    attempt=attempt,
                )

                self._last_request_time = time.monotonic()

                # Codes indiquant un blocage → exception spécifique
                if response.status_code == 429:
                    raise RateLimitError(
                        "Rate limit atteint (429)",
                        context={"url": url},
                    )
                if response.status_code == 403:
                    raise ConnectorBlockedError(
                        "Accès refusé (403)",
                        context={"url": url},
                    )

                return response

            except (RateLimitError, ConnectorBlockedError):
                raise  # Propager immédiatement, pas de retry
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                wait = self.retry_delay * (2 ** (attempt - 1))
                log.warning(
                    "Erreur réseau — retry",
                    url=url,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    wait_s=wait,
                    error=str(exc),
                )
                if attempt < self.max_retries:
                    time.sleep(wait)
            except Exception as exc:
                last_exc = exc
                log.error("Erreur HTTP inattendue", url=url, error=str(exc))
                break

        raise NetworkError(
            f"Échec après {self.max_retries} tentatives",
            context={"url": url, "last_error": str(last_exc)},
        )

    def _respect_rate_limit(self) -> None:
        """
        Applique un délai poli entre les requêtes si delay_min > 0.
        """
        if self.delay_min <= 0:
            return
        elapsed = time.monotonic() - self._last_request_time
        min_interval = 1.0 / max(self.delay_min, 0.01)  # rps
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        # Délai aléatoire supplémentaire
        if self.delay_max > 0:
            extra = random.uniform(0, self.delay_max - self.delay_min)
            time.sleep(extra)

    def rotate_user_agent(self) -> None:
        """Remplace le User-Agent par un autre aléatoire."""
        self._client.headers["User-Agent"] = random.choice(_USER_AGENTS)

    def close(self) -> None:
        """Ferme la session HTTP proprement."""
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()