# Changelog

All notable changes to BFAB Mail Lab are documented here. The project follows Semantic Versioning from v1.0.0 onward.

## [1.2.0] - 2026-07-26

### Added

- Explicit `showcase` and `live` application modes.
- Synthetic, ephemeral showcase mailbox with simulated read, unread, delete, send and reset operations.
- Clear UI and API indicators for simulated data and delivery.
- `/healthz` liveness and `/readyz` readiness probes.
- Payload, UID, recipient, subject, body, password and rate limits.
- CSP nonces and hardened browser response headers.
- Pytest regression suite, Ruff configuration and GitHub Actions CI.
- Canonical release history and safe deployment documentation.

### Changed

- Render now deploys only the isolated showcase and carries no mail or Supabase secrets.
- Production startup fails closed on a weak Flask secret or insecure session cookies.
- Live login verifies credentials before storing them and regenerates the session identifier.
- All writes, including login, require CSRF validation.
- Live message reads use bounded previews and message lists retain batched IMAP fetches.

### Removed

- Duplicate legacy `readme.md`.
- Obsolete Fly.io, Procfile and `runtime.txt` deployment files.
- Unused Flake8 and generated agent-note configuration.
- Supabase from the application runtime path.

## [1.1.0] - 2026-07-22

- Replaced per-message IMAP header requests with a bounded batch fetch.
- Reduced mailbox-list latency and avoided the N+1 fetch pattern.

## [1.0.0] - 2026-07-03

- Introduced the browser-based IMAP/SMTP webmail client.
- Added server-side sessions, CSRF protection and Docker deployment.
- Added optional Docker Mailserver, Redis, Caddy, MTA-STS and bootstrap infrastructure.

## [0.2.0] - 2026-06-20

- Added an early Flask interface and Supabase-backed email demonstration.
- Expanded the original object-oriented prototype into a hosted experiment.

## [0.1.0] - 2026-06-19

- Created the first console-based object-oriented email system prototype.
- Implemented basic account, message and folder concepts with in-memory state.
