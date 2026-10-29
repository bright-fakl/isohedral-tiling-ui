"""Edge curve editor with draggable control points."""

from __future__ import annotations

from functools import partial

import panel as pn
import param
from bokeh.events import DoubleTap
from bokeh.models import ColumnDataSource, CustomJS
from bokeh.plotting import figure
from isotile import interpolate

from isohedral_tiling_ui.editor_model import editable, preset_availability
from isohedral_tiling_ui.utils.curve_presets import get_curve_preset, list_curve_presets

SELECTED_FILL, SELECTED_LINE = "crimson", "#7a0000"
NODE_FILL, NODE_LINE = "#444", "#111"
FIXED_FILL, FIXED_LINE = "#ccc", "#999"
MUTED_ALPHA = 0.3  # alpha of the movable nodes while one of them is selected


class CurveEditor(pn.viewable.Viewer):
    """Edge curve editor with draggable control points."""

    state = param.ClassSelector(class_=param.Parameterized)

    def __init__(self, **params):
        super().__init__(**params)
        self._cp_source = ColumnDataSource(
            data={
                "x": [],
                "y": [],
                "idx": [],
                "editable": [],
                "fill_color": [],
                "line_color": [],
                "alpha": [],
                "edge_shape": [],
            }
        )
        # Index (in the curve) of the selected movable node, -1 if none. Kept apart from the selection of the source,
        # which PointDrawTool clears at the end of every drag.
        self._selection_source = ColumnDataSource(data={"idx": [-1]})
        self._selection_curve_id: int | None = None
        # Separate source for fixed (non-editable) points - not connected to PointDrawTool
        self._fixed_cp_source = ColumnDataSource(data={"x": [], "y": [], "idx": [], "fill_color": [], "line_color": []})
        self._curve_source = ColumnDataSource(data={"x": [], "y": []})
        self._baseline_source = ColumnDataSource(data={"x": [0, 1], "y": [0, 0]})
        self._updating_from_state = False  # Flag to prevent circular updates
        self._programmatic_cp_update = False  # Flag to skip history push on programmatic cp_source writes
        self._y_scale = 1.0  # Y-axis scale factor (default ±1)
        self._preset_buttons: dict[str, pn.widgets.ButtonIcon] = {}
        # name -> reason a preset is unavailable ("" if available). Not the ``disabled`` property: the browser model of
        # a ButtonIcon sets it to true on click and to false 75 ms later, which overwrites the value set here.
        self._preset_reasons: dict[str, str] = {}
        self._preset_grid = self._build_preset_buttons()
        self._figure = self._build_figure()
        self._setup_callbacks()
        # Initialize with current state values since @param.depends doesn't fire on init
        self._update_figure()
        self._refresh_presets()

    def _build_figure(self):
        """Build the Bokeh curve editor figure."""
        p = figure(
            width=400,
            height=210,
            x_range=(-0.2, 1.2),  # Extend x-range to show points outside [0,1]
            y_range=(-1.1 * self._y_scale, 1.1 * self._y_scale),
            tools="",  # We'll add tools manually
            toolbar_location=None,
            sizing_mode="stretch_width",
        )
        # Add wheel zoom tool for y-axis only
        # Using Bokeh's built-in WheelZoomTool which properly prevents browser scroll
        from bokeh.models import WheelZoomTool

        wheel_zoom = WheelZoomTool(dimensions="height")  # Only zoom y-axis
        p.add_tools(wheel_zoom)
        p.toolbar.active_scroll = wheel_zoom

        # Add callback to keep y-range symmetric around 0 after zoom
        # This ensures the curve stays centered vertically
        p.y_range.js_on_change(
            "start",
            CustomJS(
                args={"y_range": p.y_range},
                code="""
            // Make y-range symmetric around 0 while preserving the zoom level
            // Only update if not already symmetric (avoid infinite loop)
            if (Math.abs(y_range.start + y_range.end) > 0.001) {
                const current_half = (y_range.end - y_range.start) / 2;
                y_range.start = -current_half;
                y_range.end = current_half;
            }
        """,
            ),
        )
        # Show axes with scales
        p.axis.visible = True
        p.grid.visible = True
        p.grid.grid_line_color = "#f0f0f0"
        p.axis.axis_label_text_font_size = "10pt"
        p.axis.major_label_text_font_size = "8pt"
        # p.xaxis.axis_label = "X"
        # p.yaxis.axis_label = "Y"

        p.line("x", "y", source=self._baseline_source, line_color="#ddd", line_width=1, line_dash="dashed")

        self._curve_renderer = p.line("x", "y", source=self._curve_source, line_color="steelblue", line_width=2.5)

        # Fixed (non-editable) points - rendered first so they appear behind editable points
        self._fixed_cp_renderer = p.scatter(
            "x",
            "y",
            source=self._fixed_cp_source,
            size=10,
            fill_color="fill_color",
            line_color="line_color",
        )

        # Editable control points - connected to PointDrawTool
        self._cp_renderer = p.scatter(
            "x",
            "y",
            source=self._cp_source,
            size=10,
            fill_color="fill_color",
            line_color="line_color",
            fill_alpha="alpha",
            line_alpha="alpha",
        )
        # the node colors come from the columns, not from the selection of the source
        self._cp_renderer.selection_glyph = None
        self._cp_renderer.nonselection_glyph = None

        # Add text labels for editable control points
        from bokeh.models import LabelSet

        self._cp_labels = LabelSet(
            x="x",
            y="y",
            text="idx_text",
            source=self._cp_source,
            text_font_size="8pt",
            x_offset=5,
            y_offset=5,
            background_fill_color="white",
            background_fill_alpha=0.7,
            border_line_color=None,
        )
        p.add_layout(self._cp_labels)

        from bokeh.models import PointDrawTool

        # add=False prevents single-click point creation; double-click handled separately
        # Only connect editable points to PointDrawTool
        draw = PointDrawTool(renderers=[self._cp_renderer], add=False)
        p.add_tools(draw)
        p.toolbar.active_tap = draw

        p.on_event(DoubleTap, self._on_double_tap)
        self._add_selection_callbacks(p)

        # Use pan event for live feedback during drag
        # Pan fires continuously during drag operations
        # We need to track if we're actually dragging an editable point
        p.js_on_event(
            "pan",
            CustomJS(
                args={
                    "curve_source": self._curve_source,
                    "cp_source": self._cp_source,
                    "fixed_cp_source": self._fixed_cp_source,
                },
                code="""
            const data = cp_source.data;
            const fixedData = fixed_cp_source.data;
            const n = data.x.length;
            const nFixed = fixedData.x.length;
            if (n < 1) return;

            // Store previous positions to detect if editable points actually moved
            // Reinitialize if array lengths don't match (point added/removed)
            if (!cp_source._prevX || cp_source._prevX.length !== n) {
                cp_source._prevX = data.x.slice();
                cp_source._prevY = data.y.slice();
            }

            // Check if any editable point has moved
            let hasChanged = false;
            for (let i = 0; i < n; i++) {
                if (Math.abs(data.x[i] - cp_source._prevX[i]) > 0.0001 ||
                    Math.abs(data.y[i] - cp_source._prevY[i]) > 0.0001) {
                    hasChanged = true;
                    break;
                }
            }

            // If no editable point has moved, don't update the curve
            // This prevents the straight-line appearance when dragging fixed points
            if (!hasChanged) return;

            // Update previous positions
            cp_source._prevX = data.x.slice();
            cp_source._prevY = data.y.slice();

            // Get edge shape from first point
            const edgeShape = data.edge_shape && data.edge_shape.length > 0 ? data.edge_shape[0] : "J";

            // For U curves, constrain all editable points to x <= 0.5
            if (edgeShape === "U") {
                const totalPoints = n + nFixed;
                const half = (totalPoints - 1) / 2;
                for (let i = 0; i < n; i++) {
                    // Constrain midpoint x to 0.5
                    if (Math.abs(data.idx[i] - half) < 0.001) {
                        data.x[i] = 0.5;
                    } else {
                        // Constrain other editable points to x <= 0.5
                        data.x[i] = Math.min(data.x[i], 0.5);
                    }
                }
            }

            // For S/U curves, update the mirrored points during drag
            if (edgeShape === "S" || edgeShape === "U") {
                const totalPoints = n + nFixed;
                const half = (totalPoints - 1) / 2;

                // Update fixed points based on editable points
                for (let i = 0; i < nFixed; i++) {
                    const idx = fixedData.idx[i];

                    if (idx === 0) {
                        // Start point
                        fixedData.x[i] = 0;
                        fixedData.y[i] = 0;
                    } else if (idx === totalPoints - 1) {
                        // End point
                        fixedData.x[i] = 1;
                        fixedData.y[i] = 0;
                    } else if (Math.abs(idx - half) < 0.001) {
                        // Midpoint - only for S curves (U midpoint is editable)
                        if (edgeShape === "S") {
                            fixedData.x[i] = 0.5;
                            fixedData.y[i] = 0;
                        }
                    } else if (idx > half) {
                        // Mirrored point - find corresponding editable point
                        const mirrorIdx = totalPoints - 1 - idx;
                        // Find editable point with this index
                        for (let j = 0; j < n; j++) {
                            if (data.idx[j] === mirrorIdx) {
                                if (edgeShape === "S") {
                                    fixedData.x[i] = 1 - data.x[j];
                                    fixedData.y[i] = -data.y[j];
                                } else {
                                    fixedData.x[i] = 1 - data.x[j];
                                    fixedData.y[i] = data.y[j];
                                }
                                break;
                            }
                        }
                    }
                }
                // Emit change to update the fixed points scatter
                fixed_cp_source.change.emit();
            }

            // For live feedback during drag, use linear interpolation (fast)
            // Combine editable and fixed points for curve rendering
            const allX = data.x.slice().concat(fixedData.x);
            const allY = data.y.slice().concat(fixedData.y);
            const allIdx = data.idx.slice().concat(fixedData.idx);

            // Sort by index for proper rendering
            const points = allX.map((x, i) => ({x, y: allY[i], idx: allIdx[i]}));
            points.sort((a, b) => a.idx - b.idx);

            curve_source.data = {x: points.map(p => p.x), y: points.map(p => p.y)};
        """,
            ),
        )

        return p

    def _add_selection_callbacks(self, p) -> None:
        """Select a node by clicking or dragging it, deselect by clicking elsewhere; the selection survives drags.

        The selected node is highlighted and the other movable nodes are muted. PointDrawTool selects the node under
        the pointer and clears that selection when a drag ends, so the choice is copied to ``_selection_source``.
        """
        args = {
            "cp_source": self._cp_source,
            "sel_source": self._selection_source,
            "style": {
                "fill": SELECTED_FILL,
                "line": SELECTED_LINE,
                "node_fill": NODE_FILL,
                "node_line": NODE_LINE,
                "muted": MUTED_ALPHA,
            },
        }
        recolor = """
        const cur = sel_source.data.idx[0];
        const d = cp_source.data;
        for (let i = 0; i < d.idx.length; i++) {
            const on = d.idx[i] === cur;
            d.fill_color[i] = on ? style.fill : style.node_fill;
            d.line_color[i] = on ? style.line : style.node_line;
            d.alpha[i] = (cur < 0 || on) ? 1 : style.muted;
        }
        cp_source.change.emit();
        """
        on_indices = CustomJS(
            args=args,
            code="""
            if (cp_source._panning && cp_source.selected.indices.length === 0) return;  // the end of a drag
            const k = cp_source.selected.indices;
            const sel = k.length ? cp_source.data.idx[k[0]] : -1;
            if (sel_source.data.idx[0] === sel) return;
            sel_source.data = {idx: [sel]};
            """
            + recolor,
        )
        self._cp_source.selected.js_on_change("indices", on_indices)
        p.js_on_event("panstart", CustomJS(args={"cp_source": self._cp_source}, code="cp_source._panning = true;"))
        p.js_on_event(
            "panend",
            CustomJS(
                args={"cp_source": self._cp_source},
                code="setTimeout(() => { cp_source._panning = false; }, 0);",
            ),
        )
        # a click on empty space leaves the source without selection, a click on a node leaves it selected
        p.js_on_event(
            "tap",
            CustomJS(
                args=args,
                code="""
                setTimeout(() => {
                    if (cp_source.selected.indices.length === 0 && sel_source.data.idx[0] !== -1) {
                        sel_source.data = {idx: [-1]};
                        """
                + recolor
                + """
                    }
                }, 0);
                """,
            ),
        )

    def _setup_callbacks(self):
        """Set up callbacks for drag and selection."""
        # Only use Python callback - JS pan event handles live feedback during drag
        self._cp_source.on_change("data", self._on_curve_change)

    @param.depends("state.draft", "state.active_curve_id", watch=True)
    def _update_figure(self):
        """Update curve visualization."""
        # Skip if we're in the middle of updating from a drag operation
        if self._updating_from_state:
            return

        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curve = self.state.draft.curve(curve_id)
        n = len(curve)
        edge_shape = self.state.shape_type(curve_id)

        # Keep the selection while the curve keeps the node
        selected = self._selection_source.data["idx"][0]
        if curve_id != self._selection_curve_id or selected >= n or not editable(edge_shape, n, selected):
            selected = -1
        self._selection_curve_id = curve_id
        if self._selection_source.data["idx"][0] != selected:
            self._selection_source.data = {"idx": [selected]}

        # Separate editable and fixed points
        editable_x, editable_y, editable_idx = [], [], []
        editable_fill_color, editable_line_color, editable_alpha = [], [], []
        fixed_x, fixed_y, fixed_idx = [], [], []

        for i in range(n):
            if editable(edge_shape, n, i):
                editable_x.append(curve[i][0])
                editable_y.append(curve[i][1])
                editable_idx.append(i)
                is_selected = i == selected
                editable_fill_color.append(SELECTED_FILL if is_selected else NODE_FILL)
                editable_line_color.append(SELECTED_LINE if is_selected else NODE_LINE)
                editable_alpha.append(1.0 if is_selected or selected < 0 else MUTED_ALPHA)
            else:
                fixed_x.append(curve[i][0])
                fixed_y.append(curve[i][1])
                fixed_idx.append(i)

        # Update editable and fixed points sources.
        # Wrap in _programmatic_cp_update so _on_curve_change skips history push.
        self._programmatic_cp_update = True
        try:
            self._cp_source.data = {
                "x": editable_x,
                "y": editable_y,
                "idx": editable_idx,
                "idx_text": [f"P{i}" for i in editable_idx],
                "editable": [True] * len(editable_x),
                "fill_color": editable_fill_color,
                "line_color": editable_line_color,
                "alpha": editable_alpha,
                "edge_shape": [edge_shape] * len(editable_x),
            }
            self._fixed_cp_source.data = {
                "x": fixed_x,
                "y": fixed_y,
                "idx": fixed_idx,
                "fill_color": [FIXED_FILL] * len(fixed_x),
                "line_color": [FIXED_LINE] * len(fixed_x),
            }
        finally:
            self._programmatic_cp_update = False

        self._show_curve(curve_id, curve)

    def _show_curve(self, curve_id: int, curve) -> None:
        """Draw the curve of a shape id in its mode (linear through the control points, or spline)."""
        pts = interpolate(curve, self.state.draft.mode(curve_id))
        self._curve_source.data = {"x": pts[:, 0].tolist(), "y": pts[:, 1].tolist()}

    def _on_double_tap(self, event):
        """Handle double-click: add point on curve, remove point if on existing control point."""
        if event.x is None or event.y is None:
            return

        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curve = list(self.state.draft.curve(curve_id))
        edge_shape = self.state.shape_type(curve_id)

        # Allow x < 0 and x > 1 for creative freedom
        click_x = event.x
        click_y = max(-self._y_scale, min(self._y_scale, event.y))

        # Check if double-click is on an existing control point
        threshold = 0.05  # Distance threshold for "on point"
        for i, (px, py) in enumerate(curve):
            dist = ((px - click_x) ** 2 + (py - click_y) ** 2) ** 0.5
            if dist < threshold:
                # Double-click on existing point - remove it (and its partner on S and U curves) if editable
                if editable(edge_shape, len(curve), i) and len(curve) > 2:
                    self.state.remove_node(curve_id, i)
                return

        # Not on existing point - check if near the curve
        # For U-type curves, don't allow adding points with x > 0.5
        if edge_shape == "U" and click_x > 0.5:
            return

        curve_pts = interpolate(curve, self.state.draft.mode(curve_id))

        # Check if click is near the curve (within threshold)
        near_curve = False
        for i in range(len(curve_pts) - 1):
            x1, y1 = curve_pts[i]
            x2, y2 = curve_pts[i + 1]
            # Check distance to line segment
            if self._point_near_segment(click_x, click_y, x1, y1, x2, y2, threshold):
                near_curve = True
                break

        if near_curve:
            self.state.add_node(curve_id, click_x, click_y)

    def _point_near_segment(self, px, py, x1, y1, x2, y2, threshold):
        """Check if point (px, py) is within threshold distance of line segment (x1,y1)-(x2,y2)."""
        # Vector from p1 to p2
        dx = x2 - x1
        dy = y2 - y1

        # If segment is a point, check distance to that point
        if dx == 0 and dy == 0:
            return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5 < threshold

        # Parameter t for closest point on line
        t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))

        # Closest point on segment
        closest_x = x1 + t * dx
        closest_y = y1 + t * dy

        # Distance from point to closest point on segment
        dist = ((px - closest_x) ** 2 + (py - closest_y) ** 2) ** 0.5
        return dist < threshold

    def _on_curve_change(self, attr, old, new):
        """Callback when control points change - updates state directly."""
        if self._programmatic_cp_update:
            return  # Triggered by _update_figure, not a user drag
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        try:
            new_x = list(new.get("x", []))
            new_y = list(new.get("y", []))
            new_idx = list(new.get("idx", []))
        except (AttributeError, TypeError):
            return

        if not new_x:
            return

        # Reconstruct the full curve by updating the editable points of the current curve
        curve = list(self.state.draft.curve(curve_id))
        edge_shape = self.state.shape_type(curve_id)
        for x, y, idx in zip(new_x, new_y, new_idx, strict=False):
            if idx < len(curve):
                # Allow x < 0 and x > 1 for creative freedom
                curve[idx] = (x, max(-self._y_scale, min(self._y_scale, y)))

        # Set flag to prevent _update_figure from running (circular update). The history entries of one drag
        # coalesce: a pause longer than 500 ms starts a new entry.
        self._updating_from_state = True
        try:
            committed = self.state.set_curve(
                curve_id,
                curve,
                f"Move curve {curve_id} control point",
                key=f"curve_drag_{curve_id}",
                window=0.5,
            )
        finally:
            self._updating_from_state = False
        if not committed:
            self._update_figure()  # back to the curve of the draft
            return

        # Show the symmetric curve; the JS callback draws only straight lines during the drag
        curve = self.state.draft.curve(curve_id)
        self._show_curve(curve_id, curve)
        n = len(curve)
        fixed = [i for i in range(n) if not editable(edge_shape, n, i)]
        self._fixed_cp_source.data = {
            "x": [curve[i][0] for i in fixed],
            "y": [curve[i][1] for i in fixed],
            "idx": fixed,
            "fill_color": [FIXED_FILL] * len(fixed),
            "line_color": [FIXED_LINE] * len(fixed),
        }

    def _apply_preset(self, preset_name: str):
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return
        if self._preset_reasons.get(preset_name):
            return
        preset_label = preset_name.replace("_", " ")
        self.state.set_curve(curve_id, get_curve_preset(preset_name), f"Apply '{preset_label}' to curve {curve_id}")

    def _create_preset_icon(self, preset_name: str) -> str:
        pts = get_curve_preset(preset_name)
        width, height, pad = 24, 24, 3
        inner_width = width - 4
        inner_height = height - 4

        def tx(x):
            return pad + x * inner_width

        def ty(y):
            return pad + (0.25 - y) / 0.5 * inner_height

        poly = " ".join(f"{tx(x):.1f},{ty(y):.1f}" for x, y in pts)
        by = f"{ty(0):.1f}"

        return (
            f'<svg width="{width}" height="{height}" viewbox="0 0 24 30" xmlns="http://www.w3.org/2000/svg">'
            f'<line x1="{pad}" y1="{by}" x2="{pad + inner_width}" y2="{by}" stroke="#ddd" stroke-width="1"/>'
            f'<polyline points="{poly}" fill="none" stroke="#1971c2" stroke-width="1.5"/>'
            f"</svg>"
        )

    def __panel__(self):
        return pn.Column(
            pn.pane.Markdown("### Edge Curves"),
            self._build_curve_selector_row,
            pn.Row(
                pn.pane.Bokeh(self._figure, height=210, sizing_mode="stretch_width"),
                self._preset_grid,
                sizing_mode="stretch_width",
            ),
            self._build_point_inputs,  # Pass as callable for reactivity
            sizing_mode="stretch_width",
            margin=5,
            align=("center", "start"),
        )

    @param.depends("state.tiling_type", "state.active_curve_id", "state.edge_curve_modes", watch=False)
    def _build_curve_selector_row(self):
        """Build combined row with curve selector, info label, and mode widget."""
        if self.state is None:
            return pn.pane.Markdown("*No state set*")

        info = self.state.type_info
        customizable = info.get("customizable_shape_ids", [])
        if not customizable:
            return pn.pane.Markdown("*No editable edge curves for this tile type*")

        n_curves = len(customizable)

        # Clamp active_curve_id to a valid customizable shape of the current type.
        # Using display_sid everywhere prevents the Select widget from auto-correcting to a
        # random option (which would corrupt active_curve_id via its watcher).
        current_sid = self.state.active_curve_id
        display_sid = current_sid if current_sid in customizable else customizable[0]

        # Narrow radio buttons: bare IDs with reduced padding via stylesheet
        def _shape_type(sid):
            e = next((e for e in info["edges"] if e["shape_id"] == sid), None)
            return e["shape_type"] if e else "J"

        radio = pn.widgets.Select(  # RadioButtonGroup(
            options={f"C{sid} ({_shape_type(sid)})": sid for sid in customizable},
            value=display_sid,
            # button_type='default',
            # button_style='outline',
            margin=0,
            width=70,
            # stylesheets=["""
            #     .bk-btn { padding: 2px 2px !important; font-size: 12px !important; }
            # """],
        )
        radio.param.watch(lambda e: setattr(self.state, "active_curve_id", e.new), "value")

        count_chip = pn.pane.HTML(
            f"<span style='font-size:14px;color:#888;white-space:nowrap'>"
            f"{n_curves}&nbsp;curve{'s' if n_curves != 1 else ''}:</span>",
            margin=(0, 4, 0, 0),
            styles={"line-height": "24px"},
            width=55,
            align="center",
        )

        # Compatible copy-from control (goes on row 1, right side)
        edge_info = next((e for e in info["edges"] if e["shape_id"] == display_sid), None)
        active_shape_type = edge_info["shape_type"] if edge_info else "J"
        compatible = []
        for sid in customizable:
            if sid == display_sid:
                continue
            src_info = next((e for e in info["edges"] if e["shape_id"] == sid), None)
            src_type = src_info["shape_type"] if src_info else "J"
            if src_type == active_shape_type or active_shape_type == "J":
                compatible.append((sid, src_type))

        if compatible:
            copy_options = {f"C{sid} ({stype})": sid for sid, stype in compatible}
            copy_select = pn.widgets.Select(
                options=copy_options,
                value=next(iter(copy_options.values())),
                # button_type='default',
                # button_style='outline',
                align="center",
                sizing_mode="stretch_width",
                width=70,
                margin=(0, 0, 0, 0),
                # stylesheets=["""
                #     .bk-btn { padding: 2px 7px !important; font-size: 12px !important; }
                # """],
            )

            def _do_copy(event, _sel=copy_select):
                self._copy_from_curve(_sel.value)

            copy_btn = pn.widgets.Button(
                name="Copy from:",
                on_click=_do_copy,
                width=70,
                align="center",
                sizing_mode="stretch_width",
                button_type="default",
                margin=(0, 0, 0, 0),
                stylesheets=[
                    """
                    .bk-btn { padding: 2px 7px !important; font-size: 12px !important; }
                """
                ],
            )
            copy_control = pn.Column(
                pn.Row(copy_btn, align="center"),
                pn.Row(copy_select, align="center"),
                # margin=0,
                sizing_mode="fixed",
                width=100,
                align="center",
                margin=(0, 0, 0, 10),
            )
        else:
            copy_control = pn.Spacer(width=0)

        # Row 2: terse info + inline mode toggle
        if edge_info is not None:
            shape_type = edge_info["shape_type"]
            type_desc = {"J": "free", "S": "180°rot", "U": "mirror", "I": "fixed"}.get(shape_type, "?")
            edges_using = [e["edge_index"] for e in info["edges"] if e["shape_id"] == display_sid]
            edge_list = ", ".join(str(e) for e in edges_using)
            info_label = pn.pane.HTML(
                f"<span style='color:#666;font-size:12px;white-space:nowrap'>"
                f"<b>{shape_type}</b> ({type_desc}) &middot; edges {edge_list}</span>",
                margin=(10, 0, 10, 0),
                stylesheets=[":host { padding: 0 !important; margin: 0 !important; }"],
            )

            current_mode = self.state.draft.mode(display_sid)

            def on_mode_change(event):
                self.state.set_mode(display_sid, event.new)

            mode_widget = pn.widgets.RadioBoxGroup(
                options=["linear", "spline"],
                value=current_mode,
                inline=False,
                margin=(0, 10),
            )
            mode_widget.param.watch(on_mode_change, "value")
        else:
            info_label = pn.Spacer(width=0)
            mode_widget = pn.Spacer(width=0)

        return pn.Row(
            pn.Column(
                pn.Row(count_chip, radio, mode_widget, align="center"),
                info_label,
                sizing_mode="stretch_width",
            ),
            pn.HSpacer(),
            copy_control,
            sizing_mode="stretch_width",
            align="center",
        )

    def _build_preset_buttons(self):
        # Preset descriptions for tooltips
        preset_descriptions = {
            "straight": "Straight line",
            "scalloped": "Gentle upward scallop",
            "scalloped_down": "Gentle downward scallop",
            "scalloped_deep": "Deep upward scallop",
            "scalloped_deep_down": "Deep downward scallop",
            "s_curve": "S-curve (up then down)",
            "s_curve_alt": "S-curve (down then up)",
            "puzzle": "Puzzle piece tab (up)",
            "puzzle_inverse": "Puzzle piece tab (down)",
            "arrow": "Arrow pointing up",
            "arrow_down": "Arrow pointing down",
            "bulge": "Bulge upward",
            "dent": "Dent downward",
            "hook_right": "Hook curving right",
            "hook_left": "Hook curving left",
            "ramp_up": "Ramp up then flat",
            "ramp_down": "Flat then ramp down",
        }

        self._preset_descriptions: dict[str, str] = {}
        presets = list_curve_presets()
        buttons = []

        for name in presets:
            svg = self._create_preset_icon(name)
            # Get description for tooltip
            desc = preset_descriptions.get(name, name.replace("_", " ").title())

            btn = pn.widgets.ButtonIcon(
                icon=svg,
                active_icon=svg,  # the default active icon is a check mark that stays after a click
                description=desc,
                on_click=lambda e, n=name: self._apply_preset(n),
                size="24px",
                tags=[f"preset:{name}"],
            )
            self._preset_buttons[name] = btn
            self._preset_descriptions[name] = desc

            buttons.append(btn)

        return pn.GridBox(*buttons, ncols=3, sizing_mode="fixed", width=100)

    @param.depends("state.draft", "state.active_curve_id", watch=True)
    def _refresh_presets(self):
        """Mute the presets that cannot be applied to the selected curve and say why in the tooltip."""
        curve_id = self.state.active_curve_id
        if not self._preset_buttons or curve_id is None:
            return
        status = preset_availability(self.state.draft, curve_id)
        for name, button in self._preset_buttons.items():
            reason = status[name]
            description = self._preset_descriptions[name]
            self._preset_reasons[name] = reason
            button.description = f"{description}. Not available: {reason}" if reason else description
            button.styles = {"opacity": "0.35"} if reason else {}

    @param.depends("state.draft", "state.active_curve_id", watch=False)
    def _build_point_inputs(self):
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return pn.Spacer()

        curve = self.state.draft.curve(curve_id)
        n = len(curve)
        edge_shape = self.state.shape_type(curve_id)

        # Prepare data for Tabulator
        half = (n - 1) / 2
        table_data = []
        for i, (x, y) in enumerate(curve):
            is_editable = editable(edge_shape, n, i)
            # For U curves, midpoint x is not editable (only y is)
            is_midpoint_u = edge_shape == "U" and abs(i - half) < 1e-9
            x_editable = is_editable and not is_midpoint_u
            table_data.append({
                "Point": f"P{i}",
                "X": round(x, 2),
                "Y": round(y, 2),
                "Editable": is_editable,
                "XEditable": x_editable,
                "Index": i,
            })

        # Create Tabulator with fixed dimensions and styling
        import pandas as pd

        df = pd.DataFrame(table_data)
        # Use JSCode for per-cell editing control
        from panel.io import JSCode

        # Function to determine if X column is editable
        x_editable_code = JSCode("""
            function format(cell) {
                var data = cell.getRow().getData();
                return data.XEditable;
            }
        """)

        # Function to determine if Y column is editable
        y_editable_code = JSCode("""
            function format(cell) {
                var data = cell.getRow().getData();
                return data.Editable;
            }
        """)

        table = pn.widgets.Tabulator(
            df,
            height=200,
            width=380,
            selectable=False,
            header_filters=False,
            pagination=None,  # Disable pagination
            sizing_mode="fixed",
            hidden_columns=["Index", "XEditable"],
            formatters={
                "Point": {"type": "plaintext"},
                "X": {"type": "numeric", "precision": 2},
                "Y": {"type": "numeric", "precision": 2},
                "Editable": {"type": "tickCross"},
            },
            editors={
                "Point": None,  # Non-editable
                "X": {"type": "number", "step": 0.01, "min": -2, "max": 2},
                "Y": {"type": "number", "step": 0.01, "min": -5, "max": 5},
                "Editable": None,  # Non-editable
            },
            editables={"X": x_editable_code, "Y": y_editable_code, "Point": False, "Editable": False},
            show_index=False,
            layout="fit_columns",
        )

        # Handle cell edits
        def on_edit(event):
            # event.row is the integer index, not the row data
            idx = table.value.iloc[event.row]["Index"]
            col = event.column
            value = event.value
            if col == "X":
                handler = partial(self._on_x_change, idx, value)
            elif col == "Y":
                handler = partial(self._on_y_change, idx, value)
            else:
                return
            # Panel calls on_edit outside the document lock, and the edit changes Bokeh models (the figures):
            # run it as a scheduled callback.
            pn.state.execute(handler, schedule=True)

        table.on_edit(on_edit)

        return pn.Column(
            # pn.pane.Markdown("**Control Points:**"),
            pn.pane.Markdown("Double-click on curve to add point, on control point to remove"),
            table,
            sizing_mode="stretch_width",
        )

    def _on_x_change(self, idx: int, value: float):
        self._on_point_change(idx, x=value)

    def _on_y_change(self, idx: int, value: float):
        self._on_point_change(idx, y=value)

    def _on_point_change(self, idx: int, x: float | None = None, y: float | None = None):
        """Move a node to the value typed in the table; the symmetric partner follows."""
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curve = self.state.draft.curve(curve_id)
        if idx < len(curve):
            self.state.move_node(
                curve_id,
                idx,
                curve[idx][0] if x is None else x,
                curve[idx][1] if y is None else y,
                f"Edit curve {curve_id} control point",
                key=f"curve_point_{curve_id}",
                window=1.0,
            )

    def _copy_from_curve(self, src_curve_id: int):
        """Copy nodes from src_curve_id to the active curve, enforcing destination symmetry."""
        curve_id = self.state.active_curve_id
        if curve_id is None or src_curve_id is None:
            return
        self.state.copy_curve(src_curve_id, curve_id)
