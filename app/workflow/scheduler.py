"""
Scheduler — Planification automatique des sessions d'analyse (Phase 2).

Permet de configurer des analyses récurrentes sans intervention manuelle :
  - daily   : tous les jours à l'heure définie
  - weekly  : une fois par semaine (jour + heure)
  - manual  : désactive la planification automatique

La configuration est persistée dans settings.json sous la clé "schedule".

Usage :
    scheduler = Scheduler()
    scheduler.configure(mode="daily", hour=2, minute=0, brands=["spanx"])
    scheduler.start()
    ...
    scheduler.stop()
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings, PROJECT_ROOT
from app.core.logger import get_logger

log = get_logger(__name__)

_SETTINGS_FILE = PROJECT_ROOT / "settings.json"
_SCHEDULE_KEY  = "schedule"

_DEFAULT_SCHEDULE: dict = {
    "mode":    "manual",    # manual | daily | weekly
    "hour":    2,           # heure de déclenchement (0–23)
    "minute":  0,           # minute (0–59)
    "weekday": 1,           # 0=lundi … 6=dimanche (mode weekly uniquement)
    "brands":  None,        # None = toutes les marques
    "enabled": False,
}


class ScheduleConfig:
    """Configuration d'une planification."""

    def __init__(self, data: dict | None = None) -> None:
        d = data or {}
        self.mode:    str           = d.get("mode", "manual")
        self.hour:    int           = int(d.get("hour", 2))
        self.minute:  int           = int(d.get("minute", 0))
        self.weekday: int           = int(d.get("weekday", 1))
        self.brands:  list | None   = d.get("brands")
        self.enabled: bool          = bool(d.get("enabled", False))

    def to_dict(self) -> dict:
        return {
            "mode":    self.mode,
            "hour":    self.hour,
            "minute":  self.minute,
            "weekday": self.weekday,
            "brands":  self.brands,
            "enabled": self.enabled,
        }

    @property
    def is_active(self) -> bool:
        return self.enabled and self.mode != "manual"

    def describe(self) -> str:
        """Retourne une description lisible de la planification."""
        if not self.is_active:
            return "Planification désactivée"
        time_str = f"{self.hour:02d}:{self.minute:02d}"
        if self.mode == "daily":
            return f"Chaque jour à {time_str}"
        if self.mode == "weekly":
            days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
            day_name = days[self.weekday % 7]
            return f"Chaque {day_name} à {time_str}"
        return "Manuel"


