import unittest
from shotcraft.discover import (parse_dns_sd_browse, parse_dns_sd_resolve,
                                parse_avahi, subnet_hosts, looks_like_machine,
                                verify, discover)

# real captured output from `dns-sd -B _http._tcp local`
BROWSE = """Browsing for _http._tcp.local
DATE: ---Sat 25 Jul 2026---
14:16:32.558  ...STARTING...
Timestamp     A/R    Flags  if Domain               Service Type         Instance Name
14:16:32.767  Add        2  11 local.               _http._tcp.          meticulousExampleMachine-000000
14:16:32.923  Add        2  11 local.               _http._tcp.          AWAIR-R2-10824E
"""

# real captured output from `dns-sd -G v4 <host>.local`
RESOLVE = """DATE: ---Sat 25 Jul 2026---
14:16:52.804  ...STARTING...
Timestamp     A/R  Flags         IF  Hostname                               Address       TTL
14:16:52.805  Add  40000002      11  meticulousExampleMachine-000000.local. 192.168.1.42  120
"""

AVAHI = (";eth0;IPv4;Printer;_http._tcp;local;printer.local;192.168.0.20;80;\n"
         "=;wlan0;IPv4;meticulousExampleMachine-000000;_http._tcp;local;"
         "met.local;192.168.1.42;80;\n")

MACHINE = {"name": "MeticulousExampleMachine",
           "hostname": "meticulousExampleMachine-000000",
           "firmware": "0.2.24-369", "serial": "000000"}


class TestParsers(unittest.TestCase):
    def test_browse_finds_every_instance(self):
        names = parse_dns_sd_browse(BROWSE)
        self.assertIn("meticulousExampleMachine-000000", names)
        self.assertIn("AWAIR-R2-10824E", names)

    def test_browse_ignores_header_and_status_lines(self):
        for junk in ("Browsing", "STARTING", "Timestamp", "DATE"):
            self.assertFalse(any(junk in n for n in parse_dns_sd_browse(BROWSE)))

    def test_browse_of_empty_output_is_empty(self):
        self.assertEqual(parse_dns_sd_browse(""), [])

    def test_resolve_extracts_the_ipv4(self):
        self.assertEqual(parse_dns_sd_resolve(RESOLVE), "192.168.1.42")

    def test_resolve_without_an_address_returns_none(self):
        self.assertIsNone(parse_dns_sd_resolve("DATE: ---x---\n...STARTING...\n"))

    def test_avahi_returns_name_and_ip_for_resolved_rows_only(self):
        found = parse_avahi(AVAHI)
        self.assertIn(("meticulousExampleMachine-000000", "192.168.1.42"), found)
        self.assertEqual(len(found), 1)      # the ';' row is not a resolution

    def test_avahi_tolerates_junk(self):
        self.assertEqual(parse_avahi("garbage\n\n"), [])


class TestSubnet(unittest.TestCase):
    def test_hosts_cover_the_slash_24_without_network_or_broadcast(self):
        hosts = subnet_hosts("192.168.0.157")
        # 254 usable addresses in a /24, minus ourselves
        self.assertEqual(len(hosts), 253)
        self.assertIn("192.168.0.1", hosts)
        self.assertIn("192.168.0.254", hosts)
        self.assertNotIn("192.168.0.0", hosts)
        self.assertNotIn("192.168.0.255", hosts)

    def test_the_host_itself_is_skipped(self):
        self.assertNotIn("192.168.0.157", subnet_hosts("192.168.0.157"))

    def test_bad_address_yields_nothing(self):
        self.assertEqual(subnet_hosts("not-an-ip"), [])


