"""/books: search, filters, pagination, and the book detail / loan request flow."""
from fastapi.testclient import TestClient

from app import catalogue, config, notify, services
from app.web import create_app
from tests.conftest import make_member


def _client(monkeypatch=None):
    if monkeypatch:
        async def noop(*a, **k):
            pass
        monkeypatch.setattr(notify, "send", noop)
        monkeypatch.setattr(notify, "deliver", noop)
    return TestClient(create_app())


def _signup(c):
    r = c.post("/api/register/web", json={"form": {"name": "سارا", "role": "سایر", "phone": "09151112233", "agree": True,
                                                   "username": "sara", "password": "secret12"}}).json()
    assert r["ok"]
    return r["code"]


def test_list_count_pagination_and_groups():
    c = _client()
    total = len(services.catalog())
    t = c.get("/books").text
    assert f"{total} کتاب".translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")) in t
    assert t.count('class="book-tile') == catalogue.PAGE_SIZE and 'rel="next"' in t and "catalogue-group-title" in t
    last = c.get("/books", params={"page": 99}).text      # out-of-range page falls back to the last one
    assert last.count('class="book-tile') == total - catalogue.PAGE_SIZE * ((total - 1) // catalogue.PAGE_SIZE)


def test_search_is_forgiving():
    books = services.catalog()
    b = books[0]
    assert catalogue.search(books, b["code"].lower())[0]["code"] == b["code"]
    assert catalogue.search(books, b["code"].replace("-", ""))[0]["code"] == b["code"]
    arabic = b["title"].replace("ی", "ي").replace("ک", "ك")
    assert any(x["code"] == b["code"] for x in catalogue.search(books, arabic))
    assert catalogue.search(books, "zzzz-nothing") == []
    c = _client()
    t = c.get("/books", params={"q": "zzzz-nothing"}).text
    assert "کتابی پیدا نشد" in t and "پاک کردن فیلترها" in t


def test_filters_and_sort():
    c = _client()
    topic = "مغز و ذهن"
    t = c.get("/books", params={"category": topic, "available": "1", "sort": "title"}).text
    assert 'aria-current="true"' in t and "catalogue-group-title" in t
    # an unknown topic is ignored, not an empty page
    assert c.get("/books", params={"category": "x"}).text.count('class="book-tile') == catalogue.PAGE_SIZE


def test_detail_fragment_page_and_404():
    c = _client()
    frag = c.get("/books/NS-001", params={"fragment": "1"})
    assert frag.status_code == 200 and "<html" not in frag.text and "NS-001" in frag.text
    assert "وارد شوید" in frag.text and "/login?next=/books/NS-001" in frag.text   # guest: no fake request button
    full = c.get("/books/ns-001")
    assert full.status_code == 200 and "<html" in full.text and "catalogue.js" in full.text
    assert c.get("/books/NS-999").status_code == 404


def test_member_borrow_and_queue_states(monkeypatch):
    c = _client(monkeypatch)
    code = _signup(c)
    services.approve_payment(code)
    t = c.get("/books/NS-001", params={"fragment": "1"}).text
    assert "data-go-borrow" in t and "درخواست امانت" in t and 'name="days"' in t
    r = c.post("/api/request", json={"code": "NS-001", "days": 14}).json()
    assert r["ok"]
    t = c.get("/books/NS-001", params={"fragment": "1"}).text
    assert "همین حالا به نام توست" in t and "data-go-borrow" not in t
    t = c.get("/books/NS-002", params={"fragment": "1"}).text      # holding a book: the server's reason is shown
    assert "data-go-borrow" not in t and "فقط یک کتاب" in t


def test_login_returns_to_book(monkeypatch):
    c = _client(monkeypatch)
    _signup(c)
    c.get("/logout")
    assert "next" in c.get("/login", params={"next": "/books/NS-001"}).text
    r = c.post("/login/password", data={"username": "sara", "password": "secret12", "next": "/books/NS-001"}, follow_redirects=False)
    assert r.headers["location"] == "/books/NS-001"
    c.get("/logout")
    r = c.post("/login/password", data={"username": "sara", "password": "secret12", "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/me"        # no open redirect
