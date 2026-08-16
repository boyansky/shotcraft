"""Layer 1.5: does the taste call agree with what the machine actually did?

Layer 1 asks whether the machine did what the profile asked. Layer 2 asks
whether asking that was right. This sits between them: it grades a blind taste
call against the shot's own execution.

WHY THE PROFILE IS THE REFERENCE: an earlier draft compared each shot against
a prior shot on the same bag and profile. With several bags in rotation
crossed against eight profiles that pair usually does not exist, so the tool
would fall silent exactly when experimentation is heaviest. The profile is
always there, so this works on the first shot of a new bag.

THE CLAIM, AND ITS LIMIT. Only this: a shot that finished short of its declared
final_weight put less water through the same coffee than intended, so it is
less extracted THAN THE PROFILE ASKED FOR, and should read sour. Overshoot
reads bitter. It is never claimed that a shot was over- or under-extracted in
absolute terms: a Turbo at ratio 3.0 in 20s and a Traditional Lever at ratio
2.0 in 40s do not lie on one scale, and pretending otherwise would teach a
false lesson faster than no tool at all.
"""
import datetime

# Starting hypothesis, to be revised once there is data — the same status the
# v1 taste scale carried, and for the same reason: nobody has measured it yet.
MIN_ABS_G = 2.0
MIN_FRAC = 0.05

# The schema whose SHAPE grade() understands: it reaches into taste["lean"],
# which is schema 3's shape specifically. Deliberately not the same name as
# taste.TASTE_SCHEMA, and not imported from there: TASTE_SCHEMA means "the
# version new ratings are stamped with today" and moves forward on its own
# schedule. If a future schema 4 changes the shape again, this module has to
# be updated on purpose — silently following TASTE_SCHEMA would let grade()
# misread schema-4 rows as if they were schema 3, which is exactly the
# pooling of incomparable rows this codebase refuses everywhere else.
GRADEABLE_SCHEMA = 3


def weight_outcome(entry):
    """Declared target, achieved weight, and the signed gap between them."""
    profile = entry.get("profile") or {}
    target = profile.get("final_weight")
    target = float(target) if isinstance(target, (int, float)) else None

    weights = [s["shot"]["weight"] for s in (entry.get("data") or [])
               if isinstance(s.get("shot"), dict) and "weight" in s["shot"]]
    actual = round(float(weights[-1]), 2) if weights else None

    deficit = None if (target is None or actual is None) else round(actual - target, 2)
    return {"target": target, "actual": actual, "deficit": deficit}


def threshold(target):
    """How far off counts as a miss. Floor and percentage, whichever is larger.

    A flat gram figure is too strict on a 90g Allonge and a flat percentage is
    too loose on a 20g ristretto, so both apply.
    """
    return max(MIN_ABS_G, MIN_FRAC * target)


def expected_lean(outcome):
    """Which way a miss of this shape should read, or None if it is not a miss."""
    deficit, target = outcome["deficit"], outcome["target"]
    if deficit is None or target is None:
        return None
    if abs(deficit) < threshold(target):
        return None
    return "sour" if deficit < 0 else "bitter"


def grade(entry, taste):
    """Compare a blind taste call against the shot's execution.

    Every not-gradeable case carries a reason. A tool that silently declines to
    speak is indistinguishable from one that has nothing to say.
    """
    outcome = weight_outcome(entry)
    base = {"gradeable": False, "reason": "", "expected": None,
            "agreed": None, "outcome": outcome}

    if not taste:
        base["reason"] = "not rated"
        return base
    if taste.get("lean") == "both":
        base["reason"] = ("called both sour and bitter, which is the signature of "
                          "uneven extraction rather than of extraction level")
        return base
    if outcome["target"] is None:
        base["reason"] = "this profile declares no final_weight to compare against"
        return base
    if outcome["actual"] is None:
        base["reason"] = "no weight samples in this shot's telemetry"
        return base

    expected = expected_lean(outcome)
    if expected is None:
        base["reason"] = ("the shot tracked its profile, so the machine has nothing "
                          "to say about how it tasted")
        return base

    base.update({"gradeable": True, "expected": expected,
                 "agreed": taste.get("lean") == expected})
    return base


def agreement_tally(rows, telemetry_loader):
    """(agreed, gradeable) across rated schema-3 rows.

    `telemetry_loader` takes a shot id and returns the stored entry dict, or
    None when the blob is missing. Rows under other schemas are excluded
    outright rather than coerced: they never carried a `lean` to grade.

    A flagged row (its own telemetry could not be parsed into a normal shot)
    is excluded too, whatever its raw blob happens to still contain: the row
    itself was already judged too unreliable to trust for the fields grade()
    would otherwise read, so drawing a calibration conclusion from its raw
    telemetry would be trusting the one thing that was already flagged as
    suspect.
    """
    agreed = gradeable = 0
    for row in rows:
        if (row.get("taste_schema") != GRADEABLE_SCHEMA or not row.get("taste")
                or row.get("flags")):
            continue
        entry = telemetry_loader(row["id"])
        if entry is None:
            continue
        result = grade(entry, row["taste"])
        if result["gradeable"]:
            gradeable += 1
            agreed += 1 if result["agreed"] else 0
    return agreed, gradeable


# A shot whose yield moved this far against a comparable sibling, on the same
# logged dial-in, is more likely a grinder that was moved without being logged
# than a puck that behaved differently. A third is deliberately coarse: this
# asks a question, it does not record a conclusion.
REDIAL_SUSPECT_FRACTION = 1 / 3


def matched_pair(rows, row):
    """The most recent prior shot on the same bag AND profile, or None.

    Both must match. A Turbo and a Traditional Lever on the same bean are not
    comparable on yield or time, which is the whole reason the profile is the
    primary reference and this is only ever a bonus.
    """
    bag, profile, ts = row.get("bag"), row.get("profile"), row.get("ts") or ""
    if not bag or not profile or not ts:
        return None
    earlier = [r for r in rows
               if r["id"] != row["id"]
               and r.get("bag") == bag and r.get("profile") == profile
               and (r.get("ts") or "") < ts]
    if not earlier:
        return None
    return max(earlier, key=lambda r: r.get("ts") or "")


def pair_note(row, previous):
    """Day gap, yield gap, and whether an unlogged re-dial is worth asking about.

    The day gap is printed because a pair eight days apart is comparing partly
    stale coffee, and saying so is cheaper than pretending it is not there.
    """
    days = None
    try:
        later = datetime.date.fromisoformat((row.get("ts") or "")[:10])
        older = datetime.date.fromisoformat((previous.get("ts") or "")[:10])
        days = (later - older).days
    except ValueError:
        days = None

    a, b = row.get("yield_g"), previous.get("yield_g")
    gap = None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b:
        gap = abs(a - b) / b

    # a logged grind change explains a yield change, so it is never suspect --
    # but BOTH grinds must actually be known for that to be a fact rather than
    # a coincidence of two missing values. `None == None` is True in Python,
    # so without that guard two shots with no dial-in logged at all (the real
    # state before any `dial` has ever been run) compare as "the same dial",
    # and a genuine yield swing gets blamed on a re-dial that was never logged
    # because none was ever logged.
    a_grind, b_grind = row.get("grind"), previous.get("grind")
    same_dial = a_grind is not None and b_grind is not None and a_grind == b_grind
    suspect = bool(gap is not None and same_dial and gap >= REDIAL_SUSPECT_FRACTION)
    return {"days": days, "yield_gap_frac": gap, "suspect_redial": suspect}
