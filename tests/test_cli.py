import argparse, contextlib, io, json, os, pathlib, tempfile, unittest
from unittest.mock import patch

from shotcraft import cli
from shotcraft.cli import evidence_level, format_bags, format_report
from shotcraft.entry import current_bag_id
from shotcraft.extract import grade, pair_note
from shotcraft.format import format_reveal
from shotcraft import format as fmt
from shotcraft.store import Store

FIX = pathlib.Path(__file__).parent / "fixtures"

def scripted(answers):
    """Return an `ask` callable that replays answers in order."""
    it = iter(answers)
    return lambda prompt: next(it)

ROWS = [
    {"id": "a", "ts": "2026-07-25T12:44:27", "bag": "b001", "profile": "Turbo",
     "dose_g": 18.0, "grind": 2.8, "yield_g": 50.8, "time_s": 21.0, "ratio": 2.82,
     "taste": {"sour": 1, "bitter": 6, "body": 3, "overall": 4}, "taste_schema": 2},
    {"id": "b", "ts": "2026-07-25T10:19:00", "bag": "b001", "profile": "Turbo",
     "dose_g": 18.0, "grind": 2.6, "yield_g": 44.0, "time_s": 26.0, "ratio": 2.44,
     "taste": None, "taste_schema": 1},
]

BAGS = [{"id": "b001", "roaster": "Square Mile", "roast_date": "2026-07-18"}]

class TestEvidenceLevel(unittest.TestCase):
    def test_single_point_is_observation(self):
        self.assertEqual(evidence_level(1), "observation")

    def test_small_sample_is_pattern(self):
        self.assertEqual(evidence_level(3), "pattern")

    def test_ten_or_more_is_hypothesis(self):
        self.assertEqual(evidence_level(10), "hypothesis")

    def test_version_flag_exits_zero_and_prints_it(self):
        from shotcraft import __version__
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(__version__, out.getvalue())

    def test_never_returns_validated(self):
        for n in range(0, 500):
            self.assertNotEqual(evidence_level(n), "validated")

class TestFormatReport(unittest.TestCase):
    def test_states_sample_size(self):
        out = format_report(ROWS)
        self.assertIn("n=2", out)

    def test_reports_unrated_count(self):
        out = format_report(ROWS)
        self.assertIn("1 unrated", out)

    def test_groups_by_bag(self):
        self.assertIn("b001", format_report(ROWS))

    def test_empty_corpus_says_so(self):
        self.assertIn("no shots", format_report([]).lower())

    def test_corpus_confidence_counts_rated_rows_only(self):
        # a corpus that is mostly unrated must not borrow the row count's
        # confidence -- the same fix already applied to the per-bag label.
        # 20 unrated plus 1 rated must read "observation" (n=1), not
        # "hypothesis" (n=21) borrowed from rows that carry no taste signal
        many_unrated = [{**ROWS[1], "id": f"u{i}"} for i in range(20)]
        out = format_report(many_unrated + [ROWS[0]])
        self.assertIn("n=21, 20 unrated  [observation]", out)

    def test_mixed_taste_schema_is_flagged(self):
        # only RATED rows carry a schema, so the mix must be built from two
        # rated rows of different versions, not from an unrated one
        v1 = {**ROWS[0], "id": "old",
              "taste": {"sour_bitter": 0, "body": 3, "overall": 4},
              "taste_schema": 1}
        self.assertIn("taste_schema", format_report([v1, ROWS[0]]))

    def test_partial_v1_row_does_not_crash(self):
        # the v2 branch already read every field with .get(); v1 subscripted
        # 'body' and 'overall' directly, so a hand-edited or partial old row
        # raised a KeyError out of the whole report instead of rendering
        partial_v1 = {**ROWS[0], "id": "old", "taste": {"sour_bitter": 0},
                      "taste_schema": 1}
        out = format_report([partial_v1], BAGS)
        self.assertIn("sb+0", out)
        self.assertNotIn("None", out)

    def test_rows_carry_a_short_id(self):
        # without an id on screen, `rate` and `check` are unreachable
        out = format_report(ROWS, BAGS)
        self.assertIn("  a  ", out)

    def test_bag_confidence_counts_rated_rows_only(self):
        out = format_report(ROWS, BAGS)
        self.assertIn("n=2, 1 rated  [observation]", out)

    def test_bag_with_no_ratings_claims_no_confidence(self):
        unrated = [{**r, "taste": None} for r in ROWS]
        out = format_report(unrated, BAGS)
        self.assertIn("0 rated  [no taste signal]", out)
        self.assertNotIn("[pattern]\n  a", out)

    def test_null_numbers_render_as_dashes_not_none(self):
        blank = [{"id": "z", "ts": "2026-07-25T09:00:00", "bag": None,
                  "profile": "Turbo", "dose_g": None, "grind": None,
                  "yield_g": None, "time_s": None, "ratio": None,
                  "taste": None, "taste_schema": None, "flags": []}]
        out = format_report(blank, BAGS)
        self.assertNotIn("None", out)
        self.assertIn("grind=-", out)

    def test_flagged_row_is_visibly_marked(self):
        flagged = [{**ROWS[0], "flags": ["telemetry_unparsed: boom"]}]
        self.assertIn("!telemetry_unparsed", format_report(flagged, BAGS))

    def test_unknown_bag_is_flagged_at_read_time(self):
        rows = [{**ROWS[0], "bag": "b999"}]
        self.assertIn("!unknown_bag", format_report(rows, BAGS))

    def test_row_with_null_ts_does_not_crash_the_report(self):
        rows = [ROWS[0], {**ROWS[1], "ts": None, "flags": ["telemetry_unparsed: x"]}]
        self.assertIn("b001", format_report(rows, BAGS))

def fixture_entries():
    return json.loads((FIX / "history.json").read_text())["history"]

class FakeAPI:
    """Stands in for MeticulousAPI. Nothing here touches the network."""
    entries = None

    def __init__(self, *args, **kwargs):
        pass

    def history(self):
        return fixture_entries() if self.entries is None else self.entries

# a real fixture shot: Traditional Lever, target 42.0g, actual 28.76g
LEVER = "6410f387"

