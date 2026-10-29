"""Headless tests of the Panel layer: the state adapter and the components, driven the way Bokeh and Panel call them.

The JavaScript handlers, the Bokeh tools and the browser rendering are not run here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from isotile import IsohedralTiling, enforce_symmetry, get_type, is_symmetric

from isohedral_tiling_ui.components import (
    CurveEditor,
    GalleryPanel,
    HistoryToolbar,
    LoadSavePanel,
    PreviewStrip,
    PrototileEditor,
    TileTypeSelector,
)
from isohedral_tiling_ui.editor_model import Draft, parameter_groups, preset_availability, preset_curve
from isohedral_tiling_ui.state import TilingState
from isohedral_tiling_ui.utils.curve_presets import get_curve_preset

SRC = Path(__file__).parent.parent / "src" / "isohedral_tiling_ui"


class Notes:
    """Collects the notifications of a state."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def __call__(self, level: str, message: str) -> None:
        self.events.append((level, message))

    def levels(self) -> list[str]:
        return [level for level, _ in self.events]


@pytest.fixture
def notes() -> Notes:
    return Notes()


@pytest.fixture
def state(notes: Notes) -> TilingState:
    return TilingState(notify=notes)


def watch_draft(state: TilingState) -> list:
    events: list = []
    state.param.watch(events.append, "draft")
    return events


# ----------------------------------------------------------------------------------------------------------------------
# State


def test_initial_state(state):
    assert state.tiling_type == 10
    assert state.source_label == "regular_hexagon"
    assert state.model.labels == ["Initial state"]
    assert not state.reset_available
    assert state.baseline_label == "regular_hexagon"
    assert state.baseline_svg.startswith("<svg")
    assert state.active_curve_id == 0
    assert isinstance(state.tiling, IsohedralTiling)


def test_type_change_gives_the_defaults_and_a_new_history(state):
    state.load_type(41)
    assert state.tiling_type == 41
    assert list(state.parameters) == list(get_type(41).default_params)
    assert state.edge_curves == {}
    assert state.source_label == ""
    assert state.default_name == "IH41"
    assert state.model.labels == ["IH41"]
    assert state.type_info["num_parameters"] == 2
    assert state.reset_ranges


def test_load_preset_publishes_one_event_even_for_the_same_preset(state):
    events = watch_draft(state)
    state.load_preset("puzzle_piece")
    assert len(events) == 1
    assert state.source_label == "puzzle_piece"
    assert state.default_name == "puzzle_piece"
    assert set(state.edge_curves) == {0, 1}
    assert state.edge_curve_modes == {0: "spline", 1: "spline"}
    assert state.model.labels == ["Load preset 'Puzzle Piece'"]
    state.load_preset("puzzle_piece")  # the views redraw (the user resets the axes this way)
    assert len(events) == 2


def test_one_edit_publishes_one_event(state):
    state.load_preset("square")
    events = watch_draft(state)
    assert state.set_parameters([0.5, 0.5])
    assert len(events) == 1
    assert list(state.parameters) == [0.5, 0.5]


def test_parameter_change_enables_reset_and_reset_restores(state):
    state.load_preset("square")
    initial = list(state.parameters)
    assert state.set_parameters([initial[0] * 0.5, initial[1]])
    assert state.reset_available
    state.reset()
    assert list(state.parameters) == initial
    assert not state.reset_available
    assert state.model.labels[-1] == "Reset to Square"


def test_undo_redo_round_trip(state):
    state.load_preset("square")
    first = state.parameters
    state.set_parameters([0.5, 0.5])
    assert state.model.labels == ["Load preset 'Square'", "Move prototile vertices"]
    state.undo()
    assert state.parameters == first
    assert state.can_redo
    state.redo()
    assert list(state.parameters) == [0.5, 0.5]


def test_undo_does_not_change_the_selected_curve(state):
    state.load_preset("puzzle_piece")
    state.set_curve(0, [(0, 0), (0.5, 0.1), (1, 0)], "edit")
    state.active_curve_id = 1
    state.undo()
    assert state.active_curve_id == 1


