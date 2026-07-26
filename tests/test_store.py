import json, tempfile, pathlib, unittest
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

if __name__ == "__main__":
    unittest.main()