class TestMain(unittest.TestCase):
    """main() itself, the layer eleven passing reviews never executed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        root_patch = patch.object(cli, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        # the client has no built-in address any more; commands that reach the
        # machine must be configured, exactly as a real user would be
        env = patch.dict(os.environ, {"SHOTCRAFT_API_URL": "http://192.0.2.1",
                                      "XDG_CONFIG_HOME": str(self.root / "cfg")})
        env.start(); self.addCleanup(env.stop)

    def run_main(self, argv, answers=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if answers is None:
                code = cli.main(argv)
            else:
                with patch("builtins.input", side_effect=answers):
                    code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def sync(self, entries=None):
        with patch.object(cli, "MeticulousAPI", FakeAPI):
            FakeAPI.entries = entries
            try:
                return self.run_main(["sync"])
            finally:
                FakeAPI.entries = None

    def test_sync_stores_the_fixture_corpus(self):
        code, out, _ = self.sync()
        self.assertEqual(code, 0)
        self.assertIn("added 7", out)

    def test_sync_lists_the_ids_awaiting_ratings(self):
        _, out, _ = self.sync()
        self.assertIn("awaiting taste ratings", out)
        for entry in fixture_entries():
            self.assertIn(entry["id"][:8], out)

    def test_sync_reports_unreachable_machine_without_a_traceback(self):
        class Dead:
            def __init__(self, *a, **k): pass
            def history(self): raise cli.MachineUnreachable("no route to host")
        with patch.object(cli, "MeticulousAPI", Dead):
            code, out, err = self.run_main(["sync"])
        self.assertEqual(code, 1)
        self.assertIn("Nothing written: no route to host", err)

    def test_sync_reports_an_unexpected_error_as_a_message(self):
        class Broken:
            def __init__(self, *a, **k): pass
            def history(self): raise RuntimeError("something surprising")
        with patch.object(cli, "MeticulousAPI", Broken):
            code, out, err = self.run_main(["sync"])
        self.assertEqual(code, 1)
        self.assertIn("something surprising", err)

    def test_check_on_a_real_fixture_shot_exits_zero(self):
        # regression: every fixture profile declares a final_weight, so the
        # shot-total row fired on 100% of real shots and crashed the printer
        self.sync()
        code, out, err = self.run_main(["check", LEVER])
        self.assertEqual(code, 0, err)
        self.assertIn("(shot total)", out)

    def test_check_reports_target_against_actual_final_weight(self):
        self.sync()
        _, out, _ = self.run_main(["check", LEVER])
        self.assertIn("42.00", out)
        self.assertIn("28.76", out)

    def test_check_prints_no_none_anywhere(self):
        self.sync()
        for entry in fixture_entries():
            _, out, _ = self.run_main(["check", entry["id"]])
            self.assertNotIn("None", out, entry["id"])

    def test_check_unknown_id_exits_one(self):
        self.sync()
        code, _, err = self.run_main(["check", "unknown"])
        self.assertEqual(code, 1)
        self.assertIn("unknown", err)

    def test_check_ambiguous_prefix_says_so_instead_of_guessing(self):
        self.sync()
        code, _, err = self.run_main(["check", "c"])   # cc7e8b44 and c68e731b
        self.assertEqual(code, 1)
        self.assertIn("ambiguous", err)

    def test_report_on_an_empty_store(self):
        code, out, _ = self.run_main(["report"])
        self.assertEqual(code, 0)
        self.assertIn("No shots recorded yet", out)

    def test_report_prints_short_ids_that_check_accepts(self):
        self.sync()
        code, out, _ = self.run_main(["report"])
        self.assertEqual(code, 0)
        self.assertIn(LEVER, out)
        self.assertEqual(self.run_main(["check", LEVER])[0], 0)

    def test_rate_rejects_an_unknown_bag(self):
        # rate_shot raises on the bag answer before asking anything else, so
        # only "b999" is ever consumed; the rest was dead v2-era input
        self.sync()
        code, _, err = self.run_main(["rate", LEVER], ["b999"])
        self.assertEqual(code, 1)
        self.assertIn("b999", err)

    def test_rate_unknown_shot_exits_one(self):
        self.sync()
        code, _, err = self.run_main(["rate", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("nope", err)

    def test_sync_is_idempotent_through_the_cli(self):
        self.sync()
        code, out, _ = self.sync()
        self.assertEqual(code, 0)
        self.assertIn("added 0, skipped 7", out)

    def test_malformed_entry_reaches_the_report_flagged(self):
        entries = fixture_entries()[:3]
        entries[1] = {**entries[1], "time": None}
        code, out, _ = self.sync(entries)
        self.assertEqual(code, 0)
        self.assertIn("unreadable telemetry", out)
        code, out, _ = self.run_main(["report"])
        self.assertEqual(code, 0)
        self.assertIn("!telemetry_unparsed", out)

    def test_report_wires_agreement_tally_end_to_end(self):
        # the real archive carries zero schema-3 ratings, so this is the only
        # place cli.py's report handler -- the lambda threading
        # _telemetry_loader(store) through extract.agreement_tally -- has
        # ever actually run against schema-3 data rather than being exercised
        # only through format_report called directly
        store = Store(self.root)
        store.append_bag({"id": "b001", "roaster": "r", "name": "n",
                          "process": "washed", "roast_date": "2026-08-01",
                          "opened": "2026-08-01", "note": ""})
        rows = [{"id": f"s{i}", "ts": "2026-08-10T07:00:00", "bag": "b001",
                 "grinder": "g001", "profile": "Turbo", "dose_g": 18.0,
                 "grind": 3.1, "yield_g": 30.0, "time_s": 28.0, "ratio": 1.67,
                 "taste": {"lean": "sour", "intensity": 2, "versus": None},
                 "taste_schema": 3, "note": "", "flags": []} for i in range(5)]
        store.append_shots(rows)
        # target 40g, actual 30g: a real deficit past threshold(40)=2.0, so
        # grade() reads it as sour -- matching every blind call, so the
        # wiring's own math (not a stub) is what produces "5 of 5"
        telemetry = {"profile": {"final_weight": 40.0},
                     "data": [{"shot": {"weight": 30.0}}]}
        for row in rows:
            store.write_telemetry(row["id"], telemetry)
        code, out, err = self.run_main(["report"])
        self.assertEqual(code, 0, err)
        self.assertIn("leans sour in 5 of 5", out)
        self.assertIn("your calls agreed with the machine in 5 of 5", out)


class TestFormatBags(unittest.TestCase):
    BAGS = [
        {"id": "b001", "roaster": "Copenhagen Roasters", "name": "Slow Roast",
         "process": "washed", "roast_date": "2026-06-16", "opened": "2026-07-25"},
        {"id": "b002", "roaster": "Square Mile", "name": "Red Brick",
         "process": "natural", "roast_date": "2026-07-20", "opened": "2026-07-24"},
    ]
    ROWS = [
        {"id": "a", "ts": "2026-07-25T09:00:00", "bag": "b001", "taste": None},
        {"id": "b", "ts": "2026-07-25T10:00:00", "bag": "b001", "taste": None},
        {"id": "c", "ts": "2026-07-25T11:00:00", "bag": None, "taste": None},
    ]

    def test_empty_says_how_to_add_one(self):
        out = format_bags([], today="2026-07-25")
        self.assertIn("No bags", out)
        self.assertIn("bag", out)

    def test_lists_every_bag(self):
        out = format_bags(self.BAGS, today="2026-07-25")
        self.assertIn("b001", out)
        self.assertIn("b002", out)
        self.assertIn("Copenhagen Roasters", out)

    def test_marks_the_most_recently_used_bag_as_current(self):
        # b001 is the OLDER registered bag but the only one any shot has
        # used (per self.ROWS). Under the superseded "most recently
        # registered" rule this would wrongly mark b002, which no shot has
        # touched, so this fails against the old definition and passes
        # against the current one.
        out = format_bags(self.BAGS, self.ROWS, today="2026-07-25")
        current = [l for l in out.splitlines() if l.startswith("*")]
        self.assertEqual(len(current), 1)
        self.assertIn("b001", current[0])

    def test_falls_back_to_most_recently_registered_when_no_shot_has_used_one(self):
        # most_recently_used_bag's fallback: with nothing used yet, there is
        # nothing used to point at, so registration order still decides.
        out = format_bags(self.BAGS, today="2026-07-25")
        current = [l for l in out.splitlines() if l.startswith("*")]
        self.assertEqual(len(current), 1)
        self.assertIn("b002", current[0])

    def test_marker_agrees_with_entry_current_bag_id(self):
        # format_bags and entry.current_bag_id share model.most_recently_used_bag,
        # so they can never name two different bags as "current". Drive both
        # from one real Store round-trip rather than two hand-typed fixtures
        # that could quietly drift apart from each other.
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(pathlib.Path(tmp))
            for bag in self.BAGS:
                store.append_bag(bag)
            store.append_shots(self.ROWS)
            out = format_bags(store.load_bags(), store.load_shots(), today="2026-07-25")
            current = [l for l in out.splitlines() if l.startswith("*")][0]
            self.assertIn(current_bag_id(store), current)

    def test_shows_days_off_roast(self):
        out = format_bags(self.BAGS, today="2026-07-25")
        self.assertIn("d+39", out)   # 2026-06-16 -> 2026-07-25
        self.assertIn("d+5", out)    # 2026-07-20 -> 2026-07-25

    def test_counts_shots_per_bag(self):
        out = format_bags(self.BAGS, self.ROWS, today="2026-07-25")
        b001 = [l for l in out.splitlines() if "d+39" in l][0]
        self.assertIn("2 shots", b001)

    def test_missing_roast_date_does_not_crash(self):
        out = format_bags([{"id": "b001", "roaster": "X"}], today="2026-07-25")
        self.assertIn("b001", out)
        self.assertNotIn("d+", out)


class TestMixedSchemaRendering(unittest.TestCase):
    """v1 rows must stay readable next to v2 rows, and must never be pooled."""

    V1 = {"id": "a", "ts": "2026-07-26T07:30:00", "bag": "b001", "profile": "Lever",
          "dose_g": 20.0, "grind": 2.7, "yield_g": 39.6, "time_s": 30.0, "ratio": 1.98,
          "taste": {"sour_bitter": 0, "body": 4, "overall": 4}, "taste_schema": 1}
    V2 = {"id": "b", "ts": "2026-07-27T07:30:00", "bag": "b001", "profile": "Lever",
          "dose_g": 20.0, "grind": 2.7, "yield_g": 40.0, "time_s": 31.0, "ratio": 2.0,
          "taste": {"sour": 6, "bitter": 5, "body": 7, "overall": 4}, "taste_schema": 2}

    def test_v1_row_renders_without_crashing_and_is_marked(self):
        out = format_report([self.V1])
        self.assertIn("v1", out)
        self.assertNotIn("Traceback", out)

    def test_v2_row_shows_both_axes(self):
        out = format_report([self.V2])
        self.assertIn("sour6", out)
        self.assertIn("bitter5", out)

    def test_mixing_versions_warns(self):
        out = format_report([self.V1, self.V2])
        self.assertIn("taste_schema", out)
        self.assertIn("not comparable", out)

    def test_all_v2_does_not_warn(self):
        self.assertNotIn("not comparable", format_report([self.V2]))


class TestBanner(unittest.TestCase):
    """The logo is a welcome mat, not a header. It must appear on a bare
    invocation and nowhere else, or it corrupts output that gets piped."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(cli, "ROOT", pathlib.Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_bare_invocation_shows_the_banner_and_exits_zero(self):
        code, out = self.run_main([])
        self.assertEqual(code, 0)
        # assert on the shape, not one glyph run, so a redesign does not
        # silently turn this into a test of nothing
        self.assertTrue(any(ch in out for ch in "█▀▄"), "no wordmark in banner")
        self.assertTrue(any("\u2800" <= ch <= "\u28ff" for ch in out),
                        "no braille curve in banner")
        self.assertIn("intent", out)

    def test_banner_lists_every_command(self):
        _, out = self.run_main([])
        for name, _description in cli.COMMANDS:
            self.assertIn(name.split()[0], out)

    def test_banner_states_the_read_only_guarantee(self):
        # the claim must be present; its capitalisation is not the point
        _, out = self.run_main([])
        self.assertIn("read-only", out.lower())
        self.assertIn("never brews", out.lower())

    def test_banner_does_not_appear_on_report(self):
        _, out = self.run_main(["report"])
        self.assertNotIn("▀▀▀", out)

    def test_banner_does_not_appear_on_bags(self):
        _, out = self.run_main(["bags"])
        self.assertNotIn("▀▀▀", out)

    def test_logo_lines_fit_a_narrow_terminal(self):
        for line in cli.LOGO.splitlines():
            self.assertLessEqual(len(line), 60, line)

    def test_command_list_matches_the_registered_subcommands(self):
        # a command added to the parser but missing from the banner would be
        # undiscoverable, which is how `rate` and `check` were unreachable once,
        # and how `nudge` was nearly unreachable again. Checked against the
        # parser's own subparser choices, not a third hand-maintained list --
        # a hardcoded literal here passes silently on exactly this mistake.
        listed = {name.split()[0] for name, _ in cli.COMMANDS}
        subparsers_action = next(
            action for action in cli.build_parser()._actions
            if isinstance(action, argparse._SubParsersAction))
        registered = set(subparsers_action.choices)
        self.assertEqual(listed, registered)

    def test_logo_uses_braille_blanks_not_spaces_inside_the_plot(self):
        # U+2020 space would collapse in some fonts and skew the curve; the
        # plot must be padded with U+2800 braille blank
        plot = [l for l in cli.LOGO.splitlines()
                if any("\u2800" <= ch <= "\u28ff" for ch in l)]
        self.assertTrue(plot, "no braille rows found in the logo")
        for line in plot:
            body = "".join(ch for ch in line.strip())
            self.assertNotIn(" ", body, line)

    def test_logo_is_pure_text_with_no_escape_codes(self):
        # colour would fight the user's terminal theme
        self.assertNotIn("\x1b", cli.LOGO)

    def test_banner_names_the_machine_it_talks_to(self):
        # someone running this bare should not need the README to learn that
        _, out = self.run_main([])
        self.assertIn("Meticulous", out)

    def test_banner_states_what_the_tool_records(self):
        _, out = self.run_main([])
        for word in ("profile", "machine", "tasted"):
            self.assertIn(word, out)

    def test_description_sits_between_logo_and_commands(self):
        _, out = self.run_main([])
        desc = out.index(cli.DESCRIPTION[0])
        self.assertLess(out.index("intent · telemetry · taste"), desc)
        self.assertLess(desc, out.index("sync"))

    def test_description_lines_stay_narrow(self):
        for line in cli.DESCRIPTION:
            self.assertLessEqual(len(line), 68, line)

    def test_logo_contains_the_glass(self):
        # the mark is a glass with the curve as its steam; losing either half
        # loses the idea
        self.assertTrue(any(ch in cli.LOGO for ch in "╭╰╲╱"), "no glass in logo")
        self.assertIn("▒", cli.LOGO, "glass has no crema")


