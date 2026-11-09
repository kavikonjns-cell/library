"""Website accounts: a username and password for each member, besides sign-in through Telegram.

* Signing in checks ``password_hash`` (scrypt, salted).
* The owner asked that librarians can see each member's password in the admin panel, so a copy is also
  kept in ``password_enc``, encrypted with a key made from ``SECRET_KEY``. A copy of the database alone
  does not reveal passwords; if ``SECRET_KEY`` changes, old copies can no longer be shown (sign-in still works).
* Forgotten password: a member linked to Telegram gets a one-time link from the bot; otherwise a librarian
  sets a new password in the panel.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

from . import config
from .db import connect
from .jalali import en

RESET_TTL = dt.timedelta(minutes=30)
MIN_PASSWORD = 6
USERNAME_RE = re.compile(r"[a-z][a-z0-9_.]{2,31}")
USERNAME_HELP = "نام کاربری باید ۳ تا ۳۲ حرف انگلیسی باشد، با حرف شروع شود و فقط حرف، عدد، نقطه یا _ داشته باشد."


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------ checks

def normalize_username(raw: str) -> str:
    return en(raw or "").strip().lower()


def username_problem(username: str, conn=None, except_code: str | None = None) -> str:
    """Why this username cannot be used; '' when it is fine."""
    if not USERNAME_RE.fullmatch(username):
        return USERNAME_HELP
    sql = "SELECT code FROM members WHERE username = ?"
    if conn is None:
        with connect() as c:
            row = c.execute(sql, (username,)).fetchone()
    else:
        row = conn.execute(sql, (username,)).fetchone()
    if row and row["code"] != except_code:
        return "این نام کاربری را کس دیگری برداشته است. نام دیگری انتخاب کنید."
    return ""


def password_problem(password: str) -> str:
    if len(password or "") < MIN_PASSWORD:
        return f"رمز عبور باید دست‌کم {MIN_PASSWORD} حرف یا عدد باشد."
    if len(password) > 128:
        return "رمز عبور خیلی بلند است."
    return ""


# ------------------------------------------------------------------ hashing / encryption

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, salt, digest = stored.split("$")
    except ValueError:
        return False
    if kind != "scrypt":
        return False
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(got.hex(), digest)


def _fernet() -> Fernet:
    key = hashlib.sha256(("kavikonj-passwords:" + config.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def reveal(member) -> str:
    """The member's password for the admin panel ('' if none or it can no longer be decrypted)."""
    enc = member["password_enc"] if member else ""
    if not enc:
        return ""
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def _store(conn, code: str, password: str) -> None:
    conn.execute("UPDATE members SET password_hash = ?, password_enc = ? WHERE code = ?",
                 (hash_password(password), _fernet().encrypt(password.encode()).decode(), code))


def set_password(code: str, password: str) -> str:
    """Set a new password; returns a problem message or ''."""
    problem = password_problem(password)
    if problem:
        return problem
    with connect() as conn:
        _store(conn, code, password)
    return ""


def set_credentials(code: str, username: str, password: str, conn=None) -> str:
    """Give a member a username and password; returns a problem message or ''."""
    username = normalize_username(username)
    if conn is None:
        with connect() as c:
            return set_credentials(code, username, password, c)
    problem = username_problem(username, conn, except_code=code) or password_problem(password)
    if problem:
        return problem
    conn.execute("UPDATE members SET username = ? WHERE code = ?", (username, code))
    _store(conn, code, password)
    return ""


def new_temp_password() -> str:
    return "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))


# ------------------------------------------------------------------ sign-in

_failures: dict[str, list[float]] = {}
MAX_FAILURES, WINDOW = 8, 15 * 60


def _blocked(username: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(username, []) if now - t < WINDOW]
    _failures[username] = recent
    return len(recent) >= MAX_FAILURES


def authenticate(username: str, password: str):
    """The member row for a correct username + password; None otherwise.
    ``username`` may also be the member code (M-07) or the phone number."""
    key = normalize_username(username)
    if not key or _blocked(key):
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM members WHERE username = ? AND username != ''", (key,)).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM members WHERE code = ? OR (phone = ? AND phone != '')",
                               (key.upper(), key)).fetchone()
    if row is not None and row["password_hash"] and check_password(password or "", row["password_hash"]):
        _failures.pop(key, None)
        return row
    _failures.setdefault(key, []).append(time.time())
    return None


def find_member(identifier: str):
    """Member for a username, member code or phone number (for «رمز را فراموش کرده‌ام»)."""
    key = normalize_username(identifier).replace(" ", "").replace("-", "") if identifier else ""
    if not key:
        return None
    phone = key
    if re.fullmatch(r"(\+98|0098|98)?9\d{9}", key):
        phone = "0" + key[-10:]
    code = key.upper()
    if re.fullmatch(r"M\d+", code):
        code = f"M-{int(code[1:]):02d}"
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM members WHERE (username = ? AND username != '') OR code = ? OR (phone = ? AND phone != '')"
            " ORDER BY seq LIMIT 1", (normalize_username(identifier), code, phone)).fetchone()


# ------------------------------------------------------------------ reset links

def new_reset(code: str) -> str:
    token = secrets.token_urlsafe(24)
    with connect() as conn:
        conn.execute("DELETE FROM password_resets WHERE created_at < ?", ((_now() - RESET_TTL).isoformat(),))
        conn.execute("INSERT INTO password_resets (token, member_code, created_at) VALUES (?, ?, ?)",
                     (token, code, _now().isoformat()))
    return token


def reset_member(token: str) -> str | None:
    """Member code for a fresh reset link; None if unknown or expired."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM password_resets WHERE token = ?", (token,)).fetchone()
    if row is None or _now() - dt.datetime.fromisoformat(row["created_at"]) >= RESET_TTL:
        return None
    return row["member_code"]


def use_reset(token: str, password: str) -> tuple[str | None, str]:
    """Set the new password with a reset link: (member code, problem)."""
    code = reset_member(token)
    if not code:
        return None, "این لینک منقضی شده است. دوباره «رمز را فراموش کرده‌ام» را بزنید."
    problem = set_password(code, password)
    if problem:
        return None, problem
    with connect() as conn:
        conn.execute("DELETE FROM password_resets WHERE member_code = ?", (code,))
    return code, ""
