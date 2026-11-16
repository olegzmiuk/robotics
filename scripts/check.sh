#!/usr/bin/env bash

set -e

cd "$(dirname "$0")/.."

bash -n scripts/*.sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
npm run check --prefix dashboard/frontend
npm run build --prefix dashboard/frontend
