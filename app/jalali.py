"""Small helpers around Jalali (شمسی) dates and Persian digits."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import jdatetime

from . import config

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_TO_FA = str.maketrans("0123456789", FA_DIGITS)
_TO_EN = str.maketrans(FA_DIGITS + AR_DIGITS, "0123456789" * 2)

MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def today() -> dt.date:
    return dt.datetime.now(ZoneInfo(config.TIMEZONE)).date()


def fa(value) -> str:
    return str(value).translate(_TO_FA)


def en(value) -> str:
    return str(value).translate(_TO_EN)


def money(amount: int | None) -> str:
    """50000 -> «۵۰٬۰۰۰ تومان»."""
    if not amount:
        return "۰ تومان"
    return fa(f"{amount:,}".replace(",", "٬")) + " تومان"


def to_jalali(d: dt.date | str | None) -> str:
    """Gregorian date -> «۱۴۰۵/۰۸/۱۲». Empty string for None."""
    if not d:
        return ""
    if isinstance(d, str):
        d = dt.date.fromisoformat(d[:10])
    j = jdatetime.date.fromgregorian(date=d)
    return fa(f"{j.year}/{j.month:02d}/{j.day:02d}")


def to_jalali_long(d: dt.date | str | None) -> str:
    """Gregorian date -> «۱۲ آبان ۱۴۰۵»."""
    if not d:
        return ""
    if isinstance(d, str):
        d = dt.date.fromisoformat(d[:10])
    j = jdatetime.date.fromgregorian(date=d)
    return fa(f"{j.day} {MONTHS[j.month - 1]} {j.year}")


def parse_jalali(text: str) -> dt.date:
    """«۱۴۰۵/۷/۲۳» or «1405-07-23» -> Gregorian date. Raises ValueError."""
    parts = en(text).strip().replace("-", "/").split("/")
    if len(parts) != 3:
        raise ValueError("تاریخ باید به شکل ۱۴۰۵/۰۷/۲۳ باشد")
    y, m, d = (int(p) for p in parts)
    return jdatetime.date(y, m, d).togregorian()


def membership_valid_until(registered: dt.date) -> dt.date:
    """Last day of Esfand of the membership year.

    Rule 10: membership lasts until the end of Esfand of the same year; if the
    member registers from 1 Dey onward, until the end of Esfand of next year.
    """
    j = jdatetime.date.fromgregorian(date=registered)
    year = j.year + 1 if j.month >= config.NEXT_YEAR_FROM_MONTH else j.year
    first_of_next = jdatetime.date(year + 1, 1, 1).togregorian()
    return first_of_next - dt.timedelta(days=1)


def jalali_year(d: dt.date) -> int:
    return jdatetime.date.fromgregorian(date=d).year
