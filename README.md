# Système de recommandation de films

Projet pédagogique : un modèle **LightGBM LambdaRank** classe les films à partir de
variables historiques de popularité et de notes (MovieLens, données jusqu’en 2018).
Le classement est **global** (non personnalisé).

Une interface web affiche les dix films recommandés par ce pipeline, calculés
à la date de référence « dernière évaluation du dataset + 1 seconde »
(24 septembre 2018, 14:27:31 UTC).

## Structure

```
movies_Data/                    ratings.csv, movies.csv (non versionnés)
Movies_recommendation/
  artifacts/movie_ranker.joblib modèle + paramètres + ordre des features
  model_generalise.py           build_features() et recommend_movies()
  test.py                       load_data(), get_recommendations() + démo console
  web/app.py                    backend FastAPI (GET /api/recommendations)
  web/static/                   page HTML/CSS/JS
```

## Installation (Windows, PowerShell)

Depuis la racine du projet, avec [uv](https://docs.astral.sh/uv/) (Python 3.14) :

```powershell
uv sync
```

Ou avec pip dans l’environnement virtuel existant :

```powershell
.venv\Scripts\python.exe -m pip install -r requirement.txt
```

## Lancement de l’application web

```powershell
.venv\Scripts\python.exe -m uvicorn Movies_recommendation.web.app:app --port 8000
```

Puis ouvrir <http://127.0.0.1:8000/>.

Le modèle et les CSV sont chargés une seule fois au démarrage et le Top 10 est
pré-calculé en mémoire. Si un fichier manque, l’API renvoie une erreur 503
indiquant le chemin attendu, et la page l’affiche.

Pour lancer depuis un autre dossier, ajouter
`--app-dir "C:\chemin\vers\Systeme de recommendation"`.

## Démonstration console

```powershell
.venv\Scripts\python.exe Movies_recommendation\test.py
```

## API

`GET /api/recommendations`

```json
{
  "reference_date": "2018-09-24T14:27:31+00:00",
  "ranking_type": "global",
  "count": 10,
  "recommendations": [
    {"rank": 1, "movieId": 318, "title": "Shawshank Redemption, The (1994)",
     "score": 2.2637, "genres": ["Crime", "Drama"]}
  ]
}
```

Le « score du modèle » est une valeur relative servant uniquement à ordonner
les films : ce n’est ni une note sur 5 ni une probabilité.
