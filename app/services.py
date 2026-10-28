"""Library business logic.

Every public function here works on the database and returns a ``Result``:
a human readable message (Persian) plus a list of ``Notice`` objects that the
Telegram layer delivers to members and librarians. Nothing in this module
talks to Telegram directly, so it is easy to test.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass, field

from . import config
from .db import connect
from .jalali import en, fa, jalali_year, money, membership_valid_until, to_jalali, today as _today

Button = tuple[str, str]  # (label, callback_data)


@dataclass
class Notice:
    text: str
    member_code: str | None = None     # deliver to this member's Telegram
    admins: bool = False               # deliver to every librarian
    buttons: list[list[Button]] = field(default_factory=list)
    inbox: bool = True                 # also keep it in the member's inbox on the website


@dataclass
class Result:
    ok: bool
    message: str
    notices: list[Notice] = field(default_factory=list)
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------- helpers

def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def _date(s: str | None) -> dt.date | None:
    return dt.date.fromisoformat(s[:10]) if s else None


def fee_for_role(role: str) -> int:
    return config.ROLE_FEES.get(role, config.ROLE_FEES["سایر"])


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"[\s\-()]", "", en(raw or ""))
    if digits.startswith("+98"):
        digits = "0" + digits[3:]
    elif digits.startswith("0098"):
        digits = "0" + digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif digits.startswith("9") and len(digits) == 10:
        digits = "0" + digits
    return digits


def valid_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"09\d{9}", phone))


def get_member(code: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM members WHERE code = ?", (code.upper(),)).fetchone()


def get_member_by_tg(tg_user_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM members WHERE tg_user_id = ?", (tg_user_id,)).fetchone()


def get_book(code: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM books WHERE code = ?", (code.upper(),)).fetchone()


def get_loan(loan_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT l.*, b.title, b.base_code, m.name AS member_name FROM loans l "
            "JOIN books b ON b.code = l.book_code JOIN members m ON m.code = l.member_code "
            "WHERE l.id = ?", (loan_id,)).fetchone()


def late_days(loan, on: dt.date) -> int:
    due = _date(loan["due_date"])
    if not due:
        return 0
    ref = _date(loan["returned_at"]) or on
    return max(0, (ref - due).days)


def fine_for(days_late: int) -> int:
    return min(days_late, config.FINE_MAX_DAYS) * config.FINE_PER_DAY


def max_date(loan) -> dt.date | None:
    handed = _date(loan["handed_at"])
    return handed + dt.timedelta(days=config.LOAN_MAX_TOTAL_DAYS) if handed else None


# ------------------------------------------------------------- membership

def _next_member_code(conn) -> tuple[str, int]:
    seq = (conn.execute("SELECT MAX(seq) FROM members").fetchone()[0] or 0) + 1
    return f"M-{seq:02d}", seq


def membership_problems(member, on: dt.date) -> list[str]:
    """Reasons this member may not borrow right now (membership only)."""
    if member is None:
        return ["هنوز عضو کتابخانه نیستید. اول فرم عضویت را پر کنید."]
    problems = []
    if member["status"] == "revoked":
        problems.append("عضویت شما به دلیل تأخیر ۵ روزه باطل شده است. برای عضویت دوباره با مسئول کتابخانه صحبت کنید.")
    if member["payment_status"] != "paid":
        problems.append("پرداخت حق عضویت شما هنوز تأیید نشده است. عکس فیش واریز را در همین ربات بفرستید.")
    if _date(member["valid_until"]) < on:
        problems.append(f"اعتبار عضویت شما در {to_jalali(member['valid_until'])} تمام شده است. برای تمدید عضویت با مسئول کتابخانه هماهنگ کنید.")
    return problems


def register_member(form: dict, tg_user_id: int | None = None, tg_username: str = "",
                    on: dt.date | None = None) -> Result:
    """Create a member from the membership form and issue a member code."""
    on = on or _today()
    name = (form.get("name") or "").strip()
    role = (form.get("role") or "").strip()
    phone = normalize_phone(form.get("phone", ""))
    tg_phone = normalize_phone(form.get("tg_phone", ""))
    errors = []
    if not name:
        errors.append("نام و نام خانوادگی را بنویسید.")
    if role not in config.ROLE_FEES:
        errors.append("نقش را انتخاب کنید (عضو باغ شبنم، والد باغ شبنم یا سایر).")
    if not valid_phone(phone):
        errors.append("شمارۀ تماس معتبر بنویسید (۱۱ رقم و با ۰۹ شروع شود).")
    if not form.get("agree"):
        errors.append("برای عضویت، پذیرش قوانین کتابخانه لازم است.")
    if errors:
        return Result(False, "\n".join(errors))
    if form.get("_validate_only"):
        return Result(True, "")

    topics = form.get("topics") or []
    if isinstance(topics, str):
        topics = [t.strip() for t in re.split(r"[؛;،,]", topics) if t.strip()]

    with connect() as conn:
        if tg_user_id:
            old = conn.execute("SELECT * FROM members WHERE tg_user_id = ?", (tg_user_id,)).fetchone()
            if old:
                return Result(False, f"شما قبلاً با کد عضویت {old['code']} ثبت‌نام کرده‌اید.",
                              data={"code": old["code"], "existing": True})
        code, seq = _next_member_code(conn)
        fee = fee_for_role(role)
        valid_until = membership_valid_until(on)
        conn.execute(
            "INSERT INTO members (code, seq, tg_user_id, tg_username, name, role, role_other, child_name,"
            " child_group, phone, tg_phone, topics, wish, fee, registered_at, valid_until)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, seq, tg_user_id, tg_username or "", name, role, (form.get("role_other") or "").strip(),
             (form.get("child_name") or "").strip(), (form.get("child_group") or "").strip(), phone,
             tg_phone if tg_phone and tg_phone != phone else "", "؛ ".join(topics),
             (form.get("wish") or "").strip(), fee, on.isoformat(), valid_until.isoformat()))

    msg = (f"🎉 ثبت‌نام شما انجام شد.\n\n"
           f"کد عضویت: {code}\n"
           f"حق عضویت سالیانه: {money(fee)}\n"
           f"اعتبار عضویت: تا پایان اسفند {fa(jalali_year(valid_until))}\n\n"
           f"💳 لطفاً حق عضویت را به کارت {fa(_card())} به نام {config.CARD_OWNER} واریز کنید "
           f"و عکس فیش را همین‌جا در ربات بفرستید. بعد از تأیید پرداخت می‌توانید کتاب امانت بگیرید.")
    admin = Notice(
        admins=True,
        text=(f"🆕 عضو تازه: {code}\n{name} · {role}{' (' + form.get('role_other', '') + ')' if form.get('role_other') else ''}\n"
              f"تماس: {fa(phone)}\nحق عضویت: {money(fee)}\nمنتظر فیش واریز."),
    )
    return Result(True, msg, [admin], {"code": code, "fee": fee, "valid_until": valid_until.isoformat()})


def _card() -> str:
    c = config.CARD_NUMBER
    return " ".join(c[i:i + 4] for i in range(0, len(c), 4))


_FIELD_PATTERNS = {
    "name": r"^نام(?: و نام خانوادگی)?\s*:\s*(.+)$",
    "role": r"^نقش[^:]*:\s*(.+)$",
    "child_name": r"^نام کودک\s*:\s*(.+)$",
    "child_group": r"^گروه کودک\s*:\s*(.+)$",
    "child_both": r"^نام و گروه کودک[^:]*:\s*(.+)$",
    "phone": r"^شماره(?:ٔ|ۀ)? تماس\s*:\s*(.+)$",
    "tg_phone": r"^شماره(?:ٔ|ۀ)? تلگرام\s*:\s*(.+)$",
    "topics": r"^موضوع‌?\s?های مورد علاقه\s*:\s*(.+)$",
    "wish": r"^پیشنهاد کتاب\s*:\s*(.+)$",
}


def parse_registration_text(text: str) -> dict | None:
    """Read the message produced by the HTML membership form (or its paper template)."""
    if "عضویت" not in text or "نام" not in text:
        return None
    form: dict = {}
    for line in text.splitlines():
        line = line.strip().replace("‏", "")
        for key, pat in _FIELD_PATTERNS.items():
            m = re.match(pat, line)
            if m and m.group(1).strip() and key not in form:
                form[key] = m.group(1).strip()
                break
    if "child_both" in form:
        form.setdefault("child_name", form.pop("child_both"))
    role_text = form.get("role", "")
    for role in config.ROLE_FEES:
        if role_text.startswith(role):
            form["role"] = role
            m = re.search(r"\((.+)\)", role_text)
            if role == "سایر" and m:
                form["role_other"] = m.group(1)
            break
    form["agree"] = "می‌پذیرم" in text or "میپذیرم" in text
    return form if form.get("name") else None


PENDING_FORM_DAYS = 2


def save_pending_form(form: dict, on: dt.datetime | None = None) -> Result:
    """Keep a form filled outside Telegram until its owner opens the bot; returns a one-time token."""
    import json
    import secrets

    check = register_member({**form, "_validate_only": True})
    if not check.ok:
        return check
    token = secrets.token_urlsafe(12)
    stamp = (on or dt.datetime.now()).isoformat(timespec="seconds")
    with connect() as conn:
        cutoff = ((on or dt.datetime.now()) - dt.timedelta(days=PENDING_FORM_DAYS)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM pending_forms WHERE created_at < ?", (cutoff,))
        conn.execute("INSERT INTO pending_forms (token, form, created_at) VALUES (?, ?, ?)",
                     (token, json.dumps(form, ensure_ascii=False), stamp))
    return Result(True, "", data={"token": token})


def take_pending_form(token: str) -> dict | None:
    import json

    with connect() as conn:
        row = conn.execute("SELECT * FROM pending_forms WHERE token = ?", (token,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM pending_forms WHERE token = ?", (token,))
    if dt.datetime.now() - dt.datetime.fromisoformat(row["created_at"]) > dt.timedelta(days=PENDING_FORM_DAYS):
        return None
    return json.loads(row["form"])


def attach_receipt(member_code: str, file_id: str) -> Result:
    with connect() as conn:
        conn.execute("UPDATE members SET receipt_file_id = ? WHERE code = ?", (file_id, member_code))
        m = conn.execute("SELECT * FROM members WHERE code = ?", (member_code,)).fetchone()
    return Result(True, "🧾 فیش شما دریافت شد و برای مسئول کتابخانه فرستاده شد. بعد از تأیید خبرتان می‌کنیم.",
                  data={"member": dict(m)})


def receipt_review(member) -> tuple[str, list[list[Button]]]:
    """Caption and librarian buttons for a receipt a member sent (fine first, then membership fee)."""
    with connect() as conn:
        fine = conn.execute("SELECT * FROM loans WHERE member_code = ? AND fine > 0 AND fine_paid = 0"
                            " AND status IN ('returned','lost') ORDER BY id LIMIT 1", (member["code"],)).fetchone()
    if fine:
        return (f"🧾 فیش جریمه از {member['code']} ({member['name']})\nامانت #{fa(fine['id'])} · مبلغ {money(fine['fine'])}",
                [[("✅ جریمه تسویه شد", f"fine_ok:{fine['id']}")]])
    if member["payment_status"] != "paid":
        return ((f"🧾 فیش حق عضویت از {member['code']} · {member['name']}\nنقش: {member['role']} · "
                 f"مبلغ: {money(member['fee'])}\nتماس: {fa(member['phone'])}"),
                [[("✅ تأیید پرداخت", f"pay_ok:{member['code']}"), ("❌ رد فیش", f"pay_no:{member['code']}")]])
    return f"📎 فایل از {member['code']} ({member['name']})", []


def approve_payment(member_code: str, method: str = "کارت‌به‌کارت", by: str = "",
                    on: dt.date | None = None) -> Result:
    on = on or _today()
    with connect() as conn:
        m = conn.execute("SELECT * FROM members WHERE code = ?", (member_code,)).fetchone()
        if not m:
            return Result(False, "عضوی با این کد پیدا نشد.")
        if m["payment_status"] == "paid":
            return Result(False, f"پرداخت {member_code} قبلاً تأیید شده است.")
        conn.execute("UPDATE members SET payment_status = 'paid', status = 'active' WHERE code = ?", (member_code,))
        conn.execute(
            "INSERT INTO payments (date, category, direction, amount, member_code, method, description)"
            " VALUES (?, 'حق عضویت', 'in', ?, ?, ?, ?)",
            (on.isoformat(), m["fee"], member_code, method, f"حق عضویت {m['name']}" + (f" · ثبت: {by}" if by else "")))
    return Result(True, f"پرداخت حق عضویت {member_code} ({m['name']}) تأیید شد.", [Notice(
        member_code=member_code,
        text=(f"✅ پرداخت حق عضویت شما تأیید شد. عضویت شما فعال است.\n"
              f"کد عضویت: {member_code}\nاعتبار تا: {to_jalali(m['valid_until'])}\n\n"
              f"حالا می‌توانید از «📚 فهرست کتاب‌ها» کتاب مورد نظرتان را انتخاب کنید."))])


def reject_payment(member_code: str, reason: str = "") -> Result:
    with connect() as conn:
        m = conn.execute("SELECT * FROM members WHERE code = ?", (member_code,)).fetchone()
        if not m:
            return Result(False, "عضوی با این کد پیدا نشد.")
        conn.execute("UPDATE members SET payment_status = 'rejected' WHERE code = ?", (member_code,))
    return Result(True, f"فیش {member_code} رد شد.", [Notice(
        member_code=member_code,
        text="⚠️ فیش واریز شما تأیید نشد" + (f": {reason}" if reason else ".") +
             " لطفاً دوباره بررسی کنید و عکس درست فیش را بفرستید، یا با مسئول کتابخانه تماس بگیرید.")])


def renew_membership(member_code: str, role: str | None = None, on: dt.date | None = None) -> Result:
    """Start a new membership year (or re-join after revocation). Payment becomes pending."""
    on = on or _today()
    with connect() as conn:
        m = conn.execute("SELECT * FROM members WHERE code = ?", (member_code,)).fetchone()
        if not m:
            return Result(False, "عضوی با این کد پیدا نشد.")
        role = role or m["role"]
        valid_until = membership_valid_until(on)
        conn.execute(
            "UPDATE members SET role = ?, fee = ?, registered_at = ?, valid_until = ?, payment_status = 'pending',"
            " status = 'active' WHERE code = ?",
            (role, fee_for_role(role), on.isoformat(), valid_until.isoformat(), member_code))
    return Result(True, f"عضویت {member_code} تا {to_jalali(valid_until)} تمدید شد؛ منتظر پرداخت.", [Notice(
        member_code=member_code,
        text=(f"🔁 عضویت شما تا {to_jalali(valid_until)} تمدید شد. لطفاً حق عضویت ({money(fee_for_role(role))}) را "
              f"به کارت {fa(_card())} واریز کنید و عکس فیش را در ربات بفرستید."))])


def set_member_status(member_code: str, status: str) -> Result:
    with connect() as conn:
        conn.execute("UPDATE members SET status = ? WHERE code = ?", (status, member_code))
    return Result(True, "وضعیت عضو به‌روز شد.")


# ---------------------------------------------------------- availability

def _open_loan_of_copy(conn, code: str):
    return conn.execute(
        "SELECT * FROM loans WHERE book_code = ? AND status IN ('reserved','active') ORDER BY id DESC LIMIT 1",
        (code,)).fetchone()


def _free_copy(conn, base_code: str) -> str | None:
    row = conn.execute(
        "SELECT b.code FROM books b WHERE b.base_code = ? AND b.active = 1 AND NOT EXISTS ("
        " SELECT 1 FROM loans l WHERE l.book_code = b.code AND l.status IN ('reserved','active'))"
        " ORDER BY b.code LIMIT 1", (base_code,)).fetchone()
    return row["code"] if row else None


def title_status(conn, base_code: str) -> dict:
    """Shelf status of a title over all its copies."""
    copies = conn.execute("SELECT code FROM books WHERE base_code = ? AND active = 1", (base_code,)).fetchall()
    open_loans = conn.execute(
        "SELECT l.* FROM loans l JOIN books b ON b.code = l.book_code"
        " WHERE b.base_code = ? AND b.active = 1 AND l.status IN ('reserved','active')", (base_code,)).fetchall()
    waiting = conn.execute(
        "SELECT COUNT(*) FROM waitlist WHERE base_code = ? AND status = 'waiting'", (base_code,)).fetchone()[0]
    total, busy = len(copies), len(open_loans)
    state, until = "available", None
    if total == 0:
        state = "unavailable"
    elif busy >= total:
        active = [l for l in open_loans if l["status"] == "active"]
        if active:
            state = "loaned"
            until = min(_date(l["due_date"]) for l in active)
        else:
            state = "reserved"
            until = min(_date(l["reserve_expires"]) for l in open_loans)
    return {"state": state, "until": until, "copies": total, "free": max(0, total - busy), "waiting": waiting}


def status_label(st: dict) -> str:
    if st["state"] == "available":
        extra = f" ({fa(st['free'])} نسخه)" if st["copies"] > 1 else ""
        return f"✅ موجود در قفسه{extra}"
    if st["state"] == "loaned":
        return f"📕 در امانت تا {to_jalali(st['until'])}"
    if st["state"] == "reserved":
        return f"⏳ رزرو شده تا {to_jalali(st['until'])}"
    return "⛔ فعلاً در دسترس نیست"


def catalog() -> list[dict]:
    """One entry per title (copies merged) with live status, in topic order."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM books WHERE code = base_code ORDER BY code").fetchall()
        out = []
        for b in rows:
            st = title_status(conn, b["base_code"])
            out.append({**dict(b), "status": st, "status_label": status_label(st)})
    return out


