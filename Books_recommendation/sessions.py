"""Session anonyme du module livres, identifiée par un cookie HttpOnly.

Le backend est le seul à créer et lire l'identifiant de session : le frontend
ne l'envoie jamais dans le corps d'une requête (il ne peut même pas le lire).
"""

import os
import uuid

from fastapi import Request, Response
from sqlalchemy import text
from sqlalchemy.engine import Connection

from Books_recommendation.db import utc_now

COOKIE_NAME = "books_session"
COOKIE_MAX_AGE = 365 * 24 * 3600  # un an : la session survit aux actualisations


def _parse_uuid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def ensure_session(conn: Connection, cookie_value: str | None) -> tuple[str, bool]:
    """Retourne (session_id, créée). Crée la session si le cookie est absent ou inconnu.

    Un cookie inconnu de la base (base réinitialisée, valeur forgée) n'est pas
    réutilisé : on génère un nouvel UUID côté serveur.
    """
    session_id = _parse_uuid(cookie_value)
    now = utc_now()
    if session_id is not None:
        found = conn.execute(
            text("SELECT 1 FROM book_sessions WHERE session_id = :sid"), {"sid": session_id}
        ).first()
        if found:
            conn.execute(
                text("UPDATE book_sessions SET last_seen_at = :now WHERE session_id = :sid"),
                {"now": now, "sid": session_id},
            )
            return session_id, False

    session_id = str(uuid.uuid4())
    conn.execute(
        text("""
            INSERT INTO book_sessions (session_id, created_at, last_seen_at)
            VALUES (:sid, :now, :now)
        """),
        {"sid": session_id, "now": now},
    )
    return session_id, True


def lock_session(conn: Connection, session_id: str) -> None:
    """Verrou de ligne : sérialise les tirages concurrents d'une même session."""
    conn.execute(
        text("SELECT session_id FROM book_sessions WHERE session_id = :sid FOR UPDATE"),
        {"sid": session_id},
    )


def _cookie_secure(request: Request) -> bool:
    """BOOKS_COOKIE_SECURE = auto (défaut) | true | false.

    En « auto », Secure est activé si la requête arrive en HTTPS (directement
    ou derrière un proxy), et désactivé en développement sur http://127.0.0.1.
    """
    mode = os.environ.get("BOOKS_COOKIE_SECURE", "auto").strip().lower()
    if mode in ("1", "true", "yes"):
        return True
    if mode in ("0", "false", "no"):
        return False
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"


def set_session_cookie(response: Response, request: Request, session_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        session_id,
        max_age=COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )
