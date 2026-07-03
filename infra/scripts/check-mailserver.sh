#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing .env. Copy .env.example to .env and fill in your domain values." >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "${ENV_FILE}"
set +a

: "${MAIL_DOMAIN:?MAIL_DOMAIN is required}"
: "${MAIL_FQDN:?MAIL_FQDN is required}"
: "${WEBMAIL_FQDN:?WEBMAIL_FQDN is required}"
: "${MTA_STS_FQDN:?MTA_STS_FQDN is required}"

check_tls() {
  local label="$1"
  shift
  echo
  echo "== ${label} =="
  timeout 15 openssl s_client "$@" </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates
}

if command -v dig >/dev/null 2>&1; then
  echo "== DNS =="
  dig +short A "${MAIL_FQDN}"
  dig +short MX "${MAIL_DOMAIN}"
  dig +short TXT "${MAIL_DOMAIN}"
  dig +short TXT "_dmarc.${MAIL_DOMAIN}"
  dig +short TXT "_mta-sts.${MAIL_DOMAIN}"
  dig +short TXT "_smtp._tls.${MAIL_DOMAIN}"
else
  echo "dig is not installed; skipping DNS checks."
fi

check_tls "SMTP STARTTLS 25" -starttls smtp -connect "${MAIL_FQDN}:25" -servername "${MAIL_FQDN}"
check_tls "Submission STARTTLS 587" -starttls smtp -connect "${MAIL_FQDN}:587" -servername "${MAIL_FQDN}"
check_tls "IMAPS 993" -connect "${MAIL_FQDN}:993" -servername "${MAIL_FQDN}"
check_tls "Webmail HTTPS" -connect "${WEBMAIL_FQDN}:443" -servername "${WEBMAIL_FQDN}"
check_tls "MTA-STS HTTPS" -connect "${MTA_STS_FQDN}:443" -servername "${MTA_STS_FQDN}"

echo
echo "Fetch MTA-STS policy:"
curl -fsS "https://${MTA_STS_FQDN}/.well-known/mta-sts.txt"