# ----------------------------------------------------------------- loans

def _member_open_loan(conn, member_code: str):
    return conn.execute(
        "SELECT l.*, b.title, b.base_code FROM loans l JOIN books b ON b.code = l.book_code"
        " WHERE l.member_code = ? AND l.status IN ('reserved','active') ORDER BY l.id DESC LIMIT 1",
        (member_code,)).fetchone()


def _unpaid_fines(conn, member_code: str) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(fine), 0) FROM loans WHERE member_code = ? AND fine > 0 AND fine_paid = 0"
        " AND status IN ('returned','lost')", (member_code,)).fetchone()[0]


def borrow_problems(conn, member, on: dt.date) -> list[str]:
    problems = membership_problems(member, on)
    if member is None:
        return problems
    current = _member_open_loan(conn, member["code"])
    if current:
        word = "رزرو کرده‌اید" if current["status"] == "reserved" else "در دست دارید"
        problems.append(f"هر عضو در یک زمان فقط یک کتاب امانت می‌گیرد و شما الان «{current['title']}» را {word}.")
    unpaid = _unpaid_fines(conn, member["code"])
    if unpaid:
        problems.append(f"جریمۀ دیرکرد پرداخت‌نشده دارید ({money(unpaid)}). تا جریمه تسویه نشود، کتاب تازه امانت داده نمی‌شود.")
    return problems