def test_invalid_edit_is_refused_with_a_message_and_the_views_redraw(state, notes):
    state.load_type(41)
    events = watch_draft(state)
    before = state.draft
    bow_tie = [(0, 0), (0.8, 0.5), (0.2, 0.5), (1, 0)]
    assert not state.set_curve(0, bow_tie, "Apply 'bow'")
    assert state.draft is before
    assert len(events) == 1  # forced, so that the figures show the draft again
    assert notes.levels() == ["error"]
    assert notes.events[0][1].startswith("Not applied (Apply 'bow'):")
    assert state.model.labels == ["IH41"]


def test_refused_mode_change(state, notes):
    state.model.load(
        Draft.of(14, curves={0: [(0, 0), (0.25, -0.198), (0.5, -0.34), (0.75, -0.354), (1, 0)]}, modes={0: "linear"}),
        "x",
    )
    state.param.update(draft=state.model.draft)
    assert not state.set_mode(0, "spline")
    assert state.draft.mode(0) == "linear"
    assert notes.levels() == ["error"]


def test_active_curve_follows_the_type(state):
    state.load_type(93)  # equilateral triangle: all edges straight
    assert state.active_curve_id is None
    state.load_type(84)
    assert state.active_curve_id == get_type(84).info()["customizable_shape_ids"][0]


def test_load_data_notifies_repairs_and_errors(state, notes):
    assert state.load_data({"tiling_type": 41, "parameters": [0.3]}, "mine")
    assert state.source_label == "mine"
    assert list(state.parameters) == list(get_type(41).default_params)
    assert notes.levels() == ["warning"]
    assert not state.load_data({"tiling_type": 999})
    assert notes.levels() == ["warning", "error"]
    assert state.tiling_type == 41  # unchanged
    assert state.source_label == "mine"


def test_state_never_prints(state, capsys):
    state.load_preset("tree")
    state.set_parameters(state.parameters)
    state.undo()
    state.redo()
    state.reset()
    assert capsys.readouterr().out == ""


# ----------------------------------------------------------------------------------------------------------------------
# Components


def test_every_component_builds_and_renders(state):
    for cls in (
        TileTypeSelector,
        GalleryPanel,
        LoadSavePanel,
        PreviewStrip,
        PrototileEditor,
        CurveEditor,
        HistoryToolbar,
    ):
        component = cls(state=state)
        assert component.__panel__() is not None


def test_slider_event_commits_and_an_invalid_value_is_refused(state, notes):
    editor = PrototileEditor(state=state)
    state.load_type(41)
    editor._on_slider_change(SimpleNamespace(new=0.3), 0)
    assert state.parameters[0] == pytest.approx(0.3)
    assert state.model.labels[-1].startswith("Adjust")
    editor._on_slider_change(SimpleNamespace(new=0.35), 0)
    assert state.model.labels.count(state.model.labels[-1]) == 1  # coalesced
    n = 30  # one parameter; the prototile is invalid above 0.487
    state.load_type(n)
    bad = float(get_type(n).safe_ranges[0][1]) + 0.05
    before = state.draft
    editor._on_slider_change(SimpleNamespace(new=bad), 0)
    assert state.draft is before
    assert notes.levels() == ["error"]


def test_prototile_figure_follows_the_draft(state):
    editor = PrototileEditor(state=state)
    state.load_preset("puzzle_piece")
    verts = state.tiling.vertices
    assert np.allclose(editor._outline_source.data["xs"][0][:-1], verts[:, 0])
    assert len(editor._curve_renderers) == len(verts)
    state.set_parameters([0.3, 0.4])
    assert np.allclose(editor._outline_source.data["xs"][0][:-1], state.tiling.vertices[:, 0])
    state.load_type(10)
    assert len(editor._curve_renderers) == 6


