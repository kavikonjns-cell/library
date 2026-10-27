# Kavi Konj library — handover notes for Claude

Read this first. The owner writes in Persian; answer in Persian, short and in plain language
(the owner is not a programmer). All user-facing text in the app is Persian.

## What this is
Library management for «کتابخانه کودک و طبیعت کاوی کنج» (Mashhad, pickup/return at «باغ شبنم»).
- Telegram bot: **@Kavikonj_librarybot** (token is NOT in the repo; the owner has it).
- Members register through a form opened inside Telegram (mini-app `/app/register`) or by pasting the
  text of the old HTML form; the bot issues a member code `M-01`, `M-02`, …
- Member sends a photo of the payment receipt; librarians approve it with an inline button.
- Members pick a book from the catalogue (`/`) and a loan length; the bot checks the rules, then
  reserves it (3 days to pick up) or puts the member in the waitlist and says until which date the
  book is out. Returned book → auto-reserved for the next eligible member in the queue.
- The catalogue page shows live shelf status per title («موجود در قفسه» / «در امانت تا ۱۴۰۵/۰۸/۱۹»).
- Admin web panel `/admin` (password from `ADMIN_PASSWORD`): members, loans, books, accounting,
  Excel export, offline HTML catalogue export.
- Daily job: due reminders, overdue fines, revocation at 5 days late, expired reservations.

Library rules are in `app/config.py` (taken from the paper membership form and the library Excel file):
one book at a time; 14 days, max 30 days total with extensions; no extension if someone waits;
fine 50,000 toman/day up to 4 days; membership revoked at 5 days late; membership valid until end
of Esfand (from 1 Dey: end of Esfand next year); fees by role 600k / 800k / 1M toman.

## Code map
- `app/services.py` — all business logic, returns `Result`/`Notice`; no Telegram calls (tested).
- `app/bot.py` — python-telegram-bot handlers, librarian commands, daily job.
- `app/web.py` — FastAPI: catalogue, mini-app API (validates Telegram initData), admin panel.
- `app/main.py` — runs web + bot (long polling) in one process.
- `app/db.py` — SQLite schema; seeds books from `data/seed/books.json` on first run.
- `app/exporter.py` — Excel and self-contained HTML exports.
- `scripts/import_books.py` — re-import books from the library Excel + old HTML list.
- Tests: `pip install -r requirements-dev.txt && python -m pytest` (18 tests, all passing).

## Status (end of first session)
- Code complete and tested locally; bot tested live against Telegram (connects, commands registered).
- Code lives on branch `claude/determined-carson-3c3fay` of `sub7mehrdad-a11y/kavikonj`
  (draft PR #1 into `main`; `main` only has `.gitignore`). The owner now wants the project in
  **`kavikonjns-cell/library`** — push the full history there as `main`.
- NOT deployed yet. There is no server.

## Agreed next step: deploy on PythonAnywhere (free "Beginner" account)
The owner chose PythonAnywhere free over a paid VPS and over a GitHub-Actions-only version
(Actions would answer only every 5–15 min and has no admin panel). Plan:
1. Owner creates the account and gives: username, PythonAnywhere API token, their Telegram numeric
   id (from @userinfobot) for `ADMIN_IDS`, and an admin panel password.
2. Free accounts: WSGI is the supported way (ASGI is experimental) and outbound traffic goes through
   a proxy allowlist. So switch the bot from long polling to **webhook** mode, served by the web app
   (e.g. a Flask/WSGI entry point, or confirm ASGI works), and send Telegram API calls through the
   proxy. Verify first that api.telegram.org is reachable from the free account.
3. Daily job → PythonAnywhere scheduled task (free accounts get one daily task).
4. Auto-deploy: on push to `main`, a GitHub Action tells the server to `git pull` and reload.
   Keep the SQLite file (`data/kavikonj.db`) outside what deploys overwrite.
5. Secrets (bot token, API token, admin password, SECRET_KEY) only in the server `.env` /
   GitHub Actions secrets — never commit them; the repos are public.
6. Free web apps must be renewed every 3 months (a button in the PythonAnywhere Web tab).

## Owner preferences / context
- Owner also runs content bots (repo `heraderm-blog-agent`) on GitHub Actions schedules.
- Optional: put the original `BHoma.ttf` font in `app/static/fonts/` (not committed: licence unknown);
  otherwise the Vazirmatn web font is used.
- Library card / phone numbers in `app/config.py` are public info already printed on the forms.