def clamp_days(days: int | None) -> int:
    days = days or config.LOAN_DEFAULT_DAYS
    return max(1, min(int(days), config.LOAN_MAX_INITIAL_DAYS))


def request_book(member_code: str | None, code: str, days: int | None = None,
                 on: dt.date | None = None) -> Result:
    """A member asks for a book: analyse the request, then reserve it or queue the member."""
    on = on or _today()
    days = clamp_days(days)
    with connect() as conn:
        book = conn.execute("SELECT * FROM books WHERE code = ? OR base_code = ? ORDER BY code LIMIT 1",
                            (code.upper(), code.upper())).fetchone()
        if not book:
            return Result(False, f"کتابی با کد {code} در فهرست نیست.")
        base = book["base_code"]
        member = conn.execute("SELECT * FROM members WHERE code = ?", (member_code or "",)).fetchone()
        title = f"«{book['title']}» ({base})"

        problems = borrow_problems(conn, member, on)
        if member is not None:
            current = _member_open_loan(conn, member["code"])
            if current and current["base_code"] == base:
                return Result(False, f"{title} همین حالا به نام شماست.")

        copy = _free_copy(conn, base)
        if problems:
            # Membership problems stop everything; holding a book still allows joining the queue.
            hard = membership_problems(member, on)
            if hard or copy:
                return Result(False, "درخواست شما برای " + title + " ثبت نشد:\n\n• " + "\n• ".join(problems))

        if copy and not problems:
            expires = on + dt.timedelta(days=config.RESERVE_HOLD_DAYS)
            cur = conn.execute(
                "INSERT INTO loans (book_code, member_code, status, requested_days, requested_at, reserve_expires)"
                " VALUES (?, ?, 'reserved', ?, ?, ?)",
                (copy, member["code"], days, on.isoformat(), expires.isoformat()))
            loan_id = cur.lastrowid
            conn.execute("UPDATE waitlist SET status = 'fulfilled' WHERE base_code = ? AND member_code = ?"
                         " AND status = 'waiting'", (base, member["code"]))
            return Result(True,
                          f"✅ کتاب {title} در قفسه موجود است و برای شما رزرو شد.\n\n"
                          f"📍 لطفاً تا {to_jalali(expires)} کتاب را از {config.PICKUP_PLACE} تحویل بگیرید.\n"
                          f"⏱ مدت امانت درخواستی: {fa(days)} روز؛ مهلت بازگشت از روز تحویل حساب می‌شود.",
                          [_reservation_notice(loan_id, copy, book["title"], member, days, expires)],
                          {"loan_id": loan_id, "state": "reserved"})

        # No free copy: put the member in the queue.
        st = title_status(conn, base)
        already = conn.execute("SELECT id FROM waitlist WHERE base_code = ? AND member_code = ? AND status = 'waiting'",
                               (base, member["code"])).fetchone()
        if not already:
            conn.execute("INSERT INTO waitlist (base_code, member_code, requested_at, requested_days)"
                         " VALUES (?, ?, ?, ?)", (base, member["code"], on.isoformat(), days))
        position = conn.execute(
            "SELECT COUNT(*) FROM waitlist WHERE base_code = ? AND status = 'waiting' AND id <= ("
            " SELECT id FROM waitlist WHERE base_code = ? AND member_code = ? AND status = 'waiting')",
            (base, base, member["code"])).fetchone()[0]
    when = (f"در امانت است و تا {to_jalali(st['until'])} باید برگردد" if st["state"] == "loaned"
            else f"رزرو شده است (تا {to_jalali(st['until'])})" if st["state"] == "reserved"
            else "فعلاً در دسترس نیست")
    note = ("\n\nℹ️ " + problems[0]) if problems else ""
    return Result(True,
                  f"📕 کتاب {title} الان {when}.\n\n"
                  f"🕒 شما نفر {fa(position)} در صف انتظار این کتاب هستید. "
                  f"به محض برگشتن کتاب، برایتان رزرو می‌شود و در همین ربات خبرتان می‌کنیم.{note}",
                  [Notice(admins=True, text=f"🕒 صف انتظار: {member['code']} ({member['name']}) برای {title}، نفر {fa(position)}")],
                  {"state": "waiting", "position": position})


