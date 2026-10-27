import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
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
