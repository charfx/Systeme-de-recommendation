"""Routes HTTP du module livres (FastAPI APIRouter, inclus dans l'app principale).

    GET  /books                          page HTML
    GET  /api/books/discovery            sélection courante (créée si absente)
    POST /api/books/discovery/refresh    nouveau tirage
    GET  /api/books/recommendations      recommandations TF-IDF de la session
    GET  /api/books?query=&page=&page_size=   recherche par titre
    GET  /api/books/{book_id}            fiche complète + état pour la session
    POST /api/book-events                enregistre une interaction (open, like…)

Les routes fixes (« /api/books/discovery… », « /api/books/recommendations »)
sont déclarées AVANT « /api/books/{book_id} » : sinon « discovery » ou
« recommendations » serait pris pour un book_id.
"""

import logging
import uuid
from contextlib import contextmanager
from pathlib import Path
from functools import lru_cache
from typing import Literal

import pymysql
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import DBAPIError

from Books_recommendation import catalog, discovery
from Books_recommendation.db import get_engine
from Books_recommendation.interactions import (
    EVENT_TYPES,
    BookNotFound,
    RequestIdConflict,
    record_book_event,
    state_for,
)
from Books_recommendation.sessions import COOKIE_NAME, ensure_session, set_session_cookie

BOOKS_STATIC_DIR = Path(__file__).resolve().parent / "static"

logger = logging.getLogger("books_web")
router = APIRouter()


@contextmanager
def transaction():
    """Une transaction par requête ; erreurs MySQL -> 503 sans détails sensibles."""
    try:
        with get_engine().begin() as conn:
            yield conn
    except DBAPIError:
        logger.exception("Erreur base de données (module livres)")
        raise HTTPException(
            status_code=503,
            detail="Base de données indisponible. Vérifiez que MySQL (XAMPP) est lancé "
                   "et que la configuration .env est correcte.",
        ) from None


def current_session(conn, request: Request, response: Response) -> str:
    """Session déterminée par le cookie HttpOnly, jamais par le corps de la requête."""
    session_id, _created = ensure_session(conn, request.cookies.get(COOKIE_NAME))
    set_session_cookie(response, request, session_id)  # rafraîchit l'expiration
    return session_id


# ---------------------------------------------------------------- page HTML

@router.get("/books", include_in_schema=False)
def books_page():
    return FileResponse(BOOKS_STATIC_DIR / "books.html")


# ------------------------------------------------- découverte (routes fixes)

@router.get("/api/books/discovery")
def get_discovery(request: Request, response: Response):
    with transaction() as conn:
        session_id = current_session(conn, request, response)
        selection_id = discovery.get_current_selection(conn, session_id)
        return discovery.selection_payload(conn, session_id, selection_id)


@router.post("/api/books/discovery/refresh")
def refresh_discovery(request: Request, response: Response):
    with transaction() as conn:
        session_id = current_session(conn, request, response)
        selection_id = discovery.refresh_selection(conn, session_id)
        return discovery.selection_payload(conn, session_id, selection_id)


# ----------------------------------------------- recommandations TF-IDF

@lru_cache(maxsize=1)
def get_book_recommender():
    """Chargé une seule fois, à la première requête (pas au démarrage).

    Si le chargement échoue, rien n'est mis en cache : on réessaiera.
    """
    from Books_recommendation.Book_recommender import BookRecommender
    return BookRecommender()


@router.get("/api/books/recommendations")
def get_book_recommendations(
    request: Request,
    top_k: int = Query(4, ge=1, le=50),
):
    session_id = request.cookies.get(COOKIE_NAME)  # "books_session"
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Session absente. Chargez d'abord la page livres.",
        )

    try:
        recommender = get_book_recommender()
    except (FileNotFoundError, ValueError):
        logger.exception("Chargement du modèle TF-IDF impossible")
        raise HTTPException(
            status_code=503,
            detail="Modèle de recommandation indisponible.",
        ) from None

    try:
        result = recommender.recommend(session_id=session_id, top_k=top_k)
    except ValueError as exc:  # session inconnue, top_k invalide
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except pymysql.MySQLError:
        logger.exception("Erreur MySQL pendant la recommandation")
        raise HTTPException(
            status_code=503,
            detail="Base de données indisponible.",
        ) from None

    # Même format que les autres cartes de l'API (listes, image en HTTPS).
    # La description complète n'est pas utile ici : la fiche la recharge.
    for book in result["recommendations"]:
        book["authors"] = catalog.split_list(book.get("authors"))
        book["categories"] = catalog.split_list(book.get("categories"))
        book["thumbnail"] = catalog.safe_thumbnail(book.get("thumbnail"))
        book.pop("description", None)
    return result


# ------------------------------------------------------------- catalogue

@router.get("/api/books")
def search_books(
    query: str = Query("", max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    with transaction() as conn:
        return catalog.search_books(conn, query, page, page_size)


@router.get("/api/books/{book_id}")
def get_book(book_id: str, request: Request, response: Response):
    with transaction() as conn:
        book = catalog.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Livre introuvable.")
        session_id = current_session(conn, request, response)
        state = state_for(conn, session_id, book_id)
        return {**book, "state": {"liked": state.liked, "dismissed": state.dismissed}}


# ----------------------------------------------------------- interactions

class BookEventIn(BaseModel):
    """Corps attendu : {book_id, event_type, request_id}. Rien d'autre.

    extra="forbid" : un session_id envoyé par le client est refusé (422).
    """
    model_config = ConfigDict(extra="forbid")

    book_id: str = Field(min_length=1, max_length=20)
    event_type: Literal[EVENT_TYPES]  # open, like, unlike, dismiss, undismiss
    request_id: uuid.UUID


@router.post("/api/book-events")
def post_book_event(event: BookEventIn, request: Request, response: Response):
    """Reçoit une interaction envoyée par sendBookEvent() (books.js)."""
    with transaction() as conn:
        session_id = current_session(conn, request, response)
        try:
            state, duplicate = record_book_event(
                conn,
                session_id=session_id,
                book_id=event.book_id,
                event_type=event.event_type,
                request_id=str(event.request_id),
            )
        except BookNotFound:
            raise HTTPException(status_code=404, detail="Livre introuvable.") from None
        except RequestIdConflict:
            raise HTTPException(
                status_code=409,
                detail="Ce request_id a déjà été utilisé pour une autre action.",
            ) from None

        return {
            "request_id": str(event.request_id),
            "book_id": event.book_id,
            "event_type": event.event_type,
            "duplicate": duplicate,  # True = reprise réseau, aucune ligne ajoutée
            "state": {"liked": state.liked, "dismissed": state.dismissed},
        }
