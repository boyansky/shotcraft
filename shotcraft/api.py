"""Read-only HTTP client for the Meticulous espresso machine.

SAFETY: this module issues GET requests only. It must never gain the ability
to write to the machine. tests/test_api.py enforces this by scanning source.
"""
import json
import urllib.error
import urllib.request

from . import config

WRITE_VERBS = ("POST", "PUT", "PATCH", "DELETE")

# No default address: one used to be hardcoded to the author's LAN, which is
# exactly the kind of thing that makes a tool unpublishable. The address comes
# from config (env var or `setup`), and its absence is a clear error, not a
# silent attempt to reach someone else's kitchen.


class MachineUnreachable(RuntimeError):
    """The machine did not answer. Changes nothing; safe to retry."""


class MeticulousAPI:
    def __init__(self, base_url=None, timeout=10):
        self.base_url = (base_url or config.base_url()).rstrip("/")
        self.timeout = timeout

    def _get(self, path):
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)  # urllib defaults to GET
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise MachineUnreachable(f"{url}: {exc}") from exc

    def machine(self):
        return self._get("/api/v1/machine")

    def profiles(self):
        return self._get("/api/v1/profile/list")

    def history(self):
        raw = self._get("/api/v1/history")
        if isinstance(raw, dict):
            return raw.get("history", [])
        return raw
