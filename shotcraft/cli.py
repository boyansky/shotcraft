"""Command line entry point: sync, check, report."""
import argparse
import datetime
import json
import pathlib
import sys

from .api import MachineUnreachable, MeticulousAPI
from .config import NotConfigured
from . import __version__
from . import config
from . import discover as discovery
from .check import SHOT_TOTAL, check_shot
from .entry import new_bag, rate_shot
from .model import FLAG_UNKNOWN_BAG, days_off_roast
from .store import Store
from .sync import sync as run_sync

# the record lives outside the package (see config.data_home): installing
# shotcraft must never mean writing your shots into site-packages
ROOT = config.data_home()

SHORT_ID = 8   # uuid prefix printed everywhere and accepted back by rate/check


def evidence_level(n):
    """Honesty ladder. v1 never claims a validated finding."""
    if n < 2:
        return "observation"
    if n < 10:
        return "pattern"
    return "hypothesis"


def _dash(value):
    """Missing numbers read as '-'. The string 'None' looks like data."""
    return "-" if value is None else str(value)


def _num(value, width, places):
    """Fixed-width number, or a dash of the same width when it is missing."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:{width}.{places}f}"
    return f"{'-':>{width}}"


def invoked_as():
    """The name the user actually typed.

    Lets one install answer to more than one command name without any string
    in the help, the banner or the hints drifting out of sync with reality.
    Falls back to the package name when run as `python3 -m shotcraft.cli`,
    where argv[0] is a file path rather than a command.
    """
    name = pathlib.Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return "shotcraft" if (not name or name.endswith(".py")) else name


# The mark: an extraction curve drawn in braille (2x4 dots per cell, so it
# renders as a real thin line rather than a bar chart) sitting on the rim of a
# shot glass. The rim doubles as the x-axis, which is what lets the curve be
# read two ways at once: as steam off the glass, and as the shot's own trace
# returning to baseline.
#
# The glass is 23 wide, not the wordmark's 39. Terminal cells are about twice
# as tall as wide, so a full-width rim made the glass read at roughly 5:1 and
# looked like a trough. 23 is a deliberate compromise: a real shot glass is
# nearer 1:1.2, but a glass narrow enough for that leaves too few columns for
# the curve to read as a curve. Straight walls with a tapered base, rather
# than taper all the way up, is what makes the silhouette a shot glass instead
# of a funnel.
#
# The axis is deliberately ABSENT. This tool checks pressure-led and flow-led
# stages alike, so labelling the mark "9 bar" would brand it around one control
# mode the machine exists to move past.
#
# Braille blanks are U+2800, not spaces, so the curve cannot skew in fonts
# where the two differ in width. Hardcoded rather than computed: a logo should
# never be a code path that can fail at startup.
LOGO = """
          ⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠞⠉⠉⠓⠲⠤⣄⣀⠀⠀⠀⠀⠀
          ⠀⠀⠀⠀⠀⠀⠀⢀⡴⠋⠀⠀⠀⠀⠀⠀⠀⠈⠙⠒⠦⢤⣀
          ⠤⠴⠚⠉⠉⠉⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
          ╭─────────────────────╮
          │                     │
          ╲▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒╱
           ╲▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒╱
            ╰─────────────────╯

  █▀▀ █  █ █▀▀█ ▀█▀ █▀▀ █▀▀█ █▀▀█ █▀▀ ▀█▀
  ▀▀█ █▀▀█ █  █  █  █   █▄▄▀ █▄▄█ █▀▀  █ 
  ▀▀▀ ▀  ▀ ▀▀▀▀  ▀  ▀▀▀ ▀  ▀ ▀  ▀ ▀    ▀ 

         intent · telemetry · taste
