"""
ScrapingEngine v2 — Orchestrateur multi-threaded (Phase 2).

Deux modes :
  - Single-threaded : max_workers=1 (comportement Phase 1)
  - Multi-threaded  : pool ThreadPoolExecutor (2–4 workers par défaut)

La collecte HTTP est parallélisée par worker, le traitement (pipeline) reste
séquentiel pour éviter les conflits en base de données.

Usage :
    engine = ScrapingEngine(connector, max_workers=2, session_id=42)
    for raw_product in engine.crawl(categories):
        pipeline.process(raw_product)
"""
from __future__ import annotations

import json
from pathlib import Path
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Generator

from app.connectors.base import BaseConnector, Category, RawProduct
from app.core.events import event_bus
from app.core.exceptions import ConnectorBlockedError, ScrapingException
from app.core.logger import get_logger

log = get_logger(__name__)

# Sentinelle de fin de file pour le mode multi-threaded
_SENTINEL = object()


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

    En mode multi-threaded (max_workers > 1) :
      - Chaque worker est responsable d'un sous-ensemble d'URLs.
      - Les produits sont envoyés dans une queue thread-safe.
      - Le générateur principal lit la queue et yield les produits.

    Usage :
        engine = ScrapingEngine(connector, max_workers=2)
        for raw_product in engine.crawl(categories):
            pipeline.process(raw_product)
    """

    def __init__(
        self,
        connector: BaseConnector,
        session_id: int | None = None,
        max_workers: int | None = None,
    ) -> None:
        self._connector  = connector
        self._session_id = session_id
        self._meta       = connector.get_metadata()
        self._cancelled  = False

        # max_workers : depuis le paramètre, sinon depuis settings
        if max_workers is None:
            try:
                from app.core.config import settings
                max_workers = settings.MAX_WORKERS
            except Exception:
                max_workers = 1
        self._max_workers = max(1, int(max_workers))

    # ── Interface principale ──────────────────────────────────────────────

    def crawl(
        self, categories: list[Category] | None = None
    ) -> Generator[RawProduct, None, None]:
        """
        Crawle toutes les catégories et yield les RawProduct.

        Le mode de crawl est choisi automatiquement :
          - max_workers == 1 → single-threaded (plus simple, moins de charge)
          - max_workers  > 1 → multi-threaded avec ThreadPoolExecutor
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
            workers=self._max_workers,
        )

        if self._max_workers <= 1:
            yield from self._crawl_single(categories, stats)
        else:
            yield from self._crawl_multi(categories, stats)

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

    def cancel(self) -> None:
        """Demande l'arrêt propre du crawl après la tâche en cours."""
        self._cancelled = True
        log.info("Annulation demandée", brand=self._meta.slug)

    # ── Mode single-threaded ─────────────────────────────────────────────

    def _crawl_single(
        self, categories: list[Category], stats: ScrapingStats
    ) -> Generator[RawProduct, None, None]:
        for category in categories:
            if self._cancelled:
                break
            yield from self._crawl_category(category, stats)

    # ── Mode multi-threaded ──────────────────────────────────────────────

    def _crawl_multi(
        self, categories: list[Category], stats: ScrapingStats
    ) -> Generator[RawProduct, None, None]:
        """
        Crawl parallèle : chaque catégorie est attribuée à un worker.
        Les produits sont collectés dans une queue thread-safe.
        """
        product_queue: queue.Queue = queue.Queue(maxsize=200)
        workers_done = threading.Event()
        worker_count = [0]
        lock = threading.Lock()

        def worker(category: Category) -> None:
            try:
                for product in self._crawl_category_threadsafe(category, stats):
                    if self._cancelled:
                        break
                    product_queue.put(product)
            except Exception as exc:
                log.error(
                    "Erreur worker",
                    brand=self._meta.slug,
                    category=category.slug,
                    error=str(exc),
                )
            finally:
                with lock:
                    worker_count[0] -= 1
                    if worker_count[0] == 0:
                        workers_done.set()
                        product_queue.put(_SENTINEL)

        # Lancer les workers
        with lock:
            worker_count[0] = len(categories)

        executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix=f"scraper-{self._meta.slug}",
        )
        futures = [executor.submit(worker, cat) for cat in categories]

        # Consommer la queue jusqu'à la sentinelle
        try:
            while True:
                try:
                    item = product_queue.get(timeout=0.5)
                    if item is _SENTINEL:
                        break
                    stats.processed += 1
                    yield item
                except queue.Empty:
                    if workers_done.is_set() and product_queue.empty():
                        break
                    continue
        finally:
            self._cancelled = True  # signaler aux workers de s'arrêter
            executor.shutdown(wait=False)

    # ── Crawl d'une catégorie (thread-safe) ─────────────────────────────

    def _crawl_category_threadsafe(
        self, category: Category, stats: ScrapingStats
    ) -> Generator[RawProduct, None, None]:
        """Version thread-safe — pas de yield direct dans le thread principal."""
        log.info("Crawl catégorie", brand=self._meta.slug, category=category.slug)
        try:
            urls = self._connector.get_product_urls(category)
        except Exception as exc:
            log.error(
                "Impossible de récupérer les URLs",
                brand=self._meta.slug, category=category.slug, error=str(exc),
            )
            return

        with threading.Lock():
            stats.total_urls += len(urls)

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

            product = self._fetch_safe(url, stats)
            if product:
                event_bus.emit(
                    "product.fetched",
                    brand=self._meta.slug, url=url,
                    name=product.name, session_id=self._session_id,
                )
                yield product

    def _crawl_category(
        self, category: Category, stats: ScrapingStats
    ) -> Generator[RawProduct, None, None]:
        """Version single-threaded."""
        log.info("Crawl catégorie", brand=self._meta.slug, category=category.slug)
        try:
            urls = self._connector.get_product_urls(category)
            stats.total_urls += len(urls)
        except Exception as exc:
            log.error(
                "Impossible de récupérer les URLs",
                brand=self._meta.slug, category=category.slug, error=str(exc),
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

            product = self._fetch_safe(url, stats)
            if product:
                stats.processed += 1
                event_bus.emit(
                    "product.fetched",
                    brand=self._meta.slug, url=url,
                    name=product.name, session_id=self._session_id,
                )
                yield product

    # ── Fetch d'un produit ────────────────────────────────────────────────

    def _fetch_safe(self, url: str, stats: ScrapingStats) -> RawProduct | None:
        """Fetch + parse avec gestion des erreurs et blocage."""
        try:
            return self._fetch_product(url)
        except ConnectorBlockedError:
            stats.blocked += 1
            log.warning("Connecteur bloqué", brand=self._meta.slug, url=url)
            time.sleep(90)
            return None
        except ScrapingException as exc:
            stats.errors += 1
            log.error("Erreur scraping", brand=self._meta.slug, url=url, error=str(exc))
            return None
        except Exception as exc:
            stats.errors += 1
            log.error("Erreur inattendue", brand=self._meta.slug, url=url, error=str(exc))
            return None

    def _fetch_product(self, url: str) -> RawProduct | None:
        """
        Récupère et parse un produit depuis son URL.

        Si l'URL n'utilise pas le schéma http(s), on considère qu'il s'agit
        d'une URL virtuelle (ex: shapermint://product/<slug>) définie par le
        connecteur pour court-circuiter le fetch HTTP. Dans ce cas on délègue
        directement à parse_product() avec l'URL comme données, laissant le
        connecteur récupérer les données depuis son cache interne.
        """
        # ── URL virtuelle (schéma non-HTTP) → délégation directe ─────────
        if not url.startswith("http://") and not url.startswith("https://"):
            log.debug(
                "URL virtuelle détectée — délégation directe au connecteur",
                brand=self._meta.slug,
                url=url,
            )
            return self._connector.parse_product(url, url)

        # ── URL HTTP standard ────────────────────────────────────────────
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
            _shopify_cache_dir = Path("data/shopify")
            _shopify_cache_dir.mkdir(parents=True, exist_ok=True)
            _shopify_cache_dir.joinpath(
                "shopify_{}.json".format(url.split("/")[-1])
            ).write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            product_data = data.get("product", data)
            return self._connector.parse_product(url, product_data)
        else:
            response = client.get(url)
            if response.status_code != 200:
                return None
            return self._connector.parse_product(url, response.text)