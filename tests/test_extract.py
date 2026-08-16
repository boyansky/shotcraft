import unittest

from shotcraft.extract import (agreement_tally, expected_lean, grade,
                               matched_pair, pair_note,
                               weight_outcome)


def entry(target=40.0, actual=40.0):
    samples = [{"shot": {"pressure": 6.0, "flow": 2.0, "weight": actual},
                "profile_time": 20000, "status": "infusion"}]
    return {"id": "abc", "time": 1786351889.0,
            "profile": {"name": "Turbo", "final_weight": target},
            "data": samples}


def rating(lean, intensity=2):
    return {"lean": lean, "intensity": intensity, "versus": None}


class TestWeightOutcome(unittest.TestCase):
    def test_deficit_is_actual_minus_target(self):
        out = weight_outcome(entry(target=40.0, actual=35.0))
        self.assertEqual(out["deficit"], -5.0)

    def test_no_declared_target_gives_none(self):
        e = entry()
        e["profile"].pop("final_weight")
        self.assertIsNone(weight_outcome(e)["target"])

    def test_no_weight_samples_gives_none(self):
        e = entry()
        e["data"] = [{"shot": {"pressure": 6.0}, "profile_time": 1000}]
        self.assertIsNone(weight_outcome(e)["actual"])


class TestExpectedLean(unittest.TestCase):
    def test_short_shot_should_read_sour(self):
        self.assertEqual(expected_lean(weight_outcome(entry(40.0, 35.0))), "sour")

    def test_long_shot_should_read_bitter(self):
        self.assertEqual(expected_lean(weight_outcome(entry(40.0, 45.0))), "bitter")

    def test_clean_shot_expects_nothing(self):
        self.assertIsNone(expected_lean(weight_outcome(entry(40.0, 40.5))))

    def test_threshold_floor_applies_to_small_targets(self):
        # 5% of 20g is 1.0g, below the 2.0g floor, so 1.5g is still clean
        self.assertIsNone(expected_lean(weight_outcome(entry(20.0, 18.5))))
        self.assertEqual(expected_lean(weight_outcome(entry(20.0, 17.9))), "sour")

    def test_percentage_applies_to_large_targets(self):
        # 5% of 90g is 4.5g, above the floor, so 3g short is still clean
        self.assertIsNone(expected_lean(weight_outcome(entry(90.0, 87.0))))
        self.assertEqual(expected_lean(weight_outcome(entry(90.0, 85.0))), "sour")


class TestGrade(unittest.TestCase):
    def test_agreement_when_call_matches_the_miss(self):
        result = grade(entry(40.0, 35.0), rating("sour"))
        self.assertTrue(result["gradeable"])
        self.assertTrue(result["agreed"])

    def test_disagreement_when_call_opposes_the_miss(self):
        result = grade(entry(40.0, 35.0), rating("bitter"))
        self.assertTrue(result["gradeable"])
        self.assertFalse(result["agreed"])

    def test_none_lean_against_a_miss_is_a_disagreement(self):
        result = grade(entry(40.0, 35.0), rating("none", intensity=0))
        self.assertTrue(result["gradeable"])
        self.assertFalse(result["agreed"])

    def test_both_is_never_graded(self):
        result = grade(entry(40.0, 35.0), rating("both"))
        self.assertFalse(result["gradeable"])
        self.assertIn("both", result["reason"])

    def test_clean_shot_is_never_graded(self):
        result = grade(entry(40.0, 40.2), rating("sour"))
        self.assertFalse(result["gradeable"])
        self.assertIn("tracked", result["reason"])

    def test_missing_target_is_never_graded(self):
        e = entry()
        e["profile"].pop("final_weight")
        result = grade(e, rating("sour"))
        self.assertFalse(result["gradeable"])
        self.assertIn("final_weight", result["reason"])

    def test_no_weight_samples_is_never_graded(self):
        # declared target present, but the telemetry carries no weight
        # channel at all — distinct from the missing-target case above, and
        # must stay reachable through grade() itself, not just weight_outcome
        e = entry()
        e["data"] = [{"shot": {"pressure": 6.0}, "profile_time": 1000}]
        result = grade(e, rating("sour"))
        self.assertFalse(result["gradeable"])
        self.assertIn("weight samples", result["reason"])

    def test_unrated_shot_is_never_graded(self):
        result = grade(entry(40.0, 35.0), None)
        self.assertFalse(result["gradeable"])


