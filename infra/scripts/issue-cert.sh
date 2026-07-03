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

: "${MAIL_FQDN:?MAIL_FQDN is required}"
: "${WEBMAIL_FQDN:?WEBMAIL_FQDN is required}"
: "${MTA_STS_FQDN:?MTA_STS_FQDN is required}"
: "${ACME_EMAIL:?ACME_EMAIL is required}"

mkdir -p "${ROOT_DIR}/docker-data/letsencrypt"

cat <<EOF
Requesting Let's Encrypt certificate for:
  - ${MAIL_FQDN}
  - ${WEBMAIL_FQDN}
  - ${MTA_STS_FQDN}

Before continuing, confirm these hostnames already resolve to this VPS and that
TCP port 80 is reachable from the public internet.
EOF

docker run --rm \
  -p 80:80 \
  -v "${ROOT_DIR}/docker-data/letsencrypt:/etc/letsencrypt" \
  certbot/certbot:latest certonly \
  --standalone \
  --preferred-challenges http \
  --email "${ACME_EMAIL}" \
  --agree-tos \
  --no-eff-email \
  -d "${MAIL_FQDN}" \
  -d "${WEBMAIL_FQDN}" \
  -d "${MTA_STS_FQDN}"

echo "Certificate written under docker-data/letsencrypt/live/${CERT_NAME:-${MAIL_FQDN}}"
