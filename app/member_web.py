"""Member area of the website: sign-in through Telegram, «حساب من», the readers' club pages."""
from __future__ import annotations

import datetime as dt

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, club, config, jalali, notify, services
from .db import connect
from .telegram_auth import validate_init_data

MAX_RECEIPT_BYTES = 8 * 1024 * 1024


def current_member(request: Request):
    code = request.session.get("member")
    return services.get_member(code) if code else None


def sign_in(request: Request, tg_user_id: int):
    m = services.get_member_by_tg(tg_user_id)
    if m:
        request.session["member"] = m["code"]
    return m


def _flash(request: Request, msg: str, ok: bool = True, to: str = "/me"):
    request.session["flash"] = {"msg": msg, "ok": ok}
    return RedirectResponse(to, status_code=303)


async def _done(request: Request, res: services.Result, to: str = "/me"):
    await notify.deliver(res.notices)
    return _flash(request, res.message, res.ok, to)


def _require(request: Request):
    m = current_member(request)
    if not m:
        raise HTTPException(303, headers={"Location": "/login"})
    return m


def overview(code: str, on: dt.date) -> dict:
    """Everything «حساب من» shows, in one go."""
    with connect() as conn:
        open_loan = conn.execute(
            "SELECT l.*, b.title, b.base_code FROM loans l JOIN books b ON b.code = l.book_code"
            " WHERE l.member_code = ? AND l.status IN ('reserved','active') ORDER BY l.id DESC LIMIT 1", (code,)).fetchone()
        waits = conn.execute("SELECT w.*, b.title FROM waitlist w JOIN books b ON b.code = w.base_code"
                             " WHERE w.member_code = ? AND w.status = 'waiting' ORDER BY w.id", (code,)).fetchall()
        recent = conn.execute(
            "SELECT l.*, b.title, b.base_code FROM loans l JOIN books b ON b.code = l.book_code"
            " WHERE l.member_code = ? AND l.status IN ('returned','lost') ORDER BY l.returned_at DESC, l.id DESC LIMIT 6",
            (code,)).fetchall()
        unpaid = conn.execute("SELECT COALESCE(SUM(fine), 0) FROM loans WHERE member_code = ? AND fine > 0 AND fine_paid = 0"
                              " AND status IN ('returned','lost')", (code,)).fetchone()[0]
    loan = dict(open_loan) if open_loan else None
    if loan and loan["status"] == "active":
        loan["late"] = services.late_days(loan, on)
        loan["current_fine"] = services.fine_for(loan["late"])
        ext = services.extension_options(code, on)
        loan["ext_choices"] = ext.data.get("choices", []) if ext.ok else []
        loan["ext_note"] = "" if ext.ok else ext.message
    return {"loan": loan, "waits": [dict(w) for w in waits], "recent": [dict(r) for r in recent], "unpaid": unpaid}


