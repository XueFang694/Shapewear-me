"""
ScrapingEngine — Orchestrateur principal des crawls (Phase 1 : single-threaded).

Reçoit un connecteur et une liste de catégories,
retourne un flux de RawProduct via un générateur.
Émet les événements de progression vers le bus d'événements.

Phase 2 : pool de threads (max_workers configurable).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Generator

from app.connectors.base import BaseConnector, Category, RawProduct
from app.core.events import event_bus
from app.core.exceptions import ConnectorBlockedError, ScrapingException
from app.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class ScrapingStats:
    """Statistiques d'un crawl."""
    brand_slug: str
    total_urls: int = 0
    processed: int = 0
    errors: int = 0
    blocked: int = 0
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float = 0.0

    @property
    def duration_s(self) -> float:
        end = self.ended_at or time.monotonic()
        return end - self.started_at

    @property
    def success_rate(self) -> float:
        if self.total_urls == 0:
            return 0.0
        return (self.processed / self.total_urls) * 100


class ScrapingEngine:
    """
    Orchestre le crawl d'un connecteur sur ses catégories.

    Usage :
        engine = ScrapingEngine(connector)
        for raw_product in engine.crawl(categories):
            pipeline.process(raw_product)
    """

    def __init__(
        self,
        connector: BaseConnector,
        session_id: int | None = None,
    ) -> None:
        self._connector = connector
        self._session_id = session_id
        self._meta = connector.get_metadata()
        self._cancelled = False

    def crawl(
        self, categories: list[Category] | None = None
    ) -> Generator[RawProduct, None, None]:
        """
        Crawle toutes les catégories et yield les RawProduct.

        Args:
            categories: liste des catégories à crawler.
                        Si None, utilise get_categories() du connecteur.

        Yields:
            RawProduct pour chaque produit trouvé.
        """
        if categories is None:
            categories = self._connector.get_categories()

        stats = ScrapingStats(brand_slug=self._meta.slug)

        event_bus.emit(
            "crawl.task.started",
            task_id=f"{self._meta.slug}",
            brand=self._meta.slug,
            category="all",
            session_id=self._session_id,
        )

        log.info(
            "Début de crawl",
            brand=self._meta.slug,
            categories=len(categories),
        )

        for category in categories:
            if self._cancelled:
                log.info("Crawl annulé", brand=self._meta.slug)
                break

            yield from self._crawl_category(category, stats)

        stats.ended_at = time.monotonic()

        event_bus.emit(
            "crawl.task.completed",
            task_id=self._meta.slug,
            brand=self._meta.slug,
            products_count=stats.processed,
            errors=stats.errors,
            duration_s=round(stats.duration_s, 1),
            session_id=self._session_id,
        )

        log.info(
            "Crawl terminé",
            brand=self._meta.slug,
            processed=stats.processed,
            errors=stats.errors,
            duration_s=round(stats.duration_s, 1),
            success_rate=f"{stats.success_rate:.1f}%",
        )

    def _crawl_category(
        self, category: Category, stats: ScrapingStats
    ) -> Generator[RawProduct, None, None]:
        """Crawle une seule catégorie."""
        log.info(
            "Crawl catégorie",
            brand=self._meta.slug,
            category=category.slug,
        )
        try:
            urls = self._connector.get_product_urls(category)
            stats.total_urls += len(urls)
        except Exception as exc:
            log.error(
                "Impossible de récupérer les URLs",
                brand=self._meta.slug,
                category=category.slug,
                error=str(exc),
            )
            stats.errors += 1
            return

        for i, url in enumerate(urls, 1):
            if self._cancelled:
                return

            event_bus.emit(
                "crawl.task.progress",
                task_id=self._meta.slug,
                brand=self._meta.slug,
                category=category.slug,
                current=i,
                total=len(urls),
                session_id=self._session_id,
            )

            try:
                product = self._fetch_product(url)
                if product:
                    stats.processed += 1
                    event_bus.emit(
                        "product.fetched",
                        brand=self._meta.slug,
                        url=url,
                        name=product.name,
                        session_id=self._session_id,
                    )
                    yield product

            except ConnectorBlockedError:
                stats.blocked += 1
                log.warning(
                    "Connecteur bloqué",
                    brand=self._meta.slug,
                    url=url,
                )
                # Pause longue puis reprise
                import time as _time
                _time.sleep(90)

            except ScrapingException as exc:
                stats.errors += 1
                log.error(
                    "Erreur scraping produit",
                    brand=self._meta.slug,
                    url=url,
                    error=str(exc),
                )

            except Exception as exc:
                stats.errors += 1
                log.error(
                    "Erreur inattendue produit",
                    brand=self._meta.slug,
                    url=url,
                    error=str(exc),
                )

    def _fetch_product(self, url: str) -> RawProduct | None:
        """Récupère et parse un produit depuis son URL."""
        from app.scraping.http_client import HttpClient

        engine = self._connector.config.get("engine", "html")
        client = HttpClient(
            delay_min=self._connector.delay_min,
            delay_max=self._connector.delay_max,
            headers=self._connector.config.get("headers", {}),
        )

        if engine == "shopify_json":
            response = client.get(url)
            if response.status_code != 200:
                return None
            data = response.json()
            product_data = data.get("product", data)
            return self._connector.parse_product(url, product_data)
        else:
            response = client.get(url)
            if response.status_code != 200:
                return None
            return self._connector.parse_product(url, response.text)

    def cancel(self) -> None:
        """Demande l'arrêt propre du crawl après la tâche en cours."""
        self._cancelled = True
        log.info("Annulation demandée", brand=self._meta.slug)