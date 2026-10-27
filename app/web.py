"""Website: public catalogue, Telegram mini-app pages and the librarian panel."""
from __future__ import annotations

import hmac
import io
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import config, exporter, jalali, notify, services
from .db import connect, upsert_books
from .telegram_auth import validate_init_data

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals.update(
    cfg=config, fa=jalali.fa, money=jalali.money, jdate=jalali.to_jalali, jlong=jalali.to_jalali_long,
    quote=quote, today_long=lambda: jalali.to_jalali_long(jalali.today()), cover_url=lambda code: f"/static/covers/{code}.jpg" if (HERE / "static" / "covers" / f"{code}.jpg").exists() else "",
)


def create_app(lifespan=None) -> FastAPI:
    app = FastAPI(title="Kavi Konj", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=14 * 86400, same_site="lax")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    _public_routes(app)
    _api_routes(app)
    _admin_routes(app)
    return app


def bot_link(payload: str = "") -> str:
    if config.BOT_USERNAME:
        return f"https://t.me/{config.BOT_USERNAME}" + (f"?start={payload}" if payload else "")
    return f"tg://resolve?phone=98{config.LIBRARY_PHONE.lstrip('0')}"


templates.env.globals["bot_link"] = bot_link


# ------------------------------------------------------------------ public

def grouped_catalog() -> list[tuple[str, list[dict]]]:
    books = services.catalog()
    groups: dict[str, list[dict]] = {t: [] for t in config.TOPICS}
    for b in books:
        placed = False
        for t in (b["topic1"], b["topic2"]):
            if t in groups:
                groups[t].append(b)
                placed = True
        if not placed:
            groups["متفرقه"].append(b)
    return [(t, items) for t, items in groups.items() if items]


def _public_routes(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def catalog_page(request: Request):
        groups = grouped_catalog()
        return templates.TemplateResponse(request, "catalog.html", {
            "groups": groups, "today": jalali.to_jalali(jalali.today()),
            "day_choices": config.LOAN_DAY_CHOICES, "default_days": min(config.LOAN_DEFAULT_DAYS, config.LOAN_MAX_INITIAL_DAYS),
        })

    @app.get("/app/register", response_class=HTMLResponse)
    async def register_page(request: Request):
        valid = jalali.membership_valid_until(jalali.today())
        return templates.TemplateResponse(request, "register.html", {
            "valid_year": jalali.fa(jalali.jalali_year(valid)), "today": jalali.to_jalali(jalali.today()),
        })

    @app.get("/app/catalog")
    async def app_catalog():
        return RedirectResponse("/", status_code=307)

    @app.post("/tg/{secret}")
    async def telegram_webhook(secret: str, request: Request):
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not config.WEBHOOK_SECRET or not (hmac.compare_digest(secret, config.WEBHOOK_SECRET)
                                             and hmac.compare_digest(token, config.WEBHOOK_SECRET)):
            raise HTTPException(403)
        from .bot import process_webhook_update
        try:
            await process_webhook_update(await request.json())
        except Exception:  # answer 200 anyway so Telegram does not resend the same update forever
            import logging
            logging.getLogger(__name__).exception("webhook update failed")
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}


# --------------------------------------------------------------------- api

async def _tg_user(request: Request) -> tuple[dict, dict]:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "bad json")
    user = validate_init_data(body.get("initData", ""), config.BOT_TOKEN)
    if not user:
        raise HTTPException(401, "این صفحه را از داخل ربات تلگرام کتابخانه باز کنید.")
    return user, body


