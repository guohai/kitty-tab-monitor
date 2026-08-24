#!/usr/bin/env bash
# Enable kitty remote control so the monitor can read tabs and type into them.
set -euo pipefail

CONF_DIR="${KITTY_CONFIG_DIRECTORY:-$HOME/.config/kitty}"
CONF="$CONF_DIR/kitty.conf"
HERE="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$CONF_DIR"
touch "$CONF"
cp "$CONF" "$CONF.ktm-backup.$(date +%s)"
echo "backed up $CONF"

if grep -qE '^\s*allow_remote_control\s+(yes|socket-only|password)' "$CONF"; then
  echo "ok: allow_remote_control already enabled"
elif grep -qE '^\s*allow_remote_control' "$CONF"; then
  echo "WARNING: allow_remote_control is not yes, socket-only, or password."
  echo "         Edit $CONF and set: allow_remote_control socket-only"
else
  echo 'allow_remote_control socket-only' >> "$CONF"
  echo "added: allow_remote_control socket-only"
fi

if grep -qE '^\s*listen_on' "$CONF"; then
  echo "ok: listen_on already set"
else
  echo 'listen_on unix:/tmp/kitty-{kitty_pid}' >> "$CONF"
  echo "added: listen_on unix:/tmp/kitty-{kitty_pid}"
fi

chmod +x "$HERE/run.sh"

cat <<EOF

Done editing kitty.conf.

Next steps:
  1) Fully quit and reopen kitty (remote-control changes need a restart).
  2) Put your credentials in .env (next to config.json):
        OPENAI_API_KEY=...      OPENAI_BASE_URL=...   # gateway or api.openai.com/v1
     If kitty uses allow_remote_control password, also set:
        KITTY_RC_PASSWORD=...
  3) In kitty, open a dedicated tab and name it so the monitor ignores itself:
        kitty @ set-tab-title tab-monitor
  4) Watch first (types nothing):
        $HERE/run.sh --dry-run
     When the decisions look right, go live:
        $HERE/run.sh
EOF
