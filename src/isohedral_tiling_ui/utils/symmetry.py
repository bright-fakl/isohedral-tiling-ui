"""Edge curve symmetry enforcement utilities.

Edge curves in isohedral tilings have symmetry constraints based on their
edge shape type (J, S, U, I). This module provides functions to enforce
these constraints when users edit curves.

Edge Types
----------
J (Free)
    No symmetry constraint - the full curve can be edited freely.
S (180° rotation)
    The second half of the curve is a 180° rotation of the first half
    about the midpoint (0.5, 0).
U (Mirror)
    The second half of the curve is a mirror reflection of the first half
    about the vertical line x = 0.5.
I (Fixed)
    Straight line from (0, 0) to (1, 0) - not editable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from tactile import EdgeShape


def enforce_curve_symmetry(
    points: list[tuple[float, float]],
    edge_shape: EdgeShape,
) -> list[tuple[float, float]]:
    """Enforce symmetry constraints on an edge curve.

    Parameters
    ----------
    points : list of (x, y)
        Raw curve points from (0, 0) to (1, 0).
    edge_shape : EdgeShape
        The edge's symmetry type from tactile.

    Returns
    -------
    list of (x, y)
        Curve with symmetry enforced.
    """
    from tactile import EdgeShape

    if edge_shape == EdgeShape.I:
        # Fixed straight line
        return [(0.0, 0.0), (1.0, 0.0)]

    if edge_shape == EdgeShape.J:
        # Free curve - no modification needed
        return list(points)

    # For S and U, use INDEX-BASED first half (not x-coordinate based)
    # This allows nodes to have x > 0.5 while still being in the first half
    n = len(points)
    half = (n - 1) / 2

    # Take first half by index (points with index <= half)
    first_half = []
    for i, (x, y) in enumerate(points):
        if i <= half + 1e-9:
            first_half.append((x, y))

    if not first_half:
        first_half = [(0.0, 0.0)]

    # Ensure first point is (0, 0)
    if abs(first_half[0][0]) > 1e-9 or abs(first_half[0][1]) > 1e-9:
        first_half.insert(0, (0.0, 0.0))

    # Check if we have a midpoint (point at index half)
    # For S-type: midpoint is required and y must be 0
    # For U-type: midpoint is optional - only add if user explicitly added one
    has_midpoint = len(first_half) > 1 and abs(first_half[-1][0] - 0.5) < 1e-9

    if edge_shape == EdgeShape.S:
        # S-type always needs a midpoint at (0.5, 0)
        if has_midpoint:
            first_half[-1] = (0.5, 0.0)
        else:
            # Only add midpoint if we have more than just the start point
            # This preserves the implicit midpoint behavior
            if len(first_half) > 1:
                first_half.append((0.5, 0.0))
    elif edge_shape == EdgeShape.U:
        # U-type: midpoint is optional, only fix x if it exists
        if has_midpoint:
            first_half[-1] = (0.5, first_half[-1][1])

    if edge_shape == EdgeShape.S:
        # 180° rotational symmetry about (0.5, 0)
        second_half = [(1.0 - x, -y) for x, y in reversed(first_half[:-1])]
    else:  # EdgeShape.U
        # Mirror symmetry about x = 0.5
        second_half = [(1.0 - x, y) for x, y in reversed(first_half[:-1])]

    # Remove duplicate midpoint if present
    if second_half and abs(second_half[0][0] - 0.5) < 1e-9:
        second_half = second_half[1:]

    return first_half + second_half


def interpolate_curve(
    points: list[tuple[float, float]],
    num_points: int = 20,
    method: str = "spline",
) -> list[tuple[float, float]]:
    """Interpolate a curve to have more points.

    Parameters
    ----------
    points : list of (x, y)
        Control points.
    num_points : int
        Number of output points.
    method : str
        Interpolation method: "linear" or "spline".

    Returns
    -------
    list of (x, y)
        Interpolated points.
    """
    if len(points) < 2:
        return points

    points = sorted(points, key=lambda p: p[0])

    if method == "linear":
        # Linear interpolation
        x = np.array([p[0] for p in points])
        y = np.array([p[1] for p in points])
        x_new = np.linspace(0, 1, num_points)
        y_new = np.interp(x_new, x, y)
        return [(float(xi), float(yi)) for xi, yi in zip(x_new, y_new, strict=False)]

    # Spline interpolation (using cubic)
    from scipy.interpolate import CubicSpline

    x = np.array([p[0] for p in points])
    y = np.array([p[1] for p in points])

    # Ensure x is strictly increasing for spline
    if not np.all(np.diff(x) > 0):
        # Fall back to linear if x is not strictly increasing
        return interpolate_curve(points, num_points, method="linear")

    cs = CubicSpline(x, y, bc_type="clamped")
    x_new = np.linspace(0, 1, num_points)
    y_new = cs(x_new)

    return [(float(xi), float(yi)) for xi, yi in zip(x_new, y_new, strict=False)]


def get_curve_preset(preset_name: str) -> list[tuple[float, float]]:
    """Get a preset edge curve by name.

    Parameters
    ----------
    preset_name : str
        Name of the preset curve.

    Returns
    -------
    list of (x, y)
        Curve control points.
    """
    presets = {
        "straight": [(0, 0), (1, 0)],
        "scalloped": [(0, 0), (0.25, 0.08), (0.5, 0.12), (0.75, 0.08), (1, 0)],
        "scalloped_down": [(0, 0), (0.25, -0.08), (0.5, -0.12), (0.75, -0.08), (1, 0)],
        "scalloped_deep": [(0, 0), (0.2, 0.12), (0.5, 0.2), (0.8, 0.12), (1, 0)],
        "scalloped_deep_down": [(0, 0), (0.2, -0.12), (0.5, -0.2), (0.8, -0.12), (1, 0)],
        "s_curve": [(0, 0), (0.25, 0.1), (0.5, 0), (0.75, -0.1), (1, 0)],
        "s_curve_alt": [(0, 0), (0.25, -0.1), (0.5, 0), (0.75, 0.1), (1, 0)],
        "puzzle": [
            (0, 0),
            (0.35, 0),
            (0.35, 0.06),
            (0.38, 0.12),
            (0.44, 0.16),
            (0.5, 0.18),
            (0.56, 0.16),
            (0.62, 0.12),
            (0.65, 0.06),
            (0.65, 0),
            (1, 0),
        ],
        "puzzle_inverse": [
            (0, 0),
            (0.35, 0),
            (0.35, -0.06),
            (0.38, -0.12),
            (0.44, -0.16),
            (0.5, -0.18),
            (0.56, -0.16),
            (0.62, -0.12),
            (0.65, -0.06),
            (0.65, 0),
            (1, 0),
        ],
        "arrow": [(0, 0), (0.3, 0), (0.5, 0.15), (0.7, 0), (1, 0)],
        "arrow_down": [(0, 0), (0.3, 0), (0.5, -0.15), (0.7, 0), (1, 0)],
        "bulge": [(0, 0), (0.25, 0.1), (0.5, 0.15), (0.75, 0.1), (1, 0)],
        "dent": [(0, 0), (0.25, -0.1), (0.5, -0.15), (0.75, -0.1), (1, 0)],
        "hook_right": [(0, 0), (0.4, 0), (0.6, 0.1), (0.8, 0.05), (1, 0)],
        "hook_left": [(0, 0), (0.2, 0.05), (0.4, 0.1), (0.6, 0), (1, 0)],
        "ramp_up": [(0, 0), (0.3, 0), (0.7, 0.12), (1, 0)],
        "ramp_down": [(0, 0), (0.3, 0.12), (0.7, 0), (1, 0)],
    }

    return presets.get(preset_name, presets["straight"])


def list_curve_presets() -> list[str]:
    """Return list of available curve preset names."""
    return [
        "straight",
        "scalloped",
        "scalloped_down",
        "scalloped_deep",
        "scalloped_deep_down",
        "s_curve",
        "s_curve_alt",
        "puzzle",
        "puzzle_inverse",
        "arrow",
        "arrow_down",
        "bulge",
        "dent",
        "hook_right",
        "hook_left",
        "ramp_up",
        "ramp_down",
    ]
