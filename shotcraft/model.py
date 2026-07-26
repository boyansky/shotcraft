"""Shot and bag record construction, plus taste-scale validation.

TASTE_SCHEMA is versioned on purpose. Boyan had never rated a shot when this
scale was defined, so it is expected to be revised. Rows carrying different
schema versions must never be silently pooled by analysis.
"""
import datetime

from .derive import derive

TASTE_SCHEMA = 2

# Flags name a defect in a row rather than hiding it. A row whose telemetry
# could not be parsed must not render identically to a healthy one.
FLAG_TELEMETRY = "telemetry_unparsed"
FLAG_UNKNOWN_BAG = "unknown_bag"

# v2 (2026-07-26) split v1's single `sour_bitter` axis into two independent
# axes and widened everything to 0-10.
#
# WHY TWO AXES: a shot can be sour AND bitter at once. That is not a
# contradiction, it is the signature of uneven extraction — part of the puck
# under-extracted, part over-extracted, both arriving in the cup. v1 put those
# at opposite ends of one axis, so "both at once" landed on 0 and was recorded
# as *balanced*: the most diagnostically interesting shot, logged as the least.
#
# WHY 0-10: on v1's 1-5, real ratings piled onto 3 and 4 with nothing left to
# separate them. Integers only — a decimal you cannot reproduce blind is noise
# wearing a decimal point, and it defeats the anchors that stop a scale
# drifting over months.
TASTE_RANGES = {
    "sour":    (0, 10),   # 0 none · 3 slight brightness · 6 hollow · 10 puckering
    "bitter":  (0, 10),   # 0 none · 3 dry finish · 6 drying · 10 ashy
    "body":    (0, 10),   # 0 watery · 3 skim · 5 whole milk · 8 cream · 10 syrupy
    "overall": (0, 10),   # 0 poured it out · 5 fine · 7 would repeat · 10 best yet
}


def validate_taste(taste):
    """Raise ValueError unless taste is None or a complete, in-range rating."""
    if taste is None:
        return
    for key, (low, high) in TASTE_RANGES.items():
        if key not in taste:
            raise ValueError(f"taste missing required key {key!r}")
        value = taste[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"taste.{key} must be an int, got {value!r}")
        if not low <= value <= high:
            raise ValueError(f"taste.{key}={value} outside {low}..{high}")


def compute_ratio(yield_g, dose_g):
    if yield_g is None or not dose_g:
        return None
    return round(float(yield_g) / float(dose_g), 2)


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
