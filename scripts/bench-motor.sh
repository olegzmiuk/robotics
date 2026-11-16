#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERIAL_PORT="${UAV_SERIAL_PORT:-/dev/ttyAMA0}"
OUTPUT="1050"
DURATION="0.15"

usage() {
  cat <<'EOF'
Usage: ./scripts/bench-motor.sh MOTOR [options]

Run one supervised, propeller-free motor pulse. MOTOR must be M1, M2, M3, or M4.

Options:
  --port PATH        Serial device (default: UAV_SERIAL_PORT or /dev/ttyAMA0).
  --output VALUE     External motor value (default: 1050; maximum: 1100).
  --duration SEC     Pulse duration (default: 0.15; maximum: 0.2).
  -h, --help         Show this help without accessing hardware.

The dashboard must be stopped because it owns the same UART.
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

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

MOTOR="${1:-}"
if [[ -z "${MOTOR}" ]]; then
  usage >&2
  exit 2
fi
shift

case "${MOTOR}" in
  M1 | M2 | M3 | M4) ;;
  *)
    printf '%s\n' "motor must be M1, M2, M3, or M4" >&2
    exit 2
    ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      [[ $# -ge 2 ]] || { printf '%s\n' "--port requires a value" >&2; exit 2; }
      SERIAL_PORT="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || { printf '%s\n' "--output requires a value" >&2; exit 2; }
      OUTPUT="$2"
      shift 2
      ;;
    --duration)
      [[ $# -ge 2 ]] || { printf '%s\n' "--duration requires a value" >&2; exit 2; }
      DURATION="$2"
      shift 2
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

if [[ ! -t 0 || ! -t 1 ]]; then
  printf '%s\n' "motor pulse requires an interactive terminal" >&2
  exit 1
fi

cat <<EOF

BENCH MOTOR TEST: ${MOTOR}

Confirm all of the following before continuing:
  - every propeller is physically removed;
  - the frame is secured;
  - the flight controller is disarmed;
  - the dashboard is stopped;
  - you are present with immediate access to the battery connector.

Requested output: ${OUTPUT}
Requested duration: ${DURATION} s
Serial port: ${SERIAL_PORT}

Betaflight has no timeout for this command. If STOP is not confirmed,
disconnect the battery immediately.
EOF

printf 'Type PULSE %s to continue: ' "${MOTOR}"
read -r CONFIRMATION
if [[ "${CONFIRMATION}" != "PULSE ${MOTOR}" ]]; then
  printf '%s\n' "Confirmation did not match; no command was sent."
  exit 1
fi

UV_BIN="$(find_uv)"
cd "${ROOT_DIR}"

export UAV_BENCH_MOTOR_ENABLED="PROPELLERS_REMOVED"
exec "${UV_BIN}" run --locked --no-sync uav-bench-motor \
  --port "${SERIAL_PORT}" \
  --motor "${MOTOR}" \
  --output "${OUTPUT}" \
  --duration "${DURATION}" \
  --confirm-propellers-removed \
  --confirm-frame-secured \
  --confirm-controller-disarmed \
  --confirm-battery-disconnect-ready
