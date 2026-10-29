#!/bin/bash
# Set the app up as an always-on service on this Mac (run from the cloned repo).
#   scripts/install_macmini.sh [--lan] [path/to/ebay-lister-bundle.tar]
#   --lan   listen on all interfaces so other devices on the network can open it
# Safe to re-run: it rebuilds the venv packages and reloads the service.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.ebaylister.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HOST="127.0.0.1"
BUNDLE=""
for arg in "$@"; do
  case "$arg" in
    --lan) HOST="0.0.0.0" ;;
    *) BUNDLE="$arg" ;;
  esac
done
cd "$REPO"

# 1. Python 3.10+ (macOS's /usr/bin/python3 is 3.9 — install Homebrew or python.org Python).
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
[ -n "$PY" ] || { echo "Need Python 3.10+: brew install python  (or python.org installer)"; exit 1; }
echo "Python: $PY ($("$PY" --version))"

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# 2. Restore data from the old Mac.
if [ -n "$BUNDLE" ]; then
  if [ -f products.db ]; then
    backup="products.db.pre-restore-$(date +%Y%m%d-%H%M%S)"
    mv products.db "$backup"; rm -f products.db-wal products.db-shm
    echo "Existing DB moved to $backup"
  fi
  tar -xf "$BUNDLE" -C "$REPO"
  if [ -d _home/claude-memory ]; then
    mem="$HOME/.claude/projects/$(printf '%s' "$REPO" | sed 's/[^A-Za-z0-9]/-/g')/memory"
    mkdir -p "$mem" && cp -Rn _home/claude-memory/. "$mem/"
    echo "Claude memory -> $mem"
  fi
  if [ -f _home/.ebay_mobile_auth.json ] && [ ! -f "$HOME/.ebay_mobile_auth.json" ]; then
    cp _home/.ebay_mobile_auth.json "$HOME/"
  fi
  rm -rf _home
  chmod 600 settings.json 2>/dev/null || true
  echo "Restored: $(sqlite3 products.db 'select count(*) from products') products, $(ls photos | wc -l | tr -d ' ') photo folders"
fi

# 3. launchd starts jobs with a bare PATH; the AI backend shells out to `claude`,
#    which is a Node script, so both claude's and node's folders must be on it.
SERVICE_PATH="$REPO/.venv/bin"
for tool in claude node; do
  if p="$(command -v "$tool")"; then SERVICE_PATH="$SERVICE_PATH:$(dirname "$p")"; fi
done
SERVICE_PATH="$SERVICE_PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
command -v claude >/dev/null || echo "WARNING: claude CLI not found — install Claude Code and log in, or put an Anthropic API key in Settings."

mkdir -p logs "$HOME/Library/LaunchAgents"
.venv/bin/python - "$PLIST" "$LABEL" "$REPO" "$SERVICE_PATH" "$HOST" <<'EOF'
import plistlib, sys
plist, label, repo, path, host = sys.argv[1:]
with open(plist, "wb") as f:
    plistlib.dump({
        "Label": label,
        "ProgramArguments": [f"{repo}/.venv/bin/python", f"{repo}/app.py"],
        "WorkingDirectory": repo,
        "EnvironmentVariables": {"PATH": path, "PYTHONUNBUFFERED": "1", "LISTER_HOST": host},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": f"{repo}/logs/app.log",
        "StandardErrorPath": f"{repo}/logs/app.log",
    }, f)
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# 4. Check it came up.
for _ in $(seq 1 20); do
  if curl -fs -o /dev/null "http://127.0.0.1:8090/"; then
    echo "Running: http://localhost:8090"
    [ "$HOST" = "0.0.0.0" ] && echo "From other devices: http://$(scutil --get LocalHostName).local:8090"
    echo "Logs: $REPO/logs/app.log"
    exit 0
  fi
  sleep 1
done
echo "Service didn't answer on :8090 — see $REPO/logs/app.log"
exit 1
