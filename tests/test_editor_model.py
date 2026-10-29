"""Tests of the state layer (editor_model.py): no Panel, no browser."""

from __future__ import annotations

import json

import numpy as np
import pytest
from isotile import IsohedralTiling, get_type, is_symmetric, list_presets, tiling_types

from isohedral_tiling_ui import editor_model as em
from isohedral_tiling_ui import exports
from isohedral_tiling_ui.editor_model import Draft, EditorModel


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def with_curve_on_ih14() -> Draft:
    """IH14 with a curve on shape id 0 that is valid as lines and invalid as a spline."""
    curve = [(0, 0), (0.25, -0.198), (0.5, -0.34), (0.75, -0.354), (1, 0)]
    return Draft.of(14, curves={0: curve}, modes={0: "linear"})


# ----------------------------------------------------------------------------------------------------------------------
# Draft


def test_draft_is_frozen_hashable_and_compares_by_value():
    d = Draft.of(41, [0.2, 0.5], {0: [(0, 0), (0.5, 0.1), (1, 0)]})
    same = Draft.of(41, [0.2, 0.5], {0: [[0, 0], [0.5, 0.1], [1, 0]]})
    assert d == same
    assert hash(d) == hash(same)
    with pytest.raises(AttributeError):
        d.type_number = 3  # type: ignore[misc]
    changed = d.with_parameters([0.3, 0.5])
    assert changed != d
    assert d.parameters == (0.2, 0.5)  # the original is unchanged


def test_straight_curve_is_not_stored():
    d = Draft.of(41, curves={0: [(0, 0), (0.5, 0.1), (1, 0)]})
    assert d.curve(0) != em.STRAIGHT
    assert d.with_curve(0, [(0, 0), (1, 0)]).curves == ()
    assert d.curve(1) == em.STRAIGHT


def test_report_and_tiling_are_derived_from_the_draft():
    d = Draft.of(41)
    assert d.report().valid
    assert isinstance(d.tiling(), IsohedralTiling)
    assert d.tiling() is d.tiling()  # cached by draft
    bad = Draft.of(41, curves={0: [(0, 0), (0.8, 0.5), (0.2, 0.5), (1, 0)]})
    assert not bad.report().valid
    with pytest.raises(ValueError, match="invalid prototile"):
        bad.tiling()


def test_malformed_draft_is_reported_not_raised():
    wrong_length = Draft(41, (0.5,))
    assert not wrong_length.report().valid
    on_i_edge = Draft.of(14, curves={1: [(0, 0), (0.5, 0.1), (1, 0)]})
    assert get_type(14).shape_types[1] == "I"
    assert not on_i_edge.report().valid
    assert "straight" in on_i_edge.report().message


def test_every_preset_loads_and_round_trips():
    for name in list_presets():
        m = EditorModel()
        m.load_preset(name)
        d = m.draft
        data = json.loads(exports.design_json(d.tiling(), name))
        again, notes = em.load_design(data)
        assert notes == []
        assert again.type_number == d.type_number
        assert dict(again.curves).keys() == dict(d.curves).keys()
        assert np.allclose(again.tiling().prototile(), d.tiling().prototile())
        assert again.tiling().edge_curve_modes == d.tiling().edge_curve_modes


def test_every_type_loads_at_its_defaults():
    for n in tiling_types():
        m = EditorModel()
        m.load_type(n)
        assert m.draft.report().valid
        assert not m.can_undo
        assert not m.can_reset
        assert m.baseline_label == f"IH{n}"


# ----------------------------------------------------------------------------------------------------------------------
# The model: commits, history


def test_invalid_parameter_is_not_committed_and_the_report_names_edges():
    n = next(n for n in tiling_types() if get_type(n).n_params and np.isfinite(get_type(n).safe_ranges[0][1]))
    m = EditorModel()
    m.load_type(n)
    p = list(get_type(n).default_params)
    p[0] = get_type(n).safe_ranges[0][1] + 0.01
    result = m.commit("x", m.draft.with_parameters(p))
    assert not result.committed
    assert result.report.bad_edges
    assert m.draft.parameters == tuple(get_type(n).default_params)
    assert not m.can_undo


