"""Import (or update) the book list from the library Excel file and the old HTML book list.

    python scripts/import_books.py "کتابخانه.xlsx" ["فهرست کتاب‌ها.html"]

* Excel, sheet «کتاب‌ها», from row 4: code, title, author, translator, publisher, year,
  topic 1, topic 2, short intro, physical condition, source, date added, ... note (column Q).
* HTML (optional): the long description (p.sum) and the cover image of each book are
  taken from the old self-contained book list; covers go to app/static/covers/<code>.jpg.
Existing books are updated by code; loans and members are not touched.
"""
from __future__ import annotations

import base64
import html as htmllib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.importer import import_books, read_excel  # noqa: E402

COVERS = Path(__file__).resolve().parent.parent / "app" / "static" / "covers"


def read_html(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    summaries = {}
    COVERS.mkdir(parents=True, exist_ok=True)
    for m in re.finditer(r'<article class="book"[^>]*>(.*?)</article>', text, re.S):
        block = m.group(1)
        code = re.search(r'<span class="code">([^<]+)</span>', block)
        if not code:
            continue
        code = code.group(1).strip()
        s = re.search(r'<p class="sum">(.*?)</p>', block, re.S)
        if s and 'class="soon"' not in s.group(0):
            summaries[code] = htmllib.unescape(re.sub(r"<[^>]+>", "", s.group(1))).strip()
        img = re.search(r'src="data:image/jpeg;base64,([^"]+)"', block)
        if img:
            (COVERS / f"{code}.jpg").write_bytes(base64.b64decode(img.group(1)))
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
