# Système de recommandation : films et livres

L’application web contient deux modules :

- **Films** (`/`) : classement global produit par un modèle LightGBM ;
- **Livres** (`/books`) : cinq livres tirés au hasard et collecte des
  interactions de chaque session dans MySQL (étape 1, sans recommandation).

## Module films

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

Puis ouvrir :

- page films : <http://127.0.0.1:8000/>
- page livres : <http://127.0.0.1:8000/books> (voir la configuration MySQL plus bas)

La page films ne dépend pas de MySQL : elle fonctionne même si XAMPP est arrêté.

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

## Module livres

### Structure

```
Books_recommendation/
  data/books_clean.csv      catalogue nettoyé (6 548 livres)
  sql/schema.sql            schéma des 5 tables (CREATE TABLE IF NOT EXISTS)
  db.py                     configuration .env + moteur SQLAlchemy/PyMySQL
  import_books.py           commande d'import relançable du catalogue
  sessions.py               cookie de session HttpOnly
  discovery.py              tirage aléatoire de 5 livres (book_selections)
  catalog.py                fiche livre et recherche par titre
  interactions.py           ENREGISTREMENT des événements + état d'un livre
  web/api.py                routes FastAPI du module
  web/static/books.html     page /books
  web/static/books.js       DÉTECTION des clics + sendBookEvent()
  web/static/books.css
```

### 1. Configuration MySQL (XAMPP)

1. Démarrer **MySQL** dans le panneau de contrôle XAMPP.
2. Créer le fichier `.env` à partir de l'exemple (il est ignoré par git) :

   ```powershell
   Copy-Item .env.example .env
   ```

   Valeurs par défaut : `127.0.0.1:3306`, utilisateur `root` sans mot de passe,
   base `recommendation_mvp`. Les identifiants ne quittent jamais le serveur.

3. Seulement si les tables n'existent pas encore, créer le schéma. Le fichier
   est sans risque pour des tables existantes :

   ```powershell
   cmd /c "C:\xampp\mysql\bin\mysql.exe -u root < Books_recommendation\sql\schema.sql"
   ```

### 2. Import du catalogue

```powershell
.venv\Scripts\python.exe -m Books_recommendation.import_books
```

Au premier import, la sortie attendue est `Lignes traitées : 6548` puis
`Nombre final de livres : 6548`. La commande est **relançable** :
`INSERT … ON DUPLICATE KEY UPDATE` met à jour les métadonnées sans créer de
doublon et sans supprimer de livre, donc les interactions restent valides.

- Les ISBN sont lus comme du texte, ce qui conserve les zéros initiaux.
- Les valeurs manquantes deviennent `NULL`.
- Les entiers stockés en flottants sont convertis : `2004.0` devient `2004`.

Pour importer un autre fichier : `--csv chemin\vers\fichier.csv`.

### 3. Démarrage

```powershell
.venv\Scripts\python.exe -m uvicorn Movies_recommendation.web.app:app --port 8000
```

Ouvrir <http://127.0.0.1:8000/books>. Le menu « Films / Livres » en haut des deux
pages permet de passer de l'une à l'autre.

