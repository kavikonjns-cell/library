"""Read the book list from the library Excel file (sheet «کتاب‌ها») and the HTML book list."""
from __future__ import annotations

import base64
import html as htmllib
import io
import re
from pathlib import Path

import openpyxl

from .db import connect, upsert_books


def read_excel(source: Path | bytes) -> list[dict]:
    """Rows from row 4: code, title, author, translator, publisher, year, topic 1, topic 2, short intro,
    physical condition, source, date added, ..., note (column Q)."""
    wb = openpyxl.load_workbook(io.BytesIO(source) if isinstance(source, bytes) else source, data_only=True)
    if "کتاب‌ها" not in wb.sheetnames:
        raise ValueError("برگهٔ «کتاب‌ها» در این فایل نیست.")
    books = []
    for r in wb["کتاب‌ها"].iter_rows(min_row=4, values_only=True):
        if not r or not r[0]:
            continue
        v = lambda i: "" if i >= len(r) or r[i] is None else str(r[i]).strip()  # noqa: E731
        code = v(0).upper()
        if not re.match(r"^NS-\d+(-\d+)?$", code) or not v(1):
            continue
        books.append(dict(code=code, base_code=re.sub(r"^(NS-\d+)-\d+$", r"\1", code), title=v(1), author=v(2),
                          translator=v(3), publisher=v(4), year=v(5), topic1=v(6), topic2=v(7), short_intro=v(8),
                          condition=v(9) or "سالم", source=v(10), added_at=v(11), note=v(16)))
    return books


def read_html(source: str | bytes) -> tuple[dict[str, str], dict[str, bytes]]:
    """Long descriptions and JPG covers from the self-contained HTML book list, by book code."""
    text = source.decode("utf-8") if isinstance(source, bytes) else source
    summaries, covers = {}, {}
    for m in re.finditer(r'<article class="book"[^>]*>(.*?)</article>', text, re.S):
        block = m.group(1)
        code = re.search(r'<span class="code">([^<]+)</span>', block)
        if not code:
            continue
        code = code.group(1).strip().upper()
        s = re.search(r'<p class="sum">(.*?)</p>', block, re.S)
        if s and 'class="soon"' not in s.group(0):
            summaries[code] = htmllib.unescape(re.sub(r"<[^>]+>", "", s.group(1))).strip()
        img = re.search(r'src="data:image/jpeg;base64,([^"]+)"', block)
        if img:
            covers[code] = base64.b64decode(img.group(1))
    return summaries, covers


def save_covers(covers: dict[str, bytes]) -> int:
    """Keep covers in the database, so they survive redeploys (the server's disk is not permanent)."""
    from .jalali import today
    with connect() as conn:
        for code, data in covers.items():
            conn.execute("INSERT INTO covers (code, data, updated_at) VALUES (?, ?, ?) ON CONFLICT(code) DO UPDATE SET"
                         " data = excluded.data, updated_at = excluded.updated_at",
                         (code, base64.b64encode(data).decode(), today().isoformat()))
    return len(covers)


def update_summaries(summaries: dict[str, str]) -> int:
    """Set long descriptions of books already in the list (base code applies to every copy)."""
    with connect() as conn:
        n = 0
        for code, text in summaries.items():
            if text:
                n += conn.execute("UPDATE books SET summary = ? WHERE base_code = ? OR code = ?", (text, code, code)).rowcount
    return n


def import_books(books: list[dict], summaries: dict[str, str] | None = None) -> tuple[int, int]:
    """Add new copies and update existing ones. Long descriptions written on the site are kept;
    loans, members and books missing from the file are not touched. Returns (added, updated)."""
    summaries = summaries or {}
    with connect() as conn:
        old = {r["code"]: r["summary"] for r in conn.execute("SELECT code, summary FROM books")}
        copy_summary = {}
        for code, summary in old.items():
            copy_summary.setdefault(re.sub(r"^(NS-\d+)-\d+$", r"\1", code), summary)
    for b in books:
        b["summary"] = summaries.get(b["base_code"]) or old.get(b["code"]) or copy_summary.get(b["base_code"], "")
    upsert_books(books)
    added = sum(1 for b in books if b["code"] not in old)
    return added, len(books) - added