def _reservation_notice(loan_id, copy, title, member, days, expires) -> Notice:
    return Notice(
        admins=True,
        text=(f"📌 رزرو تازه #{fa(loan_id)}\nکتاب: {title} ({copy})\nعضو: {member['code']} · {member['name']}"
              f" · {fa(member['phone'])}\nمدت درخواستی: {fa(days)} روز\nمهلت تحویل گرفتن: {to_jalali(expires)}"),
        buttons=[[("✅ تحویل داده شد", f"hand:{loan_id}"), ("✖️ لغو رزرو", f"cancel:{loan_id}")]],
    )


def handover(loan_id: int, by: str = "", on: dt.date | None = None) -> Result:
    """The member picked the reserved book up: the loan starts now."""
    on = on or _today()
    with connect() as conn:
        loan = conn.execute("SELECT l.*, b.title FROM loans l JOIN books b ON b.code = l.book_code WHERE l.id = ?",
                            (loan_id,)).fetchone()
        if not loan or loan["status"] != "reserved":
            return Result(False, "این رزرو پیدا نشد یا قبلاً تحویل داده شده است.")
        due = on + dt.timedelta(days=loan["requested_days"])
        conn.execute("UPDATE loans SET status = 'active', handed_at = ?, due_date = ?, handed_by = ? WHERE id = ?",
                     (on.isoformat(), due.isoformat(), by, loan_id))
        in_club = conn.execute("SELECT club FROM members WHERE code = ?", (loan["member_code"],)).fetchone()[0]
    text = (f"📗 کتاب «{loan['title']}» به شما تحویل داده شد. مطالعۀ خوبی داشته باشید!\n\n"
            f"📅 مهلت بازگشت: {to_jalali(due)}\n"
            f"برای تمدید، پیش از پایان مهلت از دکمۀ «🔁 تمدید امانت» استفاده کنید.")
    if in_club:
        text += f"\n\n📖 هر روز که از این کتاب خواندید، زمانش را ثبت کنید تا در {config.CLUB_NAME} حساب شود."
    return Result(True, f"امانت #{fa(loan_id)} شروع شد؛ مهلت بازگشت {to_jalali(due)}.",
                  [Notice(member_code=loan["member_code"], text=text)], {"due_date": due.isoformat()})


