"""Tiling type selector component with filters."""

from __future__ import annotations

import math

import panel as pn
import param


class TileTypeSelector(pn.viewable.Viewer):
    """Filterable grid of tiling type buttons with colored shapes."""

    state = param.ClassSelector(class_=param.Parameterized)

    filter_vertices = param.Selector(default="All", objects=["All", "3", "4", "5", "6"])
    filter_params = param.Selector(default="All", objects=["All", "0", "1", "2", "3", "4", "5", "6"])
    filter_customizable = param.Boolean(default=True)

    def __init__(self, **params):
        super().__init__(**params)
        self._grid = pn.GridBox(ncols=6, nrows=0)
        self._build_grid()

    @param.depends("filter_vertices", "filter_params", "filter_customizable", watch=True)
    def _build_grid(self):
        """Rebuild the type grid when filters change."""
        from carto_flow.symbol_cartogram.tiling import IsohedralTiling

        v = int(self.filter_vertices) if self.filter_vertices != "All" else None
        p = int(self.filter_params) if self.filter_params != "All" else None
        has_cust = self.filter_customizable or None

        types = IsohedralTiling.find_types(
            num_vertices=v,
            num_parameters=p,
            has_customizable_edges=has_cust,
        )

        # Keep current type in list
        if self.state.tiling_type not in types:
            types = [self.state.tiling_type, *types]

        buttons = []
        for t in sorted(set(types)):
            info = IsohedralTiling.type_info(t)
            btn = self._create_type_button(t, info)
            buttons.append(btn)

        self._grid.objects = buttons

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
            width=30,
            height=30,
            margin=0,  # (1, 1, 1, 1),  # Tight spacing
            description=tooltip_text,  # Tooltip on hover
            on_click=lambda e, t=tiling_type: self._on_type_select(t),
            styles={"border": "1px solid #eee"},
            align="center",
        )

        # btn.stylesheets = [f"""
        #     button.bk-btn {{
        #         border: {border} !important;
        #         border-radius: 4px !important;
        #     }}
        # """]

        # btn = pn.widgets.Button(
        #     name="",
        #     width=30,
        #     height=30,
        #     margin=(1, 1, 1, 1),  # Tight spacing
        #     description=tooltip_text,  # Tooltip on hover
        #     on_click=lambda e, t=tiling_type: self._on_type_select(t),
        # )

        # # Embed SVG as background image using data URI - only use stylesheets, not styles
        # import base64
        # svg_bytes = svg.encode('utf-8')
        # svg_b64 = base64.b64encode(svg_bytes).decode('utf-8')
        # btn.stylesheets = [f"""
        #     button.bk-btn {{
        #         background-image: url("data:image/svg+xml;base64,{svg_b64}") !important;
        #         background-repeat: no-repeat !important;
        #         background-position: center !important;
        #         background-size: 24px 24px !important;
        #         background-color: {bg} !important;
        #         border: {border} !important;
        #         border-radius: 4px !important;
        #         width: 30px !important;
        #         height: 30px !important;
        #         padding: 2px !important;
        #     }}
        # """]
        return btn

    def _on_type_select(self, tiling_type: int):
        """Handle tiling type selection."""
        from carto_flow.symbol_cartogram.tiling import IsohedralTiling

        # Create new tiling and update all state at once
        tiling = IsohedralTiling(tiling_type)

        # Use batch update to avoid multiple preview regenerations
        with self.state.batch_update():
            # Update parameters BEFORE tiling_type to avoid triggering _update_tiling with wrong params
            # First set parameters, then tiling_type
            old_params = list(tiling._ih.parameters)
            self.state.parameters = old_params
            self.state.tiling_type = tiling_type
            self.state.edge_curves = {}
            self.state.edge_curve_modes = {}
            self.state.current_preset = ""
            # Save initial state
            self.state.set_initial_state(tiling)

        self.state.parameters_version += 1
        # Clear history and make new tile type the first entry
        self.state._history = [self.state._snapshot()]
        self.state._history_idx = 0
        print(f"DEBUG: History cleared and tile_type:IH{tiling_type} → index 0 (length: 1)")

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
