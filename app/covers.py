"""Book covers: uploaded ones live in the database, the original ones in app/static/covers."""
from __future__ import annotations

import base64
import time
from pathlib import Path

from .db import connect

STATIC = Path(__file__).resolve().parent / "static" / "covers"
_cache: dict = {"at": 0.0, "codes": set()}


def codes() -> set[str]:
    """Codes that have a cover (refreshed at most once a minute)."""
    if time.time() - _cache["at"] > 60:
        found = {p.stem for p in STATIC.glob("*.jpg")}
        try:
            with connect() as conn:
                found |= {r["code"] for r in conn.execute("SELECT code FROM covers")}
        except Exception:  # noqa: BLE001 - a missing table must not break page rendering
            pass
        _cache.update(at=time.time(), codes=found)
    return _cache["codes"]


def forget() -> None:
    _cache["at"] = 0.0


def url(code: str) -> str:
    return f"/covers/{code}.jpg" if code in codes() else ""


def data(code: str) -> bytes | None:
    with connect() as conn:
        row = conn.execute("SELECT data FROM covers WHERE code = ?", (code,)).fetchone()
    if row:
        return base64.b64decode(row["data"])
    path = STATIC / f"{code}.jpg"
    return path.read_bytes() if path.exists() else None
