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

cd "${ROOT_DIR}"

usage() {
  cat <<'EOF'
Usage:
  infra/scripts/alias.sh list
  infra/scripts/alias.sh add alias@example.com recipient@example.com
  infra/scripts/alias.sh delete alias@example.com recipient@example.com
EOF
}

require_pair() {
  if [[ $# -lt 2 || "${1}" != *@* || "${2}" != *@* ]]; then
    echo "Alias and recipient must both be full email addresses." >&2
    usage
    exit 1
  fi
}

command="${1:-}"
shift || true

case "${command}" in
  list)
    docker compose -f compose.prod.yaml exec -T mailserver setup alias list
    ;;
  add)
    require_pair "$@"
    docker compose -f compose.prod.yaml exec -T mailserver setup alias add "$@"
    ;;
  delete|del|remove|rm)
    require_pair "$@"
    docker compose -f compose.prod.yaml exec -T mailserver setup alias del "$@"
    ;;
  help|-h|--help|"")
    usage
    ;;
  *)
    echo "Unknown alias command: ${command}" >&2
    usage
    exit 1
    ;;
esac
