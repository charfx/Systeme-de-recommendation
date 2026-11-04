"""Collecte des interactions livres : enregistrement et état courant.

C'est ici que les « faits » bruts sont écrits dans `book_events`, et relus
pour reconstruire l'état d'un livre pour une session.

Étape suivante (profil TF-IDF) : lire les événements avec `fetch_events()`
ou l'état avec `compute_book_states()`, puis les convertir en poids. Rien
dans ce module ne calcule de poids : on conserve les événements tels quels.
"""

from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from Books_recommendation.db import to_iso_utc, utc_now

EVENT_TYPES = ("open", "like", "unlike", "dismiss", "undismiss")


class BookNotFound(Exception):
    pass


class RequestIdConflict(Exception):
    """Le même request_id a déjà servi pour une autre action."""


@dataclass
class BookState:
    book_id: str
    liked: bool = False
    dismissed: bool = False
    open_count: int = 0
    last_event_id: int | None = None

    def apply(self, event_type: str, event_id: int) -> None:
        """Règles de transition, appliquées dans l'ordre croissant de event_id.

        - like     : aimé, et annule un éventuel rejet ;
        - unlike   : n'est plus aimé ;
        - dismiss  : rejeté, et annule un éventuel like ;
        - undismiss: n'est plus rejeté ;
        - open     : n'influence ni like ni rejet (simple ouverture de fiche).
        Un livre ne peut donc jamais être à la fois aimé et rejeté.
        """
        if event_type == "like":
            self.liked, self.dismissed = True, False
        elif event_type == "unlike":
            self.liked = False
        elif event_type == "dismiss":
            self.dismissed, self.liked = True, False
        elif event_type == "undismiss":
            self.dismissed = False
        elif event_type == "open":
            self.open_count += 1
        self.last_event_id = event_id

    def to_dict(self) -> dict:
        return {
            "book_id": self.book_id,
            "liked": self.liked,
            "dismissed": self.dismissed,
            "open_count": self.open_count,
        }


def fetch_events(conn: Connection, session_id: str, book_ids=None) -> list[dict]:
    """Événements bruts d'une session, triés par event_id (ordre déterministe)."""
    sql = """
        SELECT event_id, request_id, book_id, event_type, created_at
        FROM book_events
        WHERE session_id = :session_id
    """
    params = {"session_id": session_id}
    if book_ids is not None:
        book_ids = list(book_ids)
        if not book_ids:
            return []
        sql += " AND book_id IN :book_ids"
        params["book_ids"] = book_ids
    sql += " ORDER BY event_id"
    stmt = text(sql)
    if book_ids is not None:
        stmt = stmt.bindparams(bindparam("book_ids", expanding=True))
    rows = conn.execute(stmt, params).mappings().all()
    return [
        {**row, "created_at": to_iso_utc(row["created_at"])}
        for row in rows
    ]


def compute_book_states(conn: Connection, session_id: str, book_ids=None) -> dict[str, BookState]:
    """Rejoue les événements dans l'ordre event_id pour obtenir l'état de chaque livre.

    Les livres sans événement ne figurent pas dans le résultat (état par défaut).
    """
    states: dict[str, BookState] = {}
    for event in fetch_events(conn, session_id, book_ids):
        state = states.setdefault(event["book_id"], BookState(event["book_id"]))
        state.apply(event["event_type"], event["event_id"])
    return states


def state_for(conn: Connection, session_id: str, book_id: str) -> BookState:
    return compute_book_states(conn, session_id, [book_id]).get(book_id, BookState(book_id))


def dismissed_book_ids(conn: Connection, session_id: str) -> set[str]:
    return {b for b, s in compute_book_states(conn, session_id).items() if s.dismissed}


def record_book_event(
    conn: Connection,
    session_id: str,
    book_id: str,
    event_type: str,
    request_id: str,
) -> tuple[BookState, bool]:
    """Écrit UNE ligne dans book_events (idempotent grâce à request_id).

    Retourne (état courant du livre, duplicate). `duplicate` vaut True si ce
    request_id avait déjà été enregistré : c'est une reprise réseau, on ne
    crée pas de deuxième ligne. À appeler dans une transaction.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"event_type invalide : {event_type!r}")

    exists = conn.execute(
        text("SELECT 1 FROM books WHERE book_id = :book_id"),
        {"book_id": book_id},
    ).first()
    if exists is None:
        raise BookNotFound(book_id)

    original = _find_by_request_id(conn, session_id, request_id)
    duplicate = original is not None
    if not duplicate:
        try:
            # SAVEPOINT : un échec de cet INSERT n'annule pas la transaction.
            with conn.begin_nested():
                conn.execute(
                    text("""
                        INSERT INTO book_events
                            (request_id, session_id, book_id, event_type, created_at)
                        VALUES
                            (:request_id, :session_id, :book_id, :event_type, :created_at)
                    """),
                    {
                        "request_id": request_id,
                        "session_id": session_id,
                        "book_id": book_id,
                        "event_type": event_type,
                        "created_at": utc_now(),
                    },
                )
        except IntegrityError:
            # Deux envois simultanés du même request_id : la contrainte
            # UNIQUE (session_id, request_id) a bloqué le second.
            # Lecture verrouillante : en REPEATABLE READ, une lecture simple
            # utiliserait l'instantané de la transaction, qui ne contient pas
            # encore la ligne validée par l'autre requête.
            original = _find_by_request_id(conn, session_id, request_id, latest=True)
            if original is None:
                raise
            duplicate = True

    if duplicate and (original["book_id"], original["event_type"]) != (book_id, event_type):
        raise RequestIdConflict(request_id)

    return state_for(conn, session_id, book_id), duplicate


def _find_by_request_id(conn: Connection, session_id: str, request_id: str, latest: bool = False):
    sql = """
        SELECT book_id, event_type FROM book_events
        WHERE session_id = :session_id AND request_id = :request_id
    """
    if latest:
        sql += " LOCK IN SHARE MODE"  # lit la dernière version validée
    return conn.execute(
        text(sql),
        {"session_id": session_id, "request_id": request_id},
    ).mappings().first()
