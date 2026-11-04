"""Application web de démonstration : Top 10 global du modèle LightGBM LambdaRank.

Lancement (depuis la racine du projet) :
    .venv\\Scripts\\python.exe -m uvicorn Movies_recommendation.web.app:app --port 8000

Le modèle et les CSV sont chargés une seule fois au démarrage, et le Top 10
est pré-calculé et conservé en mémoire. Les requêtes ne font que le relire.
"""


# pour lancer l'app : .venv\Scripts\python.exe -m uvicorn Movies_recommendation.web.app:app --port 8000
import logging
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from Books_recommendation.web.api import BOOKS_STATIC_DIR
from Books_recommendation.web.api import router as books_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
TOP_K = 10

logger = logging.getLogger("movies_web")

# État calculé au démarrage : soit un résultat, soit un message d'erreur.
STATE: dict = {"payload": None, "error": None}


def _clean_float(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _clean_text(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return str(value)


def _parse_genres(raw):
    raw = _clean_text(raw)
    if not raw or raw == "(no genres listed)":
        return []
    return [g for g in raw.split("|") if g]


def build_payload(reference_date, recommendations, movies) -> dict:
    """Convertit la sortie du pipeline en JSON standard (pas de NaN/Infinity)."""
    genres_by_id = movies.set_index("movieId")["genres"].to_dict()
    items = []
    for rank, rec in enumerate(recommendations, start=1):
        movie_id = int(rec["movieId"])
        items.append({
            "rank": rank,
            "movieId": movie_id,
            "title": _clean_text(rec.get("title")),
            "score": _clean_float(rec["score"]),
            "genres": _parse_genres(genres_by_id.get(movie_id)),
        })
    return {
        "reference_date": pd.Timestamp(reference_date).isoformat(),
        "ranking_type": "global",
        "count": len(items),
        "recommendations": items,
    }


def compute_top_recommendations(data_dir=None) -> dict:
    # Import ici : le chargement de l'artefact (model_generalise) peut échouer
    # et on veut alors afficher l'erreur proprement plutôt que planter.
    from Movies_recommendation import test as pipeline

    ratings, movies = pipeline.load_data(data_dir or pipeline.DATA_DIR)
    reference_date, recommendations = pipeline.get_recommendations(
        ratings, movies, top_k=TOP_K
    )
    return build_payload(reference_date, recommendations, movies)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # MOVIES_DATA_DIR permet de pointer vers un autre dossier de CSV (tests).
    data_dir = os.environ.get("MOVIES_DATA_DIR")
    try:
        STATE["payload"] = compute_top_recommendations(data_dir)
        STATE["error"] = None
        logger.info("Top %d pré-calculé.", STATE["payload"]["count"])
    except Exception as exc:  # noqa: BLE001 - message affiché à l'utilisateur
        STATE["payload"] = None
        STATE["error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("Échec du pré-calcul des recommandations")
    yield


app = FastAPI(title="Films recommandés", lifespan=lifespan)


@app.get("/api/recommendations")
def get_recommendations():
    if STATE["error"] is not None or STATE["payload"] is None:
        return JSONResponse(
            status_code=503,
            content={
                "detail": STATE["error"]
                or "Les recommandations ne sont pas disponibles."
            },
        )
    return STATE["payload"]


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


# Module livres : page /books et routes /api/books…, /api/book-events.
# La connexion MySQL n'est ouverte qu'à la première requête livres : la page
# films reste utilisable même si MySQL est arrêté.
app.include_router(books_router)

# "/static/books" doit être monté avant "/static", plus général.
app.mount("/static/books", StaticFiles(directory=BOOKS_STATIC_DIR), name="books_static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
