"""Tests of the tile selection, colors and exports (exports.py): no Panel."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from io import BytesIO

import matplotlib.image as mpimg
import numpy as np
import pytest
from isotile import IsohedralTiling, get_type, list_presets
from matplotlib import colormaps
from matplotlib.colors import to_hex
from matplotlib.path import Path as MplPath
from matplotlib.transforms import Bbox

from isohedral_tiling_ui import exports
from isohedral_tiling_ui.editor_model import load_design
from isohedral_tiling_ui.exports import Appearance

PRESETS = list(list_presets())


def overlapping(tiles, bounds) -> np.ndarray:
    box = Bbox([[bounds[0] + 1e-9, bounds[1] + 1e-9], [bounds[2] - 1e-9, bounds[3] - 1e-9]])
    return np.array([MplPath(o).intersects_bbox(box, filled=True) for o in tiles.outlines()])


def test_the_strip_selection_misses_no_tile_of_the_window():
    """The straight outline with a margin of 0.25 selects every tile that the curved outline selects in the window."""
    for name in PRESETS:
        tiling = IsohedralTiling.from_preset(name)
        straight = exports.strip_tiles(tiling)
        curved = tiling.fill_region(exports.STRIP_BOUNDS, n_tiles=exports.STRIP_N_TILES, margin=exports.STRIP_MARGIN)
        in_window = curved.select(overlapping(curved, exports.STRIP_BOUNDS))
        straight_cells = {(tuple(c), a) for c, a in zip(straight.cell.tolist(), straight.aspect.tolist(), strict=True)}
        wanted = {(tuple(c), a) for c, a in zip(in_window.cell.tolist(), in_window.aspect.tolist(), strict=True)}
        assert wanted <= straight_cells, name


def test_export_tiles_cover_the_square():
    tiling = IsohedralTiling.from_preset("flowers")
    tiles = exports.export_tiles(tiling, 50)
    assert 50 <= len(tiles) <= 120
    assert tiles.centroids.min() > -3.5
    assert tiles.centroids.max() < 3.5


@pytest.mark.parametrize("number", [1, 10, 41, 84, 43])
def test_aspect_mode_gives_one_color_per_aspect(number):
    tiling = IsohedralTiling(number)
    tiles = exports.export_tiles(tiling, 100)
    colors = exports.face_colors(tiles, Appearance("aspect"))
    cmap = colormaps["tab10"]
    for aspect, color in zip(tiles.aspect.tolist(), colors, strict=True):
        assert color == to_hex(cmap(aspect))
    assert len(set(colors)) == get_type(number).n_aspects


def test_coloring_mode_uses_the_coloring_of_the_type():
    tiles = exports.export_tiles(IsohedralTiling(10), 100)
    colors = exports.face_colors(tiles, Appearance("coloring"))
    index = tiles.colors()
    assert len(set(colors)) == index.max() + 1
    assert len(set(zip(index.tolist(), colors, strict=True))) == index.max() + 1  # one color per index


def test_uniform_mode_and_colormaps():
    tiles = exports.export_tiles(IsohedralTiling(84), 50)
    assert set(exports.face_colors(tiles, Appearance("uniform", "#112233"))) == {"#112233"}
    for name in ("Viridis", "Plasma", "Inferno", "Turbo"):
        colors = exports.face_colors(tiles, Appearance("aspect", face_colormap=name))
        assert len(set(colors)) == get_type(84).n_aspects
    single = exports.export_tiles(IsohedralTiling(10), 50)
    assert len(set(exports.face_colors(single, Appearance("aspect", face_colormap="Viridis")))) == 1


def test_unknown_color_mode_is_an_error():
    with pytest.raises(ValueError, match="unknown face color mode"):
        exports.face_colors(exports.export_tiles(IsohedralTiling(10), 50), Appearance("index"))


@pytest.mark.parametrize("name", ["square", "puzzle_piece", "tree"])
def test_svg_has_one_polygon_per_tile(name):
    tiling = IsohedralTiling.from_preset(name)
    appearance = Appearance("aspect", edge_color="#112233")
    root = ET.fromstring(exports.svg_export(tiling, appearance, 50))  # noqa: S314 (own output)
    ns = "{http://www.w3.org/2000/svg}"
    polygons = root.findall(f"{ns}polygon")
    tiles = exports.export_tiles(tiling, 50)
    assert len(polygons) == len(tiles)
    assert root.get("viewBox") == "0 0 800 800"
    assert {p.get("stroke") for p in polygons} == {"#112233"}
    assert [p.get("fill") for p in polygons] == exports.face_colors(tiles, appearance)
    outline_points = tiling.prototile().shape[0]
    assert all(len(p.get("points").split()) == outline_points for p in polygons)


@pytest.mark.parametrize("name", PRESETS)
def test_geojson_is_valid_and_complete(name):
    tiling = IsohedralTiling.from_preset(name)
    appearance = Appearance("coloring")
    data = json.loads(exports.geojson_export(tiling, appearance, 50))
    tiles = exports.export_tiles(tiling, 50)
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == len(tiles)
    keys = {"tile_index", "aspect", "cell_u", "cell_v", "color_index", "color", "center_x", "center_y"}
    colors = exports.face_colors(tiles, appearance)
    for i, feature in enumerate(data["features"]):
        p = feature["properties"]
        assert set(p) == keys
        assert p["tile_index"] == i
        assert (p["cell_u"], p["cell_v"]) == tuple(tiles.cell[i])
        assert p["aspect"] == tiles.aspect[i]
        assert p["color_index"] == tiles.colors()[i]
        assert p["color"] == colors[i]
        assert (p["center_x"], p["center_y"]) == pytest.approx(tuple(tiles.centroids[i]), abs=1e-6)
        ring = np.array(feature["geometry"]["coordinates"][0])
        assert (ring[0] == ring[-1]).all()


def test_png_sizes():
    tiling = IsohedralTiling.from_preset("wavy_triangle")
    strip = mpimg.imread(BytesIO(exports.strip_png(tiling, Appearance())), format="png")
    assert strip.shape[1] / strip.shape[0] == pytest.approx(300 / 90, rel=0.05)
    square = mpimg.imread(BytesIO(exports.png_export(tiling, Appearance(), 50)), format="png")
    assert abs(square.shape[0] - square.shape[1]) <= 1


def test_design_json_round_trips_through_the_loader():
    tiling = IsohedralTiling.from_preset("arrow_quad")
    data = json.loads(exports.design_json(tiling, "mine"))
    assert data["version"] == "1.1"
    assert data["description"] == "mine"
    assert data["edge_curve_modes"] == {"0": "linear", "1": "linear"}
    assert data["metadata"]["created"].endswith("+00:00")
    draft, notes = load_design(data)
    assert notes == []
    assert np.allclose(draft.tiling().prototile(), tiling.prototile())
    assert IsohedralTiling.from_dict(data).edge_curve_modes == tiling.edge_curve_modes  # isotile reads it as well


@pytest.mark.parametrize("name", PRESETS)
def test_python_snippet_rebuilds_the_tiling(name):
    tiling = IsohedralTiling.from_preset(name)
    code = exports.python_snippet(tiling)
    assert code.splitlines()[0] == "from isotile import IsohedralTiling"
    namespace: dict = {}
    exec(code, namespace)  # noqa: S102
    assert np.allclose(namespace["tiling"].prototile(), tiling.prototile())
    assert namespace["tiling"].edge_curve_modes == tiling.edge_curve_modes
    assert len(namespace["outlines"]) == len(exports.export_tiles(tiling, 100))
