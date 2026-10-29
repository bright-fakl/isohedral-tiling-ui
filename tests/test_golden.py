"""Golden tests: the GUI against fixtures that this implementation wrote.

The fixtures (``tests/data``) record the tiles of the preview, the SVG and GeoJSON exports, the Python snippet and design
JSON, and the result of loading design files. A change in tile selection, colors, exports or loading fails a test; if the
change is intended, regenerate the fixtures with ``python -m tests.make_fixtures`` and review the diff of ``tests/data``.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from tests import golden_support as g

ATOL = 1e-9  # geometry; tile sizes are about 0.2
PIXEL_ATOL = 1e-6  # SVG coordinates are rounded to 3 decimals


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
EXPORT_CASES = g.export_cases()
DESIGNS = [p.stem for p in g.design_files()]


def test_fixture_has_the_cases(preview_fx, text_fx):
    assert len(CASES) == 26
    assert text_fx["cases"] == CASES
    assert {k.split("/")[0] for k in preview_fx} == set(CASES)
    assert set(text_fx["text_exports"]) == set(CASES)
    assert set(text_fx["loaded"]) == set(DESIGNS)


@pytest.mark.parametrize("case", CASES)
def test_preview_tiles(case, preview_fx):
    for key, val in g.preview_case(case).items():
        want = preview_fx[f"{case}/{key}"]
        assert val.shape == want.shape, f"{key}: shape {val.shape} != {want.shape}"
        if val.dtype.kind in "iub":
            assert (val == want).all(), key
        else:
            assert np.allclose(val, want, rtol=0, atol=ATOL), key


@pytest.mark.parametrize(("case", "size"), EXPORT_CASES)
def test_svg_and_geojson_export(case, size, exports_fx, text_fx):
    key = f"{case}/{size}"
    want_text = text_fx["exports"][key]
    for k, v in g.export_summary(case, size).items():
        if isinstance(v, np.ndarray):
            want = exports_fx[f"{key}/{k}"]
            assert v.shape == want.shape, k
            if v.dtype.kind in "iub":
                assert (v == want).all(), k
            else:
                assert np.allclose(v, want, rtol=0, atol=PIXEL_ATOL), k
        else:
            assert v == want_text[k], k
    assert list(g.png_export_size(case, size)) == want_text["png_size"]


@pytest.mark.parametrize("case", CASES)
def test_python_snippet_and_design_json(case, text_fx):
    got = g.text_exports(case)
    want = text_fx["text_exports"][case]
    assert got["python"] == want["python"]
    assert got["config"] == want["config"]


@pytest.mark.parametrize("name", DESIGNS)
def test_loaded_design(name, text_fx):
    got = g.load_design((g.DESIGNS / f"{name}.json").read_text())
    want = text_fx["loaded"][name]
    assert set(got) == set(want)
    for k, v in got.items():
        if k in ("parameters", "preview_centroid_sum", "preview_area_sum"):
            assert np.allclose(v, want[k], rtol=0, atol=ATOL), k
        else:
            assert v == want[k], k


def test_loaded_design_outcomes_are_covered(text_fx):
    """The design files cover a loaded design, a repaired one and a refusal."""
    levels = {n[0] for d in text_fx["loaded"].values() for n in d["notifications"]}
    assert levels == {"success", "warning", "error"}
    assert len(DESIGNS) >= 17
