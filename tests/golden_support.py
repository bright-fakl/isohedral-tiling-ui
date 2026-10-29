"""Shared code of the golden fixtures: the case lists and the functions that compute a case from the GUI.

The fixtures in ``tests/data`` were written by ``tests/make_golden.py`` from the current implementation (carto-flow
2.0.0 with tactile 0.1.0). The tests compute the same quantities from the code under test and compare them with the
fixtures. Only this module and ``make_golden.py`` touch the GUI classes; the tests compare plain numbers and text.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np

DATA = Path(__file__).parent / "data"
DESIGNS = DATA / "designs"

WINDOW = (-1.0, -0.3, 1.0, 0.3)
PREVIEW_N_TILES = 100
PREVIEW_MARGIN = 0.25
EXPORT_SIZES = {"Small": 50, "Medium": 100, "Large": 200}

# Export formats are compared for these cases (the 15 presets at the three grid sizes).
FLOAT_RE = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")


def preset_names() -> list[str]:
    from carto_flow.symbol_cartogram.tiling import IsohedralTiling

    return list(IsohedralTiling.list_presets())


def type_numbers() -> list[int]:
    from carto_flow.symbol_cartogram.tiling import IsohedralTiling

    return [int(n) for n in IsohedralTiling.available_types()]


def case_ids() -> list[str]:
    """The 96 cases: ``preset:<name>`` for the 15 presets and ``type:<n>`` for the 81 types at their defaults."""
    return [f"preset:{n}" for n in preset_names()] + [f"type:{n}" for n in type_numbers()]


def make_state(case: str):
    """A ``TilingState`` for a case, set the way the GUI sets it (preset button or type selector)."""
    from carto_flow.symbol_cartogram.tiling import IsohedralTiling

    from isohedral_tiling_ui.state import TilingState

    state = TilingState()
    kind, _, key = case.partition(":")
    if kind == "preset":
        state.load_preset(key)
    else:
        n = int(key)
        info = IsohedralTiling.type_info(n)
        with state.batch_update():
            state.tiling_type = n
            state.parameters = [p["default"] for p in info["parameters"]]
            state.edge_curves = {}
            state.edge_curve_modes = {}
            state.current_preset = ""
    return state


def preview_call(state):
    """The preview call of ``PreviewStrip._render_strip_png``."""
    return state._tiling.generate(
        bounds=WINDOW, n_tiles=PREVIEW_N_TILES, margin=PREVIEW_MARGIN, compute_adjacency=False
    )


def tile_arrays(result) -> dict[str, np.ndarray]:
    """Centroids, areas, rotations and flips of all tiles, and the exterior of three tiles (first, middle, last)."""
    polys = result.polygons
    n = len(polys)
    out = {
        "centroids": np.array([[p.centroid.x, p.centroid.y] for p in polys]),
        "areas": np.array([p.area for p in polys]),
        "rotation": np.array([t.rotation for t in result.transforms]),
        "flipped": np.array([bool(t.flipped) for t in result.transforms]),
    }
    for label, i in (("first", 0), ("middle", n // 2), ("last", n - 1)):
        out[f"poly_{label}"] = np.array(polys[i].exterior.coords)
    return out


def preview_case(case: str) -> dict[str, np.ndarray]:
    return tile_arrays(preview_call(make_state(case)))


def strip_png(state) -> np.ndarray:
    """The strip of ``PreviewStrip._render_strip_png`` as a grayscale uint8 array (uniform color mode)."""
    import matplotlib.image as mpimg

    from isohedral_tiling_ui.components.preview_strip import PreviewStrip

    state.face_color_mode = "uniform"
    data = PreviewStrip(state=state)._render_strip_png()
    img = mpimg.imread(BytesIO(data), format="png")
    gray = img[..., :3].mean(axis=-1)
    return np.round(gray * 255).astype(np.uint8)


def pixel_difference(a: np.ndarray, b: np.ndarray) -> float:
    """Share of pixels that differ by more than 0.05 in intensity; 1.0 if the shapes differ."""
    if a.shape != b.shape:
        return 1.0
    return float(np.mean(np.abs(a.astype(float) - b.astype(float)) / 255 > 0.05))


def _numbers(text: str) -> list[float]:
    return [float(x) for x in FLOAT_RE.findall(text)]


def text_skeleton(text: str) -> str:
    """The text with every number replaced by a placeholder, to compare the structure of a file exactly."""
    return FLOAT_RE.sub("#", text)


def numbers_close(a: str, b: str, atol: float = 1e-6) -> bool:
    """True if the numbers in two texts agree within atol (the texts have the same skeleton)."""
    x, y = _numbers(a), _numbers(b)
    return len(x) == len(y) and bool(np.allclose(x, y, rtol=0, atol=atol))


def panel(case: str):
    from isohedral_tiling_ui.components.preview_strip import PreviewStrip

    return PreviewStrip(state=make_state(case))


def export_summary(case: str, grid_size: str) -> dict:
    """What the SVG and GeoJSON exports of a case contain, without the full coordinates."""
    strip = panel(case)
    strip.state.grid_size = grid_size

    svg = strip._generate_svg_export().getvalue()
    polygons = re.findall(r'<polygon fill="([^"]*)" opacity="([^"]*)" points="([^"]*)" stroke="([^"]*)"', svg)
    svg_centroids, svg_nverts = [], []
    for _fill, _op, pts, _stroke in polygons:
        xy = np.array([[float(v) for v in p.split(",")] for p in pts.split()])
        svg_centroids.append(xy.mean(axis=0))
        svg_nverts.append(len(xy))
    head = svg[: svg.index("<polygon")] if polygons else svg

    gj = json.loads(strip._generate_geojson_export().getvalue())
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
        "gj_flipped": [f["properties"]["flipped"] for f in feats],
        "gj_rotation": np.array([f["properties"]["rotation"] for f in feats]),
        "gj_center": np.array([[f["properties"]["center_x"], f["properties"]["center_y"]] for f in feats]),
        "gj_vertices": np.array([len(f["geometry"]["coordinates"][0]) for f in feats]),
        "gj_centroids": np.array(gj_centroids),
    }


def png_export_size(case: str, grid_size: str) -> tuple[int, int]:
    """Width and height of the PNG export (image decoded from the bytes)."""
    import matplotlib.image as mpimg

    strip = panel(case)
    strip.state.grid_size = grid_size
    img = mpimg.imread(strip._generate_png_export(), format="png")
    return int(img.shape[1]), int(img.shape[0])


def text_exports(case: str) -> dict:
    """The Python snippet and the JSON of the export dialog (without the timestamp)."""
    strip = panel(case)
    config = json.loads(strip._generate_config_json().getvalue())
    config.pop("metadata", None)
    return {"python": strip._generate_python_code(), "config": config}


def export_cases() -> list[tuple[str, str]]:
    return [(f"preset:{n}", g) for n in preset_names() for g in EXPORT_SIZES]


# ----------------------------------------------------------------------------------------------------------------------
# Loaded files


class _Notifications:
    """Stand-in for ``pn.state.notifications``, which is ``None`` outside a server."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def success(self, msg, *a, **k):
        self.events.append(("success", str(msg)))

    def error(self, msg, *a, **k):
        self.events.append(("error", str(msg)))


