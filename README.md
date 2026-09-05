# Hiddify Cursor Failover

<p align="center">
  <img src="logo.png" alt="Hiddify Cursor Failover" width="280">
</p>

An open-source **Linux** helper so **Cursor Agent** stays stable over **Hiddify**.

ابزار اوپن‌سورس لینوکس برای پایدار ماندن **Cursor Agent** روی **هیدیفای**.

---

## Does Cursor Agent keep dropping over VPN?

Cursor’s **Agent** is not a short, ordinary request. It opens a **long stream** to Cursor’s servers and keeps that connection open the whole time Agent is working (thinking, reading files, streaming tokens).

On many **Hiddify / VPN** setups that connection fails in annoying ways:

- Agent starts, then stops mid-work or disconnects with no clear error
- It works for a few seconds, then hangs until you Retry
- Some nodes look “fine” in a browser test, but they break the Agent stream
- Switching nodes by hand is slow, and a “fast” node is not always good for Cursor

### فارسی — آیا اتصال Cursor Agent موقع کار با VPN مدام قطع می‌شود؟

درخواست **Agent** در Cursor یک درخواست کوتاه معمولی نیست. یک اتصال **استریم طولانی** به سرورهای Cursor باز می‌کند و تا وقتی Agent کار می‌کند (فکر کردن، خواندن فایل، استریم توکن) این اتصال باز می‌ماند.

روی خیلی از تنظیمات **Hiddify / VPN** این اتصال به شکل‌های آزاردهنده قطع می‌شود:

- Agent شروع می‌شود، وسط کار می‌ایستد یا بدون خطای واضح قطع می‌شود
- چند ثانیه کار می‌کند، بعد hang می‌شود تا دوباره Retry کنید
- بعضی نودها در تست مرورگر «خوب» به‌نظر می‌رسند، ولی استریم Agent را خراب می‌کنند
- عوض کردن دستی نود وقت‌گیر است و نود «سریع» لزوماً برای Cursor مناسب نیست

---

## What this project does

**Hiddify Cursor Failover** has three jobs:

1. **Patch** — set Hiddify prefs / TUN for long Agent streams (DNS, MTU, stack; turn off mux / fragment / WARP)
2. **Failover** — test nodes (prefer Reality + TCP), pick the best one, and switch automatically when quality drops
3. **Bypass local network** — keep chosen LAN / corp IPs and DNS **DIRECT** (off the VPN) in TUN mode, and keep those excludes after Connect rebuilds the config

It uses Hiddify’s Clash-compatible API. Standard-library Python only — no extra dependencies. A systemd user service can keep watching in the background.

This does **not** replace or rewrite the Hiddify app. You run this repo; it writes Hiddify’s own prefs/config and can be applied again anytime.

### فارسی — این پروژه چه می‌کند

**Hiddify Cursor Failover** سه کار می‌کند:

1. **Patch** — تنظیم prefs/TUN در هیدیفای برای استریم‌های طولانی Agent (DNS، MTU، stack؛ خاموش کردن mux / fragment / WARP)
2. **Failover** — تست نودها (ترجیح Reality TCP)، انتخاب بهترین نود، و سوئیچ خودکار وقتی کیفیت افت می‌کند
3. **Bypass شبکهٔ محلی** — IP و DNS داخلی/شرکتی انتخابی را **DIRECT** نگه می‌دارد (از VPN خارج) و بعد از Connect هم excludeها را از نو می‌سازد

از API سازگار با Clash در هیدیفای استفاده می‌کند. فقط کتابخانهٔ استاندارد پایتون — بدون dependency اضافه. سرویس systemd هم برای مانیتورینگ مداوم دارد.

این ابزار باینری هیدیفای را عوض نمی‌کند. همین پروژه را اجرا می‌کنید؛ تنظیمات را در فایل‌های خود هیدیفای می‌نویسد و هر وقت بخواهید دوباره اعمال می‌شود.

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

تمام. هیدیفای را باز و وصل بگذارید. برنامهٔ کمکی در پس‌زمینه بهترین سرور را برای Agent انتخاب می‌کند.

اگر بعد از بستن و باز کردن هیدیفای، Agent خراب شد، دوباره `bash install.sh` را اجرا کنید.

---

## Do office / LAN sites die as soon as Hiddify TUN is on?

Hiddify **TUN** is a system-wide tunnel. Almost every packet — including your **LAN**, **office intranet**, and **office DNS** — can be pulled into the VPN.

That is not the same as “the VPN is slow.” Local things stop working in confusing ways:

