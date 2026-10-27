"""Daily check (reminders, overdue fines, revocations, expired reservations).

Run once a day, e.g. as a PythonAnywhere scheduled task:
    python /home/<username>/kavikonj/scripts/daily.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import notify, services  # noqa: E402
from app.db import init_db  # noqa: E402

init_db()
notices = services.daily_tasks()
asyncio.run(notify.deliver(notices))
print(f"{len(notices)} notices")
