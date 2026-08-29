# Hiddify Cursor Failover

<p align="center">
  <img src="logo.png" alt="Hiddify Cursor Failover" width="280">
</p>

Keeps **Cursor Agent** stable over **Hiddify** on Linux: it picks a good server and switches automatically when the connection gets bad.

---

## Install (one time) / نصب (یک‌بار)

### English

1. Open **Hiddify** and click **Connect**. Leave it connected.
2. Open a terminal **in this folder** and run:

```bash
bash install.sh
```

3. If it asks for your password, type it and press Enter.
4. Wait until you see **Done**. You can close the terminal.

That is all. Leave Hiddify open. The helper runs in the background and chooses the best server for Agent.

If Agent breaks after you close and reopen Hiddify, run `bash install.sh` again.

### فارسی

1. **هیدیفای** را باز کنید و **Connect** بزنید. وصل بماند.
2. در **همین پوشه** ترمینال باز کنید و این را بزنید:

```bash
bash install.sh
```

3. اگر رمز خواست، رمز سیستم را بزنید و Enter.
4. تا پیام **Done** صبر کنید. بعد ترمینال را ببندید.

تمام. هیدیفای را باز و وصل بگذارید. برنامه کمکی در پس‌زمینه بهترین سرور را برای Agent انتخاب می‌کند.

اگر بعد از بستن و باز کردن هیدیفای، Agent خراب شد، دوباره `bash install.sh` را اجرا کنید.

---

## The problem / مشکل

### English

Cursor’s **Agent** is not a short webpage request. It keeps a **long stream** open to Cursor’s servers.

On many Hiddify setups that stream stalls, hangs, or dies even when browsing feels fine. Wrong node, mux/fragment/WARP, or round-robin hopping mid-stream are common causes.

This tool:

1. Optionally applies Cursor-friendly Hiddify settings
2. Tests nodes against Cursor’s API (prefers Reality + TCP)
3. Auto-switches when quality drops

### فارسی

درخواست **Agent** در Cursor یک درخواست کوتاه نیست؛ یک **استریم طولانی** است.

روی خیلی از تنظیمات هیدیفای این استریم وسط کار می‌ایستد، حتی اگر مرورگر «خوب» باشد.

این ابزار نود مناسب Cursor را انتخاب می‌کند و وقتی کیفیت افت کرد عوض می‌کند.

---

## Advanced / برای افراد فنی

Needs Linux, Hiddify running, `python3`, and `sudo` if Hiddify was started with sudo.

```bash
./bin/hiddify-cursor-failover refresh-secret
./bin/hiddify-cursor-failover patch
./bin/hiddify-cursor-failover once
./bin/hiddify-cursor-failover watch
```

Flags: `--skip-patch`, `--all-nodes`, `--group select`, `--test-url https://api2.cursor.sh/`

Hiddify data dirs:

- Root (`sudo hiddify`): `/root/.local/share/app.hiddify.com/`
- User / older: `~/.local/share/hiddify/` or `~/.local/share/app.hiddify.com/`

Cached API secret (do not commit): `~/.config/hiddify-cursor/clash_secret`

### What `patch` changes

| Key | Value | Why |
|-----|-------|-----|
| `flutter.service-mode` | `vpn` | full TUN path |
| `flutter.mtu` | `1400` | avoid silent drops from huge MTU |
| `flutter.tun-implementation` | `system` | avoid gvisor stream issues |
| `flutter.bypass-lan` | `true` | keep LAN local |
| `flutter.enable-mux` | `false` | mux breaks long streams |
| `flutter.enable-tls-fragment` | `false` | fragmenting hurts Agent |
| `flutter.enable-warp` | `false` | extra hop / buffering |
| `flutter.balancer-strategy` | `sticky-sessions` | stop round-robin mid-stream |
| `flutter.connection-test-url` | `https://api2.cursor.sh/` | delay tests match Cursor |
| `flutter.remote-dns-domain-strategy` | `ipv4_only` | stable DNS to Cursor |
| `flutter.direct-dns-domain-strategy` | `ipv4_only` | same |
| `flutter.remote-dns-address` | `1.1.1.1` | predictable remote resolver |
| `flutter.direct-dns-address` | `1.1.1.1` | predictable direct resolver |
| `flutter.enable-fake-dns` | `false` | fewer DNS surprises |
| `flutter.enable-dns-routing` | `false` | keep routing simple |
| `flutter.resolve-destination` | `false` | leave resolve to tunnel |

Hiddify has no “agent long connection” switch. Patch while the app is **quit** if you want Config Options to show these values.

### How failover picks a node

1. Skip subscription banner rows
2. Prefer Reality + TCP (skip ws / http / grpc / CDN transports)
3. URL-test `https://api2.cursor.sh/`
4. Select the lowest-latency working node
5. In watch mode, switch after bad latency or repeated failures

---

## License

MIT
