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

if grep -qE '^\s*allow_remote_control\s+(yes|socket-only)' "$CONF"; then
  echo "ok: allow_remote_control already enabled"
elif grep -qE '^\s*allow_remote_control' "$CONF"; then
  echo "WARNING: allow_remote_control is set to something other than yes/socket-only."
  echo "         Edit $CONF and set: allow_remote_control yes"
else
  echo 'allow_remote_control yes' >> "$CONF"
  echo "added: allow_remote_control yes"
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
  2) export OPENAI_API_KEY=sk-...        # put this in your shell rc
  3) In kitty, open a dedicated tab and name it so the monitor ignores itself:
        kitty @ set-tab-title tab-monitor
  4) Watch first (types nothing):
        $HERE/run.sh --dry-run
     When the decisions look right, go live:
        $HERE/run.sh
EOF
