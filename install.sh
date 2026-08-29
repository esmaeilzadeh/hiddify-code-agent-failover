#!/usr/bin/env bash
# One-click setup: tune Hiddify for Cursor Agent and start the auto-picker.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$ROOT_DIR/bin/hiddify-cursor-failover"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hiddify-cursor-failover.service"

ok() { printf '\n  ✓  %s\n' "$1"; }
say() { printf '\n%s\n' "$1"; }
die() {
  printf '\n  ✗  %s\n' "$1" >&2
  printf '      %s\n\n' "$2" >&2
  exit 1
}

chmod +x "$BIN" "$ROOT_DIR/install.sh" 2>/dev/null || true

cat <<'BANNER'

  Hiddify + Cursor  —  one-time setup
  ────────────────────────────────────
  English: Keep Hiddify open and connected. Type your password if asked.
  فارسی: هیدیفای را باز و وصل نگه دارید. اگر رمز خواست وارد کنید.

BANNER

if ! command -v python3 >/dev/null 2>&1; then
  die "Python 3 is missing." "نصب کنید: sudo apt install python3"
fi

if ! pgrep -x hiddify >/dev/null 2>&1; then
  die \
    "Hiddify is not running. Open Hiddify, click Connect, then run this again:" \
    "هیدیفای باز نیست. برنامه را باز کنید، Connect بزنید، بعد دوباره:  bash install.sh"
fi
ok "Hiddify is running / هیدیفای در حال اجراست"

say "1/4  Saving Hiddify access (password may be asked once)…"
say "     ذخیره دسترسی به هیدیفای (ممکن است یک بار رمز بخواهد)…"
if ! "$BIN" refresh-secret; then
  die \
    "Could not talk to Hiddify. Keep it connected and run:  bash install.sh" \
    "ارتباط با هیدیفای برقرار نشد. وصل بماند و دوباره install را اجرا کنید."
fi
ok "Access saved / دسترسی ذخیره شد"

say "2/4  Applying Cursor-friendly settings (optional but recommended)…"
say "     اعمال تنظیمات مناسب Cursor…"
if "$BIN" patch; then
  ok "Settings applied / تنظیمات اعمال شد"
else
  say "     (Skipped — auto-picker will still work / رد شد؛ انتخاب خودکار همچنان کار می‌کند)"
fi

say "3/4  Installing the background helper…"
say "     نصب برنامه کمکی در پس‌زمینه…"
mkdir -p "$(dirname "$UNIT_DST")"
cat >"$UNIT_DST" <<EOF
[Unit]
Description=Hiddify best-node failover for Cursor
After=network-online.target

[Service]
Type=simple
Environment=HIDDIFY_PATCH_NONINTERACTIVE=1
ExecStart=$BIN watch --skip-patch --interval 20 --bad-ms 1500 --fail-threshold 2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

if ! command -v systemctl >/dev/null 2>&1; then
  die "systemd is not available on this computer." "این سیستم systemd ندارد."
fi

systemctl --user daemon-reload
systemctl --user enable --now hiddify-cursor-failover.service >/dev/null
loginctl enable-linger "$USER" >/dev/null 2>&1 || true

sleep 2
if ! systemctl --user is-active --quiet hiddify-cursor-failover.service; then
  die \
    "Helper did not start. Keep Hiddify connected and run:  bash install.sh" \
    "کمکی بالا نیامد. هیدیفای را وصل نگه دارید و دوباره install را اجرا کنید."
fi
ok "Helper is running / برنامه کمکی روشن است"

cat <<'DONE'

  ────────────────────────────────────
  Done. You can close this window.
  تمام شد. این پنجره را ببندید.

  From now on:
  • Leave Hiddify open and connected.
  • The helper picks the best server for Cursor Agent in the background.
  • You do not need to change nodes by hand.

  از این به بعد:
  • هیدیفای را باز و وصل بگذارید.
  • برنامه کمکی خودش بهترین سرور را برای Agent انتخاب می‌کند.
  • لازم نیست نود را دستی عوض کنید.

  If Cursor Agent breaks after you restart Hiddify, run this file again:
  اگر بعد از بستن/باز کردن هیدیفای Agent خراب شد، همین فایل را دوباره اجرا کنید:
      bash install.sh

DONE
