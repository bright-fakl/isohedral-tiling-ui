"""Gallery of preset tilings with prototile previews."""

from __future__ import annotations

import panel as pn
import param


class GalleryPanel(pn.viewable.Viewer):
    """Gallery of preset tilings with prototile previews."""

    state = param.ClassSelector(class_=param.Parameterized)

    def __init__(self, **params):
        super().__init__(**params)
        self._grid = pn.GridBox(ncols=5, nrows=0)  # 5 columns for snug fit
        self._build_gallery()

    def _build_gallery(self):
        """Build the gallery grid."""
        from carto_flow.symbol_cartogram.tiling import IsohedralTiling

        presets = sorted(IsohedralTiling.list_presets().items())
        items = []

        for name, _ in presets:
            tiling = IsohedralTiling.from_preset(name)
            # Get vertex count from the base tiling type (not from preset)
            info = IsohedralTiling.type_info(tiling._type)
            num_vertices = info.get("num_vertices", 4)

            svg = self._create_prototile_svg(name, num_vertices)
            # Format preset name for tooltip
            display_name = name.replace("_", " ").title()

            btn = pn.widgets.ButtonIcon(
                icon=svg,
                width=40,
                height=40,
                margin=(1, 1, 1, 1),  # Tight spacing
                description=display_name,  # Tooltip on hover
                on_click=lambda e, n=name: self.state.load_preset(n),
                styles={"border": "1px solid #eee"},
                align="center",
            )

            # btn = pn.widgets.Button(
            #     name="",
            #     width=36,
            #     height=36,
            #     margin=(1, 1, 1, 1),
            #     description=display_name,  # Tooltip on hover
            #     on_click=lambda e, n=name: self.state.load_preset(n),
            # )
            # btn.stylesheets = [f"""
            #     button.bk-btn {{
            #         background-image: url("data:image/svg+xml;base64,{svg_b64}") !important;
            #         background-repeat: no-repeat !important;
            #         background-position: center !important;
            #         background-size: 32px 32px !important;
            #         background-color: {bg} !important;
            #         border: {border} !important;
            #         border-radius: 4px !important;
            #         width: 36px !important;
            #         height: 36px !important;
            #         padding: 2px !important;
            #     }}
            #     button.bk-btn:hover {{
            #         border-color: #1971c2 !important;
            #     }}
            # """]
            items.append(btn)

        self._grid.objects = items

    def _create_prototile_svg(self, preset_name: str, num_vertices: int) -> str:
        """Generate SVG of prototile polygon with color based on vertex count."""
        from carto_flow.symbol_cartogram.tiling import IsohedralTiling

        tiling = IsohedralTiling.from_preset(preset_name)
        verts = list(tiling._unit_tile.exterior.coords[:-1])

        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0

        size = 38
        pad = 2
        scale = (size - 4) / span
        pts = " ".join(f"{pad + (x - min(xs)) * scale:.1f},{pad + (max(ys) - y) * scale:.1f}" for x, y in verts)

        colors = {3: "#f5b7b1", 4: "#aed6f1", 5: "#f9e79f", 6: "#abebc6"}
        fill_color = colors.get(num_vertices, "#d5d8dc")

        return (
            f'<svg width="{size}" height="{size}" xmlns="http://www.w3.org/2000/svg">'
            f'<polygon points="{pts}" fill="{fill_color}" '
            f'stroke="#1a5fa0" stroke-width="1.5"/>'
            f"</svg>"
        )

    def __panel__(self):
        return pn.Card(self._grid, title="Gallery", collapsed=False)
