# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A Django 5.2 app that recreates the College Board "Bluebook" digital SAT testing app UI in the browser (deployed at `bbtest.shop`). One project (`bluebook_project`) with a single app (`ui`). No frontend build step: all UI is Django templates with inline CSS/JS plus `ui/static/`.

## Commands

```bash
pip install -r requirements.txt
cp .env.example .env              # SECRET_KEY, DEBUG, ALLOWED_HOSTS, RESEND_API_KEY (DESMOS_API_KEY is also read)
python manage.py migrate
python manage.py runserver        # dev server
python manage.py createsuperuser  # needed for /question-editor/ and /admin/
python manage.py test ui          # run tests
python manage.py test ui.tests.SomeTestCase.test_method   # single test
python manage.py deactivate_users # sets is_active=False on every non-staff user
python manage.py collectstatic    # required in production (nginx serves staticfiles/)
gunicorn bluebook_project.wsgi -c gunicorn.conf.py   # production (binds 127.0.0.1:8000, 9 workers)
```

There is no linter or formatter configured.

`deploy/` holds server config that is installed by hand, not applied by deploys: an nginx per-IP rate limit for sign-in POSTs (student and admin), nginx compression and cache headers for `/static/`, and a daily `clearsessions` cron script. See `deploy/README.md`. Production runs behind nginx, which serves `/static/` itself.

## Architecture

**User flow (URL order):** `/` (fake login landing) → `/cb_login/` (real auth via email-as-username) → `/dashboard/` → `/setup/`, `/setup_step2/` … `/setup_step8/` → `/start-code/` → `/test/` → `/congrats/`. Setup step routes are generated in a loop in `ui/urls.py` as `TemplateView`s and have no view logic.

**Users** are created by an admin (no signup). `UserProfile` (one-to-one with `auth.User`) holds `full_name`, `test_center_address`, `contact_email`, `start_code`, `test_completed`. Logging in through `cb_login_view` resets `test_completed` to False. Failed sign-ins are recorded in `LoginFailure`, and an account is locked for 15 minutes after 10 failures. The lockout lives in the authentication backend (`ui/auth_backends.py`, set in `AUTHENTICATION_BACKENDS`), so it covers both `/cb_login/` and the Django admin login. The limit is per account rather than per IP, because students at a test center share one IP. Sign-out is a POST to `/logout/`. Passwords use PBKDF2 at 200,000 iterations (`ui/hashers.py`) instead of Django's 1,000,000, because sign-in is CPU-bound; it replaces Django's PBKDF2 hasher in `PASSWORD_HASHERS` (same algorithm name, so old hashes still verify and are re-hashed on next sign-in). The profile can be missing, so views fetch it with `_get_profile()` and check for None.

**Questions** are stored in `ui/data/questions.json`, not in the database. Each entry has `id`, `type` (`"rw"` or `"math"`), `question`, an optional `passage`, and optional `options` (math questions without `options` are student-produced response). There is no answer key. Math text uses `$...$` delimiters and is rendered client-side with MathQuill.
- `get_cached_modules_json()` in `ui/views.py` splits the questions by type into the 4 modules defined in `MODULES` (RW 27+27, Math 22+22). It caches the result keyed on the file's mtime, so every gunicorn worker picks up edits. Because the file is sliced by position, order within each type matters. A short bank only logs a warning and produces short modules. If the file can't be read, `/test/` returns a 503.
- `/question-editor/` (superuser only) is an in-browser editor that GETs and POSTs the entire JSON array to `/api/questions/`. A POST is checked by `validate_questions()` (structural errors give a 400 and the file is not touched; short counts only return `warnings`), then written atomically with a temp file and `os.replace`.
- `scripts/` holds one-off JSON migration scripts. Run them from the repo root, since they use the relative path `ui/data/questions.json`.

**Test interface** (`ui/templates/test_interface.html`, ~3300 lines) is a single-page app in one template. The server injects `modules_json` (parsed with `escapejs`) and `desmos_api_key`. All test state lives in a global JS `app` object plus `moduleState`. Nothing about answers is sent to the server; the only server call is `POST /api/mark-test-completed/`, made by `finishTest()` at the end. If the device is offline, `finishTest()` shows a "Reconnect to Finish" message and retries until it gets through, then goes to `/congrats/`. The Desmos calculator (`ui/static/calculator.js`) is lazy-loaded on first use. MathQuill's stylesheet and Symbola font are served from `ui/static/vendor/mathquill/` (cdnjs fonts are blocked by the CSP); `mathquill.js` and jQuery still come from CDNs.

**Templates:** everything extends `base.html`, which depends on these context values:
- `debug_mode` (from the `ui.context_processors.debug_mode_context` context processor) gates the anti-inspection script, which blocks right-click and devtools. That script only runs when `DEBUG=False`.
- `static_files_preloader` adds `<link rel="prefetch">` for a hand-maintained list of UI images (`_PREFETCH_IMAGES` in `ui/context_processors.py`).

**Email:** `start_code_view` accepts only 6-digit codes, caps each user at `START_CODE_EMAILS_PER_HOUR` (50) emails, kept high because users test the flow repeatedly, and calls `enqueue_email()` in `ui/email_queue.py`, which stores a `QueuedEmail` row. Sender threads run in every server process: `start_workers()` is called from `bluebook_project/wsgi.py`, so they don't run in management commands or tests. They claim rows from the shared table with a conditional UPDATE and send through django-anymail's Resend backend. Emails a user triggers are sent from `user_sender(user)` (e.g. `Bluebook <messages+u42-ana-lee@bbtest.shop>`, so Resend's list shows the student while inboxes show "Bluebook") and carry Resend tags `user_id` and `username`. Failed sends are retried with backoff and marked `failed` after 5 attempts; the `QueuedEmail` admin shows their status. Resend's 20 req/s limit applies per account, so a 19 req/s budget is split across `EMAIL_SENDER_PROCESSES` processes, which must match `workers` in `gunicorn.conf.py`. Don't enable gunicorn `preload_app`, or the threads would start before fork and die in the workers. In tests, call `email_queue.process_one()` directly.

**Database:** SQLite in WAL mode with `transaction_mode: IMMEDIATE` and a 20 s busy timeout (see `DATABASES` in settings), so concurrent writes from the gunicorn workers wait instead of failing. The login lockout relies on IMMEDIATE to make its check-and-record atomic (`_reserve_login_attempt` in `ui/auth_backends.py`); on another database it would need `select_for_update` or similar.

**Production-only behavior** (when `DEBUG=False`): `SECRET_KEY` is required, SSL redirect and secure cookies are on, and static files use whitenoise's `CompressedManifestStaticFilesStorage` (configured through `STORAGES`). Reference static files with `{% static %}` so they get hashed, long-cached names, and run `collectstatic` on every deploy. Also, `bluebook_project/middleware.py` adds a strict CSP. Any new external script, style, font or frame origin must be added to `CSP_POLICY` there, or it will work in dev and break in production.
