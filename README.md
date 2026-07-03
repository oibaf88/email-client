# Domain Mail

Flask webmail client for a self-hosted domain mail server. The app signs in to
real mailboxes over IMAP, sends mail through authenticated SMTP submission, and
uses server-side sessions so mailbox passwords are not stored in browser cookies.

This replaces the original two-user email simulator. It no longer uses Supabase
as a mailbox database.

## What this service does

- Logs in with a real mailbox, such as `admin@example.com`.
- Lists IMAP folders.
- Lists recent messages in a folder.
- Opens messages and marks them as read.
- Deletes messages through IMAP.
- Sends messages through SMTP submission.
- Requires STARTTLS before SMTP authentication on port `587`.
- Saves a sent copy to the IMAP Sent folder when available.

## What this service does not do

- It does not run the domain mail server itself.
- It does not replace Postfix, Dovecot, DKIM signing, antispam, or MX handling.
- It does not provide end-to-end encryption. It uses transparent TLS/ECDHE for
  transport encryption, so recipients read mail normally.

For the mail server infrastructure, see
[`docs/mail-server-setup.md`](docs/mail-server-setup.md).

## Required environment variables

```env
MAIL_DOMAIN=example.com
IMAP_HOST=mail.example.com
IMAP_PORT=993
SMTP_HOST=mail.example.com
SMTP_SUBMISSION_PORT=587
FLASK_SECRET_KEY=change-me-to-a-long-random-secret
SESSION_REDIS_URL=redis://localhost:6379/0
SESSION_TTL_SECONDS=28800
SEND_RATE_LIMIT_PER_HOUR=60
MAX_MESSAGE_LIST_SIZE=50
SESSION_COOKIE_SECURE=true
```

`SESSION_REDIS_URL` is strongly recommended in production. If it is missing, the
app falls back to filesystem-backed sessions for local development.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask --app app run --debug
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
flask --app app run --debug
```

## Production notes

You may host this Flask web UI on any HTTPS-capable web host that can reach your
mail server on `993` and `587`. The actual mail server should run on a VPS with
port `25`, reverse DNS, SPF, DKIM, DMARC, MTA-STS, TLS-RPT, and antispam.

Render can serve the Flask web app, but it should not be used as the MX/mail
server for the domain.

Start command:

```bash
gunicorn app:app
```

Health check path:

```text
/healthz
```

## API

- `POST /api/login`
- `POST /api/logout`
- `GET /api/folders`
- `GET /api/messages?folder=INBOX`
- `GET /api/messages/<uid>?folder=INBOX`
- `POST /api/messages/<uid>/read?folder=INBOX`
- `DELETE /api/messages/<uid>?folder=INBOX`
- `POST /api/send`

All write endpoints except login require the `X-CSRF-Token` returned by
`GET /api/state`.

## Python version

This repo pins Python with `runtime.txt`:

```text
python-3.12.7
```
