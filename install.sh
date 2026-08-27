#!/usr/bin/env bash
# Install / refresh the user systemd unit for this checkout.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC="$ROOT_DIR/systemd/hiddify-cursor-failover.service"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hiddify-cursor-failover.service"
BIN="$ROOT_DIR/bin/hiddify-cursor-failover"

chmod +x "$BIN" "$ROOT_DIR/install.sh"
mkdir -p "$(dirname "$UNIT_DST")"

sed "s|^ExecStart=.*|ExecStart=$BIN watch --interval 20 --bad-ms 1500 --fail-threshold 2|" \
  "$UNIT_SRC" >"$UNIT_DST"

systemctl --user daemon-reload
echo "Installed unit -> $UNIT_DST"
echo
echo "Next:"
echo "  $BIN refresh-secret"
echo "  $BIN once"
echo "  systemctl --user enable --now hiddify-cursor-failover.service"
