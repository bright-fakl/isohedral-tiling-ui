"""Tests of the validity feedback: probe, display state, slider limits and the history under random edits."""

from __future__ import annotations

import numpy as np
import pytest
from isotile import check_prototile, get_type

from isohedral_tiling_ui import editor_model as em
from isohedral_tiling_ui.components import CurveEditor, PrototileEditor, StatusLine
from isohedral_tiling_ui.components.probe import ProbeChannel
from isohedral_tiling_ui.editor_model import Draft, edge_local_coordinates, placed_curve, slider_limits
from isohedral_tiling_ui.state import TilingState, Validity
from isohedral_tiling_ui.utils.curve_presets import get_curve_preset, list_curve_presets


class Notes:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def __call__(self, level: str, message: str) -> None:
        self.events.append((level, message))


@pytest.fixture
def notes() -> Notes:
    return Notes()


@pytest.fixture
def state(notes: Notes) -> TilingState:
    return TilingState(notify=notes)


CROSSING = [(0, 0), (0.5, 0.9), (0.5, -0.9), (1, 0)]  # a bow tie on a free edge


# -- slider limits ----------------------------------------------------------------------------------------------------


def sweep(draft: Draft, k: int, step: float = 0.004) -> tuple[float, float]:
    """The connected interval of valid values around the current value, from a fine sweep over 0 to 1."""
    grid = np.arange(0.0, 1.0 + 1e-9, step)

    def ok(v: float) -> bool:
        p = list(draft.parameters)
        p[k] = float(v)
        return draft.with_parameters(p).report().valid

    i0 = int(np.argmin(np.abs(grid - draft.parameters[k])))
    lo = hi = grid[i0]
    for i in range(i0, -1, -1):
        if not ok(grid[i]):
            break
        lo = grid[i]
    for i in range(i0, len(grid)):
        if not ok(grid[i]):
            break
        hi = grid[i]
    return float(lo), float(hi)


def drafts_for_limits() -> list[tuple[str, Draft]]:
    out = [(f"IH{n}", Draft.of(n)) for n in (30, 4, 7, 12, 16)]
    scalloped = get_curve_preset("scalloped")
    out.append(("IH4 with a curve", Draft.of(4, curves={0: em.enforce_symmetry(scalloped, "S")})))
    out.append(("IH12 with curves", Draft.of(12, curves={0: em.enforce_symmetry(get_curve_preset("ramp_up"), "U")})))
    out.append(("IH30 at 0.4", Draft.of(30).with_parameters([0.4])))
    return out


@pytest.mark.parametrize(("name", "draft"), drafts_for_limits(), ids=[n for n, _ in drafts_for_limits()])
def test_slider_limits_agree_with_a_fine_sweep(name, draft):
    assert draft.report().valid
    for k in range(len(draft.parameters)):
        lo, hi = slider_limits(draft, k)
        s_lo, s_hi = sweep(draft, k)
        assert lo == pytest.approx(s_lo, abs=0.006), (name, k)
        assert hi == pytest.approx(s_hi, abs=0.006), (name, k)
        assert lo <= draft.parameters[k] <= hi


def test_slider_limits_are_valid_inside_and_invalid_just_outside():
    draft = Draft.of(30)
    lo, hi = slider_limits(draft, 0)
    assert (lo, hi) == pytest.approx((0.0, 0.487), abs=2e-3)
    assert draft.with_parameters([hi]).report().valid
    assert not draft.with_parameters([hi + 0.01]).report().valid


# -- probe ------------------------------------------------------------------------------------------------------------


def test_probe_does_not_change_the_state(state):
    state.load_type(36)
    before_draft, before_labels, before_index = state.draft, list(state.model.labels), state.model._index
    assert not state.probe_curve(0, CROSSING)
    assert state.draft is before_draft
    assert state.model.labels == before_labels and state.model._index == before_index
    assert state.validity is not None and state.validity.shape_ids == (0,)
    assert state.probe_curve(0, get_curve_preset("scalloped"))  # valid again: nothing is shown
    assert state.validity is None
    assert state.draft is before_draft and state.model.labels == before_labels


def test_probe_of_parameters_names_the_edges(state):
    state.load_type(30)
    assert state.probe_parameters([0.55]) is False
    assert state.validity.bad_edges and state.validity.message
    assert state.probe_parameters([0.3])
    assert state.validity is None


