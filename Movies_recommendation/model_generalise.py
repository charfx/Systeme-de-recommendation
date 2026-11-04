#importation de model pre entraine et implementation des features pour la recommandation de films
#fonction qui retourne les films recommandés en fonction des données d'évaluation, des données de films et d'une date de référence.
import joblib
import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ARTIFACT_PATH = BASE_DIR / "artifacts" / "movie_ranker.joblib"

if not ARTIFACT_PATH.is_file():
    raise FileNotFoundError(
        f"Artefact du modèle introuvable : {ARTIFACT_PATH}"
    )
artifact = joblib.load(ARTIFACT_PATH)

loaded_model = artifact["model"]
feature_columns = artifact["feature_columns"]

def build_features(
    ratings_data,
    reference_date,
    min_past_users,
    positive_threshold,
    half_life_days
):
    past = ratings_data.loc[
        ratings_data["datetime"] < reference_date
    ].copy()

    features = past.groupby("movieId").agg(
        past_users=("userId", "nunique"),
        past_mean_rating=("rating", "mean"),
        last_rating_date=("datetime", "max")
    )

    # Éligibilité fondée exclusivement sur le passé.
    features = features.loc[
        features["past_users"] >= min_past_users
    ].copy()

    positive_past = past.loc[
        past["rating"] >= positive_threshold
    ]

    features["past_positive_users"] = (
        positive_past.groupby("movieId")["userId"].nunique()
    )

    for days in [7, 30, 90]:
        window_start = reference_date - pd.Timedelta(days=days)

        window = past.loc[
            past["datetime"] >= window_start
        ]

        features[f"users_{days}d"] = (
            window.groupby("movieId")["userId"].nunique()
        )

        if days == 30:
            features["mean_rating_30d"] = (
                window.groupby("movieId")["rating"].mean()
            )

            positive_window = window.loc[
                window["rating"] >= positive_threshold
            ]

            features["positive_users_30d"] = (
                positive_window.groupby("movieId")["userId"]
                .nunique()
            )

    # Fenêtre précédente : [t - 60 jours, t - 30 jours[
    previous_window = past.loc[
        (
            past["datetime"]
            >= reference_date - pd.Timedelta(days=60)
        )
        & (
            past["datetime"]
            < reference_date - pd.Timedelta(days=30)
        )
    ]

    previous_users = (
        previous_window.groupby("movieId")["userId"]
        .nunique()
        .reindex(features.index, fill_value=0)
    )

    features["trend_30d"] = (
        features["users_30d"].fillna(0) - previous_users
    )

    age_days = (
        reference_date - past["datetime"]
    ).dt.total_seconds() / 86400.0

    past["time_weight"] = np.exp2(
        -age_days / half_life_days
    )

    features["decayed_popularity"] = (
        past.groupby("movieId")["time_weight"].sum()
    )

    features["days_since_last_rating"] = (
        reference_date - features["last_rating_date"]
    ).dt.total_seconds() / 86400.0

    # Si aucun avis récent : moyenne historique du film.
    features["mean_rating_30d"] = (
        features["mean_rating_30d"]
        .fillna(features["past_mean_rating"])
    )

    features = (
        features.drop(columns="last_rating_date")
        .fillna(0)
        .reset_index()
    )

    features["reference_date"] = reference_date

    return features


def recommend_movies(
    ratings_data,
    movies_data,
    reference_date,
    top_k=10
):
    reference_date = pd.to_datetime(reference_date, utc=True)

    candidates = build_features(
        ratings_data=ratings_data,
        reference_date=reference_date,
        min_past_users=artifact["min_past_users"],
        positive_threshold=artifact["positive_threshold"],
        half_life_days=artifact["half_life_days"]
    )

    if candidates.empty:
        return []

    # Même ordre et mêmes types qu'à l'entraînement
    X = candidates[feature_columns].astype(float)

    candidates["score"] = loaded_model.predict(
        X,
        num_iteration=artifact["best_iteration"]
    )

    recommendations = (
        candidates.sort_values(
            ["score", "movieId"],
            ascending=[False, True]
        )
        .head(top_k)
        .merge(
            movies_data[["movieId", "title"]],
            on="movieId",
            how="left",
            validate="one_to_one"
        )
    )

    return recommendations[
        ["movieId", "title", "score"]
    ].to_dict(orient="records")