class TestSetup(unittest.TestCase):
    """`setup` must never write until a machine is confirmed, and must never
    save an address that did not actually answer as a Meticulous."""

    MACHINE = {"name": "MeticulousExample", "hostname": "met-000000",
               "firmware": "0.2.24", "serial": "000000"}
    OTHER = {"name": "Second Machine", "hostname": "met-999", 
             "firmware": "0.2.24", "serial": "000999"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "cfg"),
                                      "XDG_DATA_HOME": str(root / "data")})
        env.start(); self.addCleanup(env.stop)
        for name in ("SHOTCRAFT_API_URL", "METICULOUS_API_URL", "SHOTCRAFT_HOME"):
            os.environ.pop(name, None)

    def run_setup(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.run_setup(**kw)
        return code, out.getvalue(), err.getvalue()

    def stored(self):
        from shotcraft import config
        return config.load()

    def test_a_single_find_is_saved_without_asking(self):
        code, out, _ = self.run_setup(
            discover=lambda: [{"base_url": "http://10.0.0.5", "machine": self.MACHINE}])
        self.assertEqual(code, 0)
        self.assertEqual(self.stored()["base_url"], "http://10.0.0.5")
        self.assertIn("000000", out)

    def test_nothing_found_exits_one_and_saves_nothing(self):
        code, _, err = self.run_setup(discover=lambda: [])
        self.assertEqual(code, 1)
        self.assertEqual(self.stored(), {})
        self.assertIn("--url", err)

    def test_explicit_url_that_does_not_answer_saves_nothing(self):
        code, _, err = self.run_setup(url="http://10.0.0.9", verify=lambda u: None)
        self.assertEqual(code, 1)
        self.assertEqual(self.stored(), {})

    def test_explicit_url_that_answers_is_saved(self):
        code, _, _ = self.run_setup(url="http://10.0.0.9/",
                                    verify=lambda u: self.MACHINE)
        self.assertEqual(code, 0)
        self.assertEqual(self.stored()["base_url"], "http://10.0.0.9")

    def test_two_machines_prompts_and_honours_the_choice(self):
        found = [{"base_url": "http://10.0.0.5", "machine": self.MACHINE},
                 {"base_url": "http://10.0.0.6", "machine": self.OTHER}]
        code, _, _ = self.run_setup(discover=lambda: found, ask=lambda p: "2")
        self.assertEqual(code, 0)
        self.assertEqual(self.stored()["base_url"], "http://10.0.0.6")

    def test_blank_answer_takes_the_first(self):
        found = [{"base_url": "http://10.0.0.5", "machine": self.MACHINE},
                 {"base_url": "http://10.0.0.6", "machine": self.OTHER}]
        code, _, _ = self.run_setup(discover=lambda: found, ask=lambda p: "")
        self.assertEqual(self.stored()["base_url"], "http://10.0.0.5")

    def test_nonsense_choice_saves_nothing(self):
        found = [{"base_url": "http://10.0.0.5", "machine": self.MACHINE},
                 {"base_url": "http://10.0.0.6", "machine": self.OTHER}]
        code, _, err = self.run_setup(discover=lambda: found, ask=lambda p: "9")
        self.assertEqual(code, 1)
        self.assertEqual(self.stored(), {})

    def test_show_reports_without_writing(self):
        code, out, _ = self.run_setup(show=True)
        self.assertEqual(code, 0)
        self.assertIn("in effect", out)
        self.assertEqual(self.stored(), {})

    def test_show_reveals_that_env_overrides_the_file(self):
        from shotcraft import config
        config.save(base_url="http://from-file")
        with patch.dict(os.environ, {"SHOTCRAFT_API_URL": "http://from-env"}):
            _, out, _ = self.run_setup(show=True)
        self.assertIn("http://from-env", out)

    def test_sync_without_configuration_says_what_to_do(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["sync"])
        message = err.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("setup", message)
        self.assertNotIn("Traceback", message)


class TestVersionAndOrder(unittest.TestCase):
    def test_version_appears_in_the_banner(self):
        from shotcraft import __version__
        self.assertIn(f"v{__version__}", cli.format_banner())

    def test_banner_version_comes_from_the_single_constant(self):
        # not a literal in the art: patching the constant must change the output.
        # format_banner lives in shotcraft.format now, so that is the copy of
        # __version__ that has to be patched for the assertion to mean anything.
        with patch("shotcraft.format.__version__", "9.9.9"):
            self.assertIn("v9.9.9", cli.format_banner())

    def test_the_art_itself_carries_no_version_number(self):
        # a number baked into the ASCII would go stale silently
        self.assertNotIn("v0.", cli.LOGO)

    def test_setup_is_listed_first(self):
        self.assertEqual(cli.COMMANDS[0][0], "setup",
                         "setup must lead: nothing else works unconfigured")

    def test_every_registered_command_is_listed_exactly_once(self):
        names = [n.split()[0] for n, _ in cli.COMMANDS]
        self.assertEqual(len(names), len(set(names)))

    def test_version_rides_on_the_final_line_not_one_of_its_own(self):
        from shotcraft import __version__
        last = cli.format_banner().rstrip().splitlines()[-1]
        self.assertIn(f"v{__version__}", last)
        self.assertIn("Read-only", last, "version must not be orphaned alone")

    def test_the_banner_fits_eighty_columns(self):
        for line in cli.format_banner().splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_the_tagline_stays_clean(self):
        # the version used to ride on the tagline and broke its centring
        tagline = [l for l in cli.format_banner().splitlines() if "intent ·" in l][0]
        self.assertNotIn("v0.", tagline)

    def test_readme_shows_the_same_art_as_the_banner(self):
        # the README is the repo's front page; if the art drifts from the real
        # banner the first thing anyone sees is already a lie
        readme = (pathlib.Path(__file__).parent.parent / "README.md").read_text()
        for line in cli.LOGO.strip("\n").splitlines():
            self.assertIn(line, readme, f"README missing logo line: {line!r}")


class TestSyncFromDirectory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.blobs = self.root / "blobs"
        self.blobs.mkdir()
        # --from arrives via argv, not env; ROOT is only the destination store
        # path, so that is the only thing that needs patching here (same
        # convention as TestMain.setUp / TestBanner.setUp).
        root_patch = patch.object(cli, "ROOT", self.root / "data")
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def test_sync_from_directory_needs_no_configured_machine(self):
        entry = {"id": "abc123", "time": 1786351889.0, "name": "Turbo",
                 "profile": {"name": "Turbo"},
                 "data": [{"shot": {"pressure": 6.0, "flow": 2.0, "weight": 40.0},
                           "profile_time": 20000, "status": "infusion"}]}
        (self.blobs / "abc123.json").write_text(json.dumps(entry))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["sync", "--from", str(self.blobs)])
        self.assertEqual(code, 0)
        self.assertIn("added 1", out.getvalue())

    def test_sync_from_directory_stamps_the_blobs_capture_time_not_now(self):
        # the entry above is dated 2026-08-10, days before this test runs;
        # stamping `now` would hide a capture directory that stopped updating
        entry = {"id": "abc123", "time": 1786351889.0, "name": "Turbo",
                 "profile": {"name": "Turbo"},
                 "data": [{"shot": {"pressure": 6.0, "flow": 2.0, "weight": 40.0},
                           "profile_time": 20000, "status": "infusion"}]}
        (self.blobs / "abc123.json").write_text(json.dumps(entry))
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["sync", "--from", str(self.blobs)])
        store = Store(self.root / "data")
        self.assertGreater(store.sync_age_days(), 1.0)


