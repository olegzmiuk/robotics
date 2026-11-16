#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="0.0.0.0"
PORT="8000"
BUILD_FRONTEND=true

usage() {
  cat <<'EOF'
Usage: ./scripts/dashboard.sh [options]

Build the React frontend and start the FastAPI dashboard.

Options:
  --host HOST    Bind host (default: 0.0.0.0).
  --port PORT    Bind port (default: 8000).
  --skip-build   Start with the existing dashboard/frontend/dist build.
  -h, --help     Show this help.

Existing UAV_* environment variables are passed through unchanged.
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      [[ $# -ge 2 ]] || { printf '%s\n' "--host requires a value" >&2; exit 2; }
      HOST="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || { printf '%s\n' "--port requires a value" >&2; exit 2; }
      PORT="$2"
      shift 2
      ;;
    --skip-build)
      BUILD_FRONTEND=false
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  printf '%s\n' "port must be an integer between 1 and 65535" >&2
  exit 2
fi

UV_BIN="$(find_uv)"
cd "${ROOT_DIR}"

if [[ "${BUILD_FRONTEND}" == true ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    printf '%s\n' "npm is missing; run ./scripts/install.sh first." >&2
    exit 1
  fi
  npm run build --prefix dashboard/frontend
elif [[ ! -f dashboard/frontend/dist/index.html ]]; then
  printf '%s\n' "frontend build is missing; remove --skip-build" >&2
  exit 1
fi

printf 'Starting dashboard at http://%s:%s\n' "${HOST}" "${PORT}"
exec "${UV_BIN}" run --locked --no-sync uvicorn uav_dashboard.api:app --host "${HOST}" --port "${PORT}"
