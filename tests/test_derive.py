import json, pathlib, unittest
from shotcraft.derive import derive

FIX = pathlib.Path(__file__).parent / "fixtures"

def load_shot(name, occurrence=0):
    hist = json.loads((FIX / "history.json").read_text())["history"]
    matches = [e for e in hist if e["name"].strip() == name]
    if len(matches) <= occurrence:
        raise AssertionError(f"no fixture shot {name!r} #{occurrence}")
    return matches[occurrence]

class TestDerive(unittest.TestCase):
    def test_yield_is_final_weight_sample(self):
        d = derive(load_shot("Turbo"))
        self.assertAlmostEqual(d["yield_g"], 50.85, places=2)

    def test_yield_is_final_weight_not_max(self):
        # this shot's final weight is BELOW its max; a max() regression fails here
        entry = load_shot("Italian Style")
        weights = [s["shot"]["weight"] for s in entry["data"] if "shot" in s]
        self.assertLess(weights[-1], max(weights), "fixture no longer discriminates")
        self.assertAlmostEqual(derive(entry)["yield_g"], weights[-1], places=2)

    def test_time_is_seconds_not_milliseconds(self):
        d = derive(load_shot("Turbo"))
        # brew ends before the trailing retracting phase, so under 21.8s
        self.assertLess(d["time_s"], 21.8)
        self.assertGreater(d["time_s"], 5.0)

    def test_peaks_come_from_shot_channel(self):
        d = derive(load_shot("Turbo"))
        self.assertAlmostEqual(d["peak_pressure"], 2.58, places=2)

    def test_traditional_lever_peak_pressure(self):
        d = derive(load_shot("Traditional Lever"))
        self.assertAlmostEqual(d["peak_pressure"], 6.45, places=2)

    def test_empty_data_yields_nulls(self):
        d = derive({"data": []})
        self.assertIsNone(d["yield_g"])
        self.assertIsNone(d["time_s"])
        self.assertIsNone(d["peak_pressure"])

    def test_missing_data_key_yields_nulls(self):
        d = derive({})
        self.assertIsNone(d["yield_g"])

if __name__ == "__main__":
    unittest.main()
