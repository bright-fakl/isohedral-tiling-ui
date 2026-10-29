"""Tests in a real browser (Chromium through Playwright): preset buttons, node colors, dragging, the highlight of tiles.

They need ``pip install playwright`` and a Chromium (``playwright install chromium``, or the path of one in
``ISOHEDRAL_CHROMIUM``) and are skipped otherwise. The app runs in a ``panel serve`` subprocess.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from isohedral_tiling_ui.utils.curve_presets import list_curve_presets

sync_api = pytest.importorskip("playwright.sync_api")

APP = Path(__file__).parents[2] / "src" / "isohedral_tiling_ui" / "app.py"


@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "panel", "serve", str(APP), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://localhost:{port}/app"
    try:
        for _ in range(20):
            try:
                urllib.request.urlopen(url, timeout=60)
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("the app did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=os.environ.get("ISOHEDRAL_CHROMIUM") or None)
        except sync_api.Error as e:
            pytest.skip(f"no Chromium: {e}")
        yield b
        b.close()


class App:
    """The page of the app and helpers to read the Bokeh models and to act on them like a user."""

    def __init__(self, page):
        self.page = page

    def js(self, code, arg=None):
        return self.page.evaluate(code, arg)

    # -- models ----------------------------------------------------------------------------------------

    def button(self, tag: str) -> str:
        """Id of the button model with a tag (``preset:<name>``, ``tile:<name>``, ``type:<number>``) once it is shown."""
        find = (
            "(t) => [...Bokeh.documents[0].all_models].find(m => m.tags && m.tags.includes(t)"
            " && Bokeh.index.find_one(m))"
        )
        self.page.wait_for_function(f"(t) => !!({find})(t)", arg=tag)
        return self.js(f"(t) => ({find})(t).id", tag)

    def tagged(self, kind: str) -> dict[str, dict]:
        """Style border and disabled flag of the ButtonIcon models by tag of a kind (``preset``, ``tile``, ``type``)."""
        return self.js(
            "(k) => Object.fromEntries([...Bokeh.documents[0].all_models].filter(m => m.tags && "
            "m.tags.some(t => typeof t === 'string' && t.startsWith(k + ':'))).map(m => "
            "[m.tags[0].slice(k.length + 1), {disabled: m.disabled, styles: m.styles || {}}]))",
            kind,
        )

    def marked(self, kind: str) -> list[str]:
        """Names of the buttons of a kind that carry the selection border."""
        return sorted(n for n, b in self.tagged(kind).items() if b["styles"].get("border", "").startswith("2px"))

    def click_model(self, model_id: str) -> None:
        x, y = self.js(
            "(id) => { const m = Bokeh.documents[0].get_model_by_id(id); const r = Bokeh.index.find_one(m)"
            ".el; r.scrollIntoView({block: 'center'}); const b = r.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; }",
            model_id,
        )
        self.page.mouse.click(x, y)
        time.sleep(0.4)

    def click_preset(self, name: str) -> None:
        """Click a preset; if it was available, wait until it is applied (it is then the current curve: muted)."""
        was_muted = name in self.muted_presets()
        self.click_model(self.button(f"preset:{name}"))
        if not was_muted:
            self.wait_for(lambda: name in self.muted_presets(), True)

    def muted_presets(self) -> set[str]:
        """Names of the preset buttons shown as unavailable."""
        buttons = self.tagged("preset")
        assert len(buttons) == len(list_curve_presets())
        assert not any(b["disabled"] for b in buttons.values()), "the disabled property must not be used"
        return {n for n, b in buttons.items() if b["styles"].get("opacity") == "0.35"}

    def wait_for(self, read, expected, timeout: float = 8.0) -> None:
        """Wait until ``read()`` returns ``expected``; fail with the last value otherwise."""
        end = time.monotonic() + timeout
        while True:
            value = read()
            if value == expected or time.monotonic() > end:
                assert value == expected
                return
            time.sleep(0.2)

    def click_until(self, tag: str, read, expected, tries: int = 3) -> None:
        """Click a button and wait for its effect; click again if a click was lost while the page was updating."""
        for attempt in range(tries):
            self.click_model(self.button(tag))
            try:
                self.wait_for(read, expected, timeout=4.0)
            except AssertionError:
                if attempt == tries - 1:
                    raise
            else:
                return

    def wait_muted(self, names: set[str]) -> None:
        self.wait_for(self.muted_presets, names)

    def undo_count(self) -> int:
        text = self.page.get_by_role("button", name="Undo").inner_text()
        return int(text.split("(")[1].rstrip(")"))

    def click_text(self, text: str) -> None:
        self.page.get_by_role("button", name=text).click()
        time.sleep(0.5)

    # -- the curve editor ------------------------------------------------------------------------------

    def curve_nodes(self) -> dict:
        """Index, fill color and position (screen) of the movable nodes of the curve editor."""
        return self.js(
            """() => {
            const cp = [...Bokeh.documents[0].all_models].find(
                m => m.type === 'ColumnDataSource' && m.data.editable && m.data.x.length);
            const fig = [...Bokeh.documents[0].all_models].find(m => m.type === 'Figure'
                && m.renderers.some(r => r.data_source === cp));
            const v = Bokeh.index.find_one(fig), r = v.canvas_view.el.getBoundingClientRect();
            const d = cp.data;
            return {idx: Array.from(d.idx), fill: Array.from(d.fill_color), line: Array.from(d.line_color),
                x: Array.from(d.x), y: Array.from(d.y),
                px: Array.from(d.x).map(x => r.x + v.frame.x_scale.compute(x)),
                py: Array.from(d.y).map(y => r.y + v.frame.y_scale.compute(y))};
            }"""
        )

    def drag_node(self, k: int, dx: float, dy: float, release: bool = True) -> None:
        nodes = self.curve_nodes()
        x, y = nodes["px"][k], nodes["py"][k]
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x + dx / 2, y + dy / 2, steps=5)
        self.page.mouse.move(x + dx, y + dy, steps=5)
        if release:
            self.page.mouse.up()
            time.sleep(0.8)

    def vertices(self) -> dict:
        """Data (and screen positions) of the movable vertices of the prototile editor."""
        return self.js(
            """() => {
            const fig = [...Bokeh.documents[0].all_models].find(m => m.type === 'Figure' && m.toolbar.tools.some(
                t => t.type === 'PointDrawTool' && !t.renderers[0].data_source.data.editable));
            const tool = fig.toolbar.tools.find(t => t.type === 'PointDrawTool');
            const d = tool.renderers[0].data_source.data;
            const v = Bokeh.index.find_one(fig), r = v.canvas_view.el.getBoundingClientRect();
            return {x: Array.from(d.x), y: Array.from(d.y),
                px: Array.from(d.x).map(x => r.x + v.frame.x_scale.compute(x)),
                py: Array.from(d.y).map(y => r.y + v.frame.y_scale.compute(y))};
            }"""
        )

    def drag_vertex(self, k: int, dx: float, dy: float) -> None:
        v = self.vertices()
        x, y = v["px"][k], v["py"][k]
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x + dx / 2, y + dy / 2, steps=5)
        self.page.mouse.move(x + dx, y + dy, steps=5)
        self.page.mouse.up()
        time.sleep(0.8)

    def curve_editor_state(self) -> dict:
        """Number of movable nodes, fixed nodes and curve points of the curve editor, and whether it is shown."""
        return self.js(
            """() => {
            const by = n => [...Bokeh.documents[0].all_models].find(m => m.name === n);
            const nodes = by('movable_nodes'), fig = [...Bokeh.documents[0].all_models].find(
                m => m.type === 'Figure' && m.renderers.some(r => r.data_source === nodes));
            const view = Bokeh.index.find_one(fig);
            return {nodes: nodes.data.x.length, fixed: by('fixed_nodes').data.x.length,
                curve: by('curve').data.x.length, shown: !!(view && view.el.offsetParent !== null)};
            }"""
        )

    def select_curve(self, label: str) -> None:
        self.page.locator("select", has=self.page.locator(f"option:text-is('{label}')")).first.select_option(
            label=label
        )
        time.sleep(0.8)

    def centering(self, tag: str) -> tuple[float, float]:
        """Offset (x, y) in px of the center of the icon from the center of the box of the button with a tag."""
        return self.js(
            """(t) => {
            const m = [...Bokeh.documents[0].all_models].find(m => m.tags && m.tags.includes(t));
            const host = Bokeh.index.find_one(m).el, h = host.getBoundingClientRect();
            const i = host.shadowRoot.querySelector('.bk-SVGIcon').getBoundingClientRect();
            return [i.x + i.width / 2 - (h.x + h.width / 2), i.y + i.height / 2 - (h.y + h.height / 2)];
            }""",
            tag,
        )

    def preset_opacities(self) -> dict[str, float]:
        """Computed opacity of each rendered preset button (a hidden button has no view and is left out)."""
        return self.js(
            """() => Object.fromEntries([...Bokeh.documents[0].all_models].filter(
                m => m.tags && m.tags.some(t => typeof t === 'string' && t.startsWith('preset:'))
            ).map(m => [m.tags[0].slice(7), Bokeh.index.find_one(m)]).filter(([, v]) => v).map(
                ([n, v]) => [n, parseFloat(getComputedStyle(v.el).opacity)]))"""
        )

    def expect_presets_match(self, unavailable: set[str]) -> None:
        """The computed opacity is below 1 for the unavailable presets and 1 for the others."""
        names = set(list_curve_presets())

        def read():
            return {n for n, o in self.preset_opacities().items() if o < 1}

        self.wait_for(read, unavailable)
        assert set(self.preset_opacities()) == names

    def vertex_and_outline(self, k: int) -> tuple[list[float], list[float]]:
        """Position of the k-th movable handle of the prototile editor and of the same vertex in the outline."""
        d = self.js(
            """(k) => {
            const fig = [...Bokeh.documents[0].all_models].find(m => m.type === 'Figure' && m.toolbar.tools.some(
                t => t.type === 'PointDrawTool' && !t.renderers[0].data_source.data.editable));
            const d = fig.toolbar.tools.find(t => t.type === 'PointDrawTool').renderers[0].data_source.data;
            const out = fig.renderers.map(r => r.data_source).find(
                s => s.data.xs && s.data.xs.length === 1 && s.data.xs[0].length > 4);
            const i = d.idx[k];
            return [[d.x[k], d.y[k]], [out.data.xs[0][i], out.data.ys[0][i]]];
            }""",
            k,
        )
        return d[0], d[1]

    def validity(self) -> dict:
        """The red display: the color of the dragged curve, the red edges of the prototile editor, the status text."""
        shown = self.js(
            """() => {
            const all = [...Bokeh.documents[0].all_models];
            const fig = all.find(m => m.type === 'Figure' && m.renderers.some(
                r => r.data_source && r.data_source.name === 'curve'));
            const r = fig.renderers.find(r => r.data_source && r.data_source.name === 'curve');
            const filters = all.filter(m => m.type === 'BooleanFilter');
            return {curve: r.glyph.line_color.value, bad: filters.map(f => Array.from(f.booleans))};
            }"""
        )
        shown["status"] = self.page.get_by_text("Invalid:").count() > 0
        shown["red_edges"] = sum(sum(b) for b in shown["bad"])
        return shown

    def curved_edges(self) -> dict:
        """Colors, first and last points and a middle point of the curved edges of the prototile editor."""
        return self.js(
            """() => {
            const all = [...Bokeh.documents[0].all_models];
            const c = all.find(m => m.type === 'ColumnDataSource' && m.data.xs && m.data.line_width && m.data.edge_idx);
            const poly = all.find(m => m.type === 'ColumnDataSource' && m.data.xs && m.data.xs.length === 1
                && m.data.xs[0].length > 3);
            const ends = c.data.xs.map((xs, i) => [[xs[0], c.data.ys[i][0]], [xs[xs.length - 1], c.data.ys[i][xs.length - 1]]]);
            const mid = c.data.xs.map((xs, i) => [xs[xs.length >> 1], c.data.ys[i][xs.length >> 1]]);
            return {colors: Array.from(c.data.line_color), ends: ends, mid: mid,
                    polygon: Array.from(poly.data.xs[0], (x, i) => [x, poly.data.ys[0][i]])};
            }"""
        )

    def toasts(self) -> list[str]:
        return self.page.get_by_text("Not applied").all_inner_texts()

    def probe_log(self) -> list[float]:
        """Round-trip times in ms of the probes of this page."""
        return self.js("() => (window._isoProbeLog || []).slice()")

    def tap_empty(self) -> None:
        nodes = self.curve_nodes()
        self.page.mouse.click(nodes["px"][0] - 30, nodes["py"][0] - 80)
        time.sleep(0.5)


@pytest.fixture
def app(browser, server):
    page = browser.new_page(viewport={"width": 1700, "height": 1100})
    page.goto(server)
    page.wait_for_function(
        "window.Bokeh && Bokeh.documents.length && [...Bokeh.documents[0].all_models].some(m => m.type === 'Figure')",
        timeout=60000,
    )
    time.sleep(2)
    yield App(page)
    page.close()


def test_presets_stay_available_after_clicks(app):
    app.wait_muted({"straight"})  # the hexagon edge is straight
    app.click_preset("scalloped")
    app.wait_muted({"scalloped"})
    app.click_preset("straight")
    app.wait_muted({"straight"})  # the clicked button of before is available again
    app.click_preset("puzzle")
    app.wait_muted({"puzzle"})
    app.click_text("Undo")
    app.wait_muted({"straight"})
    app.click_text("Redo")
    app.wait_muted({"puzzle"})
    app.click_text("Reset")
    app.wait_muted({"straight"})
    # a muted button does nothing
    n = app.undo_count()
    app.click_preset("straight")
    assert app.undo_count() == n


def test_nodes_have_one_color_and_the_selection_survives_a_drag(app):
    app.click_preset("scalloped")
    nodes = app.curve_nodes()
    assert set(nodes["fill"]) == {"#444"}  # no node is special
    app.wait_for(app.undo_count, 1)
    n = 1
    app.drag_node(1, 0, -40)  # drag the middle node up, release
    after = app.curve_nodes()
    assert after["y"][1] > nodes["y"][1] + 0.2
    assert app.undo_count() == n + 1
    # the dragged node stays selected; the others keep the color of the unselected state
    assert after["fill"] == ["#444", "crimson", "#444"]
    # a drag of another node moves the selection
    app.drag_node(0, 0, 20)
    assert app.curve_nodes()["fill"] == ["crimson", "#444", "#444"]
    # a click on empty space deselects
    app.tap_empty()
    assert set(app.curve_nodes()["fill"]) == {"#444"}
    # a click on a node selects it
    nodes = app.curve_nodes()
    app.page.mouse.click(nodes["px"][2], nodes["py"][2])
    time.sleep(0.5)
    assert app.curve_nodes()["fill"] == ["#444", "#444", "crimson"]


def test_nodes_are_selected_with_shift_and_moved_together(app):
    app.click_preset("scalloped")
    nodes = app.curve_nodes()
    app.page.mouse.click(nodes["px"][0], nodes["py"][0])
    time.sleep(0.6)  # two clicks within 300 ms are a double click
    app.page.keyboard.down("Shift")
    app.page.mouse.click(nodes["px"][2], nodes["py"][2])
    app.page.keyboard.up("Shift")
    time.sleep(0.5)
    assert app.curve_nodes()["fill"] == ["crimson", "#444", "crimson"]
    n = app.undo_count()
    app.drag_node(0, 0, -30)  # dragging one selected node moves both
    after = app.curve_nodes()
    assert after["fill"] == ["crimson", "#444", "crimson"]  # the selection survives the drag
    shift = [a - b for a, b in zip(after["y"], nodes["y"], strict=True)]
    assert shift[0] > 0.2 and shift[2] == pytest.approx(shift[0], abs=1e-6) and shift[1] == pytest.approx(0, abs=1e-9)
    app.wait_for(app.undo_count, n + 1)
    app.drag_node(2, 0, 20)  # and again, from the kept selection
    again = app.curve_nodes()
    moved = [a - b for a, b in zip(again["y"], after["y"], strict=True)]
    assert moved[0] < -0.1 and moved[2] == pytest.approx(moved[0], abs=1e-6) and moved[1] == pytest.approx(0, abs=1e-9)
    # a plain click on one node selects only that node
    app.page.mouse.click(again["px"][1], again["py"][1])
    time.sleep(0.5)
    assert app.curve_nodes()["fill"] == ["#444", "crimson", "#444"]


def test_the_selection_survives_a_redraw_of_the_curve(app):
    app.click_preset("scalloped")
    app.drag_node(1, 0, -20)
    assert app.curve_nodes()["fill"] == ["#444", "crimson", "#444"]
    app.click_text("Undo")  # the curve is drawn again from the draft: the node still exists and stays selected
    app.wait_for(lambda: app.curve_nodes()["fill"], ["#444", "crimson", "#444"])
    app.click_text("Redo")
    app.wait_for(lambda: app.curve_nodes()["fill"], ["#444", "crimson", "#444"])
    app.click_text("Undo")
    app.click_text("Undo")  # back to the straight curve: no movable nodes
    app.wait_for(lambda: app.undo_count(), 0)


def test_an_invalid_drag_is_refused_and_the_previous_draft_kept(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:36"))
    app.wait_for(lambda: app.marked("type"), ["36"])
    app.click_preset("scalloped")
    app.wait_for(app.undo_count, 1)
    before = app.curve_nodes()
    app.drag_node(1, 0, -60)  # the middle node far up: the prototile would be invalid
    time.sleep(1)
    after = app.curve_nodes()
    assert app.undo_count() == 1
    assert after["x"] == before["x"] and after["y"] == before["y"]
    assert app.page.get_by_text("Not applied").count() >= 1
    app.drag_node(1, 0, -8)  # a small drag is valid: it changes the draft
    app.wait_for(app.undo_count, 2)
    assert app.curve_nodes()["y"][1] > before["y"][1]


def test_dragging_a_prototile_vertex_changes_the_draft(app):
    app.click_model(app.button("tile:square"))
    app.wait_for(lambda: app.marked("tile"), ["square"])
    vertices = app.vertices()
    assert vertices["x"], "the square has movable vertices"
    n = app.undo_count()
    app.drag_vertex(0, 25, 10)
    app.wait_for(app.undo_count, n + 1)
    moved = app.vertices()
    assert (moved["x"][0], moved["y"][0]) != (vertices["x"][0], vertices["y"][0])


def test_the_gallery_tile_of_the_design_is_the_marked_one(app):
    app.wait_for(lambda: app.marked("tile"), ["regular_hexagon"])
    assert app.marked("type") == []  # the type panel marks nothing for a gallery preset
    app.click_model(app.button("tile:tree"))
    app.wait_for(lambda: app.marked("tile"), ["tree"])
    assert app.marked("type") == []
    app.click_text("Tiling Type")  # open the card
    app.click_model(app.button("type:41"))
    app.wait_for(lambda: app.marked("type"), ["41"])
    assert app.marked("tile") == []
    app.click_model(app.button("tile:square"))
    app.wait_for(lambda: app.marked("tile"), ["square"])
    assert app.marked("type") == []


def test_tiles_and_types_stay_selectable_after_being_selected(app):
    app.click_text("Tiling Type")
    for tag, kind in [
        ("tile:tree", "tile"),
        ("tile:square", "tile"),
        ("tile:tree", "tile"),
        ("type:41", "type"),
        ("type:4", "type"),
        ("type:41", "type"),
        ("tile:square", "tile"),
        ("tile:square", "tile"),
        ("tile:tree", "tile"),
        ("type:4", "type"),
    ]:
        app.click_model(app.button(tag))
        app.wait_for(lambda k=kind: app.marked(k), [tag.split(":")[1]])
        other = "type" if kind == "tile" else "tile"
        assert app.marked(other) == []
        buttons = app.tagged(kind)
        assert not any(b["disabled"] for b in buttons.values())
    # after an edit the same tile loads again (a new baseline)
    app.click_model(app.button("tile:tree"))
    app.wait_for(app.undo_count, 0)
    available = [n for n in list_curve_presets() if n not in app.muted_presets()]
    app.click_preset(available[0])  # an edit of the tree
    app.wait_for(app.undo_count, 1)
    app.click_until("tile:tree", app.undo_count, 0)


def test_a_tile_without_editable_curves_clears_the_curve_editor(app):
    app.click_preset("scalloped")
    assert len(app.curve_nodes()["x"]) == 3
    app.click_model(app.button("tile:equilateral_triangle"))  # type 93: no editable edge
    app.wait_for(lambda: app.marked("tile"), ["equilateral_triangle"])
    state = app.curve_editor_state()
    assert state == {"nodes": 0, "fixed": 0, "curve": 0, "shown": False}
    app.wait_for(app.muted_presets, set(list_curve_presets()))
    n = app.undo_count()
    app.click_preset("scalloped")  # no effect
    assert app.undo_count() == n
    # a tile with curves again, then a type without curves, undo and reset
    app.click_model(app.button("tile:wavy_triangle"))
    app.wait_for(lambda: app.curve_editor_state()["shown"], True)
    assert app.curve_editor_state()["curve"] > 2
    app.click_text("Tiling Type")
    app.click_model(app.button("type:93"))
    app.wait_for(lambda: app.curve_editor_state(), {"nodes": 0, "fixed": 0, "curve": 0, "shown": False})


def test_preset_availability_follows_the_selected_edge(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:41"))  # two free edges C0 and C1
    app.wait_for(lambda: app.marked("type"), ["41"])
    app.click_preset("scalloped")  # C0
    app.wait_for(app.undo_count, 1)
    assert "scalloped" in app.muted_presets() and "straight" not in app.muted_presets()
    app.select_curve("C1 (J)")
    app.wait_for(lambda: "scalloped" in app.muted_presets(), False)
    assert "straight" in app.muted_presets()  # C1 is still straight
    app.click_preset("scalloped_down")
    app.wait_for(lambda: "scalloped_down" in app.muted_presets(), True)
    assert "straight" not in app.muted_presets()
    app.select_curve("C0 (J)")
    app.wait_for(lambda: "scalloped" in app.muted_presets(), True)
    assert "scalloped_down" not in app.muted_presets() and "straight" not in app.muted_presets()


def test_icons_are_centered_in_their_buttons(app):
    app.click_text("Tiling Type")
    for tag in ["tile:regular_hexagon", "tile:tree", "type:10", "type:41", "preset:scalloped", "preset:straight"]:
        dx, dy = app.centering(tag)
        assert abs(dx) < 1.0 and abs(dy) < 1.0, (tag, dx, dy)


def test_presets_are_muted_only_where_unavailable(app):
    from isohedral_tiling_ui.editor_model import preset_availability
    from isohedral_tiling_ui.state import TilingState

    app.click_text("Tiling Type")
    for number in (41, 4, 12):  # J edges; S and J edges; U and J edges
        app.click_model(app.button(f"type:{number}"))
        app.wait_for(lambda n=str(number): app.marked("type"), [str(number)])
        state = TilingState()
        state.load_type(number)
        for curve_id in state.type_info["customizable_shape_ids"]:
            if curve_id != state.type_info["customizable_shape_ids"][0]:
                app.select_curve(f"C{curve_id} ({state.shape_type(curve_id)})")
            expected = {n for n, reason in preset_availability(state.draft, curve_id).items() if reason}
            app.wait_for(app.muted_presets, expected)
            assert expected < set(list_curve_presets())  # not all of them


def test_a_dragged_vertex_stays_on_the_vertex_the_parameters_allow(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:16"))  # one parameter: the vertices cannot follow the cursor freely
    app.wait_for(lambda: app.marked("type"), ["16"])
    v = app.vertices()
    k = 0
    x, y = v["px"][k], v["py"][k]
    app.page.mouse.move(x, y)
    app.page.mouse.down()
    app.page.mouse.move(x + 20, y - 15, steps=6)
    app.page.mouse.move(x + 40, y - 30, steps=6)
    handle, vertex = app.vertex_and_outline(k)  # during the drag
    assert handle == pytest.approx(vertex, abs=1e-6)
    n = app.undo_count()
    app.page.mouse.up()
    app.wait_for(app.undo_count, n + 1)
    handle, vertex = app.vertex_and_outline(k)  # after the release
    assert handle == pytest.approx(vertex, abs=1e-6)


def test_presets_are_not_muted_after_a_tile_without_curves(app):
    from isohedral_tiling_ui.editor_model import preset_availability
    from isohedral_tiling_ui.state import TilingState

    def expected(number=None, preset=None):
        state = TilingState()
        state.load_type(number) if number else state.load_preset(preset)
        cid = state.active_curve_id
        return {n for n, r in preset_availability(state.draft, cid).items() if r}

    app.click_model(app.button("tile:equilateral_triangle"))
    app.wait_for(lambda: app.marked("tile"), ["equilateral_triangle"])
    app.wait_for(lambda: app.curve_editor_state()["shown"], False)
    app.click_text("Tiling Type")
    for number in (41, 4, 12, 36):  # J; S and J; U and J; J
        app.click_model(app.button("tile:equilateral_triangle"))
        app.wait_for(lambda: app.curve_editor_state()["shown"], False)
        app.click_model(app.button(f"type:{number}"))
        app.wait_for(lambda n=str(number): app.marked("type"), [str(number)])
        app.wait_for(lambda: app.curve_editor_state()["shown"], True)
        app.expect_presets_match(expected(number))
    # the other ways back: a tile, undo and redo of a load do not exist (a load starts a new history), so a tile
    app.click_model(app.button("tile:equilateral_triangle"))
    app.wait_for(lambda: app.curve_editor_state()["shown"], False)
    app.click_model(app.button("tile:wavy_triangle"))
    app.wait_for(lambda: app.curve_editor_state()["shown"], True)
    app.expect_presets_match(expected(preset="wavy_triangle"))


def drag_and_hold(app, x, y, moves):
    """Press at (x, y) and move along ``moves`` (relative offsets) without releasing."""
    app.page.mouse.move(x, y)
    app.page.mouse.down()
    for dx, dy in moves:
        app.page.mouse.move(x + dx, y + dy, steps=6)
    time.sleep(0.8)


def test_a_node_dragged_into_an_invalid_position_is_red_and_reverted_on_release(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:36"))
    app.wait_for(lambda: app.marked("type"), ["36"])
    app.click_preset("scalloped")
    app.wait_for(app.undo_count, 1)
    before = app.curve_nodes()
    assert app.validity()["curve"] == "steelblue" and app.validity()["red_edges"] == 0
    drag_and_hold(app, before["px"][1], before["py"][1], [(0, -20), (0, -60)])
    shown = app.validity()
    assert shown["curve"] == "#d62728"  # the dragged curve
    assert shown["red_edges"] > 0 and shown["status"]  # the edges in the prototile editor and the message
    app.page.mouse.up()
    time.sleep(0.8)
    after = app.curve_nodes()
    assert after["y"] == before["y"] and after["x"] == before["x"]  # back at the last valid position
    assert app.undo_count() == 1  # nothing was committed
    assert any("Not applied" in t and "C0" in t and "edge" in t for t in app.toasts()), app.toasts()
    assert app.validity()["curve"] == "#d62728"  # the red display stays after the release
    app.wait_for(lambda: app.validity()["curve"], "steelblue")  # and goes after 1.5 s
    assert app.validity()["red_edges"] == 0 and not app.validity()["status"]


def test_red_clears_when_the_dragged_node_is_valid_again(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:36"))
    app.wait_for(lambda: app.marked("type"), ["36"])
    app.click_preset("scalloped")
    before = app.curve_nodes()
    x, y = before["px"][1], before["py"][1]
    drag_and_hold(app, x, y, [(0, -60)])
    assert app.validity()["curve"] == "#d62728"
    app.page.mouse.move(x, y - 5, steps=6)  # back to a valid position, still pressed
    app.wait_for(lambda: app.validity()["curve"], "steelblue")
    assert app.validity()["red_edges"] == 0
    app.page.mouse.up()
    app.wait_for(app.undo_count, 2)  # a valid release commits


def vertex_pixels(app, number: int, parameter_change: float):
    """Screen offset of the first movable handle for a change of the parameter (the vertices are affine in it)."""
    from isohedral_tiling_ui.editor_model import parameter_groups

    group = parameter_groups(number)[0]
    handles = app.vertices()
    indices = app.js(
        """() => {
        const fig = [...Bokeh.documents[0].all_models].find(m => m.type === 'Figure' && m.toolbar.tools.some(
            t => t.type === 'PointDrawTool' && !t.renderers[0].data_source.data.editable));
        return Array.from(fig.toolbar.tools.find(t => t.type === 'PointDrawTool').renderers[0].data_source.data.idx);
        }"""
    )
    vertex = indices[0]
    dx, dy = group["jacobian"][(vertex, group["params"][0])]
    scale = abs((handles["py"][1] - handles["py"][0]) / (handles["y"][1] - handles["y"][0]))  # px per unit
    return handles["px"][0], handles["py"][0], dx * parameter_change * scale, -dy * parameter_change * scale


def test_a_vertex_dragged_to_an_invalid_parameter_is_red_and_reverted_on_release(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:30"))  # valid for the parameter up to 0.487; the default is 0.231
    app.wait_for(lambda: app.marked("type"), ["30"])
    x, y, dx, dy = vertex_pixels(app, 30, 0.4)
    before = app.vertices()
    n = app.undo_count()
    drag_and_hold(app, x, y, [(dx, dy)])
    shown = app.validity()
    assert shown["red_edges"] > 0 and shown["status"]
    app.page.mouse.up()
    time.sleep(1.0)
    assert app.undo_count() == n  # nothing was committed
    assert app.vertices()["x"] == pytest.approx(before["x"])  # the handles are back
    assert any("Not applied" in t and "C0" in t for t in app.toasts()), app.toasts()
    app.wait_for(lambda: app.validity()["red_edges"], 0)


def test_a_vertex_dragged_to_a_valid_parameter_has_no_red(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:30"))
    app.wait_for(lambda: app.marked("type"), ["30"])
    x, y, dx, dy = vertex_pixels(app, 30, 0.1)
    n = app.undo_count()
    drag_and_hold(app, x, y, [(dx, dy)])
    assert app.validity()["red_edges"] == 0 and not app.validity()["status"]
    app.page.mouse.up()
    app.wait_for(app.undo_count, n + 1)


def test_the_slider_cannot_reach_a_value_that_makes_the_prototile_invalid(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:30"))
    app.wait_for(lambda: app.marked("type"), ["30"])
    handle = app.page.locator(".noUi-handle").first
    box = handle.bounding_box()
    track = app.page.locator(".noUi-base").first.bounding_box()
    app.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    app.page.mouse.down()
    app.page.mouse.move(track["x"] + track["width"] - 2, box["y"] + box["height"] / 2, steps=12)  # to the end
    time.sleep(1.0)
    app.page.mouse.up()
    time.sleep(1.0)
    value = app.js("() => [...Bokeh.documents[0].all_models].find(m => m.type === 'Slider' && m.step).value")
    assert 0.35 < value <= 0.49  # held at the limit (0.487), not at 1
    assert app.validity()["red_edges"] == 0
    assert not any("Not applied" in t for t in app.toasts())


def test_probe_round_trip_is_measured(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:36"))
    app.wait_for(lambda: app.marked("type"), ["36"])
    app.click_preset("scalloped")
    nodes = app.curve_nodes()
    x, y = nodes["px"][1], nodes["py"][1]
    app.page.mouse.move(x, y)
    app.page.mouse.down()
    for i in range(1, 41):  # a slow drag of 2.4 s with moves of 1 px every 60 ms
        app.page.mouse.move(x, y - 8 - i)
        time.sleep(0.06)
    log = app.probe_log()
    app.page.mouse.up()
    assert 8 <= len(log) <= 25
    median = sorted(log)[len(log) // 2]
    print(f"probe round trip: n={len(log)} median {median:.0f} ms, max {max(log):.0f} ms")
    assert median < 500


def test_the_curved_edges_follow_a_vertex_drag_live(app):
    app.click_model(app.button("tile:flowers"))
    app.wait_for(lambda: app.marked("tile"), ["flowers"])
    start = app.curved_edges()
    handles = app.vertices()
    drag_and_hold(app, handles["px"][0], handles["py"][0], [(10, -10), (25, -20)])
    live = app.curved_edges()  # the mouse is still pressed
    assert live["mid"] != start["mid"]  # the curves moved with the vertices
    for ends, (a, b) in zip(live["ends"], zip(live["polygon"], live["polygon"][1:], strict=False), strict=False):
        assert ends[0] == pytest.approx(a, abs=1e-6) and ends[1] == pytest.approx(b, abs=1e-6)  # attached
    app.page.mouse.up()
    app.wait_for(app.undo_count, 1)
    final = app.curved_edges()  # after the release the outline is that of the new draft
    assert np.allclose(final["mid"], live["mid"], atol=1e-3)


def test_the_curved_edges_follow_a_node_drag_live(app):
    app.click_model(app.button("tile:flowers"))
    app.wait_for(lambda: app.marked("tile"), ["flowers"])
    start = app.curved_edges()
    nodes = app.curve_nodes()
    drag_and_hold(app, nodes["px"][0], nodes["py"][0], [(0, -10), (0, -40)])
    live = app.curved_edges()
    assert live["mid"][0] != start["mid"][0]  # edge 0 follows the dragged curve
    assert live["mid"][1:] == start["mid"][1:]  # the other shape ids are untouched
    app.page.mouse.up()
    app.wait_for(lambda: bool(np.allclose(app.curved_edges()["mid"][0], live["mid"][0], atol=1e-3)), True)


def test_invalid_curved_edges_are_red_for_every_copy(app):
    app.click_text("Tiling Type")
    app.click_model(app.button("type:41"))
    app.wait_for(lambda: app.marked("type"), ["41"])
    app.click_preset("scalloped")
    nodes = app.curve_nodes()
    drag_and_hold(app, nodes["px"][1], nodes["py"][1], [(40, 0), (110, 0)])
    colors = app.curved_edges()["colors"]
    red = colors.count("#d62728")
    assert red >= 2 and red % 2 == 0  # every copy of each shape id that the check names (two edges per id here)
    assert red == app.validity()["red_edges"]  # the same edges as the hidden straight overlay
    app.page.mouse.up()
    app.wait_for(lambda: "#d62728" in app.curved_edges()["colors"], False)  # cleared after the hold


def slider_values(app) -> list[float]:
    return app.js(
        "() => [...Bokeh.documents[0].all_models].filter(m => m.type === 'Slider' && m.step && Bokeh.index.find_one(m)).map(m => m.value)"
    )


def slider_windows(app) -> list[list[float]]:
    return app.js(
        "() => [...Bokeh.documents[0].all_models].filter(m => m.type === 'Slider' && m.step && Bokeh.index.find_one(m)).map(m => [m.start, m.end])"
    )


def type_in_field(app, k: int, text: str) -> None:
    """Type into the k-th numeric field of the parameter sliders and press Enter."""
    app.page.wait_for_function(
        """(k) => { const m = [...Bokeh.documents[0].all_models].filter(m => m.type === 'Spinner' && Bokeh.index.find_one(m))[k];
        return m && Bokeh.index.find_one(m); }""",
        arg=k,
    )
    x, y = app.js(
        """(k) => {
        const m = [...Bokeh.documents[0].all_models].filter(m => m.type === 'Spinner' && Bokeh.index.find_one(m))[k];
        const r = Bokeh.index.find_one(m).el.getBoundingClientRect();
        return [r.x + r.width / 2, r.y + r.height / 2];
        }""",
        k,
    )
    app.page.mouse.click(x, y)
    app.page.keyboard.press("Control+A")
    app.page.keyboard.type(text)
    app.page.keyboard.press("Enter")


def test_a_parameter_can_be_typed_beyond_the_old_range_and_the_window_follows(app):
    app.click_model(app.button("tile:flowers"))
    app.wait_for(lambda: app.marked("tile"), ["flowers"])
    assert slider_windows(app)[1] == pytest.approx([0.0, 2.0])  # valid beyond 1: the window reaches 2
    type_in_field(app, 1, "1.6")
    app.wait_for(lambda: round(slider_values(app)[1], 2), 1.6)
    type_in_field(app, 1, "0.05")  # below the minimum 0.165: refused, the value stays
    time.sleep(1.0)
    assert slider_values(app)[1] == pytest.approx(1.6)
    assert any("Not applied" in t for t in app.toasts())
    app.page.get_by_role("button", name="Zoom out x2").click()
    app.wait_for(lambda: slider_windows(app)[1][1] >= 3.0, True)
    app.page.get_by_role("button", name="Zoom reset").click()
    app.wait_for(lambda: slider_windows(app)[1][1] >= 1.6, True)  # the window still holds the value
