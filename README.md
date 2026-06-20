# Email Client

Interactive Python Email System demo built with Flask, Render and Supabase PostgreSQL.

## What this service does

- Serves the email client web UI.
- Creates two demo users per browser session.
- Sends emails between both users.
- Lists inboxes.
- Marks emails as read.
- Deletes emails.
- Persists data in Supabase using the `email_demo_sessions` and `email_demo_emails` tables.

## Render configuration

Build Command:

```bash
pip install -r requirements.txt
```

Start Command:

```bash
gunicorn app:app
```

Health Check Path:

```text
/healthz
```

## Required Render environment variables

Set these in the Render dashboard. Do not commit real secrets to GitHub.

```env
SUPABASE_URL=https://zzgavefdyzbukbrowzot.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
FLASK_SECRET_KEY=your_long_random_secret
```

## Python version

This repo pins Python with `runtime.txt`:

```text
python-3.12.7
```

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app app run --debug
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app app run --debug
```
