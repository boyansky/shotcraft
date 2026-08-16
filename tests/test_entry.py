import contextlib, datetime, io, pathlib, tempfile, unittest
from shotcraft.entry import current_bag_id, new_bag, rate_shot
from shotcraft.entry import default_grinder_id, new_grinder
from shotcraft.entry import record_dial, resolve_dial
from shotcraft.model import flagged_row
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

class TestRateShot(Base):
    def setUp(self):
        super().setUp()
        # rate_shot prints its two blind-rating lines. Nothing here reads them
        # except the no-machine-output pins below, which install their own
        # capture and nest cleanly inside this one, so swallow the rest
        # rather than let the suite's own output carry them.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        new_grinder(self.store, scripted(["1Zpresso", "K-Ultra", "d", "lower", ""]))
        new_bag(self.store, scripted(["roaster", "name", "washed", "2026-08-01", ""]))
        record_dial(self.store, 3.1, dose_g=18.0, bag="b001",
                    now=datetime.datetime(2026, 8, 1, 6, 0))
        self.store.append_shots([{
            "id": "shot1", "ts": "2026-08-02T07:00:00", "bag": None,
            "profile": "Turbo", "dose_g": None, "grind": None,
            "yield_g": 35.7, "time_s": 28.1, "ratio": None,
            "peak_pressure": 6.1, "peak_flow": 4.2,
            "taste": None, "taste_schema": None, "note": "", "flags": []}])

    def test_rating_is_stored_with_schema_three(self):
        row = rate_shot(self.store, "shot1", scripted(["", "s", "2", ""]))
        self.assertEqual(row["taste"]["lean"], "sour")
        self.assertEqual(row["taste"]["intensity"], 2)
        self.assertEqual(row["taste_schema"], 3)

    def test_dial_supplies_grind_and_dose_without_asking(self):
        row = rate_shot(self.store, "shot1", scripted(["", "s", "2", ""]))
        self.assertEqual(row["grind"], 3.1)
        self.assertEqual(row["dose_g"], 18.0)
        self.assertEqual(row["ratio"], 1.98)

    def test_intensity_is_not_asked_when_lean_is_none(self):
        row = rate_shot(self.store, "shot1", scripted(["", "-", ""]))
        self.assertEqual(row["taste"]["intensity"], 0)

    def test_versus_is_null_for_the_days_first_shot(self):
        row = rate_shot(self.store, "shot1", scripted(["", "s", "2", ""]))
        self.assertIsNone(row["taste"]["versus"])

    def test_versus_records_the_prior_shot_that_day(self):
        # File order is deliberately NOT chronological, and the intended answer
        # sits in the MIDDLE, so this passes only if the selector really sorts
        # by ts: `earlier[0]` gives 05:00 and `earlier[-1]` gives 04:00, both
        # wrong. sync appends in the machine's history order (newest-first in
        # practice), so file order can never stand in for chronology. The rule
        # outlived `default_dose`, whose test used to be the only thing
        # carrying it. The null-ts flagged row must be filtered out rather than
        # blow up the comparison.
        # A versus verdict bound to the wrong shot is the wrong-bag-label harm:
        # a human judgement silently attached to the wrong thing.
        self.store.append_shots([
            {"id": "shot_early", "ts": "2026-08-02T05:00:00", "bag": "b001",
             "profile": "Allonge", "taste": None, "taste_schema": None,
             "note": "", "flags": []},
            {"id": "shot0", "ts": "2026-08-02T06:00:00", "bag": "b001",
             "profile": "Allonge", "yield_g": 40.0, "time_s": 30.0,
             "taste": None, "taste_schema": None, "note": "", "flags": []},
            {"id": "shot_broken", "ts": None, "bag": "b001", "profile": "",
             "taste": None, "taste_schema": None, "note": "",
             "flags": ["telemetry_unparsed: x"]},
            {"id": "shot_earliest", "ts": "2026-08-02T04:00:00", "bag": "b001",
             "profile": "Allonge", "taste": None, "taste_schema": None,
             "note": "", "flags": []},
        ])
        row = rate_shot(self.store, "shot1", scripted(["", "s", "2", "w", ""]))
        self.assertEqual(row["taste"]["versus"],
                         {"shot": "shot0", "verdict": "worse"})

    def test_no_machine_number_is_printed_during_rating(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rate_shot(self.store, "shot1", scripted(["", "s", "2", ""]))
        printed = out.getvalue()
        for forbidden in ("35.7", "28.1", "6.1", "4.2", "1.98"):
            self.assertNotIn(forbidden, printed)

    def test_prompts_and_stdout_never_mention_machine_numbers(self):
        # The no-machine-output rule's original pin, restored for schema 3.
        # rate_shot speaks on TWO channels: the strings it hands to `ask`, and
        # whatever it prints. An earlier version of this test watched only
        # the prompts and a print() of yield/ratio/time went through with the
        # suite green, so both are captured. The forbidden values are read
        # back off the stored row rather than hardcoded, so this cannot
        # drift from the fixture.
        row = [r for r in self.store.load_shots() if r["id"] == "shot1"][0]
        seen = []
        answers = iter(["", "s", "2", ""])
        def spy(prompt):
            seen.append(prompt)
            return next(answers)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rate_shot(self.store, "shot1", spy)

        forbidden = ["yield", "ratio", "pressure", "flow",
                     str(row["yield_g"]), str(row["time_s"]),
                     str(row["peak_pressure"]), str(row["peak_flow"])]
        channels = {"prompts": " ".join(seen).lower(),
                    "stdout": stdout.getvalue().lower()}
        # positive anchors first: every other assertion here is negative, so a
        # capture that silently stopped working would satisfy all of them and
        # pin nothing. Both channels must be shown to carry their own content.
        self.assertIn("turbo", channels["stdout"])
        self.assertIn("lean", channels["prompts"])
        for name, blob in channels.items():
            for word in forbidden:
                self.assertNotIn(word.lower(), blob, f"{name} leaked {word!r}")

    def test_rating_a_flagged_shot_prints_no_machine_values(self):
        # The no-machine-output rule's second pin, restored for schema 3. A
        # flagged row carries null machine fields and an empty profile name,
        # which is the shape where a naive implementation prints "None",
        # falls back to a stand-in derived from telemetry, or crashes. The
        # stand-in text must be descriptive, and "None" must never reach the
        # screen as if it were
        # data.
        self.store.append_shots([flagged_row({"id": "bad1", "time": None}, "x")])
        seen = []
        answers = iter(["", "s", "2", ""])
        def spy(prompt):
            seen.append(prompt)
            return next(answers)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            stored = rate_shot(self.store, "bad1", spy)

        self.assertIsNone(stored["grind"])      # no dial resolves without a ts
        self.assertIsNone(stored["ratio"])      # no yield to divide
        self.assertTrue(stored["flags"])        # still flagged after rating
        printed = stdout.getvalue().lower()
        self.assertIn("(time unknown)", printed)
        channels = {"prompts": " ".join(seen).lower(), "stdout": printed}
        for name, blob in channels.items():
            for word in ("yield", "ratio", "pressure", "flow", "none"):
                self.assertNotIn(word, blob, f"{name} leaked {word!r}")

    def test_bad_lean_raises_and_stores_nothing(self):
        with self.assertRaises(ValueError):
            rate_shot(self.store, "shot1", scripted(["", "q"]))
        stored = [r for r in self.store.load_shots() if r["id"] == "shot1"][0]
        self.assertIsNone(stored["taste"])

    def test_bag_menu_default_is_accepted_on_empty_input(self):
        row = rate_shot(self.store, "shot1", scripted(["", "s", "2", ""]))
        self.assertEqual(row["bag"], "b001")

    def test_one_grinder_is_implicit_and_adds_no_prompt(self):
        # With exactly one grinder registered, asking is skipped entirely --
        # implicit rather than a question. This is the common path, so the
        # four keystrokes stay four.
        seen = []
        answers = iter(["", "s", "2", ""])
        def spy(prompt):
            seen.append(prompt)
            return next(answers)
        row = rate_shot(self.store, "shot1", spy)
        self.assertEqual(len(seen), 4)
        self.assertFalse(any("grinder" in p for p in seen), seen)
        self.assertEqual(row["grinder"], "g001")
        self.assertEqual(row["grind"], 3.1)

    def test_unknown_shot_raises(self):
        with self.assertRaises(KeyError):
            rate_shot(self.store, "nope", scripted([]))

    def test_unknown_bag_is_rejected_and_stores_nothing(self):
        # rate_shot raises on the bag answer before asking anything else, so
        # only "b999" is ever consumed; the rest was dead v2-era input
        with self.assertRaises(ValueError):
            rate_shot(self.store, "s1", scripted(["b999"]))
        stored = [r for r in self.store.load_shots() if r["id"] == "s1"][0]
        self.assertIsNone(stored["bag"])
        self.assertIsNone(stored["taste"])

class TestRateShotWithSeveralGrinders(Base):
    """The grinder is asked for only when more than one is registered.

    The question was promised by `default_grinder_id`'s docstring and asked
    nowhere. Unasked, `rate_shot` stored grind, dose and ratio as null while a
    perfectly good dial sat in the log — and a shot that was dialled but lost
    its setting is indistinguishable afterwards from one never dialled at all,
    which is the same class of harm as a wrong bag label.
    """

    def setUp(self):
        super().setUp()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        new_grinder(self.store, scripted(["1Zpresso", "K-Ultra", "d", "lower", ""]))
        new_grinder(self.store, scripted(["Niche", "Zero", "collar", "lower", ""]))
        new_bag(self.store, scripted(["roaster", "name", "washed", "2026-08-01", ""]))
        record_dial(self.store, 3.1, dose_g=18.0, bag="b001", grinder="g001",
                    now=datetime.datetime(2026, 8, 1, 6, 0))
        record_dial(self.store, 25.0, dose_g=20.0, bag="b001", grinder="g002",
                    now=datetime.datetime(2026, 8, 1, 6, 30))
        self.store.append_shots([{
            "id": "shot1", "ts": "2026-08-02T07:00:00", "bag": None,
            "profile": "Turbo", "dose_g": None, "grind": None,
            "yield_g": 35.7, "time_s": 28.1, "ratio": None,
            "peak_pressure": 6.1, "peak_flow": 4.2,
            "taste": None, "taste_schema": None, "note": "", "flags": []}])

    def spy(self, answers):
        self.seen = []
        it = iter(answers)
        def ask(prompt):
            self.seen.append(prompt)
            return next(it)
        return ask

    def test_the_grinder_is_asked_for_and_the_choice_resolves_its_dial(self):
        row = rate_shot(self.store, "shot1", self.spy(["", "2", "s", "2", ""]))
        self.assertTrue(any("grinder" in p for p in self.seen), self.seen)
        self.assertEqual(row["grinder"], "g002")
        self.assertEqual(row["grind"], 25.0)     # g002's dial, not g001's 3.1
        self.assertEqual(row["dose_g"], 20.0)
        self.assertEqual(row["ratio"], 1.79)     # recomputed from g002's dose

    def test_nothing_is_silently_null_once_the_question_is_asked(self):
        # the bug this fixes: a dial existed for both grinders and every one of
        # these came back None because nothing asked which grinder was used
        row = rate_shot(self.store, "shot1", self.spy(["", "1", "s", "2", ""]))
        for field in ("grinder", "grind", "dose_g", "ratio"):
            self.assertIsNotNone(row[field], field)

    def test_the_default_is_the_grinder_of_the_most_recent_rated_shot(self):
        # Same chronology rule as the versus selector, and the same trap: with
        # one grinder-bearing row, `max`, `[0]` and `[-1]` are indistinguishable.
        # The intended answer sits in the MIDDLE of a non-chronological file, so
        # `used[0]` and `used[-1]` both give g001 and only a real sort by ts
        # gives g002. The null-ts row is the one that matters most here: unlike
        # the versus selector, nothing filters it out, so it reaches the sort
        # key directly and pins the `or ""` that stops None being compared
        # against a string.
        def rated(shot_id, ts, grinder):
            return {"id": shot_id, "ts": ts, "bag": "b001", "grinder": grinder,
                    "profile": "Turbo", "note": "", "flags": [],
                    "taste": {"lean": "none", "intensity": 0, "versus": None},
                    "taste_schema": 3}
        self.store.append_shots([
            rated("older", "2026-07-30T09:00:00", "g001"),
            rated("recent", "2026-08-01T09:00:00", "g002"),
            rated("broken", None, "g001"),
            rated("oldest", "2026-07-20T09:00:00", "g001"),
        ])
        row = rate_shot(self.store, "shot1", self.spy(["", "", "s", "2", ""]))
        self.assertEqual(row["grinder"], "g002")
        self.assertEqual(row["grind"], 25.0)

    def test_the_default_is_the_first_grinder_when_no_shot_names_one(self):
        row = rate_shot(self.store, "shot1", self.spy(["", "", "s", "2", ""]))
        self.assertEqual(row["grinder"], "g001")
        self.assertEqual(row["grind"], 3.1)

    def test_an_unknown_grinder_is_rejected_and_stores_nothing(self):
        # same rule as the bag: a human-supplied id is validated before it can
        # attach a grind number to a scale that does not exist.
        # The regex is load-bearing, not decoration: delete the grinder question
        # and "g999" falls through to the lean prompt, where parse_lean raises
        # ValueError too. A bare assertRaises passes either way, which is
        # exactly the wrong-reason green this test used to be.
        with self.assertRaisesRegex(ValueError, "unknown grinder"):
            rate_shot(self.store, "shot1", self.spy(["", "g999", "s", "2", ""]))
        stored = [r for r in self.store.load_shots() if r["id"] == "shot1"][0]
        self.assertIsNone(stored["taste"])
        self.assertIsNone(stored["grind"])

    def test_the_grinder_menu_leaks_no_machine_number(self):
        # a new prompt is a new no-machine-output surface to police; grinder
        # rows carry make, model and scale, none of which is machine output
        row = [r for r in self.store.load_shots() if r["id"] == "shot1"][0]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rate_shot(self.store, "shot1", self.spy(["", "2", "s", "2", ""]))
        forbidden = ["yield", "ratio", "pressure", "flow",
                     str(row["yield_g"]), str(row["time_s"]),
                     str(row["peak_pressure"]), str(row["peak_flow"])]
        channels = {"prompts": " ".join(self.seen).lower(),
                    "stdout": stdout.getvalue().lower()}
        for name, blob in channels.items():
            for word in forbidden:
                self.assertNotIn(word.lower(), blob, f"{name} leaked {word!r}")

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

class TestNewGrinder(Base):
    def test_creates_g001_then_g002(self):
        first = new_grinder(self.store, scripted(
            ["1Zpresso", "K-Ultra", "decimal dial 0.0-9.0", "lower", ""]))
        second = new_grinder(self.store, scripted(
            ["Niche", "Zero", "0-50 collar", "lower", ""]))
        self.assertEqual(first["id"], "g001")
        self.assertEqual(second["id"], "g002")

    def test_bad_direction_raises_and_stores_nothing(self):
        with self.assertRaises(ValueError):
            new_grinder(self.store, scripted(
                ["1Zpresso", "K-Ultra", "dial", "sideways", ""]))
        self.assertEqual(self.store.load_grinders(), [])

    def test_default_grinder_is_the_only_one(self):
        new_grinder(self.store, scripted(["1Zpresso", "K-Ultra", "d", "lower", ""]))
        self.assertEqual(default_grinder_id(self.store), "g001")

    def test_default_grinder_is_none_when_several(self):
        new_grinder(self.store, scripted(["1Zpresso", "K-Ultra", "d", "lower", ""]))
        new_grinder(self.store, scripted(["Niche", "Zero", "c", "lower", ""]))
        self.assertIsNone(default_grinder_id(self.store))

class TestDial(Base):
    def setUp(self):
        super().setUp()
        new_grinder(self.store, scripted(["1Zpresso", "K-Ultra", "d", "lower", ""]))
        new_bag(self.store, scripted(["roaster", "name", "washed", "2026-08-01", ""]))

    def test_records_grind_and_dose(self):
        dial = record_dial(self.store, 3.1, dose_g=18.0, bag="b001")
        self.assertEqual(dial["grind"], 3.1)
        self.assertEqual(dial["dose_g"], 18.0)
        self.assertEqual(dial["grinder"], "g001")
        self.assertEqual(dial["bag"], "b001")

    def test_dose_carries_forward_when_omitted(self):
        record_dial(self.store, 3.1, dose_g=18.0, bag="b001")
        later = record_dial(self.store, 3.4, bag="b001")
        self.assertEqual(later["dose_g"], 18.0)

    def test_dose_carries_forward_from_a_different_bag_when_none_exists_for_this_one(self):
        # the middle leg of the fallback chain: no dial exists yet for
        # (grinder, this bag), so it inherits from the last dial anywhere,
        # not the first-dial-must-state-it error.
        new_bag(self.store, scripted(["roaster2", "name2", "washed", "2026-08-05", ""]))
        record_dial(self.store, 3.1, dose_g=18.0, bag="b001")
        later = record_dial(self.store, 2.8, bag="b002")
        self.assertEqual(later["dose_g"], 18.0)

    def test_first_dial_without_dose_raises(self):
        with self.assertRaises(ValueError):
            record_dial(self.store, 3.1, bag="b001")
        self.assertEqual(self.store.load_dials(), [])

    def test_resolves_latest_event_at_or_before_the_shot(self):
        self.store.append_dial({"ts": "2026-08-01T08:00:00", "grinder": "g001",
                                "bag": "b001", "grind": 2.9, "dose_g": 18.0})
        self.store.append_dial({"ts": "2026-08-03T08:00:00", "grinder": "g001",
                                "bag": "b001", "grind": 3.1, "dose_g": 18.0})
        got = resolve_dial(self.store, "g001", "b001", "2026-08-02T07:00:00")
        self.assertEqual(got["grind"], 2.9)

    def test_never_resolves_to_a_later_event(self):
        self.store.append_dial({"ts": "2026-08-05T08:00:00", "grinder": "g001",
                                "bag": "b001", "grind": 3.1, "dose_g": 18.0})
        self.assertIsNone(
            resolve_dial(self.store, "g001", "b001", "2026-08-02T07:00:00"))

    def test_does_not_cross_bags(self):
        self.store.append_dial({"ts": "2026-08-01T08:00:00", "grinder": "g001",
                                "bag": "b001", "grind": 2.9, "dose_g": 18.0})
        self.assertIsNone(
            resolve_dial(self.store, "g001", "b002", "2026-08-02T07:00:00"))

    def test_dial_without_a_grinder_raises(self):
        empty = Store(self.root / "empty")
        with self.assertRaises(ValueError):
            record_dial(empty, 3.1, dose_g=18.0, bag="b001")

class TestCurrentBagIsMostRecentlyUsed(Base):
    def test_uses_most_recent_rated_shot_not_registration_order(self):
        # ids "u1"/"u2" deliberately avoid colliding with Base's own "s1"
        # fixture shot; reusing "s1" here would make append_shots silently
        # drop this row as a duplicate and the test would pass or fail for
        # the wrong reason.
        new_bag(self.store, scripted(["r", "old", "washed", "2026-08-01", ""]))
        new_bag(self.store, scripted(["r", "new", "washed", "2026-08-02", ""]))
        self.store.append_shots([
            {"id": "u1", "ts": "2026-08-05T07:00:00", "bag": "b001"},
            {"id": "u2", "ts": "2026-08-04T07:00:00", "bag": "b002"},
        ])
        self.assertEqual(current_bag_id(self.store), "b001")

    def test_falls_back_to_last_registered_when_no_shot_has_a_bag(self):
        new_bag(self.store, scripted(["r", "old", "washed", "2026-08-01", ""]))
        new_bag(self.store, scripted(["r", "new", "washed", "2026-08-02", ""]))
        self.assertEqual(current_bag_id(self.store), "b002")

class TestBagIsNeverInferred(Base):
    # ids "z1"/"z2" deliberately avoid colliding with Base's own "s1" fixture
    # shot; reusing "s1" here would make append_shots silently drop this row
    # as a duplicate and the test would pass or fail for the wrong reason.
    def setUp(self):
        super().setUp()
        # rate_shot prints its two blind-rating lines; same convention as
        # TestRateShot and TestRateShotWithSeveralGrinders.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_sync_leaves_bag_null(self):
        self.store.append_shots([{"id": "z1", "ts": "2026-08-02T07:00:00",
                                  "bag": None, "profile": "Turbo",
                                  "taste": None, "taste_schema": None,
                                  "note": "", "flags": []}])
        stored = [r for r in self.store.load_shots() if r["id"] == "z1"][0]
        self.assertIsNone(stored["bag"])

    def test_unknown_bag_typed_at_rating_raises_and_stores_nothing(self):
        new_grinder(self.store, scripted(["1Z", "K", "d", "lower", ""]))
        new_bag(self.store, scripted(["r", "n", "washed", "2026-08-01", ""]))
        self.store.append_shots([{"id": "z2", "ts": "2026-08-02T07:00:00",
                                  "bag": None, "profile": "Turbo",
                                  "yield_g": 40.0, "time_s": 28.0,
                                  "taste": None, "taste_schema": None,
                                  "note": "", "flags": []}])
        with self.assertRaises(ValueError):
            rate_shot(self.store, "z2", scripted(["b999", "s", "2", ""]))
        stored = [r for r in self.store.load_shots() if r["id"] == "z2"][0]
        self.assertIsNone(stored["taste"])

if __name__ == "__main__":
    unittest.main()
