#!/usr/bin/env python3
import os
import tempfile
import unittest
from pathlib import Path

from hiddify_bypass import BypassSpec
from hiddify_route_rules import (
    DNS_RULE_NAME,
    IP_RULE_NAME,
    bypass_prefs_overlay,
    build_route_rule,
    write_route_rule_file,
)


class RouteRulesTests(unittest.TestCase):
    def test_prefs_overlay_sets_office_dns(self):
        overlay = bypass_prefs_overlay(
            BypassSpec(dns=["*.ir"]),
            lan_dns=["192.168.1.1", "192.168.1.53"],
        )
        self.assertTrue(overlay["flutter.bypass-lan"])
        self.assertTrue(overlay["flutter.enable-dns-routing"])
        self.assertEqual(overlay["flutter.direct-dns-address"], "192.168.1.1")

    def test_build_route_rule_domains_and_ips(self):
        spec = BypassSpec(
            ips=["10.0.0.0/8", "192.168.0.0/16"],
            dns=["*.ir", "*.company.local", "*cdn*"],
        )
        rule = build_route_rule(spec, lan_dns=["192.168.1.1"])
        names = [r.name for r in rule.rules]
        self.assertIn(DNS_RULE_NAME, names)
        self.assertIn(IP_RULE_NAME, names)
        dns_rule = next(r for r in rule.rules if r.name == DNS_RULE_NAME)
        self.assertIn("ir", dns_rule.domain_suffixes)
        self.assertIn("company.local", dns_rule.domain_suffixes)
        self.assertIn("cdn", dns_rule.domain_keywords)
        ip_rule = next(r for r in rule.rules if r.name == IP_RULE_NAME)
        self.assertIn("10.0.0.0/8", ip_rule.ip_cidrs)
        self.assertIn("192.168.1.1/32", ip_rule.ip_cidrs)

    def test_write_merges_and_is_idempotent(self):
        spec = BypassSpec(dns=["*.ir"], ips=["192.168.0.0/16"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "route_rule.proto"
            self.assertTrue(write_route_rule_file(path, spec, lan_dns=["192.168.1.1"]))
            self.assertFalse(write_route_rule_file(path, spec, lan_dns=["192.168.1.1"]))
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
