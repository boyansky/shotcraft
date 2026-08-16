import json, pathlib, unittest
from shotcraft.model import (FLAG_TELEMETRY, TASTE_SCHEMA, validate_taste,
                            shot_row, flagged_row, days_off_roast, compute_ratio)

FIX = pathlib.Path(__file__).parent / "fixtures"

def load_shot(name):
    hist = json.loads((FIX / "history.json").read_text())["history"]
    for entry in hist:
        if entry["name"].strip() == name:
            return entry
    raise AssertionError(f"no fixture shot named {name}")

class TestComputed(unittest.TestCase):
    def test_ratio(self):
        self.assertAlmostEqual(compute_ratio(50.2, 18.0), 2.79, places=2)

    def test_ratio_none_when_dose_missing(self):
        self.assertIsNone(compute_ratio(50.2, None))

    def test_ratio_none_when_dose_zero(self):
        self.assertIsNone(compute_ratio(50.2, 0))

    def test_days_off_roast(self):
        self.assertEqual(days_off_roast("2026-07-25T12:44:27", "2026-07-18"), 7)

    def test_days_off_roast_none_without_bag(self):
        self.assertIsNone(days_off_roast("2026-07-25T12:44:27", None))

class TestShotRow(unittest.TestCase):
    def test_unrated_row_is_valid_with_nulls(self):
        row = shot_row(load_shot("Turbo"))
        self.assertIsNone(row["dose_g"])
        self.assertIsNone(row["taste"])
        self.assertIsNone(row["bag"])

    def test_unrated_row_has_no_taste_schema(self):
        # schema is stamped when a rating is entered, never at sync time
        row = shot_row(load_shot("Turbo"))
        self.assertIsNone(row["taste_schema"])

    def test_rated_row_is_stamped_with_current_schema(self):
        row = shot_row(load_shot("Turbo"),
                       taste={"lean": "sour", "intensity": 2, "versus": None})
        self.assertEqual(row["taste_schema"], TASTE_SCHEMA)

    def test_days_off_roast_is_never_stored(self):
        # it is computed at read time from the bag, not frozen at sync
        self.assertNotIn("days_off_roast", shot_row(load_shot("Turbo")))

    def test_ts_is_seconds_not_milliseconds(self):
        # entry["time"] is a float unix timestamp in SECONDS
        row = shot_row(load_shot("Turbo"))
        self.assertTrue(row["ts"].startswith("2026-"), row["ts"])

    def test_machine_fields_populated(self):
        row = shot_row(load_shot("Turbo"))
        self.assertEqual(row["profile"], "Turbo")
        self.assertAlmostEqual(row["yield_g"], 50.85, places=2)
        self.assertIsInstance(row["id"], str)
        self.assertTrue(row["ts"].startswith("20"))

    def test_human_fields_flow_through_and_ratio_computes(self):
        row = shot_row(load_shot("Turbo"), dose_g=18.0, grind=2.8)
        self.assertEqual(row["grind"], 2.8)
        self.assertAlmostEqual(row["ratio"], 2.83, places=2)

    def test_invalid_taste_rejected_at_row_construction(self):
        with self.assertRaises(ValueError):
            shot_row(load_shot("Turbo"),
                     taste={"lean": "sour", "intensity": 99, "versus": None})

    def test_healthy_row_carries_an_empty_flag_list(self):
        self.assertEqual(shot_row(load_shot("Turbo"))["flags"], [])

class TestFlaggedRow(unittest.TestCase):
    def test_flagged_row_keeps_the_id_and_names_the_failure(self):
        entry = {**load_shot("Turbo"), "time": None}
        row = flagged_row(entry, "time is null")
        self.assertEqual(row["id"], entry["id"])
        self.assertEqual(len(row["flags"]), 1)
        self.assertIn(FLAG_TELEMETRY, row["flags"][0])

    def test_flagged_row_has_null_machine_fields(self):
        row = flagged_row({**load_shot("Turbo"), "time": None}, "boom")
        for field in ("ts", "yield_g", "time_s", "ratio",
                      "peak_pressure", "peak_flow", "taste", "taste_schema"):
            self.assertIsNone(row[field], field)

    def test_flagged_row_has_the_same_shape_as_a_healthy_one(self):
        # rows are read back by the same code, so the shapes must not diverge
        self.assertEqual(set(flagged_row(load_shot("Turbo"), "boom")),
                         set(shot_row(load_shot("Turbo"))))

    def test_flagged_row_recovers_the_profile_name_when_it_can(self):
        row = flagged_row({**load_shot("Turbo"), "time": None}, "boom")
        self.assertEqual(row["profile"], "Turbo")

    def test_flagged_row_survives_an_entry_with_nothing_in_it(self):
        row = flagged_row({}, "boom")
        self.assertIsNone(row["id"])
        self.assertEqual(row["profile"], "")

    def test_days_off_roast_none_without_timestamp(self):
        # a flagged row can carry a null ts; the report must not crash on it
        self.assertIsNone(days_off_roast(None, "2026-07-18"))

if __name__ == "__main__":
    unittest.main()


from shotcraft.model import grinder_row, validate_grinder


class TestGrinder(unittest.TestCase):
    def test_row_carries_every_field(self):
        row = grinder_row("1Zpresso", "K-Ultra", "decimal dial 0.0-9.0", "lower")
        self.assertEqual(row["make"], "1Zpresso")
        self.assertEqual(row["model"], "K-Ultra")
        self.assertEqual(row["scale"], "decimal dial 0.0-9.0")
        self.assertEqual(row["finer_direction"], "lower")
        self.assertEqual(row["note"], "")

    def test_finer_direction_must_be_lower_or_higher(self):
        with self.assertRaises(ValueError):
            validate_grinder({"make": "x", "model": "y", "scale": "z",
                              "finer_direction": "clockwise"})

    def test_model_is_required(self):
        with self.assertRaises(ValueError):
            validate_grinder({"make": "1Zpresso", "model": "", "scale": "z",
                              "finer_direction": "lower"})
