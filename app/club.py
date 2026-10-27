"""Readers' club («کلوپ کتاب‌خوان‌ها»): reading log, streaks, badges and the leaderboard.

Joining is optional; members who do not join just borrow and return books. Like services.py,
functions return ``Result``/``Notice`` and never talk to Telegram themselves.
"""
from __future__ import annotations

import datetime as dt

from . import config
from .db import connect
from .jalali import fa, to_jalali, today as _today
from .services import Notice, Result, _date

BADGES = {  # code: (label, check(stats) -> bool)
    "first_log": ("🌱 اولین گزارش مطالعه", lambda s: s["days_read"] >= 1),
    "streak_7": ("🔥 ۷ روز پیاپی", lambda s: s["streak"] >= 7),
    "streak_30": ("🏅 ۳۰ روز پیاپی", lambda s: s["streak"] >= 30),
    "hours_10": ("⏳ ۱۰ ساعت مطالعه", lambda s: s["total_minutes"] >= 600),
    "hours_50": ("🌳 ۵۰ ساعت مطالعه", lambda s: s["total_minutes"] >= 3000),
    "first_book": ("📗 اولین کتاب تمام‌شده", lambda s: s["books_finished"] >= 1),
    "books_5": ("📚 ۵ کتاب تمام‌شده", lambda s: s["books_finished"] >= 5),
    "books_10": ("🦉 ۱۰ کتاب تمام‌شده", lambda s: s["books_finished"] >= 10),
}


def display_name(member) -> str:
    """First name only, for public lists."""
    return (member["name"] or "").split()[0] if member["name"] else member["code"]


def _member(conn, code: str):
    return conn.execute("SELECT * FROM members WHERE code = ?", (code,)).fetchone()


def _active_loan(conn, code: str):
    return conn.execute(
        "SELECT l.*, b.title, b.base_code FROM loans l JOIN books b ON b.code = l.book_code"
        " WHERE l.member_code = ? AND l.status = 'active' ORDER BY l.id DESC LIMIT 1", (code,)).fetchone()


# ------------------------------------------------------------- membership

def join(code: str, on: dt.date | None = None) -> Result:
    on = on or _today()
    with connect() as conn:
        m = _member(conn, code)
        if not m:
            return Result(False, "عضوی با این کد پیدا نشد.")
        if m["club"]:
            return Result(False, f"شما عضو {config.CLUB_NAME} هستید.")
        conn.execute("UPDATE members SET club = 1, club_joined_at = ? WHERE code = ?", (on.isoformat(), code))
    return Result(True, (
        f"🎉 به {config.CLUB_NAME} خوش آمدید!\n\n"
        "هر روز که از کتاب امانتی‌تان خواندید، چند دقیقه‌اش را ثبت کنید؛ با گزارش روزانه، "
        "روزهای پیاپی مطالعه‌تان (زنجیره) بلندتر می‌شود و نشان می‌گیرید.\n"
        f"هر شب ساعت {fa(config.EVENING_REMINDER_HOUR)} ربات یادتان می‌اندازد. اگر روزی نتوانستید بخوانید، "
        f"«مرخصی» بزنید (هفته‌ای {fa(config.CLUB_LEAVES_PER_WEEK)} روز) تا زنجیره نشکند."))


def leave(code: str) -> Result:
    with connect() as conn:
        conn.execute("UPDATE members SET club = 0 WHERE code = ?", (code,))
    return Result(True, f"از {config.CLUB_NAME} خارج شدید. گزارش‌های قبلی‌تان پاک نمی‌شود و هر وقت بخواهید می‌توانید برگردید.")


def set_public(code: str, public: bool) -> Result:
    with connect() as conn:
        conn.execute("UPDATE members SET public_profile = ? WHERE code = ?", (1 if public else 0, code))
    return Result(True, "نام شما در جدول برترین‌ها و صفحهٔ کلوپ نمایش داده می‌شود." if public
                  else "نام و صفحهٔ شما از جدول برترین‌ها و صفحهٔ کلوپ پنهان شد.")


