# Changelog

## 2.0.0 — Local SQLite

- Converted the project to a local-only runtime.
- Replaced session-only synthetic mailbox state with persistent SQLite storage.
- Added automatic local database schema creation and sample seeding.
- Removed IMAP and SMTP network mail code.
- Removed Redis and Flask-Session runtime dependencies.
- Removed Render deployment configuration.
- Removed the VPS Docker Mailserver/Caddy/certbot/DNS infrastructure.
- Removed the production Compose stack.
- Simplified Docker Compose to one localhost-bound Flask container.
- Added host-persistent `data/email-client.db` storage.
- Added a local-only web interface and local message composition.
- Added `DEPLOY.md` with Windows/Docker/SQLite setup and maintenance.
- Rewrote tests for the local SQLite architecture.

## 1.2.0 — Safe Showcase

- Added explicit showcase/live boundaries.
- Added synthetic showcase mailbox behavior.
- Added security headers, CSRF protection, validation limits, health checks, and CI.
- Added optional private live mail infrastructure.

## 1.0.0

- Initial project.
