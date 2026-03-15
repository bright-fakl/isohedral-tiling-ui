"""Edge curve editor with draggable control points."""

from __future__ import annotations

import panel as pn
import param
from bokeh.events import DoubleTap
from bokeh.models import ColumnDataSource, CustomJS
from bokeh.plotting import figure


class CurveEditor(pn.viewable.Viewer):
    """Edge curve editor with draggable control points."""

    state = param.ClassSelector(class_=param.Parameterized)
    selected_point_idx = param.Integer(default=1)

    def __init__(self, **params):
        super().__init__(**params)
        self._cp_source = ColumnDataSource(
            data={"x": [], "y": [], "idx": [], "editable": [], "fill_color": [], "line_color": [], "edge_shape": []}
        )
        # Separate source for fixed (non-editable) points - not connected to PointDrawTool
        self._fixed_cp_source = ColumnDataSource(data={"x": [], "y": [], "idx": [], "fill_color": [], "line_color": []})
        self._curve_source = ColumnDataSource(data={"x": [], "y": []})
        self._baseline_source = ColumnDataSource(data={"x": [0, 1], "y": [0, 0]})
        self._updating_from_state = False  # Flag to prevent circular updates
        self._programmatic_cp_update = False  # Flag to skip history push on programmatic cp_source writes
        self._y_scale = 1.0  # Y-axis scale factor (default ±1)
        self._figure = self._build_figure()
        self._setup_callbacks()
        # Initialize with current state values since @param.depends doesn't fire on init
        self._update_figure()

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
        )

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

    def _setup_callbacks(self):
        """Set up callbacks for drag and selection."""
        # Only use Python callback - JS pan event handles live feedback during drag
        self._cp_source.on_change("data", self._on_curve_change)

    @param.depends(
        "state._tiling",
        "state.active_curve_id",
        "state.edge_curves",
        "state.edge_curve_modes",
        "state._type_info",
        "state.parameters_version",
        watch=True,
    )
    def _update_figure(self):
        """Update curve visualization."""
        # Skip if we're in the middle of updating from a drag operation
        if self._updating_from_state:
            return

        # Skip during batch updates - wait for _type_info to be updated
        if hasattr(self.state, "_batching") and self.state._batching:
            return

        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curve = self.state.edge_curves.get(curve_id, [(0, 0), (1, 0)])
        n = len(curve)

        info = self.state._type_info
        if info is None:
            print("[DEBUG] _update_figure: _type_info is None, skipping")
            return
        edge_shape = next((e["shape_type"] for e in info["edges"] if e["shape_id"] == curve_id), "J")

        # Separate editable and fixed points
        editable_x = []
        editable_y = []
        editable_idx = []
        editable_fill_color = []
        editable_line_color = []

        fixed_x = []
        fixed_y = []
        fixed_idx = []
        fixed_fill_color = []
        fixed_line_color = []

        (n - 1) / 2

        for i in range(n):
            is_editable = self._is_editable(i, n, edge_shape)

            # Determine color
            if i == self.selected_point_idx:
                fill = "crimson"
                line = "#7a0000"
            elif not is_editable:
                # Non-editable points (endpoints, S midpoint, mirrored points)
                fill = "#ccc"
                line = "#999"
            else:
                fill = "#444"
                line = "#111"

            if is_editable:
                editable_x.append(curve[i][0])
                editable_y.append(curve[i][1])
                editable_idx.append(i)
                editable_fill_color.append(fill)
                editable_line_color.append(line)
            else:
                fixed_x.append(curve[i][0])
                fixed_y.append(curve[i][1])
                fixed_idx.append(i)
                fixed_fill_color.append(fill)
                fixed_line_color.append(line)

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
                "edge_shape": [edge_shape] * len(editable_x),
            }
            self._fixed_cp_source.data = {
                "x": fixed_x,
                "y": fixed_y,
                "idx": fixed_idx,
                "fill_color": fixed_fill_color,
                "line_color": fixed_line_color,
            }
        finally:
            self._programmatic_cp_update = False

        # Get mode from state.edge_curve_modes for this curve
        curve_mode = self.state.edge_curve_modes.get(curve_id, "spline")
        curve_pts = self._interpolate_spline(curve) if curve_mode == "spline" and n >= 3 else curve

        self._curve_source.data = {"x": [p[0] for p in curve_pts], "y": [p[1] for p in curve_pts]}

    def _is_editable(self, i: int, n: int, edge_shape: str) -> bool:
        if i == 0 or i == n - 1:
            return False
        if edge_shape == "S":
            half = (n - 1) / 2
            if abs(i - half) < 1e-9:
                return False  # Midpoint not editable for S curves
            return i < half + 1e-9
        if edge_shape == "U":
            half = (n - 1) / 2
            if abs(i - half) < 1e-9:
                return True  # Midpoint IS editable for U curves (y-only)
            return i < half + 1e-9
        return True

    def _on_double_tap(self, event):
        """Handle double-click: add point on curve, remove point if on existing control point."""
        if event.x is None or event.y is None:
            return

        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curve = list(self.state.edge_curves.get(curve_id, [(0, 0), (1, 0)]))
        edge_shape = self._get_edge_shape()

        # Allow x < 0 and x > 1 for creative freedom
        click_x = event.x
        click_y = max(-self._y_scale, min(self._y_scale, event.y))

        # Check if double-click is on an existing control point
        threshold = 0.05  # Distance threshold for "on point"
        for i, (px, py) in enumerate(curve):
            dist = ((px - click_x) ** 2 + (py - click_y) ** 2) ** 0.5
            if dist < threshold:
                # Double-click on existing point - remove it if editable
                if self._is_editable(i, len(curve), edge_shape) and len(curve) > 2:
                    curve.pop(i)
                    # Enforce symmetry to update mirrored points
                    curve = self._enforce_symmetry(curve, edge_shape)
                    curves = dict(self.state.edge_curves)
                    curves[curve_id] = curve
                    self.state.edge_curves = curves
                    self.state.push_history(f"Remove point from curve {curve_id}", force_new=True)
                return

        # Not on existing point - check if near the curve
        # For U-type curves, don't allow adding points with x > 0.5
        if edge_shape == "U" and click_x > 0.5:
            return

        # Get the curve mode to determine which curve to use for nearness check
        curve_mode = self.state.edge_curve_modes.get(curve_id, "spline")
        if curve_mode == "spline" and len(curve) >= 3:
            # Use spline curve for nearness check
            curve_pts = self._interpolate_spline(curve)
        else:
            # Use linear curve (control points)
            curve_pts = curve

        # Check if click is near the curve (within threshold)
        near_curve = False
        for i in range(len(curve_pts) - 1):
            x1, y1 = curve_pts[i]
            x2, y2 = curve_pts[i + 1]
            # Check distance to line segment
            if self._point_near_segment(click_x, click_y, x1, y1, x2, y2, threshold):
                near_curve = True
                break

        if not near_curve:
            return

        # Find insertion point in the control points (not the spline)
        insert_after = 0
        for i in range(len(curve) - 1):
            if curve[i][0] <= click_x:
                insert_after = i

        curve.insert(insert_after + 1, (click_x, click_y))

        curves = dict(self.state.edge_curves)
        curves[curve_id] = self._enforce_symmetry(curve, edge_shape)
        self.state.edge_curves = curves
        self.state.push_history(f"Add point to curve {curve_id}", force_new=True)

        # Ensure mode is set for this curve (default to spline)
        if curve_id not in self.state.edge_curve_modes:
            modes = dict(self.state.edge_curve_modes)
            modes[curve_id] = "spline"
            self.state.edge_curve_modes = modes

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

    def _on_right_click(self, event):
        """Handle right-click (contextmenu) event to remove control points"""
        # Find nearest point to click location
        data = self._cp_source.data
        min_dist = float("inf")
        selected_idx = -1

        for i, (x, y) in enumerate(zip(data["x"], data["y"], strict=False)):
            dist = (x - event.x) ** 2 + (y - event.y) ** 2
            if dist < min_dist:
                min_dist = dist
                selected_idx = i

        if selected_idx >= 0:
            self.selected_point_idx = selected_idx
            # Remove point if it's editable
            curve_id = self.state.active_curve_id
            if curve_id is not None:
                curve = list(self.state.edge_curves.get(curve_id, [(0, 0), (1, 0)]))
                n = len(curve)
                edge_shape = self._get_edge_shape()
                if self._is_editable(selected_idx, n, edge_shape):
                    self._remove_point_at(selected_idx)

    def _on_curve_change(self, attr, old, new):
        """Callback when control points change - updates state directly."""
        if self._programmatic_cp_update:
            return  # Triggered by _update_figure, not a user drag — skip history push
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

        # Get original curve to reconstruct full curve
        original_curve = list(self.state.edge_curves.get(curve_id, [(0, 0), (1, 0)]))
        edge_shape = self._get_edge_shape()

        # Clamp coordinates of editable points
        for i in range(len(new_x)):
            # Allow x < 0 and x > 1 for creative freedom
            new_y[i] = max(-self._y_scale, min(self._y_scale, new_y[i]))

        # Reconstruct the full curve by updating editable points
        curve = list(original_curve)
        for i, idx in enumerate(new_idx):
            if idx < len(curve):
                curve[idx] = (new_x[i], new_y[i])

        # Enforce symmetry
        curve = self._enforce_symmetry(curve, edge_shape)

        # Set flag to prevent _update_figure from running (circular update)
        self._updating_from_state = True
        try:
            curves = dict(self.state.edge_curves)
            curves[curve_id] = curve
            self.state.edge_curves = curves

            # Ensure mode is set for this curve (default to spline)
            if curve_id not in self.state.edge_curve_modes:
                modes = dict(self.state.edge_curve_modes)
                modes[curve_id] = "spline"
                self.state.edge_curve_modes = modes
        finally:
            self._updating_from_state = False
            # Update curve visualization with spline if needed
            # (JS callback only provides linear interpolation during drag)
            curve_mode = self.state.edge_curve_modes.get(curve_id, "spline")
            if curve_mode == "spline" and len(curve) >= 3:
                curve_pts = self._interpolate_spline(curve)
                self._curve_source.data = {"x": [p[0] for p in curve_pts], "y": [p[1] for p in curve_pts]}

            # Update fixed points source
            n = len(curve)
            fixed_x = []
            fixed_y = []
            fixed_idx = []
            fixed_fill_color = []
            fixed_line_color = []
            (n - 1) / 2

            for i in range(n):
                is_editable = self._is_editable(i, n, edge_shape)
                if not is_editable:
                    fixed_x.append(curve[i][0])
                    fixed_y.append(curve[i][1])
                    fixed_idx.append(i)

                    # Non-editable points are gray
                    fixed_fill_color.append("#ccc")
                    fixed_line_color.append("#999")

            self._fixed_cp_source.data = {
                "x": fixed_x,
                "y": fixed_y,
                "idx": fixed_idx,
                "fill_color": fixed_fill_color,
                "line_color": fixed_line_color,
            }

        # Push to history after the drag frame settles.
        # Short coalesce window collapses all drag-frame events into one entry;
        # a pause longer than 500ms starts a fresh entry.
        self.state.push_history(
            f"Move curve {curve_id} control point",
            coalesce_key=f"curve_drag_{curve_id}",
            coalesce_window_ms=500,
        )

    def _enforce_symmetry(self, curve: list, edge_shape: str) -> list:
        from tactile import EdgeShape

        from isohedral_tiling_ui.utils.symmetry import enforce_curve_symmetry

        sm = {"J": EdgeShape.J, "S": EdgeShape.S, "U": EdgeShape.U, "I": EdgeShape.I}
        if edge_shape in sm:
            return enforce_curve_symmetry(curve, sm[edge_shape])
        return curve

    def _interpolate_spline(self, curve: list) -> list:
        from isohedral_tiling_ui.utils.spline_cache import interpolate_spline

        return interpolate_spline(curve)

    def _apply_preset(self, preset_name: str):
        from tactile import EdgeShape

        from isohedral_tiling_ui.utils.symmetry import enforce_curve_symmetry, get_curve_preset

        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        pts = list(get_curve_preset(preset_name))

        info = self.state._type_info
        if info is None:
            return
        edge_shape = next((e["shape_type"] for e in info["edges"] if e["shape_id"] == curve_id), "J")
        sm = {"J": EdgeShape.J, "S": EdgeShape.S, "U": EdgeShape.U, "I": EdgeShape.I}
        if edge_shape in sm:
            pts = enforce_curve_symmetry(pts, sm[edge_shape])

        curves = dict(self.state.edge_curves)
        curves[curve_id] = pts
        self.state.edge_curves = curves

        # Ensure mode is set for this curve (default to spline)
        if curve_id not in self.state.edge_curve_modes:
            modes = dict(self.state.edge_curve_modes)
            modes[curve_id] = "spline"
            self.state.edge_curve_modes = modes

        preset_label = preset_name.replace("_", " ")
        self.state.push_history(f"Apply '{preset_label}' to curve {curve_id}", force_new=True)

    def _create_preset_icon(self, preset_name: str) -> str:
        from isohedral_tiling_ui.utils.symmetry import get_curve_preset

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
                self._build_preset_buttons(),
                sizing_mode="stretch_width",
            ),
            self._build_point_inputs,  # Pass as callable for reactivity
            sizing_mode="stretch_width",
            margin=5,
            align=("center", "start"),
        )

    @param.depends("state._type_info", "state.active_curve_id", "state.edge_curve_modes", watch=False)
    def _build_curve_selector_row(self):
        """Build combined row with curve selector, info label, and mode widget."""
        if self.state is None:
            return pn.pane.Markdown("*No state set*")

        info = self.state._type_info
        if info is None:
            return pn.pane.Markdown("*Loading...*")

        customizable = info.get("customizable_shape_ids", [])
        if not customizable:
            return pn.pane.Markdown("*No editable edge curves for this tile type*")

        n_curves = len(customizable)

        # Clamp active_curve_id to a valid customizable shape.
        # active_curve_id may be stale (from a previous tile) during an
        # intermediate render that occurs between _type_info and active_curve_id
        # being updated in _update_tiling().  Using display_sid everywhere
        # prevents the Select widget from auto-correcting to a random option
        # (which would corrupt active_curve_id via its watcher).
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

            current_mode = self.state.edge_curve_modes.get(display_sid, "spline")

            def on_mode_change(event):
                modes = dict(self.state.edge_curve_modes)
                modes[display_sid] = event.new
                self.state.edge_curve_modes = modes

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

    @param.depends("state.active_curve_id", "state.edge_curve_modes", watch=False)
    def _build_curve_mode_widget(self):
        """Build curve mode widget that reads/writes to state.edge_curve_modes."""
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return pn.Spacer()

        current_mode = self.state.edge_curve_modes.get(curve_id, "spline")

        def on_mode_change(event):
            # Update the mode for the current curve
            modes = dict(self.state.edge_curve_modes)
            modes[curve_id] = event.new
            self.state.edge_curve_modes = modes

        radio = pn.widgets.RadioBoxGroup(
            options=["linear", "spline"],
            value=current_mode,
            inline=True,
        )
        radio.param.watch(on_mode_change, "value")
        return radio

    def _build_preset_buttons(self):
        from isohedral_tiling_ui.utils.symmetry import list_curve_presets

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

        presets = list_curve_presets()
        buttons = []

        for name in presets:
            svg = self._create_preset_icon(name)
            # svg_b64 = base64.b64encode(svg.encode('utf-8')).decode('utf-8')

            # Get description for tooltip
            desc = preset_descriptions.get(name, name.replace("_", " ").title())

            btn = pn.widgets.ButtonIcon(
                icon=svg,
                description=desc,
                on_click=lambda e, n=name: self._apply_preset(n),
                size="24px",
            )

            # btn = pn.widgets.Button(
            #     name="",
            #     width=24, height=30, min_width=20,
            #     margin=(1, 1, 1, 1),
            #     description=desc,  # Tooltip on hover
            #     on_click=lambda e, n=name: self._apply_preset(n),
            #     sizing_mode="fixed"
            # )
            # btn.stylesheets = [f"""
            #     button.bk-btn {{
            #         background-image: url("data:image/svg+xml;base64,{svg_b64}") !important;
            #         background-repeat: no-repeat !important;
            #         background-position: center !important;
            #         background-size: 24px 24px !important;
            #         background-color: white !important;
            #         border: 1px solid #ccc !important;
            #         border-radius: 4px !important;
            #         width: 24px !important;
            #         height: 24px !important;
            #         padding: 2px !important;
            #     }}
            # """]
            buttons.append(btn)

        return pn.GridBox(*buttons, ncols=3, sizing_mode="fixed", width=100)

    @param.depends("state.edge_curves", "state.active_curve_id", "state._type_info", "state._batching", watch=False)
    def _build_point_inputs(self):
        # Skip during batch updates - wait for _type_info to be updated
        if hasattr(self.state, "_batching") and self.state._batching:
            return pn.Spacer()

        curve_id = self.state.active_curve_id
        if curve_id is None:
            return pn.Spacer()

        curve = self.state.edge_curves.get(curve_id, [(0, 0), (1, 0)])
        n = len(curve)

        info = self.state._type_info
        if info is None:
            return pn.pane.Markdown("*Loading...*")
        edge_shape = next((e["shape_type"] for e in info["edges"] if e["shape_id"] == curve_id), "J")

        # Prepare data for Tabulator
        half = (n - 1) / 2
        table_data = []
        for i, (x, y) in enumerate(curve):
            editable = self._is_editable(i, n, edge_shape)
            # For U curves, midpoint x is not editable (only y is)
            is_midpoint_u = edge_shape == "U" and abs(i - half) < 1e-9
            x_editable = editable and not is_midpoint_u
            table_data.append({
                "Point": f"P{i}",
                "X": round(x, 2),
                "Y": round(y, 2),
                "Editable": editable,
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
                self._on_x_change(idx, value)
            elif col == "Y":
                self._on_y_change(idx, value)

        table.on_edit(on_edit)

        return pn.Column(
            # pn.pane.Markdown("**Control Points:**"),
            pn.pane.Markdown("Double-click on curve to add point, on control point to remove"),
            table,
            # pn.Row(
            #     pn.widgets.Button(name="+ Add Point", on_click=self._add_point),
            #     pn.widgets.Button(name="− Remove Point", on_click=self._remove_point),
            # ),
            # height=40
            sizing_mode="stretch_width",
        )

    def _on_x_change(self, idx: int, value: float):
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curves = dict(self.state.edge_curves)
        curve = list(curves.get(curve_id, [(0, 0), (1, 0)]))
        edge_shape = self._get_edge_shape()

        if idx < len(curve):
            # For U curves, constrain midpoint x to 0.5
            n = len(curve)
            half = (n - 1) / 2
            if edge_shape == "U" and abs(idx - half) < 1e-9:
                value = 0.5  # Midpoint x is fixed at 0.5 for U curves

            curve[idx] = (value, curve[idx][1])
            curves[curve_id] = self._enforce_symmetry(curve, edge_shape)
            self.state.edge_curves = curves
            self.state.push_history(
                f"Edit curve {curve_id} control point",
                coalesce_key=f"curve_point_{curve_id}",
                coalesce_window_ms=1000,
            )

    def _on_y_change(self, idx: int, value: float):
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curves = dict(self.state.edge_curves)
        curve = list(curves.get(curve_id, [(0, 0), (1, 0)]))
        if idx < len(curve):
            curve[idx] = (curve[idx][0], value)
            curves[curve_id] = self._enforce_symmetry(curve, self._get_edge_shape())
            self.state.edge_curves = curves
            self.state.push_history(
                f"Edit curve {curve_id} control point",
                coalesce_key=f"curve_point_{curve_id}",
                coalesce_window_ms=1000,
            )

    def _add_point(self, event):
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curves = dict(self.state.edge_curves)
        curve = list(curves.get(curve_id, [(0, 0), (1, 0)]))
        edge_shape = self._get_edge_shape()

        # Find the best place to add a point (in the editable region)
        # For S/U curves, only add points with x <= 0.5
        if len(curve) > 2:
            # Find the last editable segment
            insert_before = len(curve) - 1
            for i in range(len(curve) - 1, 0, -1):
                if self._is_editable(i, len(curve), edge_shape):
                    insert_before = i + 1
                    break

            # For S/U curves, ensure we're adding in the first half
            if edge_shape in ("S", "U"):
                # Find a segment in the first half
                for i in range(len(curve) - 1):
                    if curve[i][0] < 0.5 and curve[i + 1][0] <= 0.5 + 1e-9:
                        insert_before = i + 1
                        break
                else:
                    # No segment in first half, insert before midpoint
                    for i in range(len(curve)):
                        if curve[i][0] >= 0.5 - 1e-9:
                            insert_before = i
                            break

            x_new = (curve[insert_before - 1][0] + curve[insert_before][0]) / 2
            y_new = (curve[insert_before - 1][1] + curve[insert_before][1]) / 2
            curve.insert(insert_before, [x_new, y_new])
        else:
            curve.insert(1, [0.5, 0.1])

        curves[curve_id] = self._enforce_symmetry(curve, edge_shape)
        self.state.edge_curves = curves

    def _remove_point_at(self, idx: int):
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curves = dict(self.state.edge_curves)
        curve = list(curves.get(curve_id, [(0, 0), (1, 0)]))

        if len(curve) > 2:
            edge_shape = self._get_edge_shape()
            if self._is_editable(idx, len(curve), edge_shape):
                curve.pop(idx)
                # Enforce symmetry to update mirrored points
                curve = self._enforce_symmetry(curve, edge_shape)
                curves[curve_id] = curve
                self.state.edge_curves = curves

    def _remove_point(self, event):
        curve_id = self.state.active_curve_id
        if curve_id is None:
            return

        curves = dict(self.state.edge_curves)
        curve = list(curves.get(curve_id, [(0, 0), (1, 0)]))
        edge_shape = self._get_edge_shape()

        if len(curve) > 2:
            last_editable = None
            for i in range(len(curve) - 2, 0, -1):
                if self._is_editable(i, len(curve), edge_shape):
                    last_editable = i
                    break
            if last_editable is not None:
                curve.pop(last_editable)
                # Enforce symmetry to update mirrored points
                curve = self._enforce_symmetry(curve, edge_shape)
                curves[curve_id] = curve
                self.state.edge_curves = curves

    def _copy_from_curve(self, src_curve_id: str):
        """Copy nodes from src_curve_id to the active curve, enforcing destination symmetry."""
        from tactile import EdgeShape

        from isohedral_tiling_ui.utils.symmetry import enforce_curve_symmetry

        curve_id = self.state.active_curve_id
        if curve_id is None or src_curve_id is None:
            return

        src_pts = list(self.state.edge_curves.get(src_curve_id) or [(0, 0), (1, 0)])

        dst_shape_type = self._get_edge_shape()
        sm = {"J": EdgeShape.J, "S": EdgeShape.S, "U": EdgeShape.U, "I": EdgeShape.I}
        if dst_shape_type in sm:
            src_pts = enforce_curve_symmetry(src_pts, sm[dst_shape_type])

        curves = dict(self.state.edge_curves)
        curves[curve_id] = src_pts
        self.state.edge_curves = curves
        self.state.push_history(f"Copy curve {src_curve_id} → {curve_id}", force_new=True)

    def _get_edge_shape(self) -> str:
        info = self.state._type_info
        if info is None:
            return "J"
        curve_id = self.state.active_curve_id
        return next((e["shape_type"] for e in info["edges"] if e["shape_id"] == curve_id), "J")
