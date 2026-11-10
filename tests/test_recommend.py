"""«کشف بعدی تو»: suggestions come only from the member's own loans and ticked topics."""
from app import recommend, services
from app.db import connect
from tests.conftest import make_member


def _borrow_and_return(code, book, rating=0, finished=False):
    with connect() as conn:
        conn.execute("INSERT INTO loans (book_code, member_code, status, requested_days, requested_at, handed_at, due_date,"
                     " returned_at, rating, finished) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (book, code, "returned", 14, "2026-10-01", "2026-10-02", "2026-10-16", "2026-10-10", rating, int(finished)))


def test_no_signal_no_suggestions():
    code = make_member()
    assert recommend.suggest(code) == {"basis": "none", "items": []}


def test_suggests_same_topic_not_borrowed_with_real_reason():
    code = make_member()
    book = services.catalog()[0]
    _borrow_and_return(code, book["code"], rating=5, finished=True)
    res = recommend.suggest(code, limit=3)
    assert res["basis"] == "history" and res["items"]
    for it in res["items"]:
        assert it["book"]["code"] != book["code"]                    # never the same title again
        assert book["title"] in it["reason"]                          # reason names the real source book
        assert set([it["book"]["topic1"], it["book"]["topic2"]]) & set([book["topic1"], book["topic2"]])


def test_current_loan_excluded_and_interests_only():
    code = make_member()
    with connect() as conn:
        conn.execute("UPDATE members SET topics = ? WHERE code = ?", ("مغز و ذهن", code))
    res = recommend.suggest(code)
    assert res["basis"] == "interests" and res["items"]
    assert all("مغز و ذهن" in it["reason"] for it in res["items"])
    held = res["items"][0]["book"]["code"]
    assert services.request_book(code, held, 14).ok
    assert held not in [it["book"]["code"] for it in recommend.suggest(code)["items"]]
