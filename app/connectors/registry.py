"""
ConnectorRegistry — Auto-découverte des connecteurs disponibles.

Scanne les sous-dossiers de app/connectors/ au démarrage.
Un dossier est reconnu comme connecteur s'il contient connector.py et config.yml.

Usage :
    from app.connectors.registry import ConnectorRegistry

    registry = ConnectorRegistry()
    available = registry.list_connectors()     # ['spanx', 'skims', ...]
    connector = registry.get('spanx')          # Instance SpanxConnector
"""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from app.connectors.base import BaseConnector
from app.core.exceptions import ConnectorConfigError
from app.core.logger import get_logger

log = get_logger(__name__)

_CONNECTORS_DIR = Path(__file__).parent


class ConnectorRegistry:
    """Auto-découverte et accès aux connecteurs par slug."""

    def __init__(self) -> None:
        self._registry: dict[str, type[BaseConnector]] = {}
        self._discover()

    def _discover(self) -> None:
        """Scanne les sous-dossiers et importe les connecteurs disponibles."""
        for candidate_dir in sorted(_CONNECTORS_DIR.iterdir()):
            if not candidate_dir.is_dir():
                continue
            if candidate_dir.name.startswith(("_", ".")):
                continue
            connector_file = candidate_dir / "connector.py"
            config_file = candidate_dir / "config.yml"
            if not connector_file.exists() or not config_file.exists():
                continue

            module_path = f"app.connectors.{candidate_dir.name}.connector"
            try:
                module = importlib.import_module(module_path)
                # Cherche la première sous-classe de BaseConnector dans le module
                for _name, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(obj, BaseConnector)
                        and obj is not BaseConnector
                        and not inspect.isabstract(obj)
                    ):
                        slug = candidate_dir.name
                        self._registry[slug] = obj
                        log.debug("Connecteur découvert", slug=slug, class_=_name)
                        break
            except Exception as exc:
                log.warning(
                    "Impossible de charger le connecteur",
                    dir=candidate_dir.name,
                    error=str(exc),
                )

        log.info("Connecteurs disponibles", slugs=list(self._registry.keys()))

    def list_connectors(self) -> list[str]:
        """Retourne la liste des slugs de connecteurs disponibles."""
        return sorted(self._registry.keys())

    def get(self, slug: str) -> BaseConnector:
        """
        Instancie et retourne le connecteur pour le slug donné.
        Lève ConnectorConfigError si le slug est inconnu.
        """
        if slug not in self._registry:
            raise ConnectorConfigError(
                f"Connecteur inconnu : {slug!r}",
                context={"available": self.list_connectors()},
            )
        connector_class = self._registry[slug]
        config_path = _CONNECTORS_DIR / slug / "config.yml"
        return connector_class(config_path=config_path)

    def get_class(self, slug: str) -> type[BaseConnector]:
        """Retourne la classe (non instanciée) du connecteur."""
        if slug not in self._registry:
            raise ConnectorConfigError(f"Connecteur inconnu : {slug!r}")
        return self._registry[slug]

    def is_available(self, slug: str) -> bool:
        """Vérifie si un connecteur est disponible."""
        return slug in self._registry

    def reload(self) -> None:
        """Force la re-découverte des connecteurs (utile en développement)."""
        self._registry.clear()
        self._discover()

    def __repr__(self) -> str:
        return f"ConnectorRegistry(connectors={self.list_connectors()})"