def direct_loan(book_code: str, member_code: str, days: int | None = None, by: str = "",
                on: dt.date | None = None, force: bool = False) -> Result:
    """Librarian lends a book face to face (no reservation step)."""
    on = on or _today()
    days = days or config.LOAN_DEFAULT_DAYS
    with connect() as conn:
        book = conn.execute("SELECT * FROM books WHERE code = ?", (book_code.upper(),)).fetchone()
        member = conn.execute("SELECT * FROM members WHERE code = ?", (member_code.upper(),)).fetchone()
        if not book or not member:
            return Result(False, "کد کتاب یا کد عضو درست نیست.")
        if _open_loan_of_copy(conn, book["code"]):
            return Result(False, f"نسخۀ {book['code']} الان در امانت یا رزرو است.")
        problems = borrow_problems(conn, member, on)
        if problems and not force:
            return Result(False, "\n".join(problems))
        cur = conn.execute(
            "INSERT INTO loans (book_code, member_code, status, requested_days, requested_at, reserve_expires)"
            " VALUES (?, ?, 'reserved', ?, ?, ?)",
            (book["code"], member["code"], days, on.isoformat(), on.isoformat()))
        loan_id = cur.lastrowid
    return handover(loan_id, by, on)


def _process_waitlist(conn, base_code: str, on: dt.date) -> list[Notice]:
    """Give freed copies to the next eligible members in the queue."""
    notices = []
    queue = conn.execute("SELECT * FROM waitlist WHERE base_code = ? AND status = 'waiting' ORDER BY id",
                         (base_code,)).fetchall()
    for w in queue:
        copy = _free_copy(conn, base_code)
        if not copy:
            break
        member = conn.execute("SELECT * FROM members WHERE code = ?", (w["member_code"],)).fetchone()
        if borrow_problems(conn, member, on):
            continue  # stays in the queue; their turn comes when they are eligible again
        expires = on + dt.timedelta(days=config.RESERVE_HOLD_DAYS)
        cur = conn.execute(
            "INSERT INTO loans (book_code, member_code, status, requested_days, requested_at, reserve_expires)"
            " VALUES (?, ?, 'reserved', ?, ?, ?)",
            (copy, member["code"], w["requested_days"], on.isoformat(), expires.isoformat()))
        conn.execute("UPDATE waitlist SET status = 'fulfilled' WHERE id = ?", (w["id"],))
        title = conn.execute("SELECT title FROM books WHERE code = ?", (copy,)).fetchone()["title"]
        notices.append(Notice(member_code=member["code"], text=(
            f"🎉 نوبت شما رسید! کتاب «{title}» برگشت و برای شما رزرو شد.\n"
            f"📍 لطفاً تا {to_jalali(expires)} آن را از {config.PICKUP_PLACE} تحویل بگیرید.")))
        notices.append(_reservation_notice(cur.lastrowid, copy, title, member, w["requested_days"], expires))
    return notices


