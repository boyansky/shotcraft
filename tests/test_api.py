import json, pathlib, unittest
from unittest.mock import patch, MagicMock
from shotcraft.api import MeticulousAPI, MachineUnreachable, WRITE_VERBS

FIX = pathlib.Path(__file__).parent / "fixtures"

class TestReadOnlySafety(unittest.TestCase):
    def test_module_source_contains_no_write_verbs(self):
        src = (pathlib.Path(__file__).parent.parent / "shotcraft" / "api.py").read_text()
        # the constant itself is allowed to name them; strip its definition line first
        body = "\n".join(l for l in src.splitlines() if "WRITE_VERBS" not in l)
        for verb in WRITE_VERBS:
            self.assertNotIn(f'"{verb}"', body, f"{verb} must never appear in api.py")

    def test_every_request_uses_get(self):
        api = MeticulousAPI("http://192.0.2.1")
        seen = []
        def fake_urlopen(req, timeout=None):
            seen.append(req.get_method())
            m = MagicMock()
            m.read.return_value = b"{}"
            m.__enter__ = lambda s: m
            m.__exit__ = lambda s, *a: False
            return m
        with patch("shotcraft.api.urllib.request.urlopen", fake_urlopen):
            api.machine(); api.profiles(); api.history()
        self.assertEqual(set(seen), {"GET"})

class TestParsing(unittest.TestCase):
    def test_history_returns_list_of_shots(self):
        raw = json.loads((FIX / "history.json").read_text())
        api = MeticulousAPI("http://192.0.2.1")
        with patch.object(api, "_get", return_value=raw):
            shots = api.history()
        self.assertIsInstance(shots, list)
        self.assertIn("id", shots[0])
        self.assertIn("profile", shots[0])
        self.assertIn("data", shots[0])

    def test_unreachable_raises_machine_unreachable(self):
        api = MeticulousAPI(base_url="http://127.0.0.1:9")
        with self.assertRaises(MachineUnreachable):
            api.machine()

if __name__ == "__main__":
    unittest.main()
