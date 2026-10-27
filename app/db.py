"""Storage: a local SQLite file, or PostgreSQL (e.g. Supabase) when DATABASE_URL is set.

The rest of the code writes SQLite-style SQL with ``?`` placeholders; for PostgreSQL the
connection wrapper below translates it.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    code        TEXT PRIMARY KEY,          -- NS-012 or NS-012-2 for a second copy
    base_code   TEXT NOT NULL,             -- NS-012 for both copies
    title       TEXT NOT NULL,
    author      TEXT DEFAULT '',
    translator  TEXT DEFAULT '',
    publisher   TEXT DEFAULT '',
    year        TEXT DEFAULT '',
    topic1      TEXT DEFAULT '',
    topic2      TEXT DEFAULT '',
    short_intro TEXT DEFAULT '',
    summary     TEXT DEFAULT '',
    condition   TEXT DEFAULT 'سالم',
    source      TEXT DEFAULT '',
    added_at    TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1 -- 0 = removed from the shelf (lost, retired)
);
CREATE INDEX IF NOT EXISTS books_base ON books(base_code);

CREATE TABLE IF NOT EXISTS members (
    code           TEXT PRIMARY KEY,       -- M-01
    seq            INTEGER NOT NULL UNIQUE,
    tg_user_id     INTEGER UNIQUE,
    tg_username    TEXT DEFAULT '',
    name           TEXT NOT NULL,
    role           TEXT NOT NULL,
    role_other     TEXT DEFAULT '',
    child_name     TEXT DEFAULT '',
    child_group    TEXT DEFAULT '',
    phone          TEXT DEFAULT '',
    tg_phone       TEXT DEFAULT '',
    topics         TEXT DEFAULT '',
    wish           TEXT DEFAULT '',
    fee            INTEGER NOT NULL DEFAULT 0,
    registered_at  TEXT NOT NULL,          -- ISO date of joining or last renewal
    valid_until    TEXT NOT NULL,          -- ISO date, last day of Esfand
    payment_status TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | rejected
    status         TEXT NOT NULL DEFAULT 'active',   -- active | revoked
    receipt_file_id TEXT DEFAULT '',
    note           TEXT DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS loans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    book_code       TEXT NOT NULL REFERENCES books(code),
    member_code     TEXT NOT NULL REFERENCES members(code),
    status          TEXT NOT NULL,         -- reserved | active | returned | lost | cancelled
    requested_days  INTEGER NOT NULL,
    requested_at    TEXT NOT NULL,
    reserve_expires TEXT,
    handed_at       TEXT,
    due_date        TEXT,
    returned_at     TEXT,
    return_condition TEXT DEFAULT '',
    handed_by       TEXT DEFAULT '',
    received_by     TEXT DEFAULT '',
    fine            INTEGER NOT NULL DEFAULT 0,
    fine_paid       INTEGER NOT NULL DEFAULT 0,
    feedback        TEXT DEFAULT '',
    reminded        TEXT DEFAULT ''        -- comma list of reminders already sent
);
CREATE INDEX IF NOT EXISTS loans_status ON loans(status);

CREATE TABLE IF NOT EXISTS waitlist (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    base_code    TEXT NOT NULL,
    member_code  TEXT NOT NULL REFERENCES members(code),
    requested_at TEXT NOT NULL,
    requested_days INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'waiting'  -- waiting | fulfilled | cancelled
);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    category    TEXT NOT NULL,
    direction   TEXT NOT NULL,             -- in | out
    amount      INTEGER NOT NULL,
    member_code TEXT DEFAULT '',
    book_code   TEXT DEFAULT '',
    loan_id     INTEGER,
    method      TEXT DEFAULT '',
    ref         TEXT DEFAULT '',
    description TEXT DEFAULT ''
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ---------------------------------------------------------------- postgres

PG_SCHEMA = (SCHEMA
             .replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
             .replace("tg_user_id     INTEGER UNIQUE", "tg_user_id     BIGINT UNIQUE")
             .replace("loan_id     INTEGER", "loan_id     BIGINT")
             .replace("DEFAULT (datetime('now'))", "DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))"))
# Tables with an id column: INSERTs into them get RETURNING id so ``lastrowid`` works.
_ID_TABLES = {"loans", "waitlist", "payments"}
_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+(\w+)", re.I)


class PgRow(dict):
    """Row usable both as row["col"] and row[0], like sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _pg_row_factory(cursor):
    names = [c.name for c in cursor.description or []]
    return lambda values: PgRow(zip(names, values))


class _PgCursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur.fetchall())


class PgConnection:
    """Minimal sqlite3-like facade over a psycopg connection."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql: str, params=()):
        sql = sql.replace("%", "%%").replace("?", "%s")
        m = _INSERT_RE.match(sql)
        returning = bool(m and m.group(1).lower() in _ID_TABLES and "RETURNING" not in sql.upper())
        if returning:
            sql += " RETURNING id"
        cur = self.raw.cursor(row_factory=_pg_row_factory)
        cur.execute(sql, tuple(params))
        return _PgCursor(cur, cur.fetchone()[0] if returning else None)

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            if stmt.strip():
                self.raw.execute(stmt)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()


_local = threading.local()


def _pg_connection() -> PgConnection:
    import psycopg  # only needed with DATABASE_URL

    conn = getattr(_local, "pg", None)
    if conn is None or conn.raw.closed or conn.raw.broken:
        # prepare_threshold=None: Supabase's transaction pooler does not support prepared statements
        raw = psycopg.connect(config.DATABASE_URL, prepare_threshold=None, connect_timeout=15)
        conn = _local.pg = PgConnection(raw)
    return conn


def using_postgres() -> bool:
    return bool(config.DATABASE_URL)


@contextmanager
def connect():
    """Open a connection, commit on success, roll back on error."""
    if using_postgres():
        conn = _pg_connection()  # kept open and reused by this thread
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                _local.pg = None
            raise
        return
    conn = _connect(config.DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(seed: bool = True) -> None:
    with connect() as conn:
        conn.executescript(PG_SCHEMA if using_postgres() else SCHEMA)
        empty = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
    if seed and empty:
        seed_file = config.ROOT / "data" / "seed" / "books.json"
        if seed_file.exists():
            upsert_books(json.loads(seed_file.read_text(encoding="utf-8")))


BOOK_FIELDS = ["code", "base_code", "title", "author", "translator", "publisher", "year",
               "topic1", "topic2", "short_intro", "summary", "condition", "source",
               "added_at", "note"]


def upsert_books(books: list[dict]) -> int:
    cols = ", ".join(BOOK_FIELDS)
    marks = ", ".join("?" for _ in BOOK_FIELDS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in BOOK_FIELDS if c != "code")
    with connect() as conn:
        for b in books:
            conn.execute(
                f"INSERT INTO books ({cols}) VALUES ({marks}) ON CONFLICT(code) DO UPDATE SET {updates}",
                [str(b.get(c) or "") for c in BOOK_FIELDS],
            )
    return len(books)