class TestVerify(unittest.TestCase):
    def test_a_real_machine_payload_is_recognised(self):
        self.assertTrue(looks_like_machine(MACHINE))

    def test_some_other_web_server_is_not(self):
        self.assertFalse(looks_like_machine({"title": "router admin"}))
        self.assertFalse(looks_like_machine(None))
        self.assertFalse(looks_like_machine("<html>"))

    def test_verify_returns_the_machine_on_success(self):
        got = verify("http://x", fetch=lambda url: MACHINE)
        self.assertEqual(got["serial"], "000000")

    def test_verify_returns_none_when_unreachable(self):
        def boom(url): raise OSError("no route")
        self.assertIsNone(verify("http://x", fetch=boom))

    def test_verify_returns_none_for_a_non_machine(self):
        self.assertIsNone(verify("http://x", fetch=lambda url: {"nope": 1}))


class TestDiscoverCascade(unittest.TestCase):
    def test_mdns_hit_short_circuits_and_never_scans(self):
        scanned = []
        found = discover(
            mdns=lambda: ["192.168.1.42"],
            scan=lambda: scanned.append(True) or [],
            verify=lambda url: MACHINE)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["base_url"], "http://192.168.1.42")
        self.assertEqual(scanned, [], "scan must not run when mdns succeeded")

    def test_falls_back_to_scan_when_mdns_finds_nothing(self):
        found = discover(mdns=lambda: [],
                         scan=lambda: ["192.168.0.99"],
                         verify=lambda url: MACHINE)
        self.assertEqual(found[0]["base_url"], "http://192.168.0.99")

    def test_falls_back_to_scan_when_mdns_hits_do_not_verify(self):
        # something advertises _http._tcp but is not a Meticulous
        seen = []
        def v(url):
            seen.append(url)
            return MACHINE if "99" in url else None
        found = discover(mdns=lambda: ["192.168.0.5"], scan=lambda: ["192.168.0.99"],
                         verify=v)
        self.assertEqual(found[0]["base_url"], "http://192.168.0.99")

    def test_returns_empty_when_nothing_answers(self):
        self.assertEqual(discover(mdns=lambda: [], scan=lambda: [],
                                  verify=lambda url: None), [])

    def test_a_failing_engine_does_not_abort_the_cascade(self):
        def broken(): raise OSError("dns-sd missing")
        found = discover(mdns=broken, scan=lambda: ["192.168.0.99"],
                         verify=lambda url: MACHINE)
        self.assertEqual(found[0]["base_url"], "http://192.168.0.99")

    def test_duplicates_are_collapsed(self):
        found = discover(mdns=lambda: ["192.168.1.42", "192.168.1.42"],
                         scan=lambda: [], verify=lambda url: MACHINE)
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()


class TestResolveOrdering(unittest.TestCase):
    """Resolving costs a round trip each, so machine-looking names go first."""

    def test_meticulous_names_sort_ahead(self):
        from shotcraft.discover import likely_first
        got = likely_first(["AWAIR-R2-10824E", "meticulousExalted-000000", "Printer"])
        self.assertEqual(got[0], "meticulousExalted-000000")

    def test_ordering_is_case_insensitive(self):
        from shotcraft.discover import likely_first
        self.assertEqual(likely_first(["Printer", "METICULOUS-1"])[0], "METICULOUS-1")

    def test_nothing_is_dropped_so_a_renamed_machine_is_still_found(self):
        from shotcraft.discover import likely_first
        names = ["a", "b", "meticulous-x"]
        self.assertEqual(sorted(likely_first(names)), sorted(names))

    def test_resolution_is_capped_so_a_busy_network_cannot_stall_setup(self):
        from shotcraft.discover import mdns_hosts
        calls = []
        def fake_run(cmd, timeout):
            calls.append(cmd)
            if cmd[0] == "avahi-browse":
                return ""
            if cmd[1] == "-B":
                return "\n".join(
                    f"12:00:00.000  Add  2  11 local.  _http._tcp.  dev{i}"
                    for i in range(50))
            return ""
        mdns_hosts(run=fake_run, limit=6)
        resolves = [c for c in calls if c[1] == "-G"]
        self.assertEqual(len(resolves), 6)
