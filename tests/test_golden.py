"""Migration test: the isotile implementation against fixtures that the carto-flow implementation wrote.

The fixtures (``tests/data``) record the behavior of the GUI on carto-flow 2.0.0 and tactile 0.1.0: the tiles of the
preview, the SVG and GeoJSON exports, the Python snippet and JSON export, and the result of loading design files. This
file runs the isotile implementation on the same cases and requires the same result, except for the intended
differences below. Each difference is asserted in the test that meets it, so a change in either direction fails.

Intended differences (reason in each):

DIFF_ASPECT_COLORS
    Faces are colored by the aspect index of the tile (isotile) instead of by rotation / 360. Types with one aspect
    keep their color.
DIFF_NO_INDEX_MODE
    The ``index`` color mode is gone: the tile order of tactile is not reproduced. ``coloring`` (the 2 or 3 colors of
    ``TileSet.colors``, adjacent tiles differ) takes its place.
DIFF_TILE_ORDER
    Tiles come in another order, so they are matched by centroid.
DIFF_TILE_SELECTION
    The fixtures contain tiles far outside the region (tactile fills a larger parallelogram). Only the tiles that
    overlap the window (preview) or the export square are compared.
DIFF_TREE
    The fixture of the ``tree`` preview lacks 9 tiles of the window (a gap at the bottom edge); isotile covers it.
DIFF_SAMPLING
    A spline with m control points is sampled with max(100, 10 m) points (isotile) instead of 100 (carto-flow). The
    ``puzzle_piece`` curve has 11 control points: 110 against 100 points.
DIFF_LINEAR_MODE
    The fixtures drew the ``linear`` curves of the ``arrow_quad`` preset as splines (the tiling of the preview was
    rebuilt without the modes). The port draws lines; the test compares it with the same preset in spline mode.
DIFF_SVG_RING
    SVG polygons are not closed by a repeated first point.
DIFF_GEOJSON_PROPERTIES
    ``tile_index``, ``rotation``, ``flipped``, ``center_x``, ``center_y``, ``color`` become ``tile_index``,
    ``aspect``, ``cell_u``, ``cell_v``, ``color_index``, ``color``, ``center_x``, ``center_y``. The center is the
    centroid of the tile.
DIFF_JSON_FORMAT
    Save Design and the JSON export write isotile format 1.1 (``version``, ``edge_curve_modes`` for every curve,
    ``description``); the export dialog wrote neither modes nor description.
DIFF_PYTHON_SNIPPET
    The snippet builds an ``isotile.IsohedralTiling`` and calls ``fill_region`` with ``margin=0.1``.
DIFF_LOADER
    A parameter list of the wrong length is replaced by the defaults and a curve on a straight edge is dropped, each
    with a warning (the fixture refused the first and accepted the second silently); a design whose outline is invalid
    is refused and the state is unchanged (the fixture accepted it and repaired the tiles); the messages of the
    refusals differ.
"""

from __future__ import annotations

import ast
import json

import numpy as np
import pytest
from isotile import get_type
from matplotlib.path import Path as MplPath
from matplotlib.transforms import Bbox
from scipy.spatial import cKDTree

from isohedral_tiling_ui import exports
from tests import golden_support as g

DIFF_ASPECT_COLORS = "aspect"
DIFF_LINEAR_MODE_CASES = {"preset:arrow_quad"}
DIFF_TREE_CASES = {"preset:tree"}
DIFF_TREE_MISSING_TILES = 9
DIFF_TREE_EXPORT_MISSING = {"Small": 5}  # tiles of the export square that the fixture lacks, by grid size
DIFF_SAMPLING_CASES = {"preset:puzzle_piece"}

GEOM_ATOL = 1e-9  # tile size is about 0.2
SAMPLING_CENTROID_ATOL = 1e-6
SAMPLING_POLYGON_ATOL = 1e-4
MATCH_TOL = 1e-3  # centroids closer than this are the same tile (tiles are at least 0.1 apart)


