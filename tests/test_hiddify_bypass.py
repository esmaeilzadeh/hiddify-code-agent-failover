#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from hiddify_bypass import (
    DIRECT_DNS_KEYS,
    EXCLUDE_DNS_KEYS,
    EXCLUDE_IP_KEYS,
    LAN_DNS_TAG,
    PACKAGE_ROOT,
    SENTINEL_DOMAIN,
    SENTINEL_IP,
    SENTINEL_REGEX,
    BypassParseError,
    BypassSpec,
    apply_bypass_to_config,
    apply_system_no_proxy,
    classify_dns_pattern,
    discover_direct_dns,
    load_bypass_spec,
    load_env_bypass_spec,
    merge_no_proxy,
    merge_spec,
    no_proxy_tokens,
    normalize_dns,
    normalize_ip,
    normalize_spec,
    save_bypass_spec,
    spec_with_direct_dns,
)


def _isolate_env(bypass_json: Optional[str] = None) -> dict[str, Optional[str]]:
    keys = (
        "HIDDIFY_ENV_FILE",
        "HIDDIFY_BYPASS_FILE",
        *EXCLUDE_IP_KEYS,
        *EXCLUDE_DNS_KEYS,
        *DIRECT_DNS_KEYS,
    )
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["HIDDIFY_ENV_FILE"] = ""
    for key in (*EXCLUDE_IP_KEYS, *EXCLUDE_DNS_KEYS, *DIRECT_DNS_KEYS):
        os.environ.pop(key, None)
    if bypass_json is None:
        os.environ.pop("HIDDIFY_BYPASS_FILE", None)
    else:
        os.environ["HIDDIFY_BYPASS_FILE"] = bypass_json
    return previous


def _restore_env(previous: dict[str, Optional[str]]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _base_config(**extra):
    cfg = {
        "dns": {
            "servers": [
                {"type": "udp", "tag": "dns-remote", "server": "1.1.1.1"},
                {"type": "udp", "tag": "dns-direct", "server": "1.1.1.1"},
            ],
            "rules": [{"rule_set": "geosite-ir", "server": "dns-direct"}],
        },
        "outbounds": [
            {"type": "selector", "tag": "select"},
            {"type": "direct", "tag": "direct §hide§"},
            {"type": "direct", "tag": "direct-fragment §hide§"},
        ],
        "route": {
            "rules": [
                {"action": "sniff"},
                {"protocol": "dns", "action": "hijack-dns"},
                {"ip_is_private": True, "outbound": "direct §hide§"},
                {"domain_suffix": ".ir", "outbound": "direct §hide§"},
            ]
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "tun-in",
                "stack": "system",
                "route_exclude_address": ["192.168.0.0/16"],
            }
        ],
    }
    cfg.update(extra)
    return cfg


class NormalizeTests(unittest.TestCase):
    def test_ip_and_cidr(self):
        self.assertEqual(normalize_ip("8.8.8.8"), "8.8.8.8/32")
        self.assertEqual(normalize_ip("10.0.0.0/8"), "10.0.0.0/8")
        self.assertEqual(normalize_ip("2001:db8::1"), "2001:db8::1/128")

    def test_ip_wildcards(self):
        self.assertEqual(normalize_ip("*"), "0.0.0.0/0")
        self.assertEqual(normalize_ip("192.168.*"), "192.168.0.0/16")
        self.assertEqual(normalize_ip("192.168.*.*"), "192.168.0.0/16")
        self.assertEqual(normalize_ip("10.*.*.*"), "10.0.0.0/8")
        self.assertEqual(normalize_ip("192.168.1.*"), "192.168.1.0/24")
        self.assertEqual(normalize_ip("2001:db8:*"), "2001:db8::/32")
        with self.assertRaises(BypassParseError):
            normalize_ip("192.*.1.1")

    def test_dns_host_and_url(self):
        self.assertEqual(normalize_dns("Example.COM."), ("dns", "example.com"))
        self.assertEqual(normalize_dns("*.corp.local"), ("dns", "*.corp.local"))
        self.assertEqual(normalize_dns("*cdn*"), ("dns", "*cdn*"))
        self.assertEqual(normalize_dns("api.*.internal"), ("dns", "api.*.internal"))
        self.assertEqual(normalize_dns("*"), ("dns", "*"))
        self.assertEqual(normalize_dns("https://api.internal.corp:8443/v1"), ("dns", "api.internal.corp"))
        self.assertEqual(normalize_dns("8.8.4.4"), ("ip", "8.8.4.4/32"))
        self.assertEqual(normalize_dns("1.1.1.1:53"), ("ip", "1.1.1.1/32"))
        self.assertEqual(normalize_dns("10.*"), ("ip", "10.0.0.0/8"))

    def test_dns_wildcard_kinds(self):
        self.assertEqual(classify_dns_pattern("*.example.com"), ("suffix", "example.com"))
        self.assertEqual(classify_dns_pattern("*cdn*"), ("keyword", "cdn"))
        self.assertEqual(classify_dns_pattern("api.*.internal"), ("regex", r"^api\..*\.internal$"))
        self.assertEqual(classify_dns_pattern("*"), ("regex", ".+"))
        self.assertEqual(classify_dns_pattern("cdn?.example.com"), ("regex", r"^cdn.\.example\.com$"))

    def test_rejects_garbage(self):
        with self.assertRaises(BypassParseError):
            normalize_ip("not-an-ip")
        with self.assertRaises(BypassParseError):
            normalize_dns("http://")

    def test_dns_ip_folds_into_ips(self):
        spec = normalize_spec(dns=["1.1.1.1", "example.com"])
        self.assertEqual(spec.ips, ["1.1.1.1/32"])
        self.assertEqual(spec.dns, ["example.com"])