def drag_vertices(editor: PrototileEditor, state: TilingState, parameter: int, target: float) -> None:
    """Move the vertices of the active group the way the Jacobian says a change of one parameter would."""
    group = parameter_groups(state.tiling_type)[editor.active_param_group]
    delta = target - state.parameters[parameter]
    data = {k: list(v) for k, v in editor._draggable_source.data.items()}
    for k, v in enumerate(data["idx"]):
        dx, dy = group["jacobian"].get((v, parameter), (0.0, 0.0))
        data["x"][k] += dx * delta
        data["y"][k] += dy * delta
    editor._draggable_source.data = data


def test_vertex_drag_solves_for_the_parameters(state):
    editor = PrototileEditor(state=state)
    state.load_type(30)
    drag_vertices(editor, state, 0, 0.4)
    assert state.parameters[0] == pytest.approx(0.4)
    assert state.model.labels[-1] == "Move prototile vertices"


def test_vertex_drag_to_an_invalid_prototile_is_refused(state, notes):
    editor = PrototileEditor(state=state)
    state.load_type(30)
    before = state.draft
    drag_vertices(editor, state, 0, 0.55)  # valid up to 0.487
    assert state.draft is before
    assert notes.levels() == ["error"]
    shown = editor._draggable_source.data
    assert np.allclose(shown["x"], [state.tiling.vertices[i][0] for i in shown["idx"]])  # back at the draft


def test_curve_drag_release_commits_the_symmetric_curve(state):
    editor = CurveEditor(state=state)
    state.load_preset("wavy_triangle")
    sid = state.active_curve_id
    data = {k: list(v) for k, v in editor._cp_source.data.items()}
    data["y"] = [y + 0.01 for y in data["y"]]
    editor._cp_source.data = data
    curve = state.draft.curve(sid)
    assert is_symmetric(curve, state.shape_type(sid))
    assert state.model.labels[-1] == f"Move curve {sid} control point"


def test_invalid_curve_drag_is_refused_and_the_figure_returns_to_the_draft(state, notes):
    editor = CurveEditor(state=state)
    state.load_type(41)
    state.set_curve(0, [(0, 0), (0.3, 0.1), (0.7, 0.1), (1, 0)], "setup")
    before = state.draft
    assert editor._cp_source.data["idx"] == [1, 2]
    data = {k: list(v) for k, v in editor._cp_source.data.items()}
    data["x"], data["y"] = [0.8, 0.2], [0.5, 0.5]  # the bow tie
    editor._cp_source.data = data
    assert state.draft is before
    assert notes.levels() == ["error"]
    assert list(editor._cp_source.data["x"]) == [0.3, 0.7]
    assert editor._curve_source.data["x"][0] == 0


def test_double_tap_adds_and_removes_node_pairs_on_a_u_curve(state):
    editor = CurveEditor(state=state)
    state.load_type(12)
    sid = next(i for i in get_type(12).info()["customizable_shape_ids"] if state.shape_type(i) == "U")
    state.active_curve_id = sid
    state.set_curve(sid, [(0, 0), (0.2, 0.1), (0.5, 0.15), (0.8, 0.1), (1, 0)], "setup")
    editor._on_double_tap(SimpleNamespace(x=0.35, y=0.13))
    assert len(state.draft.curve(sid)) == 7
    assert is_symmetric(state.draft.curve(sid), "U")
    editor._on_double_tap(SimpleNamespace(x=0.35, y=0.13))  # on the new node
    assert len(state.draft.curve(sid)) == 5
    editor._on_double_tap(SimpleNamespace(x=0.8, y=0.1))  # on a node of the second half: not editable
    assert len(state.draft.curve(sid)) == 5
    editor._on_double_tap(SimpleNamespace(x=0.9, y=0.0))  # U curves take no node in the second half
    assert len(state.draft.curve(sid)) == 5


def test_table_edit_moves_a_node_and_its_partner(state):
    editor = CurveEditor(state=state)
    state.load_preset("wavy_triangle")
    sid = state.active_curve_id
    curve = state.draft.curve(sid)
    editor._on_y_change(1, curve[1][1] + 0.02)
    moved = state.draft.curve(sid)
    assert moved[1][1] == pytest.approx(curve[1][1] + 0.02)
    assert moved[-2][1] == pytest.approx(-moved[1][1])
    editor._on_x_change(1, curve[1][0] + 0.01)
    assert state.model.labels.count(f"Edit curve {sid} control point") == 1  # coalesced


