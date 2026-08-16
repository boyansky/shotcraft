import datetime, json, tempfile, pathlib, unittest
from unittest.mock import patch
from shotcraft.store import Store

ROW = {"id": "abc123", "ts": "2026-07-25T12:44:27", "profile": "Turbo"}

class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_shots_on_empty_store(self):
        self.assertEqual(self.store.load_shots(), [])

    def test_append_then_load_roundtrip(self):
        self.store.append_shots([ROW])
        self.assertEqual(self.store.load_shots(), [ROW])

    def test_append_is_idempotent_by_id(self):
        self.assertEqual(self.store.append_shots([ROW]), 1)
        self.assertEqual(self.store.append_shots([ROW]), 0)
        self.assertEqual(len(self.store.load_shots()), 1)

    def test_append_mixed_new_and_existing(self):
        self.store.append_shots([ROW])
        added = self.store.append_shots([ROW, {**ROW, "id": "def456"}])
        self.assertEqual(added, 1)
        self.assertEqual(self.store.shot_ids(), {"abc123", "def456"})

    def test_append_dedupes_within_a_single_batch(self):
        fresh = {**ROW, "id": "ghi789"}
        added = self.store.append_shots([fresh, fresh])
        self.assertEqual(added, 1)
        self.assertEqual(len(self.store.load_shots()), 1)

    def test_write_telemetry_creates_file(self):
        path = self.store.write_telemetry("abc123", {"data": [1, 2, 3]})
        self.assertTrue(path.exists())
        self.assertTrue(self.store.has_telemetry("abc123"))
        self.assertEqual(json.loads(path.read_text())["data"], [1, 2, 3])

    def test_has_telemetry_false_when_absent(self):
        self.assertFalse(self.store.has_telemetry("nope"))

    def test_bags_load_and_lookup(self):
        (pathlib.Path(self.tmp.name) / "bags.jsonl").write_text(
            json.dumps({"id": "b001", "roaster": "X", "roast_date": "2026-07-18"}) + "\n"
        )
        self.assertEqual(self.store.bag_by_id("b001")["roaster"], "X")
        self.assertIsNone(self.store.bag_by_id("missing"))

    def test_blank_lines_are_skipped(self):
        (pathlib.Path(self.tmp.name) / "shots.jsonl").write_text(
            json.dumps(ROW) + "\n\n\n"
        )
        self.assertEqual(len(self.store.load_shots()), 1)

    def test_append_bag_and_load(self):
        self.assertEqual(self.store.append_bag({"id": "b001", "roaster": "X"}), 1)
        self.assertEqual(self.store.bag_by_id("b001")["roaster"], "X")

    def test_append_bag_rejects_duplicate_id(self):
        self.store.append_bag({"id": "b001", "roaster": "X"})
        self.assertEqual(self.store.append_bag({"id": "b001", "roaster": "Y"}), 0)
        self.assertEqual(self.store.bag_by_id("b001")["roaster"], "X")

    def test_update_shot_rewrites_row_in_place(self):
        self.store.append_shots([ROW, {**ROW, "id": "def456"}])
        updated = self.store.update_shot("abc123", {"grind": 2.8})
        self.assertEqual(updated["grind"], 2.8)
        rows = self.store.load_shots()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["id"] for r in rows], ["abc123", "def456"])

    def test_update_shot_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            self.store.update_shot("nope", {"grind": 2.8})

    def test_update_shot_survives_a_failed_replace(self):
        # a truncate-then-rewrite would already have zeroed shots.jsonl by
        # the time os.replace could fail; an atomic write leaves the ORIGINAL
        # file untouched until the swap actually succeeds
        self.store.append_shots([ROW, {**ROW, "id": "def456"}])
        original = self.store.shots_path.read_text()
        with patch("shotcraft.store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.update_shot("abc123", {"grind": 2.8})
        self.assertEqual(self.store.shots_path.read_text(), original)

    def test_update_shot_leaves_no_temp_file_behind_on_success(self):
        self.store.append_shots([ROW])
        self.store.update_shot("abc123", {"grind": 2.8})
        self.assertEqual(list(self.store.root.glob("*.tmp")), [])

    def test_write_telemetry_survives_a_failed_replace(self):
        self.store.write_telemetry("abc123", {"data": [1]})
        path = self.store.telemetry_dir / "abc123.json"
        original = path.read_text()
        with patch("shotcraft.store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.write_telemetry("abc123", {"data": [2, 3, 4, 5]})
        self.assertEqual(path.read_text(), original)

class TestResolveShotId(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.tmp.name))
        self.store.append_shots([
            {**ROW, "id": "9ed33350-0fd9-4517-a7d6-119663ab8a7a"},
            {**ROW, "id": "6410f387-17b5-4bfb-b60e-acce2056f6aa"},
            {**ROW, "id": "6410f000-0000-0000-0000-000000000000"},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_id_resolves_to_itself(self):
        full = "9ed33350-0fd9-4517-a7d6-119663ab8a7a"
        self.assertEqual(self.store.resolve_shot_id(full), full)

    def test_unique_short_prefix_resolves(self):
        self.assertEqual(self.store.resolve_shot_id("9ed33350"),
                         "9ed33350-0fd9-4517-a7d6-119663ab8a7a")

    def test_ambiguous_prefix_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            self.store.resolve_shot_id("6410")

    def test_unknown_prefix_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.store.resolve_shot_id("ffff")

    def test_telemetry_only_id_is_resolvable(self):
        self.store.write_telemetry("aaaa1111-2222", {"data": []})
        self.assertEqual(self.store.resolve_shot_id("aaaa"), "aaaa1111-2222")

class TestGrinders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_load(self):
        self.assertEqual(self.store.append_grinder({"id": "g001"}), 1)
        self.assertEqual([g["id"] for g in self.store.load_grinders()], ["g001"])

    def test_duplicate_id_is_refused(self):
        self.store.append_grinder({"id": "g001"})
        self.assertEqual(self.store.append_grinder({"id": "g001"}), 0)
        self.assertEqual(len(self.store.load_grinders()), 1)

    def test_lookup_by_id(self):
        self.store.append_grinder({"id": "g001", "model": "K-Ultra"})
        self.assertEqual(self.store.grinder_by_id("g001")["model"], "K-Ultra")
        self.assertIsNone(self.store.grinder_by_id("g999"))

DIAL = {"ts": "2026-08-01T08:00:00", "grinder": "g001", "bag": "b001",
        "grind": 3.1, "dose_g": 18.0, "note": ""}

class TestDials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_load(self):
        self.assertEqual(self.store.append_dial(DIAL), 1)
        self.assertEqual(self.store.load_dials(), [DIAL])

    def test_appending_an_identical_dial_twice_yields_two_rows(self):
        # unlike append_bag/append_grinder, append_dial never dedupes:
        # re-dialling back to the same number on a different day is a real
        # event, not a duplicate to be suppressed. This is the store-level
        # pin on that deliberate asymmetry.
        self.store.append_dial(DIAL)
        self.store.append_dial(DIAL)
        self.assertEqual(len(self.store.load_dials()), 2)

STAMP_TS = "2026-08-01T09:00:00"

class TestSyncStamp(unittest.TestCase):
    """nudge trusts sync_age_days for the one line it prints, so both halves
    of the round trip -- and the failure modes that must collapse to the same
    'never synced' sentinel as a fresh install -- are pinned here."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_never_synced_is_infinite(self):
        self.assertEqual(self.store.sync_age_days(), float("inf"))

    def test_stamp_is_written_as_isoformat_json(self):
        when = datetime.datetime.fromisoformat(STAMP_TS)
        self.store.write_sync_stamp(now=when)
        self.assertEqual(
            json.loads(self.store.sync_stamp_path.read_text())["ts"],
            when.isoformat(timespec="seconds"))

    def test_age_is_zero_immediately_after_a_stamp(self):
        when = datetime.datetime.fromisoformat(STAMP_TS)
        self.store.write_sync_stamp(now=when)
        self.assertEqual(self.store.sync_age_days(now=when), 0.0)

    def test_write_sync_stamp_survives_a_failed_replace(self):
        when = datetime.datetime.fromisoformat(STAMP_TS)
        self.store.write_sync_stamp(now=when)
        original = self.store.sync_stamp_path.read_text()
        later = when + datetime.timedelta(days=1)
        with patch("shotcraft.store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.write_sync_stamp(now=later)
        self.assertEqual(self.store.sync_stamp_path.read_text(), original)

    def test_age_reflects_elapsed_time(self):
        when = datetime.datetime.fromisoformat(STAMP_TS)
        self.store.write_sync_stamp(now=when)
        later = when + datetime.timedelta(days=2, hours=12)
        self.assertEqual(self.store.sync_age_days(now=later), 2.5)

    def test_corrupt_stamp_file_is_infinite_not_a_crash(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.store.sync_stamp_path.write_text("not valid json")
        self.assertEqual(self.store.sync_age_days(), float("inf"))

    def test_stamp_file_missing_ts_key_is_infinite(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.store.sync_stamp_path.write_text(json.dumps({"unrelated": "x"}))
        self.assertEqual(self.store.sync_age_days(), float("inf"))

if __name__ == "__main__":
    unittest.main()
