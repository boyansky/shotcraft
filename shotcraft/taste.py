"""Taste schema 3: a direction and a coarse magnitude, not four scales.

WHY THE CHANGE: v2 refined the instrument, splitting sour from bitter and
widening to 0-10, and in doing so made the task harder — four absolute
calibrated integers per shot. The record was then abandoned after four
ratings, whose `overall` values were 3 and 4 under v1 and 8 and 8 under v2 on
the same bag in the same week. That is not a palate that changed. It is a scale
that carries no meaning between sittings.

Absolute magnitude judgement is what an untrained palate is worst at. Relative
and directional judgement is what it is reliably good at, which is why sensory
work is built on comparison rather than solo scoring. Every question here is
one keystroke.

`both` is kept from v2, and for v2's reason: a shot can be sour and bitter at
once, which is what uneven extraction tastes like. Collapsing that onto one
axis records the most diagnostic shot as the least.

`body` and `overall` are gone. `overall` is precisely the unanswerable
question. `body` was probably being read correctly but moves no decision, and
anything that moves no decision does not earn a keystroke.
"""
TASTE_SCHEMA = 3

LEANS = ("sour", "bitter", "both", "none")
VERDICTS = ("better", "worse", "same")

LEAN_KEYS = {"s": "sour", "b": "bitter", "x": "both", "-": "none"}
VERDICT_KEYS = {"b": "better", "w": "worse", "=": "same"}

INTENSITY_RANGE = (0, 3)


def validate_taste(taste):
    """Raise ValueError unless taste is None or a complete, coherent schema-3 rating."""
    if taste is None:
        return

    lean = taste.get("lean")
    if lean not in LEANS:
        raise ValueError(f"taste.lean must be one of {LEANS}, got {lean!r}")

    intensity = taste.get("intensity")
    low, high = INTENSITY_RANGE
    if isinstance(intensity, bool) or not isinstance(intensity, int):
        raise ValueError(f"taste.intensity must be an int, got {intensity!r}")
    if not low <= intensity <= high:
        raise ValueError(f"taste.intensity={intensity} outside {low}..{high}")

    # the two halves have to agree or the row says two things at once: a lean
    # of "none" with an intensity is a shot that was both fine and not fine
    if (lean == "none") != (intensity == 0):
        raise ValueError(
            "taste.intensity must be 0 if and only if taste.lean is 'none'; "
            f"got lean={lean!r} intensity={intensity}")

    versus = taste.get("versus")
    if versus is None:
        return
    if not isinstance(versus, dict):
        raise ValueError(f"taste.versus must be a dict or None, got {versus!r}")
    if not (versus.get("shot") or "").strip():
        raise ValueError("taste.versus.shot is required when versus is present")
    if versus.get("verdict") not in VERDICTS:
        raise ValueError(
            f"taste.versus.verdict must be one of {VERDICTS}, "
            f"got {versus.get('verdict')!r}")


def parse_lean(text):
    key = (text or "").strip().lower()
    if key not in LEAN_KEYS:
        raise ValueError(
            f"lean: expected one of {'/'.join(LEAN_KEYS)}, got {text!r}")
    return LEAN_KEYS[key]


def parse_intensity(text):
    raw = (text or "").strip()
    low, high = INTENSITY_RANGE
    if not raw.isdigit() or not low <= int(raw) <= high:
        raise ValueError(f"how far: expected {low}-{high}, got {text!r}")
    return int(raw)


def parse_verdict(text):
    key = (text or "").strip().lower()
    if key not in VERDICT_KEYS:
        raise ValueError(
            f"versus: expected one of {'/'.join(VERDICT_KEYS)}, got {text!r}")
    return VERDICT_KEYS[key]
