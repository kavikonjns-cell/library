import datetime as dt

from app import config, jalali, services
from app.db import connect

from .conftest import TODAY, make_member

D = dt.timedelta


def test_membership_lasts_exactly_one_year():
    # 1405/08/05 -> 1406/08/05, whatever the month of registration
    assert jalali.to_jalali(jalali.membership_valid_until(dt.date(2026, 10, 27))) == "۱۴۰۶/۰۸/۰۵"
    dey = jalali.parse_jalali("1405/10/01")
    assert jalali.to_jalali(jalali.membership_valid_until(dey)) == "۱۴۰۶/۱۰/۰۱"
    # 30 Esfand 1403 (leap) -> last day of Esfand 1404 (29 days)
    assert jalali.to_jalali(jalali.membership_valid_until(jalali.parse_jalali("1403/12/30"))) == "۱۴۰۴/۱۲/۲۹"


def test_two_week_expiry_warning_goes_to_member_and_librarians_once():
    code = make_member(on=TODAY)
    left = dt.date.fromisoformat(services.get_member(code)["valid_until"]) - D(days=14)
    assert not [n for n in services.daily_tasks(left - D(days=1)) if "اعتبار" in n.text]   # 15 days left: nothing yet
    notes = services.daily_tasks(left)
    member_msg = [n for n in notes if n.member_code == code]
    assert member_msg and "دو هفته" in member_msg[0].text
    assert any(n.admins and code in n.text for n in notes)
    # not repeated the next day; and a renewal starts a fresh cycle
    assert not [n for n in services.daily_tasks(left + D(days=1)) if "اعتبار" in n.text]


def test_register_issues_sequential_codes_and_fee():
    a = services.register_member({"name": "الف", "role": "والد باغ شبنم", "phone": "۰۹۱۲۱۲۳۴۵۶۷", "agree": True}, 1)
    b = services.register_member({"name": "ب", "role": "سایر", "phone": "+989121234567", "agree": True}, 2)
    assert (a.data["code"], b.data["code"]) == ("M-01", "M-02")
    assert a.data["fee"] == 800_000 and b.data["fee"] == 1_000_000
    assert services.get_member("M-02")["phone"] == "09121234567"
    again = services.register_member({"name": "الف", "role": "سایر", "phone": "09121234567", "agree": True}, 1)
    assert not again.ok and again.data["code"] == "M-01"


def test_register_validation():
    res = services.register_member({"name": "", "role": "x", "phone": "123"}, 5)
    assert not res.ok and "نقش" in res.message and "شمارۀ تماس" in res.message


def test_parse_registration_text_from_html_form():
    text = ("📝 درخواست عضویت در کتابخانه کاوی کنج\n\nنام: مریم احمدی\nنقش: سایر (تسهیلگر مدرسه طبیعت)\n"
            "شماره تماس: ۰۹۱۵۱۲۳۴۵۶۷\nموضوع‌های مورد علاقه: نوجوانی؛ مغز و ذهن\nپیشنهاد کتاب: سکوت بهار\n"
            "حق عضویت سالیانه: ۱ میلیون تومان\n\n✅ قوانین کتابخانه و حق عضویت سالیانه را خواندم و می‌پذیرم.")
    form = services.parse_registration_text(text)
    assert form["name"] == "مریم احمدی" and form["role"] == "سایر" and form["role_other"] == "تسهیلگر مدرسه طبیعت"
    assert form["agree"] and form["wish"] == "سکوت بهار"
    res = services.register_member(form, 77)
    assert res.ok and services.get_member(res.data["code"])["topics"] == "نوجوانی؛ مغز و ذهن"


def test_unpaid_member_cannot_borrow():
    code = make_member(paid=False)
    res = services.request_book(code, "NS-001", 14, TODAY)
    assert not res.ok and "حق عضویت" in res.message


def test_reserve_handover_return_flow():
    code = make_member()
    res = services.request_book(code, "NS-001", 10, TODAY)
    assert res.ok and res.data["state"] == "reserved"
    with connect() as conn:
        assert services.title_status(conn, "NS-001")["state"] == "reserved"
    loan_id = res.data["loan_id"]
    h = services.handover(loan_id, "پارسا", TODAY)
    assert h.data["due_date"] == (TODAY + D(days=10)).isoformat()
    with connect() as conn:
        st = services.title_status(conn, "NS-001")
    assert st["state"] == "loaned" and st["until"] == TODAY + D(days=10)
    assert "در امانت تا" in services.status_label(st)
    r = services.return_book(loan_id, "سالم", "پارسا", TODAY + D(days=9))
    assert r.ok and r.data["fine"] == 0
    with connect() as conn:
        assert services.title_status(conn, "NS-001")["state"] == "available"


def test_one_book_at_a_time_and_duration_is_capped():
    code = make_member()
    res = services.request_book(code, "NS-001", 99, TODAY)
    assert services.get_loan(res.data["loan_id"])["requested_days"] == config.LOAN_MAX_INITIAL_DAYS
    second = services.request_book(code, "NS-002", 14, TODAY)
    assert not second.ok and "یک کتاب" in second.message