@pytest.fixture(scope="module")
def preview_fx():
    with np.load(g.DATA / "golden_preview.npz") as f:
        return {k: f[k] for k in f.files}


@pytest.fixture(scope="module")
def exports_fx():
    with np.load(g.DATA / "golden_exports.npz") as f:
        return {k: f[k] for k in f.files}


@pytest.fixture(scope="module")
def text_fx():
    return json.loads((g.DATA / "golden_text.json").read_text())


CASES = g.case_ids()
PRESETS = g.preset_names()
EXPORT_CASES = g.export_cases()
DESIGNS = [p.stem for p in g.design_files()]


def match(old: np.ndarray, new: np.ndarray, tol: float = MATCH_TOL) -> tuple[np.ndarray, np.ndarray]:
    """For each old centroid the index of the nearest new centroid and whether it is the same tile."""
    dist, index = cKDTree(new).query(old)
    return index, dist < tol


def overlaps(outlines: np.ndarray, bounds: tuple[float, float, float, float]) -> np.ndarray:
    """Which tile outlines overlap the rectangle (touching along the boundary does not count)."""
    box = Bbox([[bounds[0] + 1e-9, bounds[1] + 1e-9], [bounds[2] - 1e-9, bounds[3] - 1e-9]])
    return np.array([MplPath(o).intersects_bbox(box, filled=True) for o in outlines])


def polyline_distance(points: np.ndarray, polygon: np.ndarray) -> float:
    """Largest distance of the points to the closed polygon line."""
    a, b = polygon, np.roll(polygon, -1, axis=0)
    ab = b - a
    ap = points[:, None, :] - a[None]
    t = np.clip((ap * ab).sum(2) / (ab * ab).sum(1), 0, 1)
    return float(np.linalg.norm(ap - t[..., None] * ab, axis=2).min(1).max())


def has_sampled_curve(case: str) -> bool:
    return case in DIFF_SAMPLING_CASES


# ----------------------------------------------------------------------------------------------------------------------
# Fixture integrity


def test_fixture_has_96_cases(preview_fx, text_fx):
    assert len(CASES) == 96
    assert text_fx["cases"] == CASES
    for case in CASES:
        assert f"{case}/centroids" in preview_fx
    assert len(PRESETS) == 15
    assert len([c for c in CASES if c.startswith("type:")]) == 81


def test_fixtures_were_written_by_carto_flow(text_fx):
    assert text_fx["versions"]["carto-flow"] == "2.0.0"
    assert text_fx["versions"]["tactile"] == "0.1.0"


# ----------------------------------------------------------------------------------------------------------------------
# Preview


@pytest.mark.parametrize("case", CASES)
def test_preview_tiles(case, preview_fx):
    """Every tile of the window is in the fixture and the fixture has no tile that reaches into the window."""
    spline = case in DIFF_LINEAR_MODE_CASES  # DIFF_LINEAR_MODE
    new = g.preview_case(case, spline=spline)
    old_c, old_a = preview_fx[f"{case}/centroids"], preview_fx[f"{case}/areas"]
    new_c, outlines = new["centroids"], new["outlines"]

    in_window = overlaps(outlines, g.WINDOW)  # DIFF_TILE_SELECTION: the fixture has far more tiles
    index, same = match(old_c, new_c)
    _, new_same = match(new_c, old_c)

    missing_in_fixture = int((in_window & ~new_same).sum())
    if case in DIFF_TREE_CASES:  # DIFF_TREE
        assert missing_in_fixture == DIFF_TREE_MISSING_TILES
        assert covers(outlines[in_window], g.WINDOW)
    else:
        assert missing_in_fixture == 0

    radius = np.linalg.norm(outlines - new_c[:, None, :], axis=2).max()
    dx = np.maximum(np.maximum(g.WINDOW[0] - old_c[:, 0], old_c[:, 0] - g.WINDOW[2]), 0)
    dy = np.maximum(np.maximum(g.WINDOW[1] - old_c[:, 1], old_c[:, 1] - g.WINDOW[3]), 0)
    near_window = np.hypot(dx, dy) < radius
    assert (near_window & ~same).sum() == 0, "a tile of the fixture near the window is missing in the port"

    # Matched tiles have the same area, centroid and outline.
    centroid_atol = SAMPLING_CENTROID_ATOL if has_sampled_curve(case) else GEOM_ATOL
    assert np.abs(old_c[same] - new_c[index[same]]).max() <= centroid_atol
    assert np.abs(old_a[same] - new["areas"][index[same]]).max() <= 1e-12
    polygon_atol = SAMPLING_POLYGON_ATOL if has_sampled_curve(case) else GEOM_ATOL  # DIFF_SAMPLING
    for label in ("first", "middle", "last"):
        poly = preview_fx[f"{case}/poly_{label}"]
        x, y = poly[:, 0], poly[:, 1]
        cross = x[:-1] * y[1:] - x[1:] * y[:-1]
        area = cross.sum() / 2
        cx, cy = ((x[:-1] + x[1:]) * cross).sum() / (6 * area), ((y[:-1] + y[1:]) * cross).sum() / (6 * area)
        j, ok = match(np.array([[cx, cy]]), new_c)
        if ok[0]:
            assert polyline_distance(poly[:-1], outlines[j[0]]) <= polygon_atol
            assert polyline_distance(outlines[j[0]], poly[:-1]) <= polygon_atol


