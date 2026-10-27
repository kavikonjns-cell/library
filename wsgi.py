"""WSGI entry point (PythonAnywhere). The bot runs in webhook mode: set BOT_MODE=webhook in .env.

In the PythonAnywhere WSGI configuration file:

    import sys
    sys.path.insert(0, "/home/<username>/kavikonj")
    from wsgi import application
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from a2wsgi import ASGIMiddleware  # noqa: E402

from app.db import init_db  # noqa: E402
from app.web import create_app  # noqa: E402

init_db()
application = ASGIMiddleware(create_app())
