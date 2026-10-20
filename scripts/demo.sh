#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  printf 'uv is required: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
  exit 1
fi
uv sync --locked
exec uv run --locked python -m grok_codex.demo "$@"