"""

# Names the machine, because someone running this bare should not have to open
# a README to learn what it talks to. Naming Meticulous descriptively like this
# is nominative use; the tool is not affiliated with or endorsed by them.
DESCRIPTION = [
    "A shot record for Meticulous espresso machines: what the profile",
    "asked for, what the machine actually did, and how it tasted.",
]

COMMANDS = [
    # setup leads because nothing else works until a machine is configured
    ("setup", "find your machine on the network and remember it"),
    ("sync", "pull new shots, list the ones awaiting a rating"),
    ("rate <shot_id>", "dose, grind and taste — shows you no machine numbers"),
    ("report", "the corpus, grouped by bag, with sample sizes"),
    ("check <shot_id>", "did the shot track the profile it was given?"),
    ("bag", "register a new bag of beans"),
    ("bags", "list bags, marking the current one"),
]


def format_banner():
    """Shown only when the command is run bare.

    Deliberately NOT printed by any real command: a banner on every
    invocation is noise by day three, and it would corrupt output piped
    somewhere else.
    """
    lines = [LOGO.rstrip("\n"), ""]
    lines += [f"  {line}" for line in DESCRIPTION]
    lines.append("")
    width = max(len(name) for name, _ in COMMANDS)
    for name, description in COMMANDS:
        lines.append(f"   {name:{width}s}   {description}")
    lines.append("")
    # the guarantee, not a repeat of the description: this is the one claim
    # worth making on every bare invocation
    # the version rides on the footer rather than a line of its own: alone at
    # the bottom right it read as orphaned, and it must not go on the tagline
    # because that is centred. Rendered from the single constant in __init__,
    # never baked into the art.
    lines.append("   Read-only. Never writes to the machine, never brews "
                 f"a shot.  ·  v{__version__}")
    return "\n".join(lines)


def format_found(found):
    """One line per confirmed machine, naming it so the user can recognise it.

    Discovery finds addresses; only the machine document proves what is there.
    Showing name, firmware and serial is what lets someone confirm it is THEIR
    machine rather than a neighbour's.
    """
    lines = []
    for i, item in enumerate(found, 1):
        m = item["machine"]
        lines.append(f"  {i}. {m.get('name') or 'Meticulous'}")
        lines.append(f"     {item['base_url']}   firmware {m.get('firmware', '?')}"
                     f"   serial {m.get('serial', '?')}")
    return "\n".join(lines)


def run_setup(show=False, url=None, discover=None, verify=None, ask=input):
    """Find the machine and remember it. Writes nothing until confirmed."""
    check = verify or discovery.verify
    if show:
        env = config.env_base_url()
        print(f"config file   {config.config_path()}")
        print(f"data          {config.data_home()}")
        print(f"stored url    {config.load().get('base_url') or '(none)'}")
        print(f"env override  {env or '(none)'}")
        print(f"in effect     {config.base_url(required=False) or '(nothing)'}")
        return 0

    if url:
        machine = check(url)
        if not machine:
            print(f"Nothing that looks like a Meticulous answered at {url}",
                  file=sys.stderr)
            return 1
        found = [{"base_url": url.rstrip("/"), "machine": machine}]
    else:
        print("Looking for a Meticulous on your network...")
        print("(mDNS first, then a sweep of your subnet, which takes a few seconds)")
        found = (discover or discovery.discover)()

    if not found:
        print("\nNo machine found.", file=sys.stderr)
        print(f"If yours is awake, try: {invoked_as()} setup --url http://<its-address>",
              file=sys.stderr)
        return 1

    print(f"\nFound {len(found)}:\n")
    print(format_found(found))

    choice = found[0]
    if len(found) > 1:
        answer = ask("\nWhich one? [1] ").strip() or "1"
        try:
            index = int(answer) - 1
            if index < 0:
                raise IndexError(index)
            choice = found[index]
        except (ValueError, IndexError):
            print("Not one of the options; nothing saved.", file=sys.stderr)
            return 1

    config.save(base_url=choice["base_url"],
                machine_name=choice["machine"].get("name"),
                machine_serial=choice["machine"].get("serial"))
    print(f"\nSaved to {config.config_path()}")
    print(f"Your record lives in {config.data_home()}")
    print(f"\nNext: {invoked_as()} sync")
    return 0


def _verdict(taste):
    """Render a rating from any schema version.

    Old rows stay readable rather than being migrated: a v1 rating cannot be
    faithfully converted to v2, because v1's single axis genuinely cannot say
    whether a 0 meant "balanced" or "sour and bitter at once". Marking them
    v1 keeps them honest, and the mixed-version warning keeps them unpooled.
    """
    if not taste:
        return "unrated"
    if "sour_bitter" in taste:                        # schema 1
        return (f"v1 sb{taste['sour_bitter']:+d} body{taste['body']}"
                f" overall{taste['overall']}")
    return (f"sour{taste.get('sour')} bitter{taste.get('bitter')}"
            f" body{taste.get('body')} overall{taste.get('overall')}")


def _row_flags(row, known_bag_ids):
    """Stored flags plus the read-time one: a bag id no bag actually has."""
    flags = list(row.get("flags") or [])
    if row.get("bag") and row["bag"] not in known_bag_ids:
        flags.append(FLAG_UNKNOWN_BAG)
    return flags


def format_bags(bags, rows=(), today=None):
    """List registered bags, marking the one `rate` will default to.

    `today` is injectable so the output is testable; production passes None
    and gets the real date.
    """
    bags = list(bags)
    if not bags:
        return f"No bags registered yet. Add one with: {invoked_as()} bag"

    today = today or datetime.date.today().isoformat()

    counts = {}
    for row in rows:
        if row.get("bag"):
            counts[row["bag"]] = counts.get(row["bag"], 0) + 1

    # same rule as entry.current_bag_id: the most recently added bag wins
    current = bags[-1]["id"]

    lines = ["Bags (* = current, the default when rating)", ""]
    for bag in bags:
        mark = "*" if bag["id"] == current else " "
        label = " / ".join(x for x in (bag.get("roaster"), bag.get("name")) if x)
        age = days_off_roast(today, bag.get("roast_date"))
        age_text = f"  d+{age}" if age is not None else ""
        lines.append(f"{mark} {bag['id']}  {label}")
        lines.append(f"    roasted {bag.get('roast_date') or '?'}{age_text}"
                     f"  ·  {counts.get(bag['id'], 0)} shots")
    return "\n".join(lines)


def format_report(rows, bags=()):
    if not rows:
        return "No shots recorded yet."

    bags = list(bags)
    roast_dates = {b["id"]: b.get("roast_date") for b in bags}
    known_bag_ids = {b["id"] for b in bags}
    bag_labels = {b["id"]: (b.get("roaster") or b.get("name") or "") for b in bags}

    lines = []
    n = len(rows)
    unrated = sum(1 for r in rows if r.get("taste") is None)
    # unrated rows carry taste_schema None by design; only compare rated ones
    schemas = sorted({r["taste_schema"] for r in rows
                      if r.get("taste_schema") is not None})

    lines.append(f"Corpus: n={n}, {unrated} unrated  [{evidence_level(n)}]")
    if len(schemas) > 1:
        lines.append(f"  WARNING: mixed taste_schema versions {schemas}; not comparable")

    by_bag = {}
    for row in rows:
        by_bag.setdefault(row.get("bag") or "(no bag)", []).append(row)

    for bag, bag_rows in sorted(by_bag.items()):
        lines.append("")
        # the confidence label comes from the RATED count, not the row count:
        # a sample with no taste signal in it carries no confidence at all
        rated = sum(1 for r in bag_rows if r.get("taste") is not None)
        level = evidence_level(rated) if rated else "no taste signal"
        # name the bag, so the group header is not an opaque id
        label = bag_labels.get(bag, "")
        label_text = f"  {label[:44]}" if label else ""
        lines.append(f"{bag}  n={len(bag_rows)}, {rated} rated  [{level}]{label_text}")
        for r in sorted(bag_rows, key=lambda x: x.get("ts") or ""):
            taste = r.get("taste")
            verdict = _verdict(taste)
            # computed at read time, never stored (Amendment 1.4)
            age = days_off_roast(r.get("ts"), roast_dates.get(r.get("bag")))
            age_text = f"  d+{age}" if age is not None else ""
            flags = _row_flags(r, known_bag_ids)
            flag_text = f"  !{' !'.join(flags)}" if flags else ""
            lines.append(
                f"  {str(r.get('id') or '?')[:SHORT_ID]:8s}"
                f"  {(r.get('ts') or '')[:16]:16s}  {str(r.get('profile'))[:20]:22s}"
                f" grind={_dash(r.get('grind'))}  {_dash(r.get('yield_g'))}g"
                f"/{_dash(r.get('time_s'))}s  ratio={_dash(r.get('ratio'))}"
                f"{age_text}  {verdict}{flag_text}"
            )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog=invoked_as())
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    # not required: a bare invocation shows the banner rather than an error
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("sync")
    check_cmd = sub.add_parser("check")
    check_cmd.add_argument("shot_id")
    sub.add_parser("report")
    rate_cmd = sub.add_parser("rate")
    rate_cmd.add_argument("shot_id")
    sub.add_parser("bag", help="register a new bag")
    sub.add_parser("bags", help="list bags and show which is current")
    setup_cmd = sub.add_parser("setup", help="find your machine and remember it")
    setup_cmd.add_argument("--show", action="store_true",
                           help="print the current configuration and exit")
    setup_cmd.add_argument("--url", help="skip discovery and use this address")

    args = parser.parse_args(argv)

    if args.command is None:
        print(format_banner())
        return 0

    if args.command == "setup":
        return run_setup(show=args.show, url=args.url)

    # commands that talk to the machine must fail with a fixable sentence,
    # never a bare connection error
    if args.command == "sync" and config.base_url(required=False) is None:
        print(f"No machine configured. Run `{invoked_as()} setup` first.",
              file=sys.stderr)
        return 1

    store = Store(ROOT)

    if args.command == "sync":
        try:
            result = run_sync(MeticulousAPI(), store)
        except MachineUnreachable as exc:
            print(f"Machine unreachable, nothing written: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:      # noqa: BLE001 - a traceback is not a message
            print(f"Sync failed: {exc}", file=sys.stderr)
            return 1
        print(f"added {result['added']}, skipped {result['skipped']}")
        if result["unusable"]:
            print(f"{result['unusable']} history entries carried no id and could "
                  f"not be stored")
        if result["flagged"]:
            print(f"{len(result['flagged'])} shots stored with unreadable "
                  f"telemetry: "
                  + ", ".join(i[:SHORT_ID] for i in result["flagged"]))
        if result["vanished"]:
            # `vanished` is cumulative by design: it is every stored shot the
            # machine no longer has, not just the ones dropped this run. So it
            # is phrased as a standing fact, never as a fresh alarm. An alert
            # that fires identically forever teaches you to ignore it.
            print(f"{len(result['vanished'])} stored shots are no longer on the "
                  f"machine. History is a rolling window, so your local archive "
                  f"is their only copy. Keep syncing regularly.")
        if result["unrated"]:
            # WHICH rows await human fields, not just how many: without an id
            # on screen the only way to reach `rate` is to open shots.jsonl,
            # which is exactly the machine-numbers exposure Amendment 1.1 bans
            rows = {r["id"]: r for r in store.load_shots()}
            print(f"{len(result['unrated'])} shots awaiting taste ratings:")
            for shot_id in result["unrated"]:
                row = rows.get(shot_id, {})
                # profile and timestamp only: no machine output here either
                print(f"  {shot_id[:SHORT_ID]}  {(row.get('ts') or '')[:16]:16s}"
                      f"  {row.get('profile') or ''}")
            # take the name from the parser so this hint can never drift from
            # what `--help` prints
            print(f"  rate one with: {parser.prog} rate <id>")
        return 0

    if args.command == "check":
        try:
            shot_id = store.resolve_shot_id(args.shot_id)
        except KeyError:
            print(f"No stored shot matching {args.shot_id!r}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        path = store.telemetry_dir / f"{shot_id}.json"
        if not path.exists():
            print(f"No telemetry stored for {shot_id}", file=sys.stderr)
            return 1
        for r in check_shot(json.loads(path.read_text())):
            if not r["checkable"]:
                print(f"  {r['status']:32s} skipped: {r['reason']}")
                continue
            if r["status"] == SHOT_TOTAL:
                # whole-shot final weight: no stage, no duration, no per-sample
                # means, so it gets its own line shape (Amendment 1.6)
                target, actual = r["target_weight_g"], r["actual_weight_g"]
                deviation = (abs(actual - target)
                             if None not in (actual, target) else None)
                print(f"  {r['status']:32s} {'weight':8s}        "
                      f"target {_num(target, 6, 2)}g  actual {_num(actual, 6, 2)}g"
                      f"  dev {_num(deviation, 5, 2)}")
                continue
            flag = " (curve approximated)" if r["approximated"] else ""
            # a stage pinned against its own declared limit obeyed the profile;
            # the deviation below is real but expected, not a miss
            if r.get("limit_bound"):
                flag += f" [limit-bound: {r['limit_note']}]"
            print(f"  {r['status']:32s} {str(r['type'] or '-'):8s} "
                  f"{_num(r['duration_s'], 5, 1)}s  "
                  f"actual {_num(r['mean_actual'], 6, 2)}  "
                  f"intended {_num(r['mean_intended'], 6, 2)}"
                  f"  dev {_num(r['mean_abs_deviation'], 5, 2)}{flag}")
        return 0

    if args.command == "report":
        print(format_report(store.load_shots(), store.load_bags()))
        return 0

    if args.command == "bags":
        print(format_bags(store.load_bags(), store.load_shots()))
        return 0

    if args.command == "rate":
        try:
            shot_id = store.resolve_shot_id(args.shot_id)
        except KeyError:
            print(f"No stored shot matching {args.shot_id!r}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            rate_shot(store, shot_id)
        except (KeyError, TypeError, ValueError) as exc:
            # TypeError included so an unanticipated row shape reports a
            # message and an exit code, never a traceback at the user
            print(f"Not recorded: {exc}", file=sys.stderr)
            return 1
        print("Recorded.")
        return 0

    if args.command == "bag":
        try:
            bag = new_bag(store)
        except ValueError as exc:
            print(f"Not created: {exc}", file=sys.stderr)
            return 1
        print(f"Created {bag['id']}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
