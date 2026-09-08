# Security

## Supported runtime

BFAB Local Mail v2.1 is supported as a localhost application bound to `127.0.0.1`.

It is not a public web service and it does not host an SMTP/IMAP server. It connects as a client to the existing IMAP/SMTP provider explicitly configured by the local user.

## Secrets

The SQLite database stores non-secret server configuration only. Mailbox passwords are not stored in SQLite or `.env`.

After successful IMAP authentication, the password is retained only in a Flask server-side filesystem session so subsequent IMAP/SMTP requests can authenticate. The browser cookie contains the opaque/signed session identifier, not the mailbox password.

Sign out when finished. With the default random Flask secret, a process restart invalidates the previous browser session.

## Local database

`data/email-client.db` is ordinary, unencrypted SQLite. It contains server settings but not mailbox message bodies or mailbox credentials.

Use normal operating-system protections such as account passwords, BitLocker/device encryption, filesystem permissions and encrypted backups.

## Network boundary

`compose.yaml` publishes the web UI only to `127.0.0.1:8000`.

Do not change this to a public/LAN bind without adding a reviewed authentication/deployment threat model and HTTPS.

Outbound mail-client traffic is expected:

- IMAP over TLS;
- SMTP implicit TLS on 465 or STARTTLS on other configured ports.

TLS certificate validation is enabled and the client requires TLS 1.2 or later.

## Limitations

- Password/app-password IMAP/SMTP authentication is supported; OAuth 2.0 is not yet implemented.
- Attachments are listed but not downloadable through the UI.
- Delete uses the IMAP `\\Deleted` flag plus expunge in the currently selected folder; provider-specific Trash semantics can differ.
- This is a personal/local client, not a multi-user service.

## Repository hygiene

Do not commit:

- `.env`;
- `data/email-client.db` or SQLite WAL/SHM files;
- mailbox passwords/app-passwords;
- copied mail content containing sensitive data.

## Reporting

Do not post real credentials, private mail contents, or personally identifying local paths in public issues.
