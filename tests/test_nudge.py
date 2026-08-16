import unittest
from unittest.mock import patch

from shotcraft.format import format_nudge


class TestNudge(unittest.TestCase):
    # invoked_as() reads sys.argv[0], and `python3 -m unittest` rewrites
    # argv[0] to name itself ("<python> -m unittest") rather than leaving it
    # as the script path. Pinning it here is what lets the assertions below
    # check the exact composed line instead of a substring of it.
    def setUp(self):
        patcher = patch("sys.argv", ["shotcraft"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_silent_when_nothing_to_say(self):
        self.assertEqual(format_nudge(0, 0.0), "")

    def test_names_the_count_and_the_command(self):
        self.assertEqual(format_nudge(3, 0.0),
                         "shotcraft: 3 shots unrated  (shotcraft rate <id>)")

    def test_single_shot_is_not_pluralised(self):
        self.assertEqual(format_nudge(1, 0.0),
                         "shotcraft: 1 shot unrated  (shotcraft rate <id>)")

    def test_stale_sync_is_surfaced_even_with_nothing_unrated(self):
        # no `(shotcraft rate <id>)` hint: with nothing unrated, there is
        # nothing for that hint to fix
        self.assertEqual(format_nudge(0, 2.0), "shotcraft: last sync 2d ago")

    def test_output_is_a_single_line(self):
        self.assertEqual(len(format_nudge(3, 2.0).splitlines()), 1)

    def test_never_synced_reads_as_never_not_a_raw_infinity(self):
        # Store.sync_age_days() returns float("inf") when no stamp file has
        # ever been written -- the default state of every fresh install, not
        # an edge case. f"{float('inf'):.0f}" is the literal string "inf", so
        # a naive format would print "last sync infd ago" on day one, before
        # the tool has done anything.
        self.assertEqual(format_nudge(0, float("inf")), "shotcraft: last sync never")

    def test_hint_appears_only_when_something_is_unrated(self):
        # a stale sync alone must not carry the "rate <id>" hint; a real
        # unrated count restores it
        self.assertNotIn("rate <id>", format_nudge(0, 2.0))
        self.assertIn("rate <id>", format_nudge(1, 2.0))