class TestAgreementTally(unittest.TestCase):
    def test_matching_call_counts_in_both_agreed_and_gradeable(self):
        rows = [{"id": "a", "taste_schema": 3, "taste": rating("sour")}]
        telemetry = {"a": entry(40.0, 35.0)}
        self.assertEqual(agreement_tally(rows, telemetry.get), (1, 1))

    def test_opposing_call_counts_in_gradeable_but_not_agreed(self):
        rows = [{"id": "a", "taste_schema": 3, "taste": rating("bitter")}]
        telemetry = {"a": entry(40.0, 35.0)}
        self.assertEqual(agreement_tally(rows, telemetry.get), (0, 1))

    def test_other_schema_is_skipped_entirely(self):
        # pins GRADEABLE_SCHEMA: a schema-2 row is excluded outright, not
        # coerced into schema 3's shape
        rows = [{"id": "a", "taste_schema": 2, "taste": rating("sour")}]
        telemetry = {"a": entry(40.0, 35.0)}
        self.assertEqual(agreement_tally(rows, telemetry.get), (0, 0))

    def test_row_with_no_taste_is_skipped(self):
        rows = [{"id": "a", "taste_schema": 3, "taste": None}]
        telemetry = {"a": entry(40.0, 35.0)}
        self.assertEqual(agreement_tally(rows, telemetry.get), (0, 0))

    def test_missing_telemetry_is_skipped_not_raised(self):
        rows = [{"id": "missing", "taste_schema": 3, "taste": rating("sour")}]
        self.assertEqual(agreement_tally(rows, lambda shot_id: None), (0, 0))


def row(shot_id, ts, bag="b001", profile="Turbo", yield_g=40.0, grind=3.1):
    return {"id": shot_id, "ts": ts, "bag": bag, "profile": profile,
            "yield_g": yield_g, "grind": grind, "grinder": "g001"}


class TestMatchedPair(unittest.TestCase):
    def test_finds_most_recent_same_bag_and_profile(self):
        rows = [row("a", "2026-08-01T07:00:00"), row("b", "2026-08-05T07:00:00"),
                row("c", "2026-08-09T07:00:00")]
        self.assertEqual(matched_pair(rows, rows[2])["id"], "b")

    def test_ignores_a_different_profile(self):
        rows = [row("a", "2026-08-01T07:00:00", profile="Allonge"),
                row("b", "2026-08-09T07:00:00")]
        self.assertIsNone(matched_pair(rows, rows[1]))

    def test_ignores_a_different_bag(self):
        rows = [row("a", "2026-08-01T07:00:00", bag="b002"),
                row("b", "2026-08-09T07:00:00")]
        self.assertIsNone(matched_pair(rows, rows[1]))

    def test_never_looks_forward(self):
        rows = [row("a", "2026-08-01T07:00:00"), row("b", "2026-08-09T07:00:00")]
        self.assertIsNone(matched_pair(rows, rows[0]))


class TestPairNote(unittest.TestCase):
    def test_reports_the_gap_in_days(self):
        note = pair_note(row("b", "2026-08-09T07:00:00"),
                         row("a", "2026-08-01T07:00:00"))
        self.assertEqual(note["days"], 8)

    def test_big_yield_gap_on_the_same_dial_is_suspect(self):
        note = pair_note(row("b", "2026-08-09T07:00:00", yield_g=40.0),
                         row("a", "2026-08-08T07:00:00", yield_g=25.0))
        self.assertTrue(note["suspect_redial"])

    def test_small_yield_gap_is_not_suspect(self):
        note = pair_note(row("b", "2026-08-09T07:00:00", yield_g=40.0),
                         row("a", "2026-08-08T07:00:00", yield_g=38.0))
        self.assertFalse(note["suspect_redial"])

    def test_different_grind_is_never_suspect(self):
        # the setting was logged, so a yield change is explained
        note = pair_note(row("b", "2026-08-09T07:00:00", yield_g=40.0, grind=3.4),
                         row("a", "2026-08-08T07:00:00", yield_g=25.0, grind=3.1))
        self.assertFalse(note["suspect_redial"])

    def test_two_undialled_shots_are_never_suspect(self):
        # `None == None` is True in Python; without an explicit guard this
        # reads as "the same logged dial-in" when no dial-in has EVER been
        # logged for either shot -- the real state before any `dial` has run
        note = pair_note(row("b", "2026-08-09T07:00:00", yield_g=40.0, grind=None),
                         row("a", "2026-08-08T07:00:00", yield_g=25.0, grind=None))
        self.assertFalse(note["suspect_redial"])


class TestFlaggedRowsExcludedFromTally(unittest.TestCase):
    """The prior version of this test (named TestFlaggedRowIsNeverGraded) fed
    grade() an entry with `data: []`, which only ever exercises the
    no-weight-samples branch -- true for ANY empty-data entry, flagged or not,
    so it pinned nothing about flaggedness specifically. The row's `flags`
    field is what actually marks a row flagged, and grade() never receives
    the row, only the raw telemetry entry -- so the real fix lives in
    agreement_tally, which does see the row.
    """

    def test_a_flagged_row_is_excluded_even_though_its_raw_telemetry_would_grade(self):
        # this entry alone would grade cleanly and agree (real deficit, real
        # weight samples, matching call) -- (1, 1) if flags were ignored, so
        # a filter that merely happened to also catch empty-data rows would
        # not be caught by this test. Only the row's own `flags` field
        # explains the (0, 0) below.
        rows = [{"id": "a", "taste_schema": 3, "taste": rating("sour"),
                 "flags": ["telemetry_unparsed: KeyError: 'time'"]}]
        telemetry = {"a": entry(40.0, 35.0)}
        self.assertEqual(agreement_tally(rows, telemetry.get), (0, 0))