class TestFormatReveal(unittest.TestCase):
    """The reveal is pure — rows and results in, string out — so every branch
    is reachable without a filesystem. The wiring test only ever sees the
    agreeing one."""

    TASTE = {"lean": "sour", "intensity": 2, "versus": None}
    ROW = {"id": "z", "yield_g": 48.0, "time_s": 32.0, "ratio": 2.66,
           "taste": TASTE}
    ENTRY = {"id": "z", "profile": {"name": "T", "final_weight": 40.0},
             "data": [{"shot": {"weight": 48.0}}]}

    def test_missing_numbers_render_as_dashes_not_none(self):
        # a flagged row reaches here with null machine fields; "None" on
        # screen reads as data rather than as absence
        row = {"id": "x", "yield_g": None, "time_s": None, "ratio": None,
               "taste": self.TASTE}
        out = format_reveal(row, grade({"id": "x"}, self.TASTE), (0, 0))
        self.assertNotIn("None", out)
        self.assertIn("-g in -s", out)

    def test_an_on_target_shot_says_the_machine_has_nothing_to_say(self):
        # declining silently is indistinguishable from having no opinion, so
        # the reason is always named
        entry = {**self.ENTRY, "data": [{"shot": {"weight": 39.5}}]}
        out = format_reveal({**self.ROW, "yield_g": 39.5},
                            grade(entry, self.TASTE), (3, 7))
        self.assertIn("No call to grade:", out)
        self.assertIn("tracked its profile", out)

    def test_disagreement_names_both_directions(self):
        out = format_reveal(self.ROW, grade(self.ENTRY, self.TASTE), (1, 4))
        self.assertIn("You called it sour", out)
        self.assertIn("the machine points bitter", out)
        self.assertIn("8g past the target", out)

    def test_nothing_checkable_is_stated_not_left_silent(self):
        # "0 of 0" would read as a damning ratio the tool cannot back up, and
        # staying silent is indistinguishable from having nothing to say
        # (extract.py's own rule) -- so it says so plainly instead, without
        # a digit, "of", or "agreed" that would read as a score of zero.
        out = format_reveal(self.ROW, grade(self.ENTRY, self.TASTE), (0, 0))
        self.assertNotIn("Calls matching", out)
        last_line = out.rsplit("\n", 1)[-1]
        self.assertIn("could be checked against the machine", last_line)
        self.assertNotIn(" of ", last_line)
        self.assertNotIn("agreed", last_line)
        self.assertFalse(any(c.isdigit() for c in last_line))

    def test_a_balanced_call_reads_as_english_not_as_the_raw_enum(self):
        # `grade` filters "both" but not "none", so a balanced call against a
        # shot that missed its target is ordinary and reachable. Rendering the
        # stored enum straight out produced "You called it none".
        taste = {"lean": "none", "intensity": 0, "versus": None}
        out = format_reveal({**self.ROW, "taste": taste},
                            grade(self.ENTRY, taste), (1, 4))
        self.assertIn("You called it balanced", out)
        self.assertNotIn("called it none", out)
        self.assertIn("the machine points bitter", out)


