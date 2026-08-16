"""Human data entry: ratings and bags.

THE RULE: rating prompts must never display the machine's output. Seeing
"21.0s" before scoring turns taste into an echo of the telemetry instead of
an independent signal, which is the one thing this record needs it to be.
Only the profile name and timestamp may be shown.
"""
import datetime
import re

from .model import (TASTE_SCHEMA, compute_ratio, days_off_roast, grinder_row,
                    most_recently_used_bag, validate_taste)
from .taste import parse_intensity, parse_lean, parse_verdict


def current_bag_id(store):
    """The bag to default to: the one most recently USED, else last registered.

    The rule lives in `model.most_recently_used_bag`, shared with
    `format.format_bags` so the "bags" listing's current marker can never say
    something different from what this actually resolves to.
    """
    return most_recently_used_bag(store.load_bags(), store.load_shots())


def _next_id(rows, prefix):
    """Next {prefix}NNN, derived from the highest existing numeric suffix.

    Using `len(rows) + 1` collides once any row is removed from the middle
    or end of the file (a plain human edit of a file this system
    round-trips): the count drops but old ids remain, so a new row can be
    assigned an id that already exists. Deriving from the max suffix instead
    means removing a row can only ever skip a number, never reuse one.
    Ids that don't match the {prefix}NNN shape are ignored rather than
    crashing.
    """
    highest = 0
    for row in rows:
        match = re.fullmatch(rf"{prefix}(\d+)", row.get("id", ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1:03d}"


def _next_bag_id(store):
    return _next_id(store.load_bags(), "b")


def previous_shot_same_day(store, row):
    """The most recent shot before this one on the same calendar day, or None.

    Rated or not: it was still tasted. This is the target of the `versus`
    preference, which is a human judgement and never machine evidence.
    """
    ts = row.get("ts") or ""
    if not ts:
        return None
    day = ts[:10]
    earlier = [r for r in store.load_shots()
               if r["id"] != row["id"]
               and (r.get("ts") or "").startswith(day)
               and (r.get("ts") or "") < ts]
    if not earlier:
        return None
    return max(earlier, key=lambda r: r.get("ts") or "")


def _menu(title, rows, label, default_id):
    """Numbered pick-list plus the prompt line, shared by bag and grinder.

    Returns (prompt, {typed_key: row_id}, default_key). One shape for both
    questions on purpose: they are asked back to back, and a rating flow that
    changed its answer conventions halfway down is a flow that gets answered
    wrong at 7am. `label` renders the part that differs per entity.
    """
    choices = {}
    lines = []
    default_index = None
    for index, row in enumerate(rows, 1):
        choices[str(index)] = row["id"]
        lines.append(f"    {index} {row['id']} {label(row)}".rstrip())
        if row["id"] == default_id:
            default_index = str(index)
    prompt = "\n".join([f"  {title}"] + lines + [f"  [{default_index or ''}]: "])
    return prompt, choices, default_index


def _pick(ask, prompt, choices, default_id):
    """Resolve one menu answer.

    Blank takes the default, a listed number takes that entry, and anything
    else is taken literally as an id so a known id can still be typed straight
    in. The literal case is always validated by the caller before it is stored.
    """
    answer = ask(prompt).strip()
    if not answer:
        return default_id
    return choices.get(answer, answer)


def _bag_menu(store, default_id):
    """Numbered bag list plus the prompt line.

    Returns (prompt, {typed_key: bag_id}, default_key). The menu exists because
    bag is never inferred: with several bags in rotation a guess attaches
    the wrong bean, and a wrong label is indistinguishable from a right one.
    """
    def label(bag):
        name = " / ".join(x for x in (bag.get("roaster"), bag.get("name")) if x)
        age = days_off_roast(datetime.date.today().isoformat(), bag.get("roast_date"))
        return f"{name} d+{age}" if age is not None else name
    return _menu("bag", store.load_bags(), label, default_id)


def _grinder_menu(store, default_id):
    """Numbered grinder list plus the prompt line.

    Returns (prompt, {typed_key: grinder_id}, default_key). The scale is shown
    because that is what makes two grind numbers incomparable, so it is the
    thing that tells you which machine a remembered setting belongs to.
    """
    def label(grinder):
        made = f"{grinder.get('make', '')} {grinder.get('model', '')}".strip()
        scale = (grinder.get("scale") or "").strip()
        return f"{made} · {scale}" if scale else made
    return _menu("grinder", store.load_grinders(), label, default_id)


def _recent_grinder_id(store):
    """The grinder on the most recently rated shot, else the first registered.

    Only `rate_shot` ever writes the field, so a row carrying one is a rated
    row. Membership is re-checked because grinders.jsonl is a file a human may
    edit: a default pointing at a removed grinder would render an empty
    bracket and resolve to nothing.
    """
    grinders = store.load_grinders()
    if not grinders:
        return None
    used = [r for r in store.load_shots() if r.get("grinder")]
    if used:
        recent = max(used, key=lambda r: r.get("ts") or "")["grinder"]
        if any(g["id"] == recent for g in grinders):
            return recent
    return grinders[0]["id"]


def rate_shot(store, shot_id, ask=None):
    """Prompt for the human fields of one shot and store them.

    Rating prompts must never show machine output. A call made blind is a
    prediction, and the caller grades it AFTER this function returns.
    Nothing here prints a machine-derived value, and nothing here computes
    the reveal.
    """
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
    print(f"{row.get('profile') or '(unknown profile)'} · {when}")
    print("Rate on the first two sips. Numbers come after.")

    default_bag = row.get("bag") or current_bag_id(store)
    prompt, choices, _ = _bag_menu(store, default_bag)
    bag = _pick(ask, prompt, choices, default_bag)
    if bag is not None and store.bag_by_id(bag) is None:
        # every human-supplied field is written through code that validates
        # it. A typo'd bag id would otherwise create a phantom one-shot group
        # in the report and silently null days_off_roast.
        raise ValueError(f"unknown bag {bag!r}; create it first with the bag command")

    # Asked only when more than one grinder is registered; with exactly one
    # it is implicit and costs no keystroke. Skipping the question outright
    # stored a null grind, dose and ratio while a usable dial sat in the log,
    # and a shot that was dialled but lost its setting is indistinguishable
    # afterwards from one that was never dialled — the wrong-bag-label trade
    # again.
    grinder = default_grinder_id(store)
    if grinder is None and store.load_grinders():
        default_grinder = _recent_grinder_id(store)
        prompt, choices, _ = _grinder_menu(store, default_grinder)
        grinder = _pick(ask, prompt, choices, default_grinder)
        if store.grinder_by_id(grinder) is None:
            raise ValueError(f"unknown grinder {grinder!r}; register it first "
                             f"with the grinder command")

    # grind and dose are read off the dial log rather than typed per shot:
    # they are properties of the setup, not of the cup, and asking again every
    # morning is three of the keystrokes that killed the previous version
    dial = resolve_dial(store, grinder, bag, row.get("ts")) if grinder else None
    grind = dial["grind"] if dial else row.get("grind")
    dose = dial["dose_g"] if dial else row.get("dose_g")

    # "balanced" is the word used everywhere this value is shown to a user
    # (this prompt, the report's _verdict, the reveal's _called) -- storage
    # keeps the literal "none"
    lean = parse_lean(ask("  lean    (s sour · b bitter · x both · - balanced): "))
    intensity = 0
    if lean != "none":
        intensity = parse_intensity(ask("  how far (1 slight · 2 clear · 3 badly): "))

    versus = None
    previous = previous_shot_same_day(store, row)
    if previous is not None:
        label = f"{(previous.get('ts') or '')[11:16]} {previous.get('profile') or ''}"
        verdict = parse_verdict(
            ask(f"  vs {label.strip()} (b better · w worse · = same): "))
        versus = {"shot": previous["id"], "verdict": verdict}

    taste = {"lean": lean, "intensity": intensity, "versus": versus}
    validate_taste(taste)          # raises before anything is written
    note = ask("  note (optional): ").strip()

    return store.update_shot(shot_id, {
        "bag": bag,
        "grinder": grinder,
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


def default_grinder_id(store):
    """The grinder to use without asking: the only one, or None.

    With several registered the question has to be asked, because guessing
    attaches a grind number to the wrong scale.
    """
    grinders = store.load_grinders()
    return grinders[0]["id"] if len(grinders) == 1 else None


def new_grinder(store, ask=None):
    """Prompt for a new grinder and store it."""
    ask = ask or input
    make = ask("make (e.g. 1Zpresso): ")
    model = ask("model (e.g. K-Ultra): ")
    scale = ask("scale (e.g. decimal dial 0.0-9.0): ")
    direction = ask("does FINER mean a lower or higher number? [lower]: ").strip()
    note = ask("note (optional): ")
    grinder = grinder_row(make, model, scale, direction or "lower", note)
    grinder["id"] = _next_id(store.load_grinders(), "g")
    if not store.append_grinder(grinder):
        raise ValueError(f"grinder id {grinder['id']!r} already exists, nothing saved")
    return grinder


def record_dial(store, grind, dose_g=None, bag=None, grinder=None, note="",
                now=None):
    """Record a re-dial. Grind and dose stand until the next one.

    `dose_g` carries forward from the last dial for the same (grinder, bag),
    then from the last dial anywhere, because a basket does not change when a
    collar does. The first dial ever must state it.
    """
    grinder = grinder or default_grinder_id(store)
    if grinder is None:
        raise ValueError(
            "no grinder selected; register one with the grinder command, "
            "or name it explicitly when more than one exists")
    if store.grinder_by_id(grinder) is None:
        raise ValueError(f"unknown grinder {grinder!r}")
    bag = bag or current_bag_id(store)
    if bag is None or store.bag_by_id(bag) is None:
        raise ValueError(f"unknown bag {bag!r}; create it first with the bag command")

    if dose_g is None:
        dials = store.load_dials()
        same = [d for d in dials
                if d.get("grinder") == grinder and d.get("bag") == bag]
        source = same or dials
        if not source:
            raise ValueError("dose is required for the first dial-in")
        dose_g = max(source, key=lambda d: d.get("ts") or "")["dose_g"]

    dial = {"ts": (now or datetime.datetime.now()).isoformat(timespec="seconds"),
            "grinder": grinder, "bag": bag,
            "grind": float(grind), "dose_g": float(dose_g), "note": note.strip()}
    store.append_dial(dial)
    return dial


def resolve_dial(store, grinder_id, bag_id, ts):
    """The dial-in in force for this (grinder, bag) at `ts`, or None.

    Strictly at-or-before: resolving to a later event would attribute a shot to
    a setting that did not exist when it was pulled.
    """
    if not ts:
        return None
    candidates = [d for d in store.load_dials()
                  if d.get("grinder") == grinder_id and d.get("bag") == bag_id
                  and (d.get("ts") or "") <= ts]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.get("ts") or "")
