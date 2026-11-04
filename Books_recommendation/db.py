"""Accès à la base MySQL/MariaDB du module livres (SQLAlchemy + PyMySQL).

La configuration vient des variables d'environnement, éventuellement lues
depuis le fichier `.env` à la racine du projet (voir `.env.example`) :

    BOOKS_DB_HOST, BOOKS_DB_PORT, BOOKS_DB_USER, BOOKS_DB_PASSWORD, BOOKS_DB_NAME

Les identifiants restent côté serveur : ils ne sont jamais envoyés au frontend.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOOKS_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG_CSV = BOOKS_DIR / "data" / "books_clean.csv"

# Les variables déjà définies dans l'environnement restent prioritaires.
load_dotenv(PROJECT_ROOT / ".env", override=False)

_engine: Engine | None = None


def database_url() -> URL:
    return URL.create(
        "mysql+pymysql",
        username=os.environ.get("BOOKS_DB_USER", "root"),
        password=os.environ.get("BOOKS_DB_PASSWORD", "") or None,
        host=os.environ.get("BOOKS_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("BOOKS_DB_PORT", "3306")),
        database=os.environ.get("BOOKS_DB_NAME", "recommendation_mvp"),
        query={"charset": "utf8mb4"},
    )


def get_engine() -> Engine:
    """Moteur partagé, créé à la première utilisation (pas au démarrage).

    Ainsi la page films fonctionne même si MySQL n'est pas lancé.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            database_url(),
            pool_pre_ping=True,  # reconnexion si XAMPP a coupé la connexion
            pool_recycle=3600,
            # Toutes les dates sont écrites en UTC : on aligne la session SQL.
            connect_args={"init_command": "SET time_zone = '+00:00'"},
        )
    return _engine


def utc_now() -> datetime:
    """Heure UTC sans fuseau, format attendu par les colonnes DATETIME(6)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