class TestFormatRevealMatchedPair(unittest.TestCase):
    """The matched-pair bonus line only appears when both `previous` and
    `note` are supplied, exactly as `cli.py` supplies them together."""

    TASTE = {"lean": "sour", "intensity": 2, "versus": None}
    ROW = {"id": "z", "ts": "2026-08-09T07:00:00", "bag": "b001",
           "profile": "Turbo", "yield_g": 48.0, "time_s": 32.0,
           "ratio": 2.66, "grind": 3.1, "taste": TASTE}
    ENTRY = {"id": "z", "profile": {"name": "T", "final_weight": 40.0},
             "data": [{"shot": {"weight": 48.0}}]}
    RESULT = grade(ENTRY, TASTE)

    def test_no_pair_line_when_there_is_no_match(self):
        out = format_reveal(self.ROW, self.RESULT, (1, 4))
        self.assertNotIn("Last Turbo on this bag", out)

    def test_pair_line_names_the_previous_shot_and_the_day_gap(self):
        previous = {"ts": "2026-08-01T07:00:00", "profile": "Turbo",
                    "yield_g": 40.0, "time_s": 28.0, "grind": 3.1}
        note = pair_note(self.ROW, previous)
        out = format_reveal(self.ROW, self.RESULT, (1, 4), previous, note)
        self.assertIn("Last Turbo on this bag (2026-08-01T07:00, 8d apart): "
                      "40.0g in 28.0s", out)

    def test_same_day_pair_omits_the_apart_suffix(self):
        previous = {"ts": "2026-08-09T06:00:00", "profile": "Turbo",
                    "yield_g": 40.0, "time_s": 28.0, "grind": 3.1}
        note = pair_note(self.ROW, previous)
        out = format_reveal(self.ROW, self.RESULT, (1, 4), previous, note)
        self.assertIn("Last Turbo on this bag (2026-08-09T06:00): "
                      "40.0g in 28.0s", out)
        self.assertNotIn("apart", out)

    def test_five_or_more_days_apart_reads_the_bean_as_older(self):
        previous = {"ts": "2026-08-04T07:00:00", "profile": "Turbo",
                    "yield_g": 40.0, "time_s": 28.0, "grind": 3.1}
        note = pair_note(self.ROW, previous)
        self.assertEqual(note["days"], 5)
        out = format_reveal(self.ROW, self.RESULT, (1, 4), previous, note)
        self.assertIn("meaningfully older", out)

    def test_fewer_than_five_days_apart_says_nothing_about_staleness(self):
        previous = {"ts": "2026-08-06T07:00:00", "profile": "Turbo",
                    "yield_g": 40.0, "time_s": 28.0, "grind": 3.1}
        note = pair_note(self.ROW, previous)
        self.assertEqual(note["days"], 3)
        out = format_reveal(self.ROW, self.RESULT, (1, 4), previous, note)
        self.assertNotIn("meaningfully older", out)

    def test_suspect_redial_asks_the_question_and_names_the_fix(self):
        previous = {"ts": "2026-08-08T07:00:00", "profile": "Turbo",
                    "yield_g": 25.0, "time_s": 28.0, "grind": 3.1}
        note = pair_note(self.ROW, previous)
        self.assertTrue(note["suspect_redial"])
        out = format_reveal(self.ROW, self.RESULT, (1, 4), previous, note)
        self.assertIn("Did you re-dial without recording it?", out)
        self.assertIn("dial <value>", out)

    def test_a_logged_grind_change_never_triggers_the_redial_question(self):
        previous = {"ts": "2026-08-08T07:00:00", "profile": "Turbo",
                    "yield_g": 25.0, "time_s": 28.0, "grind": 3.4}
        note = pair_note(self.ROW, previous)
        self.assertFalse(note["suspect_redial"])
        out = format_reveal(self.ROW, self.RESULT, (1, 4), previous, note)
        self.assertNotIn("re-dial", out)


