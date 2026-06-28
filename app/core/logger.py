"""
Logger centralisé avec rotation automatique et émission vers le bus d'événements.

Usage :
    from app.core.logger import get_logger
    log = get_logger(__name__)
    log.info("Crawl démarré", brand="spanx", category="bodysuits")
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any


_loggers: dict[str, "StructLogger"] = {}
_file_handler: logging.FileHandler | None = None
_ui_handler: "_UIEventHandler | None" = None


class _UIEventHandler(logging.Handler):
    """Redirige les logs INFO+ vers le bus d'événements sans couplage direct."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Import tardif pour éviter les imports circulaires
            from app.core.events import event_bus
            event_bus.emit(
                "log.message",
                level=record.levelname,
                message=self.format(record),
                logger=record.name,
            )
        except Exception:
            pass  # Ne jamais planter à cause des logs


class StructLogger:
    """
    Logger structuré léger.
    Accepte des kwargs en plus du message pour un contexte riche.
    """

    def __init__(self, name: str, logger: logging.Logger) -> None:
        self._name = name
        self._logger = logger

    def _format_msg(self, message: str, **context: Any) -> str:
        if context:
            ctx = " | ".join(f"{k}={v}" for k, v in context.items())
            return f"{message} [{ctx}]"
        return message

    def debug(self, message: str, **context: Any) -> None:
        self._logger.debug(self._format_msg(message, **context))

    def info(self, message: str, **context: Any) -> None:
        self._logger.info(self._format_msg(message, **context))

    def warning(self, message: str, **context: Any) -> None:
        self._logger.warning(self._format_msg(message, **context))

    def error(self, message: str, **context: Any) -> None:
        self._logger.error(self._format_msg(message, **context))

    def critical(self, message: str, **context: Any) -> None:
        self._logger.critical(self._format_msg(message, **context))

    def exception(self, message: str, **context: Any) -> None:
        self._logger.exception(self._format_msg(message, **context))


def _setup_root_logger(log_dir: Path, level: str = "INFO") -> None:
    """Configure le logger racine (une seule fois)."""
    global _file_handler, _ui_handler

    root = logging.getLogger("app")
    root.setLevel(logging.DEBUG)

    # Éviter les doublons si déjà configuré
    if root.handlers:
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "market_intel.log"

    # Fichier rotatif : 5 Mo max, 10 sauvegardes
    _file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    _file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _file_handler.setFormatter(file_fmt)
    root.addHandler(_file_handler)

    # Console (dev uniquement)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    console_fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    root.addHandler(console_handler)

    # Émission vers l'UI via le bus d'événements
    _ui_handler = _UIEventHandler()
    _ui_handler.setLevel(logging.INFO)
    _ui_handler.setFormatter(
        logging.Formatter("%(levelname)s — %(message)s")
    )
    root.addHandler(_ui_handler)


def get_logger(name: str) -> StructLogger:
    """
    Retourne un StructLogger pour le module donné.
    Configure le logger racine au premier appel.
    """
    if name not in _loggers:
        # Lazy init : on lit les settings seulement quand nécessaire
        try:
            from app.core.config import settings
            _setup_root_logger(settings.LOG_DIR, settings.LOG_LEVEL)
        except Exception:
            # Fallback si la config n'est pas encore disponible
            _setup_root_logger(Path("data/logs"))

        underlying = logging.getLogger(name)
        _loggers[name] = StructLogger(name, underlying)

    return _loggers[name]