def covers(window_outlines: np.ndarray, bounds: tuple[float, float, float, float], n: int = 201) -> bool:
    """True if a grid of points of the window lies in at least one outline."""
    xs = np.linspace(bounds[0], bounds[2], n)
    ys = np.linspace(bounds[1], bounds[3], int(n * (bounds[3] - bounds[1]) / (bounds[2] - bounds[0])) + 1)
    points = np.array([(x, y) for x in xs[1:-1] for y in ys[1:-1]])
    inside = np.zeros(len(points), dtype=bool)
    for o in window_outlines:
        inside |= MplPath(o).contains_points(points)
    return bool(inside.all())


def test_linear_curves_are_drawn_as_lines(preview_fx):
    """DIFF_LINEAR_MODE: the fixture of arrow_quad has the spline tiles; the port's linear tiles are another shape."""
    case = "preset:arrow_quad"
    new = g.preview_case(case)
    _, same = match(preview_fx[f"{case}/centroids"], new["centroids"])
    assert same.sum() == 0
    assert (new["outlines"].shape[1]) < 100  # lines through the control points, not the 400+ samples of a spline


@pytest.mark.parametrize("case", [c for c in CASES if c.startswith("preset:")])
def test_window_tiles_use_every_aspect(case):
    """The aspect index takes each of the type's values in the window, so the aspect mode shows all of them."""
    new_aspect = exports.strip_tiles(g.make_state(case).tiling).aspect
    n_aspects = get_type(g.make_state(case).tiling_type).n_aspects
    assert set(new_aspect.tolist()) == set(range(n_aspects))


# ----------------------------------------------------------------------------------------------------------------------
# Exports


