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

import pytest

from isohedral_tiling_ui.utils.curve_presets import list_curve_presets

sync_api = pytest.importorskip("playwright.sync_api")

APP = Path(__file__).parents[2] / "src" / "isohedral_tiling_ui" / "app.py"
MUTED = 0.3  # alpha of the movable nodes that are not selected while one is


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
        """Id of the ButtonIcon model with a tag (``preset:<name>``, ``tile:<name>``, ``type:<number>``)."""
        return self.js(
            "(t) => [...Bokeh.documents[0].all_models].find(m => m.type === 'panel.models.icon.ButtonIcon'"
            " && m.tags.includes(t)).id",
            tag,
        )

    def tagged(self, kind: str) -> dict[str, dict]:
        """Style border and disabled flag of the ButtonIcon models by tag of a kind (``preset``, ``tile``, ``type``)."""
        return self.js(
            "(k) => Object.fromEntries([...Bokeh.documents[0].all_models].filter(m => m.type === "
            "'panel.models.icon.ButtonIcon' && m.tags.some(t => t.startsWith(k + ':'))).map(m => "
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
        self.click_model(self.button(f"preset:{name}"))

    def muted_presets(self) -> set[str]:
        """Names of the preset buttons shown as unavailable."""
        buttons = self.tagged("preset")
        assert len(buttons) == len(list_curve_presets())
        assert not any(b["disabled"] for b in buttons.values()), "the disabled property must not be used"
        return {n for n, b in buttons.items() if b["styles"].get("opacity")}

    def wait_for(self, read, expected, timeout: float = 8.0) -> None:
        """Wait until ``read()`` returns ``expected``; fail with the last value otherwise."""
        end = time.monotonic() + timeout
        while True:
            value = read()
            if value == expected or time.monotonic() > end:
                assert value == expected
                return
            time.sleep(0.2)

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
        """Index, fill color, alpha and position (screen) of the movable nodes of the curve editor."""
        return self.js(
            """() => {
            const cp = [...Bokeh.documents[0].all_models].find(
                m => m.type === 'ColumnDataSource' && m.data.editable && m.data.x.length);
            const fig = [...Bokeh.documents[0].all_models].find(m => m.type === 'Figure'
                && m.renderers.some(r => r.data_source === cp));
            const v = Bokeh.index.find_one(fig), r = v.canvas_view.el.getBoundingClientRect();
            const d = cp.data;
            return {idx: Array.from(d.idx), fill: Array.from(d.fill_color), line: Array.from(d.line_color),
                alpha: Array.from(d.alpha), x: Array.from(d.x), y: Array.from(d.y),
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
    assert len(set(nodes["fill"])) == 1 and set(nodes["alpha"]) == {1}  # no node is special
    n = app.undo_count()
    app.drag_node(1, 0, -40)  # drag the middle node up, release
    after = app.curve_nodes()
    assert after["y"][1] > nodes["y"][1] + 0.2
    assert app.undo_count() == n + 1
    # the dragged node stays selected, the others are muted
    assert after["fill"] == ["#444", "crimson", "#444"]
    assert after["alpha"] == [MUTED, 1, MUTED]
    # a drag of another node moves the selection
    app.drag_node(0, 0, 20)
    after = app.curve_nodes()
    assert after["fill"] == ["crimson", "#444", "#444"]
    assert after["alpha"] == [1, MUTED, MUTED]
    # a click on empty space deselects
    app.tap_empty()
    after = app.curve_nodes()
    assert set(after["fill"]) == {"#444"} and set(after["alpha"]) == {1}
    # a click on a node selects it
    nodes = app.curve_nodes()
    app.page.mouse.click(nodes["px"][2], nodes["py"][2])
    time.sleep(0.5)
    assert app.curve_nodes()["alpha"] == [MUTED, MUTED, 1]


def test_the_selection_survives_a_redraw_of_the_curve(app):
    app.click_preset("scalloped")
    app.drag_node(1, 0, -20)
    assert app.curve_nodes()["alpha"] == [MUTED, 1, MUTED]
    app.click_text("Undo")  # the curve is drawn again from the draft: the node still exists and stays selected
    app.wait_for(lambda: app.curve_nodes()["alpha"], [MUTED, 1, MUTED])
    app.click_text("Redo")
    app.wait_for(lambda: app.curve_nodes()["alpha"], [MUTED, 1, MUTED])
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
