"""«کشف بعدی تو»: book suggestions for a member from what they actually did — no AI involved.

Signals (all real data of the member):
* books they borrowed (returned, active or reserved) — their topics and authors count; a finished book
  or a high rating counts more, a low rating counts against;
* the topics they ticked on the membership form.
Every suggested title is a real catalogue title the member has not borrowed, with a reason that names the
signal behind it. Titles that are in the member's hands right now are never suggested.
"""
from __future__ import annotations

from . import services
from .db import connect

FORM_TOPIC_WEIGHT = 1.5


def _topics(book: dict) -> list[str]:
    return [t for t in (book.get("topic1"), book.get("topic2")) if t]


def _split(text: str) -> list[str]:
    import re
    return [t.strip() for t in re.split(r"[؛;،,]", text or "") if t.strip()]


def profile(member_code: str) -> dict:
    """What we know about the member: borrowed titles with their weight, and declared topics."""
    with connect() as conn:
        member = conn.execute("SELECT topics FROM members WHERE code = ?", (member_code,)).fetchone()
        loans = conn.execute(
            "SELECT b.base_code, l.status, l.finished, l.rating FROM loans l JOIN books b ON b.code = l.book_code"
            " WHERE l.member_code = ? AND l.status IN ('reserved','active','returned')", (member_code,)).fetchall()
    borrowed: dict[str, float] = {}
    current: set[str] = set()
    for l in loans:
        weight = 1.0 + (1.0 if l["finished"] else 0.0)
        if l["rating"]:
            weight += (l["rating"] - 3) * 0.5      # 5★ +1, 1★ -1
        borrowed[l["base_code"]] = max(borrowed.get(l["base_code"], 0.0), max(weight, 0.2))
        if l["status"] in ("reserved", "active"):
            current.add(l["base_code"])
    return {"borrowed": borrowed, "current": current, "declared": _split(member["topics"] if member else "")}


def suggest(member_code: str, limit: int = 2) -> dict:
    """{'basis': 'history'|'interests'|'none', 'items': [{'book': {...}, 'reason': '...'}]}"""
    prof = profile(member_code)
    books = services.catalog()
    by_code = {b["code"]: b for b in books}
    if not prof["borrowed"] and not prof["declared"]:
        return {"basis": "none", "items": []}

    topic_w: dict[str, float] = {}
    author_w: dict[str, float] = {}
    topic_src: dict[str, dict] = {}      # the borrowed book that most explains a topic
    author_src: dict[str, dict] = {}
    for code, w in prof["borrowed"].items():
        b = by_code.get(code)
        if not b:
            continue
        for t in _topics(b):
            topic_w[t] = topic_w.get(t, 0.0) + w
            if t not in topic_src or w > prof["borrowed"].get(topic_src[t]["code"], 0):
                topic_src[t] = b
        if b["author"]:
            author_w[b["author"]] = author_w.get(b["author"], 0.0) + w
            author_src.setdefault(b["author"], b)
    for t in prof["declared"]:
        topic_w[t] = topic_w.get(t, 0.0) + FORM_TOPIC_WEIGHT

    scored = []
    for b in books:
        if b["code"] in prof["borrowed"] or b["code"] in prof["current"]:
            continue
        score, reason = 0.0, ""
        if b["author"] and author_w.get(b["author"], 0) > 0:
            score += 2.0 * author_w[b["author"]]
            reason = f"از همان نویسنده‌ای است که «{author_src[b['author']]['title']}» را از او امانت گرفتی."
        best_topic, best = "", 0.0
        for t in _topics(b):
            if topic_w.get(t, 0) > best:
                best_topic, best = t, topic_w[t]
        score += best
        if not reason and best_topic:
            src = topic_src.get(best_topic)
            reason = (f"چون «{src['title']}» را در موضوع «{best_topic}» امانت گرفتی." if src
                      else f"چون موضوع «{best_topic}» را به‌عنوان علاقه‌ات انتخاب کرده‌ای.")
        if score <= 0:
            continue
        if b["status"]["state"] == "available":
            score += 0.3                      # prefer what can be borrowed today
        scored.append((score, b["added_at"] or "", b["code"], b, reason))
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    items = [{"book": b, "reason": reason} for _, _, _, b, reason in scored[:limit]]
    return {"basis": "history" if prof["borrowed"] else "interests", "items": items}
