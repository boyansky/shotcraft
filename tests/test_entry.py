import contextlib, io, json, pathlib, tempfile, unittest
from shotcraft.entry import current_bag_id, default_dose, new_bag, rate_shot
from shotcraft.model import TASTE_SCHEMA, flagged_row
from shotcraft.store import Store

def scripted(answers):
    """Return an `ask` callable that replays answers in order."""
    it = iter(answers)
    return lambda prompt: next(it)

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.store = Store(self.root)
        self.store.append_shots([
            {"id": "s1", "ts": "2026-07-25T12:44:27", "bag": None, "profile": "Turbo",
             "dose_g": None, "grind": None, "yield_g": 50.8, "time_s": 21.0,
             "ratio": None, "taste": None, "taste_schema": None, "note": ""},
        ])

    def tearDown(self):
        self.tmp.cleanup()

class TestDefaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_bag_is_none_when_no_bags(self):
        self.assertIsNone(current_bag_id(self.store))

    def test_current_bag_is_most_recently_added(self):
        self.store.append_bag({"id": "b001", "roaster": "X", "roast_date": "2026-07-01"})
        self.store.append_bag({"id": "b002", "roaster": "Y", "roast_date": "2026-07-20"})
        self.assertEqual(current_bag_id(self.store), "b002")

    def test_default_dose_is_none_when_no_rated_shots(self):
        self.assertIsNone(default_dose(self.store))

    def test_default_dose_is_latest_non_null(self):
        self.store.append_shots([
            {"id": "a", "ts": "2026-07-24T09:00:00", "dose_g": 17.5},
            {"id": "b", "ts": "2026-07-25T09:00:00", "dose_g": 18.0},
            {"id": "c", "ts": "2026-07-25T10:00:00", "dose_g": None},
        ])
        self.assertEqual(default_dose(self.store), 18.0)

    def test_default_dose_uses_timestamp_not_file_order(self):
        # sync writes newest-first (the machine's history endpoint returns
        # newest-first), so file order must never stand in for chronology.
        self.store.append_shots([
            {"id": "newest", "ts": "2026-07-25T10:00:00", "dose_g": 22.0},
            {"id": "oldest", "ts": "2026-07-24T09:00:00", "dose_g": 16.0},
        ])
        self.assertEqual(default_dose(self.store), 22.0)

