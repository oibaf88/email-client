# BFAB Mail Lab

A privacy-first webmail interface with two deliberately separate runtime modes:

- **Showcase** is a public, synthetic product demo. It never accepts a mailbox password and never opens IMAP, SMTP or Supabase connections.
- **Live** is an opt-in, self-hosted IMAP/SMTP client for infrastructure you control.

The repository also contains an optional Docker Mailserver appliance for a private VPS. The Render blueprint deploys **only the safe showcase**.

## Current release

**v1.2.0 — Safe Showcase**

- Explicit `APP_MODE=showcase|live` boundary.
- Automatic synthetic session in showcase mode.
- Simulated read, unread, delete, compose, send and reset flows.
- Password login disabled before request payload parsing in showcase mode.
- Production secret and secure-cookie fail-closed checks.
- Session identifier rotation after live login and logout.
- CSRF protection on every write, including login.
- Request, recipient, subject, body, password and UID limits.
- CSP nonce, HSTS, anti-framing and other browser security headers.
- Separate `/healthz` liveness and `/readyz` readiness probes.
- Pytest and Ruff checks in GitHub Actions.

See [CHANGELOG.md](CHANGELOG.md) for the project history.

## Architecture

```text
Browser
  |
  +-- APP_MODE=showcase
  |     +-- Flask server-side session
  |     +-- synthetic messages only
  |     +-- no external mail or database calls
  |
  +-- APP_MODE=live
        +-- Flask + private Redis session
        +-- IMAP over TLS
        +-- SMTP submission with STARTTLS or TLS
        +-- optional Docker Mailserver VPS stack
```

Supabase is not used by v1.2.0. Historical database experiments are intentionally outside the runtime path.

## Safe showcase

### Local run

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:APP_MODE = "showcase"
$env:APP_ENV = "development"
flask --app app run --debug
```

Open `http://127.0.0.1:5000`.

Showcase state is scoped to the server-side session and expires with it. All addresses use reserved `.invalid` domains. A reset endpoint restores the original synthetic dataset.

### Render

`render.yaml` defines one showcase web service with:

- one Gunicorn worker so filesystem-backed sessions remain consistent;
- a generated Flask secret;
- secure cookies;
- no IMAP, SMTP or Supabase credentials;
- `/readyz` as the health check.

The free Render deployment is a portfolio demonstration, not a mail relay. Keep real mail on infrastructure where outbound mail ports and persistent private session storage are supported.

## Live mode

Live mode is intended for a private deployment behind HTTPS.

1. Copy `.env.example` to `.env`.
2. Set `APP_MODE=live` and use a long random `FLASK_SECRET_KEY`.
3. Configure a private Redis instance.
4. Configure the IMAP and SMTP submission hosts you control.
5. Set `SESSION_COOKIE_SECURE=true` before using `APP_ENV=production`.
6. Run one of the supported deployments below.

Minimal production application variables:

```dotenv
APP_MODE=live
APP_ENV=production
FLASK_SECRET_KEY=replace-with-at-least-32-random-characters
SESSION_COOKIE_SECURE=true
SESSION_REDIS_URL=redis://redis:6379/0
IMAP_HOST=mail.example.com
IMAP_PORT=993
SMTP_HOST=mail.example.com
SMTP_SUBMISSION_PORT=587
MAIL_DOMAIN=example.com
```

Production startup fails if the Flask secret is weak or secure cookies are disabled. Readiness fails if live mail configuration or production Redis is missing.

### Docker application

```bash
docker compose up -d --build
curl --fail https://webmail.example.com/healthz
curl --fail https://webmail.example.com/readyz
```

`compose.yaml` explicitly selects live production mode and uses Redis-backed sessions.

### Full mail appliance

The optional `infra/` stack combines:

- Docker Mailserver for SMTP submission and IMAP;
- Redis for private server-side sessions;
- Caddy for the HTTPS webmail and MTA-STS endpoints;
- Certbot bootstrap and renewal scripts;
- DNS verification and mailbox bootstrap helpers.

Follow [docs/mail-server-setup.md](docs/mail-server-setup.md). Treat that stack as production infrastructure: review DNS, backups, abuse controls, certificate renewal and upgrades before exposing it.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Process liveness; no dependency check |
| `GET` | `/readyz` | Configuration readiness |
| `GET` | `/api/state` | Mode, authentication and CSRF state |
| `POST` | `/api/login` | Live-mode login only |
| `POST` | `/api/logout` | Rotate and clear the live session |
| `POST` | `/api/demo/reset` | Reset synthetic showcase data |
| `GET` | `/api/folders` | List folders |
| `GET` | `/api/messages?folder=INBOX` | List bounded message summaries |
| `GET` | `/api/messages/:uid?folder=INBOX` | Read one bounded message preview |
| `POST` | `/api/messages/:uid/read` | Set read or unread state |
| `DELETE` | `/api/messages/:uid` | Delete or move a message |
| `POST` | `/api/send` | Simulate or submit a validated message |

All write requests require the `X-CSRF-Token` returned by `/api/state`.

## Security model

Showcase mode is the only mode designed for a public portfolio deployment. Its UI and API explicitly label operations as simulated.

Live mode stores the IMAP password in a private server-side session for the duration of the signed-in session. That is a deliberate compatibility trade-off, not a zero-knowledge design. Use private Redis, TLS, short TTLs, access controls and a dedicated mailbox password where possible.

Never put real credentials into:

- a public Render showcase;
- Git history;
- client-side JavaScript;
- issue or pull-request text;
- Supabase tables used by anonymous roles.

See [SECURITY.md](SECURITY.md) for supported versions, disclosure and residual risks.

## Tests and quality

```powershell
pip install -r requirements-dev.txt
ruff check .
python -m compileall app.py tests
pytest -q
```

The test suite verifies the isolated showcase, simulated mail operations, CSRF, validation limits, health probes, browser headers and live session rotation without contacting a mail server.

## Repository map

```text
app.py                         Flask application and mode boundary
templates/email_system.html    Responsive webmail UI
render.yaml                    Public showcase deployment only
compose.yaml                   Private live mail appliance
infra/                         Mailserver, Caddy, DNS and bootstrap assets
tests/                         API and security regression tests
.github/workflows/ci.yml       Ruff, compile and pytest checks
```

## License and use

This is a portfolio and self-hosting project. Do not use synthetic demo behavior or UI language to imply clinical validation, regulatory approval, guaranteed delivery or suitability for patient communications.
