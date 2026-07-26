import copy, json, pathlib, unittest
from shotcraft.check import check_shot

FIX = pathlib.Path(__file__).parent / "fixtures"

def load_shot(name):
    hist = json.loads((FIX / "history.json").read_text())["history"]
    for entry in hist:
        if entry["name"].strip() == name:
            return entry
    raise AssertionError(f"no fixture shot named {name}")

class TestCheckShot(unittest.TestCase):
    def test_returns_a_result_per_run(self):
        results = check_shot(load_shot("Traditional Lever"))
        per_run = [r for r in results if r["status"] != "(shot total)"]
        self.assertEqual(len(per_run), 5)

    def test_retracting_is_not_checkable(self):
        results = check_shot(load_shot("Turbo"))
        retract = [r for r in results if r["status"] == "retracting"][0]
        self.assertFalse(retract["checkable"])
        self.assertIn("not a profile stage", retract["reason"])

    def test_turbo_pressure_stage_is_checkable(self):
        results = check_shot(load_shot("Turbo"))
        brew = [r for r in results if r["checkable"]][0]
        self.assertEqual(brew["type"], "pressure")
        self.assertIsNotNone(brew["mean_actual"])
        self.assertIsNotNone(brew["mean_intended"])

    def test_turbo_underdelivered_pressure(self):
        # measured peak was 2.58 bar against a 6 bar intent, so the mean
        # actual must sit well below the mean intended
        results = check_shot(load_shot("Turbo"))
        brew = [r for r in results if r["checkable"]][0]
        self.assertLess(brew["mean_actual"], brew["mean_intended"])
        self.assertGreater(brew["mean_abs_deviation"], 1.0)

    def test_curve_stages_are_flagged_as_approximated(self):
        results = check_shot(load_shot("Turbo"))
        brew = [r for r in results if r["checkable"]][0]
        self.assertTrue(brew["approximated"])

    def test_unmatched_status_reports_reason(self):
        # The fixture has no naturally-occurring unmatched, non-retracting run
        # (every real status besides "retracting" happens to match a profile
        # stage), so a plain filter over the raw fixture is vacuous. Synthesize
        # one: rename a real mid-shot status to something absent from the
        # profile's stage list, on every sample that carries it.
        baseline_entry = load_shot("Traditional Lever")
        baseline_results = check_shot(baseline_entry)

        entry = copy.deepcopy(baseline_entry)
        stage_names = {s["name"].strip() for s in entry["profile"]["stages"]}
        self.assertNotIn("Vanished Stage", stage_names, "synthetic name collides with a real stage")
        renamed = 0
        for sample in entry["data"]:
            if (sample.get("status") or "").strip() == "Infuse":
                sample["status"] = "Vanished Stage"
                renamed += 1
        self.assertGreater(renamed, 0, "fixture lost its Infuse-status samples")

        results = check_shot(entry)

        unmatched = [r for r in results
                     if not r["matched"] and r["status"] not in ("retracting", "(shot total)")]
        # Guard against silent vacuity: this loop must actually execute, or
        # every assertion below is dead code that can never fail.
        self.assertGreaterEqual(len(unmatched), 1,
                                 "no unmatched non-retracting run present; test would be vacuous")
        for r in unmatched:
            self.assertEqual(r["status"], "Vanished Stage")
            self.assertFalse(r["matched"])
            self.assertFalse(r["checkable"])
            self.assertTrue(r["reason"])
            # not the shot-total's specific reason string
            self.assertNotEqual(r["reason"], "no declared final_weight")

        # one bad run must not disturb the verdicts of the rest of the shot
        other_baseline = [r for r in baseline_results if r["status"] != "Infuse"]
        other_mutated = [r for r in results if r["status"] != "Vanished Stage"]
        self.assertEqual(other_mutated, other_baseline)

    def test_null_points_degrades_to_unchecked_without_crashing(self):
        entry = copy.deepcopy(load_shot("Traditional Lever"))
        for stage in entry["profile"]["stages"]:
            if stage["name"].strip() == "Infuse":
                stage["dynamics"]["points"] = None
                break
        results = check_shot(entry)
        infuse = [r for r in results if r["status"] == "Infuse"][0]
        self.assertFalse(infuse["checkable"])
        self.assertTrue(infuse["reason"])
        other_statuses = {r["status"] for r in results}
        self.assertTrue({"Fill", "Pressure Up"} <= other_statuses)
        fill = [r for r in results if r["status"] == "Fill"][0]
        self.assertTrue(fill["checkable"])

    def test_shot_total_compares_target_to_actual_weight(self):
        results = check_shot(load_shot("Turbo"))
        total = [r for r in results if r["status"] == "(shot total)"][0]
        self.assertTrue(total["checkable"])
        self.assertEqual(total["target_weight_g"], 50.0)
        self.assertAlmostEqual(total["actual_weight_g"], 50.85, places=2)

    def test_null_profile_time_sample_does_not_crash(self):
        entry = copy.deepcopy(load_shot("Turbo"))
        entry["data"][5]["profile_time"] = None     # present, explicitly null
        results = check_shot(entry)
        self.assertTrue(any(r["checkable"] for r in results))

    def test_all_results_share_one_key_set(self):
        results = check_shot(load_shot("Traditional Lever"))
        keys = {frozenset(r) for r in results}
        self.assertEqual(len(keys), 1, "result dicts must have a uniform shape")