def test_validity_names_all_edges_of_a_shape_id(state):
    state.load_type(41)  # shape id 0 is on edges 0 and 2
    state.probe_curve(0, CROSSING)
    assert state.validity.shape_ids == (0,)
    assert {0, 2} <= set(state.validity.bad_edges)
    assert "C0" in state.validity.describe() and "edges" in state.validity.describe()


def test_refused_edit_names_the_curve_and_edges_and_holds_the_display(state, notes):
    state.load_type(36)
    before = state.draft
    assert not state.set_curve(0, CROSSING, "Move curve 0 control point")
    assert state.draft is before
    assert notes.events and "C0" in notes.events[-1][1] and "edge" in notes.events[-1][1]
    assert isinstance(state.validity, Validity)  # held after the refusal
    state.clear_validity()
    assert state.validity is None


def test_a_superseded_hold_is_not_cleared(state):
    state.load_type(36)
    state.set_curve(0, CROSSING, "x")
    stale = state._hold_token
    state.probe_curve(0, CROSSING)  # a new display
    state.clear_validity(stale)
    assert state.validity is not None
    state.clear_validity()
    assert state.validity is None


def test_a_commit_clears_the_display(state):
    state.load_type(36)
    state.probe_curve(0, CROSSING)
    assert state.set_curve(0, get_curve_preset("scalloped"), "ok")
    assert state.validity is None


# -- display ------------------------------------------------------------------------------------------------------------


def test_curve_editor_draws_the_invalid_curve_red(state):
    editor = CurveEditor(state=state)
    state.load_type(36)
    editor._on_probe({"pts": CROSSING})
    assert editor._curve_renderer.glyph.line_color == "#d62728"
    editor._on_probe({"pts": get_curve_preset("scalloped")})
    assert editor._curve_renderer.glyph.line_color == "steelblue"


def test_prototile_editor_draws_all_edges_of_the_shape_ids_red(state):
    editor = PrototileEditor(state=state)
    state.load_type(41)
    state.probe_curve(0, CROSSING)
    ids = state.tiling.edge_shape_ids.tolist()
    assert list(editor._bad_filter.booleans) == [i == 0 for i in ids]
    assert sum(editor._bad_filter.booleans) == 2  # both copies
    state.clear_validity()
    assert not any(editor._bad_filter.booleans)


def test_vertex_probe_marks_the_edges_and_a_release_reverts(state, notes):
    editor = PrototileEditor(state=state)
    state.load_type(30)
    editor._on_probe({"idx": [0], "dp": [0.35]})  # 0.23 + 0.35 is invalid
    assert any(editor._bad_filter.booleans)
    before = state.draft
    assert not state.set_parameters([0.6])
    assert state.draft is before and any(editor._bad_filter.booleans)  # held
    state.set_parameters([0.3])
    assert not any(editor._bad_filter.booleans)


def test_status_line_shows_the_message(state):
    line = StatusLine(state=state)
    state.load_type(36)
    assert line._pane.object == "&nbsp;"
    state.probe_curve(0, CROSSING)
    assert "Invalid:" in line._pane.object and "C0" in line._pane.object
    state.clear_validity()
    assert line._pane.object == "&nbsp;"


def panel_widgets(editor: PrototileEditor, kind: type) -> list:
    """The widgets of a type in the parameter panel, in order."""
    found = []

    def walk(obj) -> None:
        if isinstance(obj, kind):
            found.append(obj)
        for child in getattr(obj, "objects", []) or []:
            walk(child)

    walk(editor._build_parameter_panel())
    return found


def test_slider_track_marks_the_invalid_part_and_the_slider_cannot_leave_the_valid_interval(state):
    import panel as pn

    editor = PrototileEditor(state=state)
    state.load_type(30)
    (slider,) = panel_widgets(editor, pn.widgets.FloatSlider)
    css = slider.stylesheets[0]
    hi_fraction = editor._valid_fractions(state.parameter_ranges(0)[0])[1]
    assert "linear-gradient" in css and f"{hi_fraction * 100:.1f}%" in css  # marked from the limit to the end
    assert 0.48 < hi_fraction < 0.53
    slider.value = 0.9  # an excluded value
    assert slider.value == pytest.approx(0.487, abs=2e-3)
    assert state.draft.report().valid and state.validity is None