class Scheduler:
    """
    Planificateur léger basé sur threading.Timer.

    N'utilise pas APScheduler en Phase 2 pour éviter une dépendance
    supplémentaire — le threading.Timer est suffisant pour des analyses
    quotidiennes ou hebdomadaires.

    Pour une V3 plus sophistiquée (cron, persistance des jobs au redémarrage),
    APScheduler est recommandé.
    """

    def __init__(self, run_callback: Callable | None = None) -> None:
        """
        Args:
            run_callback : fonction appelée lors d'une exécution planifiée.
                           Signature : callback(brand_slugs: list[str] | None)
        """
        self._callback   = run_callback
        self._config     = self._load_config()
        self._timer: threading.Timer | None = None
        self._running    = False
        self._lock       = threading.Lock()
        self._last_run:  datetime | None = None
        self._next_run:  datetime | None = None

    # ── Interface publique ────────────────────────────────────────────────

    def start(self) -> None:
        """Démarre le planificateur si la configuration est active."""
        with self._lock:
            if self._running:
                return
            self._running = True

        if self._config.is_active:
            self._schedule_next()
            log.info(
                "Planificateur démarré",
                mode=self._config.mode,
                next_run=self._next_run,
            )
        else:
            log.debug("Planificateur démarré — aucune planification active")

    def stop(self) -> None:
        """Arrête le planificateur proprement."""
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
        log.info("Planificateur arrêté")

    def configure(
        self,
        mode: str = "manual",
        hour: int = 2,
        minute: int = 0,
        weekday: int = 1,
        brands: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        """
        Configure et persiste une nouvelle planification.

        Args:
            mode    : "manual" | "daily" | "weekly"
            hour    : heure de déclenchement (0–23)
            minute  : minute (0–59)
            weekday : 0=Lundi … 6=Dimanche (mode weekly)
            brands  : liste de slugs, None = toutes les marques
            enabled : active la planification
        """
        config = ScheduleConfig({
            "mode":    mode,
            "hour":    hour,
            "minute":  minute,
            "weekday": weekday,
            "brands":  brands,
            "enabled": enabled,
        })
        self._config = config
        self._save_config(config)

        # Redémarrer la planification si le scheduler tourne
        with self._lock:
            if self._running:
                if self._timer:
                    self._timer.cancel()
                    self._timer = None
                if config.is_active:
                    self._schedule_next()

        log.info(
            "Planification configurée",
            description=config.describe(),
            brands=brands or "toutes",
        )

    def get_config(self) -> ScheduleConfig:
        """Retourne la configuration actuelle."""
        return self._config

    def get_status(self) -> dict:
        """Retourne l'état du planificateur."""
        return {
            "running":     self._running,
            "config":      self._config.to_dict(),
            "description": self._config.describe(),
            "last_run":    self._last_run.isoformat() if self._last_run else None,
            "next_run":    self._next_run.isoformat() if self._next_run else None,
        }

    def trigger_now(self) -> None:
        """Déclenche une exécution immédiate (hors planification)."""
        log.info("Exécution immédiate déclenchée")
        self._execute()

    # ── Logique de planification ──────────────────────────────────────────

    def _schedule_next(self) -> None:
        """Calcule le délai jusqu'à la prochaine exécution et arme le timer."""
        now  = datetime.now()
        next_run = self._compute_next_run(now)
        self._next_run = next_run
        delay = (next_run - now).total_seconds()

        if delay < 0:
            delay = 0

        self._timer = threading.Timer(delay, self._on_timer_fired)
        self._timer.daemon = True
        self._timer.name   = "Scheduler-Timer"
        self._timer.start()

        log.info(
            "Prochaine exécution planifiée",
            next_run=next_run.strftime("%d/%m/%Y %H:%M"),
            delay_h=round(delay / 3600, 1),
        )

    def _compute_next_run(self, now: datetime) -> datetime:
        """Calcule la prochaine date d'exécution selon le mode."""
        target_hour   = self._config.hour
        target_minute = self._config.minute

        if self._config.mode == "daily":
            candidate = now.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate

        if self._config.mode == "weekly":
            target_day = self._config.weekday
            # Trouver le prochain jour de la semaine cible
            days_ahead = (target_day - now.weekday()) % 7
            if days_ahead == 0:
                candidate = now.replace(
                    hour=target_hour, minute=target_minute, second=0, microsecond=0
                )
                if candidate <= now:
                    days_ahead = 7
            candidate = now + timedelta(days=days_ahead)
            return candidate.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )

        # Fallback : dans 24h
        return now + timedelta(hours=24)

    def _on_timer_fired(self) -> None:
        """Appelé par le timer — exécute la session et replanifie."""
        with self._lock:
            if not self._running:
                return

        self._execute()

        # Replanifier si toujours actif
        with self._lock:
            if self._running and self._config.is_active:
                self._schedule_next()

    def _execute(self) -> None:
        """Lance l'exécution de la session d'analyse."""
        self._last_run = datetime.now()
        brands = self._config.brands

        log.info(
            "Exécution planifiée démarrée",
            brands=brands or "toutes",
            triggered_at=self._last_run.strftime("%d/%m/%Y %H:%M"),
        )

        if self._callback:
            try:
                self._callback(brands)
            except Exception as exc:
                log.error("Erreur lors de l'exécution planifiée", error=str(exc))
        else:
            # Exécution directe sans callback (mode CLI)
            try:
                from app.workflow.runner import WorkflowRunner
                runner = WorkflowRunner()
                results = runner.run(brand_slugs=brands)
                total = sum(r.products_found for r in results)
                log.info(
                    "Exécution planifiée terminée",
                    total_products=total,
                    brands=[r.brand_slug for r in results],
                )
            except Exception as exc:
                log.error("Erreur exécution planifiée", error=str(exc))

    # ── Persistance de la configuration ──────────────────────────────────

    def _load_config(self) -> ScheduleConfig:
        """Charge la configuration depuis settings.json."""
        try:
            if _SETTINGS_FILE.exists():
                with open(_SETTINGS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                schedule_data = data.get(_SCHEDULE_KEY, _DEFAULT_SCHEDULE)
                return ScheduleConfig(schedule_data)
        except Exception as exc:
            log.warning("Impossible de charger la config planification", error=str(exc))
        return ScheduleConfig(_DEFAULT_SCHEDULE)

    def _save_config(self, config: ScheduleConfig) -> None:
        """Persiste la configuration dans settings.json."""
        try:
            existing: dict = {}
            if _SETTINGS_FILE.exists():
                with open(_SETTINGS_FILE, encoding="utf-8") as f:
                    existing = json.load(f)
            existing[_SCHEDULE_KEY] = config.to_dict()
            with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            log.error("Impossible de sauvegarder la config planification", error=str(exc))

    def __del__(self) -> None:
        """Nettoyage lors de la destruction de l'objet."""
        try:
            self.stop()
        except Exception:
            pass