class MergeTests(unittest.TestCase):
    def test_add_remove_clear(self):
        base = BypassSpec(ips=["10.0.0.0/8"], dns=["example.com"])
        added = merge_spec(base, add_ips=["8.8.8.8"], add_dns=["corp.local"])
        self.assertEqual(added.ips, ["10.0.0.0/8", "8.8.8.8/32"])
        self.assertEqual(added.dns, ["example.com", "corp.local"])
        removed = merge_spec(added, remove_ips=["8.8.8.8"], remove_dns=["example.com"])
        self.assertEqual(removed.ips, ["10.0.0.0/8"])
        self.assertEqual(removed.dns, ["corp.local"])
        cleared = merge_spec(removed, clear=True)
        self.assertTrue(cleared.is_empty())

    def test_set_replaces_category(self):
        base = BypassSpec(ips=["10.0.0.0/8"], dns=["old.example"])
        spec = merge_spec(base, set_ips=["1.1.1.1"], set_dns=["new.example"])
        self.assertEqual(spec.ips, ["1.1.1.1/32"])
        self.assertEqual(spec.dns, ["new.example"])


class ApplyConfigTests(unittest.TestCase):
    def test_injects_after_dns_hijack_and_keeps_region_rules(self):
        cfg = _base_config()
        spec = BypassSpec(ips=["8.8.8.8/32"], dns=["example.com"])
        changes = apply_bypass_to_config(cfg, spec, direct_dns=())
        self.assertTrue(changes)
        rules = cfg["route"]["rules"]
        self.assertEqual(rules[0]["action"], "sniff")
        self.assertEqual(rules[1]["protocol"], "dns")
        self.assertEqual(rules[2]["ip_cidr"][0], SENTINEL_IP)
        self.assertIn("8.8.8.8/32", rules[2]["ip_cidr"])
        self.assertEqual(rules[2]["outbound"], "direct §hide§")
        self.assertEqual(rules[3]["domain_suffix"][0], SENTINEL_DOMAIN)
        self.assertIn("example.com", rules[3]["domain_suffix"])
        self.assertTrue(any(r.get("domain_suffix") == ".ir" for r in rules))
        self.assertEqual(cfg["dns"]["rules"][0]["server"], "dns-direct")
        self.assertIn("example.com", cfg["dns"]["rules"][0]["domain_suffix"])
        self.assertIn("8.8.8.8/32", cfg["inbounds"][0]["route_exclude_address"])
        self.assertIn("192.168.0.0/16", cfg["inbounds"][0]["route_exclude_address"])

    def test_wildcard_dns_uses_suffix_keyword_and_regex(self):
        cfg = _base_config()
        spec = BypassSpec(dns=["*.example.com", "*cdn*", "api.*.internal"])
        apply_bypass_to_config(cfg, spec, direct_dns=())
        rules = cfg["route"]["rules"]
        suffix = next(r for r in rules if SENTINEL_DOMAIN in (r.get("domain_suffix") or []))
        keyword = next(r for r in rules if SENTINEL_DOMAIN in (r.get("domain_keyword") or []))
        regex = next(r for r in rules if SENTINEL_REGEX in (r.get("domain_regex") or []))
        self.assertIn("example.com", suffix["domain_suffix"])
        self.assertIn("cdn", keyword["domain_keyword"])
        self.assertIn(r"^api\..*\.internal$", regex["domain_regex"])
        dns_rules = cfg["dns"]["rules"]
        self.assertTrue(any("example.com" in (r.get("domain_suffix") or []) for r in dns_rules))
        self.assertTrue(any("cdn" in (r.get("domain_keyword") or []) for r in dns_rules))

    def test_idempotent_and_replaces(self):
        cfg = _base_config()
        spec = BypassSpec(ips=["8.8.8.8/32"], dns=["example.com"])
        apply_bypass_to_config(cfg, spec, direct_dns=())
        self.assertEqual(apply_bypass_to_config(cfg, spec, direct_dns=()), [])
        apply_bypass_to_config(cfg, BypassSpec(ips=["1.1.1.1/32"], dns=["other.com"]), direct_dns=())
        ip_rules = [r for r in cfg["route"]["rules"] if SENTINEL_IP in (r.get("ip_cidr") or [])]
        self.assertEqual(len(ip_rules), 1)
        self.assertEqual(ip_rules[0]["ip_cidr"], [SENTINEL_IP, "1.1.1.1/32"])
        self.assertNotIn("8.8.8.8/32", cfg["inbounds"][0]["route_exclude_address"])
        self.assertIn("1.1.1.1/32", cfg["inbounds"][0]["route_exclude_address"])

    def test_clear_removes_managed_rules_only(self):
        cfg = _base_config()
        apply_bypass_to_config(cfg, BypassSpec(ips=["8.8.8.8/32"], dns=["example.com"]), direct_dns=())
        apply_bypass_to_config(cfg, BypassSpec(), direct_dns=())
        self.assertFalse(any(SENTINEL_IP in (r.get("ip_cidr") or []) for r in cfg["route"]["rules"]))
        self.assertFalse(
            any(SENTINEL_DOMAIN in (r.get("domain_suffix") or []) for r in cfg["route"]["rules"])
        )
        self.assertTrue(any(r.get("domain_suffix") == ".ir" for r in cfg["route"]["rules"]))
        self.assertEqual(cfg["inbounds"][0]["route_exclude_address"], ["192.168.0.0/16"])

    def test_empty_spec_on_clean_config_is_noop(self):
        cfg = _base_config()
        before = json.dumps(cfg, sort_keys=True)
        self.assertEqual(apply_bypass_to_config(cfg, BypassSpec(), direct_dns=()), [])
        self.assertEqual(json.dumps(cfg, sort_keys=True), before)


