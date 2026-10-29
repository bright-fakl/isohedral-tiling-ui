"""Tiling type selector component with filters."""

from __future__ import annotations

import math

import panel as pn
import param
from isotile import find_types, get_type, list_presets

NORMAL = {"border": "1px solid #eee", "background": "transparent"}
SELECTED = {"border": "2px solid #1971c2", "background": "#e7f1fb"}


class TileTypeSelector(pn.viewable.Viewer):
    """Filterable grid of tiling type buttons with colored shapes."""

    state = param.ClassSelector(class_=param.Parameterized)

    filter_vertices = param.Selector(default="All", objects=["All", "3", "4", "5", "6"])
    filter_params = param.Selector(default="All", objects=["All", "0", "1", "2", "3", "4", "5", "6"])
    filter_customizable = param.Boolean(default=True)

    def __init__(self, **params):
        super().__init__(**params)
        self._grid = pn.GridBox(ncols=6, nrows=0)
        self._buttons: dict[int, pn.widgets.ButtonIcon] = {}
        self._build_grid()
        self.state.param.watch(self._mark_selected, ["tiling_type", "source_label"])

    @param.depends("filter_vertices", "filter_params", "filter_customizable", watch=True)
    def _build_grid(self):
        """Rebuild the type grid when filters change."""
        v = int(self.filter_vertices) if self.filter_vertices != "All" else None
        p = int(self.filter_params) if self.filter_params != "All" else None
        has_cust = self.filter_customizable or None

        types = find_types(
            num_vertices=v,
            num_parameters=p,
            has_customizable_edges=has_cust,
        )

        # Keep current type in list
        if self.state.tiling_type not in types:
            types = [self.state.tiling_type, *types]

        self._buttons = {t: self._create_type_button(t, get_type(t).info()) for t in sorted(set(types))}
        self._mark_selected()
        self._grid.objects = list(self._buttons.values())

    def _mark_selected(self, *events):
        """Highlight the button of the current type unless the design is a gallery preset (the gallery shows that)."""
        if self.state.tiling_type not in self._buttons:
            self._build_grid()  # the filters hide the current type; the grid keeps it
            return
        from_gallery = self.state.source_label in list_presets()
        for number, button in self._buttons.items():
            button.styles = SELECTED if number == self.state.tiling_type and not from_gallery else NORMAL

    def _create_type_button(self, tiling_type: int, info: dict) -> pn.widgets.Button:
        """Create a clickable SVG button for a tiling type."""
        num_vertices = info["num_vertices"]
        colors = {3: "#f5b7b1", 4: "#aed6f1", 5: "#f9e79f", 6: "#abebc6"}
        color = colors.get(num_vertices, "#d5d8dc")

        # Generate polygon SVG - smaller size for tighter layout
        size, center, fontsize = 12, 14.0, 14
        pts = " ".join(
            f"{center + size * math.sin(2 * math.pi * i / num_vertices + math.pi / num_vertices):.1f},"
            f"{center + size * math.cos(2 * math.pi * i / num_vertices + math.pi / num_vertices):.1f}"
            for i in range(num_vertices)
        )

        svg = (
            f'<svg width="30" height="30" xmlns="http://www.w3.org/2000/svg">'
            f'<polygon points="{pts}" fill="{color}" stroke="white" stroke-width="1"/>'
            f'<text x="{center}" y="{center + 4}" text-anchor="middle" '
            f'font-family="Arial,sans-serif" font-size="{fontsize}" font-weight="700" '
            f'fill="#333">{tiling_type}</text>'
            f"</svg>"
        )

        # Create tooltip text
        num_params = info.get("num_parameters", 0)
        num_aspects = info.get("num_aspects", 0)
        customizable = info.get("customizable_shape_ids", [])
        tooltip_text = f"IH{tiling_type}: {num_vertices} vertices, {num_params} params, {num_aspects} aspects, {len(customizable)} editable edges"

        # Create a button with the SVG as its content - smaller and tighter
        btn = pn.widgets.ButtonIcon(
            icon=svg,
            active_icon=svg,  # the default active icon is a check mark that stays after a click
            width=30,
            height=30,
            margin=0,  # (1, 1, 1, 1),  # Tight spacing
            description=tooltip_text,  # Tooltip on hover
            on_click=lambda e, t=tiling_type: self._on_type_select(t),
            styles=NORMAL,
            align="center",
            tags=[f"type:{tiling_type}"],
        )

        return btn

    def _on_type_select(self, tiling_type: int):
        """Handle tiling type selection: the type at its defaults, a new baseline and a new history."""
        self.state.load_type(tiling_type)

    def __panel__(self):
        return pn.Card(
            pn.Row(
                pn.widgets.Select.from_param(self.param.filter_vertices, name="Vertices", width=80),
                pn.widgets.Select.from_param(self.param.filter_params, name="Params", width=80),
            ),
            pn.widgets.Checkbox.from_param(self.param.filter_customizable, name="Customizable edges only"),
            self._grid,
            title="Tiling Type",
            collapsed=True,
        )
