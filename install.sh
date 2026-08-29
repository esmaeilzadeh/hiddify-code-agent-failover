#!/usr/bin/env bash
# Install / refresh the user systemd unit for this checkout.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hiddify-cursor-failover.service"
BIN="$ROOT_DIR/bin/hiddify-cursor-failover"

chmod +x "$BIN" "$ROOT_DIR/install.sh"
mkdir -p "$(dirname "$UNIT_DST")"

cat >"$UNIT_DST" <<EOF
[Unit]
Description=Hiddify best-node failover for Cursor
After=network-online.target

[Service]
Type=simple
Environment=HIDDIFY_PATCH_NONINTERACTIVE=1
# Run: $BIN patch   (sudo once), then reconnect Hiddify.
ExecStart=$BIN watch --skip-patch --interval 20 --bad-ms 1500 --fail-threshold 2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
echo "Installed unit -> $UNIT_DST"
echo
echo "Next:"
echo "  $BIN refresh-secret"
echo "  $BIN patch          # sudo once; then reconnect Hiddify"
echo "  $BIN once"
echo "  systemctl --user enable --now hiddify-cursor-failover.service"
