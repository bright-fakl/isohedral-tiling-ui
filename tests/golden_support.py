"""Shared code of the golden fixtures: the case lists and the functions that compute a case from the GUI.

The fixtures in ``tests/data`` were written by ``tests/make_golden.py`` from the implementation on carto-flow 2.0.0 and
tactile 0.1.0. This module computes the same quantities from the code under test (isotile); the tests compare them
with the fixtures and record the intended differences. The fixtures are a migration tool: once the comparison has been
run they are replaced by a smaller set regenerated from the isotile implementation, and ``make_golden.py`` is deleted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from isotile import list_presets, tiling_types

from isohedral_tiling_ui import exports

DATA = Path(__file__).parent / "data"
DESIGNS = DATA / "designs"

WINDOW = exports.STRIP_BOUNDS
EXPORT_SIZES = {"Small": 50, "Medium": 100, "Large": 200}
FLOAT_RE = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")


def preset_names() -> list[str]:
    return list(list_presets())


def type_numbers() -> list[int]:
    return [int(n) for n in tiling_types()]


def case_ids() -> list[str]:
    """The 96 cases: ``preset:<name>`` for the 15 presets and ``type:<n>`` for the 81 types at their defaults."""
    return [f"preset:{n}" for n in preset_names()] + [f"type:{n}" for n in type_numbers()]


def text_case_ids() -> list[str]:
    """Cases whose Python snippet and JSON export are stored: the 15 presets and every eighth type (11 types)."""
    return [f"preset:{n}" for n in preset_names()] + [f"type:{n}" for n in type_numbers()[::8]]


def make_state(case: str, spline: bool = False):
    """A ``TilingState`` for a case, set the way the GUI sets it (preset button or type selector).

    With ``spline`` every curve is switched to the spline mode, which is how the fixtures drew the curves of a preset.
    """
    from isohedral_tiling_ui.state import TilingState

    state = TilingState(notify=lambda level, msg: None)
    kind, _, key = case.partition(":")
    if kind == "preset":
        state.load_preset(key)
    else:
        state.load_type(int(key))
    if spline:
        for shape_id in state.edge_curves:
            assert state.set_mode(shape_id, "spline")
    return state


def tile_arrays(tiles) -> dict[str, np.ndarray]:
    """Centroids (of the polygon), areas and outlines of all tiles, with the aspect and coloring index."""
    outlines = tiles.outlines()
    x, y = outlines[:, :, 0], outlines[:, :, 1]
    x1, y1 = np.roll(x, -1, axis=1), np.roll(y, -1, axis=1)
    cross = x * y1 - x1 * y
    area = cross.sum(axis=1) / 2
    cx = ((x + x1) * cross).sum(axis=1) / (6 * area)
    cy = ((y + y1) * cross).sum(axis=1) / (6 * area)
    return {
        "centroids": np.column_stack([cx, cy]),
        "areas": np.abs(area),
        "outlines": outlines,
        "aspect": tiles.aspect,
        "coloring": tiles.colors(),
    }


def preview_case(case: str, spline: bool = False) -> dict[str, np.ndarray]:
    return tile_arrays(exports.strip_tiles(make_state(case, spline).tiling))


def _numbers(text: str) -> list[float]:
    return [float(x) for x in FLOAT_RE.findall(text)]


def text_skeleton(text: str) -> str:
    """The text with every number replaced by a placeholder, to compare the structure of a file exactly."""
    return FLOAT_RE.sub("#", text)


def numbers_close(a: str, b: str, atol: float = 1e-6) -> bool:
    """True if the numbers in two texts agree within atol (the texts have the same skeleton)."""
    x, y = _numbers(a), _numbers(b)
    return len(x) == len(y) and bool(np.allclose(x, y, rtol=0, atol=atol))


def export_summary(case: str, grid_size: str, spline: bool = False) -> dict:
    """What the SVG and GeoJSON exports of a case contain, without the full coordinates."""
    state = make_state(case, spline)
    tiling, appearance, n_tiles = state.tiling, state.view.appearance(), EXPORT_SIZES[grid_size]

    svg = exports.svg_export(tiling, appearance, n_tiles)
    polygons = re.findall(r'<polygon fill="([^"]*)" opacity="([^"]*)" points="([^"]*)" stroke="([^"]*)"', svg)
    svg_centroids, svg_nverts = [], []
    for _fill, _op, pts, _stroke in polygons:
        xy = np.array([[float(v) for v in p.split(",")] for p in pts.split()])
        svg_centroids.append(xy.mean(axis=0))
        svg_nverts.append(len(xy))
    head = svg[: svg.index("<polygon")] if polygons else svg

    gj = json.loads(exports.geojson_export(tiling, appearance, n_tiles))
    feats = gj["features"]
    gj_centroids = [np.array(f["geometry"]["coordinates"][0]).mean(axis=0) for f in feats]
    return {
        "svg_head": head,
        "svg_n": len(polygons),
        "svg_colors": [p[0] for p in polygons],
        "svg_style": sorted([list(s) for s in {(p[1], p[3]) for p in polygons}]),
        "svg_vertices": np.array(svg_nverts),
        "svg_centroids": np.array(svg_centroids),
        "gj_type": gj["type"],
        "gj_geometry_types": sorted({f["geometry"]["type"] for f in feats}),
        "gj_n": len(feats),
        "gj_keys": sorted(feats[0]["properties"]) if feats else [],
        "gj_colors": [f["properties"]["color"] for f in feats],
        "gj_center": np.array([[f["properties"]["center_x"], f["properties"]["center_y"]] for f in feats]),
        "gj_vertices": np.array([len(f["geometry"]["coordinates"][0]) for f in feats]),
        "gj_centroids": np.array(gj_centroids),
    }


def export_outlines(case: str, grid_size: str, spline: bool = False) -> np.ndarray:
    """Outlines of the tiles of the export, in the order of the SVG and GeoJSON features."""
    state = make_state(case, spline)
    return exports.export_tiles(state.tiling, EXPORT_SIZES[grid_size]).outlines()


def png_export_size(case: str, grid_size: str) -> tuple[int, int]:
    """Width and height of the PNG export (image decoded from the bytes)."""
    from io import BytesIO

    import matplotlib.image as mpimg

    state = make_state(case)
    png = exports.png_export(state.tiling, state.view.appearance(), EXPORT_SIZES[grid_size])
    img = mpimg.imread(BytesIO(png), format="png")
    return int(img.shape[1]), int(img.shape[0])


def text_exports(case: str) -> dict:
    """The Python snippet and the JSON of the export dialog (without the timestamp)."""
    state = make_state(case)
    config = json.loads(exports.design_json(state.tiling, state.default_name))
    config.pop("metadata", None)
    return {"python": exports.python_snippet(state.tiling), "config": config}


def export_cases() -> list[tuple[str, str]]:
    return [(f"preset:{n}", g) for n in preset_names() for g in EXPORT_SIZES]


# ----------------------------------------------------------------------------------------------------------------------
# Loaded files


def load_design(text: str) -> dict:
    """Load a design text through ``LoadSavePanel._on_file_content`` and record what the GUI ends up with."""
    from isohedral_tiling_ui.components.load_save import LoadSavePanel
    from isohedral_tiling_ui.state import TilingState

    events: list[list[str]] = []
    state = TilingState(notify=lambda level, msg: events.append([level, msg]))
    panel_ = LoadSavePanel(state=state)
    panel_._on_file_content(SimpleNamespace(new=text))
    out = {
        "notifications": events,
        "tiling_type": state.tiling_type,
        "parameters": [float(p) for p in state.parameters],
        "edge_curves": {str(k): [list(map(float, p)) for p in v] for k, v in state.edge_curves.items()},
        "edge_curve_modes": {str(k): v for k, v in state.edge_curve_modes.items()},
        "current_preset": state.source_label,
        "active_curve_id": state.active_curve_id,
        "history_length": len(state.model.labels),
        "history_label": state.model.labels[0],
        "filename": panel_._filename_input.value,
    }
    arrays = tile_arrays(exports.strip_tiles(state.tiling))
    out["preview_n"] = len(arrays["areas"])
    out["preview_centroid_sum"] = [float(x) for x in arrays["centroids"].sum(axis=0)]
    out["preview_area_sum"] = float(arrays["areas"].sum())
    return out


def design_files() -> list[Path]:
    return sorted(DESIGNS.glob("*.json"))