def _api_routes(app: FastAPI) -> None:
    @app.get("/api/status")
    async def status():
        data = {b["code"]: {"state": b["status"]["state"], "label": b["status_label"],
                            "waiting": b["status"]["waiting"]} for b in services.catalog()}
        return JSONResponse(data, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

    @app.post("/api/me")
    async def me(request: Request):
        user, _ = await _tg_user(request)
        m = services.get_member_by_tg(user["id"])
        if not m:
            return {"member": None}
        return {"member": {"code": m["code"], "name": m["name"],
                           "summary": services.member_summary(m["code"])}}

    @app.post("/api/register")
    async def register(request: Request):
        user, body = await _tg_user(request)
        form = body.get("form") or {}
        res = services.register_member(form, user["id"], user.get("username", ""))
        if res.ok:
            await notify.send(user["id"], res.message)
            await notify.deliver(res.notices)
        return {"ok": res.ok, "message": res.message, **res.data}

    @app.post("/api/request")
    async def request_book(request: Request):
        user, body = await _tg_user(request)
        m = services.get_member_by_tg(user["id"])
        res = services.request_book(m["code"] if m else None, str(body.get("code", "")), int(body.get("days") or 0))
        await notify.send(user["id"], res.message)
        await notify.deliver(res.notices)
        return {"ok": res.ok, "message": res.message, **res.data}


# ------------------------------------------------------------------- admin

def _is_admin(request: Request) -> bool:
    return bool(request.session.get("admin"))


def _guard(request: Request):
    if not _is_admin(request):
        raise HTTPException(303, headers={"Location": "/admin/login"})


def _back(request: Request, msg: str = "", ok: bool = True, to: str | None = None):
    request.session["flash"] = {"msg": msg, "ok": ok}
    return RedirectResponse(to or request.headers.get("referer") or "/admin", status_code=303)


def _render(request: Request, name: str, **ctx):
    ctx["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, name, ctx)


def _rows(sql: str, *args) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


async def _apply(request: Request, res: services.Result, to: str | None = None):
    await notify.deliver(res.notices)
    return _back(request, res.message, res.ok, to)


def _admin_routes(app: FastAPI) -> None:
    @app.get("/admin/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return _render(request, "admin/login.html")

    @app.post("/admin/login")
    async def login(request: Request, password: str = Form(...)):
        if config.ADMIN_PASSWORD and hmac.compare_digest(password, config.ADMIN_PASSWORD):
            request.session["admin"] = True
            return RedirectResponse("/admin", status_code=303)
        return _back(request, "رمز اشتباه است.", False, "/admin/login")

    @app.get("/admin/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/admin", response_class=HTMLResponse)
    async def dashboard(request: Request):
        _guard(request)
        today = jalali.today().isoformat()
        return _render(
            request, "admin/dashboard.html", stats=services.dashboard(),
            pending=_rows("SELECT * FROM members WHERE payment_status != 'paid' AND status = 'active' ORDER BY seq"),
            reserved=_rows(
                "SELECT l.*, b.title, m.name, m.phone FROM loans l JOIN books b ON b.code = l.book_code"
                " JOIN members m ON m.code = l.member_code WHERE l.status = 'reserved' ORDER BY l.id"),
            overdue=_rows(
                "SELECT l.*, b.title, m.name, m.phone FROM loans l JOIN books b ON b.code = l.book_code"
                " JOIN members m ON m.code = l.member_code WHERE l.status = 'active' AND l.due_date < ?"
                " ORDER BY l.due_date", today),
            fines=_rows(
                "SELECT l.*, b.title, m.name FROM loans l JOIN books b ON b.code = l.book_code"
                " JOIN members m ON m.code = l.member_code WHERE l.fine > 0 AND l.fine_paid = 0"),
            waitlist=_rows(
                "SELECT w.*, b.title, m.name FROM waitlist w JOIN books b ON b.code = w.base_code"
                " JOIN members m ON m.code = w.member_code WHERE w.status = 'waiting' ORDER BY w.id"),
        )

    # --- members
    @app.get("/admin/members", response_class=HTMLResponse)
    async def members(request: Request, q: str = ""):
        _guard(request)
        like = f"%{q.strip()}%"
        rows = _rows("SELECT m.*, (SELECT COUNT(*) FROM loans l WHERE l.member_code = m.code) AS loans_count"
                     " FROM members m WHERE ? = '%%' OR m.code LIKE ? OR m.name LIKE ? OR m.phone LIKE ?"
                     " ORDER BY m.seq DESC", like, like, like, jalali.en(like))
        return _render(request, "admin/members.html", rows=rows, q=q, today=jalali.today().isoformat())

    @app.get("/admin/members/{code}", response_class=HTMLResponse)
    async def member_detail(request: Request, code: str):
        _guard(request)
        m = services.get_member(code)
        if not m:
            raise HTTPException(404)
        loans = _rows("SELECT l.*, b.title FROM loans l JOIN books b ON b.code = l.book_code"
                      " WHERE l.member_code = ? ORDER BY l.id DESC", code)
        return _render(request, "admin/member.html", m=dict(m), loans=loans,
                       summary=services.member_summary(code))

    @app.post("/admin/members/new")
    async def member_new(request: Request):
        _guard(request)
        form = dict(await request.form())
        form["agree"] = True
        res = services.register_member(form)
        if res.ok and form.get("paid"):
            res2 = services.approve_payment(res.data["code"], form.get("method") or "کارت‌به‌کارت")
            res.message += " " + res2.message
        return _back(request, res.message.split("\n")[0] + (f" کد: {res.data.get('code')}" if res.ok else ""), res.ok)

    @app.post("/admin/members/{code}/{action}")
    async def member_action(request: Request, code: str, action: str):
        _guard(request)
        form = await request.form()
        if action == "approve":
            res = services.approve_payment(code, form.get("method") or "کارت‌به‌کارت")
        elif action == "reject":
            res = services.reject_payment(code, form.get("reason", ""))
        elif action == "renew":
            res = services.renew_membership(code, form.get("role") or None)
        elif action in ("revoke", "activate"):
            res = services.set_member_status(code, "revoked" if action == "revoke" else "active")
        elif action == "edit":
            fields = ["name", "role", "role_other", "child_name", "child_group", "phone", "tg_phone", "topics", "wish", "note"]
            with connect() as conn:
                conn.execute(f"UPDATE members SET {', '.join(f + ' = ?' for f in fields)}, fee = ? WHERE code = ?",
                             [str(form.get(f, "")).strip() for f in fields] +
                             [services.fee_for_role(str(form.get("role", ""))), code])
            res = services.Result(True, "مشخصات عضو ذخیره شد.")
        else:
            raise HTTPException(404)
        return await _apply(request, res)

    # --- books
    @app.get("/admin/books", response_class=HTMLResponse)
    async def books(request: Request, q: str = ""):
        _guard(request)
        like = f"%{q.strip()}%"
        rows = _rows("SELECT * FROM books WHERE code LIKE ? OR title LIKE ? OR author LIKE ? ORDER BY code",
                     like, like, like)
        with connect() as conn:
            for r in rows:
                loan = conn.execute(
                    "SELECT l.*, m.name FROM loans l JOIN members m ON m.code = l.member_code"
                    " WHERE l.book_code = ? AND l.status IN ('reserved','active')", (r["code"],)).fetchone()
                r["loan"] = dict(loan) if loan else None
                r["times"] = conn.execute("SELECT COUNT(*) FROM loans WHERE book_code = ? AND status IN"
                                          " ('active','returned','lost')", (r["code"],)).fetchone()[0]
        return _render(request, "admin/books.html", rows=rows, q=q)

    @app.get("/admin/books/edit", response_class=HTMLResponse)
    async def book_edit(request: Request, code: str = ""):
        _guard(request)
        book = dict(services.get_book(code)) if code and services.get_book(code) else None
        if not book:
            with connect() as conn:
                last = conn.execute("SELECT code FROM books WHERE code = base_code ORDER BY code DESC LIMIT 1").fetchone()
            n = int(last["code"].split("-")[1]) + 1 if last else 1
            book = {"code": f"NS-{n:03d}", "condition": "نو", "source": "خرید", "active": 1, "new": True}
        return _render(request, "admin/book_edit.html", b=book)

    @app.post("/admin/books/save")
    async def book_save(request: Request):
        _guard(request)
        form = {k: str(v).strip() for k, v in (await request.form()).items()}
        code = form.get("code", "").upper()
        if not code or not form.get("title"):
            return _back(request, "کد و عنوان کتاب لازم است.", False)
        parts = code.split("-")
        form["code"] = code
        form["base_code"] = "-".join(parts[:2]) if len(parts) > 2 else code
        if form.get("added_at"):
            try:
                form["added_at"] = jalali.to_jalali(jalali.parse_jalali(form["added_at"]))
            except ValueError:
                pass
        upsert_books([form])
        with connect() as conn:
            conn.execute("UPDATE books SET active = ? WHERE code = ?", (1 if form.get("active") else 0, code))
        return _back(request, f"کتاب {code} ذخیره شد.", True, "/admin/books")

    # --- loans
    @app.get("/admin/loans", response_class=HTMLResponse)
    async def loans(request: Request, status: str = "open"):
        _guard(request)
        where = {"open": "l.status IN ('reserved','active')", "all": "1=1"}.get(status, "l.status = ?")
        args = () if status in ("open", "all") else (status,)
        rows = _rows("SELECT l.*, b.title, m.name, m.phone FROM loans l JOIN books b ON b.code = l.book_code"
                     f" JOIN members m ON m.code = l.member_code WHERE {where} ORDER BY l.id DESC LIMIT 500", *args)
        today = jalali.today()
        for r in rows:
            r["late"] = services.late_days(r, today) if r["status"] in ("active", "returned") else 0
            r["current_fine"] = services.fine_for(r["late"]) if r["status"] == "active" else r["fine"]
        return _render(request, "admin/loans.html", rows=rows, status=status,
                       conditions=config.RETURN_CONDITIONS, day_choices=config.LOAN_DAY_CHOICES)

    @app.post("/admin/loans/new")
    async def loan_new(request: Request):
        _guard(request)
        f = await request.form()
        res = services.direct_loan(str(f.get("book_code", "")), str(f.get("member_code", "")),
                                   int(f.get("days") or config.LOAN_DEFAULT_DAYS), str(f.get("by", "")),
                                   force=bool(f.get("force")))
        return await _apply(request, res)

    @app.post("/admin/loans/{loan_id}/{action}")
    async def loan_action(request: Request, loan_id: int, action: str):
        _guard(request)
        f = await request.form()
        if action == "handover":
            res = services.handover(loan_id, str(f.get("by", "")))
        elif action == "return":
            res = services.return_book(loan_id, str(f.get("condition") or "سالم"), str(f.get("by", "")))
        elif action == "cancel":
            res = services.cancel_reservation(loan_id, str(f.get("reason", "")))
        elif action == "finepaid":
            res = services.mark_fine_paid(loan_id, str(f.get("method") or "کارت‌به‌کارت"))
        elif action == "due":
            try:
                new_due = jalali.parse_jalali(str(f.get("due", "")))
            except ValueError as e:
                return _back(request, str(e), False)
            with connect() as conn:
                conn.execute("UPDATE loans SET due_date = ?, reminded = '' WHERE id = ? AND status = 'active'",
                             (new_due.isoformat(), loan_id))
            res = services.Result(True, f"مهلت امانت #{jalali.fa(loan_id)} شد {jalali.to_jalali(new_due)}.")
        else:
            raise HTTPException(404)
        return await _apply(request, res)

    # --- payments
    @app.get("/admin/payments", response_class=HTMLResponse)
    async def payments(request: Request):
        _guard(request)
        rows = _rows("SELECT * FROM payments ORDER BY date DESC, id DESC LIMIT 1000")
        return _render(request, "admin/payments.html", rows=rows, stats=services.dashboard())

    @app.post("/admin/payments/new")
    async def payment_new(request: Request):
        _guard(request)
        f = {k: str(v).strip() for k, v in (await request.form()).items()}
        try:
            date = jalali.parse_jalali(f["date"]) if f.get("date") else jalali.today()
            amount = int(jalali.en(f.get("amount", "0")).replace(",", "").replace("٬", ""))
        except ValueError:
            return _back(request, "تاریخ یا مبلغ درست نیست.", False)
        direction = "in" if f.get("category") in config.PAYMENT_CATEGORIES_IN else "out"
        with connect() as conn:
            conn.execute("INSERT INTO payments (date, category, direction, amount, member_code, book_code, method, ref,"
                         " description) VALUES (?,?,?,?,?,?,?,?,?)",
                         (date.isoformat(), f.get("category", ""), direction, amount, f.get("member_code", "").upper(),
                          f.get("book_code", "").upper(), f.get("method", ""), f.get("ref", ""), f.get("description", "")))
        return _back(request, "سطر حسابداری ثبت شد.")

    # --- export
    @app.get("/admin/export.xlsx")
    async def export_xlsx(request: Request):
        _guard(request)
        data = exporter.excel_bytes()
        name = f"kavikonj-{jalali.en(jalali.to_jalali(jalali.today())).replace('/', '-')}.xlsx"
        return StreamingResponse(io.BytesIO(data), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                                 media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/admin/catalog.html")
    async def export_html(request: Request):
        _guard(request)
        html = exporter.static_catalog_html(grouped_catalog())
        return HTMLResponse(html, headers={"Content-Disposition": 'attachment; filename="kavikonj-books.html"'})
