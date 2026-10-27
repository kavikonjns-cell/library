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

## Readers' club + member website (added later)
- «کلوپ کتاب‌خوان‌های کاوی کنج» (`CLUB_NAME`, may be renamed): optional. Club members log daily reading
  minutes of their borrowed book (bot buttons or website), get a streak (consecutive days; one «مرخصی» leave
  day per 7 days keeps it), badges, a public reader page and a leaderboard; after a return they say if they
  finished the book (+ rating). Non-club members just borrow and return. Ideas come from the owner's earlier
  reading app «باروباه/کتاب‌بازی» (study log, streaks, best users).
- `CLUB_ONLY_LIBRARY_BOOKS=1`: only borrowed library books can be logged (owner may change this later).
- Membership is created only in the Telegram bot. Website sign-in = Telegram: `/login` shows a one-time code
  opened in the bot (`/start login_<nonce>`), or the bot's «🌐 ورود به سایت» sends a one-time link; inside the
  mini-app sign-in is automatic (initData). `/me` = full member area (loan, extend, waitlist, receipt upload,
  club log, feedback, suggestions, inbox). Every member Notice is also stored in the website inbox.
- Evening reminder: `/cron/evening?key=<WEBHOOK_SECRET>` (Supabase pg_cron job `kavikonj-evening`, 16:30 UTC).

## Code map
- `app/services.py` — all business logic, returns `Result`/`Notice`; no Telegram calls (tested).
- `app/club.py` — readers' club logic; `app/auth.py` — website sign-in codes; `app/member_web.py` — member pages.
- `app/bot.py` — python-telegram-bot handlers, librarian commands, daily job.
- `app/web.py` — FastAPI: catalogue, mini-app API (validates Telegram initData), admin panel.
- `app/main.py` — runs web + bot (long polling) in one process.
- `app/db.py` — SQLite schema; seeds books from `data/seed/books.json` on first run.
- `app/exporter.py` — Excel and self-contained HTML exports.
- `scripts/import_books.py` — re-import books from the library Excel + old HTML list.
- Tests: `pip install -r requirements-dev.txt && python -m pytest` (all passing; also run with TEST_DATABASE_URL against Postgres).

## Status / deployment (live)
- Repo: `kavikonjns-cell/library` (main). Everything belongs to the library's account (kavikonjns@gmail.com).
- Web + bot: Render free web service `kavikonj` (Frankfurt) → https://kavikonj.onrender.com,
  auto-deploys on push to `main`. Start: `uvicorn app.main:app`, `BOT_MODE=webhook`; the app registers the
  Telegram webhook (`/tg/<WEBHOOK_SECRET>`) on startup. Free instances sleep after 15 min idle (first reply slow).
- Data: Supabase Postgres (project ref `shahujaburrzvweeuuqc`, Frankfurt) via `DATABASE_URL` (transaction
  pooler, port 6543).
- Daily check: Supabase pg_cron job `kavikonj-daily` at 05:30 UTC (09:00 Tehran) calls
  `/cron/daily?key=<WEBHOOK_SECRET>` (also keeps the free Supabase project active).
- Secrets (bot token, DATABASE_URL, ADMIN_PASSWORD, SECRET_KEY, WEBHOOK_SECRET) live only in Render env vars.
- Librarian Telegram id (ADMIN_IDS): 6288109972. Admin panel: /admin.
- An earlier PythonAnywhere deployment (`kavikonj.pythonanywhere.com`) is disabled: free accounts cannot
  reach api.telegram.org. `wsgi.py` and `TG_PROXY` remain for WSGI hosts.
- Tests: `python -m pytest` (SQLite) and `TEST_DATABASE_URL=postgresql://... python -m pytest` (Postgres).

## Owner preferences / context
- Owner also runs content bots (repo `heraderm-blog-agent`) on GitHub Actions schedules.
- Optional: put the original `BHoma.ttf` font in `app/static/fonts/` (not committed: licence unknown);
  otherwise the Vazirmatn web font is used.
- Library card / phone numbers in `app/config.py` are public info already printed on the forms.
