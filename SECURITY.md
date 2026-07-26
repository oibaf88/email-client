# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.2.x | Yes |
| 1.1.x and earlier | No |

Security fixes are made against the latest release line.

## Report a vulnerability

Please use GitHub's private vulnerability reporting feature for this repository. Do not include mailbox passwords, session cookies, patient information, private email content or production secrets in the report.

Include, when possible:

- the affected version or commit;
- whether the deployment used `showcase` or `live` mode;
- a minimal reproduction using synthetic data;
- impact and realistic attack prerequisites;
- suggested remediation if known.

Allow reasonable time for validation and remediation before public disclosure.

## Deployment boundary

### Showcase mode

The public showcase is designed to be isolated:

- it never parses or accepts a login password;
- it never initializes an IMAP, SMTP or Supabase connection;
- it uses reserved `.invalid` identities and synthetic content;
- message operations mutate only the temporary server-side session;
- every compose response states that delivery was simulated;
- the interface displays a persistent synthetic-mode banner.

Do not add real credentials, mail hosts, patient data or production database keys to a showcase deployment.

### Live mode

Live mode is for a private, HTTPS deployment connected to mail infrastructure you control. In production it requires:

- a random `FLASK_SECRET_KEY` of at least 32 characters;
- `SESSION_COOKIE_SECURE=true`;
- private Redis-backed server-side sessions;
- configured IMAP and SMTP submission hosts;
- TLS validation using the operating system trust store.

Readiness reports missing live dependencies with HTTP 503. Liveness only confirms that the process can respond.

## Residual risks

- The IMAP password is held in the private server-side session while the user is signed in. Protect Redis, use short session TTLs and prefer a dedicated application password.
- Live delete uses the IMAP deleted flag followed by expunge. Mail-server retention and backup policy remain the operator's responsibility.
- HTML email is converted to plain text. The UI intentionally does not render remote or active HTML content.
- Message previews are bounded, but the upstream IMAP server still participates in parsing untrusted email data.
- Session-local rate limits reduce accidental abuse but are not a replacement for reverse-proxy, identity-provider or mail-server rate controls.
- The optional mail appliance requires independent DNS, deliverability, abuse, backup and certificate-renewal operations.

## Built-in controls

- CSRF protection for every write, including login.
- Session identifier regeneration after successful live login and logout.
- HttpOnly, SameSite and production Secure cookies.
- Strict request and field size limits.
- Numeric bounded UID validation and mailbox-name injection checks.
- SMTP STARTTLS enforcement on submission port 587.
- CSP nonce, HSTS, anti-framing, MIME sniffing and privacy headers.
- No external assets in the web client.
- Synthetic network-isolation tests in CI.

## Secret handling

Never commit `.env`, mail passwords, private keys, certificate material, Redis URLs with credentials or Supabase service-role keys. Rotate a secret immediately if it appears in Git history, logs, screenshots, issues or pull requests.
