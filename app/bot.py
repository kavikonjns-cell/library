"""Telegram bot: registration, book requests, receipts, reminders and librarian tools."""
from __future__ import annotations

import datetime as dt
import logging
import re
from zoneinfo import ZoneInfo

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, MenuButtonWebApp,
                      ReplyKeyboardMarkup, Update, WebAppInfo)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

from . import config, notify, services
from .db import connect
from .jalali import en, fa, money, to_jalali, today as jtoday

log = logging.getLogger(__name__)

BOOK_CODE = re.compile(r"\bNS[-‐–]?\s?(\d{1,3})(?:[-‐–](\d))?\b", re.I)
LINK_RE = re.compile(r"^\s*M[-‐–]?\s?(\d+)\s+(\+?\d[\d\s-]{8,})\s*$", re.I)

BTN_CATALOG = "📚 فهرست کتاب‌ها"
BTN_ME = "👤 وضعیت من"
BTN_EXTEND = "🔁 تمدید امانت"
BTN_RULES = "ℹ️ قوانین"
BTN_REGISTER = "📝 فرم عضویت"
BTN_LINK = "🔗 قبلاً عضو شده‌ام"

RULES_TEXT = (
    "📜 قوانین کتابخانه کاوی کنج\n\n"
    "۱. هر عضو در یک زمان فقط یک کتاب امانت می‌گیرد.\n"
    f"۲. مدت امانت دو هفته است. با تمدید، کل مدت از روز تحویل حداکثر {fa(config.LOAN_MAX_TOTAL_DAYS)} روز می‌شود.\n"
    "۳. تمدید فقط پیش از پایان مهلت و وقتی کسی در صف انتظار نباشد ممکن است.\n"
    f"۴. تحویل گرفتن و بازگرداندن کتاب فقط در {config.PICKUP_PLACE} انجام می‌شود.\n"
    f"۵. برای هر روز تأخیر {money(config.FINE_PER_DAY)} جریمه، حداکثر تا {fa(config.FINE_MAX_DAYS)} روز. "
    "تا جریمه تسویه نشود، کتاب تازه امانت داده نمی‌شود.\n"
    f"۶. اگر تأخیر به {fa(config.REVOKE_AFTER_LATE_DAYS)} روز برسد، عضویت باطل می‌شود.\n"
    "۷. کتاب با همان وضعیت برمی‌گردد؛ بدون یادداشت، خط کشیدن یا تا زدن صفحه‌ها.\n"
    "۸. اگر کتاب گم شود یا آسیب جدی ببیند، عضو نسخه‌ای جدید از همان کتاب تهیه می‌کند.\n"
    "۹. کتاب امانتی به شخص دیگری داده نمی‌شود.\n"
    "۱۰. عضویت تا پایان اسفند همان سال معتبر است؛ برای ثبت‌نام از اول دی به بعد، تا پایان اسفند سال بعد.\n"
    "۱۱. حق عضویت در هیچ شرایطی بازگردانده نمی‌شود."
)


# ----------------------------------------------------------------- helpers

def _https() -> bool:
    return config.BASE_URL.startswith("https://")


def _is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id in config.ADMIN_IDS)


def _admin_name(update: Update) -> str:
    u = update.effective_user
    return (u.full_name or u.username or str(u.id)) if u else ""