def load_design(text: str) -> dict:
    """Load a design text through ``LoadSavePanel._on_file_content`` and record what the GUI ends up with."""
    import panel as pn

    from isohedral_tiling_ui.components.load_save import LoadSavePanel
    from isohedral_tiling_ui.state import TilingState

    state = TilingState()
    panel_ = LoadSavePanel(state=state)
    notes = _Notifications()
    saved = pn.state._notification
    pn.state._notification = notes
    try:
        panel_._on_file_content(SimpleNamespace(new=text))
    finally:
        pn.state._notification = saved
    out = {
        "notifications": [list(e) for e in notes.events],
        "tiling_type": state.tiling_type,
        "parameters": [float(p) for p in state.parameters],
        "edge_curves": {str(k): [list(map(float, p)) for p in v] for k, v in state.edge_curves.items()},
        "edge_curve_modes": {str(k): v for k, v in state.edge_curve_modes.items()},
        "current_preset": state.current_preset,
        "active_curve_id": state.active_curve_id,
        "history_length": len(state._history),
        "history_label": state._history[0].get("description", "") if state._history else "",
        "filename": panel_._filename_input.value,
    }
    res = preview_call(state)
    arrays = tile_arrays(res)
    out["preview_n"] = len(res.polygons)
    out["preview_centroid_sum"] = [float(x) for x in arrays["centroids"].sum(axis=0)]
    out["preview_area_sum"] = float(arrays["areas"].sum())
    return out


def design_files() -> list[Path]:
    return sorted(DESIGNS.glob("*.json"))
