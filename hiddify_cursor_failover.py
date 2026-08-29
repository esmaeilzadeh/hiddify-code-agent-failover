#!/usr/bin/env python3
"""Patch Hiddify for Cursor, then pick/failover the best subscription node.

1. Fix Hiddify prefs / TUN (MTU, DNS strategy, mux/fragment/WARP off, …)
2. Prefer Reality TCP outbounds (avoid ws/http/xhttp/CDN buffering)
3. URL-test against Cursor and select the lowest-latency working node
4. In watch mode, switch when quality drops

Talks to Hiddify's Clash-compatible API (usually 127.0.0.1:16756 or :16757).

Examples:
  ./bin/hiddify-cursor-failover patch
  ./bin/hiddify-cursor-failover once
  ./bin/hiddify-cursor-failover watch
  ./bin/hiddify-cursor-failover refresh-secret
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from hiddify_cursor_patch import config_path_candidates, load_preferred_nodes, patch_hiddify

DEFAULT_CONFIG_CANDIDATES = tuple(str(p) for p in config_path_candidates())

CACHE_DIR_NAME = "hiddify-cursor"
DEFAULT_TEST_URL = "https://api2.cursor.sh/"
FALLBACK_TEST_URL = "http://cp.cloudflare.com/"

SKIP_NAME_RE = re.compile(
    r"(User:|Used:|Time:|expire|آپدیت|Update|traffic|روز|GB\b|days\b|قبل از)",
    re.IGNORECASE,
)
SKIP_EXACT = {"select", "lowest", "balance", "Proxy", "GLOBAL", "DIRECT", "REJECT"}


@dataclass
class ClashApi:
    base: str
    secret: str
    timeout: float = 8.0

    def _req(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        query: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        url = self.base.rstrip("/") + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {"Authorization": f"Bearer {self.secret}"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"{method} {path} -> {e}") from e

    def version(self) -> dict:
        return self._req("GET", "/version") or {}

    def proxies(self) -> dict:
        return (self._req("GET", "/proxies") or {}).get("proxies", {})

    def delay(self, name: str, url: str, timeout_ms: int) -> Optional[int]:
        enc = urllib.parse.quote(name, safe="")
        try:
            data = self._req(
                "GET",
                f"/proxies/{enc}/delay",
                query={"url": url, "timeout": str(timeout_ms)},
                timeout=(timeout_ms / 1000.0) + 2.0,
            )
        except RuntimeError:
            return None
        if not data:
            return None
        delay = data.get("delay")
        return int(delay) if isinstance(delay, int) and delay > 0 else None

    def select(self, group: str, name: str) -> None:
        enc = urllib.parse.quote(group, safe="")
        self._req("PUT", f"/proxies/{enc}", body={"name": name})

    def close_connections(self) -> None:
        try:
            self._req("DELETE", "/connections")
        except RuntimeError:
            pass


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def cache_dir() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), CACHE_DIR_NAME)


def load_json_file(path: str, use_sudo: bool) -> dict:
    noninteractive = os.environ.get("HIDDIFY_PATCH_NONINTERACTIVE") == "1"
    if use_sudo or (path.startswith("/root/") and not os.access(path, os.R_OK)):
        proc = subprocess.run(
            ["sudo", "-n", "cat", path],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 and not noninteractive:
            proc = subprocess.run(
                ["sudo", "cat", path],
                capture_output=True,
                text=True,
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Cannot read {path} via sudo: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return json.loads(proc.stdout)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _secret_candidates(
    secret: Optional[str],
    config_path: Optional[str],
    include_configs: bool = False,
) -> list[tuple[str, str, Optional[int]]]:
    """(source, secret, optional_port). Config files are optional to avoid sudo -n spam."""
    out: list[tuple[str, str, Optional[int]]] = []
    seen: set[str] = set()

    def add(source: str, value: Optional[str], port: Optional[int] = None) -> None:
        if not value or value in seen:
            return
        seen.add(value)
        out.append((source, value, port))

    add("arg", secret)
    add("env", os.environ.get("HIDDIFY_CLASH_SECRET"))

    conf = cache_dir()
    cached_port = None
    try:
        cached_port = int(open(os.path.join(conf, "clash_port"), encoding="utf-8").read().strip())
    except (OSError, ValueError):
        cached_port = None
    try:
        cached = open(os.path.join(conf, "clash_secret"), encoding="utf-8").read().strip()
    except OSError:
        cached = ""
    add("cache", cached or None, cached_port)

    if not include_configs:
        return out

    candidates: list[str] = []
    if config_path:
        candidates.append(config_path)
    candidates.extend(DEFAULT_CONFIG_CANDIDATES)
    for path in candidates:
        try:
            cfg = load_json_file(path, use_sudo=path.startswith("/root/"))
        except Exception:
            continue
        api = (cfg.get("experimental") or {}).get("clash_api") or {}
        cfg_secret = api.get("secret")
        port = None
        controller = api.get("external_controller") or ""
        if ":" in controller:
            try:
                port = int(controller.rsplit(":", 1)[-1])
            except ValueError:
                port = None
        add(path, cfg_secret, port)
    return out


def discover_api(
    secret: Optional[str],
    base: Optional[str],
    config_path: Optional[str],
) -> ClashApi:
    last_err: Optional[Exception] = None
    for include_configs in (False, True):
        secrets = _secret_candidates(secret, config_path, include_configs=include_configs)
        if not secrets:
            continue
        api, err = _try_secrets(secrets, base)
        if api:
            return api
        last_err = err
    raise SystemExit(
        f"Could not reach Clash API. Last error: {last_err}\n"
        "If Hiddify was restarted, run: ./bin/hiddify-cursor-failover refresh-secret"
    )


def _try_secrets(
    secrets: list[tuple[str, str, Optional[int]]],
    base: Optional[str],
) -> tuple[Optional[ClashApi], Optional[Exception]]:
    if base:
        bases = [base.rstrip("/")]
    elif os.environ.get("HIDDIFY_CLASH_API"):
        bases = [os.environ["HIDDIFY_CLASH_API"].rstrip("/")]
    else:
        bases = []
    last_err: Optional[Exception] = None
    extra_ports = [16757, 16756, 9090]
    for source, tok, port in secrets:
        try_bases = list(bases)
        if not try_bases:
            ports: list[int] = []
            if port:
                ports.append(port)
            for p in extra_ports:
                if p not in ports:
                    ports.append(p)
            try_bases = [f"http://127.0.0.1:{p}" for p in ports]
        for b in try_bases:
            api = ClashApi(base=b, secret=tok)
            try:
                ver = api.version()
                log(
                    f"Clash API ok at {b} (secret from {source}); "
                    f"version={ver.get('version') or ver}"
                )
                return api, None
            except Exception as e:
                last_err = e
                continue
    return None, last_err


def _is_clash_auth_error(exc: BaseException) -> bool:
    text = str(exc)
    return "401" in text or "Unauthorized" in text


def is_real_server(name: str) -> bool:
    if name in SKIP_EXACT:
        return False
    if SKIP_NAME_RE.search(name):
        return False
    return True


def candidate_names(proxies: dict, group: str) -> list[str]:
    g = proxies.get(group) or {}
    members = list(g.get("all") or [])
    if not members:
        members = [
            n
            for n, info in proxies.items()
            if info.get("type")
            not in ("Selector", "URLTest", "Fallback", "LoadBalance", "Direct", "Reject")
        ]
    return [n for n in members if is_real_server(n)]


def rank_nodes(
    api: ClashApi,
    names: Iterable[str],
    test_url: str,
    timeout_ms: int,
    concurrency_note: bool = True,
) -> list[tuple[str, int]]:
    ranked: list[tuple[str, int]] = []
    names = list(names)
    if concurrency_note:
        log(f"URL-testing {len(names)} nodes via {test_url} (timeout={timeout_ms}ms)…")
    for name in names:
        d = api.delay(name, test_url, timeout_ms)
        if d is None:
            log(f"  FAIL  {name}")
            continue
        log(f"  {d:5d}ms  {name}")
        ranked.append((name, d))
    ranked.sort(key=lambda x: x[1])
    return ranked


def pick_and_select(
    api: ClashApi,
    group: str,
    test_url: str,
    timeout_ms: int,
    exclude: Optional[set[str]] = None,
    preferred: Optional[list[str]] = None,
    prefer_reality_tcp: bool = True,
) -> Optional[tuple[str, int]]:
    proxies = api.proxies()
    if group not in proxies:
        raise SystemExit(f"Proxy group {group!r} not found. Available: {sorted(proxies)[:30]}")
    names = candidate_names(proxies, group)
    if exclude:
        names = [n for n in names if n not in exclude]
    if not names:
        log("No candidate nodes after filtering.")
        return None

    preferred_set = set(preferred or [])
    primary = names
    if prefer_reality_tcp and preferred_set:
        primary = [n for n in names if n in preferred_set]
        if primary:
            log(f"Restricting search to {len(primary)} Reality TCP nodes (of {len(names)})")
        else:
            log("No Reality TCP overlap with subscription list; testing all candidates")
            primary = names

    ranked = rank_nodes(api, primary, test_url, timeout_ms)
    if not ranked and primary is not names:
        log("All preferred Reality TCP nodes failed; falling back to full candidate list")
        ranked = rank_nodes(api, names, test_url, timeout_ms)
    if not ranked:
        if test_url != FALLBACK_TEST_URL:
            log("All Cursor URL tests failed; retrying with Cloudflare 204…")
            ranked = rank_nodes(api, primary, FALLBACK_TEST_URL, timeout_ms)
        if not ranked and primary is not names:
            ranked = rank_nodes(api, names, FALLBACK_TEST_URL, timeout_ms)
        if not ranked:
            return None
    best_name, best_ms = ranked[0]
    current = (proxies.get(group) or {}).get("now")
    if current == best_name:
        log(f"Already on best: {best_name} ({best_ms}ms)")
    else:
        log(f"Switching {group}: {current!r} -> {best_name!r} ({best_ms}ms)")
        api.select(group, best_name)
        api.close_connections()
    return best_name, best_ms


def maybe_patch(args: argparse.Namespace) -> list[str]:
    if getattr(args, "skip_patch", False):
        log("Skipping Hiddify config patch (--skip-patch)")
        return load_preferred_nodes(args.config)
    result = patch_hiddify()
    for note in result.notes:
        log(f"note: {note}")
    # Only trust preferred tags from the live (root) config when available.
    preferred = load_preferred_nodes(args.config)
    if preferred:
        return preferred
    if result.live_config and result.live_config.startswith("/root/"):
        return result.preferred_nodes
    if result.preferred_nodes:
        log(
            "note: preferred list came from a non-root config copy; "
            "ignoring it to avoid mismatched tags vs the live subscription"
        )
    return []


def measure_current(
    api: ClashApi,
    group: str,
    test_url: str,
    timeout_ms: int,
) -> tuple[Optional[str], Optional[int]]:
    proxies = api.proxies()
    current = (proxies.get(group) or {}).get("now")
    if not current or not is_real_server(current):
        return current, None
    return current, api.delay(current, test_url, timeout_ms)


def cmd_patch(args: argparse.Namespace) -> int:
    result = patch_hiddify()
    for note in result.notes:
        log(f"note: {note}")
    log(
        f"Done. prefs_changed={len(result.prefs_changed)} "
        f"live_changed={result.live_changed} preferred_nodes={len(result.preferred_nodes)}"
    )
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    preferred = maybe_patch(args)
    api = discover_api(args.secret, args.api, args.config)
    result = pick_and_select(
        api,
        args.group,
        args.test_url,
        args.timeout_ms,
        preferred=preferred,
        prefer_reality_tcp=not args.all_nodes,
    )
    if not result:
        log("No working node found.")
        return 1
    log(f"Selected {result[0]} @ {result[1]}ms")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    preferred = maybe_patch(args)
    fail_streak = 0
    last_switch = 0.0

    def connect() -> ClashApi:
        return discover_api(args.secret, args.api, args.config)

    api = connect()
    pick_and_select(
        api,
        args.group,
        args.test_url,
        args.timeout_ms,
        preferred=preferred,
        prefer_reality_tcp=not args.all_nodes,
    )

    while True:
        time.sleep(args.interval)
        try:
            name, delay = measure_current(api, args.group, args.test_url, args.timeout_ms)
        except (RuntimeError, SystemExit) as e:
            if _is_clash_auth_error(e):
                log("Clash API unauthorized (Hiddify likely restarted). Reconnecting…")
                try:
                    api = connect()
                except SystemExit as e2:
                    log(str(e2))
                    log("Run: ./bin/hiddify-cursor-failover refresh-secret")
                continue
            raise
        if delay is None:
            fail_streak += 1
            log(f"Current {name!r} unhealthy ({fail_streak}/{args.fail_threshold})")
        else:
            fail_streak = 0
            log(f"OK {name} @ {delay}ms")
            if delay <= args.bad_ms:
                continue
            log(f"Quality drop: {delay}ms > bad threshold {args.bad_ms}ms")

        now = time.time()
        if fail_streak < args.fail_threshold and delay is not None and delay <= args.bad_ms:
            continue
        if now - last_switch < args.min_switch_interval:
            log("Cooldown active; not switching yet.")
            continue

        exclude = {name} if name else set()
        result = pick_and_select(
            api,
            args.group,
            args.test_url,
            args.timeout_ms,
            exclude=exclude,
            preferred=preferred,
            prefer_reality_tcp=not args.all_nodes,
        )
        if result:
            last_switch = now
            fail_streak = 0
        else:
            log("Failover search found nothing better; will retry.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    selection = argparse.ArgumentParser(add_help=False)
    selection.add_argument("--api", help="Clash API base, e.g. http://127.0.0.1:16757")
    selection.add_argument("--secret", help="Clash API secret (else env / root config via sudo)")
    selection.add_argument("--config", help="Path to Hiddify current-config.json")
    selection.add_argument("--group", default="select", help="Selector group tag (default: select)")
    selection.add_argument("--test-url", default=DEFAULT_TEST_URL, help="URL used for delay tests")
    selection.add_argument("--timeout-ms", type=int, default=5000, help="Per-node delay test timeout")
    selection.add_argument(
        "--skip-patch",
        action="store_true",
        help="Do not patch Hiddify prefs/TUN before selecting nodes",
    )
    selection.add_argument(
        "--all-nodes",
        action="store_true",
        help="URL-test all subscription nodes (not only Reality TCP)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    patch = sub.add_parser("patch", help="Only patch Hiddify prefs/TUN for Cursor")
    patch.set_defaults(func=cmd_patch)

    once = sub.add_parser(
        "once",
        parents=[selection],
        help="Patch (unless --skip-patch), then select best node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    once.set_defaults(func=cmd_once)

    watch = sub.add_parser(
        "watch",
        parents=[selection],
        help="Patch, select best node, then auto-failover",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    watch.add_argument("--interval", type=float, default=20.0, help="Seconds between health checks")
    watch.add_argument("--bad-ms", type=int, default=1500, help="Latency above this triggers switch")
    watch.add_argument(
        "--fail-threshold",
        type=int,
        default=2,
        help="Consecutive failed checks before switching",
    )
    watch.add_argument(
        "--min-switch-interval",
        type=float,
        default=60.0,
        help="Minimum seconds between switches",
    )
    watch.set_defaults(func=cmd_watch)
    return p

def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("Stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
