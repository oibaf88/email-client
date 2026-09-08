# BFAB Local Mail

BFAB Local Mail is a **localhost-only email client**. The application server and its configuration database run on your own computer; it connects outward only to the IMAP and SMTP servers that you configure in order to read and send real mail.

It does **not** require Render, Supabase, Redis, PostgreSQL, a VPS, Docker Mailserver, Caddy, a public domain, inbound ports, or TLS certificates for the local web UI.

## Architecture

```text
Browser
  |
  | http://127.0.0.1:8000
  v
Local Flask application
  |             |
  |             +--> SQLite: data/email-client.db
  |                  (IMAP/SMTP settings only; NO mailbox password)
  |
  +--> IMAP over TLS --------> your existing mail provider
  +--> SMTP TLS/STARTTLS ----> your existing mail provider
```

The browser UI is local. Your actual mailbox remains on your mail provider.

## Local database: SQLite

Use **SQLite**. No database server needs to be installed. Python already includes SQLite support and the app creates the database automatically:

```text
data/email-client.db
```

SQLite stores only non-secret mail configuration such as:

- IMAP hostname and port;
- SMTP hostname and port;
- optional mail domain;
- fallback Sent-folder name.

**Your mailbox password is not stored in SQLite.** After login it lives only in a local server-side session and is cleared on sign-out. Restarting with the default random Flask secret also invalidates the old session.

For a free graphical database viewer, use **DB Browser for SQLite**. On Windows:

```powershell
winget install -e --id DBBrowserForSQLite.DBBrowserForSQLite
```

Stop the app before manually editing the database.

## Windows setup with Docker Desktop

Requirements:

- Git
- Docker Desktop (includes Docker Compose v2)

Clone or update the repository:

```powershell
git clone https://github.com/oibaf88/email-client.git
cd email-client
```

If you already cloned it:

```powershell
git pull
```

Build and start:

```powershell
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000
```

On first launch the app opens the mail configuration dialog. Enter the IMAP/SMTP settings supplied by your email provider. Then sign in with your mailbox address and password/app-password.

No UID/GID configuration is needed on Windows/Docker Desktop.

### Linux

The container runs without root privileges. If your host UID/GID is not `1000`:

```bash
export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
docker compose up --build
```

## Normal Docker commands

```powershell
# Start in background
docker compose up -d

# Status
docker compose ps

# Logs
docker compose logs -f web

# Stop
docker compose down

# Rebuild after code/dependency changes
docker compose up -d --build
```

The database persists on your PC at `data\email-client.db` when the container is removed or rebuilt.

## Configure the mailbox

The local UI asks for:

```text
Mail domain     optional, e.g. example.com
IMAP host       provider IMAP hostname
IMAP port       normally 993
SMTP host       provider SMTP hostname
SMTP port       normally 587 or 465
Sent folder     fallback name, normally Sent
```

The app requires:

- IMAP over TLS;
- SMTP implicit TLS on port 465, or STARTTLS on other configured SMTP ports.

Some providers no longer accept a normal account password for IMAP/SMTP. In that case use the provider's supported app-password mechanism if available. OAuth-only providers are **not yet supported** by this version.

Do not guess server settings: use the values documented by your mail provider.

## Run without Docker

Python 3.14 is the repository target:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:8000`.

## Optional `.env`

The default setup needs no `.env`. If you want to customize runtime values:

```powershell
Copy-Item .env.example .env
```

The IMAP/SMTP settings themselves are saved from the UI into local SQLite. The mailbox password is deliberately excluded from both `.env` and SQLite.

## Back up the local configuration

Stop the app and copy the database:

```powershell
docker compose down
Copy-Item .\data\email-client.db .\data\email-client.backup.db
```

Restore:

```powershell
docker compose down
Copy-Item .\data\email-client.backup.db .\data\email-client.db -Force
docker compose up -d
```

Delete the local configuration completely:

```powershell
docker compose down
Remove-Item .\data\email-client.db*
docker compose up -d
```

The empty SQLite schema is recreated automatically.

## Security boundary

The web UI is published only on:

```text
127.0.0.1:8000
```

It is not intended to be exposed to your LAN or the public Internet. Mail traffic itself necessarily leaves your computer to reach the configured IMAP/SMTP provider and uses TLS.

Mailbox credentials are kept in a server-side filesystem session under the local runtime's temporary directory, not in the browser cookie and not in SQLite.

## Tests

```powershell
pip install -r requirements-dev.txt
ruff check .
python -m compileall -q app.py tests
python -m pytest -q
```

GitHub Actions also validates `compose.yaml`, builds the Docker image, starts it, and checks `/readyz`. No real mail server is contacted by tests.

## Repository map

```text
app.py                       Flask, SQLite config, IMAP/SMTP client
Dockerfile                   Local Docker image
compose.yaml                 One-container localhost runtime
data/.gitkeep                Persistent SQLite directory
templates/email_system.html  Local web interface
README.md                    Usage overview
DEPLOY.md                    Detailed PC setup and operations
SECURITY.md                  Local threat boundary
tests/test_app.py            Offline regression tests
```
