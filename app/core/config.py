"""
Configuration centrale de l'application.
Charge les valeurs par défaut puis écrase avec settings.json si présent.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Racine du projet (deux niveaux au-dessus de ce fichier)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Paramètres globaux de l'application."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Base de données ---
    # Par défaut dans ~/.local/share/shapewear-me/ pour éviter les conflits
    # avec les outils de sauvegarde (SyncBack, Dropbox, etc.) qui verrouillent
    # les fichiers .db dans les dossiers projet surveillés.
    # Surcharger dans .env : DATABASE_URL=sqlite:////chemin/vers/shapewear.db
    DATABASE_URL: str = (
        'sqlite:///' + str(Path.home() / '.local' / 'share' / 'shapewear-me' / 'shapewear.db')
    )

    # --- Répertoires ---
    DATA_DIR: Path = PROJECT_ROOT / "data"
    LOG_DIR: Path = PROJECT_ROOT / "data" / "logs"
    EXPORT_DIR: Path = PROJECT_ROOT / "data" / "exports"
    TAXONOMIES_DIR: Path = PROJECT_ROOT / "taxonomies"

    # --- Scraping ---
    MAX_WORKERS: int = 2
    DEFAULT_TIMEOUT_CONNECT: int = 10   # secondes
    DEFAULT_TIMEOUT_READ: int = 30
    DEFAULT_RETRY_COUNT: int = 3
    DEFAULT_RETRY_DELAY: float = 2.0    # secondes (doublé à chaque échec)

    # --- Application ---
    APP_NAME: str = "Market Intelligence Platform — Shapewear US"
    APP_VERSION: str = "0.1.0"
    ENV: str = "dev"                    # dev | prod
    LOG_LEVEL: str = "INFO"

    # --- Export ---
    PROXY_URL: str = ""
    ANTHROPIC_API_KEY: str = ""

    def __init__(self, **data):
        super().__init__(**data)
        # Charger un éventuel settings.json utilisateur
        user_settings_path = PROJECT_ROOT / "settings.json"
        if user_settings_path.exists():
            with open(user_settings_path, encoding="utf-8") as f:
                user_data = json.load(f)
            for key, value in user_data.items():
                if hasattr(self, key):
                    object.__setattr__(self, key, value)
        # Créer les répertoires nécessaires
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def is_dev(self) -> bool:
        return self.ENV == "dev"

    @property
    def is_prod(self) -> bool:
        return self.ENV == "prod"


# Instance unique partagée dans toute l'application
settings = Settings()