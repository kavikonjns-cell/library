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


@asynccontextmanager
async def lifespan(_app):
    init_db()
    tg_app = None
    if config.BOT_TOKEN:
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
    else:
        log.warning("BOT_TOKEN is empty: running the website only")
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
