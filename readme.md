# Python Email System

Interactive Flask + Supabase version of a Python object-oriented email simulation.

## Features

- Create two demo users.
- Send emails between users.
- View inboxes.
- Read emails.
- Delete emails.
- Persist data in Supabase PostgreSQL.
- Deployable on Render.

## Tech Stack

- Python
- Flask
- Gunicorn
- Supabase PostgreSQL
- HTML/CSS/JavaScript

## Required Environment Variables

Set these variables in Render:

```env
SUPABASE_URL=https://zzgavefdyzbukbrowzot.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
FLASK_SECRET_KEY=your_long_random_secret
