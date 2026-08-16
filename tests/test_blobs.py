import json, pathlib, tempfile, unittest

from shotcraft.blobs import BlobSource, SourceUnreadable


class TestBlobSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, payload):
        (self.dir / name).write_text(json.dumps(payload))

    def test_reads_entries_newest_first(self):
        self._write("aaa.json", {"id": "aaa", "time": 100.0, "name": "Turbo"})
        self._write("bbb.json", {"id": "bbb", "time": 200.0, "name": "Allonge"})
        entries = BlobSource(self.dir).history()
        self.assertEqual([e["id"] for e in entries], ["bbb", "aaa"])

    def test_unparseable_file_is_skipped_and_named(self):
        self._write("good.json", {"id": "good", "time": 100.0})
        (self.dir / "bad.json").write_text("{not json")
        source = BlobSource(self.dir)
        self.assertEqual([e["id"] for e in source.history()], ["good"])
        self.assertEqual(source.skipped, ["bad.json"])

    def test_non_dict_json_is_skipped_not_a_crash(self):
        # valid JSON that parses to a bare list or number has no `.get()`;
        # _sort_time would raise AttributeError on it if it reached the
        # sort, escaping the except that guards actual parse failures and
        # aborting the whole ingest -- one bad blob must never cost the batch
        self._write("good.json", {"id": "good", "time": 100.0})
        (self.dir / "bare_list.json").write_text("[1, 2, 3]")
        (self.dir / "bare_number.json").write_text("42")
        source = BlobSource(self.dir)
        self.assertEqual([e["id"] for e in source.history()], ["good"])
        self.assertEqual(sorted(source.skipped),
                         ["bare_list.json", "bare_number.json"])

    def test_missing_directory_raises_source_unreadable(self):
        with self.assertRaises(SourceUnreadable):
            BlobSource(self.dir / "nope").history()

    def test_entry_without_time_sorts_last_rather_than_raising(self):
        self._write("aaa.json", {"id": "aaa"})
        self._write("bbb.json", {"id": "bbb", "time": 200.0})
        self.assertEqual([e["id"] for e in BlobSource(self.dir).history()],
                         ["bbb", "aaa"])
