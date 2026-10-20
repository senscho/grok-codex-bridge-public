#!/usr/bin/env bash
# Read-only bot-host (box) check for the Grok-Codex MCP connector.
# Prints PASS/FAIL/WARN/INFO lines only; never prints the token or tunnel URL.
# Exit status 1 if any FAIL.
#
# Usage: bash scripts/check_box.sh [--url-file FILE | --url ORIGIN] [--prefix /codex]
#   With BRIDGE_GROK_TOKEN in the environment it also checks an authenticated call.
set -u

ROOT=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
URL=${CODEX_TUNNEL_URL:-}
URL_FILE=${CODEX_TUNNEL_URL_FILE:-}
PREFIX=${BRIDGE_PATH_PREFIX-/codex}
FAILS=0
WARNS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL=$2; shift 2 ;;
    --url-file) URL_FILE=$2; shift 2 ;;
    --prefix) PREFIX=$2; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

pass() { printf 'PASS %s: %s\n' "$1" "$2"; }
fail() { printf 'FAIL %s: %s\n' "$1" "$2"; FAILS=$((FAILS + 1)); }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS + 1)); }
info() { printf 'INFO %s: %s\n' "$1" "$2"; }
have() { command -v "$1" >/dev/null 2>&1; }

printf '== Grok-Codex connector host check (read-only) ==\n'

if have uv; then pass uv "$(uv --version 2>/dev/null)"; else fail uv "not installed; see https://docs.astral.sh/uv/"; fi
if have curl; then pass curl "available"; else fail curl "not installed"; fi
if have git; then pass git "$(git --version 2>/dev/null)"; else warn git "not installed (needed only to clone/update)"; fi

if have uv; then
  PY=$(uv python find '>=3.12' 2>/dev/null)
  if [ -n "$PY" ]; then pass python "$("$PY" --version 2>&1) via uv"
  else warn python "no Python 3.12+ found; uv downloads one on first run (or: uv python install 3.12)"; fi
  if uv run --quiet --no-project --with-requirements "$ROOT/connectors/grok-mcp/requirements.txt" \
       python -c 'import mcp, httpx' >/dev/null 2>&1; then
    pass connector-deps "mcp and httpx resolve"
  else
    fail connector-deps "cannot install mcp/httpx; check outbound HTTPS to PyPI"
  fi
fi

if have curl; then
  if curl -fsS --max-time 10 -o /dev/null https://pypi.org/simple/ 2>/dev/null; then pass outbound-https "PyPI reachable"
  else fail outbound-https "cannot reach https://pypi.org"; fi
fi

if [ -z "$URL" ] && [ -n "$URL_FILE" ]; then
  if [ -r "$URL_FILE" ]; then
    URL=$(tr -d ' \t\r\n' < "$URL_FILE")
    if [ "$(uname -s)" = Darwin ]; then M=$(stat -f %Lp "$URL_FILE"); else M=$(stat -c %a "$URL_FILE"); fi
    if [ "$M" = 600 ]; then pass url-file "readable, mode 600"; else warn url-file "mode $M; run: chmod 600 FILE"; fi
  else
    fail url-file "tunnel URL file not readable"
  fi
fi

if [ -z "$URL" ]; then
  info tunnel "no tunnel URL given; skipping reachability (pass --url-file after stage 5)"
else
  case "$URL" in
    https://*) pass tunnel-url "HTTPS origin (value not shown)" ;;
    http://127.0.0.1*|http://localhost*) warn tunnel-url "loopback HTTP; testing only" ;;
    *) fail tunnel-url "must be an https:// origin" ;;
  esac
  BASE="${URL%/}${PREFIX%/}"
  CODE=$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$BASE/healthz" 2>/dev/null)
  if [ "$CODE" = 200 ]; then pass tunnel-health "bridge health check OK through tunnel and prefix"
  else fail tunnel-health "health check returned HTTP ${CODE:-none}; check tunnel, proxy and BRIDGE_PATH_PREFIX"; fi
  if [ -n "${BRIDGE_GROK_TOKEN:-}" ]; then
    CODE=$(curl -sS --max-time 30 -o /dev/null -w '%{http_code}' -X POST \
      -H "Authorization: Bearer $BRIDGE_GROK_TOKEN" -H 'Content-Type: application/json' \
      -d '{}' "$BASE/tools/codex_list" 2>/dev/null)
    case "$CODE" in
      200) pass grok-token "authenticated codex_list OK" ;;
      401) fail grok-token "401: wrong Grok token in the box secret" ;;
      *) fail grok-token "codex_list returned HTTP ${CODE:-none}" ;;
    esac
  else
    info grok-token "BRIDGE_GROK_TOKEN not in this environment; authenticated check skipped"
  fi
fi

printf '== Summary: %d FAIL, %d WARN ==\n' "$FAILS" "$WARNS"
[ "$FAILS" -eq 0 ]
