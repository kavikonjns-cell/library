"""Website signup with a username and password, password sign-in, recovery and the admin view."""
from fastapi.testclient import TestClient

from app import accounts, config, notify, services
from app.web import create_app

FORM = {"name": "سارا احمدی", "role": "سایر", "role_other": "والد", "phone": "09151112233", "agree": True,
        "username": "Sara.A", "password": "secret12"}


def _client(monkeypatch, sent=None):
    async def fake_send(chat_id, text, buttons=None):
        if sent is not None:
            sent.append((chat_id, text))

    async def fake_deliver(notices):
        pass
    monkeypatch.setattr(notify, "send", fake_send)
    monkeypatch.setattr(notify, "deliver", fake_deliver)
    return TestClient(create_app())


def test_password_hash_and_admin_copy():
    h = accounts.hash_password("secret12")
    assert "secret12" not in h and accounts.check_password("secret12", h) and not accounts.check_password("nope", h)
    res = services.register_member(FORM)
    assert res.ok and "sara.a" in res.message
    m = services.get_member(res.data["code"])
    assert m["username"] == "sara.a" and "secret12" not in m["password_enc"] and accounts.reveal(m) == "secret12"


def test_signup_rules():
    assert not services.register_member({**FORM, "username": "ab"}).ok
    assert not services.register_member({**FORM, "password": "123"}).ok
    assert services.register_member(FORM).ok
    dup = services.register_member({**FORM, "phone": "09150000000", "username": "SARA.A"})
    assert not dup.ok and "برداشته" in dup.message
    # signup in the bot still works without a username
    assert services.register_member({"name": "علی", "role": "سایر", "phone": "09150000001", "agree": True}, 77).ok


def test_web_signup_signs_in_and_password_login(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/register/web", json={"form": FORM}).json()
    assert r["ok"] and r["code"] == "M-01"
    assert c.get("/me").status_code == 200  # signed in right after signup
    c.get("/logout")
    assert c.get("/me", follow_redirects=False).status_code == 303
    r = c.post("/login/password", data={"username": "sara.a", "password": "wrong"}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    r = c.post("/login/password", data={"username": "SARA.A", "password": "secret12"}, follow_redirects=False)
    assert r.headers["location"] == "/me"
    c.get("/logout")
    r = c.post("/login/password", data={"username": "M-01", "password": "secret12"}, follow_redirects=False)
    assert r.headers["location"] == "/me"
    assert "sara.a" in c.get("/me").text
    for page in ["/login", "/forgot", "/app/register"]:
        assert c.get(page).status_code == 200, page


def test_forgot_password_through_bot(monkeypatch):
    sent = []
    c = _client(monkeypatch, sent)
    code = services.register_member({**FORM}, tg_user_id=555).data["code"]
    c.post("/forgot", data={"who": "09151112233"})
    assert sent and sent[0][0] == 555 and "/reset/" in sent[0][1]
    token = sent[0][1].split("/reset/")[1].split()[0]
    assert "sara.a" in c.get(f"/reset/{token}").text
    r = c.post(f"/reset/{token}", data={"password": "newpass1", "password2": "newpass1"}, follow_redirects=False)
    assert r.headers["location"] == "/me"
    assert accounts.authenticate("sara.a", "newpass1")["code"] == code
    assert accounts.authenticate("sara.a", "secret12") is None
    assert "منقضی" in c.get(f"/reset/{token}").text  # single use


def test_bot_member_chooses_username_with_reset_link(monkeypatch):
    sent = []
    c = _client(monkeypatch, sent)
    services.register_member({"name": "علی", "role": "سایر", "phone": "09150000001", "agree": True}, 77)
    c.post("/forgot", data={"who": "M-01"})
    token = sent[0][1].split("/reset/")[1].split()[0]
    c.post(f"/reset/{token}", data={"username": "ali", "password": "pass123", "password2": "pass123"})
    assert accounts.authenticate("ali", "pass123")


def test_admin_sees_username_and_password_and_sets_new(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "pw")
    c = _client(monkeypatch)
    services.register_member(FORM)
    c.post("/admin/login", data={"password": "pw"})
    assert "sara.a" in c.get("/admin/members").text
    page = c.get("/admin/members/M-01").text
    assert "sara.a" in page and "secret12" in page
    c.post("/admin/members/M-01/password", data={"username": "", "password": ""})
    new = accounts.reveal(services.get_member("M-01"))
    assert new and new != "secret12" and accounts.authenticate("sara.a", new)


def test_member_changes_password_from_account(monkeypatch):
    c = _client(monkeypatch)
    c.post("/api/register/web", json={"form": FORM})
    c.post("/me/account", data={"username": "sara.a", "current": "bad", "password": "x123456", "password2": "x123456"})
    assert accounts.authenticate("sara.a", "secret12")
    c.post("/me/account", data={"username": "sara2", "current": "secret12", "password": "x123456", "password2": "x123456"})
    assert accounts.authenticate("sara2", "x123456")