def return_book(loan_id: int, condition: str = "سالم", by: str = "", on: dt.date | None = None) -> Result:
    on = on or _today()
    with connect() as conn:
        loan = conn.execute("SELECT l.*, b.title, b.base_code FROM loans l JOIN books b ON b.code = l.book_code"
                            " WHERE l.id = ?", (loan_id,)).fetchone()
        if not loan or loan["status"] != "active":
            return Result(False, "امانت فعالی با این شماره پیدا نشد.")
        lost = condition == "گم شده"
        due = _date(loan["due_date"])
        days_late = max(0, (on - due).days)
        fine = 0 if lost else fine_for(days_late)
        conn.execute(
            "UPDATE loans SET status = ?, returned_at = ?, return_condition = ?, received_by = ?, fine = ? WHERE id = ?",
            ("lost" if lost else "returned", on.isoformat(), condition, by, fine, loan_id))
        if lost or condition == "آسیب جدی":
            conn.execute("UPDATE books SET active = 0, note = TRIM(note || ' ' || ?) WHERE code = ?",
                         (f"[{to_jalali(on)}: {condition} در امانت #{loan_id}؛ منتظر نسخۀ جایگزین]", loan["book_code"]))
        notices = _process_waitlist(conn, loan["base_code"], on)
        in_club = conn.execute("SELECT club FROM members WHERE code = ?", (loan["member_code"],)).fetchone()[0]

    if lost or condition == "آسیب جدی":
        member_text = (f"کتاب «{loan['title']}» به عنوان «{condition}» ثبت شد. طبق قوانین، لطفاً نسخه‌ای "
                       f"جدید از همین کتاب تهیه کنید و به کتابخانه بدهید.")
    else:
        member_text = f"🙏 کتاب «{loan['title']}» را تحویل گرفتیم. ممنون از شما!"
        if fine:
            member_text += (f"\n\n⏰ کتاب {fa(days_late)} روز دیر برگشت؛ جریمۀ دیرکرد: {money(fine)}.\n"
                            f"لطفاً جریمه را به کارت {fa(_card())} واریز کنید و عکس فیش را بفرستید."
                            f" تا جریمه تسویه نشود، کتاب تازه امانت داده نمی‌شود.")
        member_text += "\n\nاگر دوست دارید، نظرتان دربارۀ کتاب را برایمان بنویسید. 💬"
    buttons = [] if lost or condition == "آسیب جدی" else [[("✍️ نوشتن نظر دربارۀ کتاب", "fb")]]
    if buttons and in_club:
        member_text += "\n\n📖 کتاب را تا آخر خواندید؟"
        buttons.insert(0, [("✅ بله، تمامش کردم", f"fin:{loan_id}:1"), ("هنوز نه", f"fin:{loan_id}:0")])
    notices.insert(0, Notice(member_code=loan["member_code"], text=member_text, buttons=buttons))
    msg = f"کتاب «{loan['title']}» برگشت."
    if fine:
        msg += f" تأخیر {fa(days_late)} روز؛ جریمه {money(fine)}."
    return Result(True, msg, notices, {"fine": fine, "late": days_late})


def delete_loan(loan_id: int, on: dt.date | None = None) -> Result:
    """Remove a loan entered by mistake or for testing (with its fine payment and reading logs)."""
    on = on or _today()
    with connect() as conn:
        loan = conn.execute("SELECT l.*, b.title, b.base_code FROM loans l JOIN books b ON b.code = l.book_code"
                            " WHERE l.id = ?", (loan_id,)).fetchone()
        if not loan:
            return Result(False, "این امانت پیدا نشد.")
        conn.execute("DELETE FROM payments WHERE loan_id = ?", (loan_id,))
        conn.execute("DELETE FROM reading_logs WHERE loan_id = ?", (loan_id,))
        conn.execute("DELETE FROM loans WHERE id = ?", (loan_id,))
        if loan["status"] == "lost":
            conn.execute("UPDATE books SET active = 1 WHERE code = ?", (loan["book_code"],))
        notices = _process_waitlist(conn, loan["base_code"], on) if loan["status"] in ("reserved", "active") else []
    return Result(True, f"امانت #{fa(loan_id)} («{loan['title']}»، {loan['member_code']}) پاک شد.", notices)


def mark_fine_paid(loan_id: int, method: str = "کارت‌به‌کارت", on: dt.date | None = None) -> Result:
    on = on or _today()
    with connect() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan or not loan["fine"] or loan["fine_paid"]:
            return Result(False, "جریمۀ پرداخت‌نشده‌ای برای این امانت نیست.")
        conn.execute("UPDATE loans SET fine_paid = 1 WHERE id = ?", (loan_id,))
        conn.execute(
            "INSERT INTO payments (date, category, direction, amount, member_code, book_code, loan_id, method, description)"
            " VALUES (?, 'جریمۀ دیرکرد', 'in', ?, ?, ?, ?, ?, ?)",
            (on.isoformat(), loan["fine"], loan["member_code"], loan["book_code"], loan_id, method,
             f"جریمۀ امانت #{loan_id}"))
    return Result(True, f"جریمۀ امانت #{fa(loan_id)} تسویه شد.", [Notice(
        member_code=loan["member_code"], text="✅ جریمۀ دیرکرد شما تسویه شد. حالا می‌توانید کتاب تازه امانت بگیرید.")])


def cancel_reservation(loan_id: int, reason: str = "", on: dt.date | None = None) -> Result:
    on = on or _today()
    with connect() as conn:
        loan = conn.execute("SELECT l.*, b.title, b.base_code FROM loans l JOIN books b ON b.code = l.book_code"
                            " WHERE l.id = ?", (loan_id,)).fetchone()
        if not loan or loan["status"] != "reserved":
            return Result(False, "رزرو فعالی با این شماره پیدا نشد.")
        conn.execute("UPDATE loans SET status = 'cancelled' WHERE id = ?", (loan_id,))
        notices = _process_waitlist(conn, loan["base_code"], on)
    notices.insert(0, Notice(member_code=loan["member_code"],
                             text=f"رزرو کتاب «{loan['title']}» لغو شد" + (f": {reason}" if reason else ".")))
    return Result(True, f"رزرو #{fa(loan_id)} لغو شد.", notices)


def leave_waitlist(member_code: str, base_code: str) -> Result:
    with connect() as conn:
        conn.execute("UPDATE waitlist SET status = 'cancelled' WHERE member_code = ? AND base_code = ?"
                     " AND status = 'waiting'", (member_code, base_code))
    return Result(True, "از صف انتظار خارج شدید.")


