"""
BaseConnector — Classe abstraite définissant le contrat de tout connecteur de marque.

Chaque connecteur doit :
  1. Implémenter les 4 méthodes abstraites
  2. Placer son config.yml dans le même dossier
  3. Être auto-découvert par ConnectorRegistry

Usage :
    from app.connectors.base import BaseConnector, RawProduct, ConnectorMeta
"""
from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Generator

import yaml

from app.core.exceptions import ConnectorBlockedError, ConnectorConfigError
from app.core.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses de sortie
# ---------------------------------------------------------------------------

@dataclass
class Category:
    """Représente une catégorie de produits d'un site."""
    slug: str
    name: str
    url: str
    brand_slug: str
    extra: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Category(slug={self.slug!r}, name={self.name!r})"


@dataclass
class RawProduct:
    """
    Contrat de sortie de tout connecteur.
    Tous les champs sont optionnels sauf external_id, url, name, brand_slug.
    """
    # Obligatoires
    external_id: str
    url: str
    name: str
    brand_slug: str

    # Prix
    price: float | None = None
    original_price: float | None = None
    currency: str = "USD"
    on_sale: bool = False

    # Classification brute (telle que vue sur le site)
    category_raw: str | None = None
    subcategory_raw: str | None = None

    # Contenu
    description: str | None = None
    composition: str | None = None
    size_guide: str | None = None
    images: list[str] = field(default_factory=list)

    # Variantes
    sizes: list[str] = field(default_factory=list)
    colors: list[dict] = field(default_factory=list)
    variants: list[dict] = field(default_factory=list)

    # Disponibilité
    availability: str = "unknown"   # in_stock | out_of_stock | unknown

    # Avis clients
    rating: float | None = None
    review_count: int | None = None

    # Compression (si disponible dans les données brutes)
    compression_level_raw: str | None = None
    target_zones_raw: list[str] = field(default_factory=list)

    # Extension libre pour les champs spécifiques à une marque
    extra: dict = field(default_factory=dict)

    # Métadonnées de collecte
    crawled_at: datetime = field(default_factory=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"RawProduct(external_id={self.external_id!r}, "
            f"name={self.name!r}, brand={self.brand_slug!r}, "
            f"price={self.price})"
        )


@dataclass
class ConnectorMeta:
    """Métadonnées d'un connecteur."""
    name: str
    slug: str
    version: str
    engine: str = "shopify_json"   # shopify_json | html | graphql
    base_url: str = ""


class ConnectionStatus(Enum):
    OK = "ok"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"


# ---------------------------------------------------------------------------
# BaseConnector
# ---------------------------------------------------------------------------

