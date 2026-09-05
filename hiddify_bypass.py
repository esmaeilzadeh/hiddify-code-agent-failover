#!/usr/bin/env python3
"""Exclude IPs and DNS names from Hiddify VPN / system proxy.

Saves a bypass list from `.env` (`HIDDIFY_EXCLUDE_IPS` / `HIDDIFY_EXCLUDE_DNS`,
see example.env) plus optional ~/.config/hiddify-cursor/bypass.json extras,
and injects sing-box route + DNS rules (and TUN route_exclude_address when
present) so those destinations go DIRECT instead of through the selected proxy.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# TEST-NET-1 / RFC 2606 — never appear in real traffic; mark rules we manage.
SENTINEL_IP = "192.0.2.1/32"
SENTINEL_DOMAIN = "hiddify-cursor-bypass.invalid"

CACHE_DIR_NAME = "hiddify-cursor"
BYPASS_FILENAME = "bypass.json"
PACKAGE_ROOT = Path(__file__).resolve().parent
EXCLUDE_IP_KEYS = ("HIDDIFY_EXCLUDE_IPS", "HIDDIFY_BYPASS_IPS")
EXCLUDE_DNS_KEYS = ("HIDDIFY_EXCLUDE_DNS", "HIDDIFY_BYPASS_DNS")
DIRECT_DNS_KEYS = ("HIDDIFY_DIRECT_DNS", "HIDDIFY_LAN_DNS")
LAN_DNS_TAG = "dns-hiddify-cursor-lan"
_DOTENV_ASSIGN = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$", re.IGNORECASE)
_DNS_GLOB_LABEL_RE = re.compile(r"^[a-z0-9*?-]+$", re.IGNORECASE)
_IPV4_WILDCARD_RE = re.compile(r"^(\d{1,3}|\*)(?:\.(\d{1,3}|\*)){0,3}$")
SENTINEL_REGEX = r"^hiddify-cursor-bypass\.invalid$"


@dataclass
class BypassSpec:
    ips: list[str] = field(default_factory=list)
    dns: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.ips and not self.dns

    def to_json(self) -> dict[str, list[str]]:
        return {"ips": list(self.ips), "dns": list(self.dns)}


class BypassParseError(ValueError):
    pass


def cache_dir() -> str:
    return os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
        CACHE_DIR_NAME,
    )


def bypass_file_path(path: Optional[str] = None) -> Path:
    if path:
        return Path(path).expanduser()
    env = os.environ.get("HIDDIFY_BYPASS_FILE")
    if env:
        return Path(env).expanduser()
    return Path(cache_dir()) / BYPASS_FILENAME


def split_values(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in values:
        text = str(raw).replace(";", ",").replace("\n", ",")
        for part in text.split(","):
            item = part.strip().strip("'").strip('"')
            if item:
                out.append(item)
    return out


def _try_ip_network(raw: str) -> Optional[str]:
    try:
        return str(ipaddress.ip_network(raw.strip(), strict=False))
    except ValueError:
        return None


def _ipv4_wildcard_to_cidr(raw: str) -> Optional[str]:
    value = raw.strip()
    if value in {"*", "*.*", "*.*.*", "*.*.*.*"}:
        return "0.0.0.0/0"
    if "*" not in value:
        return None
    if "?" in value:
        raise BypassParseError(
            f"IP {raw!r}: '?' is not supported. Use trailing * (192.168.*) or a CIDR."
        )
    if not _IPV4_WILDCARD_RE.match(value):
        return None
    parts = [p.strip() for p in value.split(".")]
    while len(parts) < 4:
        parts.append("*")
    prefix: list[int] = []
    seen_wild = False
    for part in parts:
        if part == "*":
            seen_wild = True
            continue
        if seen_wild:
            raise BypassParseError(
                f"IP wildcard {raw!r} must use trailing * only (e.g. 192.168.*.*), or a CIDR"
            )
        if not part.isdigit() or not 0 <= int(part) <= 255:
            raise BypassParseError(f"Not a valid IP or CIDR: {raw!r}")
        prefix.append(int(part))
    prefix_len = 8 * len(prefix)
    octets = prefix + [0] * (4 - len(prefix))
    return str(ipaddress.IPv4Network(f"{octets[0]}.{octets[1]}.{octets[2]}.{octets[3]}/{prefix_len}"))


def _ipv6_wildcard_to_cidr(raw: str) -> Optional[str]:
    value = raw.strip()
    if "*" not in value or "." in value:
        return None
    if value in {"*", "*:*", "*:*:*:*:*:*:*:*"}:
        return "::/0"
    if "::" in value:
        raise BypassParseError(
            f"IPv6 wildcard {raw!r} cannot use '::'. Use trailing * (e.g. 2001:db8:*) or a CIDR."
        )
    parts = [p for p in value.split(":") if p != ""]
    if len(parts) > 8:
        raise BypassParseError(f"Not a valid IP or CIDR: {raw!r}")
    while len(parts) < 8:
        if parts and parts[-1] == "*":
            parts.append("*")
        else:
            break
    prefix: list[str] = []
    seen_wild = False
    for part in parts:
        if part == "*":
            seen_wild = True
            continue
        if seen_wild:
            raise BypassParseError(
                f"IPv6 wildcard {raw!r} must use trailing * only (e.g. 2001:db8:*), or a CIDR"
            )
        try:
            int(part, 16)
        except ValueError as e:
            raise BypassParseError(f"Not a valid IP or CIDR: {raw!r}") from e
        if len(part) > 4:
            raise BypassParseError(f"Not a valid IP or CIDR: {raw!r}")
        prefix.append(part)
    prefix_len = 16 * len(prefix)
    hextets = prefix + ["0"] * (8 - len(prefix))
    return str(ipaddress.IPv6Network(f"{':'.join(hextets)}/{prefix_len}"))


def normalize_ip(raw: str) -> str:
    value = raw.strip()
    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]
    cidr = _try_ip_network(value)
    if cidr is not None:
        return cidr
    cidr = _ipv4_wildcard_to_cidr(value)
    if cidr is not None:
        return cidr
    cidr = _ipv6_wildcard_to_cidr(value)
    if cidr is not None:
        return cidr
    raise BypassParseError(f"Not a valid IP, CIDR, or wildcard: {raw!r}")


def _strip_host(raw: str) -> str:
    value = raw.strip().lower()
    if "://" in value:
        parsed = urllib.parse.urlparse(value)
        if parsed.hostname:
            value = parsed.hostname
        else:
            value = value.split("://", 1)[1].split("/")[0]
    if value.endswith("."):
        value = value[:-1]
    if "*" not in value and "?" not in value and ":" in value and value.count(":") == 1:
        host, _, port = value.rpartition(":")
        if port.isdigit():
            value = host
    return value


def _looks_like_ip_pattern(value: str) -> bool:
    if _try_ip_network(value) is not None:
        return True
    if _IPV4_WILDCARD_RE.match(value) and any(ch.isdigit() for ch in value):
        return True
    if ":" in value and ("*" in value or _try_ip_network(value) is not None):
        return True
    return False


def _glob_to_domain_regex(pattern: str) -> str:
    out: list[str] = []
    for char in pattern:
        if char == "*":
            out.append(".*")
        elif char == "?":
            out.append(".")
        else:
            out.append(re.escape(char))
    return "^" + "".join(out) + "$"


def _valid_dns_glob(pattern: str) -> bool:
    if pattern in {"*", "*.*", "?"}:
        return True
    labels = pattern.split(".")
    if not labels or any(not lab for lab in labels):
        return False
    return all(_DNS_GLOB_LABEL_RE.match(lab) for lab in labels)


def classify_dns_pattern(pattern: str) -> tuple[str, str]:
    """Return (kind, sing-box value) where kind is suffix, keyword, or regex."""
    raw = pattern.strip().lower().rstrip(".")
    if not raw:
        raise BypassParseError("Empty DNS pattern")
    if raw in {"*", "*.*", "**"}:
        return "regex", ".+"
    if raw.startswith("*.") and "*" not in raw[2:] and "?" not in raw[2:]:
        suffix = raw[2:].lstrip(".")
        if not suffix or not _DOMAIN_RE.match(suffix):
            raise BypassParseError(f"Not a valid DNS wildcard: {pattern!r}")
        return "suffix", suffix
    if raw.startswith(".") and "*" not in raw and "?" not in raw:
        suffix = raw.lstrip(".")
        if not suffix or not _DOMAIN_RE.match(suffix):
            raise BypassParseError(f"Not a valid DNS suffix: {pattern!r}")
        return "suffix", suffix
    if "*" not in raw and "?" not in raw:
        if not _DOMAIN_RE.match(raw):
            raise BypassParseError(f"Not a valid DNS name or IP: {pattern!r}")
        return "suffix", raw
    if not _valid_dns_glob(raw):
        raise BypassParseError(f"Not a valid DNS wildcard: {pattern!r}")
    keyword = re.fullmatch(r"\*([a-z0-9-]+)\*", raw)
    if keyword:
        return "keyword", keyword.group(1)
    return "regex", _glob_to_domain_regex(raw)


def normalize_dns(raw: str) -> tuple[str, str]:
    """Return (kind, value) where kind is 'ip' or 'dns'."""
    stripped = raw.strip()
    host = _strip_host(stripped)
    if _looks_like_ip_pattern(host) or _looks_like_ip_pattern(stripped):
        try:
            return "ip", normalize_ip(host if _looks_like_ip_pattern(host) else stripped)
        except BypassParseError:
            pass
    classify_dns_pattern(host)
    return "dns", host


def normalize_spec(
    ips: Iterable[str] = (),
    dns: Iterable[str] = (),
) -> BypassSpec:
    ip_out: list[str] = []
    dns_out: list[str] = []
    seen_ip: set[str] = set()
    seen_dns: set[str] = set()

    def add_ip(item: str) -> None:
        cidr = normalize_ip(item)
        if cidr == SENTINEL_IP or cidr in seen_ip:
            return
        seen_ip.add(cidr)
        ip_out.append(cidr)

    def add_dns(item: str) -> None:
        kind, value = normalize_dns(item)
        if kind == "ip":
            add_ip(value)
            return
        if value == SENTINEL_DOMAIN or value in seen_dns:
            return
        seen_dns.add(value)
        dns_out.append(value)

    for item in split_values(ips):
        add_ip(item)
    for item in split_values(dns):
        add_dns(item)
    return BypassSpec(ips=ip_out, dns=dns_out)


def looks_like_env_file(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith(".env") or name.startswith(".env"):
        return True
    try:
        start = path.read_text(encoding="utf-8").lstrip()[:1]
    except OSError:
        return False
    return bool(start) and start != "{"


def parse_dotenv(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise BypassParseError(f"Cannot read {path}: {e}") from e
    data: dict[str, str] = {}
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        match = _DOTENV_ASSIGN.match(stripped)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if value.startswith(("'", '"')):
            quote = value[0]
            body = value[1:]
            if body.endswith(quote):
                value = body[:-1]
            else:
                chunks = [body]
                while i < len(lines):
                    nxt = lines[i]
                    i += 1
                    if nxt.rstrip().endswith(quote):
                        chunks.append(nxt.rstrip()[:-1])
                        break
                    chunks.append(nxt)
                value = "\n".join(chunks)
            value = value.replace("\\n", "\n")
        else:
            if " #" in value:
                value = value[: value.index(" #")].rstrip()
            value = value.strip().strip("'").strip('"')
        data[key] = value
    return data


def dotenv_candidates(explicit: Optional[str] = None) -> list[Path]:
    if explicit is not None:
        text = explicit.strip()
        return [Path(text).expanduser()] if text else []
    env = os.environ.get("HIDDIFY_ENV_FILE")
    if env is not None:
        text = env.strip()
        return [Path(text).expanduser()] if text else []
    return [
        Path.cwd() / ".env",
        Path(cache_dir()) / ".env",
        PACKAGE_ROOT / ".env",
    ]


def load_dotenv(explicit: Optional[str] = None) -> Optional[Path]:
    """Load the first existing .env into os.environ without overriding existing keys."""
    loaded: Optional[Path] = None
    for path in dotenv_candidates(explicit):
        if not path.is_file():
            continue
        parsed = parse_dotenv(path)
        for key, value in parsed.items():
            os.environ.setdefault(key, value)
        if loaded is None:
            loaded = path
        if explicit is not None or os.environ.get("HIDDIFY_ENV_FILE") is not None:
            break
    return loaded


def _first_env_value(
    keys: tuple[str, ...],
    extra: Optional[dict[str, str]] = None,
    *,
    environ: bool = True,
) -> str:
    source = extra or {}
    for key in keys:
        if key in source and str(source[key]).strip():
            return str(source[key])
        if environ:
            value = os.environ.get(key)
            if value and value.strip():
                return value
    return ""


def spec_from_env_map(data: dict[str, str], *, environ: bool = False) -> BypassSpec:
    return normalize_spec(
        [_first_env_value(EXCLUDE_IP_KEYS, data, environ=environ)],
        [_first_env_value(EXCLUDE_DNS_KEYS, data, environ=environ)],
    )


def load_env_bypass_spec(path: Optional[str] = None) -> BypassSpec:
    if path:
        return spec_from_env_map(parse_dotenv(Path(path).expanduser()), environ=False)
    load_dotenv()
    return spec_from_env_map({}, environ=True)


def load_json_bypass_spec(path: Optional[str] = None) -> BypassSpec:
    file_path = bypass_file_path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return BypassSpec()
    except OSError as e:
        raise BypassParseError(f"Cannot read {file_path}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BypassParseError(f"Invalid JSON in {file_path}: {e}") from e
    if not isinstance(data, dict):
        raise BypassParseError(f"{file_path} must be a JSON object with 'ips' and/or 'dns'")
    return normalize_spec(data.get("ips") or [], data.get("dns") or [])


def load_bypass_spec(path: Optional[str] = None) -> BypassSpec:
    if path:
        file_path = Path(path).expanduser()
        if looks_like_env_file(file_path):
            return load_env_bypass_spec(str(file_path))
        return load_json_bypass_spec(str(file_path))
    env_spec = load_env_bypass_spec()
    json_spec = load_json_bypass_spec()
    return merge_spec(env_spec, add_ips=json_spec.ips, add_dns=json_spec.dns)


def save_bypass_spec(spec: BypassSpec, path: Optional[str] = None) -> Path:
    file_path = bypass_file_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(spec.to_json(), indent=2, ensure_ascii=False) + "\n"
    file_path.write_text(payload, encoding="utf-8")
    os.chmod(file_path, 0o600)
    return file_path


def merge_spec(
    base: BypassSpec,
    *,
    add_ips: Iterable[str] = (),
    add_dns: Iterable[str] = (),
    remove_ips: Iterable[str] = (),
    remove_dns: Iterable[str] = (),
    set_ips: Optional[Iterable[str]] = None,
    set_dns: Optional[Iterable[str]] = None,
    clear: bool = False,
) -> BypassSpec:
    if clear:
        base = BypassSpec()
    ips = list(base.ips)
    dns = list(base.dns)
    if set_ips is not None:
        ips = normalize_spec(ips=set_ips).ips
    if set_dns is not None:
        added = normalize_spec(dns=set_dns)
        dns = added.dns
        for cidr in added.ips:
            if cidr not in ips:
                ips.append(cidr)
    added = normalize_spec(ips=add_ips, dns=add_dns)
    for cidr in added.ips:
        if cidr not in ips:
            ips.append(cidr)
    for name in added.dns:
        if name not in dns:
            dns.append(name)
    drop_ips = {normalize_ip(x) for x in split_values(remove_ips)}
    drop_dns: set[str] = set()
    drop_dns_as_ip: set[str] = set()
    for item in split_values(remove_dns):
        kind, value = normalize_dns(item)
        if kind == "ip":
            drop_dns_as_ip.add(value)
        else:
            drop_dns.add(value)
    ips = [x for x in ips if x not in drop_ips and x not in drop_dns_as_ip]
    dns = [x for x in dns if x not in drop_dns]
    return BypassSpec(ips=ips, dns=dns)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(x) for x in value]


def _direct_outbound_tag(config: dict) -> str:
    for ob in config.get("outbounds") or []:
        if not isinstance(ob, dict) or ob.get("type") != "direct":
            continue
        tag = str(ob.get("tag") or "")
        if "fragment" in tag.lower():
            continue
        if tag:
            return tag
    return "direct"


def _dns_direct_tag(config: dict) -> str:
    servers = (config.get("dns") or {}).get("servers") or []
    for srv in servers:
        if isinstance(srv, dict) and srv.get("tag") == "dns-direct":
            return "dns-direct"
    for srv in servers:
        if not isinstance(srv, dict):
            continue
        tag = str(srv.get("tag") or "")
        lowered = tag.lower()
        if "direct" in lowered and "trick" not in lowered and "fragment" not in lowered:
            return tag
    return "dns-direct"


def _is_sentinel_route_rule(rule: Any) -> bool:
    if not isinstance(rule, dict):
        return False
    if SENTINEL_IP in _as_list(rule.get("ip_cidr")):
        return True
    for key in ("domain_suffix", "domain", "domain_keyword"):
        if SENTINEL_DOMAIN in _as_list(rule.get(key)):
            return True
    return SENTINEL_REGEX in _as_list(rule.get("domain_regex"))


def _is_sentinel_dns_rule(rule: Any) -> bool:
    if not isinstance(rule, dict):
        return False
    for key in ("domain_suffix", "domain", "domain_keyword"):
        if SENTINEL_DOMAIN in _as_list(rule.get(key)):
            return True
    return SENTINEL_REGEX in _as_list(rule.get("domain_regex"))


def _bypass_insert_index(rules: list) -> int:
    i = 0
    while i < len(rules):
        rule = rules[i]
        if not isinstance(rule, dict):
            break
        action = rule.get("action")
        if action == "sniff" or action == "hijack-dns" or rule.get("protocol") == "dns":
            i += 1
            continue
        break
    return i


def _previous_managed_ips(rules: Iterable[Any]) -> list[str]:
    for rule in rules:
        if isinstance(rule, dict) and SENTINEL_IP in _as_list(rule.get("ip_cidr")):
            return [c for c in _as_list(rule.get("ip_cidr")) if c != SENTINEL_IP]
    return []


def _merge_exclude_addrs(existing: Any, old_ips: Iterable[str], new_ips: Iterable[str]) -> list[str]:
    drop = {SENTINEL_IP, *old_ips}
    kept = [x for x in _as_list(existing) if x not in drop]
    out: list[str] = []
    seen: set[str] = set()
    for item in [*kept, *new_ips]:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _dns_match_groups(patterns: Iterable[str]) -> dict[str, list[str]]:
    groups = {"suffix": [], "keyword": [], "regex": []}
    seen: dict[str, set[str]] = {key: set() for key in groups}
    for pattern in patterns:
        kind, value = classify_dns_pattern(pattern)
        if value in seen[kind]:
            continue
        seen[kind].add(value)
        groups[kind].append(value)
    return groups


def _dns_match_rules(groups: dict[str, list[str]], extra: dict) -> list[dict]:
    rules: list[dict] = []
    if groups["suffix"]:
        rules.append({"domain_suffix": [SENTINEL_DOMAIN, *groups["suffix"]], **extra})
    if groups["keyword"]:
        rules.append({"domain_keyword": [SENTINEL_DOMAIN, *groups["keyword"]], **extra})
    if groups["regex"]:
        rules.append({"domain_regex": [SENTINEL_REGEX, *groups["regex"]], **extra})
    return rules


NO_PROXY_ENV_FILENAME = "90-hiddify-cursor-bypass.conf"
DEFAULT_NO_PROXY = ("localhost", "127.0.0.0/8", "::1")


def no_proxy_tokens(spec: BypassSpec) -> list[str]:
    """curl-compatible NO_PROXY tokens. `*.ir` is ignored by curl; `.ir` is not."""
    out: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        value = token.strip()
        if not value:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(value)

    for cidr in spec.ips:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            add(cidr)
            continue
        if net.prefixlen == net.max_prefixlen:
            add(str(net.network_address))
        else:
            add(str(net))

    for pattern in spec.dns:
        try:
            kind, value = classify_dns_pattern(pattern)
        except BypassParseError:
            continue
        if kind != "suffix":
            continue
        suffix = value.lstrip(".")
        if suffix:
            add(suffix)
            add("." + suffix)
    return out


def merge_no_proxy(existing: str, spec: BypassSpec) -> str:
    parts: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        value = token.strip()
        if not value:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        parts.append(value)

    for raw in (existing or "").replace(";", ",").split(","):
        add(raw)
    if not parts:
        for token in DEFAULT_NO_PROXY:
            add(token)
    for token in no_proxy_tokens(spec):
        add(token)
    return ",".join(parts)


def _environment_d_path(config_home: Optional[str] = None) -> Path:
    home = config_home or os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(home) / "environment.d" / NO_PROXY_ENV_FILENAME


def apply_system_no_proxy(
    spec: BypassSpec,
    *,
    environ: Optional[dict[str, str]] = None,
    config_home: Optional[str] = None,
    update_session: bool = True,
) -> list[str]:
    """Merge excludes into NO_PROXY so mixed-mode HTTP proxy skips those hosts."""
    notes: list[str] = []
    if spec.is_empty():
        return notes
    env = environ if environ is not None else os.environ
    current = env.get("NO_PROXY") or env.get("no_proxy") or ""
    merged = merge_no_proxy(current, spec)
    if environ is None:
        os.environ["NO_PROXY"] = merged
        os.environ["no_proxy"] = merged
    else:
        env["NO_PROXY"] = merged
        env["no_proxy"] = merged
    path = _environment_d_path(config_home)
    payload = (
        "# Generated by hiddify-cursor-failover from HIDDIFY_EXCLUDE_*\n"
        "# curl ignores *.ir in NO_PROXY; leading-dot .ir is a suffix match.\n"
        f"NO_PROXY={merged}\n"
        f"no_proxy={merged}\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.read_text(encoding="utf-8") if path.is_file() else ""
        if previous != payload:
            path.write_text(payload, encoding="utf-8")
            notes.append(
                f"Updated NO_PROXY for curl/Agent (use .ir, not *.ir) -> {path}"
            )
        elif merged != current:
            notes.append("Updated process NO_PROXY for curl/Agent (.ir suffix match)")
    except OSError as e:
        notes.append(f"could not write {path}: {e}")
        return notes
    if update_session:
        try:
            subprocess.run(
                ["systemctl", "--user", "set-environment", f"NO_PROXY={merged}", f"no_proxy={merged}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    return notes


def _is_private_ip(raw: str) -> bool:
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local)


def _private_ips_from_text(text: str) -> list[str]:
    found: list[str] = []
    for token in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
        if _is_private_ip(token) and token not in found:
            found.append(token)
    return found


def discover_direct_dns(*, environ: bool = True) -> list[str]:
    """Office/LAN resolvers for split-horizon names (intranet.company.local).

    HIDDIFY_DIRECT_DNS wins. Otherwise RFC1918 servers from resolvectl/nmcli.
    Public resolvers (1.1.1.1, Shecan) are ignored — they return NXDOMAIN
    for internal names.
    """
    raw = _first_env_value(DIRECT_DNS_KEYS, environ=environ) if environ else ""
    if raw.strip():
        out: list[str] = []
        for item in split_values([raw]):
            host = _strip_host(item)
            try:
                ip = str(ipaddress.ip_address(host.split("/")[0]))
            except ValueError:
                continue
            if ip not in out:
                out.append(ip)
        return out
    for cmd in (
        ["resolvectl", "dns"],
        ["nmcli", "-t", "-f", "IP4.DNS", "dev", "show"],
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            continue
        found = _private_ips_from_text(proc.stdout or "")
        if found:
            return found
    return []


def spec_with_direct_dns(spec: BypassSpec, servers: Iterable[str]) -> BypassSpec:
    ips = list(spec.ips)
    for server in servers:
        try:
            cidr = normalize_ip(server)
        except BypassParseError:
            continue
        if cidr not in ips:
            ips.append(cidr)
    return BypassSpec(ips=ips, dns=list(spec.dns))


def _is_managed_dns_server(srv: Any) -> bool:
    return isinstance(srv, dict) and str(srv.get("tag") or "").startswith(LAN_DNS_TAG)


def _ensure_direct_dns_servers(
    config: dict,
    servers: list[str],
    direct_outbound: str,
) -> tuple[str, bool]:
    fallback = _dns_direct_tag(config)
    dns_block = config.get("dns")
    if not isinstance(dns_block, dict):
        dns_block = {}
        config["dns"] = dns_block
    existing = list(dns_block.get("servers") or [])
    kept = [s for s in existing if not _is_managed_dns_server(s)]
    if not servers:
        changed = kept != existing
        if changed:
            dns_block["servers"] = kept
        return fallback, changed
    managed: list[dict] = []
    for i, server in enumerate(servers):
        tag = LAN_DNS_TAG if i == 0 else f"{LAN_DNS_TAG}-{i}"
        managed.append(
            {
                "type": "udp",
                "tag": tag,
                "server": server,
                "detour": direct_outbound,
                "connect_timeout": "5s",
            }
        )
    new_servers = kept + managed
    changed = new_servers != existing
    if changed:
        dns_block["servers"] = new_servers
    return LAN_DNS_TAG, changed


def apply_bypass_to_config(
    config: dict,
    spec: BypassSpec,
    *,
    direct_dns: Optional[Iterable[str]] = None,
) -> list[str]:
    """Mutate sing-box config so spec.ips / spec.dns go DIRECT. Idempotent."""
    changes: list[str] = []
    lan = [str(x) for x in direct_dns] if direct_dns is not None else discover_direct_dns()
    spec = spec_with_direct_dns(spec, lan)
    direct = _direct_outbound_tag(config)
    dns_tag, dns_server_changed = _ensure_direct_dns_servers(config, lan, direct)
    if dns_server_changed and lan:
        changes.append(f"lan DNS for excludes: {', '.join(lan)}")

    route = config.get("route")
    if not isinstance(route, dict):
        route = {}
    rules = list(route.get("rules") or [])
    old_ips = _previous_managed_ips(rules)
    new_rules = [r for r in rules if not _is_sentinel_route_rule(r)]
    insert_at = _bypass_insert_index(new_rules)
    injected: list[dict] = []
    dns_groups = _dns_match_groups(spec.dns)
    if spec.ips:
        injected.append({"ip_cidr": [SENTINEL_IP, *spec.ips], "outbound": direct})
    injected.extend(_dns_match_rules(dns_groups, {"outbound": direct}))
    for offset, rule in enumerate(injected):
        new_rules.insert(insert_at + offset, rule)
    if new_rules != rules:
        route = dict(route)
        route["rules"] = new_rules
        config["route"] = route
        if spec.ips:
            changes.append(f"route bypass IPs: {', '.join(spec.ips)}")
        if spec.dns:
            changes.append(f"route bypass DNS: {', '.join(spec.dns)}")
        if spec.is_empty() and (old_ips or any(_is_sentinel_route_rule(r) for r in rules)):
            changes.append("cleared route bypass rules")

    dns_block = config.get("dns")
    if not isinstance(dns_block, dict):
        dns_block = {}
    dns_rules = list(dns_block.get("rules") or [])
    had_dns_sentinel = any(_is_sentinel_dns_rule(r) for r in dns_rules)
    kept_dns = [r for r in dns_rules if not _is_sentinel_dns_rule(r)]
    dns_injected = _dns_match_rules(dns_groups, {"server": dns_tag})
    for offset, rule in enumerate(dns_injected):
        kept_dns.insert(offset, rule)
    if kept_dns != dns_rules:
        dns_block = dict(dns_block)
        dns_block["rules"] = kept_dns
        config["dns"] = dns_block
        if spec.dns:
            label = "lan DNS" if lan else "dns-direct"
            changes.append(f"{label} for: {', '.join(spec.dns)}")
        elif had_dns_sentinel:
            changes.append("cleared DNS bypass rules")

    for ib in config.get("inbounds") or []:
        if not isinstance(ib, dict) or ib.get("type") != "tun":
            continue
        for key in (
            "route_exclude_address",
            "inet4_route_exclude_address",
            "inet6_route_exclude_address",
        ):
            if key != "route_exclude_address" and key not in ib:
                continue
            if key == "inet4_route_exclude_address":
                subset = [c for c in spec.ips if ":" not in c]
                old_subset = [c for c in old_ips if ":" not in c]
            elif key == "inet6_route_exclude_address":
                subset = [c for c in spec.ips if ":" in c]
                old_subset = [c for c in old_ips if ":" in c]
            else:
                subset = list(spec.ips)
                old_subset = old_ips
            if key not in ib and not subset:
                continue
            merged = _merge_exclude_addrs(ib.get(key), old_subset, subset)
            if key == "route_exclude_address" and not merged:
                if key in ib:
                    del ib[key]
                    changes.append("cleared tun.route_exclude_address bypass")
                continue
            if ib.get(key) != merged:
                ib[key] = merged
                if subset:
                    changes.append(f"tun.{key}: exclude {', '.join(subset)}")
                else:
                    changes.append(f"cleared tun.{key} bypass")
    return changes
