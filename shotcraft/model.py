"""Shot and bag record construction, plus taste-scale validation.

TASTE_SCHEMA is versioned on purpose: nobody had rated a single shot when
this scale was first defined, so it is expected to be revised. Rows carrying
different schema versions must never be silently pooled by analysis.
"""
import datetime

from .derive import derive

# Schema and validation live in taste.py as of schema 3. Re-exported here
# because model is where every caller already imports them from, and a rename
# would churn call sites for no gain.
from .taste import TASTE_SCHEMA, validate_taste  # noqa: F401

# Flags name a defect in a row rather than hiding it. A row whose telemetry
# could not be parsed must not render identically to a healthy one.
FLAG_TELEMETRY = "telemetry_unparsed"
FLAG_UNKNOWN_BAG = "unknown_bag"


def compute_ratio(yield_g, dose_g):
    if yield_g is None or not dose_g:
        return None
    return round(float(yield_g) / float(dose_g), 2)


def most_recently_used_bag(bags, rows):
    """The bag most recently used by a shot, else the most recently registered.

    "Most recently registered" is almost never the bag in the grinder once
    several are in rotation, and defaulting to the wrong bean is worse than
    defaulting to none — a wrong label is indistinguishable from a right one
    afterwards. Falls back to registration order only when nothing has used
    a bag yet, because there is nothing used to point at.

    Shared by `entry.current_bag_id` (which resolves it from a `Store`) and
    `format.format_bags` (which marks it in the bag listing), so the two
    never state two different answers to the same question. Takes plain
    lists rather than a `Store` so it stays a pure function usable from
    `format.py` without pulling persistence into rendering.
    """
    used = [r for r in rows if r.get("bag")]
    if used:
        return max(used, key=lambda r: r.get("ts") or "")["bag"]
    bags = list(bags)
    return bags[-1]["id"] if bags else None


def days_off_roast(shot_ts, roast_date):
    # a flagged row can carry a null ts; an unanswerable question returns None
    # rather than raising in the middle of a report
    if not roast_date or not shot_ts:
        return None
    shot_day = datetime.datetime.fromisoformat(shot_ts).date()
    roast_day = datetime.date.fromisoformat(roast_date)
    return (shot_day - roast_day).days


def shot_row(entry, bag=None, dose_g=None, grind=None, taste=None, note=""):
    """Build a thin shot row. Human fields default to None (unrated).

    `days_off_roast` is deliberately NOT stored: it depends on the bag, which
    is assigned after sync, so freezing it here would leave it permanently
    null. It is computed at read time instead.

    `taste_schema` is stamped only when a rating is actually present, so an
    unrated row cannot later claim it was rated under an older scale.
    """
    validate_taste(taste)
    derived = derive(entry)
    # entry["time"] is a float unix timestamp in SECONDS (not milliseconds,
    # unlike the telemetry sample fields). Do not divide it.
    ts = datetime.datetime.fromtimestamp(entry["time"]).isoformat(timespec="seconds")
    return {
        "id": entry["id"],
        "ts": ts,
        "bag": bag,
        "profile": (entry.get("profile") or {}).get("name", entry.get("name", "")).strip(),
        "dose_g": dose_g,
        "grind": grind,
        "yield_g": derived["yield_g"],
        "time_s": derived["time_s"],
        "ratio": compute_ratio(derived["yield_g"], dose_g),
        "peak_pressure": derived["peak_pressure"],
        "peak_flow": derived["peak_flow"],
        "taste": taste,
        "taste_schema": TASTE_SCHEMA if taste is not None else None,
        "note": note,
        "flags": [],
    }


def flagged_row(entry, reason):
    """A thin row for an entry whose telemetry could not be parsed.

    The raw blob is already on disk by the time this is called; the row exists
    so the id still reaches shots.jsonl. Without it, sync would rebuild the
    same broken row on every run and re-raise forever, and the healthy entries
    that shared the batch would be lost with it.

    Machine fields are null, whatever is readable is kept, and the row carries
    a flag naming the failure so it can never be mistaken for a healthy shot.
    """
    entry = entry or {}
    profile = entry.get("profile") or {}
    name = profile.get("name") or entry.get("name") or ""
    return {
        "id": entry.get("id"),
        "ts": _safe_ts(entry.get("time")),
        "bag": None,
        "profile": name.strip() if isinstance(name, str) else "",
        "dose_g": None,
        "grind": None,
        "yield_g": None,
        "time_s": None,
        "ratio": None,
        "peak_pressure": None,
        "peak_flow": None,
        "taste": None,
        "taste_schema": None,
        "note": "",
        "flags": [f"{FLAG_TELEMETRY}: {reason}"],
    }


def _safe_ts(value):
    """Best-effort timestamp. Returns None instead of raising."""
    try:
        return datetime.datetime.fromtimestamp(value).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


# A grind reading is meaningless without the device that produced it: swap
# grinders and every historical number silently becomes incomparable, which is
# the failure taste_schema versioning exists to prevent, left unguarded one
# field over. So the grinder is an entity and grind is never pooled across ids.
FINER_DIRECTIONS = ("lower", "higher")


def validate_grinder(grinder):
    """Raise ValueError unless the grinder is complete and usable."""
    for key in ("make", "model", "scale"):
        if not (grinder.get(key) or "").strip():
            raise ValueError(f"grinder {key} is required")
    direction = grinder.get("finer_direction")
    if direction not in FINER_DIRECTIONS:
        raise ValueError(
            f"finer_direction must be one of {FINER_DIRECTIONS}, got {direction!r}")


def grinder_row(make, model, scale, finer_direction, note=""):
    """Build a grinder row. `id` is assigned by the caller.

    `finer_direction` is load-bearing, not description: advice of the form
    "grind finer" cannot be rendered without knowing which way the dial runs.
    """
    row = {"id": None, "make": make.strip(), "model": model.strip(),
           "scale": scale.strip(), "finer_direction": finer_direction,
           "note": note.strip()}
    validate_grinder(row)
    return row
