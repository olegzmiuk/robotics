#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: ./scripts/install.sh [--reinstall]

Synchronize the locked Python environment and reinstall exact frontend packages.

Options:
  --reinstall  Force uv to reinstall every Python package.
  -h, --help   Show this help.
EOF
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
  elif [[ -x "${ROOT_DIR}/.venv/bin/uv" ]]; then
    printf '%s\n' "${ROOT_DIR}/.venv/bin/uv"
  else
    printf '%s\n' "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
  fi
}

REINSTALL=false
case "${1:-}" in
  "") ;;
  --reinstall) REINSTALL=true ;;
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
  printf '%s\n' "npm is required (Node.js 24)." >&2
  exit 1
fi

UV_BIN="$(find_uv)"
cd "${ROOT_DIR}"

printf '%s\n' "Synchronizing locked Python dependencies..."
if [[ "${REINSTALL}" == true ]]; then
  "${UV_BIN}" sync --locked --extra dev --reinstall
else
  "${UV_BIN}" sync --locked --extra dev
fi

printf '%s\n' "Installing exact frontend dependencies..."
npm ci --prefix dashboard/frontend

printf '%s\n' "Installation complete. Run ./scripts/check.sh next."
