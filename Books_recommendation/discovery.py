"""Tirage aléatoire de livres « à découvrir » (pas encore de recommandation).

Une sélection est un lot de livres SERVIS à la page (positions 1..5), stocké
dans book_selections / book_selection_items. Cela ne prouve pas que
l'utilisateur les a lus ni même vus : seuls les événements comptent.
"""

import random
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from Books_recommendation.catalog import all_book_ids, get_cards
from Books_recommendation.db import to_iso_utc, utc_now
from Books_recommendation.interactions import compute_book_states, dismissed_book_ids
from Books_recommendation.sessions import lock_session

SELECTION_SIZE = 5
_rng = random.SystemRandom()


def choose_books(
    catalog: list[str],
    already_shown: set[str],
    dismissed: set[str],
    previous: set[str],
    size: int = SELECTION_SIZE,
    rng: random.Random = _rng,
) -> list[str]:
    """Choisit jusqu'à `size` book_id distincts, par ordre de préférence :

    1. livres jamais proposés à cette session, hors rejetés ;
    2. sinon, livres déjà proposés, hors rejetés et hors sélection précédente ;
    3. en dernier recours, livres de la sélection précédente non rejetés.
    Si moins de `size` livres sont disponibles, on retourne ceux qui existent.
    """
    eligible = [b for b in catalog if b not in dismissed]
    tiers = [
        [b for b in eligible if b not in already_shown],
        [b for b in eligible if b in already_shown and b not in previous],
        [b for b in eligible if b in already_shown and b in previous],
    ]
    chosen: list[str] = []
    for tier in tiers:
        missing = size - len(chosen)
        if missing <= 0:
            break
        chosen.extend(rng.sample(tier, min(missing, len(tier))))
    return chosen


def _latest_selection_id(conn: Connection, session_id: str) -> str | None:
    return conn.execute(
        text("""
            SELECT selection_id FROM book_selections
            WHERE session_id = :sid
            ORDER BY created_at DESC, selection_id DESC
            LIMIT 1
        """),
        {"sid": session_id},
    ).scalar()


def _selection_book_ids(conn: Connection, selection_id: str) -> list[str]:
    return list(conn.execute(
        text("""
            SELECT book_id FROM book_selection_items
            WHERE selection_id = :sel ORDER BY position
        """),
        {"sel": selection_id},
    ).scalars())


def _create_selection(conn: Connection, session_id: str) -> str:
    previous_id = _latest_selection_id(conn, session_id)
    previous = set(_selection_book_ids(conn, previous_id)) if previous_id else set()
    already_shown = set(conn.execute(
        text("""
            SELECT DISTINCT i.book_id
            FROM book_selection_items i
            JOIN book_selections s ON s.selection_id = i.selection_id
            WHERE s.session_id = :sid
        """),
        {"sid": session_id},
    ).scalars())

    book_ids = choose_books(
        catalog=all_book_ids(conn),
        already_shown=already_shown,
        dismissed=dismissed_book_ids(conn, session_id),
        previous=previous,
    )

    selection_id = str(uuid.uuid4())
    conn.execute(
        text("""
            INSERT INTO book_selections (selection_id, session_id, created_at)
            VALUES (:sel, :sid, :now)
        """),
        {"sel": selection_id, "sid": session_id, "now": utc_now()},
    )
    if book_ids:
        conn.execute(
            text("""
                INSERT INTO book_selection_items (selection_id, book_id, position)
                VALUES (:sel, :book_id, :position)
            """),
            [
                {"sel": selection_id, "book_id": book_id, "position": position}
                for position, book_id in enumerate(book_ids, start=1)
            ],
        )
    return selection_id


def get_current_selection(conn: Connection, session_id: str) -> str:
    """Dernière sélection de la session ; la crée seulement si aucune n'existe.

    Une actualisation de la page ne refait donc pas de tirage.
    """
    lock_session(conn, session_id)
    return _latest_selection_id(conn, session_id) or _create_selection(conn, session_id)


def refresh_selection(conn: Connection, session_id: str) -> str:
    """Bouton « Découvrir d'autres livres » : nouveau tirage."""
    lock_session(conn, session_id)
    return _create_selection(conn, session_id)


def selection_payload(conn: Connection, session_id: str, selection_id: str) -> dict:
    created_at = conn.execute(
        text("SELECT created_at FROM book_selections WHERE selection_id = :sel"),
        {"sel": selection_id},
    ).scalar_one()
    book_ids = _selection_book_ids(conn, selection_id)
    cards = get_cards(conn, book_ids)
    states = compute_book_states(conn, session_id, book_ids)

    items = []
    for position, book_id in enumerate(book_ids, start=1):
        state = states.get(book_id)
        items.append({
            "position": position,
            **cards[book_id],
            "state": {
                "liked": bool(state and state.liked),
                "dismissed": bool(state and state.dismissed),
            },
        })
    return {
        "selection_id": selection_id,
        "created_at": to_iso_utc(created_at),
        "requested_size": SELECTION_SIZE,
        "count": len(items),
        "items": items,
    }
