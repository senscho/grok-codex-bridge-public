#!/usr/bin/env bash
# Print (stdout only, writes nothing) a per-user launchd plist for the bridge
# daemon or the optional Caddy path proxy. macOS /bin/bash 3.2 compatible.
#
# Usage: bash scripts/render_launchd.sh bridge|caddy [--caddyfile FILE]
# Install: bash scripts/render_launchd.sh bridge > ~/Library/LaunchAgents/com.grok-codex-bridge.daemon.plist
set -eu

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
KIND=${1:-}
[ $# -gt 0 ] && shift
CADDYFILE="$REPO_DIR/examples/Caddyfile"
while [ $# -gt 0 ]; do
  case "$1" in
    --caddyfile) CADDYFILE=$2; shift 2 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

LOG_DIR="$HOME/Library/Logs/grok-codex"
# launchd starts with a minimal PATH; keep the directories that hold uv and codex.
LPATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
for t in uv codex caddy; do
  d=$(dirname "$(command -v "$t" 2>/dev/null || echo /usr/bin/x)")
  case ":$LPATH:" in *":$d:"*) ;; *) LPATH="$d:$LPATH" ;; esac
done

xml() { printf '%s' "$1" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g'; }

case "$KIND" in
  bridge)
    LABEL=com.grok-codex-bridge.daemon
    BIN=$(command -v uv) || { echo 'uv not found' >&2; exit 1; }
    ARGS="<string>$(xml "$BIN")</string><string>run</string><string>--locked</string><string>--project</string><string>$(xml "$REPO_DIR")</string><string>grok-codex</string><string>--config</string><string>$(xml "$REPO_DIR/.env")</string><string>daemon</string>"
    ;;
  caddy)
    LABEL=com.grok-codex-bridge.caddy
    BIN=$(command -v caddy) || { echo 'caddy not found; brew install caddy' >&2; exit 1; }
    ARGS="<string>$(xml "$BIN")</string><string>run</string><string>--config</string><string>$(xml "$CADDYFILE")</string><string>--adapter</string><string>caddyfile</string>"
    ;;
  *) printf 'Usage: %s bridge|caddy [--caddyfile FILE]\n' "$0" >&2; exit 2 ;;
esac

cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>$ARGS</array>
  <key>WorkingDirectory</key><string>$(xml "$REPO_DIR")</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$(xml "$LPATH")</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$(xml "$LOG_DIR/$KIND.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$LOG_DIR/$KIND.log")</string>
</dict>
</plist>
PLIST