# -------------------------------------------------------------- reading log

def _log_problems(conn, m, on: dt.date) -> str | None:
    if not m:
        return "عضوی با این کد پیدا نشد."
    if not m["club"]:
        return f"ثبت مطالعه برای اعضای {config.CLUB_NAME} است."
    if config.CLUB_ONLY_LIBRARY_BOOKS and not _active_loan(conn, m["code"]):
        return "فعلاً فقط مطالعهٔ کتاب‌هایی که از کتابخانه امانت گرفته‌اید ثبت می‌شود و الان کتابی در دست ندارید."
    return None


def log_reading(code: str, minutes: int, on: dt.date | None = None) -> Result:
    on = on or _today()
    minutes = int(minutes)
    if minutes <= 0:
        return Result(False, "زمان مطالعه را به دقیقه بنویسید.")
    with connect() as conn:
        m = _member(conn, code)
        problem = _log_problems(conn, m, on)
        if problem:
            return Result(False, problem)
        loan = _active_loan(conn, code)
        row = conn.execute("SELECT * FROM reading_logs WHERE member_code = ? AND day = ?", (code, on.isoformat())).fetchone()
        total = min((row["minutes"] if row else 0) + minutes, config.CLUB_MAX_MINUTES_PER_DAY)
        if row:
            conn.execute("UPDATE reading_logs SET minutes = ?, is_leave = 0, loan_id = ? WHERE id = ?",
                         (total, loan["id"] if loan else None, row["id"]))
        else:
            conn.execute("INSERT INTO reading_logs (member_code, day, loan_id, minutes, is_leave) VALUES (?, ?, ?, ?, 0)",
                         (code, on.isoformat(), loan["id"] if loan else None, total))
    s = stats(code, on)
    msg = (f"✅ ثبت شد: امروز {fa(total)} دقیقه از «{loan['title']}».\n"
           f"🔥 زنجیرهٔ مطالعهٔ شما: {fa(s['streak'])} روز")
    return Result(True, msg, _new_badges(code, s, on), {"streak": s["streak"], "today": total})


def log_leave(code: str, on: dt.date | None = None) -> Result:
    on = on or _today()
    with connect() as conn:
        m = _member(conn, code)
        if not m or not m["club"]:
            return Result(False, f"مرخصی برای اعضای {config.CLUB_NAME} است.")
        row = conn.execute("SELECT * FROM reading_logs WHERE member_code = ? AND day = ?", (code, on.isoformat())).fetchone()
        if row and not row["is_leave"]:
            return Result(False, "امروز مطالعه ثبت کرده‌اید؛ مرخصی لازم نیست. 🙂")
        if row:
            return Result(False, "امروز قبلاً مرخصی ثبت شده است.")
        week_start = (on - dt.timedelta(days=6)).isoformat()
        used = conn.execute("SELECT COUNT(*) FROM reading_logs WHERE member_code = ? AND is_leave = 1 AND day >= ?",
                            (code, week_start)).fetchone()[0]
        if used >= config.CLUB_LEAVES_PER_WEEK:
            return Result(False, f"در هر ۷ روز فقط {fa(config.CLUB_LEAVES_PER_WEEK)} روز مرخصی می‌شود گرفت.")
        conn.execute("INSERT INTO reading_logs (member_code, day, minutes, is_leave) VALUES (?, ?, 0, 1)",
                     (code, on.isoformat()))
    return Result(True, f"🌙 مرخصی امروز ثبت شد؛ زنجیرهٔ {fa(stats(code, on)['streak'])} روزهٔ شما حفظ می‌شود.")


def _streak(days: set[dt.date], on: dt.date) -> int:
    d = on if on in days else on - dt.timedelta(days=1)  # today may not be logged yet
    n = 0
    while d in days:
        n += 1
        d -= dt.timedelta(days=1)
    return n


