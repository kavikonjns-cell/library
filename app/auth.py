"""Website sign-in for members. Membership lives in Telegram, so signing in is always confirmed there.

Two ways:
* the website shows «ورود با تلگرام»: it opens the bot with a one-time code (``/start login_<nonce>``),
  the member's Telegram account approves the code and the waiting page signs in;
* the bot's «🌐 ورود به سایت» button sends a one-time link that signs in directly.
"""
from __future__ import annotations

import datetime as dt
import secrets

from .db import connect

TTL = dt.timedelta(minutes=10)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _fresh(row) -> bool:
    return row is not None and _now() - dt.datetime.fromisoformat(row["created_at"]) < TTL


def _cleanup(conn) -> None:
    conn.execute("DELETE FROM login_tokens WHERE created_at < ?", ((_now() - TTL).isoformat(),))


def new_nonce(tg_user_id: int | None = None) -> str:
    """A one-time sign-in code; pre-approved when ``tg_user_id`` is given (link sent by the bot)."""
    nonce = secrets.token_urlsafe(18)
    with connect() as conn:
        _cleanup(conn)
        conn.execute("INSERT INTO login_tokens (nonce, tg_user_id, created_at) VALUES (?, ?, ?)",
                     (nonce, tg_user_id, _now().isoformat()))
    return nonce


def approve(nonce: str, tg_user_id: int) -> bool:
    """The member pressed the bot link: bind the code to their Telegram account."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM login_tokens WHERE nonce = ?", (nonce,)).fetchone()
        if not _fresh(row) or row["tg_user_id"]:
            return False
        conn.execute("UPDATE login_tokens SET tg_user_id = ? WHERE nonce = ?", (tg_user_id, nonce))
    return True


def consume(nonce: str) -> int | None:
    """Telegram user id for an approved, fresh code (single use); None while still waiting."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM login_tokens WHERE nonce = ?", (nonce,)).fetchone()
        if not _fresh(row) or not row["tg_user_id"]:
            return None
        conn.execute("DELETE FROM login_tokens WHERE nonce = ?", (nonce,))
    return int(row["tg_user_id"])


def is_pending(nonce: str) -> bool:
    with connect() as conn:
        return _fresh(conn.execute("SELECT * FROM login_tokens WHERE nonce = ?", (nonce,)).fetchone())
