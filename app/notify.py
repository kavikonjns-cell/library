"""Deliver ``Notice`` objects through the running Telegram bot."""
from __future__ import annotations

import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest

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
    if _bot is None and config.BOT_TOKEN:
        return await _with_bot(send(chat_id, text, buttons))
    if _bot is None:
        log.info("bot offline, not sent to %s: %s", chat_id, text[:80])
        return
    try:
        await _bot.send_message(chat_id, text, reply_markup=keyboard(buttons))
    except Exception:  # a blocked bot or a wrong id must not break the caller
        log.exception("could not send message to %s", chat_id)


def make_bot() -> Bot:
    """A standalone bot client (for web requests and scheduled tasks without a running bot)."""
    request = HTTPXRequest(proxy=config.TG_PROXY or None)
    return Bot(config.BOT_TOKEN, request=request)


async def _with_bot(coro) -> None:
    """Run ``coro`` with a short-lived bot client; never raise (Telegram may be unreachable)."""
    try:
        async with make_bot() as bot:
            set_bot(bot)
            try:
                await coro
            finally:
                set_bot(None)
    except Exception:
        coro.close()
        log.exception("Telegram is not reachable; messages not sent")


async def deliver(notices: list[services.Notice]) -> None:
    if not notices:
        return
    for n in notices:
        if n.member_code and n.inbox and not getattr(n, "_stored", False):
            try:
                services.add_inbox(n.member_code, n.text)
                n._stored = True  # a retried delivery must not store it twice
            except Exception:
                log.exception("could not store inbox message")
    if _bot is None and config.BOT_TOKEN:
        return await _with_bot(deliver(notices))
    for n in notices:
        if n.admins:
            for admin_id in config.ADMIN_IDS:
                await send(admin_id, n.text, n.buttons)
        if n.member_code:
            member = services.get_member(n.member_code)
            if member and member["tg_user_id"]:
                await send(member["tg_user_id"], n.text, n.buttons)


async def send_photo_to_admins(data: bytes, filename: str, caption: str, buttons=None) -> str | None:
    """Send an uploaded photo (e.g. a receipt from the website) to every librarian; returns its file_id."""
    async def _send(bot) -> str | None:
        file_id = None
        for admin_id in config.ADMIN_IDS:
            try:
                msg = await bot.send_photo(admin_id, photo=file_id or data, caption=caption[:1000],
                                           reply_markup=keyboard(buttons), filename=filename or "receipt.jpg")
                file_id = file_id or msg.photo[-1].file_id
            except Exception:
                log.exception("could not send photo to %s", admin_id)
        return file_id

    if _bot is not None:
        return await _send(_bot)
    if not config.BOT_TOKEN:
        return None
    try:
        async with make_bot() as bot:
            return await _send(bot)
    except Exception:
        log.exception("Telegram is not reachable; photo not sent")
        return None
