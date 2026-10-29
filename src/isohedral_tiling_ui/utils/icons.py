"""SVG icons of prototiles."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike

VERTEX_FILL = {3: "#f5b7b1", 4: "#aed6f1", 5: "#f9e79f", 6: "#abebc6"}


def prototile_svg(
    outline: ArrayLike, n_vertices: int, size: int, stroke: str = "#1971c2", stroke_width: float = 1.0
) -> str:
    """SVG of a prototile outline, scaled to its larger extent, filled by the number of vertices of the type."""
    xy = np.asarray(outline, dtype=float)
    lo = xy.min(axis=0)
    pad = 2
    span = float((xy.max(axis=0) - lo).max()) or 1.0
    scale = (size - 4) / span
    pts = " ".join(f"{pad + (x - lo[0]) * scale:.1f},{pad + (xy[:, 1].max() - y) * scale:.1f}" for x, y in xy)
    fill = VERTEX_FILL.get(n_vertices, "#d5d8dc")
    return (
        f'<svg width="{size}" height="{size}" xmlns="http://www.w3.org/2000/svg">'
        f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width:g}"/>'
        f"</svg>"
    )
