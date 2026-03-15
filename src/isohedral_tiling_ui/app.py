"""Isohedral Tiling Generator - Panel + Bokeh UI."""

from __future__ import annotations

from pathlib import Path

import panel as pn

_FAVICON = str(Path(__file__).parent / "assets" / "favicon.svg")

from isohedral_tiling_ui.components import (
    CurveEditor,
    GalleryPanel,
    HistoryToolbar,
    LoadSavePanel,
    PreviewStrip,
    PrototileEditor,
    TileTypeSelector,
)
from isohedral_tiling_ui.state import TilingState

# Initialize Panel extension
pn.extension(
    "floatpanel",
    notifications=True,
    sizing_mode="stretch_width",
)

pn.extension("modal")
pn.extension("tabulator")

# Create reactive state
state = TilingState()

# Build sidebar
sidebar = pn.Column(
    TileTypeSelector(state=state),
    GalleryPanel(state=state),
    LoadSavePanel(state=state),
    width=290,
    sizing_mode="stretch_height",
)

preview = PreviewStrip(state=state)

# Build main content
main = pn.Column(
    pn.Row(
        pn.layout.HSpacer(),
        HistoryToolbar(state=state),
        pn.Spacer(width=20),
        preview,
        pn.layout.HSpacer(),
        sizing_mode="stretch_width",
    ),
    pn.Row(
        pn.layout.HSpacer(),  # Left spacer - expands
        PrototileEditor(state=state),
        pn.Spacer(width=20),
        CurveEditor(state=state),
        pn.layout.HSpacer(),  # Right spacer - expands
        sizing_mode="stretch_width",
    ),
    # preview._modal,
    sizing_mode="stretch_width",
)


# Create app layout
app = pn.template.FastListTemplate(
    title="Isohedral Tiling Generator",
    favicon=_FAVICON,
    logo=_FAVICON,
    sidebar=sidebar,
    main=main,
    sidebar_width=290,
    header_background="#1971c2",
    theme_toggle=False,
)

app.modal.append(preview._modal)


# 4. Define a callback function to open the modal
def open_modal_callback(event):
    app.open_modal()


# 5. Link the button click event to the callback
preview.settings_btn.on_click(open_modal_callback)

app.servable()
