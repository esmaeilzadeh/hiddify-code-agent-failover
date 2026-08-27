# Hiddify Cursor Failover

Pick the best node from the current Hiddify subscription for Cursor, then
auto-switch when latency/quality drops.

Uses Hiddify’s Clash-compatible API (`/proxies`, delay tests, selector PUT).
No extra Python deps (stdlib only).

## Requirements

- Linux
- Hiddify running (often as root) with Clash API enabled
- `python3`, `sudo` (only for one-time secret cache), `ss`, `systemctl` (optional)

## Quick start

```bash
cd ~/Tools/failover
chmod +x bin/hiddify-cursor-failover install.sh

# one-time: cache Clash API secret from root Hiddify config
./bin/hiddify-cursor-failover refresh-secret

# pick best node now
./bin/hiddify-cursor-failover once

# continuous monitor
./bin/hiddify-cursor-failover watch
```

### systemd user service

```bash
./install.sh
./bin/hiddify-cursor-failover refresh-secret
systemctl --user enable --now hiddify-cursor-failover.service
systemctl --user status hiddify-cursor-failover.service
```

Keep **Hiddify running**. This service only talks to its API; it does not replace Hiddify.

## How it works

1. Reads Clash API secret from:
   - `HIDDIFY_CLASH_SECRET` / `HIDDIFY_CLASH_API`, or
   - `~/.config/hiddify-cursor/clash_secret` (from `refresh-secret`), or
   - Hiddify `current-config.json` (sudo for `/root/...`)
2. Filters out subscription banner rows (`User:`, `Used:`, update warnings, …)
3. URL-tests nodes against `https://api2.cursor.sh/` (fallback: Cloudflare 204)
4. Selects the lowest-latency node in the `select` group
5. In `watch` mode: rechecks on an interval; switches after bad latency or repeated failures

## Config / secrets

| Path | Purpose |
|------|---------|
| `~/.config/hiddify-cursor/clash_secret` | Cached API secret (mode 600) |
| `~/.config/hiddify-cursor/clash_port` | Cached Clash API port |

Do **not** commit these files. Re-run `refresh-secret` after Hiddify restarts if the secret rotates.

Optional env:

- `HIDDIFY_ROOT_CONFIG` — override root config path
- `HIDDIFY_CLASH_SECRET` / `HIDDIFY_CLASH_API` — skip cache

## CLI

```text
./bin/hiddify-cursor-failover refresh-secret
./bin/hiddify-cursor-failover once [--bad-ms via watch only]
./bin/hiddify-cursor-failover watch --interval 20 --bad-ms 1500 --fail-threshold 2
```

Useful flags on both `once` / `watch`:

- `--group select`
- `--test-url https://api2.cursor.sh/`
- `--timeout-ms 5000`
- `--api http://127.0.0.1:16757`
- `--secret …`

## Layout

```text
failover/
  hiddify_cursor_failover.py   # core logic
  bin/hiddify-cursor-failover  # CLI wrapper
  systemd/…service             # unit template
  install.sh                   # install user unit
  README.md
```

## License

MIT
