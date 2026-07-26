"""Layer 1: did the machine do what the profile asked?

Machine-only, no taste required, meaningful from the first shot. For each
telemetry run matched to a profile stage, reconstruct the intended value at
each sample and compare it to what was measured.

Honesty notes baked in:
  * "power" stages have no shot.* channel, so they are reported not checkable.
  * "curve" interpolation is approximated linearly, flagged via approximated.
"""
from .profile_math import interpolate, resolve, resolve_points
from .stages import join_to_profile, segment

CHANNELS = {"pressure": "pressure", "flow": "flow"}

# the whole-shot final-weight row, which is not a profile stage and carries no
# duration or per-sample means. Named so printers can branch on it.
SHOT_TOTAL = "(shot total)"

# A stage may declare a limit that legitimately stops it reaching its own
# dynamics target: "flow 11.3 ml/s, capped at 6.5 bar" is obeyed, not missed,
# when pressure pins at the cap and flow falls short. Scoring that as a
# deviation is a false positive, so we detect it and say so. We report the
# constraint alongside the raw deviation rather than suppressing the number.
LIMIT_TOLERANCE = 0.98      # within 2% of the cap counts as sitting on it
LIMIT_BOUND_FRACTION = 0.5  # at least half the stage's samples pinned there


def _mean(values):
    return round(sum(values) / len(values), 3) if values else None


def _resolve_limits(stage, variables):
    """Resolve a stage's declared limits. Unresolvable ones are dropped."""
    resolved = []
    for limit in stage.get("limits") or []:
        if not isinstance(limit, dict):
            continue
        try:
            resolved.append({"type": limit.get("type"),
                             "value": resolve(limit.get("value"), variables)})
        except (KeyError, ValueError, TypeError):
            continue
    return resolved


def _limit_binding(limits, samples):
    """Was the stage pinned against a declared limit for most of its length?

    Returns (bound, note). Only limits with a readable telemetry channel can
    be assessed; anything else is reported as not assessable rather than
    guessed at.
    """
    for limit in limits:
        channel = CHANNELS.get(limit["type"])
        if channel is None:
            continue
        measured = [s["shot"][channel] for s in samples
                    if isinstance(s.get("shot"), dict) and channel in s["shot"]]
        if not measured:
            continue
        cap = limit["value"]
        pinned = sum(1 for m in measured if m >= cap * LIMIT_TOLERANCE)
        fraction = pinned / len(measured)
        if fraction >= LIMIT_BOUND_FRACTION:
            return True, (f"{limit['type']} held at its {cap:g} limit for "
                          f"{fraction:.0%} of the stage")
    return False, ""


def check_shot(entry):
    profile = entry.get("profile") or {}
    variables = profile.get("variables") or []
    runs = join_to_profile(segment(entry.get("data") or []), profile)

    results = []
    for run in runs:
        base = {
            "status": run["status"],
            "matched": run["matched"],
            "duration_s": run["duration_s"],
            "checkable": False,
            "reason": "",
            "type": None,
            "mean_actual": None,
            "mean_intended": None,
            "mean_abs_deviation": None,
            "approximated": False,
            "target_weight_g": None,
            "actual_weight_g": None,
            "limits": [],
            "limit_bound": False,
            "limit_note": "",
        }

        if not run["matched"]:
            base["reason"] = "not a profile stage"
            results.append(base)
            continue

        stage = run["stage"]
        stage_type = stage.get("type")
        base["type"] = stage_type

        # resolved before the channel check so a stage we cannot score still
        # reports what it was constrained by
        base["limits"] = _resolve_limits(stage, variables)
        base["limit_bound"], base["limit_note"] = _limit_binding(
            base["limits"], run["samples"])

        channel = CHANNELS.get(stage_type)
        if channel is None:
            base["reason"] = f"stage type {stage_type!r} has no shot telemetry channel"
            results.append(base)
            continue

        dynamics = stage.get("dynamics") or {}
        try:
            points = resolve_points(dynamics.get("points") or [], variables)
        except (KeyError, ValueError, TypeError) as exc:
            base["reason"] = f"could not resolve stage dynamics: {exc}"
            results.append(base)
            continue

        if not points:
            base["reason"] = "stage has no dynamics points"
            results.append(base)
            continue

        start_s = run["start_s"]
        actual, intended = [], []
        for sample in run["samples"]:
            shot = sample.get("shot") or {}
            if channel not in shot:
                continue
            elapsed = float(sample.get("profile_time") or 0.0) / 1000.0 - start_s
            actual.append(float(shot[channel]))
            intended.append(interpolate(points, elapsed))

        if not actual:
            base["reason"] = f"no {channel} samples in this stage"
            results.append(base)
            continue

        deviations = [abs(a - i) for a, i in zip(actual, intended)]
        base.update({
            "checkable": True,
            "mean_actual": _mean(actual),
            "mean_intended": _mean(intended),
            "mean_abs_deviation": _mean(deviations),
            "approximated": dynamics.get("interpolation") == "curve",
        })
        results.append(base)

    target = profile.get("final_weight")
    weights = [s["shot"]["weight"] for s in (entry.get("data") or [])
               if isinstance(s.get("shot"), dict) and "weight" in s["shot"]]
    results.append({
        "status": SHOT_TOTAL,
        "matched": False,
        "duration_s": None,
        "checkable": target is not None and bool(weights),
        "reason": "" if (target is not None and weights) else "no declared final_weight",
        "type": "weight",
        "mean_actual": None,
        "mean_intended": None,
        "mean_abs_deviation": None,
        "approximated": False,
        "target_weight_g": float(target) if target is not None else None,
        "actual_weight_g": round(weights[-1], 2) if weights else None,
        # the whole-shot row is not a stage, so it carries no stage limits
        "limits": [],
        "limit_bound": False,
        "limit_note": "",
    })

    return results
