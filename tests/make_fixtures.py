"""Write the golden fixtures from the current implementation.

Run from the repository root with the GUI installed, only when a change of the output is intended:

    MPLBACKEND=Agg python -m tests.make_fixtures

Outputs (all in ``tests/data``):
    golden_preview.npz     per case (15 presets, 11 types): tiles of the preview call
    golden_exports.npz     SVG and GeoJSON summaries of 4 presets at 50 tiles
    golden_text.json       Python snippet and design JSON of the cases, PNG sizes, results of loading the design files,
                           versions of the libraries that wrote the fixtures
"""

from __future__ import annotations

import json
import os
from importlib.metadata import version

import numpy as np

os.environ.setdefault("MPLBACKEND", "Agg")

from tests import golden_support as g


def main() -> None:
    cases = g.case_ids()
    preview: dict[str, np.ndarray] = {}
    for case in cases:
        for key, val in g.preview_case(case).items():
            preview[f"{case}/{key}"] = val
    np.savez_compressed(g.DATA / "golden_preview.npz", **preview)

    exp_arrays: dict[str, np.ndarray] = {}
    exp_text: dict[str, dict] = {}
    for case, size in g.export_cases():
        key = f"{case}/{size}"
        exp_text[key] = {}
        for k, v in g.export_summary(case, size).items():
            if isinstance(v, np.ndarray):
                exp_arrays[f"{key}/{k}"] = v
            else:
                exp_text[key][k] = v
        exp_text[key]["png_size"] = list(g.png_export_size(case, size))
    np.savez_compressed(g.DATA / "golden_exports.npz", **exp_arrays)

    text = {
        "versions": {p: version(p) for p in ("isotile", "numpy", "matplotlib", "svgwrite")},
        "cases": cases,
        "text_exports": {case: g.text_exports(case) for case in cases},
        "exports": exp_text,
        "loaded": {f.stem: g.load_design(f.read_text()) for f in g.design_files()},
    }
    (g.DATA / "golden_text.json").write_text(json.dumps(text, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