def test_probe_does_not_change_the_state():
    m = EditorModel()
    m.load_type(41)
    d = m.draft.with_curve(0, [(0, 0), (0.8, 0.5), (0.2, 0.5), (1, 0)])
    assert not m.probe(d).valid
    assert m.draft.curves == ()
    assert m.labels == ["IH41"]


def test_history_coalesces_within_the_window_and_undo_redo_walk_the_entries():
    clock = Clock()
    m = EditorModel(clock=clock)
    m.load_type(41)
    d = m.draft
    for i, (t, key) in enumerate([(0.0, "a"), (0.4, "a"), (0.6, "b"), (5.0, "b")], start=1):
        clock.t = t
        m.commit(f"edit {i}", d.with_parameters([0.2 + 0.01 * i, 0.5]), key=key, window=1.0)
    assert m.labels == ["IH41", "edit 2", "edit 3", "edit 4"]  # edit 2 replaced edit 1; 5.0 s later starts a new entry
    m.undo()
    assert m.draft.parameters[0] == pytest.approx(0.23)
    m.undo()
    assert m.draft.parameters[0] == pytest.approx(0.22)
    m.undo()
    assert m.draft == m.baseline
    assert not m.can_undo
    assert m.can_redo
    assert m.n_redo == 3
    m.redo()
    assert m.undo_label == "edit 2"  # what Undo would undo
    assert m.redo_label == "edit 3"


def test_an_edit_after_undo_does_not_overwrite_the_undone_entry():
    clock = Clock()
    m = EditorModel(clock=clock)
    m.load_type(41)
    d = m.draft
    m.commit("one", d.with_parameters([0.25, 0.5]), key="k", window=10.0)
    m.commit("two", d.with_parameters([0.3, 0.5]), key="k", window=10.0)  # coalesces into "one"
    assert m.labels == ["IH41", "two"]
    m.undo()
    m.commit("three", d.with_parameters([0.35, 0.5]), key="k", window=10.0)
    assert m.labels == ["IH41", "three"]
    m.commit("four", d.with_parameters([0.4, 0.5]), key="other")
    m.undo()
    m.commit("five", d.with_parameters([0.45, 0.5]), key="other", window=10.0)
    assert m.labels == ["IH41", "three", "five"]  # the entry "three" survives the undo


def test_reset_returns_to_the_baseline_and_is_a_history_entry():
    m = EditorModel()
    m.load_preset("puzzle_piece")
    assert not m.can_reset
    m.commit("edit", m.draft.with_parameters([0.3, 0.4]))
    m.commit("mode", m.draft.with_mode(0, "linear"))
    assert m.can_reset
    assert m.reset().committed
    assert m.draft == m.baseline
    assert m.labels[-1] == "Reset to Puzzle Piece"
    assert not m.can_reset
    m.undo()
    assert m.draft.mode(0) == "linear"  # Reset restored the modes; Undo brings them back


def test_load_starts_a_new_history_and_baseline():
    m = EditorModel()
    m.commit("edit", m.draft.with_curve(0, [(0, 0), (0.5, 0.1), (1, 0)]))
    m.load_type(4)
    assert m.labels == ["IH4"]
    assert m.baseline == m.draft
    m.load_preset("tree")
    assert m.labels == ["Load preset 'Tree'"]
    assert m.baseline_label == "Tree"


def test_mode_change_is_checked():
    m = EditorModel(with_curve_on_ih14())
    assert m.draft.report().valid
    result = m.commit("spline", m.draft.with_mode(0, "spline"))
    assert not result.committed
    assert result.report.bad_edges
    assert m.draft.mode(0) == "linear"


# ----------------------------------------------------------------------------------------------------------------------
# Curve edits


def test_move_node_keeps_the_symmetry():
    clock = Clock()
    m = EditorModel(clock=clock)
    m.load_preset("wavy_triangle")  # IH84: S edges
    c0 = m.draft.curve(0)
    shape = em.shape_of(84, 0)
    assert shape == "S"
    c1 = em.move_node(c0, shape, 1, c0[1][0], c0[1][1] + 0.01)
    assert is_symmetric(c1, shape)
    assert c1[1][1] == pytest.approx(c0[1][1] + 0.01)
    assert c1[-2] == pytest.approx((1 - c1[1][0], -c1[1][1]))  # point-symmetric partner