def extension_options(member_code: str, on: dt.date | None = None) -> Result:
    """Which new due dates the member may pick (rule 2 & 3)."""
    on = on or _today()
    with connect() as conn:
        loan = _member_open_loan(conn, member_code)
        if not loan or loan["status"] != "active":
            return Result(False, "کتاب امانتی در دست ندارید.")
        due, cap = _date(loan["due_date"]), max_date(loan)
        if on > due:
            return Result(False, "مهلت امانت تمام شده است؛ تمدید فقط پیش از پایان مهلت ممکن است. لطفاً کتاب را برگردانید.")
        waiting = conn.execute("SELECT COUNT(*) FROM waitlist WHERE base_code = ? AND status = 'waiting'",
                               (loan["base_code"],)).fetchone()[0]
        if waiting:
            return Result(False, f"کسی در صف انتظار «{loan['title']}» است، پس تمدید ممکن نیست. لطفاً تا {to_jalali(due)} کتاب را برگردانید.")
        if due >= cap:
            return Result(False, f"امانت شما به سقف {fa(config.LOAN_MAX_TOTAL_DAYS)} روز رسیده و بیشتر تمدید نمی‌شود.")
        choices = sorted({min(due + dt.timedelta(days=d), cap) for d in (7, 14)} | {cap})
    return Result(True, f"مهلت فعلی «{loan['title']}»: {to_jalali(due)}\nتا کدام تاریخ تمدید شود؟",
                  data={"loan_id": loan["id"], "choices": [c.isoformat() for c in choices if c > due]})


def extend_loan(member_code: str, new_due: dt.date, on: dt.date | None = None) -> Result:
    on = on or _today()
    check = extension_options(member_code, on)
    if not check.ok:
        return check
    loan = get_loan(check.data["loan_id"])
    cap = max_date(loan)
    if new_due > cap or new_due <= _date(loan["due_date"]):
        return Result(False, f"تاریخ تمدید باید بعد از مهلت فعلی و حداکثر تا {to_jalali(cap)} باشد.")
    with connect() as conn:
        conn.execute("UPDATE loans SET due_date = ?, reminded = '' WHERE id = ?", (new_due.isoformat(), loan["id"]))
    return Result(True, f"🔁 امانت «{loan['title']}» تا {to_jalali(new_due)} تمدید شد.",
                  [Notice(admins=True, text=f"🔁 تمدید: {member_code} · «{loan['title']}» تا {to_jalali(new_due)}")])


# ---------------------------------------------------------- daily checks

def daily_tasks(on: dt.date | None = None) -> list[Notice]:
    """Reminders, overdue warnings, revocations and expired reservations. Run once a day."""
    on = on or _today()
    notices: list[Notice] = []
    with connect() as conn:
        for r in conn.execute("SELECT l.*, b.title, b.base_code FROM loans l JOIN books b ON b.code = l.book_code"
                              " WHERE l.status = 'reserved' AND l.reserve_expires < ?", (on.isoformat(),)).fetchall():
            conn.execute("UPDATE loans SET status = 'cancelled' WHERE id = ?", (r["id"],))
            notices.append(Notice(member_code=r["member_code"],
                                  text=f"⌛ مهلت تحویل گرفتن «{r['title']}» تمام شد و رزرو لغو شد. اگر هنوز می‌خواهید، دوباره درخواست بدهید."))
            notices.append(Notice(admins=True, text=f"⌛ رزرو #{fa(r['id'])} ({r['book_code']}، {r['member_code']}) منقضی و لغو شد."))
            notices.extend(_process_waitlist(conn, r["base_code"], on))

        overdue_lines = []
        for loan in conn.execute("SELECT l.*, b.title, m.name, m.phone, m.status AS mstatus FROM loans l"
                                 " JOIN books b ON b.code = l.book_code JOIN members m ON m.code = l.member_code"
                                 " WHERE l.status = 'active'").fetchall():
            due = _date(loan["due_date"])
            left = (due - on).days
            sent = set(filter(None, (loan["reminded"] or "").split(",")))
            tag, text = None, None
            if left == config.REMIND_DAYS_BEFORE and "pre" not in sent:
                tag, text = "pre", (f"📅 یادآوری: مهلت بازگشت «{loan['title']}» {to_jalali(due)} است "
                                    f"({fa(left)} روز دیگر). اگر لازم دارید، پیش از پایان مهلت تمدید کنید.")
            elif left == 0 and "due" not in sent:
                tag, text = "due", f"📅 امروز آخرین روز مهلت «{loan['title']}» است. لطفاً کتاب را به {config.PICKUP_PLACE} برگردانید."
            elif left < 0:
                late = -left
                tag = f"late{late}"
                if tag not in sent:
                    if late >= config.REVOKE_AFTER_LATE_DAYS:
                        text = (f"⛔ «{loan['title']}» {fa(late)} روز دیر شده است. طبق قوانین، عضویت شما باطل شد. "
                                f"لطفاً کتاب را هرچه زودتر برگردانید.")
                    else:
                        text = (f"⏰ «{loan['title']}» {fa(late)} روز دیر شده است. جریمۀ تا امروز: {money(fine_for(late))}."
                                f" از روز {fa(config.REVOKE_AFTER_LATE_DAYS)}م تأخیر، عضویت باطل می‌شود.")
                overdue_lines.append(f"• {loan['book_code']} «{loan['title']}» ← {loan['member_code']} {loan['name']}"
                                     f" ({fa(loan['phone'])}): {fa(late)} روز")
                if late >= config.REVOKE_AFTER_LATE_DAYS and loan["mstatus"] != "revoked":
                    conn.execute("UPDATE members SET status = 'revoked' WHERE code = ?", (loan["member_code"],))
                    notices.append(Notice(admins=True, text=f"⛔ عضویت {loan['member_code']} ({loan['name']}) به دلیل {fa(late)} روز تأخیر باطل شد."))
            if tag and text:
                conn.execute("UPDATE loans SET reminded = ? WHERE id = ?", (",".join(sorted(sent | {tag})), loan["id"]))
                notices.append(Notice(member_code=loan["member_code"], text=text))
        if overdue_lines:
            notices.append(Notice(admins=True, text="📋 امانت‌های دیرکرد امروز:\n" + "\n".join(overdue_lines)))

        week = (on + dt.timedelta(days=7)).isoformat()
        for m in conn.execute("SELECT * FROM members WHERE valid_until = ? AND status = 'active'", (week,)).fetchall():
            notices.append(Notice(member_code=m["code"], text=(
                f"🔔 اعتبار عضویت شما در کتابخانه کاوی کنج {to_jalali(m['valid_until'])} تمام می‌شود. "
                f"برای تمدید با مسئول کتابخانه هماهنگ کنید.")))
    return notices


