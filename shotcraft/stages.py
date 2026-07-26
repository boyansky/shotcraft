"""Segment telemetry into contiguous status runs and join them to profile stages.

The telemetry `status` string equals `profile.stages[].name` once stripped.
Not every status corresponds to a stage (e.g. "Fill start", "retracting"), so
unmatched runs are retained with matched=False rather than dropped.
"""
from .derive import RETRACTING


def segment(data):
    """Group samples into contiguous runs of identical (stripped) status."""
    runs = []
    for sample in data:
        status = (sample.get("status") or "").strip()
        # `or 0.0` not a default: the key can be present and explicitly null
        t_s = float(sample.get("profile_time") or 0.0) / 1000.0
        if not runs or runs[-1]["status"] != status:
            runs.append({"status": status, "start_s": t_s, "end_s": t_s, "samples": []})
        runs[-1]["samples"].append(sample)
        runs[-1]["end_s"] = t_s
    for run in runs:
        run["duration_s"] = round(run["end_s"] - run["start_s"], 2)
        run["start_s"] = round(run["start_s"], 2)
        run["end_s"] = round(run["end_s"], 2)
    return runs


def join_to_profile(runs, profile):
    """Attach the matching profile stage to each run by name."""
    by_name = {s["name"].strip(): s for s in profile.get("stages", [])}
    joined = []
    for run in runs:
        stage = None if run["status"] == RETRACTING else by_name.get(run["status"])
        joined.append({**run, "stage": stage, "matched": stage is not None})
    return joined
