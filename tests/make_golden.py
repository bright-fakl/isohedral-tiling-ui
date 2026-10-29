"""Write the golden fixtures from the current implementation.

Run once in an environment with the pinned carto-flow and tactile (see ``ci/constraints.txt``) and the GUI installed:

    MPLBACKEND=Agg python -m tests.make_golden

Outputs (all in ``tests/data``):
    designs/*.json         design files of the kinds the GUI writes or has to survive
    golden_preview.npz     per case (81 types, 15 presets): tiles of the preview call; per preset: the strip image
    golden_exports.npz     SVG and GeoJSON summaries of the 15 presets at 50, 100 and 200 tiles
    golden_text.json       Python snippet and JSON of the export dialog per case, PNG sizes, loaded-file results,
                           versions of the libraries that produced the fixtures
"""

from __future__ import annotations

import json
import os
from importlib.metadata import version

import numpy as np

os.environ.setdefault("MPLBACKEND", "Agg")

from tests import golden_support as g


def write_designs() -> None:
    from carto_flow.symbol_cartogram.tiling import IsohedralTiling as IT

    g.DESIGNS.mkdir(parents=True, exist_ok=True)
    for old in g.DESIGNS.glob("*.json"):
        old.unlink()

    def dump(name: str, obj) -> None:
        text = obj if isinstance(obj, str) else json.dumps(obj, indent=2)
        (g.DESIGNS / f"{name}.json").write_text(text + "\n")

    def form(tiling, with_modes: bool, desc: str) -> dict:
        cfg = {
            "version": "1.0",
            "tiling_type": tiling._type,
            "parameters": list(tiling._ih.parameters),
            "edge_curves": {str(k): [list(p) for p in v] for k, v in (tiling._edge_curves or {}).items()},
        }
        if with_modes:
            cfg["edge_curve_modes"] = {str(k): v for k, v in tiling._edge_curve_modes.items()}
            cfg["description"] = desc
            cfg["metadata"] = {"created": "2026-01-01T00:00:00+00:00"}
        else:
            cfg["metadata"] = {"created": "2026-01-01T00:00:00+00:00"}
        return cfg

    # The two forms of the GUI for each preset: Save Design (modes, description) and the export dialog (neither).
    for name in g.preset_names():
        t = IT.from_preset(name)
        dump(f"save_{name}", form(t, True, name))
        dump(f"export_{name}", form(t, False, name))

    puzzle = IT.from_preset("puzzle_piece")
    curve = [list(p) for p in puzzle._edge_curves[0]]

    # A type with an I edge: a curve on it is something the editor does not offer but a file can contain.
    i_type = next(n for n in g.type_numbers() if "I" in IT.type_info(n)["edge_shapes"])
    i_info = IT.type_info(i_type)
    i_shape = next(e["shape_id"] for e in i_info["edges"] if e["shape_type"] == "I")

    dump("edge_type_only", {"version": "1.0", "tiling_type": 10})
    dump(
        "edge_parameters_wrong_length",
        {"version": "1.0", "tiling_type": 41, "parameters": [0.3], "description": "wrong_length"},
    )
    dump(
        "edge_mode_on_straight_edge",
        {
            "version": "1.0",
            "tiling_type": 41,
            "parameters": [0.230769230769, 0.5],
            "edge_curves": {"0": curve},
            "edge_curve_modes": {"0": "spline", "1": "linear"},
            "description": "mode_without_curve",
        },
    )
    dump(
        "edge_curve_on_i_edge",
        {
            "version": "1.0",
            "tiling_type": i_type,
            "parameters": list(i_info["default_parameters"]),
            "edge_curves": {str(i_shape): [[0, 0], [0.3, 0.1], [0.7, -0.1], [1, 0]]},
            "description": "curve_on_i_edge",
        },
    )
    dump(
        "edge_invalid_outline",
        {
            "version": "1.0",
            "tiling_type": 41,
            "parameters": [0.230769230769, 0.5],
            "edge_curves": {"0": [[0, 0], [0.2, 0.9], [0.8, -0.9], [1, 0]], "1": [[0, 0], [0.5, 0.9], [1, 0]]},
            "description": "invalid_outline",
        },
    )
    dump(
        "edge_linear_curve_without_mode",
        {
            "version": "1.0",
            "tiling_type": 41,
            "parameters": [0.230769230769, 0.5],
            "edge_curves": {"0": [[0, 0], [0.5, 0.2], [1, 0]], "1": [[0, 0], [0.5, 0.2], [1, 0]]},
            "description": "linear_without_mode",
        },
    )
    dump(
        "edge_version_1_1",
        {
            "version": "1.1",
            "tiling_type": 41,
            "parameters": [0.230769230769, 0.5],
            "edge_curves": {"0": curve, "1": curve},
            "edge_curve_modes": {"0": "spline", "1": "spline"},
            "frame": None,
            "description": "version_1_1",
        },
    )
    dump("edge_unknown_type", {"version": "1.0", "tiling_type": 999, "parameters": []})
    dump("edge_missing_type", {"version": "1.0", "parameters": [0.5]})
    dump("edge_not_json", "this is not json")
    dump("edge_too_many_parameters", {"version": "1.0", "tiling_type": 10, "parameters": [0.1, 0.2, 0.3]})


def main() -> None:
    write_designs()
    arrays: dict[str, np.ndarray] = {}
    cases = g.case_ids()
    for case in cases:
        for key, val in g.preview_case(case).items():
            arrays[f"{case}/{key}"] = val
    for name in g.preset_names():
        arrays[f"strip/{name}"] = g.strip_png(g.make_state(f"preset:{name}"))
    np.savez_compressed(g.DATA / "golden_preview.npz", **arrays)

    exp_arrays: dict[str, np.ndarray] = {}
    exp_text: dict[str, dict] = {}
    for case, size in g.export_cases():
        summ = g.export_summary(case, size)
        key = f"{case}/{size}"
        exp_text[key] = {}
        for k, v in summ.items():
            if isinstance(v, np.ndarray):
                exp_arrays[f"{key}/{k}"] = v
            else:
                exp_text[key][k] = v
        exp_text[key]["png_size"] = list(g.png_export_size(case, size))
    np.savez_compressed(g.DATA / "golden_exports.npz", **exp_arrays)

    text = {
        "versions": {p: version(p) for p in ("carto-flow", "tactile", "numpy", "shapely", "matplotlib", "svgwrite")},
        "cases": cases,
        "text_exports": {case: g.text_exports(case) for case in cases},
        "exports": exp_text,
        "loaded": {f.stem: g.load_design(f.read_text()) for f in g.design_files()},
    }
    (g.DATA / "golden_text.json").write_text(json.dumps(text, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
