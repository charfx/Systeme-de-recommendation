"""Lecture du catalogue `books` : fiche complète, cartes et recherche par titre."""

from urllib.parse import urlsplit

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

CARD_COLUMNS = "book_id, title, subtitle, authors, categories, thumbnail, published_year"
DETAIL_COLUMNS = (
    "book_id, isbn10, title, subtitle, authors, categories, thumbnail, description, "
    "published_year, average_rating, num_pages, ratings_count"
)


def _split_list(value: str | None) -> list[str]:
    """« Charles Osborne;Agatha Christie » -> ["Charles Osborne", "Agatha Christie"]."""
    if not value:
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def _safe_thumbnail(url: str | None) -> str | None:
    """N'accepte que des URL http(s) ; passe Google Books en HTTPS."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.scheme == "http" and parts.netloc.endswith("books.google.com"):
        return "https://" + url.strip()[len("http://"):]
    return url.strip()


def serialize_card(row) -> dict:
    return {
        "book_id": row["book_id"],
        "title": row["title"],
        "subtitle": row["subtitle"],
        "authors": _split_list(row["authors"]),
        "categories": _split_list(row["categories"]),
        "thumbnail": _safe_thumbnail(row["thumbnail"]),
        "published_year": row["published_year"],
    }


def serialize_detail(row) -> dict:
    card = serialize_card(row)
    rating = row["average_rating"]
    return {
        **card,
        "isbn10": row["isbn10"],
        "isbn13": row["book_id"],
        "description": row["description"],  # texte brut, affiché avec textContent
        "average_rating": float(rating) if rating is not None else None,
        "num_pages": row["num_pages"],
        "ratings_count": row["ratings_count"],
    }


def get_book(conn: Connection, book_id: str) -> dict | None:
    row = conn.execute(
        text(f"SELECT {DETAIL_COLUMNS} FROM books WHERE book_id = :book_id"),
        {"book_id": book_id},
    ).mappings().first()
    return serialize_detail(row) if row else None


def get_cards(conn: Connection, book_ids: list[str]) -> dict[str, dict]:
    if not book_ids:
        return {}
    rows = conn.execute(
        text(f"SELECT {CARD_COLUMNS} FROM books WHERE book_id IN :ids")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": list(book_ids)},
    ).mappings().all()
    return {row["book_id"]: serialize_card(row) for row in rows}


def all_book_ids(conn: Connection) -> list[str]:
    return list(conn.execute(text("SELECT book_id FROM books")).scalars())


def _like_pattern(query: str) -> str:
    # « ! » sert de caractère d'échappement : % et _ tapés par l'utilisateur
    # sont cherchés littéralement.
    escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def search_books(conn: Connection, query: str, page: int, page_size: int) -> dict:
    query = (query or "").strip()
    where, params = "", {}
    if query:
        # Collation utf8mb4_unicode_ci : insensible à la casse et aux accents.
        where = "WHERE title LIKE :pattern ESCAPE '!'"
        params["pattern"] = _like_pattern(query)

    total = conn.execute(text(f"SELECT COUNT(*) FROM books {where}"), params).scalar_one()
    rows = conn.execute(
        text(f"""
            SELECT {CARD_COLUMNS} FROM books {where}
            ORDER BY title, book_id
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    ).mappings().all()

    return {
        "query": query,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, -(-total // page_size)),
        "items": [serialize_card(row) for row in rows],
    }