### API

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/books/discovery` | sélection courante ; tirage seulement s'il n'en existe aucune |
| POST | `/api/books/discovery/refresh` | « Découvrir d'autres livres » : nouveau tirage |
| GET | `/api/books?query=…&page=1&page_size=20` | recherche par titre (page_size ≤ 50) |
| GET | `/api/books/{book_id}` | fiche complète + état pour la session |
| POST | `/api/book-events` | enregistre une interaction |

Les routes `/api/books/discovery…` sont déclarées avant `/api/books/{book_id}`.
Sinon, « discovery » serait pris pour un `book_id`.

### Sessions et sélections

- À la première requête du module, le backend génère un UUID, l'insère dans
  `book_sessions` et le place dans le cookie `books_session` (HttpOnly,
  SameSite=Lax, valable un an). `Secure` est activé automatiquement en HTTPS ;
  `BOOKS_COOKIE_SECURE=true|false` force le comportement.
- Le backend lit la session **uniquement** depuis ce cookie : un `session_id`
  envoyé dans le corps d'une requête est refusé (422).
- Une sélection, ce sont 5 livres **servis** à la page (`book_selections` et
  `book_selection_items`, positions 1 à 5). Cela ne prouve ni lecture ni
  visibilité réelle.
- Un tirage choisit d'abord des livres jamais proposés à la session, puis des
  livres déjà proposés hors sélection précédente, puis la sélection précédente.
- Les livres rejetés sont toujours exclus. S'il en reste moins de 5, la page
  affiche ceux qui existent.

### Collecte des interactions (pour brancher le profil ensuite)

| Événement | Déclencheur |
|---|---|
| `open` | fiche du livre chargée avec succès (pas un simple affichage de carte) |
| `like` / `unlike` | bouton « J'aime » / « ♥ Aimé » |
| `dismiss` / `undismiss` | bouton « Pas intéressé » / « Écarté · annuler » |

**Trajet d'un clic « J'aime »**

1. **Détection** : `handleBookAction(event)` dans
   [books.js](Books_recommendation/web/static/books.js). Un seul listener `click`
   délégué par conteneur (`#book-grid`, `#search-results`, `#book-dialog`),
   attaché une fois à l'initialisation. Il lit `data-action="like"` sur le
   bouton et `data-book-id` sur la carte. Seul le bouton cliqué compte, donc
   « J'aime » n'ouvre jamais la fiche.
2. `performStateAction(bookId, "like")` désactive les boutons du livre et
   attend la confirmation du backend.
3. **Envoi** : `sendBookEvent(bookId, eventType)` fait un
   `POST /api/book-events` avec `{"book_id", "event_type", "request_id"}`. Le
   navigateur joint le cookie. Le `request_id` (UUID) est conservé tant que
   l'action n'est pas confirmée : les reprises réseau automatiques et le bouton
   « Réessayer » renvoient le même identifiant.
4. **Réception** : `post_book_event()` dans
   [api.py](Books_recommendation/web/api.py). Pydantic valide le corps, la
   session est lue depuis le cookie et une transaction est ouverte.
5. **Enregistrement** : `record_book_event()` dans
   [interactions.py](Books_recommendation/interactions.py). La fonction vérifie
   que le livre existe, puis exécute
   `INSERT INTO book_events (request_id, session_id, book_id, event_type, created_at)`.
   Si ce couple `(session_id, request_id)` existe déjà (contrainte UNIQUE),
   aucune ligne n'est ajoutée et la réponse contient `"duplicate": true`.
6. La réponse contient l'état reconstruit, par exemple
   `{"state": {"liked": true, "dismissed": false}}`. `renderBookState()` met
   alors à jour la carte et la fiche. En cas d'échec, un lien « Réessayer »
   s'affiche.

Ligne obtenue dans MySQL :

```
event_id | request_id | session_id | book_id       | event_type | created_at (UTC)
      42 | 7f3c…      | 1b9e…      | 9780002005883 | like       | 2026-11-04 14:03:12.481920
```

**État d'un livre** : `compute_book_states()` rejoue les événements dans
l'ordre `event_id` avec `BookState.apply()`. Un `like` annule un rejet, un
`dismiss` annule un like, et `open` ne change pas l'état. Les événements bruts
sont conservés : rien n'est encore converti en poids.

**Brancher le profil TF-IDF (étape suivante)** sans toucher à la collecte :

```python
from Books_recommendation.db import get_engine
from Books_recommendation.interactions import fetch_events, compute_book_states

with get_engine().connect() as conn:
    events = fetch_events(conn, session_id)          # faits bruts, ordonnés
    states = compute_book_states(conn, session_id)   # {book_id: BookState}

liked = [b for b, s in states.items() if s.liked]
dismissed = [b for b, s in states.items() if s.dismissed]
opened = {b: s.open_count for b, s in states.items()}
# -> pondérer ces livres pour construire le vecteur profil TF-IDF.
```
