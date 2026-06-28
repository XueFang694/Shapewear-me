"""Alembic Environment — configuration des migrations SQLAlchemy."""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Importer les modèles pour que Alembic les détecte
from app.storage.models import Base
from app.core.config import settings

# Objet de configuration Alembic (alembic.ini)
config = context.config

# Configurer le logging via alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Métadonnées cibles pour les migrations auto-générées
target_metadata = Base.metadata

# Utiliser la DATABASE_URL de nos settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def run_migrations_offline() -> None:
    """Mode offline : génère le SQL sans connexion."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Mode online : exécute les migrations sur la base."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()