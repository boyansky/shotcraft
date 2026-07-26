import json, pathlib, unittest
from shotcraft.stages import segment, join_to_profile

FIX = pathlib.Path(__file__).parent / "fixtures"

def load_shot(name):
    hist = json.loads((FIX / "history.json").read_text())["history"]
    for entry in hist:
        if entry["name"].strip() == name:
            return entry
    raise AssertionError(f"no fixture shot named {name}")

class TestSegment(unittest.TestCase):
    def test_multistage_shot_splits_into_expected_runs(self):
        runs = segment(load_shot("Traditional Lever")["data"])
        self.assertEqual(
            [r["status"] for r in runs],
            ["Fill start", "Fill", "Infuse", "Pressure Up", "retracting"],
        )

    def test_status_is_stripped(self):
        runs = segment(load_shot("Turbo")["data"])
        self.assertEqual(runs[0]["status"], "no ramp (6bar down ramp) turbo")

    def test_durations_are_seconds_and_positive(self):
        runs = segment(load_shot("Italian Style")["data"])
        for r in runs:
            self.assertGreaterEqual(r["duration_s"], 0.0)
            self.assertLess(r["duration_s"], 120.0)

    def test_runs_are_contiguous(self):
        runs = segment(load_shot("Italian Style")["data"])
        for a, b in zip(runs, runs[1:]):
            self.assertLessEqual(a["end_s"], b["start_s"] + 0.001)

    def test_empty_data_returns_empty(self):
        self.assertEqual(segment([]), [])

    def test_explicit_null_profile_time_does_not_crash(self):
        # the key is present and null, which .get(key, default) does not catch
        runs = segment([{"status": "Fill", "profile_time": None},
                        {"status": "Fill", "profile_time": 1000}])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["start_s"], 0.0)
        self.assertEqual(runs[0]["duration_s"], 1.0)

    def test_explicit_null_status_does_not_crash(self):
        runs = segment([{"status": None, "profile_time": 0}])
        self.assertEqual(runs[0]["status"], "")

class TestJoin(unittest.TestCase):
    def test_turbo_single_stage_matches_by_name(self):
        entry = load_shot("Turbo")
        joined = join_to_profile(segment(entry["data"]), entry["profile"])
        brew = [j for j in joined if j["status"] != "retracting"]
        self.assertTrue(brew[0]["matched"])
        self.assertEqual(brew[0]["stage"]["type"], "pressure")

    def test_retracting_never_matches(self):
        entry = load_shot("Turbo")
        joined = join_to_profile(segment(entry["data"]), entry["profile"])
        retract = [j for j in joined if j["status"] == "retracting"]
        self.assertEqual(len(retract), 1)
        self.assertFalse(retract[0]["matched"])
        self.assertIsNone(retract[0]["stage"])

    def test_unmatched_runs_are_kept_not_dropped(self):
        entry = load_shot("Traditional Lever")
        joined = join_to_profile(segment(entry["data"]), entry["profile"])
        self.assertEqual(len(joined), 5)

if __name__ == "__main__":
    unittest.main()
