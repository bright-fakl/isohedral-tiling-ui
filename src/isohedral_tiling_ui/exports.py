"""Tile selection, colors and exports (strip PNG, PNG, SVG, GeoJSON, JSON, Python snippet). No Panel, no shapely.

The preview selects tiles with the straight outline and a margin of 0.25, which misses no tile of the visible window; the
exports use the curved outline of the tiling and a margin of 0.1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

import numpy as np
from isotile import IsohedralTiling, TileSet

GRID_SIZES = {"Small": 50, "Medium": 100, "Large": 200}
STRIP_BOUNDS = (-1.0, -0.3, 1.0, 0.3)
STRIP_N_TILES = 100
STRIP_MARGIN = 0.25
EXPORT_BOUNDS = (-2.0, -2.0, 2.0, 2.0)
EXPORT_MARGIN = 0.1
COLORMAPS = {"Category10": "tab10", "Viridis": "viridis", "Plasma": "plasma", "Inferno": "inferno", "Turbo": "turbo"}


@dataclass(frozen=True)
class Appearance:
    """How tiles are drawn: face color mode (uniform, aspect or coloring), face color, colormap name and edge color."""

    face_color_mode: str = "aspect"
    face_color: str = "#1f77b4"
    face_colormap: str = "Category10"
    edge_color: str = "#333333"


def strip_tiles(tiling: IsohedralTiling) -> TileSet:
    """The tiles of the preview strip."""
    return tiling.fill_region(STRIP_BOUNDS, n_tiles=STRIP_N_TILES, margin=STRIP_MARGIN, outline="straight")


def export_tiles(tiling: IsohedralTiling, n_tiles: int) -> TileSet:
    """The tiles of an export: about ``n_tiles`` tiles in the square (-2, -2, 2, 2)."""
    return tiling.fill_region(EXPORT_BOUNDS, n_tiles=n_tiles, margin=EXPORT_MARGIN)


def face_colors(tiles: TileSet, appearance: Appearance) -> list[str]:
    """Hex face color of each tile: one color, the aspect of the tile, or the color of the type's coloring."""
    from matplotlib import colormaps
    from matplotlib.colors import to_hex

    mode = appearance.face_color_mode
    if mode == "uniform" or len(tiles) == 0:
        return [appearance.face_color] * len(tiles)
    if mode == "aspect":
        index, count = tiles.aspect, tiles.tiling.type.n_aspects
    elif mode == "coloring":
        index = tiles.colors()
        count = int(index.max()) + 1
    else:
        raise ValueError(f"unknown face color mode {mode!r}")
    cmap = colormaps[COLORMAPS[appearance.face_colormap]]
    if appearance.face_colormap == "Category10":
        palette = [to_hex(cmap(i % cmap.N)) for i in range(count)]
    else:
        palette = [to_hex(cmap(v)) for v in (np.linspace(0.0, 1.0, count) if count > 1 else [0.5])]
    return [palette[i] for i in index.tolist()]


def _png(
    tiles: TileSet,
    appearance: Appearance,
    figsize: tuple[float, float],
    dpi: int,
    bounds: tuple[float, float, float, float],
    linewidth: float,
    equal_aspect: bool,
    pad: float,
) -> bytes:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import PolyCollection
    from matplotlib.figure import Figure

    fig = Figure(figsize=figsize, dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    if equal_aspect:
        ax.set_aspect("equal")
    ax.axis("off")
    ax.add_collection(
        PolyCollection(
            list(tiles.outlines()),
            facecolors=face_colors(tiles, appearance),
            edgecolors=appearance.edge_color,
            linewidths=linewidth,
            alpha=0.85,
        )
    )
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white", bbox_inches="tight", pad_inches=pad)
    return buf.getvalue()


def strip_png(tiling: IsohedralTiling, appearance: Appearance) -> bytes:
    """PNG of the preview strip."""
    return _png(strip_tiles(tiling), appearance, (3.0, 0.9), 100, STRIP_BOUNDS, 0.3, False, 0.0)


def png_export(tiling: IsohedralTiling, appearance: Appearance, n_tiles: int) -> bytes:
    """Square PNG of about ``n_tiles`` tiles."""
    return _png(export_tiles(tiling, n_tiles), appearance, (8.0, 8.0), 150, EXPORT_BOUNDS, 0.5, True, 0.05)


def svg_export(tiling: IsohedralTiling, appearance: Appearance, n_tiles: int) -> str:
    """SVG of about ``n_tiles`` tiles, 800 by 800 pixels."""
    import svgwrite

    tiles = export_tiles(tiling, n_tiles)
    xmin, ymin, xmax, ymax = EXPORT_BOUNDS
    size = 800
    outlines = tiles.outlines()
    x = np.round((outlines[:, :, 0] - xmin) / (xmax - xmin) * size, 3)
    y = np.round((ymax - outlines[:, :, 1]) / (ymax - ymin) * size, 3)
    dwg = svgwrite.Drawing(size=(f"{size}px", f"{size}px"), viewBox=f"0 0 {size} {size}")
    dwg.add(dwg.rect((0, 0), (size, size), fill="white"))
    for xs, ys, color in zip(x, y, face_colors(tiles, appearance), strict=True):
        dwg.add(
            dwg.polygon(
                list(zip(xs.tolist(), ys.tolist(), strict=True)),
                fill=color,
                stroke=appearance.edge_color,
                stroke_width=0.5,
                opacity=0.85,
            )
        )
    return str(dwg.tostring())


def geojson_export(tiling: IsohedralTiling, appearance: Appearance, n_tiles: int) -> str:
    """GeoJSON FeatureCollection of about ``n_tiles`` tiles.

    Properties: ``tile_index``, ``aspect``, ``cell_u``, ``cell_v``, ``color_index`` (the type's coloring), ``color``
    (hex face color), ``center_x``, ``center_y`` (centroid of the tile).
    """
    tiles = export_tiles(tiling, n_tiles)
    centroids = tiles.centroids
    data = tiles.to_geojson({
        "tile_index": np.arange(len(tiles)),
        "cell_u": tiles.cell[:, 0],
        "cell_v": tiles.cell[:, 1],
        "color_index": tiles.colors(),
        "color": face_colors(tiles, appearance),
        "center_x": np.round(centroids[:, 0], 6),
        "center_y": np.round(centroids[:, 1], 6),
    })
    for feature in data["features"]:
        del feature["properties"]["cell"]
    return json.dumps(data)


def design_json(tiling: IsohedralTiling, description: str) -> str:
    """The design as isotile format 1.1 with the ``description`` and the creation time added."""
    config = {
        **tiling.to_dict(),
        "description": description,
        "metadata": {"created": datetime.now(timezone.utc).isoformat()},
    }
    return json.dumps(config, indent=2)


def python_snippet(tiling: IsohedralTiling, n_tiles: int = 100) -> str:
    """Python code that rebuilds the tiling and fills the export square."""
    return "\n".join([
        "from isotile import IsohedralTiling",
        "",
        f"tiling = {tiling!r}",
        "",
        f"tiles = tiling.fill_region({EXPORT_BOUNDS!r}, n_tiles={n_tiles}, margin={EXPORT_MARGIN})",
        "outlines = tiles.outlines()  # (n_tiles, n_points, 2) array of the tile outlines",
        "# polygons = tiles.polygons()  # shapely polygons; needs isotile[shapely]",
    ])
