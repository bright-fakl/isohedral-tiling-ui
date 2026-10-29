"""One line under the toolbar that says why an edit that is being made, or was refused, gives an invalid prototile."""

from __future__ import annotations

import html

import panel as pn
import param


class StatusLine(pn.viewable.Viewer):
    """The message of ``state.validity``, red, or an empty line of the same height."""

    state = param.ClassSelector(class_=param.Parameterized)

    def __init__(self, **params):
        super().__init__(**params)
        self._pane = pn.pane.HTML("", height=36, margin=(0, 5), sizing_mode="stretch_width")
        self._show()

    @param.depends("state.validity", watch=True)
    def _show(self) -> None:
        validity = self.state.validity
        if validity is None:
            self._pane.object = "&nbsp;"
        else:
            text = html.escape(validity.describe())
            self._pane.object = f"<span style='color:#d62728;font-size:13px'>Invalid: {text}</span>"

    def __panel__(self):
        return self._pane
