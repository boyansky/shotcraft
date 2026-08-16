import json, pathlib, tempfile, unittest
from shotcraft.api import MachineUnreachable
from shotcraft.store import Store
from shotcraft.sync import sync

FIX = pathlib.Path(__file__).parent / "fixtures"

class FakeAPI:
    def __init__(self, entries):
        self.entries = entries
    def history(self):
        return self.entries

class DeadAPI:
    def history(self):
        raise MachineUnreachable("boom")

def fixture_entries():
    return json.loads((FIX / "history.json").read_text())["history"]

class TestSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.tmp.name))
        self.entries = fixture_entries()

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_sync_adds_all(self):
        result = sync(FakeAPI(self.entries), self.store)
        self.assertEqual(result["added"], len(self.entries))
        self.assertEqual(len(self.store.load_shots()), len(self.entries))

    def test_second_sync_adds_nothing(self):
        sync(FakeAPI(self.entries), self.store)
        result = sync(FakeAPI(self.entries), self.store)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["skipped"], len(self.entries))

    def test_telemetry_written_for_each_shot(self):
        sync(FakeAPI(self.entries), self.store)
        for entry in self.entries:
            self.assertTrue(self.store.has_telemetry(entry["id"]))

    def test_all_new_shots_are_reported_unrated(self):
        result = sync(FakeAPI(self.entries), self.store)
        self.assertEqual(len(result["unrated"]), len(self.entries))

    def test_vanished_ids_detected_when_window_rolls(self):
        sync(FakeAPI(self.entries), self.store)
        dropped = self.entries[0]["id"]
        result = sync(FakeAPI(self.entries[1:]), self.store)
        self.assertIn(dropped, result["vanished"])

    def test_no_vanished_when_nothing_dropped(self):
        sync(FakeAPI(self.entries), self.store)
        result = sync(FakeAPI(self.entries), self.store)
        self.assertEqual(result["vanished"], [])

    def test_unreachable_machine_writes_nothing(self):
        with self.assertRaises(MachineUnreachable):
            sync(DeadAPI(), self.store)
        self.assertEqual(self.store.load_shots(), [])

    def test_successful_sync_writes_a_sync_stamp(self):
        # nudge ages this stamp to warn about a silently failing scheduled
        # sync, so a real success has to leave one behind.
        self.assertFalse(self.store.sync_stamp_path.exists())
        sync(FakeAPI(self.entries), self.store)
        self.assertTrue(self.store.sync_stamp_path.exists())
        self.assertLess(self.store.sync_age_days(), 1.0)

    def test_failed_sync_writes_no_sync_stamp(self):
        # the stamp exists to make a silent failure visible; a sync that
        # never wrote a shot must not also claim to have succeeded.
        with self.assertRaises(MachineUnreachable):
            sync(DeadAPI(), self.store)
        self.assertFalse(self.store.sync_stamp_path.exists())
        self.assertEqual(self.store.sync_age_days(), float("inf"))

    def test_http_path_stamps_now(self):
        # stamp_now defaults True: a live round trip to the machine just
        # proved the archive is, right now, as fresh as it can be
        sync(FakeAPI(self.entries), self.store)
        self.assertLess(self.store.sync_age_days(), 1.0)

    def test_from_directory_path_stamps_the_newest_entry_not_now(self):
        # the fixture's newest entry is dated 2026-07-25, weeks before any
        # run of this test -- stamping `now` would hide exactly the capture
        # staleness this path exists to surface
        sync(FakeAPI(self.entries), self.store, stamp_now=False)
        self.assertGreater(self.store.sync_age_days(), 10.0)

class TestMalformedEntries(unittest.TestCase):
    """One bad entry must not cost the batch, nor wedge every future sync."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.tmp.name))
        entries = fixture_entries()[:3]
        entries[1] = {**entries[1], "time": None}   # unparseable timestamp
        self.entries = entries
        self.bad_id = entries[1]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_healthy_entries_in_the_batch_are_still_stored(self):
        result = sync(FakeAPI(self.entries), self.store)
        self.assertEqual(result["added"], 3)
        stored = {r["id"] for r in self.store.load_shots()}
        self.assertEqual(stored, {e["id"] for e in self.entries})

    def test_bad_entry_is_flagged_with_null_machine_fields(self):
        result = sync(FakeAPI(self.entries), self.store)
        self.assertEqual(result["flagged"], [self.bad_id])
        row = [r for r in self.store.load_shots() if r["id"] == self.bad_id][0]
        self.assertTrue(row["flags"])
        self.assertIsNone(row["yield_g"])
        self.assertIsNone(row["time_s"])
        self.assertIsNone(row["ts"])

    def test_healthy_rows_carry_no_flags(self):
        sync(FakeAPI(self.entries), self.store)
        good = [r for r in self.store.load_shots() if r["id"] != self.bad_id]
        self.assertEqual(len(good), 2)
        for row in good:
            self.assertEqual(row["flags"], [])

    def test_raw_blob_is_written_even_for_the_bad_entry(self):
        sync(FakeAPI(self.entries), self.store)
        for entry in self.entries:
            self.assertTrue(self.store.has_telemetry(entry["id"]))

    def test_retry_does_not_re_crash_or_duplicate(self):
        sync(FakeAPI(self.entries), self.store)
        result = sync(FakeAPI(self.entries), self.store)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["skipped"], 3)
        self.assertEqual(len(self.store.load_shots()), 3)

    def test_entry_without_an_id_is_counted_not_stored(self):
        entries = fixture_entries()[:2] + [{"time": 1784976267.7, "data": []}]
        result = sync(FakeAPI(entries), self.store)
        self.assertEqual(result["unusable"], 1)
        self.assertEqual(result["added"], 2)

if __name__ == "__main__":
    unittest.main()