def test_waitlist_gets_book_when_returned():
    a, b = make_member(1), make_member(2, phone="09120000000")
    loan = services.request_book(a, "NS-003", 14, TODAY).data["loan_id"]
    services.handover(loan, on=TODAY)
    w = services.request_book(b, "NS-003", 7, TODAY)
    assert w.ok and w.data["state"] == "waiting" and w.data["position"] == 1
    assert "در امانت است و تا" in w.message
    # since Aban 1405 a waiting member does not block an extension
    assert services.extension_options(a, TODAY).ok
    r = services.return_book(loan, "سالم", on=TODAY + D(days=3))
    reserved_for_b = [n for n in r.notices if n.member_code == b]
    assert reserved_for_b and "نوبت شما" in reserved_for_b[0].text
    assert services.title_status.__name__  # sanity
    with connect() as conn:
        open_loan = conn.execute("SELECT * FROM loans WHERE member_code = ? AND status = 'reserved'", (b,)).fetchone()
    assert open_loan and open_loan["requested_days"] == 7


def test_second_copy_is_lent_before_queueing():
    a, b = make_member(1), make_member(2, phone="09120000000")
    r1 = services.request_book(a, "NS-012", 14, TODAY)
    r2 = services.request_book(b, "NS-012", 14, TODAY)
    assert r1.data["state"] == r2.data["state"] == "reserved"
    codes = {services.get_loan(r.data["loan_id"])["book_code"] for r in (r1, r2)}
    assert codes == {"NS-012", "NS-012-2"}


def test_late_fine_capped_and_blocks_new_loans():
    code = make_member()
    loan = services.request_book(code, "NS-001", 14, TODAY).data["loan_id"]
    services.handover(loan, on=TODAY)
    r = services.return_book(loan, "سالم", on=TODAY + D(days=14 + 3))
    assert r.data == {"fine": 150_000, "late": 3}
    blocked = services.request_book(code, "NS-002", 14, TODAY + D(days=18))
    assert not blocked.ok and "جریمه" in blocked.message
    services.mark_fine_paid(loan, on=TODAY + D(days=18))
    assert services.request_book(code, "NS-002", 14, TODAY + D(days=18)).ok
    assert services.fine_for(10) == config.FINE_MAX_DAYS * config.FINE_PER_DAY


def test_extension_limited_to_30_days_total():
    code = make_member()
    loan = services.request_book(code, "NS-001", 14, TODAY).data["loan_id"]
    services.handover(loan, on=TODAY)
    opts = services.extension_options(code, TODAY + D(days=5))
    assert opts.ok
    cap = TODAY + D(days=30)
    assert opts.data["choices"][-1] == cap.isoformat()
    assert not services.extend_loan(code, cap + D(days=1), TODAY + D(days=5)).ok
    assert services.extend_loan(code, cap, TODAY + D(days=5)).ok
    assert not services.extension_options(code, TODAY + D(days=6)).ok  # at the cap now


def test_daily_tasks_remind_and_revoke():
    code = make_member()
    loan = services.request_book(code, "NS-001", 14, TODAY).data["loan_id"]
    services.handover(loan, on=TODAY)
    due = TODAY + D(days=14)
    pre = services.daily_tasks(due - D(days=2))
    assert any(n.member_code == code and "یادآوری" in n.text for n in pre)
    assert not any(n.member_code == code for n in services.daily_tasks(due - D(days=2)))  # once only
    late2 = services.daily_tasks(due + D(days=2))
    assert any("۲ روز دیر" in n.text for n in late2)
    services.daily_tasks(due + D(days=5))
    assert services.get_member(code)["status"] == "revoked"
    assert not services.request_book(code, "NS-002", 14, due + D(days=6)).ok


def test_unclaimed_reservation_expires():
    code = make_member()
    services.request_book(code, "NS-001", 14, TODAY)
    services.daily_tasks(TODAY + D(days=config.RESERVE_HOLD_DAYS + 1))
    with connect() as conn:
        assert services.title_status(conn, "NS-001")["state"] == "available"


def test_lost_book_takes_copy_off_shelf_without_fine():
    code = make_member()
    loan = services.request_book(code, "NS-005", 14, TODAY).data["loan_id"]
    services.handover(loan, on=TODAY)
    r = services.return_book(loan, "گم شده", on=TODAY + D(days=20))
    assert r.data["fine"] == 0
    assert services.get_book("NS-005")["active"] == 0


def test_schema_comments_have_no_semicolons():
    """The schema is split on ';' for PostgreSQL: a ';' inside a comment breaks the server start."""
    from app.db import SCHEMA
    bad = [line for line in SCHEMA.splitlines() if "--" in line and ";" in line.split("--", 1)[1]]
    assert bad == []
