"""
Bus pub/sub interne léger et thread-safe.

Les modules émettent des événements, l'UI s'y abonne sans couplage direct.

Événements définis :
    crawl.session.started       { session_id, brands }
    crawl.task.started          { task_id, brand, category }
    crawl.task.progress         { task_id, current, total }
    crawl.task.completed        { task_id, products_count }
    crawl.task.failed           { task_id, error }
    crawl.session.completed     { session_id, summary }
    product.saved               { product_id, brand, is_new }
    change.detected             { change_type, product_id, old_value, new_value }
    log.message                 { level, message, context }

Usage :
    from app.core.events import event_bus

    # S'abonner
    event_bus.subscribe("crawl.task.progress", my_handler)

    # Émettre
    event_bus.emit("crawl.task.progress", task_id=42, current=10, total=100)
"""
from __future__ import annotations

import queue
import threading
from collections import defaultdict
from typing import Any, Callable

from app.core.logger import get_logger

log = get_logger(__name__)

# Type d'un handler d'événement
EventHandler = Callable[..., None]


class EventBus:
    """
    Bus d'événements pub/sub thread-safe.
    Les handlers sont appelés dans un thread dédié pour ne pas bloquer les émetteurs.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[str, dict]] = queue.Queue()
        self._running = False
        self._worker_thread: threading.Thread | None = None

    def start(self) -> None:
        """Démarre le thread de dispatch des événements."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._dispatch_loop,
            name="EventBus-Worker",
            daemon=True,
        )
        self._worker_thread.start()
        log.debug("Bus d'événements démarré")

    def stop(self) -> None:
        """Arrête proprement le bus d'événements."""
        self._running = False
        # Poison pill pour débloquer le worker
        self._queue.put(("__stop__", {}))
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
        log.debug("Bus d'événements arrêté")

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Abonne un handler à un type d'événement."""
        with self._lock:
            self._subscribers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        """Désabonne un handler."""
        with self._lock:
            handlers = self._subscribers.get(event_name, [])
            if handler in handlers:
                handlers.remove(handler)

    def emit(self, event_name: str, **kwargs: Any) -> None:
        """
        Émet un événement de manière asynchrone (non-bloquant).
        Si le bus n'est pas démarré, dispatch synchrone en fallback.
        """
        if self._running:
            self._queue.put((event_name, kwargs))
        else:
            # Dispatch synchrone (utile en tests ou avant démarrage de l'UI)
            self._call_handlers(event_name, kwargs)

    def emit_sync(self, event_name: str, **kwargs: Any) -> None:
        """Émet un événement de manière synchrone (bloquant — pour les tests)."""
        self._call_handlers(event_name, kwargs)

    def _dispatch_loop(self) -> None:
        """Boucle interne du thread de dispatch."""
        while self._running:
            try:
                event_name, kwargs = self._queue.get(timeout=0.1)
                if event_name == "__stop__":
                    break
                self._call_handlers(event_name, kwargs)
            except queue.Empty:
                continue

    def _call_handlers(self, event_name: str, kwargs: dict) -> None:
        """Appelle tous les handlers abonnés à cet événement."""
        with self._lock:
            handlers = list(self._subscribers.get(event_name, []))
        for handler in handlers:
            try:
                handler(**kwargs)
            except Exception as exc:
                log.error(
                    "Erreur dans un handler d'événement",
                    event=event_name,
                    handler=handler.__name__,
                    error=str(exc),
                )

    def clear(self) -> None:
        """Supprime tous les abonnements (utile pour les tests)."""
        with self._lock:
            self._subscribers.clear()


# Instance unique partagée dans toute l'application
event_bus = EventBus()