def member_routes(app: FastAPI, templates) -> None:
    def render(request: Request, name: str, **ctx):
        ctx["flash"] = request.session.pop("flash", None)
        ctx.setdefault("member", current_member(request))
        return templates.TemplateResponse(request, name, ctx)

    # ------------------------------------------------------------- sign-in
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if current_member(request):
            return RedirectResponse("/me", status_code=303)
        nonce = auth.new_nonce()
        return render(request, "member/login.html", nonce=nonce, member=None)

    @app.get("/api/login/poll")
    async def login_poll(request: Request, nonce: str):
        tg_id = auth.consume(nonce)
        if tg_id is None:
            return {"state": "waiting" if auth.is_pending(nonce) else "expired"}
        m = sign_in(request, tg_id)
        return {"state": "ok" if m else "not_member"}

    @app.post("/api/login/webapp")
    async def login_webapp(request: Request):
        body = await request.json()
        user = validate_init_data(body.get("initData", ""), config.BOT_TOKEN)
        if not user:
            raise HTTPException(401)
        m = sign_in(request, user["id"])
        return {"ok": bool(m)}

    @app.get("/login/t/{nonce}")
    async def login_link(request: Request, nonce: str):
        tg_id = auth.consume(nonce)
        if tg_id is None or not sign_in(request, tg_id):
            return _flash(request, "این لینک ورود منقضی شده است. از ربات دوباره «🌐 ورود به سایت» را بزنید.", False, "/login")
        return RedirectResponse("/me", status_code=303)

    @app.get("/logout")
    async def logout(request: Request):
        request.session.pop("member", None)
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------- my account
    @app.get("/me", response_class=HTMLResponse)
    async def me_page(request: Request):
        m = _require(request)
        on = jalali.today()
        return render(request, "member/me.html", m=dict(m), o=overview(m["code"], on),
                      club_stats=club.stats(m["code"], on) if m["club"] else None,
                      badges=club.BADGES, inbox=services.inbox(m["code"]),
                      expired=jalali.to_jalali(m["valid_until"]), valid=m["valid_until"] >= on.isoformat(),
                      log_choices=config.CLUB_LOG_CHOICES, hours=club.hours)

    @app.post("/me/{action}")
    async def me_action(request: Request, action: str):
        m = _require(request)
        code = m["code"]
        if action == "receipt":
            return await _receipt(request, m)
        f = await request.form()
        if action == "extend":
            try:
                res = services.extend_loan(code, dt.date.fromisoformat(str(f.get("due", ""))))
            except ValueError:
                res = services.Result(False, "تاریخ تمدید درست نیست.")
        elif action == "leave-queue":
            res = services.leave_waitlist(code, str(f.get("base_code", "")))
        elif action == "feedback":
            res = _feedback(code, int(f.get("loan_id") or 0), str(f.get("text", "")))
        elif action == "finished":
            res = club.mark_finished(code, int(f.get("loan_id") or 0), True, int(f.get("rating") or 0))
        elif action == "join":
            res = club.join(code)
        elif action == "leave-club":
            res = club.leave(code)
        elif action == "public":
            res = club.set_public(code, f.get("value") == "1")
        elif action == "log":
            value = jalali.en(str(f.get("minutes", ""))).strip()
            if value == "leave":
                res = club.log_leave(code)
            elif value.isdigit():
                res = club.log_reading(code, int(value))
            else:
                res = services.Result(False, "زمان مطالعه را به دقیقه بنویسید.")
        elif action == "suggest":
            res = club.suggest(code, str(f.get("title", "")))
        else:
            raise HTTPException(404)
        return await _done(request, res)

    async def _receipt(request: Request, m):
        f = await request.form()
        upload: UploadFile | None = f.get("file")  # type: ignore[assignment]
        if not upload or not getattr(upload, "filename", ""):
            return _flash(request, "عکس فیش را انتخاب کنید.", False)
        data = await upload.read(MAX_RECEIPT_BYTES + 1)
        if len(data) > MAX_RECEIPT_BYTES:
            return _flash(request, "حجم عکس زیاد است (حداکثر ۸ مگابایت).", False)
        caption, buttons = services.receipt_review(m)
        file_id = await notify.send_photo_to_admins(data, upload.filename, caption + "\n(از طریق سایت)", buttons)
        if not file_id:
            return _flash(request, "فرستادن فیش ممکن نشد. لطفاً عکس فیش را در ربات بفرستید.", False)
        if m["payment_status"] != "paid":
            services.attach_receipt(m["code"], file_id)
        return _flash(request, "🧾 فیش دریافت شد و برای مسئول کتابخانه فرستاده شد. بعد از بررسی خبرتان می‌کنیم.")

    def _feedback(code: str, loan_id: int, text: str) -> services.Result:
        text = text.strip()
        if len(text) < 3:
            return services.Result(False, "نظرتان را بنویسید.")
        with connect() as conn:
            row = conn.execute("SELECT id FROM loans WHERE id = ? AND member_code = ?", (loan_id, code)).fetchone()
            if not row:
                return services.Result(False, "این امانت پیدا نشد.")
            conn.execute("UPDATE loans SET feedback = ? WHERE id = ?", (text[:2000], loan_id))
        m = services.get_member(code)
        return services.Result(True, "💬 ممنون! نظرتان ثبت شد.",
                               [services.Notice(admins=True, text=f"💬 نظر {code} ({m['name']}):\n{text[:2000]}")])

    # ------------------------------------------------------------------ club
    @app.get("/club", response_class=HTMLResponse)
    async def club_page(request: Request):
        with connect() as conn:
            feed = conn.execute(
                "SELECT l.returned_at, l.rating, l.feedback, b.title, b.base_code, m.name, m.code FROM loans l"
                " JOIN books b ON b.code = l.book_code JOIN members m ON m.code = l.member_code"
                " WHERE l.finished = 1 AND m.club = 1 AND m.public_profile = 1 ORDER BY l.returned_at DESC LIMIT 12").fetchall()
            members = conn.execute("SELECT COUNT(*) FROM members WHERE club = 1").fetchone()[0]
        return render(request, "member/club.html", board=club.leaderboard(), members_count=members,
                      feed=[{**dict(r), "who": club.display_name(r)} for r in feed], hours=club.hours)

    @app.get("/club/r/{code}", response_class=HTMLResponse)
    async def reader_page(request: Request, code: str):
        me = current_member(request)
        p = club.reader_profile(code.upper())
        if not p or (not p["member"]["public_profile"] and not (me and me["code"] == code.upper())):
            raise HTTPException(404)
        return render(request, "member/reader.html", p=p, badges=club.BADGES, hours=club.hours)