def test_add_node_on_a_u_curve_inserts_the_pair():
    curve = ((0, 0), (0.2, 0.1), (0.5, 0.15), (0.8, 0.1), (1, 0))
    got = em.add_node(curve, "U", 0.35, 0.2)
    assert got == ((0, 0), (0.2, 0.1), (0.35, 0.2), (0.5, 0.15), (0.65, 0.2), (0.8, 0.1), (1, 0))
    assert em.remove_node(got, "U", 2) == ((0, 0), (0.2, 0.1), (0.5, 0.15), (0.8, 0.1), (1, 0))


def test_add_and_remove_node_on_s_and_j_curves():
    s = ((0, 0), (0.25, 0.1), (0.5, 0), (0.75, -0.1), (1, 0))
    added = em.add_node(s, "S", 0.1, 0.05)
    assert added == ((0, 0), (0.1, 0.05), (0.25, 0.1), (0.5, 0), (0.75, -0.1), (0.9, -0.05), (1, 0))
    assert em.remove_node(added, "S", 2) == ((0, 0), (0.1, 0.05), (0.5, 0), (0.9, -0.05), (1, 0))
    j = ((0, 0), (0.5, 0.1), (1, 0))
    assert em.add_node(j, "J", 0.2, 0.3) == ((0, 0), (0.2, 0.3), (0.5, 0.1), (1, 0))
    assert em.remove_node(j, "J", 1) == ((0, 0), (1, 0))
    assert em.remove_node(((0, 0), (1, 0)), "J", 1) == ((0, 0), (1, 0))  # nothing to remove
    assert em.remove_node(j, "J", 0) == j  # end points stay


def test_removing_the_middle_node_of_a_u_curve_leaves_an_even_curve():
    curve = ((0, 0), (0.2, 0.1), (0.5, 0.15), (0.8, 0.1), (1, 0))
    got = em.remove_node(curve, "U", 2)
    assert got == ((0, 0), (0.2, 0.1), (0.8, 0.1), (1, 0))
    assert is_symmetric(got, "U")
    assert em.add_node(got, "U", 0.1, 0.2) == ((0, 0), (0.1, 0.2), (0.2, 0.1), (0.8, 0.1), (0.9, 0.2), (1, 0))


@pytest.mark.parametrize("shape", ["J", "U", "S"])
def test_add_and_remove_node_keep_the_symmetry(shape):
    base = ((0, 0), (0.25, 0.1), (0.5, 0.0), (0.75, -0.1), (1, 0))
    c = em.move_node(base, shape, 1, 0.25, 0.1)
    c2 = em.add_node(c, shape, 0.1, 0.05)
    assert len(c2) == len(c) + (1 if shape == "J" else 2)
    assert is_symmetric(c2, shape)
    i = next(k for k in range(len(c2)) if em.editable(shape, len(c2), k))
    c3 = em.remove_node(c2, shape, i)
    assert len(c3) == len(c2) - (1 if shape == "J" else 2)
    assert is_symmetric(c3, shape)


@pytest.mark.parametrize(
    ("shape", "n", "editable"),
    [
        ("J", 5, [1, 2, 3]),
        ("U", 5, [1, 2]),  # the middle node moves in y
        ("S", 5, [1]),  # the middle node is fixed at (0.5, 0)
        ("U", 4, [1]),
        ("S", 6, [1, 2]),
        ("I", 2, []),
    ],
)
def test_editable_nodes(shape, n, editable):
    assert [i for i in range(n) if em.editable(shape, n, i)] == editable


