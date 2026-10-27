import datetime as dt
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    pg_url = os.environ.get("TEST_DATABASE_URL")  # run the suite against PostgreSQL too
    monkeypatch.setattr(config, "DATABASE_URL", pg_url or "")
    if pg_url:
        with db.connect() as conn:
            conn.execute("DROP TABLE IF EXISTS login_tokens, suggestions, inbox, badges, reading_logs,"
                         " payments, waitlist, loans, members, books CASCADE")
    db.init_db()
    yield


# 1405/08/05 (Aban)
TODAY = dt.date(2026, 10, 27)


def make_member(tg=1001, role="عضو باغ شبنم", paid=True, on=TODAY, phone="09121234567"):
    from app import services
    res = services.register_member({"name": f"عضو {tg}", "role": role, "phone": phone, "agree": True}, tg, "", on)
    assert res.ok, res.message
    if paid:
        services.approve_payment(res.data["code"], on=on)
    return res.data["code"]
