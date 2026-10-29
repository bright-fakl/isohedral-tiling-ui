"""Shared code of the golden fixtures: the case lists and the functions that compute a case from the GUI.

The fixtures in ``tests/data`` are written by ``tests/make_fixtures.py`` from this implementation. They record the tiles
of the preview, the SVG and GeoJSON exports, the Python snippet and JSON export, and the result of loading design
files, so that a change in tile selection, colors, exports or loading shows up as a failing test. Only this module
and ``make_fixtures.py`` touch the GUI classes; the tests compare plain numbers and text.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import matplotlib.image as mpimg
import numpy as np
from isotile import list_presets, tiling_types

from isohedral_tiling_ui import exports

DATA = Path(__file__).parent / "data"
DESIGNS = DATA / "designs"

EXPORT_SIZES = {"Small": 50, "Medium": 100, "Large": 200}
EXPORT_PRESETS = ("square", "puzzle_piece", "arrow_quad", "tree")  # exports are stored for these presets at 50 tiles


def preset_names() -> list[str]:
    return list(list_presets())


def case_ids() -> list[str]:
    """``preset:<name>`` for the 15 presets and ``type:<n>`` for every eighth type at its defaults (11 types)."""
    return [f"preset:{n}" for n in preset_names()] + [f"type:{n}" for n in tiling_types()[::8]]


def export_cases() -> list[tuple[str, str]]:
    return [(f"preset:{n}", "Small") for n in EXPORT_PRESETS]


def make_state(case: str):
    """A ``TilingState`` for a case, set the way the GUI sets it (preset button or type selector)."""
    from isohedral_tiling_ui.state import TilingState

    state = TilingState(notify=lambda level, message: None)
    kind, _, key = case.partition(":")
    if kind == "preset":
        state.load_preset(key)
    else:
        state.load_type(int(key))
    return state


def tile_arrays(tiles) -> dict[str, np.ndarray]:
    """Centroids and areas of the tile polygons, aspect and coloring index, and the outline of three tiles."""
    outlines = tiles.outlines()
    x, y = outlines[:, :, 0], outlines[:, :, 1]
    x1, y1 = np.roll(x, -1, axis=1), np.roll(y, -1, axis=1)
    cross = x * y1 - x1 * y
    area = cross.sum(axis=1) / 2
    n = len(outlines)
    out = {
        "centroids": np.column_stack([
            ((x + x1) * cross).sum(axis=1) / (6 * area),
            ((y + y1) * cross).sum(axis=1) / (6 * area),
        ]),
        "areas": np.abs(area),
        "aspect": tiles.aspect,
        "coloring": tiles.colors(),
    }
    for label, i in (("first", 0), ("middle", n // 2), ("last", n - 1)):
        out[f"outline_{label}"] = outlines[i]
    return out


def preview_case(case: str) -> dict[str, np.ndarray]:
    return tile_arrays(exports.strip_tiles(make_state(case).tiling))


def export_summary(case: str, grid_size: str) -> dict:
    """What the SVG and GeoJSON exports of a case contain, without the full coordinates."""
    state = make_state(case)
    tiling, appearance, n_tiles = state.tiling, state.view.appearance(), EXPORT_SIZES[grid_size]

    svg = exports.svg_export(tiling, appearance, n_tiles)
    polygons = re.findall(r'<polygon fill="([^"]*)" opacity="([^"]*)" points="([^"]*)" stroke="([^"]*)"', svg)
    svg_means, svg_nverts = [], []
    for _fill, _opacity, pts, _stroke in polygons:
        xy = np.array([[float(v) for v in p.split(",")] for p in pts.split()])
        svg_means.append(xy.mean(axis=0))
        svg_nverts.append(len(xy))

    gj = json.loads(exports.geojson_export(tiling, appearance, n_tiles))
    feats = gj["features"]
    return {
        "svg_head": svg[: svg.index("<polygon")],
        "svg_n": len(polygons),
        "svg_colors": [p[0] for p in polygons],
        "svg_style": sorted([list(s) for s in {(p[1], p[3]) for p in polygons}]),
        "svg_vertices": np.array(svg_nverts),
        "svg_means": np.array(svg_means),
        "gj_type": gj["type"],
        "gj_n": len(feats),
        "gj_keys": sorted(feats[0]["properties"]),
        "gj_colors": [f["properties"]["color"] for f in feats],
        "gj_color_index": np.array([f["properties"]["color_index"] for f in feats]),
        "gj_aspect": np.array([f["properties"]["aspect"] for f in feats]),
        "gj_cell": np.array([[f["properties"]["cell_u"], f["properties"]["cell_v"]] for f in feats]),
        "gj_center": np.array([[f["properties"]["center_x"], f["properties"]["center_y"]] for f in feats]),
        "gj_vertices": np.array([len(f["geometry"]["coordinates"][0]) for f in feats]),
    }


def png_export_size(case: str, grid_size: str) -> tuple[int, int]:
    """Width and height of the PNG export (image decoded from the bytes)."""
    state = make_state(case)
    png = exports.png_export(state.tiling, state.view.appearance(), EXPORT_SIZES[grid_size])
    img = mpimg.imread(BytesIO(png), format="png")
    return int(img.shape[1]), int(img.shape[0])


def text_exports(case: str) -> dict:
    """The Python snippet and the JSON of the design export (without the timestamp)."""
    state = make_state(case)
    config = json.loads(exports.design_json(state.tiling, state.default_name))
    config.pop("metadata", None)
    return {"python": exports.python_snippet(state.tiling), "config": config}


def load_design(text: str) -> dict:
    """Load a design text through ``LoadSavePanel._on_file_content`` and record what the GUI ends up with."""
    from isohedral_tiling_ui.components.load_save import LoadSavePanel
    from isohedral_tiling_ui.state import TilingState

    events: list[list[str]] = []
    state = TilingState(notify=lambda level, message: events.append([level, message]))
    panel = LoadSavePanel(state=state)
    panel._on_file_content(SimpleNamespace(new=text))
    arrays = tile_arrays(exports.strip_tiles(state.tiling))
    return {
        "notifications": events,
        "tiling_type": state.tiling_type,
        "parameters": [float(p) for p in state.parameters],
        "edge_curves": {str(k): [list(map(float, p)) for p in v] for k, v in state.edge_curves.items()},
        "edge_curve_modes": {str(k): v for k, v in state.edge_curve_modes.items()},
        "source_label": state.source_label,
        "active_curve_id": state.active_curve_id,
        "history": state.model.labels,
        "filename": panel._filename_input.value,
        "preview_n": len(arrays["areas"]),
        "preview_centroid_sum": [float(x) for x in arrays["centroids"].sum(axis=0)],
        "preview_area_sum": float(arrays["areas"].sum()),
    }


def design_files() -> list[Path]:
    return sorted(DESIGNS.glob("*.json"))
