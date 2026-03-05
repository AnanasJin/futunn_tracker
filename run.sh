#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"
ENV_FILE="${PROJECT_ROOT}/.env"

usage() {
  cat <<'EOF'
Usage: ./run.sh <up|down|restart|status>

The script reads TRADER from .env:
  - TRADER=ibkr   -> docker compose --profile ibkr ...
  - TRADER=futunn -> docker compose ...
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

ACTION="$1"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Error: ${ENV_FILE} not found. Please create it from env.sample first."
  exit 1
fi

raw_trader="$(
  sed -nE 's/^[[:space:]]*TRADER[[:space:]]*=[[:space:]]*(.*)$/\1/p' "${ENV_FILE}" \
    | tail -n 1
)"
raw_trader="${raw_trader%%#*}"
raw_trader="${raw_trader//\"/}"
raw_trader="${raw_trader//\'/}"
TRADER="$(echo "${raw_trader}" | xargs | tr '[:upper:]' '[:lower:]')"
TRADER="${TRADER:-ibkr}"

case "${TRADER}" in
  ibkr)
    COMPOSE_PREFIX=(docker compose --profile ibkr)
    ;;
  futunn)
    COMPOSE_PREFIX=(docker compose)
    ;;
  *)
    echo "Error: Unsupported TRADER='${TRADER}' in .env (expected ibkr or futunn)."
    exit 1
    ;;
esac

run_compose() {
  echo "TRADER=${TRADER}"
  echo "+ ${COMPOSE_PREFIX[*]} $*"
  (cd "${PROJECT_ROOT}" && "${COMPOSE_PREFIX[@]}" "$@")
}

case "${ACTION}" in
  up)
    run_compose up --build -d
    ;;
  down)
    run_compose down
    ;;
  restart)
    run_compose down
    run_compose up --build -d
    ;;
  logs)
    docker compose logs -f
    ;;
  *)
    usage
    exit 1
    ;;
esac
