#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
TEMPLATE="${ROOT_DIR}/infra/mta-sts/.well-known/mta-sts.txt.template"
OUTPUT="${ROOT_DIR}/infra/mta-sts/.well-known/mta-sts.txt"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing .env. Copy .env.example to .env and fill in your domain values." >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "${ENV_FILE}"
set +a

: "${MAIL_FQDN:?MAIL_FQDN is required}"

mkdir -p "$(dirname "${OUTPUT}")"
sed "s|\${MAIL_FQDN}|${MAIL_FQDN}|g" "${TEMPLATE}" > "${OUTPUT}"

echo "Wrote ${OUTPUT}"
