# Security model

## Trust boundaries

- Public internet clients can connect to SMTP port `25`, HTTPS port `443`, and
  the submission/IMAP ports exposed by the VPS.
- Only authenticated mailbox users may send through SMTP submission.
- The web UI stores mailbox credentials only in a server-side Flask session.
- Redis must not be exposed publicly.
- `docker-data/` contains mail, TLS certificates, and runtime state. Treat it as
  sensitive data.

## Relay policy

The stack must never be an open relay. The docker-mailserver config sets
`PERMIT_DOCKER=none`, and `infra/mailserver/config/postfix-main.cf` keeps relay
rules explicit:

- authenticated users can submit outbound mail,
- unauthenticated remote clients can only deliver to local domains,
- outbound delivery requires STARTTLS.

Verify with an external open-relay test after every mailserver configuration
change.

## Transport encryption

The stack is configured for TLS/ECDHE transport encryption:

- the web UI requires TLS 1.2 minimum for IMAP/SMTP client connections,
- docker-mailserver uses `TLS_LEVEL=modern`,
- Postfix outbound SMTP is configured with `smtp_tls_security_level = encrypt`,
- MTA-STS and TLS-RPT are provided for receiving-side policy and reporting.

This is not end-to-end encryption. Remote providers that receive the message can
still access the message contents.

## Secrets

Never commit:

- `.env`,
- mailbox passwords,
- private DKIM keys,
- TLS private keys,
- `docker-data/`.

Use long random mailbox passwords and rotate them if a browser or VPS session is
suspected to be compromised.

## Recommended hardening

- Run the stack only on a VPS firewalling all ports except `25`, `465`, `587`,
  `993`, `80`, and `443`.
- Keep SSH key-only and disable password SSH logins.
- Enable provider-level backups or volume snapshots.
- Review docker-mailserver release notes before upgrades.
- Start DMARC at `p=none`, then move to `quarantine` and `reject` after SPF and
  DKIM pass consistently.