- Internal names (`intranet.company.local`, a region suffix like `*.ir` on a split-horizon network) return **NXDOMAIN** or the **wrong public IP**, because the query went to `1.1.1.1` instead of the office resolver
- The office DNS IP itself (something like `192.168.1.1`) is routed **via `tun0`**, so even the right resolver is unreachable
- `curl` / Agent still go through `127.0.0.1:12334` because `NO_PROXY` has `*.company.local` — curl **ignores** that form; it needs `.company.local`
- You patch the live `current-config.json`, reconnect, and the excludes are **gone**: Hiddify **rebuilds** that file on every Connect

Hiddify’s own “bypass LAN” switch is often not enough on Linux with `strict_route` / `auto_route`. You need durable DIRECT rules (IPs + DNS) that survive Connect, office DNS for split-horizon names, and a curl-safe `NO_PROXY`.

**What this feature does**

1. You list excludes in `.env` (`HIDDIFY_EXCLUDE_IPS`, `HIDDIFY_EXCLUDE_DNS`, optional `HIDDIFY_DIRECT_DNS`)
2. `bypass --apply` writes them into Hiddify **prefs** + **`route_rule.proto`** (not only the live JSON)
3. It also writes curl-safe `NO_PROXY` (`.ir`, not `*.ir`)
4. After you quit/reconnect Hiddify, Connect **regenerates** the same DIRECT excludes

```bash
cp example.env .env
# edit HIDDIFY_EXCLUDE_IPS, HIDDIFY_EXCLUDE_DNS, optional HIDDIFY_DIRECT_DNS
./bin/hiddify-cursor-failover bypass --apply
```

Then quit and reopen Hiddify (or Disconnect → Connect). Wildcards: `192.168.*`, `*.company.local`. See `example.env`.

### فارسی — آیا با روشن شدن TUN هیدیفای، شبکهٔ محلی/شرکت از کار می‌افتد؟

حالت **TUN** هیدیفای یک تونل سراسری است. تقریباً همهٔ بسته‌ها — از جمله **LAN**، **اینترانت شرکت** و **DNS اداری** — ممکن است داخل VPN بروند.

این با «VPN کند است» فرق دارد. چیزهای محلی به شکل گیج‌کننده‌ای خراب می‌شوند:

- نام‌های داخلی (`intranet.company.local` یا پسوند منطقه‌ای مثل `*.ir` روی شبکهٔ split-horizon) **NXDOMAIN** می‌گیرند یا IP عمومی اشتباه برمی‌گردد، چون query به `1.1.1.1` رفته نه به DNS دفتر
- خود IP مربوط به DNS دفتر (مثلاً `192.168.1.1`) از مسیر **`tun0`** می‌رود؛ حتی resolver درست هم در دسترس نیست
- `curl` / Agent هنوز از `127.0.0.1:12334` می‌روند چون در `NO_PROXY` مقدار `*.company.local` است — curl این شکل را **نمی‌فهمد**؛ باید `.company.local` باشد
- JSON زندهٔ `current-config.json` را پچ می‌کنید، دوباره Connect می‌زنید، و excludeها **ناپدید** می‌شوند: هیدیفای آن فایل را در هر Connect **از نو می‌سازد**

گزینهٔ خود هیدیفای برای «bypass LAN» روی لینوکس با `strict_route` / `auto_route` اغلب کافی نیست. به قانون‌های DIRECT پایدار (IP + DNS) که بعد از Connect بمانند، DNS دفتر برای نام‌های split-horizon، و `NO_PROXY` سازگار با curl نیاز دارید.

**این قابلیت چه می‌کند**

1. excludeها را در `.env` می‌نویسید (`HIDDIFY_EXCLUDE_IPS`، `HIDDIFY_EXCLUDE_DNS`، در صورت نیاز `HIDDIFY_DIRECT_DNS`)
2. `bypass --apply` آن‌ها را در **prefs** و **`route_rule.proto`** هیدیفای می‌نویسد (نه فقط JSON زنده)
3. `NO_PROXY` سازگار با curl را هم می‌نویسد (`.ir` نه `*.ir`)
4. بعد از قطع/وصل هیدیفای، Connect همان excludeهای DIRECT را **دوباره می‌سازد**

از `example.env` فایل `.env` بسازید، ویرایش کنید، `bypass --apply` بزنید، سپس هیدیفای را قطع/وصل کنید.

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

```bash
./bin/hiddify-cursor-failover bypass                          # show merged list
./bin/hiddify-cursor-failover bypass --file example.env --apply
./bin/hiddify-cursor-failover bypass --ip '192.168.1.20'      # extra on top of .env
```

`patch` / `once` / `watch` re-apply the same list. Optional CLI extras (`--ip`, `--dns`) are stored in `~/.config/hiddify-cursor/bypass.json` on top of `.env`.

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