class TestRateShot(Base):
    def test_rating_is_stored_and_stamped(self):
        row = rate_shot(self.store, "s1",
                        scripted(["", "18.0", "2.8", "1", "6", "3", "4", ""]))
        self.assertEqual(row["taste"], {"sour": 1, "bitter": 6, "body": 3, "overall": 4})
        self.assertEqual(row["taste_schema"], TASTE_SCHEMA)
        self.assertEqual(row["dose_g"], 18.0)
        self.assertEqual(row["grind"], 2.8)

    def test_ratio_is_recomputed_from_entered_dose(self):
        row = rate_shot(self.store, "s1",
                        scripted(["", "18.0", "2.8", "0", "0", "3", "4", ""]))
        self.assertAlmostEqual(row["ratio"], 2.82, places=2)

    def test_rating_persists_to_disk(self):
        rate_shot(self.store, "s1", scripted(["", "18.0", "2.8", "0", "0", "3", "4", ""]))
        stored = [r for r in self.store.load_shots() if r["id"] == "s1"][0]
        self.assertEqual(stored["taste"]["overall"], 4)

    def test_out_of_range_rating_raises_and_stores_nothing(self):
        with self.assertRaises(ValueError):
            rate_shot(self.store, "s1",
                      scripted(["", "18.0", "2.8", "99", "0", "3", "4", ""]))
        stored = [r for r in self.store.load_shots() if r["id"] == "s1"][0]
        self.assertIsNone(stored["taste"])

    def test_unknown_shot_raises(self):
        with self.assertRaises(KeyError):
            rate_shot(self.store, "nope", scripted([]))

    def test_prompts_and_stdout_never_mention_machine_numbers(self):
        # The branch's one Critical rule (Amendment 1.1). rate_shot speaks on
        # TWO channels: the strings it hands to `ask`, and whatever it prints.
        # Watching only the prompts let a print() of yield/ratio/time through
        # with the whole suite still green, so both channels are captured and
        # the forbidden values are taken from the stored row rather than
        # hardcoded, so this can never drift away from the fixture.
        row = [r for r in self.store.load_shots() if r["id"] == "s1"][0]
        seen = []
        def spy(prompt):
            seen.append(prompt)
            return next(answers)
        answers = iter(["", "18.0", "2.8", "0", "0", "3", "4", ""])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rate_shot(self.store, "s1", spy)

        forbidden = ["yield", "ratio", "pressure", "flow",
                     str(row["yield_g"]), str(row["time_s"])]
        channels = {"prompts": " ".join(seen).lower(),
                    "stdout": stdout.getvalue().lower()}
        for name, blob in channels.items():
            for word in forbidden:
                self.assertNotIn(word.lower(), blob, f"{name} leaked {word!r}")

    def test_rate_a_flagged_shot_does_not_crash(self):
        # sync lists flagged shots under "awaiting taste ratings" and tells you
        # to rate them, so rating one must work. The row is built by the real
        # flagged_row so this test tracks the real shape, not a guess at it.
        row = flagged_row({"id": "bad1", "time": None, "name": "Turbo"},
                          "TypeError: telemetry unreadable")
        self.assertIsNone(row["ts"])            # the shape that used to crash
        self.store.append_shots([row])

        stored = rate_shot(self.store, "bad1",
                           scripted(["", "18.0", "2.8", "1", "6", "3", "4", ""]))
        self.assertEqual(stored["taste"],
                         {"sour": 1, "bitter": 6, "body": 3, "overall": 4})
        self.assertEqual(stored["dose_g"], 18.0)
        self.assertIsNone(stored["ratio"])      # no yield to divide
        self.assertTrue(stored["flags"])        # still flagged after rating

    def test_rating_a_flagged_shot_prints_no_machine_values(self):
        # the flagged path prints a different line, so it must obey
        # Amendment 1.1 too. Its stand-in text is descriptive, never derived.
        self.store.append_shots([flagged_row({"id": "bad1", "time": None}, "x")])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rate_shot(self.store, "bad1",
                      scripted(["", "18.0", "2.8", "0", "0", "3", "4", ""]))
        printed = stdout.getvalue().lower()
        self.assertIn("(time unknown)", printed)
        for word in ("yield", "ratio", "pressure", "flow", "none"):
            self.assertNotIn(word, printed, f"stdout leaked {word!r}")

    def test_default_dose_survives_a_rated_flagged_row(self):
        # a rated flagged row carries a dose but no ts; sorting that against a
        # real timestamp must not raise
        self.store.append_shots([
            {**flagged_row({"id": "bad1", "time": None}, "x"), "dose_g": 17.0},
            {"id": "good", "ts": "2026-07-25T09:00:00", "dose_g": 18.0},
        ])
        self.assertEqual(default_dose(self.store), 18.0)

    def test_unknown_bag_is_rejected_and_stores_nothing(self):
        with self.assertRaises(ValueError):
            rate_shot(self.store, "s1",
                      scripted(["b999", "18.0", "2.8", "0", "0", "3", "4", ""]))
        stored = [r for r in self.store.load_shots() if r["id"] == "s1"][0]
        self.assertIsNone(stored["bag"])
        self.assertIsNone(stored["taste"])

    def test_known_bag_is_accepted(self):
        self.store.append_bag({"id": "b001", "roaster": "X",
                               "roast_date": "2026-07-18"})
        row = rate_shot(self.store, "s1",
                        scripted(["b001", "18.0", "2.8", "0", "0", "3", "4", ""]))
        self.assertEqual(row["bag"], "b001")

class TestNewBag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_bag_is_created_with_generated_id(self):
        bag = new_bag(self.store, scripted(
            ["Square Mile", "Red Brick", "washed", "2026-07-18", ""]))
        self.assertEqual(bag["id"], "b001")
        self.assertEqual(bag["roaster"], "Square Mile")
        self.assertEqual(bag["roast_date"], "2026-07-18")

    def test_ids_increment(self):
        new_bag(self.store, scripted(["A", "1", "washed", "2026-07-18", ""]))
        second = new_bag(self.store, scripted(["B", "2", "natural", "2026-07-20", ""]))
        self.assertEqual(second["id"], "b002")

    def test_bad_roast_date_raises_and_stores_nothing(self):
        with self.assertRaises(ValueError):
            new_bag(self.store, scripted(["A", "1", "washed", "18-07-2026", ""]))
        self.assertEqual(self.store.load_bags(), [])

    def test_bag_id_does_not_collide_after_a_bag_is_removed(self):
        new_bag(self.store, scripted(["A", "1", "washed", "2026-07-18", ""]))
        survivor = new_bag(self.store, scripted(["B", "2", "natural", "2026-07-20", ""]))
        # simulate a human hand-editing bags.jsonl and removing the first line
        lines = self.store.bags_path.read_text().splitlines()
        self.store.bags_path.write_text("\n".join(lines[1:]) + "\n")
        third = new_bag(self.store, scripted(["C", "3", "honey", "2026-07-22", ""]))
        self.assertNotEqual(third["id"], survivor["id"])
        bags = self.store.load_bags()
        self.assertEqual({b["id"] for b in bags}, {survivor["id"], third["id"]})

    def test_new_bag_raises_when_id_collides(self):
        # force the collision path directly: append_bag refuses because the
        # id already exists. new_bag must surface that as a ValueError, not
        # silently return a bag dict that was never actually saved.
        self.store.append_bag = lambda bag: 0
        with self.assertRaises(ValueError):
            new_bag(self.store, scripted(["A", "1", "washed", "2026-07-18", ""]))

if __name__ == "__main__":
    unittest.main()
