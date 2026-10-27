import datetime as dt

from fastapi.testclient import TestClient

from app import auth, club, config, services
from app.db import connect
from app.web import create_app

from .conftest import TODAY, make_member

D = dt.timedelta


def _borrow(code, book="NS-001", on=TODAY):
    loan = services.request_book(code, book, 14, on).data["loan_id"]
    services.handover(loan, on=on)
    return loan


def test_club_is_optional_and_logging_needs_a_borrowed_book():
    code = make_member()
    assert not club.log_reading(code, 30, TODAY).ok          # not in the club
    assert club.join(code, TODAY).ok and not club.join(code, TODAY).ok
    res = club.log_reading(code, 30, TODAY)
    assert not res.ok and "امانت" in res.message              # library books only
    _borrow(code)
    assert club.log_reading(code, 30, TODAY).ok


def test_streak_leave_and_badges():
    code = make_member()
    club.join(code, TODAY)
    _borrow(code)
    for i in range(6):
        r = club.log_reading(code, 20, TODAY + D(days=i))
    assert r.data["streak"] == 6
    assert any("اولین گزارش" in n.text for n in club.log_reading(code, 5, TODAY).notices) is False  # only once
    assert club.log_leave(code, TODAY + D(days=6)).ok          # leave keeps the streak
    assert not club.log_leave(code, TODAY + D(days=7)).ok      # one leave per week
    r = club.log_reading(code, 20, TODAY + D(days=7))
    assert r.data["streak"] == 8
    assert any("۷ روز پیاپی" in n.text for n in r.notices)
    assert club.stats(code, TODAY + D(days=9))["streak"] == 0  # a missed day breaks it


def test_minutes_add_up_and_are_capped():
    code = make_member()
    club.join(code, TODAY)
    _borrow(code)
    club.log_reading(code, 30, TODAY)
    assert club.log_reading(code, 15, TODAY).data["today"] == 45
    assert club.log_reading(code, 10_000, TODAY).data["today"] == config.CLUB_MAX_MINUTES_PER_DAY


def test_return_asks_club_members_if_finished_and_leaderboard():
    a, b = make_member(1), make_member(2, phone="09120000000")
    club.join(a, TODAY)
    loan = _borrow(a)
    club.log_reading(a, 40, TODAY)
    r = services.return_book(loan, "سالم", on=TODAY + D(days=3))
    buttons = [btn for n in r.notices if n.member_code == a for row in n.buttons for btn in row]
    assert ("✅ بله، تمامش کردم", f"fin:{loan}:1") in buttons
    fin = club.mark_finished(a, loan, True, 5, TODAY + D(days=3))
    assert fin.ok and any("اولین کتاب" in n.text for n in fin.notices)
    assert [row["code"] for row in club.leaderboard(TODAY)] == [a]
    club.set_public(a, False)
    assert club.leaderboard(TODAY) == []
    # non-club member: plain return, no finish question
    loan_b = _borrow(b, "NS-002")
    r = services.return_book(loan_b, "سالم", on=TODAY + D(days=2))
    assert not any(btn[1].startswith("fin:") for n in r.notices for row in n.buttons for btn in row)


def test_evening_reminder_only_for_unlogged_readers():
    code = make_member()
    club.join(code, TODAY)
    _borrow(code)
    assert len(club.evening_reminders(TODAY)) == 1
    club.log_reading(code, 10, TODAY)
    assert club.evening_reminders(TODAY) == []


def test_login_codes_are_single_use():
    code = make_member(tg=555)
    nonce = auth.new_nonce()
    assert auth.consume(nonce) is None and auth.is_pending(nonce)
    assert auth.approve(nonce, 555) and not auth.approve(nonce, 666)
    assert auth.consume(nonce) == 555 and auth.consume(nonce) is None
    assert services.get_member_by_tg(555)["code"] == code


def test_member_website_flow():
    code = make_member(tg=777)
    c = TestClient(create_app())
    assert c.get("/me", follow_redirects=False).status_code == 303
    nonce = auth.new_nonce(777)                               # link sent by the bot
    assert c.get(f"/login/t/{nonce}", follow_redirects=False).headers["location"] == "/me"
    assert "کد عضویت" in c.get("/me").text
    c.post("/me/join")
    _borrow(code)
    c.post("/me/log", data={"minutes": "25"})
    page = c.get("/me").text
    assert "۲۵ دقیقه" in page and "روز پیاپی" in page
    r = c.post("/api/request", json={"code": "NS-002", "days": 7}).json()   # request from the browser
    assert not r["ok"] and "یک کتاب" in r["message"]
    c.post("/me/suggest", data={"title": "سکوت بهار"})
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0] == 1
    assert "✉️ پیام‌ها" in c.get("/me").text
    assert c.get("/club").status_code == 200 and c.get(f"/club/r/{code}").status_code == 200
    assert c.get("/club/r/M-99").status_code == 404
    assert c.get("/").status_code == 200
