"""Derive summary fields from a shot's telemetry sample array.

All machine times are MILLISECONDS. The trailing "retracting" phase is the
piston returning, not extraction, so it is excluded from brew time.
"""

RETRACTING = "retracting"

NULLS = {"yield_g": None, "time_s": None, "peak_pressure": None, "peak_flow": None}


def _shots(data):
    return [s["shot"] for s in data if isinstance(s.get("shot"), dict)]


def brew_time_s(data):
    """Seconds of profile time, excluding the trailing retracting phase."""
    brewing = [s for s in data if (s.get("status") or "").strip() != RETRACTING]
    if not brewing:
        return None
    return round(float(brewing[-1].get("profile_time") or 0.0) / 1000.0, 1)


def derive(entry):
    data = entry.get("data") or []
    shots = _shots(data)
    if not data or not shots:
        return dict(NULLS)
    return {
        "yield_g": round(float(shots[-1].get("weight", 0.0)), 2),
        "time_s": brew_time_s(data),
        "peak_pressure": round(max(float(s.get("pressure", 0.0)) for s in shots), 2),
        "peak_flow": round(max(float(s.get("flow", 0.0)) for s in shots), 2),
    }
