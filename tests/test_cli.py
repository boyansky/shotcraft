import contextlib, io, json, os, pathlib, tempfile, unittest
from unittest.mock import patch

from shotcraft import cli
from shotcraft.cli import evidence_level, format_bags, format_report

FIX = pathlib.Path(__file__).parent / "fixtures"

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

    def test_mixed_taste_schema_is_flagged(self):
        # only RATED rows carry a schema, so the mix must be built from two
        # rated rows of different versions, not from an unrated one
        v1 = {**ROWS[0], "id": "old",
              "taste": {"sour_bitter": 0, "body": 3, "overall": 4},
              "taste_schema": 1}
        self.assertIn("taste_schema", format_report([v1, ROWS[0]]))

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
        self.assertIn("unreachable", err.lower())

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

    def test_bag_then_rate_then_report_round_trip(self):
        self.sync()
        code, out, err = self.run_main(
            ["bag"], ["Square Mile", "Red Brick", "washed", "2026-07-18", ""])
        self.assertEqual(code, 0, err)
        self.assertIn("b001", out)

        code, out, err = self.run_main(
            ["rate", LEVER], ["b001", "18.0", "2.8", "1", "6", "3", "4", "tasted thin"])
        self.assertEqual(code, 0, err)

        code, out, _ = self.run_main(["report"])
        self.assertEqual(code, 0)
        self.assertIn("b001", out)
        self.assertIn("sour1 bitter6 body3 overall4", out)
        self.assertIn("1 rated", out)
        self.assertIn("d+7", out)          # days off roast, computed at read time

    def test_rate_rejects_an_unknown_bag(self):
        self.sync()
        code, _, err = self.run_main(
            ["rate", LEVER], ["b999", "18.0", "2.8", "0", "0", "3", "4", ""])
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

    def test_rate_a_flagged_shot_through_the_cli_does_not_crash(self):
        # sync's own output tells the user to rate this id, so the advertised
        # command must work rather than dumping a traceback
        entries = fixture_entries()[:3]
        entries[1] = {**entries[1], "time": None}
        flagged = entries[1]["id"]
        _, out, _ = self.sync(entries)
        self.assertIn(flagged[:8], out)                 # sync advertises it
        code, _, err = self.run_main(
            ["rate", flagged[:8]], ["", "18.0", "2.8", "0", "0", "3", "4", ""])
        self.assertEqual(code, 0, err)
        _, out, _ = self.run_main(["report"])
        self.assertIn("sour0 bitter0 body3 overall4", out)

    def test_malformed_entry_reaches_the_report_flagged(self):
        entries = fixture_entries()[:3]
        entries[1] = {**entries[1], "time": None}
        code, out, _ = self.sync(entries)
        self.assertEqual(code, 0)
        self.assertIn("unreadable telemetry", out)
        code, out, _ = self.run_main(["report"])
        self.assertEqual(code, 0)
        self.assertIn("!telemetry_unparsed", out)

if __name__ == "__main__":
    unittest.main()


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

    def test_marks_the_most_recently_added_as_current(self):
        out = format_bags(self.BAGS, today="2026-07-25")
        current = [l for l in out.splitlines() if l.startswith("*")]
        self.assertEqual(len(current), 1)
        self.assertIn("b002", current[0])

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
        # undiscoverable, which is how `rate` and `check` were unreachable once
        listed = {name.split()[0] for name, _ in cli.COMMANDS}
        self.assertEqual(listed,
                         {"sync", "rate", "report", "check", "bag",
                          "bags", "setup"})

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
        # not a literal in the art: patching the constant must change the output
        with patch("shotcraft.cli.__version__", "9.9.9"):
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
