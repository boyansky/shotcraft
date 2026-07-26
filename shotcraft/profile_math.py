"""Resolve profile variable references and evaluate dynamics curves.

NOTE ON ACCURACY: profiles declare interpolation as "curve" or "linear". We
interpolate linearly in both cases. For "curve" stages the reconstructed
intent is an approximation, and any deviation reported against a curve stage
must be labelled as such rather than presented as exact.
"""


def resolve(value, variables):
    """Resolve a number or a "$variable_key" reference to a float."""
    if isinstance(value, str):
        if not value.startswith("$"):
            raise ValueError(f"unresolvable string value: {value!r}")
        key = value[1:]
        for var in variables:
            if var.get("key") == key:
                return float(var["value"])
        raise KeyError(f"no profile variable named {key!r}")
    return float(value)


def resolve_points(points, variables):
    """Resolve a dynamics points list into [(x, y), ...] floats."""
    return [(resolve(x, variables), resolve(y, variables)) for x, y in points]


def interpolate(points, x):
    """Piecewise-linear value at x, clamped outside the point range."""
    if not points:
        raise ValueError("cannot interpolate an empty points list")
    pts = sorted(points, key=lambda p: p[0])
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    raise AssertionError("unreachable: x lies inside the sorted range")
