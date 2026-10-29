"""Golden tests: the current GUI against fixtures that the current implementation wrote.

The fixtures (``tests/data``) record the behavior of the GUI before the port to isotile: the tiles of the preview, the
strip image, the SVG and GeoJSON exports, the Python snippet and JSON export, and the result of loading design files.
Regenerate them only on purpose, with ``python -m tests.make_golden`` in an environment with the pinned libraries.
"""

from __future__ import annotations

import json
from importlib.metadata import version

import numpy as np
import pytest

from tests import golden_support as g

ATOL = 1e-9  # geometry; tile sizes are about 0.2
SAMPLING_ATOL = 1e-6  # numbers inside text files


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


# ----------------------------------------------------------------------------------------------------------------------
# Fixture integrity


def test_fixture_has_96_cases(preview_fx, text_fx):
    assert len(CASES) == 96
    assert text_fx["cases"] == CASES
    for case in CASES:
        assert f"{case}/centroids" in preview_fx
    assert len(PRESETS) == 15
    assert len([c for c in CASES if c.startswith("type:")]) == 81


def test_environment_matches_fixture(text_fx):
    """The fixtures are exact only for the libraries that wrote them; a different version needs a regeneration."""
    now = {p: version(p) for p in text_fx["versions"]}
    for pkg in ("carto-flow", "tactile"):
        assert now[pkg] == text_fx["versions"][pkg]


# ----------------------------------------------------------------------------------------------------------------------
# Preview


@pytest.mark.parametrize("case", CASES)
def test_preview_tiles(case, preview_fx):
    got = g.preview_case(case)
    for key, val in got.items():
        want = preview_fx[f"{case}/{key}"]
        assert val.shape == want.shape, f"{key}: shape {val.shape} != {want.shape}"
        if key == "flipped":
            assert (val == want).all()
        elif key == "rotation":
            assert np.allclose(np.mod(val, 360), np.mod(want, 360), rtol=0, atol=1e-6)
        else:
            assert np.allclose(val, want, rtol=0, atol=ATOL), key


@pytest.mark.parametrize("name", PRESETS)
def test_strip_image(name, preview_fx):
    got = g.strip_png(g.make_state(f"preset:{name}"))
    # Anti-aliasing differs between matplotlib versions; the shapes must agree to within 2 percent of the pixels.
    assert g.pixel_difference(got, preview_fx[f"strip/{name}"]) <= 0.02


# ----------------------------------------------------------------------------------------------------------------------
# Exports


@pytest.mark.parametrize(("case", "size"), EXPORT_CASES)
def test_svg_and_geojson_export(case, size, exports_fx, text_fx):
    key = f"{case}/{size}"
    got = g.export_summary(case, size)
    want_text = text_fx["exports"][key]
    for k, v in got.items():
        if isinstance(v, np.ndarray):
            want = exports_fx[f"{key}/{k}"]
            assert v.shape == want.shape, k
            if v.dtype.kind in "iub":
                assert (v == want).all(), k
            else:
                assert np.allclose(v, want, rtol=0, atol=1e-6), k
        elif k == "svg_head":
            assert g.text_skeleton(v) == g.text_skeleton(want_text[k])
            assert g.numbers_close(v, want_text[k])
        else:
            assert v == want_text[k], k


@pytest.mark.parametrize(("case", "size"), [(c, s) for c, s in EXPORT_CASES if s == "Small"])
def test_png_export_size(case, size, text_fx):
    assert list(g.png_export_size(case, size)) == text_fx["exports"][f"{case}/{size}"]["png_size"]


@pytest.mark.parametrize("case", CASES)
def test_python_snippet_and_json_export(case, text_fx):
    got = g.text_exports(case)
    want = text_fx["text_exports"][case]
    assert got["python"] == want["python"]
    assert got["config"].keys() == want["config"].keys()
    assert got["config"]["tiling_type"] == want["config"]["tiling_type"]
    assert np.allclose(got["config"]["parameters"], want["config"]["parameters"], rtol=0, atol=1e-9)
    assert got["config"]["edge_curves"] == want["config"]["edge_curves"]


# ----------------------------------------------------------------------------------------------------------------------
# Loaded files


@pytest.mark.parametrize("name", DESIGNS)
def test_loaded_design(name, text_fx):
    got = g.load_design((g.DESIGNS / f"{name}.json").read_text())
    want = text_fx["loaded"][name]
    assert set(got) == set(want)
    for k, v in got.items():
        if k in ("parameters", "preview_centroid_sum", "preview_area_sum"):
            assert np.allclose(v, want[k], rtol=0, atol=1e-6), k
        else:
            assert v == want[k], k


def test_loaded_design_outcomes_are_covered(text_fx):
    """The design files cover both outcomes: a loaded design and a refusal."""
    kinds = {n[0][0] for n in (d["notifications"] for d in text_fx["loaded"].values()) if n}
    assert kinds == {"success", "error"}
    assert len(DESIGNS) >= 40
