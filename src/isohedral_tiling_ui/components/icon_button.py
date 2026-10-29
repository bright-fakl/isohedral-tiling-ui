"""A flat button that shows an SVG icon.

``pn.widgets.ButtonIcon`` is not used: its browser model sets ``disabled`` and ``value`` itself on every click and
resets them 75 ms later, and the values synchronized back to the server can leave the button disabled, so that later
clicks do nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import panel as pn

FLAT = """
.bk-btn { padding: 0 !important; background: transparent !important; border: none !important;
          box-shadow: none !important; width: 100%; height: 100%; display: flex; align-items: center;
          justify-content: center; }
.bk-btn:hover { background: rgba(25, 113, 194, 0.10) !important; }
"""


def icon_button(
    svg: str, size: int, on_click: Callable[[Any], None], width: int, height: int, **params: Any
) -> pn.widgets.Button:
    """A button showing ``svg`` at ``size`` px, with the box ``width`` x ``height`` and the given Button params."""
    button = pn.widgets.Button(
        name="", icon=svg, icon_size=f"{size}px", width=width, height=height, stylesheets=[FLAT], **params
    )
    button.on_click(on_click)
    return button
