"""Import (relançable) du catalogue nettoyé dans la table `books`.

Usage, depuis la racine du projet :
    .venv\\Scripts\\python.exe -m Books_recommendation.import_books
    .venv\\Scripts\\python.exe -m Books_recommendation.import_books --csv chemin\\vers\\books_clean.csv

Principe :
- INSERT ... ON DUPLICATE KEY UPDATE sur la clé primaire book_id : un livre
  existant voit ses métadonnées mises à jour, un nouveau livre est ajouté ;
- aucun livre n'est supprimé, donc les interactions (book_events) restent valides ;
- tout l'import se fait dans une seule transaction : en cas d'erreur, rien n'est écrit.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from Books_recommendation.db import DEFAULT_CATALOG_CSV, get_engine

TEXT_COLUMNS = [
    "book_id", "isbn10", "title", "subtitle", "authors", "categories",
    "thumbnail", "description", "content",
]
INTEGER_COLUMNS = ["published_year", "num_pages", "ratings_count"]
DECIMAL_COLUMNS = ["average_rating"]
REQUIRED_TEXT = ["book_id", "title", "description", "content"]  # NOT NULL en base

ALL_COLUMNS = TEXT_COLUMNS + INTEGER_COLUMNS + DECIMAL_COLUMNS

UPSERT_SQL = text(f"""
    INSERT INTO books ({", ".join(ALL_COLUMNS)})
    VALUES ({", ".join(":" + c for c in ALL_COLUMNS)})
    ON DUPLICATE KEY UPDATE
        {", ".join(f"{c} = VALUES({c})" for c in ALL_COLUMNS if c != "book_id")}
""")


def load_catalog(csv_path: Path) -> pd.DataFrame:
    # Identifiants lus comme chaînes : "0002005883" ne doit pas devenir 2005883.
    df = pd.read_csv(
        csv_path,
        dtype={"book_id": str, "isbn13": str, "isbn10": str},
        encoding="utf-8",
    )
    missing = [c for c in ALL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Colonnes absentes du CSV : {missing}")
    df = df[ALL_COLUMNS].copy()

    for col in TEXT_COLUMNS:
        df[col] = df[col].astype("string").str.strip()
        df.loc[df[col] == "", col] = pd.NA

    # Contrôles sur l'identifiant.
    if df["book_id"].isna().any():
        raise ValueError(f"{int(df['book_id'].isna().sum())} ligne(s) sans book_id.")
    duplicated = df.loc[df["book_id"].duplicated(keep=False), "book_id"]
    if not duplicated.empty:
        raise ValueError(f"book_id en double : {sorted(set(duplicated))[:10]}")
    too_long = df.loc[df["book_id"].str.len() > 20, "book_id"]
    if not too_long.empty:
        raise ValueError(f"book_id de plus de 20 caractères : {list(too_long[:5])}")

    for col in REQUIRED_TEXT:
        if df[col].isna().any():
            raise ValueError(f"{int(df[col].isna().sum())} valeur(s) manquante(s) dans {col}.")

    # 2004.0 -> 2004 ; une vraie valeur décimale (2004.5) arrête l'import.
    for col in INTEGER_COLUMNS:
        numbers = pd.to_numeric(df[col], errors="raise")
        not_integer = numbers.notna() & (numbers % 1 != 0)
        if not_integer.any():
            raise ValueError(f"{col} contient des valeurs non entières : {list(numbers[not_integer][:5])}")
        df[col] = numbers.astype("Int64")

    for col in DECIMAL_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="raise").round(2)

    return df


def to_records(df: pd.DataFrame) -> list[dict]:
    """Convertit en dictionnaires Python purs : NaN/NA -> None (NULL SQL)."""
    records = []
    for row in df.astype(object).itertuples(index=False):
        record = {}
        for col, value in zip(df.columns, row):
            if value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value)):
                record[col] = None
            elif col in INTEGER_COLUMNS:
                record[col] = int(value)
            elif col in DECIMAL_COLUMNS:
                record[col] = float(value)
            else:
                record[col] = str(value)
        records.append(record)
    return records


def import_catalog(csv_path: Path, batch_size: int = 500) -> dict:
    df = load_catalog(csv_path)
    records = to_records(df)

    engine = get_engine()
    with engine.begin() as conn:  # une transaction : tout ou rien
        before = conn.execute(text("SELECT COUNT(*) FROM books")).scalar_one()
        for start in range(0, len(records), batch_size):
            conn.execute(UPSERT_SQL, records[start:start + batch_size])
        after = conn.execute(text("SELECT COUNT(*) FROM books")).scalar_one()

    return {
        "processed": len(records),
        "inserted": after - before,
        "updated_or_unchanged": len(records) - (after - before),
        "total": after,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Importe books_clean.csv dans la table books.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CATALOG_CSV,
                        help=f"Chemin du CSV (défaut : {DEFAULT_CATALOG_CSV})")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args(argv)

    csv_path = args.csv.resolve()
    if not csv_path.is_file():
        print(f"Fichier introuvable : {csv_path}", file=sys.stderr)
        return 1

    print(f"Import de {csv_path}")
    stats = import_catalog(csv_path, batch_size=args.batch_size)
    print(f"Lignes traitées           : {stats['processed']}")
    print(f"Nouveaux livres           : {stats['inserted']}")
    print(f"Livres existants (MAJ)    : {stats['updated_or_unchanged']}")
    print(f"Nombre final de livres    : {stats['total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
