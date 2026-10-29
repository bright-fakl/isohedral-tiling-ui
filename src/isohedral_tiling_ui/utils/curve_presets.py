"""The preset edge curves of the curve editor: control points from (0, 0) to (1, 0), before the edge shape's symmetry."""

from __future__ import annotations


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
    presets: dict[str, list[tuple[float, float]]] = {
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