def test_curve_presets_and_copy_keep_the_symmetry(state):
    editor = CurveEditor(state=state)
    state.load_preset("wavy_triangle")
    for name in ("scalloped", "s_curve", "puzzle", "ramp_up"):
        editor._apply_preset(name)
        for sid, curve in state.draft.curves:
            assert is_symmetric(curve, state.shape_type(sid))
    ids = get_type(84).info()["customizable_shape_ids"]
    editor._copy_from_curve(ids[1])
    assert state.draft.curve(ids[0]) == state.draft.curve(ids[1])


def test_mode_radio_changes_the_mode(state):
    CurveEditor(state=state)
    state.load_preset("puzzle_piece")
    assert state.set_mode(0, "linear")
    assert state.tiling.edge_curve_modes[0] == "linear"
    assert state.tiling.edge_curve_modes[1] == "spline"


def click(button) -> None:
    """Click a widget the way the browser does: the ``clicks`` counter of the Bokeh model increases."""
    button.clicks += 1


def unavailable(editor: CurveEditor) -> set[str]:
    """Names of the preset buttons shown as unavailable (muted, with the reason in the tooltip)."""
    return {n for n, reason in editor._preset_reasons.items() if reason}


def test_preset_buttons_are_unavailable_with_a_reason_where_the_preset_cannot_apply(state):
    editor = CurveEditor(state=state)
    state.load_type(36)
    buttons = editor._preset_buttons
    assert {"scalloped_deep", "straight"} <= unavailable(editor)
    assert "scalloped" not in unavailable(editor)
    assert "Not available: the prototile would be invalid" in buttons["scalloped_deep"].description
    assert "already the current curve" in buttons["straight"].description
    assert "Not available" not in buttons["scalloped"].description
    assert buttons["scalloped_deep"].styles == {"opacity": "0.35"}
    assert buttons["scalloped"].styles == {}


def test_icon_buttons_are_not_button_icons(state):
    """A ButtonIcon changes its own disabled and value in the browser and can stay stuck; plain buttons do not."""
    selector, gallery, curves = TileTypeSelector(state=state), GalleryPanel(state=state), CurveEditor(state=state)
    buttons = [*selector._buttons.values(), *gallery._buttons.values(), *curves._preset_buttons.values()]
    assert len(buttons) > 100
    assert all(type(b).__name__ == "Button" and not b.disabled for b in buttons)
    state.load_type(36)
    click(curves._preset_buttons["scalloped"])
    assert not any(b.disabled for b in buttons)


def test_preset_availability_follows_the_draft_after_every_change(state):
    editor = CurveEditor(state=state)
    state.load_type(36)
    expected = unavailable(editor)
    assert "straight" in expected
    click(editor._preset_buttons["scalloped"])
    assert "scalloped" in unavailable(editor)  # now the current curve
    assert "straight" not in unavailable(editor)  # enabled again
    click(editor._preset_buttons["straight"])
    assert "straight" in unavailable(editor)
    assert "scalloped" not in unavailable(editor)  # enabled again after another preset was clicked
    state.undo()
    assert "scalloped" in unavailable(editor) and "straight" not in unavailable(editor)
    state.redo()
    assert "straight" in unavailable(editor) and "scalloped" not in unavailable(editor)
    state.reset()
    assert unavailable(editor) == expected
    state.load_type(36)
    assert unavailable(editor) == expected
    state.load_preset("wavy_triangle")
    for curve_id in state.type_info["customizable_shape_ids"]:  # selecting another edge
        state.active_curve_id = curve_id
        status = preset_availability(state.draft, curve_id)
        assert unavailable(editor) == {n for n, reason in status.items() if reason}


@pytest.mark.parametrize("shape", ["J", "S", "U"])
def test_preset_curves_have_the_symmetry_of_the_edge(shape):
    from isohedral_tiling_ui.utils.curve_presets import get_curve_preset, list_curve_presets

    for name in list_curve_presets():
        curve = preset_curve(name, shape)
        assert np.allclose(curve, enforce_symmetry(curve, shape))
        if shape == "J":
            assert np.allclose(curve, get_curve_preset(name))


