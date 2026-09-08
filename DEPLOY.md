# Local deployment guide

This repository supports **one deployment target: your own computer**.

There is no Render deployment, VPS mail appliance, Supabase project, Redis server, Caddy reverse proxy, Docker Mailserver, IMAP service, SMTP service, DNS setup, or TLS certificate workflow.

## 1. Recommended software

### Required

Install:

1. **Git**
2. **Docker Desktop**

Docker Desktop includes Docker Engine and Docker Compose v2.

### Optional database GUI

Install **DB Browser for SQLite** if you want to inspect the local database visually. On Windows:

```powershell
winget install -e --id DBBrowserForSQLite.DBBrowserForSQLite
```

It is not required by the app. The app uses Python's built-in `sqlite3` library.

## 2. Get the project

```powershell
git clone https://github.com/oibaf88/email-client.git
cd email-client
```

Confirm Docker:

```powershell
docker --version
docker compose version
```

## 3. Start the application

```powershell
docker compose up --build
```

The first build installs the Python dependencies and creates the container.

Open:

```text
http://127.0.0.1:8000
```

The Compose port mapping is intentionally:

```yaml
127.0.0.1:8000:8000
```

This means the service is reachable from your own computer, not from other devices on your LAN by default.

## 4. Database creation

You do not create the database manually.

On the first request, Flask creates:

```text
data/email-client.db
```

and initializes the `messages` schema plus sample rows.

Docker mounts:

```text
./data -> /app/data
```

so the SQLite file lives on your Windows filesystem rather than disappearing with the container.

## 5. Verify the installation

In a second PowerShell window:

```powershell
docker compose ps
```

The web container should become `healthy`.

You can also test:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/readyz
```

Expected readiness includes:

```text
status   : ready
mode     : local
storage  : sqlite
database : email-client.db
```

## 6. Normal start and stop

Start:

```powershell
docker compose up -d
```

View logs:

```powershell
docker compose logs -f web
```

Stop:

```powershell
docker compose down
```

Rebuild after changing Python code or dependencies:

```powershell
docker compose up --build -d
```

Full clean image rebuild:

```powershell
docker compose build --no-cache
docker compose up -d
```

## 7. Database maintenance

### Backup

Stop the app first:

```powershell
docker compose down
```

Then copy the database:

```powershell
Copy-Item .\data\email-client.db .\data\email-client.backup.db
```

### Restore

```powershell
docker compose down
Copy-Item .\data\email-client.backup.db .\data\email-client.db -Force
docker compose up -d
```

### Start from an empty database

```powershell
docker compose down
Remove-Item .\data\email-client.db*
docker compose up -d
```

The schema and sample messages are recreated automatically.

### Inspect with DB Browser for SQLite

Stop the container, open `data\email-client.db`, inspect the `messages` table, close the file, then restart Docker.

SQLite uses WAL mode, so while the app is running you may also see:

```text
email-client.db-wal
email-client.db-shm
```

These are normal SQLite files. Do not commit them.

## 8. Optional local configuration

Copy:

```powershell
Copy-Item .env.example .env
```

The default values are enough for normal use.

If you run `python app.py` directly, `.env` is loaded automatically.

Docker Compose intentionally supplies its essential local values itself. If you want Docker to use custom values, edit `compose.yaml` or add Compose variable interpolation deliberately.

## 9. Native Python deployment

Docker is recommended, but not required.

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:8000`.

To stop, press `Ctrl+C`.

## 10. What was intentionally removed

The local-only conversion removes runtime infrastructure that no longer has a purpose:

- `render.yaml`
- `compose.prod.yaml`
- `infra/`
- the VPS/mail-server setup documentation
- Redis runtime dependency
- Flask-Session runtime dependency
- Docker Mailserver
- Caddy
- Let's Encrypt/certbot scripts
- DNS/MTA-STS configuration
- IMAP/SMTP login and network mail transport

The repository retains GitHub Actions only for automated tests. GitHub Actions is not involved when the app runs on your PC.

## 11. Troubleshooting

### Port 8000 is already in use

Check:

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
```

Stop the conflicting process or change the host side of the mapping in `compose.yaml`, for example:

```yaml
ports:
  - "127.0.0.1:8080:8000"
```

Then open `http://127.0.0.1:8080`.

### Docker cannot write `data`

Because `data/.gitkeep` creates the directory when the repository is cloned, this should normally work.

If the directory was deleted, recreate it:

```powershell
New-Item -ItemType Directory -Force .\data
```

### Database is locked

Stop the app and any SQLite GUI:

```powershell
docker compose down
```

Close DB Browser for SQLite, then start again:

```powershell
docker compose up -d
```

### Reset does not mean delete the database

The UI reset repopulates sample data in the existing SQLite file. To completely recreate the file, follow the "Start from an empty database" commands above.