@pytest.mark.parametrize(("case", "size"), EXPORT_CASES)
def test_svg_and_geojson_export(case, size, exports_fx, text_fx):
    key = f"{case}/{size}"
    spline = case in DIFF_LINEAR_MODE_CASES  # DIFF_LINEAR_MODE
    got = g.export_summary(case, size, spline=spline)
    want = text_fx["exports"][key]

    assert got["svg_head"] == want["svg_head"]
    assert got["svg_style"] == want["svg_style"]
    assert got["gj_type"] == want["gj_type"]
    assert got["gj_geometry_types"] == want["gj_geometry_types"]
    assert got["svg_n"] == got["gj_n"]

    # DIFF_GEOJSON_PROPERTIES
    assert want["gj_keys"] == ["center_x", "center_y", "color", "flipped", "rotation", "tile_index"]
    assert got["gj_keys"] == [
        "aspect",
        "cell_u",
        "cell_v",
        "center_x",
        "center_y",
        "color",
        "color_index",
        "tile_index",
    ]

    # DIFF_TILE_ORDER, DIFF_TILE_SELECTION: tiles are matched by the vertex mean of the ring.
    sampled = case in DIFF_SAMPLING_CASES
    ring_tol = 1e-2 if sampled else 1e-6  # DIFF_SAMPLING: the mean of a sampled ring depends on the sampling
    old_pix, new_pix = exports_fx[f"{key}/svg_centroids"], got["svg_centroids"]
    old_ring = exports_fx[f"{key}/gj_centroids"]
    outlines = g.export_outlines(case, size, spline)
    # The fixture's mean over the closed ring (first point repeated); the port's GeoJSON rings are counterclockwise,
    # so the same quantity is taken from the outlines, which start at vertex 0 as before.
    new_ring = (outlines.sum(axis=1) + outlines[:, 0]) / (outlines.shape[1] + 1)
    _, same = match(old_ring, new_ring, ring_tol)
    new_index, new_same = match(new_ring, old_ring, ring_tol)
    in_square = overlaps(outlines, exports.EXPORT_BOUNDS)
    missing = int((in_square & ~new_same).sum())
    assert missing == (DIFF_TREE_EXPORT_MISSING.get(size, 0) if case in DIFF_TREE_CASES else 0)  # DIFF_TREE
    radius = np.linalg.norm(outlines - new_ring[:, None, :], axis=2).max()
    square = exports.EXPORT_BOUNDS
    dx = np.maximum(np.maximum(square[0] - old_ring[:, 0], old_ring[:, 0] - square[2]), 0)
    dy = np.maximum(np.maximum(square[1] - old_ring[:, 1], old_ring[:, 1] - square[3]), 0)
    assert ((np.hypot(dx, dy) < radius) & ~same).sum() == 0, "a tile of the fixture near the square is missing"

    k = new_same
    assert np.abs(new_ring[k] - old_ring[new_index[k]]).max() <= ring_tol
    if not sampled:
        # DIFF_SVG_RING: the port's polygons have one point less; closing them gives the fixture's vertex mean.
        assert (got["svg_vertices"][k] == exports_fx[f"{key}/svg_vertices"][new_index[k]] - 1).all()
        n = got["svg_vertices"][:, None]
        closed_mean = (new_pix * n + _first_points(case, size, spline)) / (n + 1)
        assert np.abs(closed_mean[k] - old_pix[new_index[k]]).max() <= 1e-3  # pixels
        assert (got["gj_vertices"][k] == exports_fx[f"{key}/gj_vertices"][new_index[k]]).all()
    _assert_geojson_rings(case, size, spline, outlines)

    # DIFF_ASPECT_COLORS
    if get_type(g.make_state(case).tiling_type).n_aspects == 1:
        assert set(got["svg_colors"]) == set(want["svg_colors"]) == {"#1f77b4"}
    else:
        assert len(set(got["svg_colors"])) == min(get_type(g.make_state(case).tiling_type).n_aspects, 10)


def _assert_geojson_rings(case: str, size: str, spline: bool, outlines: np.ndarray) -> None:
    """Each GeoJSON ring is closed, counterclockwise and has the points of the tile outline."""
    state = g.make_state(case, spline)
    data = json.loads(exports.geojson_export(state.tiling, state.view.appearance(), g.EXPORT_SIZES[size]))
    for feature, outline in zip(data["features"], outlines, strict=True):
        ring = np.array(feature["geometry"]["coordinates"][0])
        assert (ring[0] == ring[-1]).all()
        x, y = ring[:, 0], ring[:, 1]
        assert (x[:-1] * y[1:] - x[1:] * y[:-1]).sum() > 0
        assert sorted(map(tuple, ring[:-1].round(9))) == sorted(map(tuple, outline.round(9)))


def _first_points(case: str, size: str, spline: bool) -> np.ndarray:
    """First point of each SVG polygon in pixel coordinates."""
    import re

    state = g.make_state(case, spline)
    svg = exports.svg_export(state.tiling, state.view.appearance(), g.EXPORT_SIZES[size])
    first = [
        [float(v) for v in pts.split()[0].split(",")]
        for pts in re.findall(r'<polygon fill="[^"]*" opacity="[^"]*" points="([^"]*)"', svg)
    ]
    return np.array(first)


