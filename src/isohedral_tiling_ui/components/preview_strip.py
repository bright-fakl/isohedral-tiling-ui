"""Preview strip with tiling image and modal dialog for settings/export controls."""

from __future__ import annotations

from io import BytesIO, StringIO

import panel as pn
import param

from isohedral_tiling_ui import exports


class PreviewStrip(pn.viewable.Viewer):
    """Preview strip with tiling image and modal dialog for settings/export controls."""

    state = param.ClassSelector(class_=param.Parameterized)

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
        self.state.param.watch(self._update_export_filename, ["source_label", "tiling_type"])

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
            lambda e: self.state.notify("success", f"'{self._svg_download.filename}' saved to downloads folder"),
            "_clicks",
        )
        self._png_download = pn.widgets.FileDownload(
            callback=self._generate_png_export,
            filename=f"{_stem}.png",
            label="🖼️ PNG",
            **_btn_kw,
        )
        self._png_download.param.watch(
            lambda e: self.state.notify("success", f"'{self._png_download.filename}' saved to downloads folder"),
            "_clicks",
        )
        self._json_download = pn.widgets.FileDownload(
            callback=self._generate_config_json,
            filename=f"{_stem}.json",
            label="📋 JSON",
            **_btn_kw,
        )
        self._json_download.param.watch(
            lambda e: self.state.notify("success", f"'{self._json_download.filename}' saved to downloads folder"),
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
            lambda e: self.state.notify("success", f"'{self._geojson_download.filename}' saved to downloads folder"),
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
            lambda e: self.state.notify("success", f"'{self._py_download.filename}' saved to downloads folder"),
            "_clicks",
        )

        # Python code display for the export panel
        self._py_code_display = pn.pane.Markdown(
            self._python_markdown(),
            sizing_mode="stretch_width",
            stylesheets=[":host pre { overflow-x: auto; white-space: pre; }"],
            styles={"max-height": "300px", "overflow-y": "auto"},
        )
        self.state.param.watch(self._refresh_py_code, "draft")
        self.state.param.watch(self._update_strip, "draft")
        self.state.view.param.watch(
            self._update_strip, ["face_color_mode", "face_color", "face_colormap", "edge_color"]
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
        self._update_strip()

    def _update_strip(self, *events):
        """Update strip image when the design or the view settings change."""
        import threading

        # Cancel any pending render
        self._render_cancelled = True

        # Show loading overlay on the image wrapper
        self._preview_wrapper.loading = True

        # The render thread works on a snapshot: the tiling is immutable
        tiling = self.state.tiling
        appearance = self.state.view.appearance()

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

            png_data = exports.strip_png(tiling, appearance)

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

    def _build_grid_size_row(self):
        """Build grid size selector as a horizontal row."""
        grid_size = pn.widgets.RadioButtonGroup.from_param(
            self.state.view.param.grid_size,
            orientation="horizontal",
            button_type="success",
            button_style="outline",
        )
        return pn.Row(grid_size, margin=5)

    def _build_color_settings(self):
        """Build color settings section with clear layout."""
        # Face color mode - wider buttons to prevent text cutoff
        face_mode = pn.widgets.RadioButtonGroup.from_param(
            self.state.view.param.face_color_mode,
            orientation="horizontal",
            button_type="success",
            button_style="outline",
            width=200,  # Wider to fit all options
        )

        # Color pickers with visible labels
        face_color = pn.widgets.ColorPicker(
            value=self.state.view.face_color,
            width=80,
            name="Face Color",
            description="Fill color of tiles (used in 'uniform' color mode)",
        )

        def _update_face_color(event):
            self.state.view.face_color = event.new

        face_color.param.watch(_update_face_color, "value")

        edge_color = pn.widgets.ColorPicker(
            value=self.state.view.edge_color,
            width=80,
            name="Edge Color",
            description="Stroke color of tile outlines",
        )

        def _update_edge_color(event):
            self.state.view.edge_color = event.new

        edge_color.param.watch(_update_edge_color, "value")

        # Colormap select
        COLORMAP_NAMES = ["Category10", "Viridis", "Plasma", "Inferno", "Turbo"]
        colormap_select = pn.widgets.Select(
            value=self.state.view.face_colormap,
            options=COLORMAP_NAMES,
            width=150,
            name="Colormap",
            description="Color palette used in 'aspect' and 'coloring' color modes",
        )

        def _update_colormap(event):
            self.state.view.face_colormap = event.new

        colormap_select.param.watch(_update_colormap, "value")

        return pn.Column(
            pn.Row(pn.pane.HTML("<small>Color Mode:</small>"), face_mode, margin=(5, 0)),
            pn.Row(face_color, edge_color, margin=(5, 0)),
            pn.Row(colormap_select, margin=(5, 0)),
            margin=5,
        )

    def _generate_python_code(self) -> str:
        """Generate a Python snippet that reproduces the current tiling."""
        return exports.python_snippet(self.state.tiling)

    def _python_markdown(self) -> str:
        return f"```python\n{self._generate_python_code()}\n```"

    def _refresh_py_code(self, *events):
        """Keep the Python code display in sync with current state."""
        self._py_code_display.object = self._python_markdown()

    def _save_python(self):
        """Return Python code as a file-like object for download."""
        return StringIO(self._generate_python_code())

    def _default_export_filename(self) -> str:
        return self.state.default_name

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

    def _export_n_tiles(self) -> int:
        """Number of tiles of the exports for the current grid size."""
        return exports.GRID_SIZES[self.state.view.grid_size]

    def _generate_svg_export(self) -> StringIO:
        """Export tiling as SVG using svgwrite."""
        svg = exports.svg_export(self.state.tiling, self.state.view.appearance(), self._export_n_tiles())
        return StringIO(svg)

    def _generate_png_export(self) -> BytesIO:
        """Export tiling as a square PNG."""
        png = exports.png_export(self.state.tiling, self.state.view.appearance(), self._export_n_tiles())
        return BytesIO(png)

    def _generate_config_json(self) -> StringIO:
        """Export tiling configuration as JSON (isotile format 1.1)."""
        description = self._export_filename.value.strip() or self._default_export_filename()
        return StringIO(exports.design_json(self.state.tiling, description))

    def _generate_geojson_export(self) -> StringIO:
        """Export tiling as a GeoJSON FeatureCollection."""
        geojson = exports.geojson_export(self.state.tiling, self.state.view.appearance(), self._export_n_tiles())
        return StringIO(geojson)

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

    def __panel__(self):
        return self._preview_wrapper
