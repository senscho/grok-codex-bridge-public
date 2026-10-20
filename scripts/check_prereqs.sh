#!/usr/bin/env bash
# Read-only Mac prerequisite and configuration check for the Grok-Codex bridge.
# Compatible with macOS /bin/bash 3.2. Prints PASS/FAIL/WARN/INFO lines only;
# never prints token, webhook or credential values. Exit status 1 if any FAIL.
#
# Usage: bash scripts/check_prereqs.sh [--repo DIR] [--port 48765] [--proxy-port 48766]
set -u

REPO_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
PORT=48765
PROXY_PORT=48766
MIN_CODEX=0.163.0
FAILS=0
WARNS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO_DIR=$2; shift 2 ;;
    --port) PORT=$2; shift 2 ;;
    --proxy-port) PROXY_PORT=$2; shift 2 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

pass() { printf 'PASS %s: %s\n' "$1" "$2"; }
fail() { printf 'FAIL %s: %s\n' "$1" "$2"; FAILS=$((FAILS + 1)); }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS + 1)); }
info() { printf 'INFO %s: %s\n' "$1" "$2"; }
have() { command -v "$1" >/dev/null 2>&1; }
mode_of() {
  if [ "$(uname -s)" = Darwin ]; then stat -f %Lp "$1" 2>/dev/null; else stat -c %a "$1" 2>/dev/null; fi
}

# version_ge A B: true when dotted version A >= B (numeric fields only).
version_ge() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    na = split(a, x, "."); nb = split(b, y, ".");
    n = (na > nb) ? na : nb;
    for (i = 1; i <= n; i++) { xi = x[i] + 0; yi = y[i] + 0;
      if (xi > yi) exit 0; if (xi < yi) exit 1 }
    exit 0 }'
}

# Read one KEY from the private .env without sourcing it or printing it.
env_value() {
  [ -r "$REPO_DIR/.env" ] || return 1
  sed -n "s/^$1=//p" "$REPO_DIR/.env" | tail -n 1 | sed "s/^'\(.*\)'\$/\1/; s/^\"\(.*\)\"\$/\1/"
}

port_listener() {
  if have lsof; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  fi
}

printf '== Grok-Codex bridge prerequisite check (read-only) ==\n'

# 1. Operating system
if [ "$(uname -s)" = Darwin ]; then
  OSV=$(sw_vers -productVersion 2>/dev/null || echo unknown)
  if version_ge "$OSV" 13.0; then pass macos "macOS $OSV"; else warn macos "macOS $OSV; 13 or newer is recommended"; fi
else
  fail macos "this script targets macOS; found $(uname -s)"
fi
if have brew; then pass homebrew "$(brew --version 2>/dev/null | head -n 1)"; else warn homebrew "not found; install from https://brew.sh or install uv/codex another way"; fi

# 2. Tools
if have uv; then pass uv "$(uv --version 2>/dev/null)"; else fail uv "not installed; run: brew install uv"; fi
if have git; then pass git "$(git --version 2>/dev/null)"; else fail git "not installed; run: xcode-select --install"; fi
if have curl; then pass curl "available"; else fail curl "not installed"; fi

CODEX_OK=0
if have codex; then
  CV=$(codex --version 2>/dev/null | head -n 1)
  CVN=$(printf '%s' "$CV" | sed -n 's/[^0-9]*\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p')
  if [ -z "$CVN" ]; then
    warn codex-cli "installed but version unreadable ($CV)"
  elif version_ge "$CVN" "$MIN_CODEX"; then
    pass codex-cli "$CVN at $(command -v codex) (protocol contract recorded on $MIN_CODEX)"
  else
    warn codex-cli "$CVN is older than $MIN_CODEX; run: brew upgrade --cask codex"
  fi
  CODEX_OK=1
else
  fail codex-cli "not installed; run: brew install --cask codex"
fi

# login_state HOME_DIR -> prints ChatGPT|API key|none (never the raw status line).
login_state() {
  out=$(CODEX_HOME="$1" codex login status 2>&1); rc=$?
  if [ $rc -ne 0 ]; then echo none
  elif printf '%s' "$out" | grep -qi chatgpt; then echo ChatGPT
  elif printf '%s' "$out" | grep -qi 'api key'; then echo 'API key'
  else echo 'logged in'; fi
}

if [ $CODEX_OK -eq 1 ]; then
  DEFAULT_HOME=${CODEX_HOME:-$HOME/.codex}
  if [ -d "$DEFAULT_HOME" ]; then
    S=$(login_state "$DEFAULT_HOME")
    if [ "$S" = none ]; then info codex-login-default "default CODEX_HOME not logged in (bridge accounts use their own homes)"
    else info codex-login-default "default CODEX_HOME logged in using $S"; fi
  else
    info codex-login-default "no default CODEX_HOME; fine, bridge accounts use their own homes"
  fi
fi

# 3. Repository and private configuration
if [ -f "$REPO_DIR/pyproject.toml" ] && [ -f "$REPO_DIR/uv.lock" ]; then
  pass repo "bridge checkout found"
  if [ -d "$REPO_DIR/.venv" ]; then pass venv "project .venv exists"; else warn venv "no .venv yet; run: uv sync --locked"; fi
