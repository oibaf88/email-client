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

: "${CERT_NAME:?CERT_NAME is required}"

docker run --rm \
  -p 80:80 \
  -v "${ROOT_DIR}/docker-data/letsencrypt:/etc/letsencrypt" \
  certbot/certbot:latest renew \
  --standalone

cd "${ROOT_DIR}"
docker compose -f compose.prod.yaml exec -T mailserver supervisorctl restart postfix dovecot || docker compose -f compose.prod.yaml restart mailserver
docker compose -f compose.prod.yaml restart caddy

echo "Certificate renewal attempted. Check expiry with infra/scripts/check-mailserver.sh."
