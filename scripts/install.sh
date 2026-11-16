#!/usr/bin/env bash

set -e

cd "$(dirname "$0")/.."

uv sync --locked --extra dev "$@"
npm ci --prefix dashboard/frontend