def test_the_slider_window_reaches_beyond_one_where_the_prototile_is_valid(state):
    import panel as pn

    editor = PrototileEditor(state=state)
    state.load_preset("flowers")  # IH21 with curves: valid from -0.04 and from 0.18 upward, no upper limit
    ranges = state.parameter_ranges(0)
    assert ranges[0].valid_lo == pytest.approx(-0.0456, abs=2e-3) and ranges[0].open_hi
    assert ranges[1].open_hi
    below = list(state.parameters)
    below[1] = ranges[1].valid_lo - 0.005
    above = list(state.parameters)
    above[1] = ranges[1].valid_lo + 0.005
    assert not state.draft.with_parameters(below).report().valid  # a hard minimum
    assert state.draft.with_parameters(above).report().valid
    assert ranges[0].window_lo == pytest.approx(-0.05) and ranges[0].window_hi == pytest.approx(2.0)
    sliders = panel_widgets(editor, pn.widgets.FloatSlider)
    assert (sliders[1].start, sliders[1].end) == pytest.approx((0.0, 2.0))
    editor.zoom = 2  # the window is searched up to 5
    sliders = panel_widgets(editor, pn.widgets.FloatSlider)
    assert sliders[1].end == pytest.approx(5.0)
    state.load_type(30)  # a new type resets the zoom
    assert editor.zoom == 0


def test_a_value_typed_in_the_field_is_applied_if_valid_and_refused_otherwise(state, notes):
    import panel as pn

    editor = PrototileEditor(state=state)
    state.load_preset("flowers")
    field = panel_widgets(editor, pn.widgets.FloatInput)[1]
    field.value = 2.5  # beyond the old range 0 to 1, valid
    assert state.parameters[1] == pytest.approx(2.5)
    field = panel_widgets(editor, pn.widgets.FloatInput)[1]
    before = state.draft
    field.value = 0.05  # below the limit 0.18
    assert state.draft is before
    assert notes.events and "Not applied" in notes.events[-1][1]


def test_the_vertex_drag_clamps_to_the_window_of_the_slider(state):
    editor = PrototileEditor(state=state)
    state.load_preset("flowers")
    window = editor._ranges()[1]
    editor._on_probe({"idx": [1], "dp": [100.0]})  # far beyond the window
    assert state.validity is None or state.validity.bad_edges is not None
    assert max(state.parameters[1], window.window_hi) == window.window_hi
    import json

    windows = json.loads(editor._jacobian_source.data["windows"][0])
    assert windows["1"] == pytest.approx([window.window_lo, window.window_hi])


# -- curved edges ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("preset", ["flowers", "tree", "wavy_triangle", "puzzle_piece", "arrow_quad"])
def test_placed_curve_reproduces_the_edge_outlines(preset):
    from isotile import IsohedralTiling

    draft = Draft.from_tiling(IsohedralTiling.from_preset(preset))
    outlines = draft.tiling().edge_outlines()
    for sid in sorted(set(draft.tiling().edge_shape_ids.tolist())):
        placed = placed_curve(draft, int(sid), draft.curve(int(sid)))
        assert placed
        for i, (xs, ys) in placed.items():
            assert np.allclose(np.c_[xs, ys], outlines[i], atol=1e-9)


def test_placed_curve_is_available_for_a_curve_that_would_be_invalid():
    draft = Draft.of(41)
    placed = placed_curve(draft, 0, CROSSING)
    assert set(placed) == {0, 2}  # both copies
    assert not draft.with_curve(0, CROSSING).report().valid


@pytest.mark.parametrize("preset", ["flowers", "tree", "wavy_triangle", "puzzle_piece"])
def test_curved_edges_follow_moved_vertices_exactly(preset):
    from isotile import IsohedralTiling

    draft = Draft.from_tiling(IsohedralTiling.from_preset(preset))
    tiling = draft.tiling()
    if not len(draft.parameters):
        pytest.skip("no parameters")
    moved = draft.with_parameters([min(1.0, p + 0.03) for p in draft.parameters])
    if not moved.report().valid:
        pytest.skip("moved draft invalid")
    local = edge_local_coordinates(tiling)
    verts = np.asarray(moved.tiling().vertices)
    for i, expected in enumerate(moved.tiling().edge_outlines()):
        a, b = verts[i], verts[(i + 1) % len(verts)]
        d = b - a
        s_t = np.asarray(local[i])
        pts = a + s_t[:, :1] * d + s_t[:, 1:] * np.array([-d[1], d[0]])
        assert np.allclose(pts, expected, atol=1e-5)


