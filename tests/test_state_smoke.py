"""Headless smoke test of the state object: types, presets, parameters, curves, undo and redo."""

from __future__ import annotations

from isohedral_tiling_ui.state import TilingState


def test_initial_state():
    s = TilingState()
    assert s.tiling_type == 10
    assert s._tiling is not None
    assert len(s._history) == 1
    assert not s.reset_available


def test_type_change_rebuilds_tiling_with_default_parameters():
    s = TilingState()
    s.tiling_type = 41
    assert s._tiling._type == 41
    assert len(s._type_info["parameters"]) == 2


def test_parameter_change_enables_reset_and_reset_restores():
    s = TilingState()
    s.load_preset("square")
    initial = list(s.parameters)
    s.parameters = [initial[0] * 0.5, initial[1]]
    assert s.reset_available
    s.reset_parameters()
    assert s.parameters == initial
    assert not s.reset_available


def test_load_preset_resets_history():
    s = TilingState()
    s.load_preset("puzzle_piece")
    assert s.current_preset == "puzzle_piece"
    assert len(s._history) == 1
    assert s._history_idx == 0
    assert set(s.edge_curves) == {0, 1}
    assert s.edge_curve_modes == {0: "spline", 1: "spline"}


def test_undo_redo_round_trip():
    s = TilingState()
    s.load_preset("square")
    first = list(s.parameters)
    s.parameters = [0.5, 0.5]
    s.push_history("edit")
    assert len(s._history) == 2
    s.undo()
    assert s.parameters == first
    s.redo()
    assert s.parameters == [0.5, 0.5]
