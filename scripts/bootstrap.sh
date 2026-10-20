#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  printf 'uv is required. On macOS: brew install uv\n' >&2
  exit 1
fi
uv sync --locked
if [ ! -e .env ]; then
  uv run --locked grok-codex init
fi
uv run --locked pytest -q
printf '\nReady. Run: uv run --locked grok-codex daemon\n'