@pytest.mark.parametrize(("case", "size"), [(c, s) for c, s in EXPORT_CASES if s == "Small"])
def test_png_export_size(case, size, text_fx):
    assert list(g.png_export_size(case, size)) == text_fx["exports"][f"{case}/{size}"]["png_size"]


def test_png_and_strip_render_in_every_color_mode():
    from isohedral_tiling_ui.state import ViewSettings

    state = g.make_state("preset:wavy_triangle")
    for mode in ViewSettings.param.face_color_mode.objects:  # DIFF_NO_INDEX_MODE
        view = ViewSettings(face_color_mode=mode)
        assert exports.strip_png(state.tiling, view.appearance())[:4] == b"\x89PNG"
    assert ViewSettings.param.face_color_mode.objects == ["uniform", "aspect", "coloring"]


# ----------------------------------------------------------------------------------------------------------------------
# Python snippet and JSON export


def _snippet_arguments(text: str) -> dict:
    """The keyword arguments of the ``IsohedralTiling(...)`` call of a snippet, as Python values."""
    call = next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Call) and n.keywords)
    return {k.arg: ast.literal_eval(k.value) for k in call.keywords}


@pytest.mark.parametrize("case", g.text_case_ids())
def test_python_snippet_and_json_export(case, text_fx):
    got = g.text_exports(case)
    want = text_fx["text_exports"][case]

    # DIFF_JSON_FORMAT
    assert want["config"].keys() == {"version", "tiling_type", "parameters", "edge_curves"}
    assert want["config"]["version"] == "1.0"
    assert got["config"]["version"] == "1.1"
    assert got["config"].keys() - want["config"].keys() <= {"edge_curve_modes", "frame", "description"}
    assert got["config"]["tiling_type"] == want["config"]["tiling_type"]
    assert np.allclose(got["config"]["parameters"], want["config"]["parameters"], rtol=0, atol=1e-9)
    new_curves = got["config"].get("edge_curves", {})  # absent for a tiling without curves
    assert new_curves.keys() == want["config"]["edge_curves"].keys()
    for sid, curve in want["config"]["edge_curves"].items():
        assert np.allclose(new_curves[sid], curve, rtol=0, atol=1e-9)
    assert set(got["config"].get("edge_curve_modes", {})) == set(new_curves)

    # DIFF_PYTHON_SNIPPET: same design, written for isotile.
    old_args = _snippet_arguments(want["python"])
    new_text = got["python"]
    assert "carto_flow" not in new_text
    assert "generate" not in new_text
    assert "fill_region" in new_text
    assert "margin=0.1" in new_text
    namespace: dict = {}
    exec(new_text, namespace)  # noqa: S102
    tiling = namespace["tiling"]
    assert tiling.type.number == old_args["tiling_type"]
    assert np.allclose(tiling.parameters, old_args.get("parameters", []), rtol=0, atol=1e-9)
    assert tiling.edge_curves.keys() == old_args.get("edge_curves", {}).keys()
    for sid, curve in old_args.get("edge_curves", {}).items():
        assert np.allclose(tiling.edge_curves[sid], curve, rtol=0, atol=1e-9)
    old_modes = old_args.get("edge_curve_modes", {})
    # The fixture lists the modes it was given; the port lists a mode for every curve (the default is spline).
    assert tiling.edge_curve_modes == {k: old_modes.get(k, "spline") for k in tiling.edge_curves}
    assert len(namespace["outlines"]) == len(exports.export_tiles(tiling, 100))


# ----------------------------------------------------------------------------------------------------------------------
# Loaded files

