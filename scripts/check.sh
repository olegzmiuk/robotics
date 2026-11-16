#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: ./scripts/check.sh

Run the same Python and frontend validation used by the project workflow.
This script never accesses the camera, UART, or physical motors.
EOF
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
  elif [[ -x "${ROOT_DIR}/.venv/bin/uv" ]]; then
    printf '%s\n' "${ROOT_DIR}/.venv/bin/uv"
  else
    printf '%s\n' "uv is missing; run ./scripts/install.sh first." >&2
    exit 1
  fi
}

case "${1:-}" in
  "") ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi
if ! command -v npm >/dev/null 2>&1; then
  printf '%s\n' "npm is missing; run ./scripts/install.sh first." >&2
  exit 1
fi

UV_BIN="$(find_uv)"
cd "${ROOT_DIR}"

bash -n scripts/*.sh
"${UV_BIN}" run --locked --no-sync ruff check .
"${UV_BIN}" run --locked --no-sync ruff format --check .
"${UV_BIN}" run --locked --no-sync mypy
"${UV_BIN}" run --locked --no-sync pytest
npm run check --prefix dashboard/frontend
npm run build --prefix dashboard/frontend

printf '%s\n' "All checks passed."
