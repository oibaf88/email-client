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
: "${MAIL_HOSTNAME:?MAIL_HOSTNAME is required}"
: "${MAIL_FQDN:?MAIL_FQDN is required}"
: "${WEBMAIL_FQDN:?WEBMAIL_FQDN is required}"
: "${MTA_STS_FQDN:?MTA_STS_FQDN is required}"
: "${CERT_NAME:?CERT_NAME is required}"
: "${POSTMASTER_ADDRESS:?POSTMASTER_ADDRESS is required}"
: "${DMS_IMAGE:?DMS_IMAGE is required}"

command -v docker >/dev/null 2>&1 || {
  echo "Docker is required on the VPS." >&2
  exit 1
}

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required on the VPS." >&2
  exit 1
fi

CERT_PATH="${ROOT_DIR}/docker-data/letsencrypt/live/${CERT_NAME}/fullchain.pem"
if [[ ! -f "${CERT_PATH}" ]]; then
  echo "Missing TLS certificate at ${CERT_PATH}" >&2
  echo "Run infra/scripts/issue-cert.sh before bootstrapping the stack." >&2
  exit 1
fi

"${ROOT_DIR}/infra/scripts/render-mta-sts.sh"

mkdir -p \
  "${ROOT_DIR}/docker-data/dms/mail-data" \
  "${ROOT_DIR}/docker-data/dms/mail-state" \
  "${ROOT_DIR}/docker-data/dms/mail-logs" \
  "${ROOT_DIR}/docker-data/redis" \
  "${ROOT_DIR}/docker-data/caddy/data" \
  "${ROOT_DIR}/docker-data/caddy/config"

cd "${ROOT_DIR}"

docker compose pull
docker compose up -d

if [[ -n "${FIRST_MAILBOX:-}" && -n "${FIRST_MAILBOX_PASSWORD:-}" ]]; then
  echo "Ensuring first mailbox exists: ${FIRST_MAILBOX}"
  docker compose exec -T mailserver setup email add "${FIRST_MAILBOX}" "${FIRST_MAILBOX_PASSWORD}" || true
fi

echo "Generating DKIM keys. Publish the resulting TXT record in DNS."
docker compose exec -T mailserver setup config dkim

echo "Bootstrap complete."
echo "Open https://${WEBMAIL_FQDN} after DNS and TLS checks pass."