def main_keyboard(member) -> ReplyKeyboardMarkup:
    def web(label: str, path: str) -> KeyboardButton:
        return KeyboardButton(label, web_app=WebAppInfo(config.BASE_URL + path)) if _https() else KeyboardButton(label)

    if member:
        rows = [[web(BTN_CATALOG, "/")], [KeyboardButton(BTN_ME), KeyboardButton(BTN_EXTEND)], [KeyboardButton(BTN_RULES)]]
    else:
        rows = [[web(BTN_REGISTER, "/app/register")], [web(BTN_CATALOG, "/")],
                [KeyboardButton(BTN_LINK), KeyboardButton(BTN_RULES)]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def _markup(buttons) -> InlineKeyboardMarkup | None:
    return notify.keyboard(buttons)


async def _reply(update: Update, text: str, buttons=None, keyboard=None) -> None:
    await update.effective_message.reply_text(text, reply_markup=keyboard or _markup(buttons))


async def _deliver(res: services.Result) -> None:
    await notify.deliver(res.notices)


def _normalize_code(m: re.Match) -> str:
    code = f"NS-{int(m.group(1)):03d}"
    return f"{code}-{m.group(2)}" if m.group(2) else code


# ------------------------------------------------------------- member side

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    member = services.get_member_by_tg(user.id)
    payload = context.args[0] if context.args else ""
    if member and member["tg_username"] != (user.username or ""):
        with connect() as conn:
            conn.execute("UPDATE members SET tg_username = ? WHERE code = ?", (user.username or "", member["code"]))

    greeting = (f"سلام {user.first_name or ''} 🌿\nبه ربات {config.LIBRARY_NAME} خوش آمدید.\n\n")
    if member:
        greeting += f"کد عضویت شما: {member['code']}\nاز دکمه‌های پایین برای دیدن کتاب‌ها و وضعیت امانت استفاده کنید."
    else:
        greeting += ("برای امانت گرفتن کتاب، اول «📝 فرم عضویت» را پر کنید تا کد عضویت برایتان صادر شود.\n"
                     "اگر قبلاً (با فرم کاغذی) عضو شده‌اید، «🔗 قبلاً عضو شده‌ام» را بزنید.")
        if not _https():
            greeting += "\n\nمی‌توانید متن آماده‌شدۀ فرم عضویت را هم مستقیم همین‌جا بفرستید."
    await _reply(update, greeting, keyboard=main_keyboard(member))

    if payload.startswith("b_"):
        await show_book(update, context, payload[2:])
    elif payload == "register" and not member and _https():
        await update.effective_message.reply_text(
            "برای باز کردن فرم عضویت دکمۀ زیر را بزنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                "📝 فرم عضویت", web_app=WebAppInfo(config.BASE_URL + "/app/register"))]]))


async def show_book(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    book = services.get_book(code)
    if not book:
        await _reply(update, f"کتابی با کد {code} پیدا نشد.")
        return
    with connect() as conn:
        st = services.title_status(conn, book["base_code"])
    lines = [f"📗 {book['title']}", f"کد: {book['base_code']}"]
    if book["author"]:
        lines.append(book["author"])
    if book["translator"]:
        lines.append(f"ترجمۀ {book['translator']}")
    if book["short_intro"]:
        lines.append("\n" + book["short_intro"])
    lines.append("\n" + services.status_label(st))
    if st["waiting"]:
        lines.append(f"🕒 {fa(st['waiting'])} نفر در صف انتظار")
    verb = "رزرو" if st["state"] == "available" else "صف انتظار"
    lines.append(f"\nبرای {verb}، مدت امانت را انتخاب کنید:")
    row = [(f"{fa(d)} روز", f"req:{book['base_code']}:{d}") for d in config.LOAN_DAY_CHOICES]
    await _reply(update, "\n".join(lines), [row])


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    member = services.get_member_by_tg(update.effective_user.id)
    if not member:
        await _reply(update, "هنوز عضو نیستید. «📝 فرم عضویت» را بزنید.", keyboard=main_keyboard(None))
        return
    buttons = []
    with connect() as conn:
        for w in conn.execute("SELECT w.base_code, b.title FROM waitlist w JOIN books b ON b.code = w.base_code"
                              " WHERE w.member_code = ? AND w.status = 'waiting'", (member["code"],)).fetchall():
            buttons.append([(f"خروج از صف «{w['title'][:25]}»", f"leave:{w['base_code']}")])
    await _reply(update, services.member_summary(member["code"]), buttons)


async def extend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    member = services.get_member_by_tg(update.effective_user.id)
    if not member:
        await _reply(update, "هنوز عضو نیستید.")
        return
    res = services.extension_options(member["code"])
    if not res.ok:
        await _reply(update, res.message)
        return
    row = [(to_jalali(d), f"ext:{d}") for d in res.data["choices"]]
    await _reply(update, res.message, [row])


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, RULES_TEXT)


async def catalog_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "📚 فهرست کتاب‌ها و وضعیت قفسه:\n" + config.BASE_URL + "/\n\nیا کد کتاب (مثلاً NS-012) را همین‌جا بفرستید."
    buttons = None
    if _https():
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton("📚 باز کردن فهرست",
                                                              web_app=WebAppInfo(config.BASE_URL + "/"))]])
    await update.effective_message.reply_text(text, reply_markup=buttons)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    text = msg.text or ""
    user = update.effective_user
    member = services.get_member_by_tg(user.id)

    if text == BTN_ME:
        return await me(update, context)
    if text == BTN_EXTEND:
        return await extend(update, context)
    if text == BTN_RULES:
        return await rules(update, context)
    if text in (BTN_CATALOG, BTN_REGISTER) and not _https():
        if text == BTN_REGISTER:
            return await _reply(update, "فرم عضویت: " + config.BASE_URL + "/app/register\n"
                                "فرم را پر کنید و متن آماده‌شده را همین‌جا بفرستید.")
        return await catalog_link(update, context)
    if text == BTN_LINK:
        context.user_data["linking"] = True
        return await _reply(update, "کد عضویت و شمارۀ تماسی را که در فرم نوشته‌اید بفرستید، مثلاً:\nM-07 09121234567")

    if context.user_data.pop("feedback", False) and member:
        ok = services.save_feedback(member["code"], text)
        await _reply(update, "💬 ممنون! نظرتان ثبت شد." if ok else "کتاب برگشته‌ای برای ثبت نظر پیدا نشد.")
        if ok:
            await notify.deliver([services.Notice(admins=True, text=f"💬 نظر {member['code']} ({member['name']}):\n{text}")])
        return

    link = LINK_RE.match(en(text))
    if link and not member:
        return await link_member(update, f"M-{int(link.group(1)):02d}", link.group(2))

    form = services.parse_registration_text(text)
    if form:
        if member:
            return await _reply(update, f"شما قبلاً با کد {member['code']} عضو شده‌اید.")
        res = services.register_member(form, user.id, user.username or "")
        await _reply(update, res.message, keyboard=main_keyboard(services.get_member_by_tg(user.id)) if res.ok else None)
        await _deliver(res)
        return

    codes = BOOK_CODE.findall(en(text))
    if codes:
        m = BOOK_CODE.search(en(text))
        return await show_book(update, context, _normalize_code(m))

    if _is_admin(update):
        return await _reply(update, ADMIN_HELP)
    await _reply(update, "متوجه نشدم 🙂 برای دیدن کتاب‌ها «📚 فهرست کتاب‌ها» را بزنید یا کد کتاب (مثل NS-012) را بفرستید.",
                 keyboard=main_keyboard(member))


