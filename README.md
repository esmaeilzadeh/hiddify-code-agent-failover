# Hiddify Cursor Failover

Two-step toolkit for **Cursor Agent** over **Hiddify** on Linux:

1. **Patch** Hiddify prefs / TUN for stable Agent streams (DNS strategy, MTU, stack, mux/fragment/WARP off)
2. **Failover** — pick the best node in the current subscription (prefer Reality TCP), then auto-switch when quality drops

Uses Hiddify’s Clash-compatible API. No extra Python deps (stdlib only).

---

## The problem / مشکل

### English

Cursor’s **Agent** (and other long-lived AI streams) is not a normal short HTTP request. It opens a **long streaming connection** (SSE / chunked responses) to Cursor’s servers and keeps it open while the agent thinks, reads files, and streams tokens back.

Over a typical Hiddify / VPN setup that connection often fails in ways that look random:

- Agent starts, then **stalls mid-stream** or disconnects with no clear error
- Chat / Agent works for a few seconds, then hangs until you retry
- Some subscription nodes feel fine in a browser speed test, but **kill Agent streams**
- Switching nodes by hand is slow, and a “fast” node can still be a bad fit for Cursor

Common causes when tunneling Cursor through Hiddify:

| Cause | What happens to Agent |
|-------|------------------------|
| **Mux / TLS fragment / WARP** | Extra buffering or packet tricks break long streams |
| **Large MTU / gVisor TUN stack** | Silent drops or stalled TCP over the tunnel |
| **Messy DNS / fake-DNS / routing** | Unstable resolve path to `*.cursor.sh` |
| **WebSocket / HTTP / gRPC / CDN transports** | Fine for browsing; bad for long SSE — they buffer or idle-timeout streams |
| **A weak or overloaded node** | Latency spikes → Cursor API looks “dead” until you switch |

So the real problem is not “VPN is slow.” It is: **Hiddify defaults + the wrong node transport are hostile to Cursor’s long Agent streams**, and there is no built-in loop that keeps re-picking a Cursor-friendly node when quality drops.

This repo fixes that by:

1. Applying a **Cursor-oriented Hiddify patch** (prefs/TUN that favor stable streams)
2. Preferring **Reality + TCP** outbounds and **URL-testing against Cursor’s API**
3. **Auto-failing over** when latency or failures get bad (`watch` mode / systemd)

### فارسی

**Agent** در Cursor یک درخواست کوتاه معمولی نیست. یک **اتصال استریم طولانی** به سرورهای Cursor باز می‌کند و تا وقتی Agent کار می‌کند (فکر کردن، خواندن فایل، استریم توکن) این اتصال باز می‌ماند.

روی خیلی از تنظیمات Hiddify / VPN این اتصال به شکل‌های آزاردهنده قطع می‌شود:

- Agent شروع می‌شود، وسط کار **می‌ایستد** یا بدون خطای واضح قطع می‌شود
- چند ثانیه کار می‌کند، بعد hang می‌شود تا دوباره Retry کنید
- بعضی نودها در تست مرورگر «خوب» به‌نظر می‌رسند، ولی **استریم Agent را خراب می‌کنند**
- عوض کردن دستی نود وقت‌گیر است و نود «سریع» لزوماً برای Cursor مناسب نیست

دلایل رایج وقتی Cursor از تونل Hiddify رد می‌شود:

| علت | اثر روی Agent |
|-----|----------------|
| **Mux / TLS fragment / WARP** | بافر اضافه یا دستکاری پکت، استریم طولانی را می‌شکند |
| **MTU بزرگ / استک gVisor** | دراپ بی‌صدا یا گیر کردن TCP داخل تونل |
| **DNS شلوغ / fake-DNS / routing پیچیده** | مسیر resolve به `*.cursor.sh` ناپایدار می‌شود |
| **ترنسپورت‌های WS / HTTP / gRPC / CDN** | برای وب‌گردی اوکی‌اند؛ برای SSE طولانی بد — بافر یا idle-timeout |
| **نود ضعیف یا شلوغ** | پرش latency → API کورسور «مرده» به‌نظر می‌رسد تا نود عوض شود |

پس مشکل اصلی فقط «کند بودن VPN» نیست؛ مشکل این است که **تنظیمات پیش‌فرض Hiddify + نود/ترنسپورت نامناسب با استریم طولانی Agent سازگار نیست**، و حلقه‌ای برای انتخاب دوباره نود مناسب Cursor وقتی کیفیت افت می‌کند وجود ندارد.

این ریپو همان را حل می‌کند:

