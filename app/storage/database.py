"""
Database — Factory de sessions SQLAlchemy pour SQLite + PySide6 QThread.

Le fichier DB est placé dans ~/.local/share/shapewear-me/ (hors du dossier
projet) pour éviter les conflits avec les outils de sauvegarde (SyncBack,
Dropbox, etc.) qui verrouillent les fichiers .db dans les dossiers surveillés.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, pool, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.exceptions import DatabaseError
from app.core.logger import get_logger
from app.storage.models import Base

log = get_logger(__name__)

_engine = None
_engine_lock = threading.Lock()
_active_db_url: str | None = None


def _resolve_db_url() -> str:
    """
    Retourne l'URL SQLite à utiliser.

    Priorité :
      1. DATABASE_URL dans .env si elle ne pointe pas dans le dossier projet
      2. ~/.local/share/shapewear-me/shapewear.db  (hors de tout outil de sauvegarde)
      3. Fallback : /tmp/shapewear.db
    """
    configured = settings.DATABASE_URL

    # Si l'URL pointe vers un fichier, vérifier que son dossier n'est pas surveillé
    if configured.startswith("sqlite:///"):
        db_path = Path(configured.replace("sqlite:///", ""))
        # Chemin absolu
        if not db_path.is_absolute():
            db_path = Path(os.getcwd()) / db_path

        # Essayer ce chemin en premier
        if _can_lock(db_path):
            return f"sqlite:///{db_path}"

        log.warning(
            "Le fichier DB configuré est verrouillé ou dans un dossier surveillé",
            path=str(db_path),
            action="Bascule vers ~/.local/share/shapewear-me/",
        )

    # Chemin utilisateur hors projet (non surveillé par SyncBack / Dropbox / etc.)
    user_data_dir = Path.home() / ".local" / "share" / "shapewear-me"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    user_db = user_data_dir / "shapewear.db"

    if _can_lock(user_db):
        log.info("Base de données dans le répertoire utilisateur", path=str(user_db))
        return f"sqlite:///{user_db}"

    # Dernier recours : /tmp
    tmp_db = Path("/tmp/shapewear.db")
    log.warning("Utilisation de /tmp/shapewear.db (non persistant entre reboots)")
    return f"sqlite:///{tmp_db}"


def _can_lock(db_path: Path) -> bool:
    """Teste si on peut verrouiller exclusivement ce fichier SQLite."""
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=3)
        conn.execute("BEGIN EXCLUSIVE")
        conn.rollback()
        conn.close()
        return True
    except (sqlite3.OperationalError, OSError):
        return False


def _apply_pragmas(db_path: str) -> None:
    """Applique les PRAGMAs SQLite optimaux."""
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.commit()
        conn.close()
        log.info("PRAGMAs SQLite appliqués (WAL)", db=db_path)
    except sqlite3.OperationalError as exc:
        log.warning("PRAGMAs non appliqués — mode DELETE", error=str(exc))


def _create_engine_for_url(db_url: str):
    return create_engine(
        db_url,
        connect_args={"check_same_thread": False, "timeout": 60},
        poolclass=pool.NullPool,
        echo=False,
        pool_pre_ping=False,
    )


def get_engine():
    global _engine, _active_db_url
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _active_db_url = _resolve_db_url()
                if _active_db_url.startswith("sqlite:///"):
                    db_path = _active_db_url.replace("sqlite:///", "")
                    _apply_pragmas(db_path)
                _engine = _create_engine_for_url(_active_db_url)
    return _engine


def get_active_db_url() -> str | None:
    return _active_db_url


@contextmanager
def get_db() -> Generator[Session, None, None]:
    factory = sessionmaker(
        bind=get_engine(),
        autoflush=True,
        autocommit=False,
        expire_on_commit=False,
    )
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception as exc:
        try:
            session.rollback()
        except Exception:
            pass
        log.error("Erreur base de données — rollback", error=str(exc))
        raise DatabaseError(
            f"Erreur DB : {exc}",
            context={"type": type(exc).__name__},
        ) from exc
    finally:
        try:
            session.close()
        except Exception:
            pass


def init_db() -> None:
    """Résout le chemin DB, applique les PRAGMAs, crée les tables."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    log.info("Base de données initialisée", url=_active_db_url)


def check_db_connection() -> bool:
    try:
        with get_db() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.error("Connexion base échouée", error=str(exc))
        return False


def dispose_engine() -> None:
    global _engine, _active_db_url
    if _engine:
        _engine.dispose()
        _engine = None
    _active_db_url = None