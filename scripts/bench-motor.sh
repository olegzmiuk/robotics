#!/usr/bin/env bash

set -e

cd "$(dirname "$0")/.."

MOTOR="${1:?Usage: ./scripts/bench-motor.sh M1}"

echo "Propellers removed, frame secured, FC disarmed, dashboard stopped."
read -r -p "Type PULSE ${MOTOR} to continue: " CONFIRMATION

if [[ "${CONFIRMATION}" != "PULSE ${MOTOR}" ]]; then
  echo "Cancelled."
  exit 1
fi

UAV_BENCH_MOTOR_ENABLED=PROPELLERS_REMOVED \
  uv run --locked python -m uav.bench_motor_cli \
  --port "${UAV_SERIAL_PORT:-/dev/ttyAMA0}" \
  --motor "${MOTOR}" \
  --confirm-propellers-removed \
  --confirm-frame-secured \
  --confirm-controller-disarmed \
  --confirm-battery-disconnect-ready
