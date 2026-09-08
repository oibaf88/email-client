# Changelog

## 2.1.0 — Local runtime + real mail

- Clarified local-only as a deployment/storage boundary rather than offline/demo behavior.
- Restored real IMAP-over-TLS mailbox reading.
- Restored SMTP delivery using TLS/STARTTLS.
- Added local SQLite storage for non-secret IMAP/SMTP configuration.
- Added a local configuration UI and sign-in flow.
- Mailbox passwords are kept only in server-side local sessions and are never saved to SQLite.
- Kept the one-container localhost-only Docker architecture.
- Kept Render, VPS, Docker Mailserver, Caddy, Redis and cloud database infrastructure removed.
- Added offline tests for real-mail code paths via mocks.

## 2.0.0 — Local SQLite

- Converted the project to a localhost-only runtime and SQLite storage.
- Removed hosted/VPS infrastructure.
- Added Docker/SQLite deployment documentation.

## 1.2.0 — Safe Showcase

- Added explicit showcase/live boundaries and security controls.
- Added optional private live mail infrastructure.

## 1.0.0

- Initial browser-based email client.
