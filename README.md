# BFAB Local Mail

BFAB Local Mail is now a **local-only** webmail-style application. It runs on your own computer, stores its data in a local SQLite database, and does not require Render, Supabase, Redis, PostgreSQL, Docker Mailserver, Caddy, IMAP, SMTP, a domain, TLS certificates, or any cloud account.

## What changed in v2.0.0

The previous repository supported a public showcase mode and an optional self-hosted mail-server stack. That architecture has been removed.

The current architecture is deliberately simple:

```text
Browser
  |
  | http://127.0.0.1:8000
  v
Flask application
  |
  v
SQLite
data/email-client.db
```

The app is intentionally bound to localhost in the documented setup.

### Important limitation

This version is **not an Internet mail transport**. Pressing **Send** stores a message in the local `Sent` folder. It does not contact SMTP and does not deliver email to another person.

That is intentional: the project is now fully local and has no external mail dependency.

## Local database: SQLite

Use **SQLite**. It is free, open source/public domain, embedded in Python, and does not require a database server.

You do **not** need to install SQLite separately for the application. Python already includes the `sqlite3` module.

The database is created automatically on first run:

```text
data/email-client.db
```

If you want a graphical database viewer, install **DB Browser for SQLite** (free). On Windows you can install it with `winget install -e --id DBBrowserForSQLite.DBBrowserForSQLite`. Use it only while the application is stopped to avoid editing the database while Flask is writing to it.

The main table is:

```text
messages
├── id
├── folder
├── sender
├── recipient
├── subject
├── body
├── created_at
└── is_read
```

## Recommended setup on Windows: Docker Desktop

Requirements:

- Git
- Docker Desktop with Docker Compose v2

Clone the repository and enter it:

```powershell
git clone https://github.com/oibaf88/email-client.git
cd email-client
```

The repository already contains the `data` directory. Build and start:

```powershell
docker compose up --build
```

On Windows/Docker Desktop, no UID/GID configuration is required. On Linux, if your user is not UID/GID `1000`, export your host IDs before starting so the non-root container can write the bind-mounted SQLite directory:

```bash
export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000
```

Check status:

```powershell
docker compose ps
```

Stop:

```powershell
docker compose down
```

Your database remains on the host at:

```text
data\email-client.db
```

Rebuilding the Docker image does not delete that database.

To reset only the sample mailbox, use **Reset sample data** in the UI.

To delete the entire local database and start fresh:

```powershell
docker compose down
Remove-Item .\data\email-client.db*
docker compose up --build
```

## Run without Docker

Python 3.14 is the repository target.

Create and activate a virtual environment:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Optional configuration:

```powershell
Copy-Item .env.example .env
```

Run:

```powershell
python app.py
```

Open:

```text
http://127.0.0.1:8000
```

The same `data\email-client.db` file is used.

## Optional `.env`

No secrets or configuration are required for the default local setup.

Available variables:

```dotenv
APP_ENV=local
APP_RELEASE=2.0.0
DATABASE_PATH=data/email-client.db
LOCAL_ADDRESS=local@localhost.invalid
FLASK_SECRET_KEY=
```

If `FLASK_SECRET_KEY` is empty, the app generates a random key on startup. That is acceptable for local use; your browser session/CSRF token will simply change after a restart.

Never commit your real `.env` file. `.gitignore` and `.dockerignore` already exclude it.

## Inspect the database with DB Browser for SQLite

1. Stop the app:

```powershell
docker compose down
```

2. Open DB Browser for SQLite.
3. Choose **Open Database**.
4. Open:

```text
<repository>\data\email-client.db
```

5. Inspect the `messages` table.
6. Close/save your changes before restarting the app.

The database is ordinary SQLite, so you can also inspect it from Python:

```powershell
python -c "import sqlite3; db=sqlite3.connect('data/email-client.db'); print(db.execute('select folder,count(*) from messages group by folder').fetchall())"
```

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Process liveness |
| `GET` | `/readyz` | SQLite readiness |
| `GET` | `/api/state` | Local mode and CSRF token |
| `GET` | `/api/folders` | Folder counts |
| `GET` | `/api/messages?folder=INBOX` | Message summaries |
| `GET` | `/api/messages/:uid?folder=INBOX` | Read one message |
| `POST` | `/api/messages/:uid/read` | Mark read/unread |
| `DELETE` | `/api/messages/:uid` | Move to Trash or permanently delete from Trash |
| `POST` | `/api/send` | Store a local message in Sent |
| `POST` | `/api/reset` | Restore sample data |

All write requests require the CSRF token returned by `/api/state`.

## Tests

Install development dependencies:

```powershell
pip install -r requirements-dev.txt
```

Run:

```powershell
ruff check .
python -m compileall -q app.py tests
python -m pytest -q
```

GitHub Actions runs the same checks. CI is the only remote service retained because it tests the repository; it is not part of the application runtime.

## Repository map

```text
app.py                      Flask + SQLite application
templates/email_system.html Local-only web UI
data/.gitkeep               Keeps the local data directory in Git
Dockerfile                  Local application image
compose.yaml                One-container local setup
.env.example                Optional local settings template
DEPLOY.md                   Full local installation/deployment guide
tests/test_app.py            Local SQLite/API regression tests
.github/workflows/ci.yml    Repository CI only
```

## Security boundary

The supported runtime is localhost. `compose.yaml` publishes the application only to `127.0.0.1:8000`.

Do not expose this development-oriented local application directly to the public Internet. It has no account system because local-only execution is the trust boundary.

See `SECURITY.md` and `DEPLOY.md` for details.
