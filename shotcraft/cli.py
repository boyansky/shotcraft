"""Command line entry point: sync, check, report."""
import argparse
import datetime
import json
import sys

from .api import MachineUnreachable, MeticulousAPI
from .blobs import BlobSource, SourceUnreadable
from .config import NotConfigured
from . import __version__
from . import config
from . import discover as discovery
from .check import SHOT_TOTAL, check_shot
from .entry import new_bag, new_grinder, rate_shot, record_dial
from .extract import agreement_tally, grade, matched_pair, pair_note
from .format import (COMMANDS, DESCRIPTION, LOGO, SHORT_ID, evidence_level,
                     format_bags, format_banner, format_found, format_grinders,
                     format_nudge, format_report, format_reveal, invoked_as,
                     _dash, _num, _row_flags, _verdict)
from .store import Store
from .sync import sync as run_sync

# the record lives outside the package (see config.data_home): installing
# shotcraft must never mean writing your shots into site-packages
ROOT = config.data_home()


def _telemetry_loader(store):
    """Read a stored telemetry blob by shot id, or None when unreadable."""
    def load(shot_id):
        path = store.telemetry_dir / f"{shot_id}.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None
    return load


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


def build_parser():
    """Construct the argparse tree.

    Split out from `main` so tests can introspect the real subparser choices
    rather than checking `COMMANDS` against a second, hand-maintained list --
    the gap that let `nudge` be registered here without a banner entry.
    """
    parser = argparse.ArgumentParser(prog=invoked_as())
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    # not required: a bare invocation shows the banner rather than an error
    sub = parser.add_subparsers(dest="command")
    sync_cmd = sub.add_parser("sync")
    sync_cmd.add_argument("--from", dest="from_dir", metavar="DIR",
                          help="ingest from a directory of {shot_id}.json blobs "
                               "instead of the machine")
    check_cmd = sub.add_parser("check")
    check_cmd.add_argument("shot_id")
    sub.add_parser("report")
    rate_cmd = sub.add_parser("rate")
    rate_cmd.add_argument("shot_id")
    sub.add_parser("bag", help="register a new bag")
    sub.add_parser("bags", help="list bags and show which is current")
    sub.add_parser("grinder", help="register a grinder")
    sub.add_parser("grinders", help="list registered grinders")
    dial_cmd = sub.add_parser("dial", help="record a re-dial for the current bag")
    dial_cmd.add_argument("grind", type=float)
    dial_cmd.add_argument("--dose", type=float, dest="dose")
    dial_cmd.add_argument("--bag")
    dial_cmd.add_argument("--grinder")
    setup_cmd = sub.add_parser("setup", help="find your machine and remember it")
    setup_cmd.add_argument("--show", action="store_true",
                           help="print the current configuration and exit")
    setup_cmd.add_argument("--url", help="skip discovery and use this address")
    nudge_cmd = sub.add_parser("nudge", help="one line for a shell prompt hook")
    nudge_cmd.add_argument("--force", action="store_true",
                           help="ignore the once-a-day stamp")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        print(format_banner())
        return 0

    if args.command == "setup":
        return run_setup(show=args.show, url=args.url)

    # commands that talk to the machine must fail with a fixable sentence,
    # never a bare connection error
    if (args.command == "sync" and not args.from_dir
            and config.base_url(required=False) is None):
        print(f"No machine configured. Run `{invoked_as()} setup` first.",
              file=sys.stderr)
        return 1

    store = Store(ROOT)

    if args.command == "sync":
        source = BlobSource(args.from_dir) if args.from_dir else MeticulousAPI()
        try:
            # the machine path stamps `now` (a live round trip just proved
            # freshness); `--from` stamps the newest entry actually read, so
            # a stale capture directory reads as stale rather than as "just
            # synced"
            result = run_sync(source, store, stamp_now=not args.from_dir)
        except (MachineUnreachable, SourceUnreadable) as exc:
            print(f"Nothing written: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:      # noqa: BLE001 - a traceback is not a message
            print(f"Sync failed: {exc}", file=sys.stderr)
            return 1
        for name in getattr(source, "skipped", []):
            print(f"skipped unreadable blob {name}", file=sys.stderr)
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
            # current sync's source no longer has, not just the ones dropped
            # this run. So it is phrased as a standing fact, never as a fresh
            # alarm. An alert that fires identically forever teaches you to
            # ignore it. Worded source-agnostically because this fires on
            # both the machine and `sync --from` paths: the machine's history
            # is a rolling window, and a blob directory can just as easily
            # have had files removed from it.
            print(f"{len(result['vanished'])} stored shots are no longer in "
                  f"what was just synced. Your local archive may be their "
                  f"only copy. Keep syncing regularly.")
        if result["unrated"]:
            # WHICH rows await human fields, not just how many: without an id
            # on screen the only way to reach `rate` is to open shots.jsonl,
            # which is exactly the kind of machine-numbers exposure the
            # rating flow exists to avoid
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
                # means, so it gets its own line shape
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
        loader = _telemetry_loader(store)
        print(format_report(store.load_shots(), store.load_bags(),
                            grinders=store.load_grinders(),
                            tally_for=lambda rows: agreement_tally(rows, loader)))
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
            row = rate_shot(store, shot_id)
        except (KeyError, TypeError, ValueError) as exc:
            # TypeError included so an unanticipated row shape reports a
            # message and an exit code, never a traceback at the user
            print(f"Not recorded: {exc}", file=sys.stderr)
            return 1
        print("Recorded.")
        # the reveal is computed only after the rating is durably stored, so
        # blindness is a property of this ordering rather than of discipline
        path = store.telemetry_dir / f"{shot_id}.json"
        if not path.exists():
            # named rather than returned quietly, on the same rule as the two
            # branches below: a shot with no telemetry blob at all must not
            # look identical to a quiet week
            print("Rating saved; telemetry missing, no reveal", file=sys.stderr)
            return 0
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            # named rather than returned quietly: a blob that will not parse is
            # indistinguishable from a shot the machine has nothing to say
            # about if the reveal simply never appears. extract.py states the
            # same rule, and the grader failure below already obeys it.
            print(f"Rating saved; telemetry unreadable, no reveal: {exc}",
                  file=sys.stderr)
            return 0
        try:
            if row.get("flags"):
                # this row's own telemetry already failed to parse into a
                # normal shot once (sync.py flagged it); trusting its raw
                # blob for a calibration verdict now would be trusting the
                # exact thing that was already judged unreliable
                result = {"gradeable": False,
                          "reason": ("this shot's telemetry is flagged: "
                                    + "; ".join(row["flags"])),
                          "expected": None, "agreed": None,
                          "outcome": {"target": None, "actual": None, "deficit": None}}
            else:
                result = grade(entry, row.get("taste"))
            rows = store.load_shots()
            previous = matched_pair(rows, row)
            note = pair_note(row, previous) if previous is not None else None
            loader = _telemetry_loader(store)
            tally = agreement_tally(rows, loader)
            print(format_reveal(row, result, tally, previous, note))
        except Exception as exc:      # noqa: BLE001 - see below
            # The rating is already on disk and is the deliverable; the reveal
            # is a bonus. So a defect anywhere in the grader costs the reveal
            # and nothing else — not the rating, not the exit code, and not a
            # traceback at someone holding a coffee.
            print(f"Rating saved; no reveal: {exc}", file=sys.stderr)
        return 0

    if args.command == "bag":
        try:
            bag = new_bag(store)
        except ValueError as exc:
            print(f"Not created: {exc}", file=sys.stderr)
            return 1
        print(f"Created {bag['id']}")
        return 0

    if args.command == "grinders":
        print(format_grinders(store.load_grinders()))
        return 0

    if args.command == "grinder":
        try:
            grinder = new_grinder(store)
        except ValueError as exc:
            print(f"Not created: {exc}", file=sys.stderr)
            return 1
        print(f"Created {grinder['id']}")
        return 0

    if args.command == "dial":
        try:
            dial = record_dial(store, args.grind, dose_g=args.dose,
                               bag=args.bag, grinder=args.grinder)
        except ValueError as exc:
            print(f"Not recorded: {exc}", file=sys.stderr)
            return 1
        bag = store.bag_by_id(dial["bag"]) or {}
        label = " / ".join(x for x in (bag.get("roaster"), bag.get("name")) if x)
        print(f"{dial['bag']} {label}  grind {dial['grind']:g}  "
              f"dose {dial['dose_g']:g}g  on {dial['grinder']}")
        return 0

    if args.command == "nudge":
        # never raises, never blocks a shell prompt, never opens a socket
        try:
            stamp = ROOT / "last_nudge"
            today = datetime.date.today().isoformat()
            if not args.force and stamp.exists() and stamp.read_text().strip() == today:
                return 0
            rows = store.load_shots()
            unrated = sum(1 for r in rows if r.get("taste") is None)
            line = format_nudge(unrated, store.sync_age_days())
            if line:
                print(line)
                if not args.force:
                    ROOT.mkdir(parents=True, exist_ok=True)
                    stamp.write_text(today)
        except Exception:      # noqa: BLE001 - a prompt hook must never fail loudly
            pass
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
