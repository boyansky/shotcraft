"""Rendering. Every function here is pure: rows in, string out.

Split from cli.py, which had grown to carry dispatch, setup, the mark and
every formatter at once. Keeping rendering pure is what lets the report be
tested without a filesystem or a machine.
"""
import datetime
import math
import pathlib
import sys

from .model import FLAG_UNKNOWN_BAG, days_off_roast, most_recently_used_bag
from . import __version__

SHORT_ID = 8   # uuid prefix printed everywhere and accepted back by rate/check

# Below this, a "leans sour" line would be one or two shots dressed up as a
# tendency. The evidence ladder still governs what the number is called.
ADVICE_MIN_RATINGS = 5


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
    # dose and grind are no longer typed here (they come off the dial log), and
    # the blind call now buys a reveal, so the line says what it costs and pays
    ("rate <shot_id>", "taste it blind, then see what the machine did"),
    ("report", "the corpus, grouped by bag, with sample sizes"),
    ("check <shot_id>", "did the shot track the profile it was given?"),
    ("bag", "register a new bag of beans"),
    ("bags", "list bags, marking the current one"),
    ("grinder", "register a grinder"),
    ("grinders", "list registered grinders"),
    ("dial <value>", "record a re-dial: grind and dose; --profile scopes it"),
    ("nudge", "one line for a shell prompt hook, else silent"),
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