class NoProxyTests(unittest.TestCase):
    def test_suffix_wildcards_become_leading_dot(self):
        spec = BypassSpec(
            ips=["10.0.0.0/8", "127.0.0.1/32"],
            dns=["*.ir", "*.company.local", "*cdn*", "api.*.internal"],
        )
        tokens = no_proxy_tokens(spec)
        self.assertIn(".ir", tokens)
        self.assertIn("ir", tokens)
        self.assertIn(".company.local", tokens)
        self.assertIn("company.local", tokens)
        self.assertIn("10.0.0.0/8", tokens)
        self.assertIn("127.0.0.1", tokens)
        self.assertNotIn("*cdn*", tokens)
        self.assertNotIn("*.ir", tokens)
        self.assertNotIn("api.*.internal", tokens)

    def test_merge_keeps_existing_and_adds_curl_suffix(self):
        existing = "localhost,127.0.0.0/8,::1,*.ir,*.corp.local,www.example.com"
        spec = BypassSpec(dns=["*.ir", "*.company.local"])
        merged = merge_no_proxy(existing, spec)
        self.assertIn("localhost", merged)
        self.assertIn("*.ir", merged)
        self.assertIn("www.example.com", merged)
        self.assertIn(".ir", merged)
        self.assertIn(".company.local", merged)
        parts = [p.strip() for p in merged.split(",") if p.strip()]
        self.assertEqual(len(parts), len(set(p.lower() for p in parts)))

    def test_lan_dns_used_for_excluded_suffixes(self):
        cfg = _base_config()
        spec = BypassSpec(dns=["*.ir"])
        changes = apply_bypass_to_config(cfg, spec, direct_dns=["192.168.1.1"])
        self.assertTrue(any("lan DNS" in c for c in changes))
        servers = cfg["dns"]["servers"]
        lan = next(s for s in servers if s.get("tag") == LAN_DNS_TAG)
        self.assertEqual(lan["server"], "192.168.1.1")
        self.assertEqual(lan["detour"], "direct §hide§")
        self.assertEqual(cfg["dns"]["rules"][0]["server"], LAN_DNS_TAG)
        self.assertIn("192.168.1.1/32", cfg["route"]["rules"][2]["ip_cidr"])
        self.assertEqual(apply_bypass_to_config(cfg, spec, direct_dns=["192.168.1.1"]), [])

    def test_env_direct_dns_overrides_discovery(self):
        previous = _isolate_env()
        os.environ["HIDDIFY_DIRECT_DNS"] = "192.168.1.1,10.0.0.53"
        try:
            self.assertEqual(discover_direct_dns(), ["192.168.1.1", "10.0.0.53"])
            spec = spec_with_direct_dns(BypassSpec(), ["192.168.1.1"])
            self.assertIn("192.168.1.1/32", spec.ips)
        finally:
            _restore_env(previous)

    def test_apply_writes_environment_d(self):
        spec = BypassSpec(dns=["*.ir"])
        with tempfile.TemporaryDirectory() as tmp:
            notes = apply_system_no_proxy(
                spec,
                environ={"NO_PROXY": "localhost,*.ir"},
                config_home=tmp,
                update_session=False,
            )
            self.assertTrue(notes)
            path = Path(tmp) / "environment.d" / "90-hiddify-cursor-bypass.conf"
            text = path.read_text(encoding="utf-8")
            self.assertIn("NO_PROXY=", text)
            self.assertIn(".ir", text)