def test_preset_curves_for_s_and_u_edges():
    # the first half of the preset is used; a preset whose first half is straight gives the symmetric part
    assert preset_curve("scalloped", "U") == pytest.approx(np.array(get_curve_preset("scalloped")))
    assert preset_curve("scalloped", "S")[1] == pytest.approx([0.25, 0.08])
    ramp = preset_curve("ramp_up", "S")
    assert ramp[1] == pytest.approx([0.3, -0.06]) and ramp[2] == pytest.approx([0.7, 0.06])  # not flat
    assert preset_curve("ramp_up", "U")[1, 1] == pytest.approx(0.06)
    # nothing for the shape to hold: straight, and reported as unavailable
    assert np.abs(preset_curve("arrow", "S")[:, 1]).max() == 0


def test_presets_without_a_part_that_the_edge_can_hold_are_unavailable(state):
    editor = CurveEditor(state=state)
    state.load_type(4)  # edge 0 is an S edge
    assert state.shape_type(0) == "S"
    assert {"arrow", "arrow_down"} <= unavailable(editor)
    assert "symmetry of the S edge" in editor._preset_buttons["arrow"].description
    assert "ramp_up" not in unavailable(editor)
    click(editor._preset_buttons["ramp_up"])
    assert any(y != 0 for _, y in state.draft.curve(0))


def test_unavailable_presets_are_not_applied(state):
    editor = CurveEditor(state=state)
    state.load_type(36)
    n = len(state.model.labels)
    click(editor._preset_buttons["scalloped_deep"])  # would make the prototile invalid
    click(editor._preset_buttons["straight"])  # already the current curve
    assert len(state.model.labels) == n


def test_repeated_preset_clicks_keep_responding(state):
    editor = CurveEditor(state=state)
    state.load_type(36)
    buttons = editor._preset_buttons
    click(buttons["scalloped"])
    assert len(state.model.labels) == 2
    click(buttons["scalloped"])  # the same preset again: nothing to do, no error
    assert len(state.model.labels) == 2
    click(buttons["scalloped_down"])  # another one
    assert len(state.model.labels) == 3
    click(buttons["scalloped"])
    assert len(state.model.labels) == 4
    click(buttons["scalloped_down"])
    assert len(state.model.labels) == 5


def test_node_colors_are_uniform_and_the_selection_is_kept_by_the_editor(state):
    editor = CurveEditor(state=state)
    state.load_preset("puzzle_piece")
    data = editor._cp_source.data
    assert set(data["fill_color"]) == {"#444"}  # no node is special
    assert set(editor._fixed_cp_source.data["fill_color"]) == {"#ccc"}
    nodes = [data["idx"][1], data["idx"][3]]
    editor._selection_source.data = {"idx": nodes}  # what the browser sets when nodes are selected
    editor._update_figure()  # a redraw keeps the selected nodes of the same curve
    data = editor._cp_source.data
    assert [c for i, c in zip(data["idx"], data["fill_color"], strict=True) if i in nodes] == ["crimson"] * 2
    assert [c for i, c in zip(data["idx"], data["fill_color"], strict=True) if i not in nodes] == ["#444"] * (
        len(data["idx"]) - 2
    )
    assert editor._cp_source.selected.indices == [1, 3]
    other = next(c for c in state.type_info["customizable_shape_ids"] if c != state.active_curve_id)
    state.active_curve_id = other  # another curve: no selection
    assert list(editor._selection_source.data["idx"]) == []
    assert set(editor._cp_source.data["fill_color"]) <= {"#444"}


