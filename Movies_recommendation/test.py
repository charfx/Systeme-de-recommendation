"""Démonstration du classement global sur les données historiques.

Logique importable (utilisée par l'application web) :
    - load_data()                    -> (ratings, movies)
    - historical_reference_date()    -> dernière évaluation + 1 seconde
    - get_recommendations()          -> (reference_date, liste de recommandations)

Exécution directe (affiche le Top 10) :
    python Movies_recommendation/test.py
    python -m Movies_recommendation.test
"""
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "movies_Data"

if __package__ in (None, ""):
    # Lancement en script (python test.py) : rend le package importable.
    sys.path.insert(0, str(PROJECT_ROOT))

from Movies_recommendation.model_generalise import recommend_movies  # noqa: E402


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(
            f"Fichier de données introuvable : {path} "
            f"(attendu dans le dossier {path.parent})"
        )
    return pd.read_csv(path)


def load_data(data_dir: Path = DATA_DIR):
    """Charge ratings.csv et movies.csv et ajoute la colonne datetime (UTC)."""
    data_dir = Path(data_dir)
    ratings = _read_csv(data_dir / "ratings.csv")
    movies = _read_csv(data_dir / "movies.csv")
    ratings["datetime"] = pd.to_datetime(ratings["timestamp"], unit="s", utc=True)
    return ratings, movies


def historical_reference_date(ratings: pd.DataFrame) -> pd.Timestamp:
    """Date de référence de la démonstration : dernière évaluation + 1 seconde."""
    return ratings["datetime"].max() + pd.Timedelta(seconds=1)


def get_recommendations(ratings, movies, top_k=10, reference_date=None):
    """Retourne (reference_date, recommandations) via le pipeline existant."""
    if reference_date is None:
        reference_date = historical_reference_date(ratings)
    recommendations = recommend_movies(
        ratings_data=ratings,
        movies_data=movies,
        reference_date=reference_date,
        top_k=top_k,
    )
    return reference_date, recommendations


if __name__ == "__main__":
    ratings, movies = load_data()
    reference_date, recommendations = get_recommendations(ratings, movies, top_k=10)
    print(f"Date de référence : {reference_date}")
    print(pd.DataFrame(recommendations))