1. **پچ مخصوص Cursor** روی prefs/TUN در Hiddify (به نفع استریم پایدار)
2. اولویت به نودهای **Reality + TCP** و **تست واقعی روی API کورسور**
3. **failover خودکار** وقتی latency یا خطا زیاد شد (`watch` / systemd)

---

## Requirements

- Linux
- Hiddify running (often as root) with Clash API enabled
- `python3`, `sudo` (for root prefs / secret cache), `ss`, `systemctl` (optional)

## Quick start

```bash
cd ~/Tools/failover
chmod +x bin/hiddify-cursor-failover install.sh

# one-time: cache Clash API secret from root Hiddify config
./bin/hiddify-cursor-failover refresh-secret

# patch Hiddify prefs/TUN for Cursor (needs sudo if Hiddify runs as root)
./bin/hiddify-cursor-failover patch

# reconnect Hiddify once so prefs rebuild the core config, then:
./bin/hiddify-cursor-failover once

# continuous monitor
./bin/hiddify-cursor-failover watch
```

`once` / `watch` run **patch first** by default, then URL-test nodes.

### systemd user service

```bash
./install.sh
./bin/hiddify-cursor-failover refresh-secret
./bin/hiddify-cursor-failover patch   # do this in a terminal (sudo)
systemctl --user enable --now hiddify-cursor-failover.service
```

Keep **Hiddify running**. This service only talks to its API; it does not replace Hiddify.

After `patch`, reconnect Hiddify (or restart it) so MTU/TUN prefs apply to a freshly built config.

## What `patch` changes

Writes Cursor-friendly Flutter prefs (root + user `shared_preferences.json` when reachable):

| Key | Value | Why |
|-----|-------|-----|
| `flutter.service-mode` | `vpn` | full TUN path |
| `flutter.mtu` | `1400` | avoid silent drops from huge MTU |
| `flutter.tun-implementation` | `system` | avoid gvisor stream issues |
| `flutter.bypass-lan` | `true` | keep LAN local |
| `flutter.enable-mux` | `false` | mux breaks long streams |
| `flutter.enable-tls-fragment` | `false` | fragmenting hurts Agent |
| `flutter.enable-warp` | `false` | extra hop / buffering |
| `flutter.remote-dns-domain-strategy` | `ipv4_only` | stable DNS to Cursor |
| `flutter.direct-dns-domain-strategy` | `ipv4_only` | same |
| `flutter.remote-dns-address` | `1.1.1.1` | predictable remote resolver |
| `flutter.direct-dns-address` | `1.1.1.1` | predictable direct resolver |
| `flutter.enable-fake-dns` | `false` | fewer DNS surprises |
| `flutter.enable-dns-routing` | `false` | keep routing simple |
| `flutter.resolve-destination` | `false` | leave resolve to tunnel |

Also patches live `current-config.json` TUN `mtu`/`stack` when writable, and classifies **Reality TCP** outbounds as preferred for failover.

## How failover picks a node

1. Skip subscription banner rows (`User:`, `Used:`, …)
2. Prefer **Reality + TCP** outbounds (skip `ws` / `http` / `xhttp` / `grpc` / CDN-style transports that buffer SSE)
3. URL-test against `https://api2.cursor.sh/`
4. Select the lowest-latency working node in the `select` group
5. In `watch` mode: recheck on an interval; switch after bad latency or repeated failures

Use `--all-nodes` to disable the Reality TCP preference. Use `--skip-patch` to only run selection.

## Config / secrets

| Path | Purpose |
|------|---------|
| `~/.config/hiddify-cursor/clash_secret` | Cached API secret (mode 600) |
| `~/.config/hiddify-cursor/clash_port` | Cached Clash API port |

Do **not** commit these files. Re-run `refresh-secret` after Hiddify restarts if the secret rotates.

## CLI

```text
./bin/hiddify-cursor-failover refresh-secret
./bin/hiddify-cursor-failover patch
./bin/hiddify-cursor-failover once
./bin/hiddify-cursor-failover watch --interval 20 --bad-ms 1500 --fail-threshold 2
```

Useful flags:

- `--skip-patch` — selection only
- `--all-nodes` — do not restrict to Reality TCP
- `--group select`
- `--test-url https://api2.cursor.sh/`
- `--api http://127.0.0.1:16757`

## Layout

```text
failover/
  hiddify_cursor_failover.py   # Clash API + once/watch
  hiddify_cursor_patch.py      # Hiddify prefs / TUN patch
  bin/hiddify-cursor-failover  # CLI wrapper
  systemd/…service
  install.sh
  README.md
```

## License

MIT
