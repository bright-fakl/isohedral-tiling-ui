"""Cached spline interpolation shared across UI components."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=64)
def interpolate_spline_cached(
    points: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    """Interpolate a smooth spline through *points*, cached by control-point identity.

    Uses parametric cubic spline interpolation (x(t), y(t)) to handle curves with
    vertical segments where x may not be strictly increasing. The number of output
    points is ``max(100, n_controls * 10)``. Returns *points* unchanged when fewer
    than 3 points are supplied or when fitting fails.

    Parameters
    ----------
    points:
        Control points as an immutable tuple of ``(x, y)`` pairs.

    Returns
    -------
    Interpolated points as a tuple of ``(x, y)`` pairs.
    """
    import numpy as np
    from scipy.interpolate import CubicSpline

    if len(points) < 3:
        return points

    n_out = max(100, len(points) * 10)
    x = np.array([p[0] for p in points])
    y = np.array([p[1] for p in points])

    # Compute cumulative arc length parameter t
    dx = np.diff(x)
    dy = np.diff(y)
    dists = np.sqrt(dx**2 + dy**2)
    t = np.concatenate([[0], np.cumsum(dists)])

    # Normalize t to [0, 1]
    if t[-1] > 0:
        t = t / t[-1]

    try:
        t_new = np.linspace(0, 1, n_out)

        # Handle edge case where all x or y values are the same
        if len(np.unique(x)) < 2:
            xs_new = np.full_like(t_new, x[0])
        else:
            cs_x = CubicSpline(t, x, bc_type="natural")
            xs_new = cs_x(t_new)

        if len(np.unique(y)) < 2:
            ys_new = np.full_like(t_new, y[0])
        else:
            cs_y = CubicSpline(t, y, bc_type="natural")
            ys_new = cs_y(t_new)

        return tuple(zip(xs_new.tolist(), ys_new.tolist(), strict=False))
    except Exception:
        return points


def interpolate_spline(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Convenience wrapper: accepts a list, returns a list."""
    return list(interpolate_spline_cached(tuple(points)))