class BaseConnector(ABC):
    """
    Classe abstraite dont tout connecteur de marque doit hériter.

    Structure attendue dans le dossier du connecteur :
        connector.py  — ce fichier (implémentation)
        config.yml    — paramètres (URLs, sélecteurs, rate-limit)
        mappings.py   — correspondances champs bruts → normalisés
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or self._find_config()
        self._config: dict = {}
        self._http_client = None   # Injecté par ScrapingEngine si besoin
        self._load_config()

    # -------------------------------------------------------------------
    # Méthodes abstraites — obligatoires dans chaque connecteur
    # -------------------------------------------------------------------

    @abstractmethod
    def get_categories(self) -> list[Category]:
        """Retourne la liste des catégories produits du site."""

    @abstractmethod
    def get_product_urls(self, category: Category) -> list[str]:
        """Retourne toutes les URLs produits d'une catégorie (pagination incluse)."""

    @abstractmethod
    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        """
        Extrait les données d'une page/objet produit.
        En mode shopify_json, ``data`` est le dict JSON du produit.
        En mode html, ``data`` est le HTML brut de la page.
        """

    @abstractmethod
    def get_metadata(self) -> ConnectorMeta:
        """Retourne les métadonnées du connecteur (nom, version, moteur)."""

    # -------------------------------------------------------------------
    # Méthodes fournies par la base (non à réimplémenter)
    # -------------------------------------------------------------------

    def crawl_all(
        self, categories: list[Category] | None = None
    ) -> Generator[RawProduct, None, None]:
        """
        Itère sur toutes les catégories et retourne les produits un par un.
        Si categories est None, utilise get_categories().
        """
        if categories is None:
            categories = self.get_categories()

        meta = self.get_metadata()
        for category in categories:
            log.info(
                "Début crawl catégorie",
                brand=meta.slug,
                category=category.slug,
            )
            try:
                urls = self.get_product_urls(category)
                log.info(
                    "URLs récupérées",
                    brand=meta.slug,
                    category=category.slug,
                    count=len(urls),
                )
                for url in urls:
                    try:
                        product = self._fetch_and_parse(url)
                        if product:
                            yield product
                        self._polite_delay()
                    except ConnectorBlockedError:
                        log.warning(
                            "Connecteur bloqué — pause longue",
                            brand=meta.slug,
                            url=url,
                        )
                        time.sleep(random.uniform(60, 120))
                    except Exception as exc:
                        log.error(
                            "Erreur parsing produit",
                            brand=meta.slug,
                            url=url,
                            error=str(exc),
                        )
            except Exception as exc:
                log.error(
                    "Erreur catégorie",
                    brand=meta.slug,
                    category=category.slug,
                    error=str(exc),
                )

    def validate_config(self) -> bool:
        """Valide que le config.yml est bien formé."""
        required_keys = {"name", "slug", "base_url", "engine"}
        missing = required_keys - set(self._config.keys())
        if missing:
            raise ConnectorConfigError(
                f"Clés manquantes dans config.yml : {missing}",
                context={"path": str(self._config_path)},
            )
        return True

    def test_connection(self) -> ConnectionStatus:
        """
        Teste la connectivité vers le site cible.
        Retourne ConnectionStatus.OK si le site répond en HTTP 200.
        """
        from app.scraping.http_client import HttpClient
        client = HttpClient()
        base_url = self._config.get("base_url", "")
        try:
            response = client.get(base_url, timeout=10)
            if response.status_code == 200:
                log.info("Test connexion OK", brand=self._config.get("slug"), url=base_url)
                return ConnectionStatus.OK
            elif response.status_code in (403, 429):
                return ConnectionStatus.BLOCKED
            else:
                return ConnectionStatus.FAILED
        except Exception as exc:
            log.warning("Test connexion échoué", error=str(exc))
            return ConnectionStatus.TIMEOUT

    # -------------------------------------------------------------------
    # Accesseurs config
    # -------------------------------------------------------------------

    @property
    def config(self) -> dict:
        return self._config

    @property
    def base_url(self) -> str:
        return self._config.get("base_url", "")

    @property
    def rate_limit_rps(self) -> float:
        return float(self._config.get("rate_limit_rps", 0.5))

    @property
    def delay_min(self) -> float:
        return float(self._config.get("delay_min", 1.5))

    @property
    def delay_max(self) -> float:
        return float(self._config.get("delay_max", 4.0))

    # -------------------------------------------------------------------
    # Méthodes internes
    # -------------------------------------------------------------------

    def _load_config(self) -> None:
        """Charge le config.yml du connecteur."""
        if not self._config_path or not self._config_path.exists():
            raise ConnectorConfigError(
                "Fichier config.yml introuvable",
                context={"path": str(self._config_path)},
            )
        with open(self._config_path, encoding="utf-8") as f:
            self._config = yaml.safe_load(f) or {}
        log.debug(
            "Config connecteur chargée",
            slug=self._config.get("slug"),
            engine=self._config.get("engine"),
        )

    def _find_config(self) -> Path:
        """Cherche config.yml dans le dossier de la classe concrète."""
        # Le connecteur concret est dans un sous-dossier de app/connectors/
        connector_dir = Path(self.__class__.__module__.replace(".", "/")).parent
        # Fallback : cherche depuis la racine du projet
        project_root = Path(__file__).resolve().parents[2]
        candidate = project_root / connector_dir / "config.yml"
        if not candidate.exists():
            # Essai via __file__ de la sous-classe
            import inspect
            subclass_file = inspect.getfile(self.__class__)
            candidate = Path(subclass_file).parent / "config.yml"
        return candidate

    def _fetch_and_parse(self, url: str) -> RawProduct | None:
        """
        Récupère et parse un produit.
        Délégué à l'implémentation concrète via parse_product().
        En mode shopify_json, l'URL est celle d'un endpoint JSON.
        """
        from app.scraping.http_client import HttpClient
        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
        )
        engine = self._config.get("engine", "html")

        if engine == "shopify_json":
            # L'URL est déjà une URL JSON produit (/products/<handle>.json)
            response = client.get(url)
            if response.status_code != 200:
                return None
            data = response.json().get("product", response.json())
            return self.parse_product(url, data)
        else:
            response = client.get(url)
            if response.status_code != 200:
                return None
            return self.parse_product(url, response.text)

    def _polite_delay(self) -> None:
        """Attend un délai aléatoire entre delay_min et delay_max secondes."""
        delay = random.uniform(self.delay_min, self.delay_max)
        time.sleep(delay)