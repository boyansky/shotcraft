"""Human data entry: ratings and bags.

THE RULE (spec Amendment 1.1): rating prompts must never display the machine's
output. Seeing "21.0s" before scoring turns taste into an echo of the telemetry
instead of an independent signal, which is the one thing this record needs it
to be. Only the profile name and timestamp may be shown.
"""
import datetime
import re

from .model import TASTE_SCHEMA, compute_ratio, validate_taste


def current_bag_id(store):
    """The most recently added bag, or None."""
    bags = store.load_bags()
    return bags[-1]["id"] if bags else None


def default_dose(store):
    """The most recent non-null dose, by timestamp, or None.

    File order is not chronological order: sync appends shots in whatever
    order the machine's history endpoint returns them (newest-first, in
    practice), so picking the last row in the file picks the oldest dose in
    a multi-shot batch. Sort by `ts` instead.
    """
    rows = [r for r in store.load_shots() if r.get("dose_g") is not None]
    if not rows:
        return None
    # a rated flagged row has a dose but a null ts; sorting None against a
    # string raises, so an unknown time sorts oldest rather than exploding
    return max(rows, key=lambda r: r.get("ts") or "")["dose_g"]


def _next_bag_id(store):
    """Next bNNN id, derived from the highest existing numeric suffix.

    Using `len(bags) + 1` collides once any bag is removed from the middle
    or end of bags.jsonl (a plain human edit of a file this system
    round-trips): the count drops but old ids remain, so a new bag can be
    assigned an id that already exists. Deriving from the max suffix instead
    means removing a bag can only ever skip a number, never reuse one.
    Ids that don't match the bNNN shape are ignored rather than crashing.
    """
    highest = 0
    for bag in store.load_bags():
        match = re.fullmatch(r"b(\d+)", bag.get("id", ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"b{highest + 1:03d}"


def _number(text, field, cast):
    try:
        return cast(text.strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field}: expected a number, got {text!r}") from None


def rate_shot(store, shot_id, ask=None):
    """Prompt for the human fields of one shot and store them."""
    ask = ask or input          # resolved per call so tests can drive stdin
    rows = [r for r in store.load_shots() if r["id"] == shot_id]
    if not rows:
        raise KeyError(f"no stored shot with id {shot_id!r}")
    row = rows[0]

    # Deliberately minimal: profile and time only. No machine output.
    # A flagged row (unparseable telemetry) can carry a null ts and an empty
    # profile name, so neither is indexed or trusted to be printable. The
    # stand-in text is descriptive, never machine-derived.
    when = (row.get("ts") or "")[:16] or "(time unknown)"
    print(f"Rating {row.get('profile') or '(unknown profile)'} from {when}")
    print("Rate on the first two sips. Do not look at the numbers first.")

    bag_default = row.get("bag") or current_bag_id(store)
    dose_default = row.get("dose_g") or default_dose(store)

    bag = ask(f"bag [{bag_default}]: ").strip() or bag_default
    bag = bag or None                # unassigned is legitimate
    if bag is not None and store.bag_by_id(bag) is None:
        # every human-supplied field is written through code that validates
        # it (Amendment 1.2). A typo'd bag id would otherwise create a phantom
        # one-shot group in the report and silently null days_off_roast.
        raise ValueError(f"unknown bag {bag!r}; create it first with the bag command")
    dose_raw = ask(f"dose g [{dose_default}]: ").strip()
    dose = _number(dose_raw, "dose", float) if dose_raw else dose_default
    grind = _number(ask("grind: "), "grind", float)

    # sour and bitter are asked separately on purpose: a shot can be both at
    # once, which is what uneven extraction tastes like. Collapsing them onto
    # one axis records that shot as "balanced" (schema v1's defect).
    taste = {
        "sour": _number(ask("sour 0-10   (0 none, 6 hollow, 10 puckering): "),
                        "sour", int),
        "bitter": _number(ask("bitter 0-10 (0 none, 6 drying, 10 ashy): "),
                          "bitter", int),
        "body": _number(ask("body 0-10   (3 skim, 5 whole milk, 10 syrupy): "),
                        "body", int),
        "overall": _number(ask("overall 0-10 (5 fine, 7 would repeat, 10 best yet): "),
                           "overall", int),
    }
    validate_taste(taste)          # raises before anything is written
    note = ask("note (optional): ").strip()

    return store.update_shot(shot_id, {
        "bag": bag,
        "dose_g": dose,
        "grind": grind,
        "ratio": compute_ratio(row.get("yield_g"), dose),
        "taste": taste,
        "taste_schema": TASTE_SCHEMA,
        "note": note,
    })


def new_bag(store, ask=None):
    """Prompt for a new bag and store it."""
    ask = ask or input          # resolved per call so tests can drive stdin
    roaster = ask("roaster: ").strip()
    name = ask("name/origin: ").strip()
    process = ask("process: ").strip()
    roast_date = ask("roast date YYYY-MM-DD: ").strip()
    try:
        datetime.date.fromisoformat(roast_date)
    except ValueError:
        raise ValueError(f"roast date: expected YYYY-MM-DD, got {roast_date!r}") from None
    note = ask("note (optional): ").strip()

    bag = {
        "id": _next_bag_id(store),
        "roaster": roaster,
        "name": name,
        "process": process,
        "roast_date": roast_date,
        "opened": datetime.date.today().isoformat(),
        "note": note,
    }
    if not store.append_bag(bag):
        raise ValueError(f"bag id {bag['id']!r} already exists, nothing saved")
    return bag