def test_the_curve_editor_shows_nothing_for_a_tile_without_editable_curves(state):
    editor = CurveEditor(state=state)
    state.load_preset("wavy_triangle")
    click(editor._preset_buttons["scalloped"])
    assert len(editor._cp_source.data["x"]) > 0 and editor._plot_row.visible
    state.load_preset("equilateral_triangle")  # type 93 has no editable edge
    assert state.active_curve_id is None
    assert editor._cp_source.data["x"] == [] and editor._fixed_cp_source.data["x"] == []
    assert editor._curve_source.data["x"] == []
    assert not editor._plot_row.visible
    assert unavailable(editor) == set(editor._preset_buttons)
    n = len(state.model.labels)
    click(editor._preset_buttons["scalloped"])  # no effect
    assert len(state.model.labels) == n
    state.load_preset("wavy_triangle")  # a tile with curves again
    assert editor._plot_row.visible and len(editor._cp_source.data["x"]) > 0
    assert unavailable(editor) != set(editor._preset_buttons)


def test_table_edit_is_applied_through_a_scheduled_callback(state, monkeypatch):
    """Panel calls on_edit outside the document lock; changing Bokeh models there fails, so the edit is scheduled."""
    import panel as pn
    from panel.models.tabulator import TableEditEvent

    editor = CurveEditor(state=state)
    state.load_preset("wavy_triangle")
    table = editor._build_point_inputs()[1]
    scheduled = []

    run = pn.state.execute

    def execute(callback, schedule="auto"):
        if schedule is True:  # the handler of the table: hold it back like the server would
            scheduled.append((callback, schedule))
        else:
            run(callback, schedule=schedule)

    monkeypatch.setattr(pn.state, "execute", execute)
    before = state.draft
    event = TableEditEvent(None, "Y", 1, 0.3, 0.15)  # what the browser sends
    event.row = 1  # Panel maps the row to the position in the frame before it calls on_edit
    table.value.loc[1, "Y"] = 0.3  # the cell patch has landed (older Panel versions read the value from the frame)
    for callback in table._on_edit_callbacks:
        callback(event)
    assert len(scheduled) == 1
    assert scheduled[0][1] is True
    assert state.draft is before  # nothing happened outside the scheduled callback
    scheduled[0][0]()
    if tuple(int(v) for v in pn.__version__.split(".")[:2]) >= (1, 10):  # older versions pass the value of the frame
        assert state.draft.curve(state.active_curve_id)[1][1] == pytest.approx(0.3)
        assert state.model.labels[-1] == f"Edit curve {state.active_curve_id} control point"


def selected_types(selector: TileTypeSelector) -> list[int]:
    return [n for n, b in selector._buttons.items() if b.styles.get("border", "").startswith("2px")]


def selected_tiles(gallery: GalleryPanel) -> list[str]:
    return [n for n, b in gallery._buttons.items() if b.styles.get("border", "").startswith("2px")]


def test_gallery_marks_the_loaded_preset_and_the_type_panel_the_type_otherwise(state):
    """Exactly one button is marked in the gallery and the type panel together: the source of the design."""
    selector, gallery = TileTypeSelector(state=state), GalleryPanel(state=state)

    def marked():
        return selected_tiles(gallery), selected_types(selector)

    assert marked() == (["regular_hexagon"], [])
    selector._buttons[41].clicks += 1  # a click on the type button
    assert marked() == ([], [41])
    gallery._buttons["tree"].clicks += 1  # a click on a gallery tile
    assert state.source_label == "tree"
    assert marked() == (["tree"], [])
    click(CurveEditor(state=state)._preset_buttons["scalloped"])  # an edit keeps the source
    assert marked() == (["tree"], [])
    state.undo()
    state.redo()
    state.reset()
    assert marked() == (["tree"], [])
    state.load_data({"tiling_type": 84})  # a file
    assert marked() == ([], [84])
    state.load_type(93)
    assert marked() == ([], [93])
    state.load_preset("square")
    assert marked() == (["square"], [])
    selector.filter_vertices = "6"  # the type grid is rebuilt
    assert marked() == (["square"], [])
    state.load_type(93)
    assert marked() == ([], [93])
    assert all(b.styles["background"] == "transparent" for n, b in selector._buttons.items() if n != 93)