class FileRoundTripTests(unittest.TestCase):
    def test_save_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bypass.json")
            previous = _isolate_env(path)
            try:
                saved = save_bypass_spec(BypassSpec(ips=["10.0.0.0/8"], dns=["example.com"]))
                self.assertEqual(str(saved), path)
                loaded = load_bypass_spec()
                self.assertEqual(loaded.ips, ["10.0.0.0/8"])
                self.assertEqual(loaded.dns, ["example.com"])
            finally:
                _restore_env(previous)


class EnvFileTests(unittest.TestCase):
    def test_example_env_template(self):
        spec = load_env_bypass_spec(str(PACKAGE_ROOT / "example.env"))
        self.assertIn("10.0.0.0/8", spec.ips)
        self.assertIn("172.16.0.0/12", spec.ips)
        self.assertIn("192.168.0.0/16", spec.ips)
        self.assertIn("*.company.local", spec.dns)
        self.assertIn("*cdn*", spec.dns)

    def test_env_file_and_json_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "HIDDIFY_EXCLUDE_IPS=8.8.8.8\nHIDDIFY_EXCLUDE_DNS=*.example.com\n",
                encoding="utf-8",
            )
            json_path = str(Path(tmp) / "bypass.json")
            previous = _isolate_env(json_path)
            os.environ["HIDDIFY_ENV_FILE"] = str(env_path)
            try:
                save_bypass_spec(BypassSpec(ips=["10.0.0.0/8"], dns=["corp.local"]))
                spec = load_bypass_spec()
                self.assertEqual(spec.ips, ["8.8.8.8/32", "10.0.0.0/8"])
                self.assertEqual(spec.dns, ["*.example.com", "corp.local"])
            finally:
                _restore_env(previous)

    def test_quoted_multiline_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                'HIDDIFY_EXCLUDE_IPS="10.1.0.0/16,10.2.0.0/16"\n'
                "HIDDIFY_EXCLUDE_DNS='api.*.internal'\n",
                encoding="utf-8",
            )
            spec = load_env_bypass_spec(str(env_path))
            self.assertEqual(spec.ips, ["10.1.0.0/16", "10.2.0.0/16"])
            self.assertEqual(spec.dns, ["api.*.internal"])


class CliTests(unittest.TestCase):
    def test_bypass_save_only(self):
        from hiddify_cursor_failover import main

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bypass.json")
            previous = _isolate_env(path)
            try:
                rc = main(
                    [
                        "bypass",
                        "--ip",
                        "10.1.2.3",
                        "--dns",
                        "example.com,8.8.8.8",
                        "--save-only",
                    ]
                )
                self.assertEqual(rc, 0)
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.assertEqual(data["ips"], ["10.1.2.3/32", "8.8.8.8/32"])
                self.assertEqual(data["dns"], ["example.com"])
                rc = main(
                    [
                        "bypass",
                        "--ip",
                        "192.168.*",
                        "--dns",
                        "*.corp.local,*cdn*",
                        "--save-only",
                    ]
                )
                self.assertEqual(rc, 0)
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.assertIn("192.168.0.0/16", data["ips"])
                self.assertEqual(data["dns"], ["example.com", "*.corp.local", "*cdn*"])
                rc = main(["bypass", "--remove-dns", "example.com", "--save-only"])
                self.assertEqual(rc, 0)
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.assertEqual(data["dns"], ["*.corp.local", "*cdn*"])
                rc = main(["bypass", "--clear", "--save-only"])
                self.assertEqual(rc, 0)
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.assertEqual(data, {"ips": [], "dns": []})
                rc = main(
                    ["bypass", "--file", str(PACKAGE_ROOT / "example.env"), "--save-only"]
                )
                self.assertEqual(rc, 0)
            finally:
                _restore_env(previous)


if __name__ == "__main__":
    unittest.main()
