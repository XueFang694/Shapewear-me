"""
CrawlSession — Modélise l'état complet d'une session d'analyse en cours.

Attributs :
  - brands        : liste des marques à analyser
  - categories    : filtres de catégories (toutes par défaut)
  - tasks         : liste des CrawlTask (une par connecteur × catégorie)
  - status        : pending | running | completed | failed | cancelled
  - progress      : pourcentage global d'avancement
  - stats         : compteurs en temps réel (produits trouvés, erreurs, etc.)
  - started_at, ended_at, duration

Usage :
    session = CrawlSession(brands=["spanx", "skims"])
    session.start()
    session.update_progress(current=50, total=200)
    session.complete()
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.core.events import event_bus
from app.core.logger import get_logger

log = get_logger(__name__)


class SessionStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskStats:
    """Statistiques d'une tâche individuelle (1 connecteur × 1 catégorie)."""
    brand_slug: str
    category_slug: str
    total_urls: int = 0
    processed: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float = 0.0
    status: str = "pending"

    @property
    def duration_s(self) -> float:
        end = self.ended_at or time.monotonic()
        return round(end - self.started_at, 1)

    @property
    def success_rate(self) -> float:
        if self.total_urls == 0:
            return 0.0
        return round((self.processed / self.total_urls) * 100, 1)


@dataclass
class SessionStats:
    """Compteurs agrégés de toute la session."""
    products_found: int = 0
    products_new: int = 0
    products_changed: int = 0
    products_removed: int = 0
    errors: int = 0
    brands_completed: int = 0
    brands_total: int = 0

    def to_dict(self) -> dict:
        return {
            "products_found":    self.products_found,
            "products_new":      self.products_new,
            "products_changed":  self.products_changed,
            "products_removed":  self.products_removed,
            "errors":            self.errors,
            "brands_completed":  self.brands_completed,
            "brands_total":      self.brands_total,
        }


