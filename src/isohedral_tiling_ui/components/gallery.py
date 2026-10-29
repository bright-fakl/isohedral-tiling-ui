"""Gallery of preset tilings with prototile previews."""

from __future__ import annotations

import panel as pn
import param
from isotile import IsohedralTiling, list_presets

from isohedral_tiling_ui.utils.icons import prototile_svg


class GalleryPanel(pn.viewable.Viewer):
    """Gallery of preset tilings with prototile previews."""

    state = param.ClassSelector(class_=param.Parameterized)

    def __init__(self, **params):
        super().__init__(**params)
        self._grid = pn.GridBox(ncols=5, nrows=0)  # 5 columns for snug fit
        self._build_gallery()

    def _build_gallery(self):
        """Build the gallery grid."""
        items = []

        for name in sorted(list_presets()):
            tiling = IsohedralTiling.from_preset(name)
            svg = prototile_svg(
                tiling.prototile(normalized=True), tiling.type.n_vertices, size=38, stroke="#1a5fa0", stroke_width=1.5
            )
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

            items.append(btn)

        self._grid.objects = items

    def __panel__(self):
        return pn.Card(self._grid, title="Gallery", collapsed=False)