def stats(code: str, on: dt.date | None = None) -> dict:
    on = on or _today()
    with connect() as conn:
        logs = conn.execute("SELECT day, minutes, is_leave FROM reading_logs WHERE member_code = ?", (code,)).fetchall()
        finished = conn.execute("SELECT COUNT(*) FROM loans WHERE member_code = ? AND finished = 1", (code,)).fetchone()[0]
        badges = [r["badge"] for r in conn.execute("SELECT badge FROM badges WHERE member_code = ? ORDER BY earned_at",
                                                    (code,))]
        loan = _active_loan(conn, code)
    days = {_date(r["day"]) for r in logs}
    month_start = on.replace(day=1)
    today_row = next((r for r in logs if r["day"] == on.isoformat()), None)
    return {
        "streak": _streak(days, on),
        "days_read": sum(1 for r in logs if not r["is_leave"]),
        "total_minutes": sum(r["minutes"] for r in logs),
        "week_minutes": sum(r["minutes"] for r in logs if _date(r["day"]) > on - dt.timedelta(days=7)),
        "month_minutes": sum(r["minutes"] for r in logs if _date(r["day"]) >= month_start),
        "books_finished": finished,
        "badges": badges,
        "today": today_row["minutes"] if today_row else 0,
        "today_leave": bool(today_row and today_row["is_leave"]),
        "reading": loan["title"] if loan else "",
    }


def _new_badges(code: str, s: dict, on: dt.date) -> list[Notice]:
    notices = []
    with connect() as conn:
        for key, (label, check) in BADGES.items():
            if key not in s["badges"] and check(s):
                conn.execute("INSERT INTO badges (member_code, badge, earned_at) VALUES (?, ?, ?)",
                             (code, key, on.isoformat()))
                s["badges"].append(key)
                notices.append(Notice(member_code=code, text=f"🏆 نشان تازه گرفتید: {label}"))
    return notices