def _verdict(taste):
    """Render a rating from any schema version.

    Old rows stay readable rather than being migrated: v1's single axis
    genuinely cannot say whether a 0 meant "balanced" or "sour and bitter at
    once", and v2's four absolute scales cannot be converted into schema 3's
    direction without inventing the direction. Marking them keeps them honest
    and the mixed-version warning keeps them unpooled.

    v1 fields are read with `.get()`, same as v2 below: a partial row (any
    dict a human could have hand-edited) must render with dashes, not raise.
    """
    if not taste:
        return "unrated"
    if "sour_bitter" in taste:                        # schema 1
        sb = taste.get("sour_bitter")
        sb_text = f"{sb:+d}" if isinstance(sb, int) and not isinstance(sb, bool) \
            else _dash(sb)
        return (f"v1 sb{sb_text} body{_dash(taste.get('body'))}"
                f" overall{_dash(taste.get('overall'))}")
    if "lean" in taste:                               # schema 3
        # `none` renders as "balanced", the same word `_called` uses in the
        # reveal -- one taste value must not read as two different words
        # depending on which screen printed it
        lean = "balanced" if taste["lean"] == "none" else taste["lean"]
        text = f"{lean}/{taste['intensity']}"
        versus = taste.get("versus")
        return f"{text} vs {versus['verdict']}" if versus else text
    return (f"v2 sour{taste.get('sour')} bitter{taste.get('bitter')}"
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

    # shared with entry.current_bag_id via model.most_recently_used_bag, so
    # this marker and what `rate`/`dial` actually default to can never drift
    current = most_recently_used_bag(bags, rows)

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


def _grind_hint(direction, grinder):
    """The advice line's dial instruction: concrete when the grinder and its
    `finer_direction` are known, direction-agnostic otherwise.

    A grind number means nothing without the scale it was read off, and
    "finer" is not itself a number -- it is only actionable once you know
    which way THIS dial's numbers run. `grinder_row`'s own docstring states
    the same rule for why `finer_direction` is captured at all. Falls back to
    naming the physical target without a dial direction when no single
    grinder is known: honest about the limit rather than guessing a number.
    """
    physical = "finer" if direction == "sour" else "coarser"
    contact = "longer" if direction == "sour" else "shorter"
    finer_direction = grinder.get("finer_direction") if grinder else None
    if finer_direction not in ("lower", "higher"):
        return f"{physical} grind or {contact} contact"
    if direction == "sour":
        number = finer_direction
    else:
        number = "higher" if finer_direction == "lower" else "lower"
    return f"a {physical} grind (a {number} number on {grinder['id']})"


def format_report(rows, bags=(), grinders=(), tally_for=None):
    if not rows:
        return "No shots recorded yet."

    bags = list(bags)
    roast_dates = {b["id"]: b.get("roast_date") for b in bags}
    known_bag_ids = {b["id"] for b in bags}
    bag_labels = {b["id"]: (b.get("roaster") or b.get("name") or "") for b in bags}
    grinders_by_id = {g["id"]: g for g in grinders}

    lines = []
    n = len(rows)
    unrated = sum(1 for r in rows if r.get("taste") is None)
    # unrated rows carry taste_schema None by design; only compare rated ones
    schemas = sorted({r["taste_schema"] for r in rows
                      if r.get("taste_schema") is not None})

    # same rule as the per-bag confidence label below: a corpus that is
    # mostly unrated must not borrow the row count's confidence. n=69 with
    # 65 unrated is 4 actual ratings, not enough to call "hypothesis".
    rated_total = n - unrated
    corpus_level = evidence_level(rated_total) if rated_total else "no taste signal"
    lines.append(f"Corpus: n={n}, {unrated} unrated  [{corpus_level}]")
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
        v3 = [r for r in bag_rows
              if r.get("taste_schema") == 3 and r.get("taste")]
        # A pooled tier here is fine on its own, but a schema-3-specific claim
        # (the leans/agreement lines below) is about to sit right underneath
        # it. If that claim is backed by fewer ratings than the pooled count
        # (this bag also carries older-schema ratings), showing only the
        # pooled tier would let a reader carry its higher confidence onto the
        # narrower claim below -- pooling the exact rows the mixed-schema
        # warning says are not comparable. So split the label only when that
        # risk is real: v3 is a strict subset AND large enough to trigger the
        # advice block. An unmixed bag (all one schema, or too few v3 ratings
        # for advice to print) renders exactly as before.
        if v3 and len(v3) < rated and len(v3) >= ADVICE_MIN_RATINGS:
            level = (f"{evidence_level(len(v3))} for the {len(v3)} schema-3 "
                     f"ratings; {evidence_level(rated)} pooled across all {rated}")
        else:
            level = evidence_level(rated) if rated else "no taste signal"
        # name the bag, so the group header is not an opaque id
        label = bag_labels.get(bag, "")
        label_text = f"  {label[:44]}" if label else ""
        lines.append(f"{bag}  n={len(bag_rows)}, {rated} rated  [{level}]{label_text}")

        # grind is never pooled across grinders, on the same rule as taste
        # schemas: the same number means different things on different
        # scales. A row that carries a grind value but no `grinder` id at all
        # (a legacy rating, from before the grinder concept existed) is its
        # own bucket rather than dropped from the set entirely -- "unknown"
        # is no more comparable to g001 than g002 is, and silently excluding
        # it let those rows pool into a single-grinder-looking column with no
        # warning.
        NO_GRINDER = "(no grinder logged)"
        grind_sources = sorted({r.get("grinder") or NO_GRINDER
                                for r in bag_rows if r.get("grind") is not None})
        if len(grind_sources) > 1:
            lines.append(f"  WARNING: mixed grinder sources {grind_sources}; "
                         f"grind values not comparable")

        if len(v3) >= ADVICE_MIN_RATINGS:
            leans = [r["taste"]["lean"] for r in v3]
            # the advice line only ever names a single grinder's dial, so it
            # only speaks concretely when v3's rows agree on exactly one
            v3_grinder_ids = {r.get("grinder") for r in v3 if r.get("grinder")}
            advice_grinder = (grinders_by_id.get(next(iter(v3_grinder_ids)))
                              if len(v3_grinder_ids) == 1 else None)
            for direction in ("sour", "bitter"):
                count = leans.count(direction)
                if count > len(v3) / 2:
                    lines.append(f"  leans {direction} in {count} of {len(v3)}"
                                 f"  ·  suggests {_grind_hint(direction, advice_grinder)}")
            if tally_for is not None:
                # this block only renders once there are enough ratings to
                # tempt a conclusion, so whether they can be checked against
                # the machine is worth stating either way -- printed whether
                # or not a lean happened to reach a majority, never gated on
                # the leans line above it (a tool that stays quiet when it
                # has something load-bearing to say is indistinguishable
                # from one with nothing to say; see extract.py)
                agreed, gradeable = tally_for(v3)
                if gradeable:
                    lines.append(f"  your calls agreed with the machine in "
                                 f"{agreed} of {gradeable}, this bag only")
                else:
                    lines.append(f"  none of these schema-3 calls could be "
                                 f"checked against the machine: unchecked, "
                                 f"not disagreement")

        for r in sorted(bag_rows, key=lambda x: x.get("ts") or ""):
            taste = r.get("taste")
            verdict = _verdict(taste)
            # computed at read time, never stored
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


def _called(lean):
    """The operator's own call, as it reads inside a sentence.

    `none` is the balanced call — a real answer, not the absence of one — and
    `grade` filters out only `both`, so a balanced call on a shot that missed
    its target reaches the reveal routinely. Rendering the stored enum straight
    out produced "You called it none".
    """
    return {"sour": "sour", "bitter": "bitter", "none": "balanced",
            "both": "both sour and bitter"}.get(lean, "unrated")


def format_reveal(row, result, tally, previous=None, note=None):
    """What the machine saw, printed only after the rating is on disk.

    Leads with the confound when there is one: a shot that missed its profile
    badly explains its own taste, and learning from it as if it were clean
    teaches the wrong lesson.
    """
    outcome = result["outcome"]
    lines = ["  " + "─" * 55]

    if outcome["target"] is not None and outcome["actual"] is not None:
        lines.append(f"  Asked for {outcome['target']:g}g. Got "
                     f"{outcome['actual']:g}g in {_dash(row.get('time_s'))}s"
                     f"  ·  ratio {_dash(row.get('ratio'))}")
    else:
        lines.append(f"  {_dash(row.get('yield_g'))}g in "
                     f"{_dash(row.get('time_s'))}s"
                     f"  ·  ratio {_dash(row.get('ratio'))}")

    if not result["gradeable"]:
        lines.append(f"  No call to grade: {result['reason']}.")
    else:
        called = _called((row.get("taste") or {}).get("lean"))
        direction = ("short of" if result["expected"] == "sour" else "past")
        lines.append(f"  That is {abs(outcome['deficit']):g}g {direction} the "
                     f"target, so it extracted "
                     f"{'less' if result['expected'] == 'sour' else 'more'} "
                     f"than the profile asked for.")
        if result["agreed"]:
            lines.append(f"  You called it {called}. Your call and the machine "
                         f"point the same way.")
        else:
            lines.append(f"  You called it {called}; the machine points "
                         f"{result['expected']}. Worth a second taste.")

    if previous is not None and note is not None:
        when = (previous.get("ts") or "")[:16]
        gap = f", {note['days']}d apart" if note["days"] else ""
        lines.append(f"  Last {previous.get('profile')} on this bag "
                     f"({when}{gap}): {_dash(previous.get('yield_g'))}g in "
                     f"{_dash(previous.get('time_s'))}s")
        if note["days"] and note["days"] >= 5:
            lines.append("  The bean is meaningfully older than it was then, "
                         "so read that comparison loosely.")
        if note["suspect_redial"]:
            lines.append("  Yield moved a lot on the same logged dial-in. "
                         "Did you re-dial without recording it? "
                         f"({invoked_as()} dial <value>)")

    agreed, gradeable = tally
    if gradeable:
        # whole-record, not this bag: `tally` is computed in cli.py from
        # every stored shot, unlike format_report's per-bag agreement line,
        # which only ever sees one bag's rows -- naming the scope here so the
        # two numbers are never mistaken for the same denominator
        lines.append(f"  Calls matching the machine across your whole record: "
                     f"{agreed} of {gradeable}.")
    else:
        # "0 of 0" reads as a damning ratio the tool cannot actually back up;
        # this record genuinely has no checkable calls yet, on the first shot
        # ever rated and equally on the fiftieth ungradeable one -- the tool
        # cannot tell those apart, so it never implies which case it is,
        # matching the calm "unchecked, not disagreement" register format_report
        # uses for the identical situation (see its ADVICE_MIN_RATINGS branch).
        lines.append("  Nothing here could be checked against the machine: "
                     "unchecked, not disagreement.")
    return "\n".join(lines)


# A prompt line has one line to work with. Anything longer gets ignored by
# week two, which is the failure mode this whole mechanism exists to fix.
STALE_SYNC_DAYS = 1.0


def format_nudge(unrated_count, sync_age_days):
    """One line, or nothing at all.

    Stale sync is surfaced even when nothing is unrated: on a host where the
    Local Network permission can be revoked without warning, a silent sync
    failure is indistinguishable from a quiet week, and history is a rolling
    window of 20.

    `sync_age_days` is `float("inf")` when no sync has ever been stamped --
    the default state of every fresh install, not an edge case. `inf` is the
    correct internal sentinel (see Store.sync_age_days), but formatted with
    `.0f` it renders as the literal string "inf", so the never-synced case is
    special-cased here rather than left to reach the format spec.
    """
    parts = []
    if unrated_count:
        noun = "shot" if unrated_count == 1 else "shots"
        parts.append(f"{unrated_count} {noun} unrated")
    if sync_age_days >= STALE_SYNC_DAYS:
        age = "never" if math.isinf(sync_age_days) else f"{sync_age_days:.0f}d ago"
        parts.append(f"last sync {age}")
    if not parts:
        return ""
    # the hint names the fix for "unrated", so it only belongs on the line
    # when that is one of the things being said -- a stale-sync-only line has
    # nothing for `rate <id>` to fix
    hint = f"  ({invoked_as()} rate <id>)" if unrated_count else ""
    return f"shotcraft: {' · '.join(parts)}{hint}"


def format_grinders(grinders):
    grinders = list(grinders)
    if not grinders:
        return f"No grinders registered yet. Add one with: {invoked_as()} grinder"
    lines = ["Grinders", ""]
    for g in grinders:
        lines.append(f"  {g['id']}  {g.get('make', '')} {g.get('model', '')}".rstrip())
        lines.append(f"      {g.get('scale', '')}  ·  finer = {g.get('finer_direction')}")
    return "\n".join(lines)