def test_random_sequences_of_edits_keep_a_valid_symmetric_draft():
    rng = np.random.default_rng(3)
    for number in (41, 84, 4, 21, 43):
        m = EditorModel()
        m.load_type(number)
        t = get_type(number)
        ids = [i for i, s in enumerate(t.shape_types) if s != "I"]
        for _ in range(60):
            sid = int(rng.choice(ids))
            shape = t.shape_types[sid]
            curve = m.draft.curve(sid)
            op = rng.integers(4)
            if op == 0:
                i = int(rng.integers(1, max(len(curve) - 1, 2)))
                new = em.move_node(
                    curve, shape, min(i, len(curve) - 1), float(rng.uniform(0.1, 0.9)), float(rng.normal(0, 0.1))
                )
            elif op == 1:
                new = em.add_node(curve, shape, float(rng.uniform(0.05, 0.95)), float(rng.normal(0, 0.1)))
            elif op == 2 and len(curve) > 2:
                new = em.remove_node(curve, shape, int(rng.integers(1, len(curve) - 1)))
            else:
                new = curve
            m.commit("random", m.draft.with_curve(sid, new))
            assert m.draft.report().valid
            for k, c in m.draft.curves:
                assert is_symmetric(c, t.shape_types[k])
        while m.can_undo:
            m.undo()
            assert m.draft.report().valid


# ----------------------------------------------------------------------------------------------------------------------
# Reading designs


def test_curve_on_straight_edge_is_dropped_by_the_loader_and_rejected_by_isotile():
    n = next(n for n in tiling_types() if "I" in get_type(n).shape_types)
    sid = get_type(n).shape_types.index("I")
    data = {
        "tiling_type": n,
        "parameters": list(get_type(n).default_params),
        "edge_curves": {str(sid): [[0, 0], [0.5, 0.1], [1, 0]]},
    }
    with pytest.raises(ValueError, match="straight"):
        IsohedralTiling.from_dict(data)
    draft, notes = em.load_design(data)
    assert draft.curves == ()
    assert len(notes) == 1
    assert f"shape id {sid}" in notes[0]


def test_parameters_of_the_wrong_length_become_the_defaults():
    draft, notes = em.load_design({"tiling_type": 41, "parameters": [0.3]})
    assert draft.parameters == tuple(get_type(41).default_params)
    assert len(notes) == 1
    assert "IH41 takes 2" in notes[0]
    assert em.load_design({"tiling_type": 41, "parameters": []})[1] == []  # no parameters: the defaults, no note
    assert em.load_design({"tiling_type": 41})[1] == []


def test_a_mode_without_a_curve_is_kept_for_a_curve_added_later():
    data = {"tiling_type": 41, "edge_curve_modes": {"0": "linear", "1": "spline"}}
    draft, notes = em.load_design(data)
    assert draft.mode(0) == "linear"
    assert len(notes) == 1
    assert "shape id 0" in notes[0]
    assert draft.tiling().edge_curve_modes == {}
    with_curve = draft.with_curve(0, [(0, 0), (0.5, 0.1), (1, 0)])
    assert with_curve.tiling().edge_curve_modes == {0: "linear"}


def test_loaded_curves_get_the_symmetry_of_their_edge_shape():
    t = get_type(84)
    sid = t.shape_types.index("S")
    draft, _ = em.load_design({"tiling_type": 84, "edge_curves": {str(sid): [[0, 0], [0.2, 0.1], [0.4, 0.1], [1, 0]]}})
    assert is_symmetric(draft.curve(sid), "S")
    assert draft.curve(sid)[2] == pytest.approx((0.8, -0.1))


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([1, 2], "JSON object"),
        ({"parameters": [0.5]}, "no 'tiling_type'"),
        ({"tiling_type": 999}, "Invalid isohedral tiling type"),
        ({"tiling_type": "x"}, "unreadable"),
        ({"tiling_type": 41, "version": "2.0"}, "version"),
        ({"tiling_type": 41, "edge_curves": {"a": []}}, "unreadable"),
        ({"tiling_type": 41, "edge_curves": {"9": [[0, 0], [1, 0]]}}, "no edge shape id 9"),
        ({"tiling_type": 41, "parameters": "ab"}, "list of numbers"),
    ],
)
def test_unreadable_designs_are_refused(data, message):
    with pytest.raises(ValueError, match=message):
        em.load_design(data)


