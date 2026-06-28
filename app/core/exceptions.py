"""
Hiérarchie d'exceptions métier de l'application.

MarketIntelException (base)
├── ConnectorException
│   ├── ConnectorConfigError
│   ├── ConnectorParseError
│   └── ConnectorBlockedError
├── ScrapingException
│   ├── RateLimitError
│   ├── NetworkError
│   └── PaginationError
├── ProcessingException
│   ├── NormalizationError
│   └── ClassificationError
└── StorageException
    ├── DatabaseError
    └── MigrationError
"""


class MarketIntelException(Exception):
    """Exception de base pour toutes les erreurs de l'application."""

    def __init__(self, message: str = "", context: dict | None = None):
        super().__init__(message)
        self.context: dict = context or {}

    def __str__(self) -> str:
        base = super().__str__()
        if self.context:
            ctx = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{base} [{ctx}]"
        return base


# ---------------------------------------------------------------------------
# Connecteurs
# ---------------------------------------------------------------------------

class ConnectorException(MarketIntelException):
    """Erreur liée à un connecteur de marque."""


class ConnectorConfigError(ConnectorException):
    """Le fichier config.yml du connecteur est invalide ou incomplet."""


class ConnectorParseError(ConnectorException):
    """Erreur lors du parsing d'une page ou d'un objet JSON produit."""


class ConnectorBlockedError(ConnectorException):
    """Le connecteur a été bloqué par le site (403, 429, CAPTCHA)."""


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

class ScrapingException(MarketIntelException):
    """Erreur liée au moteur de scraping."""


class RateLimitError(ScrapingException):
    """Trop de requêtes — code 429 reçu."""


class NetworkError(ScrapingException):
    """Erreur réseau (timeout, DNS, connexion refusée)."""


class PaginationError(ScrapingException):
    """Impossible de naviguer dans la pagination."""


# ---------------------------------------------------------------------------
# Traitement
# ---------------------------------------------------------------------------

class ProcessingException(MarketIntelException):
    """Erreur dans le pipeline de traitement."""


class NormalizationError(ProcessingException):
    """Erreur lors de la normalisation d'un RawProduct."""


class ClassificationError(ProcessingException):
    """Erreur lors de la classification taxonomique."""


# ---------------------------------------------------------------------------
# Stockage
# ---------------------------------------------------------------------------

class StorageException(MarketIntelException):
    """Erreur liée à la persistance des données."""


class DatabaseError(StorageException):
    """Erreur SQLAlchemy ou SQL."""


class MigrationError(StorageException):
    """Erreur lors d'une migration Alembic."""