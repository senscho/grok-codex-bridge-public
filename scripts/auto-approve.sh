#!/usr/bin/env bash
# Opt-in local helper for exactly one session; human token never leaves the Mac.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --locked python -m grok_codex.auto_approve "$@"