def hours(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if not h:
        return f"{fa(m)} دقیقه"
    return f"{fa(h)} ساعت" + (f" و {fa(m)} دقیقه" if m else "")


def summary(code: str, on: dt.date | None = None) -> str:
    s = stats(code, on)
    lines = [f"📖 {config.CLUB_NAME}",
             f"🔥 زنجیره: {fa(s['streak'])} روز پیاپی",
             f"⏱ امروز: {'مرخصی' if s['today_leave'] else hours(s['today'])} · این ماه: {hours(s['month_minutes'])}",
             f"📚 کتاب‌های تمام‌شده: {fa(s['books_finished'])} · کل مطالعه: {hours(s['total_minutes'])}"]
    if s["badges"]:
        lines.append("🏆 " + "، ".join(BADGES[b][0] for b in s["badges"] if b in BADGES))
    return "\n".join(lines)


# ---------------------------------------------------------- finishing books

def mark_finished(code: str, loan_id: int, finished: bool, rating: int = 0, on: dt.date | None = None) -> Result:
    """After a return the reader says whether they finished the book (and may rate it 1–5)."""
    on = on or _today()
    with connect() as conn:
        loan = conn.execute("SELECT l.*, b.title FROM loans l JOIN books b ON b.code = l.book_code"
                            " WHERE l.id = ? AND l.member_code = ?", (loan_id, code)).fetchone()
        if not loan:
            return Result(False, "این امانت پیدا نشد.")
        conn.execute("UPDATE loans SET finished = ?, rating = ? WHERE id = ?",
                     (1 if finished else 0, max(0, min(5, int(rating or 0))), loan_id))
    if not finished:
        return Result(True, "ثبت شد. اشکالی ندارد؛ هر کتابی وقت خودش را دارد. 🌿")
    s = stats(code, on)
    return Result(True, f"👏 «{loan['title']}» به فهرست کتاب‌های تمام‌شدهٔ شما اضافه شد. "
                        f"تا حالا {fa(s['books_finished'])} کتاب.", _new_badges(code, s, on))


# ------------------------------------------------------------- public pages

def leaderboard(on: dt.date | None = None, limit: int = 20) -> list[dict]:
    on = on or _today()
    with connect() as conn:
        members = conn.execute("SELECT * FROM members WHERE club = 1 AND public_profile = 1").fetchall()
    rows = []
    for m in members:
        s = stats(m["code"], on)
        if s["streak"] or s["month_minutes"]:
            rows.append({"code": m["code"], "name": display_name(m), **s})
    rows.sort(key=lambda r: (r["streak"], r["month_minutes"]), reverse=True)
    return rows[:limit]


def reader_profile(code: str, on: dt.date | None = None) -> dict | None:
    with connect() as conn:
        m = _member(conn, code)
        if not m or not m["club"]:
            return None
        books = conn.execute(
            "SELECT l.*, b.title, b.author, b.topic1 FROM loans l JOIN books b ON b.code = l.book_code"
            " WHERE l.member_code = ? AND l.finished = 1 ORDER BY l.returned_at DESC", (code,)).fetchall()
    topics: dict[str, int] = {}
    for b in books:
        if b["topic1"]:
            topics[b["topic1"]] = topics.get(b["topic1"], 0) + 1
    return {"member": dict(m), "name": display_name(m), "stats": stats(code, on),
            "books": [dict(b) for b in books], "topics": sorted(topics.items(), key=lambda t: -t[1])}


def all_reviews() -> dict[str, list[dict]]:
    """Readers' comments for every title (for the catalogue page), newest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT b.base_code, l.feedback, l.rating, l.returned_at, m.name, m.code, m.public_profile FROM loans l"
            " JOIN books b ON b.code = l.book_code JOIN members m ON m.code = l.member_code"
            " WHERE l.feedback != '' ORDER BY l.returned_at DESC").fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["base_code"], []).append({
            "text": r["feedback"], "rating": r["rating"], "date": to_jalali(r["returned_at"]),
            "name": display_name(r) if r["public_profile"] else "یک خواننده"})
    return out


# -------------------------------------------------------------- reminders

def evening_reminders(on: dt.date | None = None) -> list[Notice]:
    """Ask club members with a borrowed book who have not logged today."""
    on = on or _today()
    notices = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.code, b.title FROM members m JOIN loans l ON l.member_code = m.code AND l.status = 'active'"
            " JOIN books b ON b.code = l.book_code WHERE m.club = 1 AND NOT EXISTS ("
            " SELECT 1 FROM reading_logs r WHERE r.member_code = m.code AND r.day = ?)", (on.isoformat(),)).fetchall()
    for r in rows:
        s = stats(r["code"], on)
        streak = f" زنجیرهٔ شما {fa(s['streak'])} روز است؛ نگذارید بشکند! 🔥" if s["streak"] else ""
        notices.append(Notice(member_code=r["code"], inbox=False, buttons=log_buttons(), text=(
            f"🌙 امروز چقدر از «{r['title']}» خواندید؟{streak}")))
    return notices


def log_buttons():
    return [[(f"{fa(m)} دقیقه", f"log:{m}") for m in config.CLUB_LOG_CHOICES], [("🌙 مرخصی امروز", "log:leave")]]


# ------------------------------------------------------------- suggestions

def suggest(code: str, title: str, on: dt.date | None = None) -> Result:
    on = on or _today()
    title = (title or "").strip()
    if len(title) < 2:
        return Result(False, "نام کتاب یا موضوع را بنویسید.")
    with connect() as conn:
        conn.execute("INSERT INTO suggestions (member_code, title, created_at) VALUES (?, ?, ?)",
                     (code, title[:300], on.isoformat()))
        m = _member(conn, code)
    return Result(True, "🙏 پیشنهادتان ثبت شد و به دست مسئول کتابخانه می‌رسد.",
                  [Notice(admins=True, text=f"💡 پیشنهاد کتاب از {code} ({m['name']}): {title[:300]}")])
