"""Find a Meticulous on the local network.

Three engines, tried in order, because they fail differently and we have hit
both failure modes for real:

  1. mDNS via the OS tool (`dns-sd` on macOS, `avahi-browse` on Linux).
     Instant and precise. Absent on Windows, and it reports what a device
     *claims* rather than what actually answers.
  2. A subnet sweep of the local /24, asking each host for /api/v1/machine.
     Slower and cross-platform, but definitive: it only finds machines that
     genuinely serve the API.
  3. Whatever the human types.

Every candidate is verified by fetching /api/v1/machine and checking the
payload really looks like a machine, so a router's web page cannot be
mistaken for an espresso machine.

The impure parts (subprocess, sockets, HTTP) are injected, so the parsers and
the cascade are testable without a network or a shell.
"""
import concurrent.futures
import json
import re
import socket
import subprocess
import urllib.error
import urllib.request

MDNS_SERVICE = "_http._tcp"
PROBE_PATH = "/api/v1/machine"

# a Meticulous answers /api/v1/machine with at least these
MACHINE_KEYS = ("name", "hostname", "firmware")

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


# ── parsers ───────────────────────────────────────────────────────────────

def parse_dns_sd_browse(text):
    """Instance names from `dns-sd -B _http._tcp local` output."""
    names = []
    for line in (text or "").splitlines():
        # data rows look like: <ts>  Add  <flags> <if> local.  _http._tcp.  <name>
        if " Add " not in line or "_tcp." not in line:
            continue
        parts = line.split()
        if parts and parts[-1] not in names:
            names.append(parts[-1])
    return names


def parse_dns_sd_resolve(text):
    """The IPv4 address from `dns-sd -G v4 <host>.local` output."""
    for line in (text or "").splitlines():
        if " Add " not in line:
            continue
        found = _IPV4.findall(line)
        if found:
            return found[0]
    return None


def parse_avahi(text):
    """(name, ip) pairs from `avahi-browse -rpt _http._tcp`.

    Only '=' rows are resolutions; '+' and ';' rows are mere sightings with
    no address attached.
    """
    out = []
    for line in (text or "").splitlines():
        if not line.startswith("="):
            continue
        f = line.split(";")
        if len(f) >= 8 and _IPV4.fullmatch(f[7]):
            out.append((f[3], f[7]))
    return out


# ── subnet ────────────────────────────────────────────────────────────────

def local_ip():
    """This host's address on the LAN. Opens no connection, sends nothing."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))     # TEST-NET-1, never routed
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def subnet_hosts(ip):
    """Every host address in this /24, minus network, broadcast and self."""
    if not ip or not _IPV4.fullmatch(ip):
        return []
    a, b, c, d = ip.split(".")
    return [f"{a}.{b}.{c}.{n}" for n in range(1, 255) if str(n) != d]


# ── verification ──────────────────────────────────────────────────────────

def looks_like_machine(payload):
    """True only for something shaped like a Meticulous machine document."""
    return isinstance(payload, dict) and all(k in payload for k in MACHINE_KEYS)


def http_get_json(url, timeout=2.0):
    req = urllib.request.Request(url)          # urllib defaults to GET
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify(base_url, fetch=None):
    """Return the machine document if base_url really is one, else None."""
    fetch = fetch or http_get_json
    try:
        payload = fetch(base_url.rstrip("/") + PROBE_PATH)
    except Exception:
        # any failure means "not a machine here"; nothing is written either way
        return None
    return payload if looks_like_machine(payload) else None


# ── engines ───────────────────────────────────────────────────────────────

def _run(cmd, timeout):
    """Run a command, tolerate it being absent, never raise on timeout.

    `dns-sd -B` never exits on its own, so a timeout IS the normal path and
    its partial output is the result we want.
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except subprocess.TimeoutExpired as exc:
        return exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    except (OSError, ValueError):
        return ""


def likely_first(names):
    """Machine-looking names first, everything else after.

    Resolving an mDNS instance costs a round trip each, and a household has
    plenty of them (printers, air monitors, TVs). Sorting rather than
    filtering keeps a renamed machine discoverable while making the common
    case fast.
    """
    return sorted(names, key=lambda n: "meticulous" not in n.lower())


def mdns_hosts(browse_timeout=3.0, resolve_timeout=2.0, run=None, limit=6):
    """Candidate addresses advertising _http._tcp, via whichever tool exists."""
    run = run or _run

    # avahi resolves in one pass, so it needs no per-name round trips
    avahi = parse_avahi(run(["avahi-browse", "-rpt", MDNS_SERVICE], browse_timeout))
    if avahi:
        return [ip for _name, ip in avahi]

    names = parse_dns_sd_browse(
        run(["dns-sd", "-B", MDNS_SERVICE, "local"], browse_timeout))
    hosts = []
    for name in likely_first(names)[:limit]:
        ip = parse_dns_sd_resolve(
            run(["dns-sd", "-G", "v4", f"{name}.local"], resolve_timeout))
        if ip:
            hosts.append(ip)
    return hosts


def scan_hosts(ip=None, workers=64, timeout=0.6, probe=None):
    """Sweep the local /24 for anything answering the machine endpoint."""
    probe = probe or (lambda host: verify(f"http://{host}"))
    hosts = subnet_hosts(ip or local_ip())
    if not hosts:
        return []
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for host, result in zip(hosts, pool.map(probe, hosts)):
            if result:
                found.append(host)
    return found


# ── cascade ───────────────────────────────────────────────────────────────

def discover(mdns=None, scan=None, verify=None):
    """Candidates from mDNS, falling back to a sweep. Verified, deduplicated.

    An engine that raises is skipped rather than aborting the cascade: a
    missing `dns-sd` must not stop the sweep from running.
    """
    mdns = mdns or mdns_hosts
    scan = scan or scan_hosts
    check = verify or globals()["verify"]

    def attempt(engine):
        try:
            return list(engine() or [])
        except Exception:
            return []

    def confirm(hosts):
        out, seen = [], set()
        for host in hosts:
            base = host if host.startswith("http") else f"http://{host}"
            if base in seen:
                continue
            seen.add(base)
            machine = check(base)
            if machine:
                out.append({"base_url": base, "machine": machine})
        return out

    return confirm(attempt(mdns)) or confirm(attempt(scan))