class CrawlSession:
    """
    Gère l'état complet d'une session d'analyse multi-marques.

    Thread-safe : toutes les modifications passent par un verrou interne.
    Émet des événements sur le bus pour notifier l'UI en temps réel.
    """

    def __init__(
        self,
        brands: list[str],
        categories: list[str] | None = None,
        session_id: int | None = None,
    ) -> None:
        self._brands     = list(brands)
        self._categories = list(categories) if categories else []
        self._session_id = session_id
        self._status     = SessionStatus.PENDING
        self._stats      = SessionStats(brands_total=len(brands))
        self._tasks: dict[str, TaskStats] = {}
        self._lock       = threading.Lock()
        self._started_at: datetime | None = None
        self._ended_at:   datetime | None = None
        self._cancel_event = threading.Event()

        log.debug(
            "Session créée",
            brands=brands,
            categories=categories or "toutes",
        )

    # ── État ──────────────────────────────────────────────────────────────

    @property
    def status(self) -> SessionStatus:
        return self._status

    @property
    def is_running(self) -> bool:
        return self._status == SessionStatus.RUNNING

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def brands(self) -> list[str]:
        return list(self._brands)

    @property
    def session_id(self) -> int | None:
        return self._session_id

    @session_id.setter
    def session_id(self, value: int) -> None:
        self._session_id = value

    @property
    def stats(self) -> SessionStats:
        return self._stats

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def ended_at(self) -> datetime | None:
        return self._ended_at

    @property
    def duration_s(self) -> float:
        if not self._started_at:
            return 0.0
        end = self._ended_at or datetime.utcnow()
        return round((end - self._started_at).total_seconds(), 1)

    # ── Transitions d'état ────────────────────────────────────────────────

    def start(self) -> None:
        """Marque le début de la session."""
        with self._lock:
            self._status     = SessionStatus.RUNNING
            self._started_at = datetime.utcnow()
        event_bus.emit(
            "crawl.session.started",
            session_id=self._session_id,
            brands=self._brands,
        )
        log.info("Session démarrée", brands=self._brands, session_id=self._session_id)

    def complete(self) -> None:
        """Marque la fin réussie de la session."""
        with self._lock:
            self._status   = SessionStatus.COMPLETED
            self._ended_at = datetime.utcnow()
        event_bus.emit(
            "crawl.session.completed",
            session_id=self._session_id,
            summary=self._stats.to_dict(),
            duration_s=self.duration_s,
        )
        log.info(
            "Session terminée",
            session_id=self._session_id,
            duration_s=self.duration_s,
            **self._stats.to_dict(),
        )

    def fail(self, error: str = "") -> None:
        """Marque la session comme échouée."""
        with self._lock:
            self._status   = SessionStatus.FAILED
            self._ended_at = datetime.utcnow()
        log.error("Session échouée", error=error, session_id=self._session_id)

    def cancel(self) -> None:
        """Demande l'annulation propre de la session."""
        self._cancel_event.set()
        with self._lock:
            if self._status == SessionStatus.RUNNING:
                self._status   = SessionStatus.CANCELLED
                self._ended_at = datetime.utcnow()
        log.info("Session annulée", session_id=self._session_id)

    # ── Suivi de la progression ───────────────────────────────────────────

    def start_task(self, brand_slug: str, category_slug: str) -> TaskStats:
        """Enregistre le début d'une tâche."""
        task = TaskStats(brand_slug=brand_slug, category_slug=category_slug)
        task.status = "running"
        with self._lock:
            key = f"{brand_slug}/{category_slug}"
            self._tasks[key] = task
        event_bus.emit(
            "crawl.task.started",
            task_id=key,
            brand=brand_slug,
            category=category_slug,
            session_id=self._session_id,
        )
        return task

    def update_task_progress(
        self,
        brand_slug: str,
        category_slug: str,
        current: int,
        total: int,
    ) -> None:
        """Met à jour la progression d'une tâche."""
        key = f"{brand_slug}/{category_slug}"
        with self._lock:
            task = self._tasks.get(key)
            if task:
                task.processed  = current
                task.total_urls = total
        event_bus.emit(
            "crawl.task.progress",
            task_id=key,
            brand=brand_slug,
            category=category_slug,
            current=current,
            total=total,
            session_id=self._session_id,
        )

    def complete_task(
        self,
        brand_slug: str,
        category_slug: str,
        products_count: int = 0,
        errors: int = 0,
    ) -> None:
        """Marque une tâche comme terminée."""
        key = f"{brand_slug}/{category_slug}"
        with self._lock:
            task = self._tasks.get(key)
            if task:
                task.status    = "completed"
                task.ended_at  = time.monotonic()
                task.errors   += errors
        event_bus.emit(
            "crawl.task.completed",
            task_id=key,
            brand=brand_slug,
            products_count=products_count,
            session_id=self._session_id,
        )

    def fail_task(self, brand_slug: str, category_slug: str, error: str) -> None:
        """Marque une tâche comme échouée."""
        key = f"{brand_slug}/{category_slug}"
        with self._lock:
            task = self._tasks.get(key)
            if task:
                task.status   = "failed"
                task.ended_at = time.monotonic()
        event_bus.emit(
            "crawl.task.failed",
            task_id=key,
            brand=brand_slug,
            error=error,
            session_id=self._session_id,
        )

    # ── Mise à jour des compteurs ─────────────────────────────────────────

    def record_product(
        self,
        is_new: bool = False,
        has_changes: bool = False,
    ) -> None:
        """Incrémente les compteurs produit."""
        with self._lock:
            self._stats.products_found += 1
            if is_new:
                self._stats.products_new += 1
            elif has_changes:
                self._stats.products_changed += 1

    def record_removal(self, count: int = 1) -> None:
        """Enregistre des suppressions de produits."""
        with self._lock:
            self._stats.products_removed += count

    def record_error(self) -> None:
        """Enregistre une erreur."""
        with self._lock:
            self._stats.errors += 1

    def record_brand_complete(self) -> None:
        """Marque une marque comme complètement analysée."""
        with self._lock:
            self._stats.brands_completed += 1

    # ── Progression globale ───────────────────────────────────────────────

    @property
    def global_progress_pct(self) -> int:
        """Pourcentage global d'avancement (0–100)."""
        with self._lock:
            tasks = list(self._tasks.values())
        if not tasks:
            return 0
        total_urls     = sum(t.total_urls for t in tasks)
        total_processed = sum(t.processed for t in tasks)
        if total_urls == 0:
            return 0
        return min(100, int(total_processed / total_urls * 100))

    def get_tasks_summary(self) -> list[dict]:
        """Retourne un résumé de toutes les tâches."""
        with self._lock:
            return [
                {
                    "brand":     t.brand_slug,
                    "category":  t.category_slug,
                    "processed": t.processed,
                    "total":     t.total_urls,
                    "errors":    t.errors,
                    "status":    t.status,
                    "duration_s": t.duration_s,
                }
                for t in self._tasks.values()
            ]

    def to_dict(self) -> dict:
        """Sérialise l'état complet de la session."""
        return {
            "session_id":  self._session_id,
            "brands":      self._brands,
            "status":      self._status.value,
            "started_at":  self._started_at.isoformat() if self._started_at else None,
            "ended_at":    self._ended_at.isoformat() if self._ended_at else None,
            "duration_s":  self.duration_s,
            "progress_pct": self.global_progress_pct,
            "stats":       self._stats.to_dict(),
            "tasks":       self.get_tasks_summary(),
        }

    def __repr__(self) -> str:
        return (
            f"CrawlSession(id={self._session_id}, "
            f"brands={self._brands}, "
            f"status={self._status.value})"
        )