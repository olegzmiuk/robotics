#!/usr/bin/env bash

set -e

cd "$(dirname "$0")/.."

npm run build --prefix dashboard/frontend
uv run --locked uvicorn uav_dashboard.api:app --host 0.0.0.0 --port 8000
