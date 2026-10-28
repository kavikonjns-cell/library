import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import bot, config, services
from app.web import create_app

FORM = {"name": "مریم احمدی", "role": "سایر", "role_other": "تسهیلگر", "phone": "09151112233", "agree": True}


class FakeMessage:
    def __init__(self, data=None):
        self.web_app_data = SimpleNamespace(data=data) if data is not None else None
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)


def fake_update(user_id, data=None):
    msg = FakeMessage(data)
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id, username="m", first_name="مریم"),
                           effective_message=msg), msg


def test_form_from_bottom_menu_registers_without_copy_paste():
    update, msg = fake_update(4242, json.dumps({"t": "register", "form": FORM}, ensure_ascii=False))
    asyncio.run(bot.on_web_app_data(update, SimpleNamespace(user_data={})))
    m = services.get_member_by_tg(4242)
    assert m and m["name"] == "مریم احمدی" and m["role_other"] == "تسهیلگر"
    assert "کد عضویت: M-01" in msg.replies[0]
    # sending it again does not create a second member
    update, msg = fake_update(4242, json.dumps({"t": "register", "form": FORM}, ensure_ascii=False))
    asyncio.run(bot.on_web_app_data(update, SimpleNamespace(user_data={})))
    assert "قبلاً" in msg.replies[0]


def test_book_request_from_bottom_menu_catalogue():
    update, _ = fake_update(4242, json.dumps({"t": "register", "form": FORM}, ensure_ascii=False))
    asyncio.run(bot.on_web_app_data(update, SimpleNamespace(user_data={})))
    update, msg = fake_update(4242, json.dumps({"t": "request", "code": "NS-001", "days": 14}))
    asyncio.run(bot.on_web_app_data(update, SimpleNamespace(user_data={})))
    assert "حق عضویت" in msg.replies[0]   # analysed by the bot: payment not approved yet


def test_form_in_a_browser_finishes_with_one_tap_in_the_bot(monkeypatch):
    monkeypatch.setattr(config, "BOT_USERNAME", "Kavikonj_librarybot")
    c = TestClient(create_app())
    bad = c.post("/api/register/pending", json={"form": {"name": "x"}}).json()
    assert not bad["ok"] and "نقش" in bad["message"]
    r = c.post("/api/register/pending", json={"form": FORM}).json()
    assert r["ok"] and r["link"].startswith("https://t.me/Kavikonj_librarybot?start=reg_")
    token = r["link"].split("reg_", 1)[1]
    update, msg = fake_update(5151)
    asyncio.run(bot.start(update, SimpleNamespace(args=["reg_" + token], user_data={})))
    assert services.get_member_by_tg(5151)["phone"] == "09151112233"
    assert "کد عضویت" in msg.replies[0]
    update, msg = fake_update(6161)                       # the link works only once
    asyncio.run(bot.start(update, SimpleNamespace(args=["reg_" + token], user_data={})))
    assert services.get_member_by_tg(6161) is None and "منقضی" in msg.replies[0]
