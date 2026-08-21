#!/usr/bin/env bash
# Launch the monitor. Run this from a kitty window (it inherits KITTY_LISTEN_ON),
# or set KTM_SOCKET / kitty_socket in config.json to point at the control socket.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m kitty_tab_monitor "$@"
