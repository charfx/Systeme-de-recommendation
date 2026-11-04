import pandas as pd
from IPython.display import display
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer


DATA_DIR = Path(__file__).resolve().parent.parent / "Books_data"
books = pd.read_csv(DATA_DIR / "data.csv")

import pandas as pd

# Travailler sur une copie
books_clean = books.copy()

# 1. Normaliser les champs textuels
text_columns = [
    "title",
    "subtitle",
    "authors",
    "categories",
    "thumbnail",
    "description",
]

for column in text_columns:
    books_clean[column] = (
        books_clean[column]
        .astype("string")
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .replace("", pd.NA)
    )

# 2. Préparer les identifiants
# À l'import original, il est préférable de lire les ISBN en chaînes :
# pd.read_csv(path, dtype={"isbn13": "string", "isbn10": "string"})

books_clean["isbn13"] = (
    books_clean["isbn13"].astype("string").str.strip()
)

books_clean["isbn10"] = (
    books_clean["isbn10"].astype("string").str.strip()
)

if books_clean["isbn13"].isna().any():
    raise ValueError("Certains ISBN13 sont manquants.")

# Aucun doublon de ligne ne garantit pas l'unicité de l'ISBN.
duplicate_isbn = books_clean["isbn13"].duplicated(keep=False)

if duplicate_isbn.any():
    print(
        books_clean.loc[
            duplicate_isbn,
            ["isbn13", "title", "authors"]
        ].sort_values("isbn13")
    )
    raise ValueError(
        "ISBN13 dupliqués : examiner ces lignes avant de continuer."
    )

# 3. Conserver les livres avec titre et description
initial_count = len(books_clean)

books_clean = books_clean.dropna(
    subset=["title", "description"]
).copy()

# ISBN utilisé comme identifiant stable
books_clean["book_id"] = books_clean["isbn13"]

# 4. Construire le texte commun à TF-IDF et aux embeddings
# Pas de valeurs 'nan' ajoutées artificiellement au texte.
content_columns = [
    "title",
    "subtitle",
    "categories",
    "description",
]

books_clean["content"] = (
    books_clean[content_columns]
    .fillna("")
    .agg(
        lambda row: "\n".join(
            value for value in row if value
        ),
        axis=1
    )
)

# 5. Fixer l'ordre des lignes pour la future matrice de vecteurs
books_clean = books_clean.reset_index(drop=True)

assert books_clean["book_id"].is_unique
assert books_clean["content"].str.strip().ne("").all()

if books_clean["content"].isna().any():
    raise ValueError("Certains contenus sont manquants.")

if books_clean["content"].str.strip().eq("").any():
    raise ValueError("Certains contenus sont vides.")

# 2. Configurer le vectoriseur
tfidf_vectorizer = TfidfVectorizer(
    lowercase=True,
    stop_words=None,
    ngram_range=(1, 2),
    min_df=2,
    max_df=0.95,
    max_features=30_000,
    sublinear_tf=True,
    norm="l2",
    dtype=np.float32
)

# 3. Apprendre le vocabulaire et vectoriser le corpus
tfidf_matrix = tfidf_vectorizer.fit_transform(
    books_clean["content"]
)

book_position = 0

vocabulary = tfidf_vectorizer.get_feature_names_out()
book_vector = tfidf_matrix.getrow(book_position)

top_terms = (
    pd.DataFrame({
        "term": vocabulary[book_vector.indices],
        "weight": book_vector.data
    })
    .sort_values("weight", ascending=False)
    .head(15)
)

print("\nLivre :", books_clean.iloc[book_position]["title"])
print(top_terms.to_string(index=False))
from pathlib import Path
import joblib

artifact_path = Path(__file__).resolve().parent / "artifacts" / "books_tfidf.joblib"
artifact_path.parent.mkdir(parents=True, exist_ok=True)

book_ids = books_clean["book_id"].astype(str).tolist()

assert len(book_ids) == tfidf_matrix.shape[0]
assert len(book_ids) == len(set(book_ids))

joblib.dump(
    {
        "vectorizer": tfidf_vectorizer,
        "matrix": tfidf_matrix.tocsr(),
        "book_ids": book_ids,
    },
    artifact_path
)

print("Artefacts sauvegardés :", artifact_path.resolve())