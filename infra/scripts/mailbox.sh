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
  infra/scripts/mailbox.sh list
  infra/scripts/mailbox.sh add user@example.com [password]
  infra/scripts/mailbox.sh update user@example.com [password]
  infra/scripts/mailbox.sh delete user@example.com

If no password is supplied for add/update, docker-mailserver prompts for it.
EOF
}

require_mailbox() {
  if [[ $# -lt 1 || "${1}" != *@* ]]; then
    echo "A full mailbox address is required." >&2
    usage
    exit 1
  fi
}

command="${1:-}"
shift || true

case "${command}" in
  list)
    docker compose exec -T mailserver setup email list
    ;;
  add)
    require_mailbox "$@"
    docker compose exec -T mailserver setup email add "$@"
    ;;
  update)
    require_mailbox "$@"
    docker compose exec -T mailserver setup email update "$@"
    ;;
  delete|del|remove|rm)
    require_mailbox "$@"
    docker compose exec -T mailserver setup email del "$@"
    ;;
  help|-h|--help|"")
    usage
    ;;
  *)
    echo "Unknown mailbox command: ${command}" >&2
    usage
    exit 1
    ;;
esac
