"""Preview strip with tiling image and modal dialog for settings/export controls."""

from __future__ import annotations

import json
from io import BytesIO, StringIO

import panel as pn
import param


class PreviewStrip(pn.viewable.Viewer):
    """Preview strip with tiling image and modal dialog for settings/export controls."""

    state = param.ClassSelector(class_=param.Parameterized)

    # Grid size mapping
    GRID_SIZES = {"Small": 50, "Medium": 100, "Large": 200}

    def __init__(self, **params):
        super().__init__(**params)
        self._render_thread = None  # Track current render thread
        self._render_cancelled = False  # Flag to cancel pending renders

        # Create image pane for the tiling preview - fixed size for aspect ratio
        self._image_pane = pn.pane.PNG(
            None,
            height=90,
            width=300,
            margin=0,
            align="center",
        )

        # Transparent full-area button overlaid on the image via CSS absolute
        # positioning. Clicking anywhere on the image opens the modal.
        self.settings_btn = pn.widgets.Button(
            name="",
            description="Click to open settings & export",
            margin=0,
            stylesheets=[
                """
                :host {
                    position: absolute;
                    top: 0;
                    left: 0;
                    z-index: 10;
                }
                .bk-btn {
                    width: 300px;
                    height: 90px;
                    background: transparent !important;
                    border: none !important;
                    cursor: pointer !important;
                    padding: 0 !important;
                }
                .bk-btn:hover {
                    background: rgba(0,0,0,0.06) !important;
                }
            """
            ],
        )

        # Wrapper: image + click overlay. Panel's built-in `loading` param
        # shows an animated spinner overlay when set to True.
        self._preview_wrapper = pn.Column(
            self._image_pane,
            self.settings_btn,
            styles={"position": "relative"},
            width=300,
            height=90,
            margin=0,
            align="center",
        )

        # Shared export filename input (auto-populated, user-editable)
        self._export_filename = pn.widgets.TextInput(
            value=self._default_export_filename(),
            placeholder="filename",
            sizing_mode="stretch_width",
        )
        self._export_filename.param.watch(self._on_export_filename_change, "value")
        self.state.param.watch(self._update_export_filename, ["tiling_type", "current_preset"])

        # Export FileDownload widgets
        _stem = self._export_filename.value
        _btn_kw = {"button_type": "light", "width": 75, "margin": 2}
        self._svg_download = pn.widgets.FileDownload(
            callback=self._generate_svg_export,
            filename=f"{_stem}.svg",
            label="📄 SVG",
            **_btn_kw,
        )
        self._svg_download.param.watch(
            lambda e: pn.state.notifications.success(f"'{self._svg_download.filename}' saved to downloads folder"),
            "_clicks",
        )
        self._png_download = pn.widgets.FileDownload(
            callback=self._generate_png_export,
            filename=f"{_stem}.png",
            label="🖼️ PNG",
            **_btn_kw,
        )
        self._png_download.param.watch(
            lambda e: pn.state.notifications.success(f"'{self._png_download.filename}' saved to downloads folder"),
            "_clicks",
        )
        self._json_download = pn.widgets.FileDownload(
            callback=self._generate_config_json,
            filename=f"{_stem}.json",
            label="📋 JSON",
            **_btn_kw,
        )
        self._json_download.param.watch(
            lambda e: pn.state.notifications.success(f"'{self._json_download.filename}' saved to downloads folder"),
            "_clicks",
        )
        self._geojson_download = pn.widgets.FileDownload(
            callback=self._generate_geojson_export,
            filename=f"{_stem}.geojson",
            label="🗺️ GeoJSON",
            button_type="light",
            width=120,
            margin=2,
        )
        self._geojson_download.param.watch(
            lambda e: pn.state.notifications.success(f"'{self._geojson_download.filename}' saved to downloads folder"),
            "_clicks",
        )
        self._py_download = pn.widgets.FileDownload(
            callback=self._save_python,
            filename=f"{_stem}.py",
            label="🐍 Save as .py",
            button_type="light",
            width=160,
            margin=2,
        )
        self._py_download.param.watch(
            lambda e: pn.state.notifications.success(f"'{self._py_download.filename}' saved to downloads folder"),
            "_clicks",
        )

        # Python code display for the export panel
        self._py_code_display = pn.pane.Markdown(
            self._python_markdown(),
            sizing_mode="stretch_width",
            stylesheets=[":host pre { overflow-x: auto; white-space: pre; }"],
            styles={"max-height": "300px", "overflow-y": "auto"},
        )
        self.state.param.watch(
            self._refresh_py_code,
            ["tiling_type", "parameters", "edge_curves", "edge_curve_modes"],
        )

        _card_style = {
            "background": "#f8f9fa",
            "border-radius": "8px",
            "padding": "15px",
        }

        # Create modal dialog content: two-column layout
        self._modal = pn.Row(
            pn.HSpacer(),
            pn.Column(
                pn.pane.HTML("<h3 style='margin:0 0 12px 0;color:#1971c2;'>Preview Settings & Export</h3>"),
                pn.Row(
                    # Left column: preview settings
                    pn.Column(
                        pn.pane.HTML("<b>Grid Size</b>", margin=(0, 0, 5, 0)),
                        self._build_grid_size_row(),
                        pn.pane.HTML("<b>Color Settings</b>", margin=(12, 0, 5, 0)),
                        self._build_color_settings(),
                        width=345,
                        styles=_card_style,
                    ),
                    pn.layout.HSpacer(width=12),
                    # Right column: export buttons + Python code
                    pn.Column(
                        pn.pane.HTML("<b>Export</b>", margin=(0, 0, 5, 0)),
                        self._build_export_buttons(),
                        pn.layout.Divider(margin=(8, 0)),
                        pn.pane.HTML("<b>Python Code</b>", margin=(0, 0, 4, 0)),
                        self._py_code_display,
                        width=510,
                        styles=_card_style,
                    ),
                ),
                margin=15,
            ),
            pn.HSpacer(),
        )

        # Schedule initial render after state is ready
        pn.state.onload(self._initial_render)

    def _initial_render(self):
        """Initial render after state is ready."""
        if self.state._tiling is not None:
            self._update_strip()

    @param.depends(
        "state._tiling",
        "state.edge_curves",
        "state.edge_curve_modes",
        "state.grid_size",
        "state.face_color_mode",
        "state.face_color",
        "state.face_colormap",
        "state.edge_color",
        watch=True,
    )
    def _update_strip(self):
        """Update strip image when tiling changes."""
        # Skip during batch operations — the final _update_tiling() will fire state._tiling.
        if getattr(self.state, "_batching", False):
            return

        import threading

        # Cancel any pending render
        self._render_cancelled = True

        # Show loading overlay on the image wrapper
        self._preview_wrapper.loading = True

        def _render_in_thread():
            # Check if cancelled before starting
            if self._render_cancelled:
                return

            # Small delay to allow cancellation of rapid requests
            import time

            time.sleep(0.05)

            # Check again after delay
            if self._render_cancelled:
                return

            png_data = self._render_strip_png()

            # Check if cancelled before updating UI
            if self._render_cancelled:
                return

            # Update UI on main thread
            pn.state.execute(lambda: self._update_image(png_data))

        # Reset cancellation flag and start new thread
        self._render_cancelled = False
        thread = threading.Thread(target=_render_in_thread, daemon=True)
        thread.start()

    def _update_image(self, png_data):
        """Update image pane on main thread."""
        self._image_pane.object = png_data
        self._preview_wrapper.loading = False

    def _get_colormap(self):
        """Get matplotlib colormap from state setting."""
        import matplotlib.pyplot as plt

        colormap_map = {
            "Category10": "tab10",  # matplotlib equivalent
            "Viridis": "viridis",
            "Plasma": "plasma",
            "Inferno": "inferno",
            "Turbo": "turbo",
        }
        name = colormap_map.get(self.state.face_colormap, "tab10")
        return plt.get_cmap(name)

    def _get_tile_color(self, index: int, rotation: float, n_tiles: int) -> str:
        """Get tile color based on current color mode and colormap."""
        mode = self.state.face_color_mode

        if mode == "uniform":
            return self.state.face_color

        # Get colormap
        cmap = self._get_colormap()

        if mode == "aspect":
            # Color by rotation angle (0-360 -> 0-1)
            value = (rotation % 360) / 360.0
        else:  # index
            # Color by tile index
            value = index / max(n_tiles - 1, 1)

        # Convert matplotlib color to hex
        import matplotlib.colors as mcolors

        return mcolors.to_hex(cmap(value))

    def _render_strip_png(self) -> bytes | None:
        """Render PNG strip and return bytes with correct aspect ratio."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.colors as mcolors
        import matplotlib.pyplot as plt
        from matplotlib.collections import PolyCollection

        tiling = self.state._tiling
        if tiling is None:
            return None

        # Wide aspect ratio for the strip
        STRIP_W, STRIP_H, DPI = 300, 90, 100

        # Skip adjacency computation for preview - not needed for visual rendering
        result = tiling.generate(bounds=(-1, -0.3, 1, 0.3), n_tiles=100, margin=0.25, compute_adjacency=False)

        fig, ax = plt.subplots(figsize=(STRIP_W / DPI, STRIP_H / DPI), dpi=DPI)
        ax.set_xlim(-1, 1)
        ax.set_ylim(-0.3, 0.3)
        ax.axis("off")

        # Pre-compute colormap
        cmap = self._get_colormap() if self.state.face_color_mode != "uniform" else None

        # Use PolyCollection for much faster rendering of many polygons
        n_tiles = len(result.polygons)
        all_coords = []
        all_colors = []
        for i, (poly, tr) in enumerate(zip(result.polygons, result.transforms, strict=False)):
            px, py = poly.exterior.xy
            all_coords.append(list(zip(px, py, strict=False)))

            if self.state.face_color_mode == "uniform":
                color = self.state.face_color
            elif self.state.face_color_mode == "aspect":
                value = (tr.rotation % 360) / 360.0
                color = mcolors.to_hex(cmap(value))
            else:  # index
                value = i / max(n_tiles - 1, 1)
                color = mcolors.to_hex(cmap(value))
            all_colors.append(color)

        # Create a single PolyCollection - much faster than individual patches
        collection = PolyCollection(
            all_coords,
            facecolors=all_colors,
            edgecolors=self.state.edge_color,
            linewidths=0.3,
            alpha=0.85,
        )
        ax.add_collection(collection)

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=DPI, facecolor="white", bbox_inches="tight", pad_inches=0)
        plt.close(fig)

        return buf.getvalue()

    def _build_grid_size_row(self):
        """Build grid size selector as a horizontal row."""
        grid_size = pn.widgets.RadioButtonGroup.from_param(
            self.state.param.grid_size,
            orientation="horizontal",
            button_type="success",
            button_style="outline",
        )
        return pn.Row(grid_size, margin=5)

    def _build_color_settings(self):
        """Build color settings section with clear layout."""
        # Face color mode - wider buttons to prevent text cutoff
        face_mode = pn.widgets.RadioButtonGroup.from_param(
            self.state.param.face_color_mode,
            orientation="horizontal",
            button_type="success",
            button_style="outline",
            width=200,  # Wider to fit all options
        )

        # Color pickers with visible labels
        face_color = pn.widgets.ColorPicker(
            value=self.state.face_color,
            width=80,
            name="Face Color",
            description="Fill color of tiles (used in 'uniform' color mode)",
        )

        def _update_face_color(event):
            self.state.face_color = event.new

        face_color.param.watch(_update_face_color, "value")

        edge_color = pn.widgets.ColorPicker(
            value=self.state.edge_color,
            width=80,
            name="Edge Color",
            description="Stroke color of tile outlines",
        )

        def _update_edge_color(event):
            self.state.edge_color = event.new

        edge_color.param.watch(_update_edge_color, "value")

        # Colormap select
        COLORMAP_NAMES = ["Category10", "Viridis", "Plasma", "Inferno", "Turbo"]
        colormap_select = pn.widgets.Select(
            value=self.state.face_colormap,
            options=COLORMAP_NAMES,
            width=150,
            name="Colormap",
            description="Color palette used in 'aspect' and 'index' color modes",
        )

        def _update_colormap(event):
            self.state.face_colormap = event.new

        colormap_select.param.watch(_update_colormap, "value")

        return pn.Column(
            pn.Row(pn.pane.HTML("<small>Color Mode:</small>"), face_mode, margin=(5, 0)),
            pn.Row(face_color, edge_color, margin=(5, 0)),
            pn.Row(colormap_select, margin=(5, 0)),
            margin=5,
        )

    def _generate_python_code(self) -> str:
        """Generate a Python snippet that reproduces the current tiling."""
        tiling_type = self.state.tiling_type
        parameters = list(self.state.parameters)
        edge_curves = self.state.edge_curves
        edge_curve_modes = self.state.edge_curve_modes

        lines = [
            "from carto_flow.symbol_cartogram.tiling import IsohedralTiling",
            "",
            "tiling = IsohedralTiling(",
            f"    tiling_type={tiling_type},",
        ]
        if parameters:
            lines.append(f"    parameters={parameters},")
        if edge_curves:
            curves = {k: [list(pt) for pt in v] for k, v in edge_curves.items()}
            lines.append(f"    edge_curves={curves},")
        if edge_curve_modes:
            lines.append(f"    edge_curve_modes={edge_curve_modes},")
        lines += [
            ")",
            "",
            "result = tiling.generate(",
            "    bounds=(-2, -2, 2, 2),",
            "    n_tiles=100,",
            "    margin=0.1,",
            ")",
        ]
        return "\n".join(lines)

    def _python_markdown(self) -> str:
        return f"```python\n{self._generate_python_code()}\n```"

    def _refresh_py_code(self, *events):
        """Keep the Python code display in sync with current state."""
        self._py_code_display.object = self._python_markdown()

    def _save_python(self):
        """Return Python code as a file-like object for download."""
        return StringIO(self._generate_python_code())

    def _default_export_filename(self) -> str:
        preset = getattr(self.state, "current_preset", "")
        return preset if preset else f"IH{self.state.tiling_type}"

    def _update_export_filename(self, *events):
        """Auto-populate export filename when tiling type or preset changes."""
        self._export_filename.value = self._default_export_filename()

    def _on_export_filename_change(self, event):
        """Propagate user-edited filename to all download widgets."""
        stem = event.new.strip() or self._default_export_filename()
        self._svg_download.filename = f"{stem}.svg"
        self._png_download.filename = f"{stem}.png"
        self._json_download.filename = f"{stem}.json"
        self._geojson_download.filename = f"{stem}.geojson"
        self._py_download.filename = f"{stem}.py"

    def _export_bounds_and_ntiles(self) -> tuple:
        """Return (bounds, n_tiles) for export based on current grid size."""
        return (-2, -2, 2, 2), self.GRID_SIZES[self.state.grid_size]

    def _generate_svg_export(self) -> StringIO:
        """Export tiling as SVG using svgwrite."""
        import svgwrite

        bounds, n_tiles = self._export_bounds_and_ntiles()
        result = self.state._tiling.generate(bounds=bounds, n_tiles=n_tiles, compute_adjacency=False)

        xmin, ymin, xmax, ymax = bounds
        W = H = 800

        def to_svg(x, y):
            sx = (x - xmin) / (xmax - xmin) * W
            sy = (ymax - y) / (ymax - ymin) * H  # flip y
            return (round(sx, 3), round(sy, 3))

        dwg = svgwrite.Drawing(size=(f"{W}px", f"{H}px"), viewBox=f"0 0 {W} {H}")
        dwg.add(dwg.rect((0, 0), (W, H), fill="white"))
        n = len(result.polygons)
        for i, (poly, tr) in enumerate(zip(result.polygons, result.transforms, strict=False)):
            pts = [to_svg(x, y) for x, y in zip(*poly.exterior.xy, strict=False)]
            dwg.add(
                dwg.polygon(
                    pts,
                    fill=self._get_tile_color(i, tr.rotation, n),
                    stroke=self.state.edge_color,
                    stroke_width=0.5,
                    opacity=0.85,
                )
            )
        return StringIO(dwg.tostring())

    def _generate_png_export(self) -> BytesIO:
        """Export tiling as a square PNG."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.collections import PolyCollection

        bounds, n_tiles = self._export_bounds_and_ntiles()
        result = self.state._tiling.generate(bounds=bounds, n_tiles=n_tiles, compute_adjacency=False)

        xmin, ymin, xmax, ymax = bounds
        fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        ax.axis("off")

        n = len(result.polygons)
        coords = [list(zip(*p.exterior.xy, strict=False)) for p in result.polygons]
        colors = [self._get_tile_color(i, tr.rotation, n) for i, tr in enumerate(result.transforms)]
        ax.add_collection(
            PolyCollection(
                coords,
                facecolors=colors,
                edgecolors=self.state.edge_color,
                linewidths=0.5,
                alpha=0.85,
            )
        )

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=150, facecolor="white", bbox_inches="tight", pad_inches=0.05)
        plt.close(fig)
        buf.seek(0)
        return buf

    def _generate_config_json(self) -> StringIO:
        """Export tiling configuration as JSON."""
        from datetime import datetime, timezone

        config = {
            "version": "1.0",
            "tiling_type": self.state.tiling_type,
            "parameters": self.state.parameters,
            "edge_curves": {str(k): [list(p) for p in v] for k, v in self.state.edge_curves.items()},
            "metadata": {"created": datetime.now(timezone.utc).isoformat()},
        }
        return StringIO(json.dumps(config, indent=2))

    def _generate_geojson_export(self) -> StringIO:
        """Export tiling as a GeoJSON FeatureCollection."""
        from shapely.geometry import mapping

        bounds, n_tiles = self._export_bounds_and_ntiles()
        result = self.state._tiling.generate(bounds=bounds, n_tiles=n_tiles, compute_adjacency=False)

        n = len(result.polygons)
        features = []
        for i, (poly, tr) in enumerate(zip(result.polygons, result.transforms, strict=False)):
            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "tile_index": i,
                    "rotation": round(tr.rotation, 4),
                    "flipped": tr.flipped,
                    "center_x": round(tr.center[0], 6),
                    "center_y": round(tr.center[1], 6),
                    "color": self._get_tile_color(i, tr.rotation, n),
                },
            })
        return StringIO(json.dumps({"type": "FeatureCollection", "features": features}))

    def _build_export_buttons(self):
        """Build export download buttons."""
        return pn.Column(
            pn.Row(
                pn.pane.HTML("<small style='line-height:30px'>Filename:</small>", width=60),
                self._export_filename,
                margin=(0, 0, 4, 0),
            ),
            pn.Row(
                self._svg_download,
                self._png_download,
                self._json_download,
                self._geojson_download,
                margin=(0, 0, 2, 0),
            ),
            self._py_download,
            margin=0,
        )

    def _build_settings_column(self):
        """Build preview settings as a compact column (legacy method)."""
        return self._build_color_settings()

    def _build_export_column(self):
        """Build export controls as a compact column of buttons (legacy method)."""
        return self._build_export_buttons()

    def __panel__(self):
        return self._preview_wrapper
