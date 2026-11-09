"""Import (or update) the book list from the library Excel file and the old HTML book list.

    python scripts/import_books.py "کتابخانه.xlsx" ["فهرست کتاب‌ها.html"]

* Excel, sheet «کتاب‌ها», from row 4: code, title, author, translator, publisher, year,
  topic 1, topic 2, short intro, physical condition, source, date added, ... note (column Q).
* HTML (optional): the long description (p.sum) and the cover image of each book are
  taken from the old self-contained book list; covers go to app/static/covers/<code>.jpg.
Existing books are updated by code; loans and members are not touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.importer import import_books, read_excel  # noqa: E402
from app.importer import read_html as read_html_bytes  # noqa: E402

COVERS = Path(__file__).resolve().parent.parent / "app" / "static" / "covers"


def read_html(path: Path) -> dict[str, str]:
    summaries, covers = read_html_bytes(path.read_bytes())
    COVERS.mkdir(parents=True, exist_ok=True)
    for code, data in covers.items():
        (COVERS / f"{code}.jpg").write_bytes(data)
    return summaries


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    db.init_db(seed=False)
    books = read_excel(Path(sys.argv[1]))
    summaries = read_html(Path(sys.argv[2])) if len(sys.argv) > 2 else {}
    added, updated = import_books(books, summaries)
    print(f"{added} new and {updated} existing copies imported, {len(summaries)} descriptions/covers from HTML.")


if __name__ == "__main__":
    main()
