# Local deployment guide

This repository has one deployment target: **the user's own computer**. The application is not deployed to Render or a VPS.

The local Flask process may connect to the IMAP/SMTP servers you explicitly configure because that is how it reads and sends real email. No public web server, inbound mail server, cloud database, Redis instance, Caddy proxy, or certificate service is part of the runtime.

## 1. Software to install on Windows

Required:

1. **Git**
2. **Docker Desktop**

Optional:

3. **DB Browser for SQLite** — free GUI for inspecting `data\email-client.db`.

Install the optional SQLite GUI with:

```powershell
winget install -e --id DBBrowserForSQLite.DBBrowserForSQLite
```

You do not need SQLite Server, MySQL, PostgreSQL, SQL Server, Supabase, or another database service. Python's `sqlite3` library creates and manages the database file.

## 2. Get/update the repository

New clone:

```powershell
git clone https://github.com/oibaf88/email-client.git
cd email-client
```

Existing clone:

```powershell
cd email-client
git pull
```

Verify Docker:

```powershell
docker --version
docker compose version
```

## 3. Start the local runtime

```powershell
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000
```

Docker publishes the web application on loopback only. Other computers on the network cannot reach it through the normal configuration.

On Linux, if needed:

```bash
export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
docker compose up --build
```

## 4. Create the local database

You do not create it manually.

The first request creates:

```text
data/email-client.db
```

with a `mail_config` table. The table contains only non-secret server settings. It does not have a password column.

The Docker bind mount is:

```text
./data -> /app/data
```

so deleting/rebuilding the container does not delete your configuration.

## 5. Configure real mail

On first launch choose **Configure** and enter the settings from your provider:

```text
mail domain      optional
IMAP host        required
IMAP port        required, commonly 993
SMTP host        required
SMTP port        required, commonly 587 or 465
Sent folder      optional fallback, defaults to Sent
```

Then choose **Sign in** and enter:

- the full mailbox email address;
- the password or app-password accepted by that provider for IMAP/SMTP.

The app tests the credential against IMAP before establishing the local session.

### Credential storage

The password is **not** written to:

- SQLite;
- `.env`;
- frontend JavaScript/localStorage;
- Git.

It is kept temporarily in a Flask server-side filesystem session. Signing out clears it. With the default random `FLASK_SECRET_KEY`, restarting the app invalidates the previous browser session as well.

## 6. TLS behavior

IMAP connects with TLS and certificate validation, minimum TLS 1.2.

SMTP behavior:

- port `465`: implicit TLS;
- any other configured SMTP port: the server must advertise STARTTLS before authentication.

The app will not silently send credentials over plaintext SMTP.

## 7. Verify local operation

```powershell
docker compose ps
```

The container should become `healthy`.

Health endpoints:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/readyz
```

`/readyz` verifies the local SQLite runtime. `mail_configured` tells you whether IMAP/SMTP settings have been saved; readiness does not contact the remote mail server.

## 8. Start, stop and rebuild

```powershell
# start
docker compose up -d

# logs
docker compose logs -f web

# stop
docker compose down

# rebuild
docker compose up -d --build

# complete image rebuild
docker compose build --no-cache
docker compose up -d
```

## 9. SQLite backup and inspection

Stop the app before manually changing the database:

```powershell
docker compose down
```

Backup:

```powershell
Copy-Item .\data\email-client.db .\data\email-client.backup.db
```

Open `data\email-client.db` in DB Browser for SQLite and inspect `mail_config`.

Restore:

```powershell
Copy-Item .\data\email-client.backup.db .\data\email-client.db -Force
docker compose up -d
```

Reset all saved local mail settings:

```powershell
docker compose down
Remove-Item .\data\email-client.db*
docker compose up -d
```

SQLite may create `-wal` and `-shm` sidecar files while running. They are normal and ignored by Git.

## 10. Native Python alternative

Docker is optional:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

The same SQLite path `data\email-client.db` is used by default.

## 11. What local-only means here

Local-only means:

- Flask runs only on your PC;
- SQLite lives only on your PC;
- server settings live only in SQLite;
- session credentials live only in the local Flask session;
- no Render/VPS/cloud database is required;
- no mail server is hosted by this repository.

It does **not** mean offline email. Reading and sending real mail necessarily connects to the configured IMAP/SMTP provider.

## 12. Provider compatibility

This implementation uses password/app-password authentication for IMAP and SMTP.

If your provider requires OAuth 2.0 and has disabled password/app-password access, login will fail until OAuth support is implemented. Use only authentication methods permitted by your provider; do not weaken account security settings just to make the client work.

## 13. Troubleshooting

### Port 8000 is occupied

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
```

Change the host side of the Compose mapping if needed, for example `127.0.0.1:8080:8000`.

### IMAP login fails

Check the exact provider documentation for:

- IMAP hostname/port;
- whether IMAP access is enabled;
- whether an app-password is required;
- whether password authentication has been disabled in favor of OAuth.

### SMTP login/send fails

Check the SMTP hostname, submission port and authentication policy. Port 587 must advertise STARTTLS; port 465 uses implicit TLS.

### Database is locked

Stop both Docker and DB Browser for SQLite, then restart:

```powershell
docker compose down
docker compose up -d
```
