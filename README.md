# Hiddify Cursor Failover

<p align="center">
  <img src="logo.png" alt="Hiddify Cursor Failover" width="280">
</p>

> ### Bypass LAN / corp IPs & DNS in Hiddify **TUN** mode
>
> Hiddify rebuilds `current-config.json` on every Connect, so live JSON patches alone do not stick. This project writes durable excludes into Hiddify prefs + `route_rule.proto` (and curl-safe `NO_PROXY`) from `.env` — so chosen IPs and names stay **DIRECT** off the VPN after reconnect.
>
> ```bash
> cp example.env .env   # set HIDDIFY_EXCLUDE_IPS / HIDDIFY_EXCLUDE_DNS / HIDDIFY_DIRECT_DNS
> ./bin/hiddify-cursor-failover bypass --apply
> ```
> Then quit/reconnect Hiddify. Details: [Bypass IPs and DNS](#bypass-ips-and-dns--عبور-مستقیم-ip-و-dns).
>
> **فارسی:** در حالت TUN، فقط پچ کردن JSON زنده کافی نیست (با Connect پاک می‌شود). این ابزار excludeها را در prefs و `route_rule.proto` می‌نویسد تا IP و DNS انتخابی روی VPN نروند. `.env` را بسازید، `bypass --apply` بزنید، هیدیفای را قطع/وصل کنید.

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
./bin/hiddify-cursor-failover bypass --apply
```

Flags: `--skip-patch`, `--all-nodes`, `--group select`, `--test-url https://api2.cursor.sh/`

Hiddify data dirs:

- Root (`sudo hiddify`): `/root/.local/share/app.hiddify.com/`
- User / older: `~/.local/share/hiddify/` or `~/.local/share/app.hiddify.com/`

Cached API secret (do not commit): `~/.config/hiddify-cursor/clash_secret`

Bypass excludes: `.env` (`HIDDIFY_EXCLUDE_IPS` / `HIDDIFY_EXCLUDE_DNS`). Copy `example.env` to `.env`. Optional extras: `~/.config/hiddify-cursor/bypass.json`

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
| `flutter.direct-dns-address` | office DNS when set / else `1.1.1.1` | split-horizon (`HIDDIFY_DIRECT_DNS`); else public |
| `flutter.enable-fake-dns` | `false` | fewer DNS surprises |
| `flutter.enable-dns-routing` | `true` | region + custom route rules → dns-direct |
| `flutter.resolve-destination` | `false` | leave resolve to tunnel |

Hiddify has no “agent long connection” switch. Patch while the app is **quit** if you want Config Options to show these values.

### Bypass IPs and DNS / عبور مستقیم IP و DNS

Excludes are read from **`.env`** (`HIDDIFY_EXCLUDE_IPS`, `HIDDIFY_EXCLUDE_DNS`). Copy the template and edit:

```bash
cp example.env .env
# edit HIDDIFY_EXCLUDE_IPS / HIDDIFY_EXCLUDE_DNS
./bin/hiddify-cursor-failover bypass --apply
```

`patch` / `once` / `watch` re-apply the same list. Optional CLI extras still work (`--ip`, `--dns`) and are stored in `~/.config/hiddify-cursor/bypass.json` on top of `.env`.

```bash
./bin/hiddify-cursor-failover bypass                          # show merged list
./bin/hiddify-cursor-failover bypass --file example.env --apply
./bin/hiddify-cursor-failover bypass --ip '192.168.1.20'      # extra on top of .env
```

Wildcards: `192.168.*`, `10.*.*.*` (trailing `*` only, stored as CIDR); `*.example.com`, `*cdn*`, `api.*.internal`. `--dns *` matches every name; `--ip *` is `0.0.0.0/0`. After changing `.env`, **reconnect Hiddify** so the core reloads routing.

`bypass --apply` / `watch` also merge those names into **`NO_PROXY`** (curl-safe `.ir`, not `*.ir`) under `~/.config/environment.d/90-hiddify-cursor-bypass.conf`. Restart Cursor or open a new terminal so Agent picks it up. `*cdn*` and `api.*.internal` stay TUN-only; they cannot be expressed in `NO_PROXY`.

Internal names (split-horizon, e.g. `intranet.company.local`) need **office DNS**, not 1.1.1.1. Set `HIDDIFY_DIRECT_DNS=192.168.1.1,...` or leave it empty to auto-pick RFC1918 resolvers. Those IPs are excluded from TUN so the resolver sees your LAN address. Hiddify running as root: `bypass --apply` uses sudo to write the live config; then reconnect Hiddify.

`bypass --apply` also teaches Hiddify-native sources that survive Connect rebuilds:

- `shared_preferences.json`: `direct-dns-address` → office DNS, `enable-dns-routing`, `bypass-lan`
- `route_rule.proto`: DIRECT rules for your `.env` domains/IPs

Quit/reopen Hiddify (or Disconnect→Connect) after apply so the core regenerates from those.

IPها و نام‌های DNS را در `.env` بگذارید (از `example.env` کپی کنید). بعد از تغییر، هیدیفای را قطع/وصل کنید.

### How failover picks a node

1. Skip subscription banner rows
2. Prefer Reality + TCP (skip ws / http / grpc / CDN transports)
3. URL-test `https://api2.cursor.sh/`
4. Select the lowest-latency working node
5. In watch mode, switch after bad latency or repeated failures

---

## License

MIT
