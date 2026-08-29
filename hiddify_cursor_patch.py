#!/usr/bin/env python3
"""Patch Hiddify prefs / live config for stable Cursor Agent streams.

Persists Cursor-friendly options into Hiddify shared_preferences (root + user)
and, when possible, applies TUN MTU/stack on the live current-config.json.

Background (why): HTTP/CDN/WebSocket outbounds and high-MTU/gvisor TUN often
buffer or kill long SSE/HTTP2 streams to *.cursor.sh.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

# Flutter prefs Hiddify reads when building the sing-box config.
CURSOR_PREFS: dict[str, Any] = {
    "flutter.service-mode": "vpn",
    "flutter.mtu": 1400,
    "flutter.tun-implementation": "system",
    "flutter.bypass-lan": True,
    "flutter.enable-tls-fragment": False,
    "flutter.enable-mux": False,
    "flutter.enable-warp": False,
    "flutter.resolve-destination": False,
    "flutter.remote-dns-domain-strategy": "ipv4_only",
    "flutter.direct-dns-domain-strategy": "ipv4_only",
    "flutter.enable-fake-dns": False,
    "flutter.enable-dns-routing": False,
    # Stable public resolvers for remote DNS (Cursor / CDN names).
    "flutter.remote-dns-address": "1.1.1.1",
    "flutter.direct-dns-address": "1.1.1.1",
    # Hiddify UI "Balancer strategy" defaults to round-robin; that hops nodes
    # mid-stream and kills Cursor Agent. Sticky keeps one node per session.
    "flutter.balancer-strategy": "sticky-sessions",
    "flutter.connection-test-url": "https://api2.cursor.sh/",
}

BAD_TRANSPORTS = {"ws", "websocket", "http", "httpupgrade", "h2", "grpc", "xhttp"}
SKIP_OUTBOUND_TYPES = {
    "selector",
    "urltest",
    "loadbalance",
    "balancer",
    "direct",
    "block",
    "dns",
    "compatible",
}


@dataclass
class PatchResult:
    prefs_paths: list[str]
    prefs_changed: list[str]
    live_config: Optional[str]
    live_changed: bool
    preferred_nodes: list[str]
    notes: list[str]


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# Hiddify ≥2.x (sudo / root) stores data under app.hiddify.com; older builds use hiddify/.
_HIDDIFY_DATA_BASES = (
    "app.hiddify.com",
    "hiddify",
)


def _data_dir_for_home(home: str, package: str) -> Path:
    return Path(home) / ".local" / "share" / package


def _pref_path_for_data_dir(data_dir: Path) -> Path:
    return data_dir / "shared_preferences.json"


def _config_path_for_data_dir(data_dir: Path) -> Path:
    return data_dir / "data" / "current-config.json"


def pref_path_candidates() -> list[Path]:
    """Hiddify shared_preferences paths, root first when reachable."""
    homes = ["/root", os.path.expanduser("~")]
    paths: list[Path] = []
    seen: set[str] = set()
    for home in homes:
        for package in _HIDDIFY_DATA_BASES:
            path = _pref_path_for_data_dir(_data_dir_for_home(home, package))
            key = str(path)
            if key not in seen:
                paths.append(path)
                seen.add(key)
    return paths


def config_path_candidates() -> list[Path]:
    """Hiddify current-config.json paths, root first when reachable."""
    homes = ["/root", os.path.expanduser("~")]
    paths: list[Path] = []
    seen: set[str] = set()
    for home in homes:
        for package in _HIDDIFY_DATA_BASES:
            path = _config_path_for_data_dir(_data_dir_for_home(home, package))
            key = str(path)
            if key not in seen:
                paths.append(path)
                seen.add(key)
    return paths


def _default_pref_paths() -> list[Path]:
    return pref_path_candidates()


def _default_config_paths() -> list[Path]:
    return config_path_candidates()


def _sudo_allowed_interactive() -> bool:
    if os.environ.get("HIDDIFY_PATCH_NONINTERACTIVE") == "1":
        return False
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _read_json(path: Path) -> dict:
    if path.as_posix().startswith("/root/") and not os.access(path, os.R_OK):
        proc = subprocess.run(
            ["sudo", "-n", "cat", str(path)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 and _sudo_allowed_interactive():
            proc = subprocess.run(
                ["sudo", "cat", str(path)],
                capture_output=True,
                text=True,
            )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "sudo cat failed")
        return json.loads(proc.stdout)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    needs_sudo = path.as_posix().startswith("/root/") and (
        not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK)
    )
    if needs_sudo:
        if not _sudo_allowed_interactive():
            # Try passwordless only under systemd / non-interactive.
            cp = subprocess.run(
                ["sudo", "-n", "cp", "-a", str(path), f"{path}.bak-failover"],
                capture_output=True,
                text=True,
            )
            proc = subprocess.run(
                ["sudo", "-n", "tee", str(path)],
                input=payload,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    "Cannot write root Hiddify prefs without passwordless sudo. "
                    "Run `./bin/hiddify-cursor-failover patch` once in a terminal."
                )
            return
        subprocess.run(
            ["sudo", "cp", "-a", str(path), f"{path}.bak-failover"],
            check=False,
            capture_output=True,
            text=True,
        )
        proc = subprocess.run(
            ["sudo", "tee", str(path)],
            input=payload,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "sudo tee failed")
        return
    if path.exists():
        shutil.copy2(path, f"{path}.bak-failover")
    path.write_text(payload, encoding="utf-8")


def apply_prefs(path: Path, desired: dict[str, Any] = CURSOR_PREFS) -> tuple[bool, list[str]]:
    data = _read_json(path)
    changes: list[str] = []
    for key, value in desired.items():
        old = data.get(key, "<missing>")
        if old != value:
            data[key] = value
            changes.append(f"{key}: {old!r} -> {value!r}")
    if changes:
        _write_json(path, data)
    return bool(changes), changes


def classify_outbound(ob: dict) -> tuple[bool, str]:
    """Return (is_preferred_for_cursor, reason)."""
    otype = ob.get("type")
    tag = str(ob.get("tag") or "")
    if otype in SKIP_OUTBOUND_TYPES:
        return False, f"skip-type:{otype}"
    if tag in {"select", "balance", "lowest", "Proxy", "GLOBAL", "DIRECT", "REJECT"}:
        return False, "meta"
    tls = ob.get("tls") or {}
    reality = bool((tls.get("reality") or {}).get("enabled"))
    transport = ((ob.get("transport") or {}).get("type") or "tcp").lower()
    if transport in BAD_TRANSPORTS:
        return False, f"bad-transport:{transport}"
    if not reality:
        return False, "no-reality"
    # Reality + TCP (or implicit TCP when transport omitted).
    if transport in {"tcp", ""}:
        return True, "reality-tcp"
    return False, f"reality+{transport}"


def preferred_node_tags(config: dict) -> list[str]:
    out: list[str] = []
    for ob in config.get("outbounds") or []:
        ok, _ = classify_outbound(ob)
        if ok:
            out.append(str(ob.get("tag")))
    return out


def patch_live_tun(config: dict) -> list[str]:
    changes: list[str] = []
    for ib in config.get("inbounds") or []:
        if ib.get("type") != "tun":
            continue
        if ib.get("mtu") != 1400:
            changes.append(f"tun.mtu: {ib.get('mtu')!r} -> 1400")
            ib["mtu"] = 1400
        # Force system stack; gvisor + high MTU was dropping Cursor streams.
        if ib.get("stack") != "system":
            changes.append(f"tun.stack: {ib.get('stack')!r} -> system")
            ib["stack"] = "system"
    for ob in config.get("outbounds") or []:
        tag = ob.get("tag")
        otype = ob.get("type")
        if tag == "balance" or otype in {"balancer", "loadbalance"}:
            # Hiddify / Clash-style configs use hyphenated names.
            sticky = "sticky-sessions"
            already = {sticky, "sticky_sessions"}
            strategy = ob.get("strategy")
            if isinstance(strategy, dict):
                old = strategy.get("type")
                if old not in already:
                    strategy["type"] = sticky
                    changes.append(f"{tag or otype}.strategy: {old!r} -> {sticky}")
            elif strategy not in already:
                changes.append(f"{tag or otype}.strategy: {strategy!r} -> {sticky}")
                ob["strategy"] = sticky
        if tag == "select" and otype == "selector":
            if not ob.get("interrupt_exist_connections"):
                ob["interrupt_exist_connections"] = True
                changes.append("select.interrupt_exist_connections -> True")
    return changes

def _path_is_usable(path: Path) -> bool:
    """True if we should attempt this path (exists or is root path we can sudo)."""
    try:
        if path.exists():
            return True
    except PermissionError:
        # Parent /root is not traversable; still try via sudo.
        return path.as_posix().startswith("/root/")
    return False


def patch_hiddify(
    pref_paths: Optional[Iterable[Path]] = None,
    config_paths: Optional[Iterable[Path]] = None,
) -> PatchResult:
    notes: list[str] = []
    prefs_touched: list[str] = []
    prefs_changed: list[str] = []

    for path in list(pref_paths or _default_pref_paths()):
        if not _path_is_usable(path):
            continue
        try:
            changed, detail = apply_prefs(path)
        except Exception as e:
            notes.append(f"skip prefs {path}: {e}")
            continue
        prefs_touched.append(str(path))
        if changed:
            prefs_changed.extend(f"{path}: {c}" for c in detail)
            log(f"Patched prefs {path} ({len(detail)} keys)")
            for line in detail:
                log(f"  {line}")
        else:
            log(f"Prefs already Cursor-friendly: {path}")

    live_path: Optional[str] = None
    live_changed = False
    preferred: list[str] = []
    for path in list(config_paths or _default_config_paths()):
        if not _path_is_usable(path):
            continue
        try:
            cfg = _read_json(path)
        except Exception as e:
            notes.append(f"skip config {path}: {e}")
            continue
        live_path = str(path)
        preferred = preferred_node_tags(cfg)
        tun_changes = patch_live_tun(cfg)
        if tun_changes:
            try:
                _write_json(path, cfg)
                live_changed = True
                log(f"Patched live TUN in {path}")
                for line in tun_changes:
                    log(f"  {line}")
            except Exception as e:
                notes.append(f"could not write live config {path}: {e}")
                notes.append("Reconnect Hiddify so prefs MTU/stack apply.")
        else:
            log(f"Live TUN already ok (or no tun inbound) in {path}")
        break

    if preferred:
        log(f"Preferred Cursor nodes (Reality TCP): {len(preferred)}")
    else:
        notes.append(
            "No Reality TCP outbounds found in current-config; "
            "failover will test all filtered subscription nodes."
        )

    if prefs_changed or live_changed:
        notes.append(
            "Fully quit Hiddify (do not only disconnect), then start it again so "
            "Config Options reload: MTU 1400, TUN=system, mux/fragment/WARP off, "
            "balancer=sticky-sessions. Then connect. Proxies must show a node, not Load Balance."
        )

    return PatchResult(
        prefs_paths=prefs_touched,
        prefs_changed=prefs_changed,
        live_config=live_path,
        live_changed=live_changed,
        preferred_nodes=preferred,
        notes=notes,
    )


def load_preferred_nodes(config_path: Optional[str] = None) -> list[str]:
    """Load Reality TCP tags from the *live* Hiddify config when possible.

    Prefer root config (the running daemon). Do not fall back to a stale user
    copy when root is unreadable — wrong tags would over-filter the live list.
    """
    if config_path:
        paths = [Path(config_path)]
    else:
        paths = [
            p for p in config_path_candidates() if str(p).startswith("/root/")
        ]
    for path in paths:
        try:
            cfg = _read_json(path)
        except Exception:
            continue
        return preferred_node_tags(cfg)
    return []