class TestRateRevealOrdering(unittest.TestCase):
    """The rating is the deliverable; the reveal is a bonus that may fail.

    ROOT is patched with `patch.object` rather than driven through
    SHOTCRAFT_HOME plus `importlib.reload(cli)`: reload permanently rebinds
    the module-level ROOT, and un-patching the env var afterwards does not
    undo it, leaving every later test in the process pointed at a deleted
    temp directory. Same convention as TestMain.setUp and TestBanner.setUp.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        root_patch = patch.object(cli, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        store = Store(self.root)
        store.append_grinder({"id": "g001", "make": "1Zpresso", "model": "K-Ultra",
                              "scale": "dial", "finer_direction": "lower",
                              "note": ""})
        store.append_bag({"id": "b001", "roaster": "r", "name": "n",
                          "process": "washed", "roast_date": "2026-08-01",
                          "opened": "2026-08-01", "note": ""})
        store.append_dial({"ts": "2026-08-01T06:00:00", "grinder": "g001",
                           "bag": "b001", "grind": 3.1, "dose_g": 18.0,
                           "note": ""})
        store.append_shots([{
            "id": "shot1", "ts": "2026-08-02T07:00:00", "bag": None,
            "profile": "Turbo", "dose_g": None, "grind": None,
            "yield_g": 35.7, "time_s": 28.1, "ratio": None,
            "peak_pressure": 6.1, "peak_flow": 4.2,
            "taste": None, "taste_schema": None, "note": "", "flags": []}])

    def rate(self, answers=("", "s", "2", "")):
        out, err = io.StringIO(), io.StringIO()
        with patch("builtins.input", scripted(list(answers))):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["rate", "shot1"])
        return code, out.getvalue(), err.getvalue()

    def stored(self):
        return [r for r in Store(self.root).load_shots() if r["id"] == "shot1"][0]

    def telemetry(self):
        Store(self.root).write_telemetry("shot1", {
            "id": "shot1", "time": 1786351889.0,
            "profile": {"name": "Turbo", "final_weight": 40.0},
            "data": [{"shot": {"pressure": 6.0, "flow": 2.0, "weight": 35.7},
                      "profile_time": 28100, "status": "infusion"}]})

    def test_rating_lands_and_exits_zero_without_a_telemetry_blob(self):
        code, printed, _ = self.rate()
        self.assertEqual(code, 0)
        self.assertIn("Recorded.", printed)
        self.assertNotIn("Asked for", printed)
        stored = self.stored()
        self.assertEqual(stored["taste"]["lean"], "sour")
        self.assertEqual(stored["taste_schema"], 3)

    def test_reveal_appears_once_telemetry_exists(self):
        self.telemetry()
        code, printed, _ = self.rate()
        self.assertEqual(code, 0)
        self.assertIn("Asked for 40g", printed)
        self.assertIn("point the same way", printed)

    def test_no_machine_number_precedes_the_recorded_line(self):
        # the ordering itself, asserted on the transcript: everything the
        # machine knows must appear AFTER the rating is on disk, and
        # "Recorded." is the only marker of that moment the user can see.
        self.telemetry()
        _, printed, _ = self.rate()
        before = printed[:printed.index("Recorded.")]
        # "40g", not a bare "40": a bare "40" also matches a timestamp such as
        # 2026-08-02T07:40, which would make the fixture's ts quietly
        # load-bearing for a reason nothing in the test records
        for forbidden in ("35.7", "28.1", "6.1", "4.2", "1.98", "40g"):
            self.assertNotIn(forbidden, before)

    def test_a_broken_grader_costs_the_reveal_and_never_the_rating(self):
        Store(self.root).write_telemetry("shot1", {"id": "shot1"})
        with patch.object(cli, "grade", side_effect=RuntimeError("boom")):
            code, printed, err = self.rate()
        self.assertEqual(code, 0)
        self.assertIn("Recorded.", printed)
        self.assertIn("no reveal", err)
        self.assertNotIn("Traceback", err)
        stored = self.stored()
        self.assertEqual(stored["taste"]["lean"], "sour")
        self.assertEqual(stored["taste_schema"], 3)

    def test_unreadable_telemetry_says_so_instead_of_declining_silently(self):
        # extract.py's own words: a tool that silently declines to speak is
        # indistinguishable from one that has nothing to say. A grader failure
        # already explains itself; an unparseable blob must too, or the reveal
        # just vanishes with no way to tell why.
        (self.root / "telemetry").mkdir(parents=True, exist_ok=True)
        (self.root / "telemetry" / "shot1.json").write_text("{not json")
        code, printed, err = self.rate()
        self.assertEqual(code, 0)
        self.assertIn("Recorded.", printed)
        self.assertIn("no reveal", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.stored()["taste"]["lean"], "sour")

    def test_missing_telemetry_says_so_instead_of_declining_silently(self):
        # no telemetry() call: the blob was never written at all, distinct
        # from the unreadable-blob case above -- this used to return 0 with
        # no word to the user while every other decline branch here speaks
        code, printed, err = self.rate()
        self.assertEqual(code, 0)
        self.assertIn("Recorded.", printed)
        self.assertIn("telemetry missing, no reveal", err)

    def test_a_flagged_shot_is_never_graded_even_though_its_telemetry_would_agree(self):
        # this row's own sync already flagged it as unparseable, but the raw
        # telemetry blob below would otherwise grade cleanly AND agree with
        # the "sour" call -- so a passing reveal here would prove the flag,
        # not the telemetry content, is what has to decide gradeability
        Store(self.root).update_shot("shot1",
                                     {"flags": ["telemetry_unparsed: boom"]})
        self.telemetry()
        code, printed, _ = self.rate()
        self.assertEqual(code, 0)
        self.assertIn("Recorded.", printed)
        self.assertIn("No call to grade:", printed)
        self.assertIn("flagged", printed)


class TestReportSchemaThree(unittest.TestCase):
    def _row(self, shot_id, lean, intensity, bag="b001", grinder="g001"):
        return {"id": shot_id, "ts": "2026-08-10T07:00:00", "bag": bag,
                "grinder": grinder, "profile": "Turbo", "dose_g": 18.0,
                "grind": 3.1, "yield_g": 40.0, "time_s": 28.0, "ratio": 2.22,
                "taste": {"lean": lean, "intensity": intensity, "versus": None},
                "taste_schema": 3, "note": "", "flags": []}

    def test_schema_three_rating_renders(self):
        out = fmt.format_report([self._row("a", "sour", 2)],
                                [{"id": "b001", "roast_date": "2026-08-01"}])
        self.assertIn("sour/2", out)

    def test_mixed_schemas_warn(self):
        v2 = self._row("b", "sour", 2)
        v2["taste"] = {"sour": 3, "bitter": 1, "body": 5, "overall": 7}
        v2["taste_schema"] = 2
        out = fmt.format_report([self._row("a", "sour", 2), v2],
                                [{"id": "b001", "roast_date": "2026-08-01"}])
        self.assertIn("not comparable", out)

    def test_multiple_grinders_on_one_bag_warn(self):
        rows = [self._row("a", "sour", 2, grinder="g001"),
                self._row("b", "sour", 2, grinder="g002")]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}])
        self.assertIn("grinder", out.lower())

    def test_a_grind_logged_with_no_grinder_id_is_its_own_bucket(self):
        # a legacy row (rated before the grinder concept existed) carries a
        # grind value but no `grinder` key at all -- dropping it from the
        # pooling check let it silently pool with g001's numbers and warn
        # about nothing, when "unknown" is no more comparable to g001 than
        # g002 is
        legacy = self._row("a", "sour", 2, grinder="g001")
        del legacy["grinder"]
        rows = [legacy, self._row("b", "sour", 2, grinder="g001")]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}])
        self.assertIn("grinder", out.lower())
        self.assertIn("not comparable", out)

    def test_uniformly_ungrindered_rows_do_not_falsely_warn(self):
        # everything missing a grinder id together is not "mixed" -- just
        # legacy-only, no warning earned
        legacy = self._row("a", "sour", 2, grinder="g001")
        del legacy["grinder"]
        out = fmt.format_report([legacy], [{"id": "b001", "roast_date": "2026-08-01"}])
        self.assertNotIn("not comparable", out)

    def test_advice_names_the_concrete_dial_direction_when_the_grinder_is_known(self):
        rows = [self._row(str(i), "sour", 2) for i in range(5)]
        grinders = [{"id": "g001", "finer_direction": "lower"}]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}],
                                grinders=grinders, tally_for=lambda _rows: (4, 5))
        self.assertIn("a finer grind (a lower number on g001)", out)

    def test_advice_names_the_opposite_direction_for_bitter(self):
        rows = [self._row(str(i), "bitter", 2) for i in range(5)]
        grinders = [{"id": "g001", "finer_direction": "higher"}]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}],
                                grinders=grinders, tally_for=lambda _rows: (4, 5))
        self.assertIn("a coarser grind (a lower number on g001)", out)

    def test_advice_falls_back_to_generic_wording_with_no_grinder_registered(self):
        # honest about the limit rather than guessing a dial direction --
        # model.grinder_row's own docstring is why this cannot be invented
        rows = [self._row(str(i), "sour", 2) for i in range(5)]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}],
                                tally_for=lambda _rows: (4, 5))
        self.assertIn("finer grind or longer contact", out)
        self.assertNotIn("number on", out)

    def test_advice_appears_at_five_ratings(self):
        rows = [self._row(str(i), "sour", 2) for i in range(5)]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01",
                                        "roaster": "r"}],
                                tally_for=lambda _rows: (4, 5))
        self.assertIn("leans sour in 5 of 5", out)
        self.assertIn("4 of 5", out)

    def test_no_advice_below_five_ratings(self):
        rows = [self._row(str(i), "sour", 2) for i in range(4)]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}],
                                tally_for=lambda _rows: (3, 4))
        self.assertNotIn("leans sour", out)

    def test_verdict_renders_balanced_and_both_leans(self):
        # "none" and "both" are real calls, not the absence of one. "none"
        # renders as "balanced" -- the same word `_called` uses in the
        # reveal, so the two screens share one vocabulary instead of drifting
        # (storage stays the literal "none")
        balanced = self._row("z", "none", 0)
        both = self._row("y", "both", 3)
        out = fmt.format_report([balanced, both],
                                [{"id": "b001", "roast_date": "2026-08-01"}])
        self.assertIn("balanced/0", out)
        self.assertIn("both/3", out)

    def test_verdict_renders_versus_suffix(self):
        row = self._row("v", "bitter", 1)
        row["taste"]["versus"] = {"shot": "some-other-id", "verdict": "better"}
        out = fmt.format_report([row], [{"id": "b001", "roast_date": "2026-08-01"}])
        self.assertIn("bitter/1 vs better", out)

    def test_unmixed_schema_bag_keeps_single_confidence_label(self):
        # a bag rated entirely under schema 3 must render exactly as it did
        # before this round: one tier, no pooling qualifier
        rows = [self._row(str(i), "sour", 2) for i in range(5)]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}])
        self.assertIn("[pattern]", out)
        self.assertNotIn("pooled", out)

    def test_mixed_schema_bag_splits_confidence_label(self):
        # 8 schema-3 ratings (their own tier: pattern) plus 2 older-schema
        # ratings (pooled tier: hypothesis, n=10) on the same bag. The pooled
        # tier must not stand alone above the schema-3-specific leans/
        # agreement lines that follow -- a reader could otherwise carry
        # "hypothesis" onto a claim only "pattern" actually supports.
        v3_rows = ([self._row(str(i), "sour", 2) for i in range(6)]
                  + [self._row(f"b{i}", "bitter", 2) for i in range(2)])
        old_v2 = self._row("old1", "sour", 2)
        old_v2["taste"] = {"sour": 3, "bitter": 1, "body": 5, "overall": 7}
        old_v2["taste_schema"] = 2
        old_v1 = self._row("old2", "sour", 2)
        old_v1["taste"] = {"sour_bitter": 0, "body": 4, "overall": 4}
        old_v1["taste_schema"] = 1
        rows = v3_rows + [old_v2, old_v1]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}])
        self.assertIn("pattern for the 8 schema-3 ratings", out)
        self.assertIn("hypothesis pooled across all 10", out)

    def test_agreement_line_states_unchecked_when_not_gradeable(self):
        # majority + not gradeable: silence here would read as a lean that
        # passed a check it never underwent, so the qualifier must speak
        # plainly rather than vanish. It must also not be readable as
        # "0 of N agreed" -- that would be a damning verdict; the truth is
        # merely that nothing could be checked at all.
        rows = [self._row(str(i), "sour", 2) for i in range(5)]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}],
                                tally_for=lambda _rows: (0, 0))
        self.assertIn("leans sour in 5 of 5", out)
        self.assertIn("unchecked", out)
        self.assertIn("not disagreement", out)
        self.assertNotIn("agreed with the machine in 0 of", out)

    def test_no_majority_still_reports_ratio_when_gradeable(self):
        # no majority + gradeable: the ratio line was never gated on a lean
        # existing, and stays that way -- it's informational about the
        # calls' track record regardless of whether this batch leaned
        rows = [self._row("a", "sour", 2), self._row("b", "sour", 2),
                self._row("c", "bitter", 2), self._row("d", "bitter", 2),
                self._row("e", "none", 0)]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}],
                                tally_for=lambda _rows: (3, 5))
        self.assertNotIn("leans", out)
        self.assertIn("your calls agreed with the machine in 3 of 5", out)

    def test_no_majority_still_reports_unchecked_when_not_gradeable(self):
        # the combination that used to render completely silent: no leans
        # line (no majority) and nothing gradeable. The advice block only
        # renders once there are enough ratings to tempt a conclusion, so
        # checkability is worth stating either way -- neither variant is
        # gated on the leans line. Must not read as commentary on a lean
        # that was never claimed, so no "leans"/direction word appears.
        rows = [self._row("a", "sour", 2), self._row("b", "sour", 2),
                self._row("c", "bitter", 2), self._row("d", "bitter", 2),
                self._row("e", "none", 0)]
        out = fmt.format_report(rows, [{"id": "b001", "roast_date": "2026-08-01"}],
                                tally_for=lambda _rows: (0, 0))
        self.assertNotIn("leans", out)
        self.assertIn("unchecked", out)
        self.assertIn("not disagreement", out)
        self.assertNotIn("agreed with the machine in 0 of", out)


class TestNudgeIsSafeInAPrompt(unittest.TestCase):
    """nudge runs inside a shell prompt hook: never raise, never touch the
    network, never print past a stale stamp.

    ROOT is patched with `patch.object` rather than SHOTCRAFT_HOME plus
    `importlib.reload(cli)`: same reasoning as TestRateRevealOrdering above --
    reload permanently rebinds the module-level ROOT, and un-patching the env
    var afterwards does not undo it, leaving every later test in the process
    pointed at a deleted temp directory.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        root_patch = patch.object(cli, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def test_exits_zero_and_prints_nothing_on_a_freshly_synced_record(self):
        # NOT a truly empty install: ad03e0e (the commit right before this
        # task started) made never-synced print "last sync never" on
        # purpose -- "the default state of every fresh install, not an edge
        # case" -- so an install with no stamp at all is not the silent
        # case. Genuine silence needs zero unrated shots AND a recent sync.
        Store(self.root).write_sync_stamp()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["nudge", "--force"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_opens_no_socket(self):
        # AssertionError is an Exception subclass, and nudge's own blanket
        # `except Exception: pass` swallows it before `return 0` -- so a
        # side_effect version of this test passed whether or not a socket
        # was ever opened. A Mock plus a call-count assertion made OUTSIDE
        # nudge's own try/except is what actually pins this.
        import socket
        with contextlib.redirect_stdout(io.StringIO()):
            with patch.object(socket, "socket") as mock_socket:
                self.assertEqual(cli.main(["nudge", "--force"]), 0)
        mock_socket.assert_not_called()

    def test_exits_zero_even_when_the_store_is_corrupt(self):
        pathlib.Path(self.root, "shots.jsonl").write_text("{not json\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["nudge", "--force"]), 0)


if __name__ == "__main__":
    unittest.main()
