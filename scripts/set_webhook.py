"""Point Telegram at this site (webhook mode) and register the bot menu and commands.

    python scripts/set_webhook.py
Needs BOT_TOKEN, BASE_URL and WEBHOOK_SECRET in .env.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, notify  # noqa: E402
from app.bot import setup_bot  # noqa: E402


async def main() -> None:
    if not (config.BOT_TOKEN and config.WEBHOOK_SECRET and config.BASE_URL.startswith("https://")):
        sys.exit("BOT_TOKEN, WEBHOOK_SECRET and an https BASE_URL are required")
    async with notify.make_bot() as bot:
        await bot.set_webhook(f"{config.BASE_URL}/tg/{config.WEBHOOK_SECRET}", secret_token=config.WEBHOOK_SECRET,
                              allowed_updates=["message", "callback_query"])
        await setup_bot(bot)
        print(await bot.get_webhook_info())


asyncio.run(main())
