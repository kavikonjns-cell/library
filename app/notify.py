"""Deliver ``Notice`` objects through the running Telegram bot."""
from __future__ import annotations

import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from . import config, services

log = logging.getLogger(__name__)
_bot: Bot | None = None


def set_bot(bot: Bot | None) -> None:
    global _bot
    _bot = bot


def keyboard(buttons) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row]
                                 for row in buttons])


async def send(chat_id: int, text: str, buttons=None) -> None:
    if _bot is None:
        log.info("bot offline, not sent to %s: %s", chat_id, text[:80])
        return
    try:
        await _bot.send_message(chat_id, text, reply_markup=keyboard(buttons))
    except Exception:  # a blocked bot or a wrong id must not break the caller
        log.exception("could not send message to %s", chat_id)


async def deliver(notices: list[services.Notice]) -> None:
    for n in notices:
        if n.admins:
            for admin_id in config.ADMIN_IDS:
                await send(admin_id, n.text, n.buttons)
        if n.member_code:
            member = services.get_member(n.member_code)
            if member and member["tg_user_id"]:
                await send(member["tg_user_id"], n.text, n.buttons)
