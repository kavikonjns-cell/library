"""Settings for Kavi Konj library.

Everything here can be overridden with environment variables (or a .env file
next to the project root). The defaults mirror the «تنظیمات» sheet of the
library Excel file.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _ids(name: str) -> set[int]:
    raw = os.environ.get(name, "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


# --- Telegram / web -------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").lstrip("@")
# Public HTTPS address of the site; Telegram mini-apps only open over HTTPS.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
# Telegram numeric user ids of librarians, comma separated.
ADMIN_IDS = _ids("ADMIN_IDS")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "data" / "kavikonj.db"))
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tehran")
DAILY_JOB_HOUR = _int("DAILY_JOB_HOUR", 9)

# --- Library rules (قوانین کتابخانه) --------------------------------------
LIBRARY_NAME = "کتابخانه کاوی کنج"
LIBRARY_SUB = "کتابخانه کودک و طبیعت · تأسیس ۱۴۰۵"
SLOGAN = "کاوی کنج؛ همراهی با کتاب، برای شناخت عمیق‌تر جهان کودک و طبیعت"
PICKUP_PLACE = "باغ شبنم"

LOAN_DEFAULT_DAYS = _int("LOAN_DEFAULT_DAYS", 14)
# Longest period a member may pick when requesting a book.
LOAN_MAX_INITIAL_DAYS = _int("LOAN_MAX_INITIAL_DAYS", 14)
# Hard cap from the handover day, extensions included.
LOAN_MAX_TOTAL_DAYS = _int("LOAN_MAX_TOTAL_DAYS", 30)
LOAN_DAY_CHOICES = [d for d in (7, 10, 14, 21, 30) if d <= LOAN_MAX_INITIAL_DAYS] or [LOAN_MAX_INITIAL_DAYS]
# How long a reserved book waits on the shelf for pickup.
RESERVE_HOLD_DAYS = _int("RESERVE_HOLD_DAYS", 3)
FINE_PER_DAY = _int("FINE_PER_DAY", 50_000)
FINE_MAX_DAYS = _int("FINE_MAX_DAYS", 4)
# Membership is cancelled when a book is this many days late.
REVOKE_AFTER_LATE_DAYS = _int("REVOKE_AFTER_LATE_DAYS", 5)
REMIND_DAYS_BEFORE = _int("REMIND_DAYS_BEFORE", 2)
# From this Jalali month on, membership lasts until the end of next year.
NEXT_YEAR_FROM_MONTH = 10

ROLE_FEES = {
    "عضو باغ شبنم": _int("FEE_STAFF", 600_000),
    "والد باغ شبنم": _int("FEE_PARENT", 800_000),
    "سایر": _int("FEE_OTHER", 1_000_000),
}
ROLE_HINTS = {
    "عضو باغ شبنم": "تسهیلگر یا کارکنان",
    "والد باغ شبنم": "پدر یا مادر کودکان باغ شبنم",
    "سایر": "والدین و تسهیلگران خارج از باغ شبنم و دیگران",
}
TOPICS = [
    "کودکی، یادگیری، بازی و طبیعت",
    "نوجوانی",
    "نگاه انتقادی به آموزش رسمی",
    "زیست‌شناسی، بوم‌شناسی و طبیعت انسان",
    "مغز و ذهن",
    "مجلات",
    "متفرقه",
]
MEMBER_TOPICS = TOPICS[:5]
RETURN_CONDITIONS = ["سالم", "آسیب جزئی", "آسیب جدی", "گم شده"]
PAYMENT_CATEGORIES_IN = ["حق عضویت", "جریمۀ دیرکرد", "کمک مالی", "سایر دریافت"]
PAYMENT_CATEGORIES_OUT = ["خرید کتاب", "تعمیر و جلد", "لوازم (برچسب، قفسه و...)", "سایر هزینه"]
PAYMENT_METHODS = ["کارت‌به‌کارت", "نقد"]

CARD_NUMBER = os.environ.get("CARD_NUMBER", "5022291574480701")
CARD_OWNER = os.environ.get("CARD_OWNER", "مرضیه زادبر، بانک پاسارگاد")
LIBRARY_PHONE = os.environ.get("LIBRARY_PHONE", "09933892067")