def test_the_probe_sends_the_dragged_outline_to_the_prototile_editor(state):
    editor = PrototileEditor(state=state)
    state.load_type(41)
    state.probe_curve(0, CROSSING)
    xs = editor._curved_source.data["xs"]
    assert set(state.live_outline) == {0, 2}
    assert xs[0] == state.live_outline[0][0] and xs[2] == state.live_outline[2][0]
    state.set_curve(0, get_curve_preset("scalloped"), "ok")  # a commit redraws from the draft
    assert state.live_outline is None


def test_red_is_on_the_curved_edges_of_the_shape_id(state):
    editor = PrototileEditor(state=state)
    state.load_type(41)
    state.probe_curve(0, CROSSING)
    colors = editor._curved_source.data["line_color"]
    ids = state.tiling.edge_shape_ids.tolist()
    assert [c == "#d62728" for c in colors] == [i == 0 for i in ids]
    assert not editor._bad_straight.visible  # the straight guide is not drawn red while the curves are shown
    editor.show_curves = False
    assert editor._bad_straight.visible and not editor._curved_renderer.visible


# -- the probe channel -----------------------------------------------------------------------------------------------


def test_probe_channel_calls_the_handler_and_answers():
    received = []
    channel = ProbeChannel("t", received.append)
    channel.in_source.data = {"seq": [3], "payload": ['{"a": 1}']}
    assert received == [{"a": 1}]
    assert channel.out_source.data["seq"] == [3]
    channel.in_source.data = {"seq": [4], "payload": ["not json"]}  # still answered
    assert channel.out_source.data["seq"] == [4]


# -- random operation sequences ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(4))
def test_no_invalid_draft_is_ever_in_the_history(seed):
    rng = np.random.default_rng(seed)
    state = TilingState(notify=lambda level, message: None)
    types = [4, 12, 30, 36, 41, 14, 1]
    state.load_type(int(rng.choice(types)))
    names = list_curve_presets()

    def check() -> None:
        for entry in state.model._history:
            assert check_prototile(
                entry.draft.type_number,
                entry.draft.parameters,
                **em._arguments(entry.draft),
            ).valid
        assert state.draft.report().valid

    for _ in range(120):
        op = int(rng.integers(0, 10))
        ids = state.type_info["customizable_shape_ids"]
        sid = int(rng.choice(ids)) if ids else None
        if op == 0 and len(state.parameters):
            state.set_parameter(int(rng.integers(len(state.parameters))), float(rng.uniform(0, 1)), "p")
        elif op == 1 and sid is not None:
            curve = [list(p) for p in state.draft.curve(sid)]
            i = int(rng.integers(len(curve)))
            curve[i][0] += float(rng.normal(0, 0.15))
            curve[i][1] += float(rng.normal(0, 0.15))
            state.set_curve(sid, curve, "move")
        elif op == 2 and sid is not None:
            state.set_curve(sid, get_curve_preset(str(rng.choice(names))), "preset")
        elif op == 3 and sid is not None:
            state.add_node(sid, float(rng.uniform(0.1, 0.9)), float(rng.normal(0, 0.1)))
        elif op == 4 and sid is not None and len(state.draft.curve(sid)) > 2:
            state.remove_node(sid, int(rng.integers(1, len(state.draft.curve(sid)) - 1)))
        elif op == 5 and sid is not None:
            state.set_mode(sid, str(rng.choice(["linear", "spline"])))
        elif op == 6 and len(ids) > 1:
            state.copy_curve(int(ids[0]), int(ids[1]))
        elif op == 7:
            [state.undo, state.redo, state.reset][int(rng.integers(3))]()
        elif op == 8 and len(state.parameters):
            state.probe_parameters([float(rng.uniform(0, 1)) for _ in state.parameters])  # never commits
        elif op == 9 and rng.random() < 0.1:
            state.load_type(int(rng.choice(types)))
        check()
    assert get_type(state.tiling_type) is not None