def test_file_of_the_old_gui_loads_with_its_modes():
    data = {
        "version": "1.0",
        "tiling_type": 41,
        "parameters": [0.5, 0.5],
        "edge_curves": {"0": [[0, 0], [0.25, 0.1], [0.5, 0.0], [0.75, -0.1], [1, 0]]},
        "edge_curve_modes": {"0": "linear"},
        "description": "mine",
        "metadata": {"created": "2026-01-01T00:00:00+00:00"},
    }
    m = EditorModel()
    assert m.load_data(data, "mine") == []
    assert m.tiling.edge_curve_modes == {0: "linear"}
    assert m.labels == ["Load 'Mine'"]


def test_load_data_refuses_an_invalid_outline_and_keeps_the_state():
    m = EditorModel()
    before = (m.draft, list(m.labels))
    bad = {
        "tiling_type": 41,
        "parameters": [0.230769230769, 0.5],
        "edge_curves": {"0": [[0, 0], [0.2, 0.9], [0.8, -0.9], [1, 0]], "1": [[0, 0], [0.5, 0.9], [1, 0]]},
    }
    with pytest.raises(ValueError, match="not a valid prototile"):
        m.load_data(bad)
    assert (m.draft, m.labels) == before


# ----------------------------------------------------------------------------------------------------------------------
# Type data


@pytest.mark.parametrize("number", [41, 84, 4, 10, 93])
def test_parameter_info_has_the_exact_derivative(number):
    t = get_type(number)
    for p in em.parameter_info(number):
        k = p["index"]
        for a in p["affected_vertices"]:
            assert (a["dx_dp"], a["dy_dp"]) == tuple(t.vertex_coeffs[a["vertex"], :, k])


@pytest.mark.parametrize("number", [n for n in tiling_types() if get_type(n).n_params])
def test_parameter_groups_partition_the_parameters(number):
    groups = em.parameter_groups(number)
    flat = sorted(p for g in groups for p in g["params"])
    assert flat == list(range(get_type(number).n_params))
    for g in groups:
        assert g["vertices"] == {v for v, _ in g["jacobian"]}


def test_parameter_groups_of_a_type_without_parameters_are_empty():
    assert em.parameter_groups(10) == ()


def test_type_info_is_cached_and_complete():
    assert em.type_info(41) is em.type_info(41)
    info = em.type_info(41)
    for key in ("num_vertices", "num_aspects", "num_parameters", "customizable_shape_ids", "parameters", "edges"):
        assert key in info


# ----------------------------------------------------------------------------------------------------------------------
# Curve preset availability


def test_preset_availability_names_the_reason():
    draft = Draft.of(36)  # a J curve on shape id 0: the deep scallops cross the neighbors
    status = em.preset_availability(draft, 0)
    assert list(status) == list(em.list_curve_presets())
    assert status["scalloped"] == ""
    assert status["scalloped_deep"].startswith("the prototile would be invalid")
    assert "[0, 1]" in status["scalloped_deep"]
    assert status["straight"] == "already the current curve"


def test_preset_availability_follows_the_current_curve_and_mode():
    draft = Draft.of(36).with_curve(0, em.enforce_symmetry(em.get_curve_preset("scalloped"), "J"))
    status = em.preset_availability(draft, 0)
    assert status["scalloped"] == "already the current curve"
    assert status["straight"] == ""
    assert em.preset_availability(draft, 0) == status  # cached, same result


@pytest.mark.parametrize("number", [n for n in tiling_types() if "I" not in get_type(n).shape_types or n % 3 == 0])
def test_preset_availability_agrees_with_committing_the_preset(number):
    t = get_type(number)
    for sid, shape in enumerate(t.shape_types):
        if shape == "I":
            continue
        draft = Draft.of(number)
        for name, reason in em.preset_availability(draft, sid).items():
            m = EditorModel(draft)
            curve = em.preset_curve(name, shape)
            result = m.commit("x", draft.with_curve(sid, curve))
            changed = m.draft != draft
            if reason.startswith("the preset has no part"):  # applies as a straight edge
                assert shape in ("S", "U") and abs(curve[:, 1]).max() == 0
                continue
            assert (reason == "") == (result.committed and changed), (number, sid, name, reason)
