import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from app import config
from app.telegram_auth import validate_init_data
from app.web import create_app

TOKEN = "123:TEST"


def init_data(user_id=42):
    fields = {"auth_date": str(int(time.time())), "query_id": "x",
              "user": json.dumps({"id": user_id, "first_name": "Test", "username": "t"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_init_data_validation():
    assert validate_init_data(init_data(), TOKEN)["id"] == 42
    assert validate_init_data(init_data(), "999:OTHER") is None
    assert validate_init_data(init_data().replace("Test", "Evil"), TOKEN) is None


def test_public_catalog_and_status():
    c = TestClient(create_app())
    r = c.get("/")
    assert r.status_code == 200 and "NS-001" in r.text and "موجود در قفسه" in r.text
    s = c.get("/api/status").json()
    assert s["NS-001"]["state"] == "available" and "NS-012-2" not in s


def test_register_and_request_through_mini_app(monkeypatch):
    monkeypatch.setattr(config, "BOT_TOKEN", TOKEN)
    c = TestClient(create_app())
    assert c.post("/api/register", json={"initData": "bad", "form": {}}).status_code == 401
    form = {"name": "سارا", "role": "عضو باغ شبنم", "phone": "09151112233", "agree": True, "topics": ["نوجوانی"]}
    r = c.post("/api/register", json={"initData": init_data(), "form": form}).json()
    assert r["ok"] and r["code"] == "M-01"
    r = c.post("/api/request", json={"initData": init_data(), "code": "NS-002", "days": 14}).json()
    assert not r["ok"] and "حق عضویت" in r["message"]


def test_admin_requires_login(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "pw")
    c = TestClient(create_app())
    assert c.get("/admin", follow_redirects=False).status_code == 303
    assert c.post("/admin/login", data={"password": "nope"}, follow_redirects=False).headers["location"] == "/admin/login"
    c.post("/admin/login", data={"password": "pw"})
    for path in ["/admin", "/admin/members", "/admin/books", "/admin/loans?status=all", "/admin/payments",
                 "/admin/books/edit?code=NS-001", "/admin/books/edit"]:
        assert c.get(path).status_code == 200, path
    r = c.post("/admin/members/new", data={"name": "علی", "role": "سایر", "phone": "09150000000", "paid": "1"})
    assert r.status_code == 200
    assert c.get("/admin/members/M-01").status_code == 200
    c.post("/admin/loans/new", data={"book_code": "NS-001", "member_code": "M-01", "days": "14"})
    assert "در امانت تا" in c.get("/").text
    x = c.get("/admin/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    h = c.get("/admin/catalog.html")
    assert h.status_code == 200 and "data:image/jpeg;base64" in h.text


def test_cron_daily_requires_secret(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "s3cret")
    c = TestClient(create_app())
    assert c.get("/cron/daily?key=nope").status_code == 403
    assert c.get("/cron/daily?key=s3cret").json()["ok"] is True


def test_admin_book_import_and_loan_delete(monkeypatch, tmp_path):
    import io
    from openpyxl import Workbook
    from app import services
    from app.db import connect
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "pw")
    c = TestClient(create_app())
    c.post("/admin/login", data={"password": "pw"})
    wb = Workbook(); ws = wb.active; ws.title = "کتاب‌ها"
    ws.append(["title row"]); ws.append(["note"]); ws.append(["headers"])
    ws.append(["NS-001", "بوم‌شناسی تخیلات کودکی (ویرایش)", "عظیمی"])
    ws.append(["NS-099", "کتاب تازه", "نویسنده", "", "", "", "نوجوانی", "", "معرفی کوتاه"])
    buf = io.BytesIO(); wb.save(buf)
    r = c.post("/admin/books/import", files={"file": ("lib.xlsx", buf.getvalue())})
    assert "۱ نسخهٔ تازه" in r.text
    with connect() as conn:
        b1 = conn.execute("SELECT * FROM books WHERE code = 'NS-001'").fetchone()
    assert b1["title"].endswith("(ویرایش)") and b1["summary"]           # long description kept
    assert services.get_book("NS-099")["topic1"] == "نوجوانی"
    c.post("/admin/members/new", data={"name": "آزمون", "role": "سایر", "phone": "09150000000", "paid": "1"})
    c.post("/admin/loans/new", data={"book_code": "NS-001", "member_code": "M-01", "days": "14"})
    with connect() as conn:
        loan_id = conn.execute("SELECT id FROM loans").fetchone()[0]
    c.post(f"/admin/loans/{loan_id}/delete")
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM loans").fetchone()[0] == 0
    assert "موجود در قفسه" in c.get("/").text
