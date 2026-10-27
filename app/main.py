"""Run the website and the Telegram bot together in one process.

    python -m app.main
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn

from . import config, notify
from .db import init_db
from .web import create_app

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("kavikonj")


def _https() -> bool:
    return config.BASE_URL.startswith("https://")


async def _register_webhook() -> None:
    """Point Telegram at /tg/<secret> on this site (idempotent, safe to run on every start)."""
    from .bot import setup_bot
    try:
        async with notify.make_bot() as bot:
            url = f"{config.BASE_URL}/tg/{config.WEBHOOK_SECRET}"
            info = await bot.get_webhook_info()
            if info.url != url:
                await bot.set_webhook(url, secret_token=config.WEBHOOK_SECRET,
                                      allowed_updates=["message", "callback_query"])
            await setup_bot(bot)  # command list may have changed with this release
            log.info("Telegram webhook ready")
    except Exception:
        log.exception("could not register the Telegram webhook")


@asynccontextmanager
async def lifespan(_app):
    init_db()
    tg_app = None
    if config.BOT_TOKEN and config.BOT_MODE == "polling":
        from .bot import build_application
        tg_app = build_application()
        await tg_app.initialize()
        if tg_app.post_init:  # only run_polling() calls it by itself
            try:
                await tg_app.post_init(tg_app)
            except Exception:
                log.exception("could not set bot commands / menu button")
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=False)
        notify.set_bot(tg_app.bot)
        log.info("Telegram bot started")
    elif config.BOT_TOKEN and config.BOT_MODE == "webhook" and config.WEBHOOK_SECRET and _https():
        await _register_webhook()
    else:
        log.warning("bot not polling (BOT_MODE=%s, token set: %s)", config.BOT_MODE, bool(config.BOT_TOKEN))
    try:
        yield
    finally:
        if tg_app:
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        notify.set_bot(None)


app = create_app(lifespan)


def main() -> None:
    import os
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 8000)),
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
