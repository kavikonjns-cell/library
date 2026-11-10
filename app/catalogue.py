"""Catalogue helpers shared by the home page and /books: search, filters, ratings, book details."""
from __future__ import annotations

import re

from . import club, config, jalali, services
from .db import connect

PAGE_SIZE = 24
SORTS = ("newest", "title", "rating")
SHELF_TOPICS = [t for t in config.TOPICS if t != "متفرقه"]


def fold(text: str) -> str:
    """Compare Persian text without caring about Arabic ي/ك, half-spaces, digit script or case."""
    text = jalali.en(text or "")
    text = text.replace("ي", "ی").replace("ك", "ک").replace("‌", " ").replace("ـ", "")
    return re.sub(r"\s+", " ", text).strip().lower()


def clean_label(label: str) -> str:
    """Status labels start with an emoji; the new design uses a coloured dot instead."""
    return re.sub(r"^[^\w؀-ۿ]+", "", label)


def topics_of(book: dict) -> list[str]:
    found = [t for t in (book.get("topic1"), book.get("topic2")) if t in SHELF_TOPICS]
    return found or ["متفرقه"]


def primary_topic(book: dict) -> str:
    return topics_of(book)[0]


def ratings() -> dict[str, dict]:
    out = {}
    for base, items in club.all_reviews().items():
        rs = [r["rating"] for r in items if r["rating"]]
        out[base] = {"avg": round(sum(rs) / len(rs), 1) if rs else 0, "n": len(rs), "texts": len(items)}
    return out


def _haystack(b: dict) -> str:
    parts = [b["code"], b["code"].replace("-", ""), b["title"], b["author"], b["translator"],
             b["topic1"], b["topic2"]]
    return fold(" ".join(p for p in parts if p))


def search(books: list[dict], q: str = "", category: str = "", available: bool = False,
           sort: str = "newest", rate: dict | None = None) -> list[dict]:
    words = fold(q).split()
    out = []
    for b in books:
        if category and category not in topics_of(b):
            continue
        if available and b["status"]["state"] != "available":
            continue
        if words:
            hay = _haystack(b)
            if not all(w in hay or w.replace("-", "") in hay for w in words):
                continue
        out.append(b)
    rate = rate or {}
    if sort == "title":
        out.sort(key=lambda b: fold(b["title"]))
    elif sort == "rating":
        out.sort(key=lambda b: (rate.get(b["code"], {}).get("avg", 0) > 0,
                                rate.get(b["code"], {}).get("avg", 0), b["added_at"] or ""), reverse=True)
    else:
        out.sort(key=lambda b: (b["added_at"] or "", b["code"]), reverse=True)
    return out


def topic_counts(books: list[dict]) -> dict[str, int]:
    return {t: sum(1 for b in books if t in topics_of(b)) for t in SHELF_TOPICS}


def group(books: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group a (page of) results by the title's main topic, keeping topic order."""
    groups: dict[str, list[dict]] = {t: [] for t in config.TOPICS}
    for b in books:
        groups[primary_topic(b)].append(b)
    return [(t, items) for t, items in groups.items() if items]


def detail(member, base_code: str):
    """Everything the detail dialog / page needs, or None for an unknown title."""
    book = next((b for b in services.catalog() if b["code"] == base_code.upper()), None)
    if book is None:
        return None
    book["status_label"] = clean_label(book["status_label"])
    reviews = club.all_reviews().get(book["code"], [])
    rate = ratings().get(book["code"])
    action = {"kind": "guest"}
    if member is not None:
        on = jalali.today()
        state = book["status"]["state"]
        with connect() as conn:
            queued = conn.execute("SELECT 1 FROM waitlist WHERE base_code = ? AND member_code = ? AND status = 'waiting'",
                                  (book["code"], member["code"])).fetchone()
            current = services._member_open_loan(conn, member["code"])
            problems = services.borrow_problems(conn, member, on)
        hard = services.membership_problems(member, on)
        if current and current["base_code"] == book["code"]:
            action = {"kind": "blocked", "text": "این کتاب همین حالا به نام توست."}
        elif queued:
            action = {"kind": "blocked", "text": "در صف انتظار این کتاب هستی. وقتی نوبتت شد، ربات و سایت خبرت می‌کنند."}
        elif state == "available":
            action = {"kind": "borrow"} if not problems else {"kind": "blocked", "text": problems[0]}
        elif state == "loaned":
            action = {"kind": "queue"} if not hard else {"kind": "blocked", "text": hard[0]}
        elif state == "reserved":
            action = {"kind": "blocked", "text": "این کتاب برای عضو دیگری رزرو شده است. اگر رزرو آزاد شود، می‌توانی درخواست بدهی."}
        else:
            action = {"kind": "blocked", "text": "این کتاب فعلاً در دسترس نیست."}
    return {"book": book, "reviews": reviews[:3], "review_total": len(reviews), "rate": rate, "action": action,
            "days": config.LOAN_DAY_CHOICES, "default_days": min(config.LOAN_DEFAULT_DAYS, config.LOAN_MAX_INITIAL_DAYS)}