async def link_member(update: Update, code: str, phone: str) -> None:
    m = services.get_member(code)
    if not m or services.normalize_phone(phone) != m["phone"]:
        await _reply(update, "کد عضویت و شمارۀ تماس با هم جور نیستند. دوباره بررسی کنید یا با مسئول کتابخانه تماس بگیرید.")
        return
    if m["tg_user_id"]:
        await _reply(update, "این کد عضویت قبلاً به یک حساب تلگرام وصل شده است. با مسئول کتابخانه تماس بگیرید.")
        return
    user = update.effective_user
    with connect() as conn:
        conn.execute("UPDATE members SET tg_user_id = ?, tg_username = ? WHERE code = ?",
                     (user.id, user.username or "", code))
    await _reply(update, f"✅ حساب تلگرام شما به عضویت {code} ({m['name']}) وصل شد.",
                 keyboard=main_keyboard(services.get_member(code)))


async def on_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo or file: most likely a payment receipt."""
    msg = update.effective_message
    member = services.get_member_by_tg(update.effective_user.id)
    if not member:
        await _reply(update, "اول فرم عضویت را پر کنید؛ بعد عکس فیش را بفرستید.")
        return
    file_id = msg.photo[-1].file_id if msg.photo else msg.document.file_id
    with connect() as conn:
        fine = conn.execute("SELECT * FROM loans WHERE member_code = ? AND fine > 0 AND fine_paid = 0"
                            " AND status IN ('returned','lost') ORDER BY id LIMIT 1", (member["code"],)).fetchone()
    if fine:
        caption = f"🧾 فیش جریمه از {member['code']} ({member['name']})\nامانت #{fa(fine['id'])} · مبلغ {money(fine['fine'])}"
        buttons = [[("✅ جریمه تسویه شد", f"fine_ok:{fine['id']}")]]
    elif member["payment_status"] != "paid":
        services.attach_receipt(member["code"], file_id)
        caption = (f"🧾 فیش حق عضویت از {member['code']} · {member['name']}\nنقش: {member['role']} · "
                   f"مبلغ: {money(member['fee'])}\nتماس: {fa(member['phone'])}")
        buttons = [[("✅ تأیید پرداخت", f"pay_ok:{member['code']}"), ("❌ رد فیش", f"pay_no:{member['code']}")]]
    else:
        caption = f"📎 فایل از {member['code']} ({member['name']})"
        buttons = None
    for admin_id in config.ADMIN_IDS:
        try:
            await msg.copy(admin_id, caption=caption, reply_markup=_markup(buttons))
        except Exception:
            log.exception("could not forward receipt to %s", admin_id)
    await _reply(update, "🧾 دریافت شد و برای مسئول کتابخانه فرستاده شد. بعد از بررسی خبرتان می‌کنیم.")


# ---------------------------------------------------------------- callbacks

ADMIN_ACTIONS = {"pay_ok", "pay_no", "hand", "cancel", "ret", "retask", "fine_ok"}


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    action, _, arg = (q.data or "").partition(":")
    if action in ADMIN_ACTIONS and not _is_admin(update):
        await q.answer("فقط مسئول کتابخانه.", show_alert=True)
        return
    await q.answer()
    member = services.get_member_by_tg(update.effective_user.id)
    by = _admin_name(update)

    if action == "req":
        code, _, days = arg.partition(":")
        res = services.request_book(member["code"] if member else None, code, int(days or 0))
        await q.edit_message_reply_markup(None)
        await _reply(update, res.message)
        return await _deliver(res)
    if action == "ext":
        if not member:
            return
        res = services.extend_loan(member["code"], dt.date.fromisoformat(arg))
        await q.edit_message_reply_markup(None)
        await _reply(update, res.message)
        return await _deliver(res)
    if action == "leave" and member:
        res = services.leave_waitlist(member["code"], arg)
        return await _reply(update, res.message)
    if action == "fb":
        context.user_data["feedback"] = True
        return await _reply(update, "نظرتان دربارۀ کتاب را بنویسید و بفرستید:")

    # librarian actions
    if action == "pay_ok":
        res = services.approve_payment(arg, by=by)
    elif action == "pay_no":
        res = services.reject_payment(arg)
    elif action == "hand":
        res = services.handover(int(arg), by)
    elif action == "cancel":
        res = services.cancel_reservation(int(arg), "به درخواست مسئول کتابخانه")
    elif action == "retask":
        loan = services.get_loan(int(arg))
        rows = [[(c, f"ret:{arg}:{i}")] for i, c in enumerate(config.RETURN_CONDITIONS)]
        return await _reply(update, f"وضعیت کتاب «{loan['title']}» هنگام بازگشت؟", rows)
    elif action == "ret":
        loan_id, _, idx = arg.partition(":")
        res = services.return_book(int(loan_id), config.RETURN_CONDITIONS[int(idx or 0)], by)
        if res.ok:
            for n in res.notices:
                if n.member_code and not n.buttons and "ممنون از شما" in n.text:
                    n.buttons = [[("✍️ نوشتن نظر دربارۀ کتاب", "fb")]]
    elif action == "fine_ok":
        res = services.mark_fine_paid(int(arg))
    else:
        return
    stamp = ("✅ " if res.ok else "⚠️ ") + res.message + f" ({by})"
    try:
        if q.message.caption is not None:
            await q.edit_message_caption((q.message.caption or "") + "\n\n" + stamp)
        else:
            await q.edit_message_text((q.message.text or "") + "\n\n" + stamp)
    except Exception:
        await _reply(update, stamp)
    await _deliver(res)


# ------------------------------------------------------------- librarian

ADMIN_HELP = (
    "🛠 دستورهای مسئول کتابخانه:\n"
    "/admin — خلاصۀ امروز و کارهای مانده\n"
    "/loans — امانت‌های فعال با دکمۀ «برگشت»\n"
    "/lend NS-012 M-07 14 — امانت حضوری (بدون رزرو)\n"
    "/return NS-012 — ثبت برگشت کتاب\n"
    "/member M-07 — مشخصات و وضعیت یک عضو\n"
    "/find کلمه — جست‌وجوی کتاب یا عضو\n"
    f"/daily — اجرای دستی بررسی روزانه (یادآوری‌ها و دیرکردها)\n\nپنل وب: {config.BASE_URL}/admin"
)


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update):
            return await _reply(update, "این دستور فقط برای مسئول کتابخانه است.")
        return await func(update, context)
    return wrapper


@admin_only
async def admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = services.dashboard()
    text = (f"📊 {config.LIBRARY_NAME}\n"
            f"عنوان‌ها: {fa(s['titles'])} · نسخه‌ها: {fa(s['copies'])} · موجود: {fa(s['available'])}\n"
            f"در امانت: {fa(s['on_loan'])} · رزرو: {fa(s['reserved'])} · دیرکرد: {fa(s['overdue'])}\n"
            f"اعضای معتبر: {fa(s['valid_members'])} از {fa(s['members'])} · منتظر پرداخت: {fa(s['pending_payments'])}\n"
            f"صف انتظار: {fa(s['waiting'])} · جریمۀ پرداخت‌نشده: {money(s['unpaid_fines'])}\n"
            f"موجودی صندوق: {money(s['balance'])}")
    await _reply(update, text)
    with connect() as conn:
        res = conn.execute("SELECT l.*, b.title, m.name FROM loans l JOIN books b ON b.code = l.book_code"
                           " JOIN members m ON m.code = l.member_code WHERE l.status = 'reserved' ORDER BY l.id").fetchall()
        pend = conn.execute("SELECT * FROM members WHERE payment_status != 'paid' AND status = 'active'").fetchall()
    for r in res:
        await _reply(update, f"📌 رزرو #{fa(r['id'])}: «{r['title']}» ({r['book_code']}) برای {r['member_code']} {r['name']}"
                             f"\nتحویل گرفتن تا {to_jalali(r['reserve_expires'])}",
                     [[("✅ تحویل داده شد", f"hand:{r['id']}"), ("✖️ لغو", f"cancel:{r['id']}")]])
    for m in pend:
        await _reply(update, f"⏳ منتظر پرداخت: {m['code']} {m['name']} · {money(m['fee'])}"
                             + (" · فیش رسیده" if m["receipt_file_id"] else ""),
                     [[("✅ تأیید پرداخت", f"pay_ok:{m['code']}"), ("❌ رد", f"pay_no:{m['code']}")]])


@admin_only
async def admin_loans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = jtoday()
    with connect() as conn:
        rows = conn.execute("SELECT l.*, b.title, m.name FROM loans l JOIN books b ON b.code = l.book_code"
                            " JOIN members m ON m.code = l.member_code WHERE l.status = 'active' ORDER BY l.due_date").fetchall()
    if not rows:
        return await _reply(update, "امانت فعالی نیست.")
    for r in rows[:40]:
        late = services.late_days(r, today)
        await _reply(update, f"📗 #{fa(r['id'])} «{r['title']}» ({r['book_code']})\n{r['member_code']} {r['name']}"
                             f" · مهلت {to_jalali(r['due_date'])}" + (f" · ⏰ {fa(late)} روز تأخیر" if late else ""),
                     [[("📥 برگشت", f"retask:{r['id']}")]])


@admin_only
async def admin_lend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = [en(a) for a in context.args]
    if len(args) < 2:
        return await _reply(update, "شکل درست: /lend NS-012 M-07 14")
    days = int(args[2]) if len(args) > 2 and args[2].isdigit() else config.LOAN_DEFAULT_DAYS
    res = services.direct_loan(args[0], args[1], days, _admin_name(update))
    await _reply(update, ("✅ " if res.ok else "⚠️ ") + res.message)
    await _deliver(res)


@admin_only
async def admin_return(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        return await _reply(update, "شکل درست: /return NS-012")
    code = en(context.args[0]).upper()
    with connect() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE book_code = ? AND status = 'active'", (code,)).fetchone()
    if not loan:
        return await _reply(update, f"امانت فعالی برای {code} نیست.")
    rows = [[(c, f"ret:{loan['id']}:{i}")] for i, c in enumerate(config.RETURN_CONDITIONS)]
    await _reply(update, f"وضعیت {code} هنگام بازگشت؟", rows)


@admin_only
async def admin_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        return await _reply(update, "شکل درست: /member M-07")
    code = en(context.args[0]).upper()
    if not code.startswith("M-"):
        code = "M-" + code.lstrip("M")
    if code[2:].isdigit():
        code = f"M-{int(code[2:]):02d}"
    m = services.get_member(code)
    if not m:
        return await _reply(update, "عضوی با این کد نیست.")
    await _reply(update, services.member_summary(code) + f"\n📞 {fa(m['phone'])}" +
                 (f" · @{m['tg_username']}" if m["tg_username"] else ""))


@admin_only
async def admin_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    term = " ".join(context.args).strip()
    if not term:
        return await _reply(update, "شکل درست: /find بازی")
    like = f"%{term}%"
    with connect() as conn:
        books = conn.execute("SELECT code, title FROM books WHERE title LIKE ? OR author LIKE ? OR code LIKE ? LIMIT 15",
                             (like, like, like)).fetchall()
        members = conn.execute("SELECT code, name, phone FROM members WHERE name LIKE ? OR phone LIKE ? OR code LIKE ? LIMIT 15",
                               (like, en(like), like)).fetchall()
    lines = [f"📗 {b['code']} {b['title']}" for b in books] + [f"👤 {m['code']} {m['name']} {fa(m['phone'])}" for m in members]
    await _reply(update, "\n".join(lines) or "چیزی پیدا نشد.")


@admin_only
async def admin_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    notices = services.daily_tasks()
    await notify.deliver(notices)
    await _reply(update, f"بررسی روزانه انجام شد؛ {fa(len(notices))} پیام فرستاده شد.")


async def daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await notify.deliver(services.daily_tasks())


# ----------------------------------------------------------------- wiring

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "شروع و منوی اصلی"),
        BotCommand("me", "وضعیت عضویت و امانت من"),
        BotCommand("books", "فهرست کتاب‌ها"),
        BotCommand("extend", "تمدید امانت"),
        BotCommand("rules", "قوانین کتابخانه"),
    ])
    if _https():
        await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp("کتاب‌ها", WebAppInfo(config.BASE_URL + "/")))


def build_application() -> Application:
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("extend", extend))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("books", catalog_link))
    app.add_handler(CommandHandler("admin", admin_home))
    app.add_handler(CommandHandler("loans", admin_loans))
    app.add_handler(CommandHandler("lend", admin_lend))
    app.add_handler(CommandHandler("return", admin_return))
    app.add_handler(CommandHandler("member", admin_member))
    app.add_handler(CommandHandler("find", admin_find))
    app.add_handler(CommandHandler("daily", admin_daily))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_receipt))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.job_queue.run_daily(daily_job, dt.time(hour=config.DAILY_JOB_HOUR, tzinfo=ZoneInfo(config.TIMEZONE)))
    return app
