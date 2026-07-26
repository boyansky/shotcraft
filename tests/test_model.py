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

class TestTaste(unittest.TestCase):
    def test_valid_taste_passes(self):
        validate_taste({"sour": 2, "bitter": 1, "body": 5, "overall": 6})

    def test_none_is_allowed_unrated(self):
        validate_taste(None)

    def test_sour_below_range_raises(self):
        with self.assertRaises(ValueError):
            validate_taste({"sour": -1, "bitter": 1, "body": 5, "overall": 6})

    def test_body_above_range_raises(self):
        with self.assertRaises(ValueError):
            validate_taste({"sour": 2, "bitter": 1, "body": 11, "overall": 6})

    def test_missing_key_raises(self):
        with self.assertRaises(ValueError):
            validate_taste({"sour": 2, "bitter": 1, "body": 5})

    def test_non_integer_raises(self):
        with self.assertRaises(ValueError):
            validate_taste({"sour": 0.5, "bitter": 1, "body": 5, "overall": 6})

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
                       taste={"sour": 2, "bitter": 1, "body": 5, "overall": 6})
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
            shot_row(load_shot("Turbo"), taste={"sour": 99, "bitter": 1, "body": 5, "overall": 6})

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


class TestSchemaV2(unittest.TestCase):
    """v2 split sour_bitter into two axes because a shot can be both at once,
    and widened every axis to 0-10 because 1-5 piled onto 3 and 4."""

    GOOD = {"sour": 6, "bitter": 5, "body": 7, "overall": 4}

    def test_schema_version_is_two(self):
        self.assertEqual(TASTE_SCHEMA, 2)

    def test_sour_and_bitter_are_independent_axes(self):
        # the case v1 could not express: both high at once = uneven extraction
        validate_taste({"sour": 8, "bitter": 8, "body": 5, "overall": 2})

    def test_all_axes_span_zero_to_ten_inclusive(self):
        validate_taste({"sour": 0, "bitter": 0, "body": 0, "overall": 0})
        validate_taste({"sour": 10, "bitter": 10, "body": 10, "overall": 10})

    def test_above_ten_raises(self):
        with self.assertRaises(ValueError):
            validate_taste({**self.GOOD, "sour": 11})

    def test_below_zero_raises(self):
        with self.assertRaises(ValueError):
            validate_taste({**self.GOOD, "bitter": -1})

    def test_old_v1_shape_is_rejected_by_the_current_validator(self):
        # v1 rows are never re-validated, but a v1-shaped NEW rating must fail
        with self.assertRaises(ValueError):
            validate_taste({"sour_bitter": 0, "body": 3, "overall": 4})

    def test_decimals_still_rejected(self):
        with self.assertRaises(ValueError):
            validate_taste({**self.GOOD, "overall": 7.5})

    def test_rated_row_stamps_schema_two(self):
        row = shot_row(load_shot("Turbo"), taste=self.GOOD)
        self.assertEqual(row["taste_schema"], 2)
