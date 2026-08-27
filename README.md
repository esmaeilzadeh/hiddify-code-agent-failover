# Hiddify Cursor Failover

Two-step toolkit for Cursor over Hiddify:

1. **Patch** Hiddify prefs / TUN for stable Agent streams (DNS strategy, MTU, stack, mux/fragment/WARP off)
2. **Failover** — pick the best node in the current subscription (prefer Reality TCP), then auto-switch when quality drops

Uses Hiddify’s Clash-compatible API. No extra Python deps (stdlib only).

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
