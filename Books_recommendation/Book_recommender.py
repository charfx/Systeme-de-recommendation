import argparse
import json
import logging
import os
from pathlib import Path

import joblib
import numpy as np
import pymysql

from dotenv import load_dotenv
from scipy import sparse
from sklearn.metrics.pairwise import cosine_similarity


# ============================================================
# CONFIGURATION
# ============================================================

MODULE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = MODULE_DIR.parent

load_dotenv(PROJECT_DIR / ".env")

ARTIFACT_PATH = MODULE_DIR / "artifacts" / "books_tfidf.joblib"

OPEN_WEIGHT = 1.0
LIKE_WEIGHT = 4.0
DEFAULT_TOP_K = 10

logger = logging.getLogger(__name__)


def open_db_connection():
    """Nouvelle connexion ; jamais partagée entre les requêtes."""
    return pymysql.connect(
        host=os.getenv("BOOKS_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("BOOKS_DB_PORT", "3306")),
        user=os.getenv("BOOKS_DB_USER", "root"),
        password=os.getenv("BOOKS_DB_PASSWORD", ""),
        database=os.getenv("BOOKS_DB_NAME", "recommendation_mvp"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10,
    )


# ============================================================
# SERVICE DE RECOMMANDATION
# ============================================================

class BookRecommender:
    def __init__(self, artifact_path=ARTIFACT_PATH):
        artifact_path = Path(artifact_path)

        if not artifact_path.exists():
            raise FileNotFoundError(
                f"Artefact TF-IDF introuvable : {artifact_path}"
            )

        artifact = joblib.load(artifact_path)

        self.matrix = sparse.csr_matrix(
            artifact["matrix"],
            dtype=np.float32,
        )

        self.book_ids = [
            str(book_id) for book_id in artifact["book_ids"]
        ]

        if self.matrix.shape[0] != len(self.book_ids):
            raise ValueError(
                "Nombre de vecteurs différent du nombre de book_id."
            )

        if len(set(self.book_ids)) != len(self.book_ids):
            raise ValueError("book_id dupliqués dans les artefacts.")

        if not np.isfinite(self.matrix.data).all():
            raise ValueError("La matrice contient des valeurs invalides.")

        self.id_to_position = {
            book_id: position
            for position, book_id in enumerate(self.book_ids)
        }

    def _read_session_states(self, connection, session_id):
        """
        Lit uniquement cette session et reconstruit les états.
        Les événements restent intégralement conservés en base.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT session_id
                FROM book_sessions
                WHERE session_id = %s
                """,
                (session_id,),
            )

            if cursor.fetchone() is None:
                raise ValueError("Session inconnue.")

            cursor.execute(
                """
                SELECT event_id, book_id, event_type
                FROM book_events
                WHERE session_id = %s
                ORDER BY event_id ASC
                """,
                (session_id,),
            )

            events = cursor.fetchall()

        states = {}

        for event in events:
            book_id = str(event["book_id"])

            state = states.setdefault(
                book_id,
                {
                    "opened": False,
                    "liked": False,
                    "dismissed": False,
                },
            )

            event_type = event["event_type"]

            if event_type == "open":
                # Plusieurs ouvertures ne multiplient pas le poids.
                state["opened"] = True

            elif event_type == "like":
                state["liked"] = True
                state["dismissed"] = False

            elif event_type == "unlike":
                state["liked"] = False

            elif event_type == "dismiss":
                state["dismissed"] = True
                state["liked"] = False

            elif event_type == "undismiss":
                # Ne restaure pas automatiquement un ancien like.
                state["dismissed"] = False

            else:
                raise ValueError(
                    f"Type d'événement inconnu : {event_type}"
                )

        return states

    def _build_profile(self, states):
        positions = []
        weights = []
        excluded_ids = set()
        contributions = []

        for book_id, state in states.items():
            # Exclusion des livres déjà consommés/appréciés ou rejetés.
            if (
                state["opened"]
                or state["liked"]
                or state["dismissed"]
            ):
                excluded_ids.add(book_id)

            if state["dismissed"]:
                continue

            weight = (
                OPEN_WEIGHT * int(state["opened"])
                + LIKE_WEIGHT * int(state["liked"])
            )

            if weight <= 0:
                continue

            position = self.id_to_position.get(book_id)

            if position is None:
                logger.warning(
                    "Livre absent des artefacts, ignoré : %s",
                    book_id,
                )
                continue

            if self.matrix.getrow(position).nnz == 0:
                logger.warning(
                    "Vecteur vide, livre ignoré : %s",
                    book_id,
                )
                continue

            positions.append(position)
            weights.append(weight)

            contributions.append({
                "book_id": book_id,
                "weight": float(weight),
            })

        if not positions:
            return None, excluded_ids, contributions

        weight_array = np.asarray(weights, dtype=np.float32)

        # Matrice ligne de poids : (1, nombre de livres positifs)
        normalized_weights = sparse.csr_matrix(
            (weight_array / weight_array.sum()).reshape(1, -1)
        )

        # (1, N) @ (N, D) -> profil (1, D)
        profile = (
            normalized_weights @ self.matrix[positions]
        ).tocsr()

        return profile, excluded_ids, contributions

    def recommend(self, session_id, top_k=DEFAULT_TOP_K):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id requis.")

        if not isinstance(top_k, int) or not 1 <= top_k <= 50:
            raise ValueError("top_k doit être compris entre 1 et 50.")

        connection = open_db_connection()

        try:
            states = self._read_session_states(
                connection, session_id
            )

            profile, excluded_ids, contributions = (
                self._build_profile(states)
            )

            if profile is None:
                return {
                    "status": "cold_start",
                    "method": "tfidf",
                    "profile_books_count": 0,
                    "count": 0,
                    "recommendations": [],
                }

            scores = cosine_similarity(
                profile, self.matrix
            ).ravel()

            candidate_positions = [
                position
                for position, book_id in enumerate(self.book_ids)
                if (
                    book_id not in excluded_ids
                    and scores[position] > 0
                )
            ]

            # Ordre stable en cas d'égalité.
            candidate_positions.sort(
                key=lambda position: (
                    -float(scores[position]),
                    self.book_ids[position],
                )
            )

            # Retrouver les métadonnées depuis MySQL.
            # On poursuit si un livre classé a disparu de la base.
            recommendations = []

            with connection.cursor() as cursor:
                for offset in range(0, len(candidate_positions), 100):
                    batch = candidate_positions[offset:offset + 100]
                    batch_ids = [
                        self.book_ids[position]
                        for position in batch
                    ]

                    placeholders = ", ".join(
                        ["%s"] * len(batch_ids)
                    )

                    cursor.execute(
                        f"""
                        SELECT book_id, title, authors,
                               categories, thumbnail, description
                        FROM books
                        WHERE book_id IN ({placeholders})
                        """,
                        tuple(batch_ids),
                    )

                    metadata = {
                        str(row["book_id"]): row
                        for row in cursor.fetchall()
                    }

                    for position in batch:
                        book_id = self.book_ids[position]
                        book = metadata.get(book_id)

                        if book is None:
                            continue

                        recommendations.append({
                            "rank": len(recommendations) + 1,
                            **book,
                            "score": float(scores[position]),
                        })

                        if len(recommendations) == top_k:
                            break

                    if len(recommendations) == top_k:
                        break

            return {
                "status": (
                    "ok" if recommendations else "no_candidates"
                ),
                "method": "tfidf",
                "profile_books_count": len(contributions),
                "count": len(recommendations),
                "recommendations": recommendations,
            }

        finally:
            connection.close()


# ============================================================
# TEST MANUEL — AUCUN SERVEUR WEB NÉCESSAIRE
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args()

    recommender = BookRecommender()

    result = recommender.recommend(
        session_id=args.session_id,
        top_k=args.top_k,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))