# ------------------------------------------------------------- summaries

def member_summary(member_code: str, on: dt.date | None = None) -> str:
    on = on or _today()
    with connect() as conn:
        m = conn.execute("SELECT * FROM members WHERE code = ?", (member_code,)).fetchone()
        if not m:
            return "عضوی با این کد پیدا نشد."
        lines = [f"👤 {m['name']} · کد عضویت {m['code']}", f"نقش: {m['role']}"]
        pay = {"paid": "پرداخت شد ✅", "pending": "منتظر فیش/تأیید ⏳", "rejected": "فیش رد شد ⚠️"}[m["payment_status"]]
        lines.append(f"حق عضویت: {money(m['fee'])} · {pay}")
        state = ("باطل‌شده ⛔" if m["status"] == "revoked"
                 else "معتبر ✅" if _date(m["valid_until"]) >= on else "منقضی ⚠️")
        lines.append(f"عضویت: {state} · اعتبار تا {to_jalali(m['valid_until'])}")
        loan = _member_open_loan(conn, member_code)
        if loan and loan["status"] == "active":
            late = late_days(loan, on)
            lines.append(f"\n📗 در دست: «{loan['title']}» ({loan['book_code']})")
            lines.append(f"مهلت بازگشت: {to_jalali(loan['due_date'])}" +
                         (f" · {fa(late)} روز تأخیر، جریمه {money(fine_for(late))}" if late else ""))
        elif loan:
            lines.append(f"\n📌 رزرو: «{loan['title']}» · تحویل گرفتن تا {to_jalali(loan['reserve_expires'])}")
        else:
            lines.append("\nکتابی در دست ندارید.")
        unpaid = _unpaid_fines(conn, member_code)
        if unpaid:
            lines.append(f"💰 جریمۀ پرداخت‌نشده: {money(unpaid)}")
        waits = conn.execute("SELECT w.*, b.title FROM waitlist w JOIN books b ON b.code = w.base_code"
                             " WHERE w.member_code = ? AND w.status = 'waiting'", (member_code,)).fetchall()
        for w in waits:
            lines.append(f"🕒 در صف انتظار: «{w['title']}»")
        total = conn.execute("SELECT COUNT(*) FROM loans WHERE member_code = ? AND status IN ('active','returned','lost')",
                             (member_code,)).fetchone()[0]
        lines.append(f"\nتعداد کل امانت‌ها: {fa(total)}")
    return "\n".join(lines)


def add_inbox(member_code: str, text: str, on: dt.datetime | None = None) -> None:
    stamp = (on or dt.datetime.now()).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("INSERT INTO inbox (member_code, text, created_at) VALUES (?, ?, ?)", (member_code, text, stamp))


def inbox(member_code: str, limit: int = 30) -> list[dict]:
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM inbox WHERE member_code = ? ORDER BY id DESC LIMIT ?", (member_code, limit))]
        conn.execute("UPDATE inbox SET seen = 1 WHERE member_code = ? AND seen = 0", (member_code,))
    return rows


def save_feedback(member_code: str, text: str) -> bool:
    """Store a reader's comment on their most recently returned book."""
    with connect() as conn:
        loan = conn.execute("SELECT id FROM loans WHERE member_code = ? AND status = 'returned' AND feedback = ''"
                            " ORDER BY returned_at DESC, id DESC LIMIT 1", (member_code,)).fetchone()
        if not loan:
            return False
        conn.execute("UPDATE loans SET feedback = ? WHERE id = ?", (text.strip(), loan["id"]))
    return True


def dashboard(on: dt.date | None = None) -> dict:
    on = on or _today()
    with connect() as conn:
        q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
        stats = {
            "titles": q("SELECT COUNT(*) FROM books WHERE code = base_code"),
            "copies": q("SELECT COUNT(*) FROM books WHERE active = 1"),
            "on_loan": q("SELECT COUNT(*) FROM loans WHERE status = 'active'"),
            "reserved": q("SELECT COUNT(*) FROM loans WHERE status = 'reserved'"),
            "overdue": q("SELECT COUNT(*) FROM loans WHERE status = 'active' AND due_date < ?", on.isoformat()),
            "members": q("SELECT COUNT(*) FROM members"),
            "valid_members": q("SELECT COUNT(*) FROM members WHERE status = 'active' AND payment_status = 'paid'"
                               " AND valid_until >= ?", on.isoformat()),
            "pending_payments": q("SELECT COUNT(*) FROM members WHERE payment_status != 'paid' AND status = 'active'"),
            "waiting": q("SELECT COUNT(*) FROM waitlist WHERE status = 'waiting'"),
            "unpaid_fines": q("SELECT COALESCE(SUM(fine),0) FROM loans WHERE fine > 0 AND fine_paid = 0"),
            "balance": q("SELECT COALESCE(SUM(CASE direction WHEN 'in' THEN amount ELSE -amount END),0) FROM payments"),
        }
    stats["available"] = stats["copies"] - stats["on_loan"] - stats["reserved"]
    return stats
