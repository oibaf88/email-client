# Full self-managed mail setup

This repository contains both pieces of the product:

- The custom Flask webmail interface.
- The Dockerized mail server stack required for a domain mailbox.

The mail-server core uses docker-mailserver instead of a hand-written SMTP/IMAP
implementation. That is intentional: SMTP interoperability, queueing, DKIM,
DMARC, antispam, delivery retries, SASL, and IMAP storage are security-sensitive
systems that should be handled by mature components.

## 1. Prepare the VPS

Use a Linux VPS with:

- Docker and Docker Compose v2.
- Static public IPv4.
- Reverse DNS/PTR control.
- Open TCP ports: `25`, `465`, `587`, `993`, `80`, `443`.
- No provider-level SMTP block on outbound port `25`.

Render is suitable for normal HTTP apps, but not for the MX server itself.
Supabase is not used for mailbox storage; Dovecot stores mail on the VPS.

## 2. Configure `.env`

Copy `.env.example`:

```bash
cp .env.example .env
```

Fill in:

```env
MAIL_DOMAIN=example.com
MAIL_HOSTNAME=mail
MAIL_FQDN=mail.example.com
WEBMAIL_FQDN=webmail.example.com
MTA_STS_FQDN=mta-sts.example.com
CERT_NAME=mail.example.com
ACME_EMAIL=admin@example.com
POSTMASTER_ADDRESS=postmaster@example.com
DMS_IMAGE=ghcr.io/docker-mailserver/docker-mailserver:latest
FIRST_MAILBOX=admin@example.com
FIRST_MAILBOX_PASSWORD=use-a-long-random-password
```

For production, replace `latest` with a stable docker-mailserver version after
checking the upstream release notes.

## 3. Publish initial DNS

Create these `A` records before requesting certificates:

```dns
mail.example.com.     A  203.0.113.10
webmail.example.com.  A  203.0.113.10
mta-sts.example.com.  A  203.0.113.10
```

Set reverse DNS at your VPS provider:

```text
203.0.113.10 -> mail.example.com
```

Then publish:

```dns
example.com.             MX 10  mail.example.com.
example.com.             TXT    "v=spf1 mx a -all"
_dmarc.example.com.      TXT    "v=DMARC1; p=none; rua=mailto:dmarc@example.com; adkim=s; aspf=s"
_smtp._tls.example.com.  TXT    "v=TLSRPTv1; rua=mailto:tls-reports@example.com"
_mta-sts.example.com.    TXT    "v=STSv1; id=2026070401"
```

Use `infra/dns-records.example.md` as the full template.

## 4. Issue TLS certificates

Run:

```bash
chmod +x infra/scripts/*.sh
infra/scripts/issue-cert.sh
```

The script requests one Let's Encrypt certificate covering:

- `mail.example.com`
- `webmail.example.com`
- `mta-sts.example.com`

The certificate is mounted into both docker-mailserver and Caddy.

## 5. Start the appliance

Run:

```bash
infra/scripts/bootstrap-vps.sh
```

The script:

- renders the public MTA-STS policy,
- creates persistent data directories,
- starts Redis, the web UI, docker-mailserver, and Caddy,
- creates `FIRST_MAILBOX` if configured,
- generates DKIM keys.

After DKIM generation, publish the DKIM TXT record shown by docker-mailserver.

The Docker network also gives the mailserver the `${MAIL_FQDN}` alias internally.
That lets the web UI connect to the mailserver using the same hostname that
appears on the TLS certificate, while traffic stays inside the Compose stack.

## 6. TLS/ECDHE policy

The web UI uses Python's certificate validation and requires TLS 1.2 minimum for
IMAP/SMTP client connections. For TLS 1.2 it asks OpenSSL for ECDHE AEAD suites.
TLS 1.3 uses ephemeral key exchange by default.

The mailserver config sets:

- `TLS_LEVEL=modern` in docker-mailserver.
- STARTTLS-required SMTP submission before authentication.
- strict outbound SMTP transport with `smtp_tls_security_level = encrypt`.
- TLS 1.2 minimum on Postfix SMTP client/server settings.
- `smtpd_tls_eecdh_grade = ultra`.

Important: this is transport encryption, not end-to-end encryption. The remote
recipient reads normally and does not manage keys, but the receiving mail server
can still access the message.

## 7. Verify

Run:

```bash
infra/scripts/check-mailserver.sh
```

Also verify with external tools:

- MX resolves to your VPS.
- PTR/rDNS points back to `mail.example.com`.
- SPF passes.
- DKIM passes after publishing the generated TXT record.
- DMARC passes.
- MTA-STS policy is reachable at
  `https://mta-sts.example.com/.well-known/mta-sts.txt`.
- The server is not an open relay.

Then test real delivery:

1. Send from `admin@example.com` to Gmail or Outlook.
2. Inspect headers for SPF, DKIM, DMARC, and TLS.
3. Send back from Gmail or Outlook to `admin@example.com`.
4. Confirm the reply appears in the custom web UI.

## 8. Operations

Create, list, update, or delete mailboxes from the VPS:

```bash
infra/scripts/mailbox.sh list
infra/scripts/mailbox.sh add user@example.com
infra/scripts/mailbox.sh update user@example.com
infra/scripts/mailbox.sh delete user@example.com
```

Manage aliases:

```bash
infra/scripts/alias.sh list
infra/scripts/alias.sh add info@example.com admin@example.com
infra/scripts/alias.sh delete info@example.com admin@example.com
```

Renew certificates:

```bash
infra/scripts/renew-cert.sh
infra/scripts/check-mailserver.sh
```

Back up these directories:

```text
docker-data/dms/mail-data
docker-data/dms/mail-state
docker-data/letsencrypt
infra/mailserver/config
```

Keep Docker images updated deliberately. Do not auto-upgrade the mail server
without reading release notes and keeping a rollback path.
