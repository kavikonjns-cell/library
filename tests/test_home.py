"""New home page: shelves preview, filters, member loan strip; full catalogue lives at /books."""
from fastapi.testclient import TestClient

from app.web import create_app


def test_guest_home():
    c = TestClient(create_app())
    r = c.get("/")
    assert r.status_code == 200
    t = r.text
    assert "هر کتاب، آغاز یک کشف تازه" in t and "در قفسه‌های کاوی کنج" in t
    assert t.count('class="book-tile') == 6 and 'href="/app/register"' in t
    assert "در امانت من" not in t and 'aria-label="امانت من"' not in t  # loan strip is for members only
    assert "کتابی پیدا نشد" not in t


def test_home_filters():
    c = TestClient(create_app())
    topic = "مغز و ذهن"
    t = c.get("/", params={"category": topic, "available": "1", "sort": "title"}).text
    assert 'aria-current="true"' in t and t.count('class="book-tile') <= 6
    # unknown category falls back to all topics instead of an empty page
    assert c.get("/", params={"category": "x"}).text.count('class="book-tile') == 6


def test_member_loan_strip_and_logout(monkeypatch):
    c = TestClient(create_app())
    c.post("/api/register/web", json={"form": {"name": "سارا", "role": "سایر", "phone": "09151112233",
                                               "agree": True, "username": "sara", "password": "secret12"}})
    t = c.get("/").text
    assert 'aria-label="امانت من"' in t and "فعلاً کتابی در دست نداری" in t and "خروج" in t
    assert c.post("/logout", follow_redirects=False).status_code == 303
    assert 'aria-label="امانت من"' not in c.get("/").text


def test_member_with_interests_sees_discovery_band():
    c = TestClient(create_app())
    c.post("/api/register/web", json={"form": {"name": "سارا", "role": "سایر", "phone": "09151112233", "agree": True,
                                               "username": "sara", "password": "secret12", "topics": ["مغز و ذهن"]}})
    t = c.get("/").text
    assert "کشف بعدی تو" in t and "بر اساس علاقه‌هایی" in t and 'class="recommendation"' in t
    assert "کشف بعدی تو" not in TestClient(create_app()).get("/").text   # guests get no fake personalisation