if __name__ == "__main__":
    unittest.main()


class TestLimitAwareness(unittest.TestCase):
    """A stage can declare a limit that legitimately prevents it reaching its
    own dynamics target. Scoring that as a miss is a false positive: the
    machine obeyed the profile. See the Linea Mini 'Brew' stage, which asks
    for 11.3 ml/s capped at 6.5 bar."""

    def _brew(self):
        entry = load_shot("Linea Mini at Home")
        return [r for r in check_shot(entry) if r["status"] == "Brew"][0]

    def test_declared_limits_are_resolved_and_reported(self):
        brew = self._brew()
        self.assertEqual(brew["limits"], [{"type": "pressure", "value": 6.5}])

    def test_stage_held_at_its_limit_is_marked_limit_bound(self):
        brew = self._brew()
        self.assertTrue(brew["limit_bound"])
        self.assertIn("pressure", brew["limit_note"])

    def test_limit_bound_stage_still_reports_its_raw_deviation(self):
        # we surface the constraint, we do not suppress the measurement
        brew = self._brew()
        self.assertTrue(brew["checkable"])
        self.assertIsNotNone(brew["mean_abs_deviation"])

    def test_stage_without_limits_is_not_limit_bound(self):
        entry = load_shot("Traditional Lever")
        infuse = [r for r in check_shot(entry) if r["status"] == "Infuse"][0]
        self.assertFalse(infuse["limit_bound"])
        self.assertEqual(infuse["limit_note"], "")

    def test_unresolvable_limit_variable_does_not_crash(self):
        entry = copy.deepcopy(load_shot("Linea Mini at Home"))
        for stage in entry["profile"]["stages"]:
            if stage["name"].strip() == "Brew":
                stage["limits"] = [{"type": "pressure", "value": "$nope"}]
        brew = [r for r in check_shot(entry) if r["status"] == "Brew"][0]
        self.assertEqual(brew["limits"], [])
        self.assertFalse(brew["limit_bound"])

    def test_null_limits_do_not_crash(self):
        entry = copy.deepcopy(load_shot("Linea Mini at Home"))
        for stage in entry["profile"]["stages"]:
            stage["limits"] = None
        brew = [r for r in check_shot(entry) if r["status"] == "Brew"][0]
        self.assertEqual(brew["limits"], [])

    def test_key_set_stays_uniform_with_the_new_fields(self):
        results = check_shot(load_shot("Linea Mini at Home"))
        self.assertEqual(len({frozenset(r) for r in results}), 1)
        for key in ("limits", "limit_bound", "limit_note"):
            self.assertIn(key, results[0])
