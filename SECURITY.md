# Security

## Supported runtime

BFAB Local Mail v2 is supported only as a local application bound to loopback (`127.0.0.1`).

The project has no public deployment mode, mail-server mode, account system, IMAP/SMTP integration, or cloud database integration.

## Trust boundary

The local computer account is the trust boundary. Anyone who can read the repository's `data/email-client.db` file can read the locally stored messages.

SQLite is not encrypted by this application.

For sensitive data, rely on operating-system protections such as:

- a password-protected Windows account;
- BitLocker/device encryption;
- normal filesystem permissions;
- encrypted backups.

## Network exposure

`compose.yaml` publishes port 8000 only on `127.0.0.1`.

Do not change that to `0.0.0.0` or expose the application directly to the public Internet unless you also design and implement proper authentication, HTTPS, rate limiting, deployment secrets, and a reviewed threat model.

## Application controls

The application includes:

- CSRF protection for write requests;
- request and field-size limits;
- SQLite parameterized queries;
- restrictive browser security headers;
- no remote mail credentials;
- no external mail transport;
- no cloud runtime secrets.

## Database handling

The SQLite database and WAL/SHM sidecar files are ignored by Git.

Do not commit:

- `data/email-client.db`
- `data/email-client.db-wal`
- `data/email-client.db-shm`
- `.env`

Stop the application before editing the database manually with DB Browser for SQLite.

## Reporting

If you discover a security problem, avoid publishing real mailbox contents, secrets, local paths containing personal information, or other sensitive data in a public issue.
