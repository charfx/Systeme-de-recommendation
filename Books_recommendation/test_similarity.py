from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer
from Books_recommendation.data_book_analysis import books_clean,tfidf_matrix

# Position du livre dans books_clean ET dans tfidf_matrix
book_position = 0
top_k = 10

# Comparer un seul livre à tout le corpus
similarity_scores = cosine_similarity(
    tfidf_matrix.getrow(book_position),
    tfidf_matrix
).ravel()

# Construire le classement sans modifier le catalogue
similar_books = books_clean[
    ["book_id", "title", "authors", "categories"]
].copy()

similar_books["similarity_score"] = similarity_scores

# Exclure le livre lui-même et les similarités nulles
similar_books = similar_books.loc[
    (similar_books.index != book_position)
    & (similar_books["similarity_score"] > 0)
]

similar_books = (
    similar_books
    .sort_values(
        ["similarity_score", "book_id"],
        ascending=[False, True]
    )
    .head(top_k)
)

print("\nLivre de référence :", books_clean.iloc[book_position]["title"])

print(
    similar_books[
        ["title", "authors", "similarity_score"]
    ].to_string(index=False)
)
from pathlib import Path

output_path = Path(__file__).resolve().parent / "data" / "books_clean.csv"
output_path.parent.mkdir(parents=True, exist_ok=True)

books_clean.to_csv(
    output_path,
    index=False,
    encoding="utf-8"
)

print("Catalogue exporté :", output_path.resolve())