# What differs from the fixture for a design file, and the port's outcome (DIFF_LOADER).
LOADED = {
    "edge_curve_on_i_edge": {
        "notifications": [
            ["warning", "Design repaired: dropped the curve on the straight edge shape id 1"],
            ["success", "Design loaded"],
        ],
        "edge_curves": {},
    },
    "edge_mode_on_straight_edge": {
        "notifications": [
            ["warning", "Design repaired: the mode of shape id 1 has no curve; it applies when a curve is added"],
            ["success", "Design loaded"],
        ],
    },
    "edge_parameters_wrong_length": {
        "notifications": [
            ["warning", "Design repaired: used the default parameters (IH41 takes 2, the file has 1)"],
            ["success", "Design loaded"],
        ],
        "tiling_type": 41,
        "parameters": [0.230769230769, 0.5],
        "current_preset": "wrong_length",
        "history_label": "Load 'Wrong Length'",
        "filename": "wrong_length",
    },
    "edge_too_many_parameters": {
        "notifications": [
            ["warning", "Design repaired: used the default parameters (IH10 takes 0, the file has 3)"],
            ["success", "Design loaded"],
        ],
        "current_preset": "",
        "history_label": "Load 'IH10'",
        "filename": "IH10",
    },
}
REFUSED = {"edge_invalid_outline", "edge_missing_type", "edge_not_json", "edge_unknown_type"}


@pytest.mark.parametrize("name", DESIGNS)
def test_loaded_design(name, text_fx):
    got = g.load_design((g.DESIGNS / f"{name}.json").read_text())
    want = text_fx["loaded"][name]
    assert set(got) == set(want)
    skip = {"preview_n", "preview_centroid_sum", "preview_area_sum"}  # DIFF_TILE_SELECTION
    expected = {**{k: v for k, v in want.items() if k not in skip}, **LOADED.get(name, {})}
    if name in REFUSED:
        # The state is the initial state; the refusal is a notification of level error.
        assert [n[0] for n in got["notifications"]] == ["error"]
        expected["notifications"] = got["notifications"]
        if name == "edge_invalid_outline":
            assert [n[0] for n in want["notifications"]] == ["success"]  # the fixture accepted it
            assert "not a valid prototile" in got["notifications"][0][1]
            expected.update({k: got[k] for k in ("tiling_type", "parameters", "edge_curves")})
            expected.update({k: got[k] for k in ("current_preset", "history_label", "filename")})
            assert got["tiling_type"] == 10
            assert got["edge_curves"] == {}
        else:
            assert [n[0] for n in want["notifications"]] == ["error"]
            # initial state: the regular_hexagon preset, which has no parameters (the fixture reports [] for it)
            expected["parameters"] = got["parameters"]
    for k, v in expected.items():
        if k == "parameters":
            assert np.allclose(got[k], v, rtol=0, atol=1e-9), k
        else:
            assert got[k] == v, k


def test_loaded_design_outcomes_are_covered(text_fx):
    """The design files cover both outcomes: a loaded design and a refusal."""
    kinds = {n[0][0] for n in (d["notifications"] for d in text_fx["loaded"].values()) if n}
    assert kinds == {"success", "error"}
    assert len(DESIGNS) >= 40


@pytest.mark.parametrize("preset", PRESETS)
def test_saved_designs_load_to_the_preset(preset):
    """The Save Design file and the export dialog JSON of a preset load to the preset (the dialog file has no modes)."""
    reference = g.make_state(f"preset:{preset}").draft
    curves = {str(k) for k in reference.curve_dict}
    for form in ("save", "export"):
        loaded = g.load_design((g.DESIGNS / f"{form}_{preset}.json").read_text())
        assert loaded["tiling_type"] == reference.type_number
        assert np.allclose(loaded["parameters"], reference.parameters, rtol=0, atol=1e-9)
        assert loaded["edge_curves"].keys() == curves
        for sid, curve in reference.curve_dict.items():
            assert np.allclose(loaded["edge_curves"][str(sid)], curve, rtol=0, atol=1e-9)
        if form == "save":
            # the fixture lists the modes it had; the default (spline) is the mode of a curve that is not listed
            assert {k: loaded["edge_curve_modes"].get(k, "spline") for k in curves} == {
                str(k): v for k, v in reference.mode_dict.items()
            }
        else:
            assert loaded["edge_curve_modes"] == {}