def test_history_toolbar_follows_the_model(state):
    toolbar = HistoryToolbar(state=state)
    state.load_preset("square")
    state.set_parameters([0.5, 0.5])
    row = toolbar.__panel__()
    buttons = row[1] if len(row) > 1 else row[0]
    names = [b.name for b in buttons]
    assert names == ["↶ Undo (1)", "↷ Redo (0)", "Reset"]
    assert not buttons[0].disabled
    assert buttons[1].disabled
    assert not buttons[2].disabled
    state.reset()
    assert next(b.name for b in toolbar.__panel__()[1]) == "↶ Undo (2)"


# ----------------------------------------------------------------------------------------------------------------------
# Files and exports


def test_load_and_save_round_trip(state, notes):
    panel = LoadSavePanel(state=state)
    state.load_preset("arrow_quad")
    saved = json.loads(panel._save_config().getvalue())
    assert saved["version"] == "1.1"
    assert saved["description"] == "arrow_quad"
    assert saved["edge_curve_modes"] == {"0": "linear", "1": "linear"}
    other = TilingState(notify=Notes())
    LoadSavePanel(state=other)._on_file_content(SimpleNamespace(new=json.dumps(saved)))
    assert other.draft == state.draft
    assert other.source_label == "arrow_quad"


def test_load_panel_updates_the_file_name_with_the_design(state, notes):
    panel = LoadSavePanel(state=state)
    panel._on_file_content(SimpleNamespace(new=json.dumps({"tiling_type": 41, "description": "my design"})))
    assert panel._filename_input.value == "my design"
    assert panel._json_download.filename == "my design.json"
    panel._on_file_content(SimpleNamespace(new=json.dumps({"tiling_type": 43})))
    assert panel._filename_input.value == "IH43"
    assert notes.levels() == ["success", "success"]


def test_load_panel_reports_unreadable_files(state, notes):
    panel = LoadSavePanel(state=state)
    panel._on_file_content(SimpleNamespace(new="not json"))
    panel._on_file_content(SimpleNamespace(new="[1, 2]"))
    panel._on_file_content(SimpleNamespace(new=""))
    assert notes.levels() == ["error", "error"]
    assert state.tiling_type == 10


def test_preview_strip_exports(state):
    strip = PreviewStrip(state=state)
    state.load_preset("puzzle_piece")
    svg = strip._generate_svg_export().getvalue()
    assert svg.startswith("<svg") and svg.count("<polygon") > 50
    geojson = json.loads(strip._generate_geojson_export().getvalue())
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == svg.count("<polygon")
    config = json.loads(strip._generate_config_json().getvalue())
    assert config["version"] == "1.1"
    assert config["description"] == "puzzle_piece"
    assert strip._generate_png_export().getvalue()[:4] == b"\x89PNG"
    namespace: dict = {}
    exec(strip._generate_python_code(), namespace)  # noqa: S102
    assert np.allclose(namespace["tiling"].prototile(), state.tiling.prototile())
    assert strip._py_code_display.object.startswith("```python\nfrom isotile import IsohedralTiling")


def test_preview_strip_follows_the_design_and_the_view(state):
    strip = PreviewStrip(state=state)
    state.load_preset("puzzle_piece")
    assert strip._py_code_display.object == strip._python_markdown()
    assert "IsohedralTiling(41" in strip._py_code_display.object
    assert strip._export_filename.value == "puzzle_piece"
    state.load_type(43)
    assert strip._export_filename.value == "IH43"
    assert strip._svg_download.filename == "IH43.svg"


# ----------------------------------------------------------------------------------------------------------------------
# The package


def test_package_has_no_reference_to_the_previous_libraries():
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for name in ("tactile", "carto_flow", "carto-flow", "_unit_tile", "_ih."):
            assert name not in text, f"{name} in {path.name}"
        assert "import shapely" not in text and "from shapely" not in text, path.name
        assert "print(" not in text, path.name


def test_app_builds_without_the_previous_libraries():
    code = (
        "import sys, isohedral_tiling_ui.app\n"
        "bad = {'carto_flow', 'tactile', 'shapely', 'geopandas', 'numba'} & set(sys.modules)\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, result.stderr[-2000:]