else
  warn repo "bridge checkout not found at the given --repo path (expected before stage 1)"
fi

STATE_DIR=''
if [ -f "$REPO_DIR/.env" ]; then
  MODE=$(mode_of "$REPO_DIR/.env")
  if [ "$MODE" = 600 ]; then pass env-file ".env exists, mode 600"; else fail env-file ".env mode is $MODE; run: chmod 600 .env"; fi
  G=$(env_value BRIDGE_GROK_TOKEN); H=$(env_value BRIDGE_HUMAN_TOKEN)
  if [ ${#G} -ge 32 ] && [ ${#H} -ge 32 ] && [ "$G" != "$H" ]; then
    pass tokens "Grok and human tokens set, distinct, 32+ chars (values not shown)"
  else
    fail tokens "tokens missing, short or identical; regenerate with grok-codex init on a fresh .env"
  fi
  G=''; H=''
  ROOTS=$(env_value BRIDGE_REPO_ROOTS)
  if [ -z "$ROOTS" ]; then
    fail repo-roots "BRIDGE_REPO_ROOTS is empty"
  else
    OLDIFS=$IFS; IFS=:
    for R in $ROOTS; do
      case "$R" in "~"*) R2="$HOME${R#\~}" ;; *) R2=$R ;; esac
      if [ "$R2" = "$REPO_DIR" ]; then warn repo-roots "a root is the bridge checkout itself (init default); set your project root"
      elif [ -d "$R2" ]; then pass repo-roots "root exists: $R2"
      else fail repo-roots "root does not exist: $R2"; fi
    done
    IFS=$OLDIFS
  fi
  STATE_DIR=$(env_value BRIDGE_STATE_DIR)
  case "$STATE_DIR" in "~"*) STATE_DIR="$HOME${STATE_DIR#\~}" ;; esac
  W=$(env_value BRIDGE_WEBHOOK_URL)
  if [ -n "$W" ]; then pass webhook "BRIDGE_WEBHOOK_URL set (value not shown)"; else info webhook "BRIDGE_WEBHOOK_URL empty; webhooks off until stage 8"; fi
  W=''
else
  info env-file "no .env yet (created in stage 2 by grok-codex init)"
fi

# 4. Codex account homes (CODEX_HOME per registered account)
[ -n "$STATE_DIR" ] || STATE_DIR="$HOME/.local/state/grok-codex"
ACC="$STATE_DIR/accounts"
if [ -d "$ACC" ]; then
  FOUND=0
  for D in "$ACC"/*; do
    [ -d "$D" ] || continue
    FOUND=1; NAME=$(basename "$D")
    DM=$(mode_of "$D")
    [ "$DM" = 700 ] || warn account-dir "$NAME home mode is $DM; expected 700"
    if [ $CODEX_OK -eq 1 ]; then
      S=$(login_state "$D")
      if [ "$S" = none ]; then
        fail codex-login "account '$NAME' not logged in; the USER runs in Terminal: CODEX_HOME=$D codex login"
      else
        pass codex-login "account '$NAME' logged in using $S"
      fi
    fi
  done
  [ $FOUND -eq 1 ] || warn codex-account "no registered accounts yet (stage 4: grok-codex register-account personal)"
else
  info codex-account "no account directory yet at $ACC (created in stage 4)"
fi

# 5. Ports
check_port() {
  port_listener "$1"; rc=$?
  if [ $rc -ne 0 ]; then pass "$2" "127.0.0.1:$1 is free"; return; fi
  if have curl && curl -fsS --max-time 3 "http://127.0.0.1:$1$3" 2>/dev/null | grep -q '"ok"'; then
    pass "$2" "127.0.0.1:$1 already serves the bridge health check"
  else
    fail "$2" "port $1 is in use by another process; choose another port"
  fi
}
check_port "$PORT" bridge-port /healthz
check_port "$PROXY_PORT" proxy-port /codex/healthz

# 6. Tunnel and proxy options
T=''
have cloudflared && T="cloudflared"
have caddy && T="$T${T:+, }caddy"
have tailscale && T="$T${T:+, }tailscale"
if [ -n "$T" ]; then pass tunnel-tools "available: $T"
else warn tunnel-tools "no cloudflared/caddy found; brew install cloudflared caddy, or use your own HTTPS reverse proxy"; fi
if have caddy; then pass path-proxy "caddy available for the /codex path-stripping proxy"
else info path-proxy "no caddy; use BRIDGE_PATH_PREFIX=\"\" with a tunnel straight to port $PORT, or your own path-stripping proxy"; fi

# 7. Optional tools
if have tmux; then pass tmux "$(tmux -V 2>/dev/null) (for grok-codex attach)"; else warn tmux "optional; brew install tmux, or approve via: grok-codex terminal SESSION_ID"; fi

printf '== Summary: %d FAIL, %d WARN ==\n' "$FAILS" "$WARNS"
[ "$FAILS" -eq 0 ]
