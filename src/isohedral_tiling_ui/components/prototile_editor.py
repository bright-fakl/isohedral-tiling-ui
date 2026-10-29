"""Interactive prototile editor with draggable vertices."""

from __future__ import annotations

import panel as pn
import param
from bokeh.models import BooleanFilter, CDSView, ColumnDataSource, CustomJS, LabelSet, PointDrawTool
from bokeh.plotting import figure

from isohedral_tiling_ui.components.probe import ProbeChannel
from isohedral_tiling_ui.editor_model import parameter_groups

INVALID_COLOR = "#d62728"  # the edges that make the prototile invalid
INVALID_TRACK = "rgba(214, 39, 40, 0.35)"  # the part of a slider track with invalid values


class PrototileEditor(pn.viewable.Viewer):
    """Interactive prototile editor with draggable vertices."""

    state = param.ClassSelector(class_=param.Parameterized)
    show_curves = param.Boolean(default=True)
    active_param_group = param.Integer(default=0)

    def __init__(self, **params):
        super().__init__(**params)
        # Separate sources for draggable and fixed vertices
        self._draggable_source = ColumnDataSource(
            data={"x": [], "y": [], "idx": [], "fill_color": [], "line_color": []}
        )
        self._fixed_source = ColumnDataSource(data={"x": [], "y": [], "idx": [], "fill_color": [], "line_color": []})
        self._outline_source = ColumnDataSource(data={"xs": [], "ys": []})
        self._edge_source = ColumnDataSource(data={"xs": [], "ys": [], "edge_idx": [], "shape_type": []})
        self._edge_label_source = ColumnDataSource(data={"x": [], "y": [], "text": [], "shape_type": []})
        self._curve_sources = {}
        self._curve_renderers = []  # Store line renderers for removal
        # Flag to prevent feedback loop between JS and Python callbacks
        self._in_drag = False
        self._primary_dragged = -1  # Track which vertex is being primarily dragged
        self._probe = ProbeChannel("vertex", self._on_probe)
        self._bad_filter = BooleanFilter(booleans=[])  # the edges of the shape ids that make the prototile invalid
        self._figure = self._build_figure()
        self._setup_callbacks()
        # Initialize with current state values since @param.depends doesn't fire on init
        self._update_figure()

    def _build_figure(self):
        """Build the Bokeh figure with prototile visualization."""
        from bokeh.models import Range1d

        # Create fixed range objects that won't auto-scale
        x_range = Range1d(0, 1)
        y_range = Range1d(0, 1)

        p = figure(
            width=400,
            height=280,
            tools="wheel_zoom,pan,reset",
            toolbar_location="above",
            match_aspect=True,
            x_range=x_range,
            y_range=y_range,
        )
        p.axis.visible = True
        p.grid.visible = True
        p.background_fill_color = "white"

        # Outline (closed polygon)
        p.line("xs", "ys", source=self._outline_source, line_color="#ccc", line_width=1)

        # Edge lines (individual segments for labeling)
        p.multi_line("xs", "ys", source=self._edge_source, line_color="line_color", line_width=1.5)
        p.multi_line(
            "xs",
            "ys",
            source=self._edge_source,
            view=CDSView(filter=self._bad_filter),
            line_color=INVALID_COLOR,
            line_width=3.5,
        )

        # Fixed vertices (no tool attached - not draggable)
        p.scatter(
            "x",
            "y",
            source=self._fixed_source,
            size=12,
            fill_color="fill_color",
            line_color="line_color",
            line_width=1.5,
        )

        # Draggable vertices (with PointDrawTool)
        self._vertex_renderer = p.scatter(
            "x",
            "y",
            source=self._draggable_source,
            size=12,
            fill_color="fill_color",
            line_color="line_color",
            line_width=1.5,
        )

        # Preview vertices (for showing linked vertex positions during drag)
        # These are rendered on top of draggable vertices with white fill to hide originals
        self._preview_source = ColumnDataSource(data={"x": [], "y": [], "idx": []})
        self._preview_renderer = p.scatter(
            "x",
            "y",
            source=self._preview_source,
            size=16,
            fill_color="white",
            line_color="#ff6600",
            line_width=2,
        )

        # Vertex labels for draggable vertices
        self._vertex_labels = LabelSet(
            x="x",
            y="y",
            text="idx",
            level="glyph",
            x_offset=8,
            y_offset=5,
            source=self._draggable_source,
            text_font_size="8pt",
            text_color="#333",
        )
        p.add_layout(self._vertex_labels)

        # Vertex labels for fixed vertices
        self._fixed_vertex_labels = LabelSet(
            x="x",
            y="y",
            text="idx",
            level="glyph",
            x_offset=8,
            y_offset=5,
            source=self._fixed_source,
            text_font_size="8pt",
            text_color="#666",
        )
        p.add_layout(self._fixed_vertex_labels)

        # Edge labels
        self._edge_labels = LabelSet(
            x="x",
            y="y",
            text="text",
            level="glyph",
            x_offset=0,
            y_offset=-10,
            source=self._edge_label_source,
            text_font_size="7pt",
            text_color="#666",
            text_align="center",
        )
        p.add_layout(self._edge_labels)

        # PointDrawTool only for draggable vertices (add=False prevents creating new points)
        draw_tool = PointDrawTool(renderers=[self._vertex_renderer], add=False)
        p.add_tools(draw_tool)
        p.toolbar.active_tap = draw_tool

        # Guide lines for snap (Shift) and axis constraint (Ctrl)
        from bokeh.models import Span

        self._snap_h_span = Span(
            location=0, dimension="width", line_color="#00aaff", line_dash="dashed", line_width=1, visible=False
        )
        self._snap_v_span = Span(
            location=0, dimension="height", line_color="#00aaff", line_dash="dashed", line_width=1, visible=False
        )
        p.add_layout(self._snap_h_span)
        p.add_layout(self._snap_v_span)

        return p

    def _setup_callbacks(self):
        """Set up callbacks for real-time updates."""
        # Store original vertex positions and Jacobian as instance attributes (not ColumnDataSource)
        # to avoid column length mismatch issues
        self._orig_positions = {"x": [], "y": [], "idx": []}
        self._jacobian_data = {}
        self._group_params = []
        self._group_vertices = []

        # Create a data source for passing Jacobian to JS (single row with JSON data)
        self._jacobian_source = ColumnDataSource(
            data={
                "jacobian": ["{}"],
                "group_params": ["[]"],
                "group_vertices": ["[]"],
                "orig_x": ["[]"],
                "orig_y": ["[]"],
                "values": ["{}"],
            }
        )

        # Create a signal source to communicate drag state to Python
        self._drag_state_source = ColumnDataSource(
            data={
                "in_drag": [False],
                "primary_dragged": [-1],
            }
        )

        # Signal Python to enter drag state at panstart (to skip callback)
        # Initialize primary_dragged to -1 (will be set during first pan event)
        # Store the current positions as the "drag start" positions
        # Also lazily register document-level keydown/keyup listeners for modifier key tracking.
        self._figure.js_on_event(
            "panstart",
            CustomJS(
                args={
                    "drag_state": self._drag_state_source,
                },
                code="""
            drag_state.data = {in_drag: [true], primary_dragged: [-1]};
            drag_state.change.emit();
            if (!window._cf_keys_initialized) {
                window._cf_shift_pressed = false;
                window._cf_ctrl_pressed = false;
                document.addEventListener('keydown', function(e) {
                    window._cf_shift_pressed = e.shiftKey;
                    window._cf_ctrl_pressed = e.ctrlKey;
                });
                document.addEventListener('keyup', function(e) {
                    window._cf_shift_pressed = e.shiftKey;
                    window._cf_ctrl_pressed = e.ctrlKey;
                });
                window._cf_keys_initialized = true;
            }
        """,
            ),
        )

        # Use pan event for live feedback during drag
        # Pass the figure so JS can access renderers
        self._figure.js_on_event(
            "pan",
            CustomJS(
                args={
                    "draggable": self._draggable_source,
                    "fixed": self._fixed_source,
                    "outline": self._outline_source,
                    "edge_source": self._edge_source,
                    "edge_labels": self._edge_label_source,
                    "jacobian_source": self._jacobian_source,
                    "probe_in": self._probe.in_source,
                    "drag_state": self._drag_state_source,
                    "fig": self._figure,
                    "snap_h_span": self._snap_h_span,
                    "snap_v_span": self._snap_v_span,
                },
                code="""
            const dx = draggable.data.x;
            const dy = draggable.data.y;
            const n = dx.length;
            if (n < 1) return;

            // Hide thick curves during drag
            for (let i = 0; i < fig.renderers.length; i++) {
                const r = fig.renderers[i];
                if (r.name === 'thick_curve') r.visible = false;
            }

            // Get Jacobian data
            let jac = {};
            let params = [];
            let vertices = [];
            let orig_x = [];
            let orig_y = [];
            let pvals = {};
            try {
                jac = JSON.parse(jacobian_source.data.jacobian[0]);
                params = JSON.parse(jacobian_source.data.group_params[0]);
                vertices = JSON.parse(jacobian_source.data.group_vertices[0]);
                orig_x = JSON.parse(jacobian_source.data.orig_x[0]);
                orig_y = JSON.parse(jacobian_source.data.orig_y[0]);
                pvals = JSON.parse(jacobian_source.data.values[0]);
            } catch (e) {}

            // Compute displacements from the true pre-drag vertex positions stored in
            // orig_x/orig_y (set by Python at the last figure update, well before any drag).
            // Using panstart positions instead would be wrong because PointDrawTool starts
            // moving the vertex immediately on mousemove, before panstart fires.
            const displacements = {};
            const didx = draggable.data.idx;
            for (let i = 0; i < n; i++) {
                const vi = didx[i];
                if (vi < orig_x.length) {
                    const dvx = dx[i] - orig_x[vi];
                    const dvy = dy[i] - orig_y[vi];
                    if (Math.abs(dvx) > 1e-9 || Math.abs(dvy) > 1e-9) {
                        displacements[vi] = [dvx, dvy];
                    }
                }
            }

            if (Object.keys(displacements).length === 0) return;

            // Identify the primary dragged vertex (the one the user is actually moving).
            // Lock this in on the first pan event so linked vertices — whose draggable.data
            // positions we correct below — are never mistaken for the primary vertex.
            // Must happen BEFORE the solve so we use only the primary's displacement.
            let primary_dragged = -1;
            if (drag_state.data.primary_dragged && drag_state.data.primary_dragged.length > 0) {
                primary_dragged = drag_state.data.primary_dragged[0];
            }
            if (primary_dragged < 0) {
                let max_disp = 0;
                for (const vi in displacements) {
                    const d = displacements[vi];
                    const disp = Math.sqrt(d[0]*d[0] + d[1]*d[1]);
                    if (disp > max_disp) {
                        max_disp = disp;
                        primary_dragged = parseInt(vi);
                    }
                }
                if (primary_dragged >= 0) {
                    drag_state.data = {in_drag: [true], primary_dragged: [primary_dragged]};
                    drag_state.change.emit();
                }
            }

            const dragged_vertices = new Set([primary_dragged]);

            // === MODIFIER KEY FEATURES (Shift = snap, Ctrl = axis constraint) ===
            const snap_active = (window._cf_shift_pressed === true);
            const constrain_active = (window._cf_ctrl_pressed === true);

            // Read cursor position in data-space from the pan event object (cb_obj.x/y).
            // This is independent of draggable.data, so we can freely override the primary
            // vertex position without creating a circular dependency (the old approach of
            // reading dx[i_primary] caused the axis-lock direction to freeze and snap-release
            // to leave the vertex stuck near the snap target).
            const mouse_data_x = (cb_obj.x !== undefined && cb_obj.x !== null) ? cb_obj.x
                : (primary_dragged >= 0 ? orig_x[primary_dragged] : 0);
            const mouse_data_y = (cb_obj.y !== undefined && cb_obj.y !== null) ? cb_obj.y
                : (primary_dragged >= 0 ? orig_y[primary_dragged] : 0);
            let snapped_x = null, snapped_y = null;

            // === SNAP (Shift): snap to x/y coordinates of other vertices ===
            if (snap_active && primary_dragged >= 0) {
                // Zoom-adaptive threshold (3% of visible range)
                const x_span_range = fig.x_range.end - fig.x_range.start;
                const y_span_range = fig.y_range.end - fig.y_range.start;
                const snap_thr_x = x_span_range * 0.03;
                const snap_thr_y = y_span_range * 0.03;
                let best_dx = snap_thr_x, best_dy = snap_thr_y;
                for (let i = 0; i < orig_x.length; i++) {
                    if (i === primary_dragged) continue;
                    const adx = Math.abs(mouse_data_x - orig_x[i]);
                    const ady = Math.abs(mouse_data_y - orig_y[i]);
                    if (adx < best_dx) { best_dx = adx; snapped_x = orig_x[i]; }
                    if (ady < best_dy) { best_dy = ady; snapped_y = orig_y[i]; }
                }
            }

            // Update snap guide spans
            snap_h_span.visible = (snap_active && snapped_y !== null);
            if (snapped_y !== null) snap_h_span.location = snapped_y;
            snap_v_span.visible = (snap_active && snapped_x !== null);
            if (snapped_x !== null) snap_v_span.location = snapped_x;

            // Displacement from pre-drag position to the effective target.
            // Snap overrides x and/or y independently; otherwise the raw mouse position is used.
            let pd_x = (primary_dragged >= 0)
                ? (snapped_x !== null ? snapped_x : mouse_data_x) - orig_x[primary_dragged] : 0;
            let pd_y = (primary_dragged >= 0)
                ? (snapped_y !== null ? snapped_y : mouse_data_y) - orig_y[primary_dragged] : 0;

            // === AXIS CONSTRAINT (Ctrl): lock to dominant axis ===
            // pd_x/pd_y at this point reflect true mouse displacement (from cb_obj.x/y),
            // so the dominant axis correctly switches as the user changes direction.
            if (constrain_active && primary_dragged >= 0) {
                const min_move = 0.003;  // deadzone before locking axis
                if (Math.abs(pd_x) >= Math.abs(pd_y) && Math.abs(pd_x) > min_move) {
                    pd_y = 0;  // x-dominant: lock to horizontal
                    snap_h_span.location = orig_y[primary_dragged];
                    snap_h_span.visible = true;
                } else if (Math.abs(pd_y) > Math.abs(pd_x) && Math.abs(pd_y) > min_move) {
                    pd_x = 0;  // y-dominant: lock to vertical
                    snap_v_span.location = orig_x[primary_dragged];
                    snap_v_span.visible = true;
                }
            }

            // Solve for parameter change dp using ONLY the primary vertex's displacement.
            // Using linked vertices would corrupt the solve: their draggable.data positions
            // are our Jacobian corrections from the previous frame, not new mouse measurements.
            let new_positions = {};
            if (params.length > 0 && primary_dragged >= 0 && (Math.abs(pd_x) > 1e-9 || Math.abs(pd_y) > 1e-9)) {
                const n_params = params.length;

                // Build J rows (x, y) for the primary vertex only
                const Jr0 = new Array(n_params).fill(0);
                const Jr1 = new Array(n_params).fill(0);
                for (let j = 0; j < n_params; j++) {
                    const key = primary_dragged + ',' + params[j];
                    if (key in jac) {
                        Jr0[j] = jac[key][0];
                        Jr1[j] = jac[key][1];
                    }
                }

                // Normal equations: J^T J dp = J^T dv
                const JtJ = [];
                for (let i = 0; i < n_params; i++) {
                    JtJ.push(new Array(n_params).fill(0));
                    for (let j = 0; j < n_params; j++) {
                        JtJ[i][j] = Jr0[i] * Jr0[j] + Jr1[i] * Jr1[j];
                    }
                }
                const Jtdv = new Array(n_params).fill(0);
                for (let i = 0; i < n_params; i++) {
                    Jtdv[i] = Jr0[i] * pd_x + Jr1[i] * pd_y;
                }

                // Gaussian elimination with partial pivoting
                const dp = new Array(n_params).fill(0);
                const aug = JtJ.map((row, i) => [...row, Jtdv[i]]);
                for (let i = 0; i < n_params; i++) {
                    let max_row = i;
                    for (let k = i + 1; k < n_params; k++) {
                        if (Math.abs(aug[k][i]) > Math.abs(aug[max_row][i])) max_row = k;
                    }
                    [aug[i], aug[max_row]] = [aug[max_row], aug[i]];
                    if (Math.abs(aug[i][i]) < 1e-12) continue;
                    for (let k = i + 1; k < n_params; k++) {
                        const factor = aug[k][i] / aug[i][i];
                        for (let j = i; j <= n_params; j++) aug[k][j] -= factor * aug[i][j];
                    }
                }
                for (let i = n_params - 1; i >= 0; i--) {
                    dp[i] = aug[i][n_params];
                    for (let j = i + 1; j < n_params; j++) dp[i] -= aug[i][j] * dp[j];
                    if (Math.abs(aug[i][i]) > 1e-12) dp[i] /= aug[i][i];
                }

                // A parameter stays in 0 to 1, the range of the sliders. The values inside the range at which the
                // prototile is invalid are reached, shown in red and refused at the release.
                for (let j = 0; j < n_params; j++) {
                    const v0 = pvals[params[j]];
                    if (v0 !== undefined) dp[j] = Math.min(1, Math.max(0, v0 + dp[j])) - v0;
                }
                """
                + self._probe.send_js("{idx: params, dp: dp}")
                + """

                // Predict positions for all group vertices from dp.
                // Use orig_x/orig_y as base — the true pre-drag positions from Python.
                for (let vi of vertices) {
                    if (vi < orig_x.length) {
                        let nx = orig_x[vi];
                        let ny = orig_y[vi];
                        for (let j = 0; j < n_params; j++) {
                            const key = vi + ',' + params[j];
                            if (key in jac) {
                                nx += dp[j] * jac[key][0];
                                ny += dp[j] * jac[key][1];
                            }
                        }
                        new_positions[vi] = [nx, ny];
                    }
                }
            }

            // Update vertex markers. Linked vertices always get the Jacobian-predicted position.
            // In constrained mode (snap/axis), the PRIMARY also gets the Jacobian prediction
            // so it visually follows the constraint and Python commits the right position.
            // This is now safe because pd_x/pd_y come from cb_obj.x/y (the raw pan event),
            // not from draggable.data — so overriding the primary creates no circular dependency.
            if (Object.keys(new_positions).length > 0 && primary_dragged >= 0) {
                const override_primary = true;  // the handle sits on the vertex that the parameters allow, not on the cursor
                const new_x = [...draggable.data.x];
                const new_y = [...draggable.data.y];
                for (let i = 0; i < didx.length; i++) {
                    const vi = didx[i];
                    if (vi in new_positions && (!dragged_vertices.has(vi) || override_primary)) {
                        new_x[i] = new_positions[vi][0];
                        new_y[i] = new_positions[vi][1];
                    }
                }
                draggable.data = {
                    x: new_x, y: new_y,
                    idx: draggable.data.idx,
                    fill_color: draggable.data.fill_color,
                    line_color: draggable.data.line_color
                };
            }

            // Build outline using Jacobian-constrained positions for ALL group vertices
            // (including primary). This gives a better preview of the final shape than
            // using the raw mouse position for the primary vertex.
            const fx = fixed.data.x;
            const fy = fixed.data.y;
            const fidx = fixed.data.idx;
            const total = orig_x.length;
            const all_x = new Array(total);
            const all_y = new Array(total);

            const by_idx = {};
            for (let i = 0; i < didx.length; i++) {
                const vi = didx[i];
                if (vi in new_positions) {
                    by_idx[vi] = new_positions[vi];
                } else {
                    by_idx[vi] = [draggable.data.x[i], draggable.data.y[i]];
                }
            }
            for (let i = 0; i < fidx.length; i++) {
                by_idx[fidx[i]] = [fx[i], fy[i]];
            }
            for (let i = 0; i < total; i++) {
                if (!(i in by_idx)) by_idx[i] = [orig_x[i], orig_y[i]];
            }
            for (let i = 0; i < total; i++) {
                all_x[i] = by_idx[i][0];
                all_y[i] = by_idx[i][1];
            }

            outline.data = {xs: [[...all_x, all_x[0]]], ys: [[...all_y, all_y[0]]]};

            const edge_xs = [];
            const edge_ys = [];
            const edge_idx = [];
            const shape_type = edge_source.data.shape_type;
            const line_color = edge_source.data.line_color;
            for (let i = 0; i < total; i++) {
                const next = (i + 1) % total;
                edge_xs.push([all_x[i], all_x[next]]);
                edge_ys.push([all_y[i], all_y[next]]);
                edge_idx.push(i);
            }
            edge_source.data = {xs: edge_xs, ys: edge_ys, edge_idx: edge_idx,
                                 shape_type: shape_type, line_color: line_color};

            const label_x = [];
            const label_y = [];
            const label_text = edge_labels.data.text;
            for (let i = 0; i < total; i++) {
                const next = (i + 1) % total;
                label_x.push((all_x[i] + all_x[next]) / 2);
                label_y.push((all_y[i] + all_y[next]) / 2);
            }
            edge_labels.data = {x: label_x, y: label_y, text: label_text, shape_type: shape_type};
        """,
            ),
        )

        # Signal Python to exit drag state and clear preview when drag ends
        self._figure.js_on_event(
            "panend",
            CustomJS(
                args={
                    "drag_state": self._drag_state_source,
                    "preview": self._preview_source,
                    "snap_h_span": self._snap_h_span,
                    "snap_v_span": self._snap_v_span,
                },
                code=self._probe.cancel_js
                + """
            drag_state.data = {in_drag: [false], primary_dragged: [-1]};
            drag_state.change.emit();
            preview.data = {x: [], y: [], idx: []};
            snap_h_span.visible = false;
            snap_v_span.visible = false;
        """,
            ),
        )

        # Handle drag state changes from JS
        self._drag_state_source.on_change("data", self._on_drag_state_change)
        self._draggable_source.on_change("data", self._on_vertex_change)

    @param.depends("state.draft", "show_curves", "state.active_curve_id", "active_param_group", watch=True)
    def _update_figure(self):
        """Update the figure from the current draft."""
        tiling = self.state.tiling
        info = self.state.type_info
        verts = [tuple(v) for v in tiling.vertices.tolist()]

        # Prevent _on_vertex_change from firing when we update draggable source programmatically
        self._in_drag = True

        # Forcefully update draggable vertices from current tiling state
        groups = parameter_groups(self.state.tiling_type)
        active_vertices = set()
        if groups and self.active_param_group < len(groups):
            active_vertices = groups[self.active_param_group]["vertices"]

        drag_x, drag_y, drag_idx = [], [], []
        fix_x, fix_y, fix_idx = [], [], []

        n = len(verts)
        for i in range(n):
            if i in active_vertices:
                drag_x.append(verts[i][0])
                drag_y.append(verts[i][1])
                drag_idx.append(i)
            else:
                fix_x.append(verts[i][0])
                fix_y.append(verts[i][1])
                fix_idx.append(i)

        # Colors for draggable vertices (all in active group are highlighted)
        drag_fill = ["orange" for _ in drag_idx]
        drag_line = ["#c05000" for _ in drag_idx]

        # Colors for fixed vertices
        fix_fill = ["#ddd"] * len(fix_idx)
        fix_line = ["#999"] * len(fix_idx)

        self._draggable_source.data = {
            "x": drag_x,
            "y": drag_y,
            "idx": drag_idx,
            "fill_color": drag_fill,
            "line_color": drag_line,
        }
        self._fixed_source.data = {
            "x": fix_x,
            "y": fix_y,
            "idx": fix_idx,
            "fill_color": fix_fill,
            "line_color": fix_line,
        }

        n = len(verts)

        # Clear preview source to prevent ghost vertices
        self._preview_source.data = {"x": [], "y": [], "idx": []}

        # Store original positions and Jacobian for drag callback (as instance attributes)
        self._orig_positions = {"x": [v[0] for v in verts], "y": [v[1] for v in verts], "idx": list(range(n))}

        # Store Jacobian data for active group
        self._jacobian_data = {}
        self._group_params = []
        self._group_vertices = []

        # Serialize Jacobian for JS callback
        import json

        jacobian_json = "{}"
        group_params_json = "[]"
        group_vertices_json = "[]"
        orig_x_json = json.dumps([v[0] for v in verts])
        orig_y_json = json.dumps([v[1] for v in verts])

        if groups and self.active_param_group < len(groups):
            active_group = groups[self.active_param_group]
            self._jacobian_data = active_group["jacobian"]
            self._group_params = active_group["params"]
            self._group_vertices = list(active_group["vertices"])

            # Convert jacobian keys to strings for JSON
            jac_dict = {}
            for (v, p), (dx, dy) in active_group["jacobian"].items():
                jac_dict[f"{v},{p}"] = [dx, dy]
            jacobian_json = json.dumps(jac_dict)
            group_params_json = json.dumps(active_group["params"])
            group_vertices_json = json.dumps(list(active_group["vertices"]))

        # Update Jacobian source for JS callback
        self._jacobian_source.data = {
            "jacobian": [jacobian_json],
            "group_params": [group_params_json],
            "group_vertices": [group_vertices_json],
            "orig_x": [orig_x_json],
            "orig_y": [orig_y_json],
            "values": [json.dumps(dict(enumerate(self.state.parameters)))],
        }

        # Update outline
        all_x = [v[0] for v in verts]
        all_y = [v[1] for v in verts]
        self._outline_source.data = {"xs": [[*all_x, all_x[0]]], "ys": [[*all_y, all_y[0]]]}

        # Update edge lines and labels
        edge_info = info.get("edges", [])
        edge_xs, edge_ys, edge_idx, shape_types, line_colors = [], [], [], [], []
        label_x, label_y, label_text = [], [], []
        active_curve_id = self.state.active_curve_id

        for i in range(n):
            next_i = (i + 1) % n
            edge_xs.append([all_x[i], all_x[next_i]])
            edge_ys.append([all_y[i], all_y[next_i]])
            edge_idx.append(i)

            # Get edge shape type and shape_id
            if i < len(edge_info):
                stype = edge_info[i].get("shape_type", "?")
                shape_id = edge_info[i].get("shape_id", "?")
                shape_types.append(stype)
                label_text.append(f"E{i} id={shape_id}:{stype}")
            else:
                shape_types.append("?")
                label_text.append(f"E{i}")

            # Set line color based on active curve
            if i < len(edge_info) and active_curve_id is not None and edge_info[i].get("shape_id") == active_curve_id:
                line_colors.append("orange")
            else:
                line_colors.append("#999")

            # Label at midpoint
            label_x.append((all_x[i] + all_x[next_i]) / 2)
            label_y.append((all_y[i] + all_y[next_i]) / 2)

        self._edge_source.data = {
            "xs": edge_xs,
            "ys": edge_ys,
            "edge_idx": edge_idx,
            "shape_type": shape_types,
            "line_color": line_colors,
        }
        self._edge_label_source.data = {"x": label_x, "y": label_y, "text": label_text, "shape_type": shape_types}
        self._mark_invalid_edges()

        # Calculate bounds only when the reset_ranges flag is set
        if self.state.reset_ranges:
            all_x = [v[0] for v in verts]
            all_y = [v[1] for v in verts]
            x_min, x_max = min(all_x), max(all_x)
            y_min, y_max = min(all_y), max(all_y)
            padding = 0.2  # 20% padding

            # Calculate required ranges with padding
            dx = x_max - x_min
            dy = y_max - y_min
            x_pad = dx * padding
            y_pad = dy * padding

            # Calculate aspect ratio of the figure
            figure_aspect = self._figure.width / self._figure.height

            # Calculate data ranges
            x_range_unadjusted = (x_min - x_pad, x_max + x_pad)
            y_range_unadjusted = (y_min - y_pad, y_max + y_pad)
            dx_total = x_range_unadjusted[1] - x_range_unadjusted[0]
            dy_total = y_range_unadjusted[1] - y_range_unadjusted[0]
            data_aspect = dx_total / dy_total

            # Adjust ranges to match figure aspect ratio
            if data_aspect > figure_aspect:
                # Data is too wide - adjust y range
                target_dy = dx_total / figure_aspect
                dy_diff = target_dy - dy_total
                y_range = (y_range_unadjusted[0] - dy_diff / 2, y_range_unadjusted[1] + dy_diff / 2)
                x_range = x_range_unadjusted
            else:
                # Data is too tall - adjust x range
                target_dx = dy_total * figure_aspect
                dx_diff = target_dx - dx_total
                x_range = (x_range_unadjusted[0] - dx_diff / 2, x_range_unadjusted[1] + dx_diff / 2)
                y_range = y_range_unadjusted

            self._figure.x_range.start, self._figure.x_range.end = x_range
            self._figure.y_range.start, self._figure.y_range.end = y_range
            self.state.reset_ranges = False  # Reset the flag

        if self.show_curves:
            self._update_edge_curves(tiling)
        else:
            # Ensure curves are hidden if show_curves is False
            for renderer in self._curve_renderers:
                renderer.visible = False
        # Show curves if show_curves is True and we have renderers
        if self.show_curves:
            for renderer in self._curve_renderers:
                renderer.visible = True

        # Reset the flag
        self._in_drag = False

    def _update_edge_curves(self, tiling):
        edges = tiling.edge_outlines()

        # Rebuild renderers only when the edge count changes (tiling type switch).
        # For same-type updates (curve drag, undo/redo within type) we reuse the
        # existing renderers and only push new data into their sources, which avoids
        # the Bokeh renderer-list mutation that causes visible blinking.
        if len(self._curve_renderers) != len(edges):
            for renderer in self._curve_renderers:
                self._figure.renderers.remove(renderer)
            self._curve_renderers.clear()
            self._curve_sources.clear()
            for edge_idx in range(len(edges)):
                source = ColumnDataSource(data={"x": [], "y": []})
                self._curve_sources[edge_idx] = source
                renderer = self._figure.line("x", "y", source=source, name="thick_curve")
                self._curve_renderers.append(renderer)

        # Update data and style in place — no renderer add/remove, no blink.
        for edge_idx, points in enumerate(edges):
            self._curve_sources[edge_idx].data = {"x": points[:, 0].tolist(), "y": points[:, 1].tolist()}
        self._style_curves()

    def _bad_shape_ids(self) -> set[int]:
        validity = self.state.validity
        return set(validity.shape_ids) if validity is not None else set()

    def _style_curves(self) -> None:
        """Color of the curve of each edge: red if its shape id makes the prototile invalid, orange if active."""
        shape_ids = self.state.tiling.edge_shape_ids.tolist()
        bad = self._bad_shape_ids()
        active_curve_id = self.state.active_curve_id
        for renderer, shape_id in zip(self._curve_renderers, shape_ids, strict=False):
            is_active = active_curve_id is not None and shape_id == active_curve_id
            is_bad = shape_id in bad
            renderer.glyph.line_color = INVALID_COLOR if is_bad else "#ff6600" if is_active else "#444"
            renderer.glyph.line_width = 3.5 if is_bad else 3 if is_active else 2

    def _mark_invalid_edges(self) -> None:
        """Draw the edges of the shape ids named by ``state.validity`` in red."""
        bad = self._bad_shape_ids()
        shape_ids = self.state.tiling.edge_shape_ids.tolist()
        self._bad_filter.booleans = [shape_id in bad for shape_id in shape_ids]

    @param.depends("state.validity", watch=True)
    def _show_validity(self):
        self._mark_invalid_edges()
        self._style_curves()

    def _limits(self) -> list[tuple[float, float]]:
        return self.state.parameter_limits()

    def _on_probe(self, payload: dict) -> None:
        """Parameter changes of a dragged vertex from the browser: check them without committing."""
        try:
            values = list(self.state.parameters)
            for i, dp in zip(payload["idx"], payload["dp"], strict=True):
                values[i] = max(0.0, min(1.0, values[i] + dp))
        except (KeyError, IndexError, TypeError, ValueError):
            return
        self.state.probe_parameters(values)

    def _on_drag_state_change(self, attr, old, new):
        """Handle drag state changes from JS callback."""
        old_in_drag = old.get("in_drag", [True])[0] if old else True
        in_drag = new["in_drag"][0]
        self._in_drag = in_drag
        # Store primary_dragged for use in _on_vertex_change
        if "primary_dragged" in new and len(new["primary_dragged"]) > 0:
            self._primary_dragged = new["primary_dragged"][0]

        # When drag ends, trigger parameter update
        if old_in_drag and not in_drag:
            # Drag just ended - trigger the parameter update
            self._on_vertex_change("data", {}, dict(self._draggable_source.data))

    def _on_vertex_change(self, attr, old, new):
        """Handle vertex drag changes - fires after drag is complete."""
        # Prevent feedback loop
        if self._in_drag:
            return

        # Use stored Jacobian data instead of recomputing
        if not self._jacobian_data or not self._group_params:
            return

        # Get all vertex displacements using stored original positions
        displacements = {}  # vertex_index → (dvx, dvy)
        drag_idx_list = list(self._draggable_source.data.get("idx", []))

        for drag_i, vi in enumerate(drag_idx_list):
            if drag_i >= len(new["x"]):
                continue
            # Find original position from stored data
            if vi in self._orig_positions["idx"]:
                orig_i = self._orig_positions["idx"].index(vi)
                orig_x = self._orig_positions["x"][orig_i]
                orig_y = self._orig_positions["y"][orig_i]
                dvx = new["x"][drag_i] - orig_x
                dvy = new["y"][drag_i] - orig_y
                if abs(dvx) > 1e-9 or abs(dvy) > 1e-9:
                    displacements[vi] = (dvx, dvy)

        if not displacements:
            return

        # Build active group from stored data
        active_group = {
            "params": self._group_params,
            "vertices": set(self._group_vertices),
            "jacobian": self._jacobian_data,
        }

        # Solve for parameter changes using least squares
        param_deltas = self._solve_group_params(active_group, displacements)

        if not param_deltas:
            return

        # Apply parameter changes
        self._in_drag = True
        try:
            new_params = list(self.state.parameters)
            for pi, dp in param_deltas.items():
                new_params[pi] = max(0, min(1, new_params[pi] + dp))
            # A refused edit redraws the figure from the unchanged draft
            self.state.set_parameters(new_params)
            # Update _orig_positions to the vertex positions of the current draft
            verts = self.state.tiling.vertices
            self._orig_positions = {
                "x": verts[:, 0].tolist(),
                "y": verts[:, 1].tolist(),
                "idx": list(range(len(verts))),
            }
        finally:
            self._in_drag = False

        # Show thick curves again after drag is complete
        # (they were hidden by JS during drag)
        if self.show_curves:
            for renderer in self._curve_renderers:
                renderer.visible = True

    def _solve_group_params(self, group: dict, displacements: dict) -> dict[int, float]:
        """Solve for parameter deltas given vertex displacements using least squares."""
        import numpy as np

        params = group["params"]
        jacobian = group["jacobian"]

        if len(params) == 0:
            return {}

        # Build the system: J @ dp = dv
        vertices = [v for v in group["vertices"] if v in displacements]

        if not vertices:
            return {}

        n_verts = len(vertices)
        n_params = len(params)

        J = np.zeros((2 * n_verts, n_params))
        dv = np.zeros(2 * n_verts)

        for i, v in enumerate(vertices):
            dv[2 * i] = displacements[v][0]  # dvx
            dv[2 * i + 1] = displacements[v][1]  # dvy

            for j, p in enumerate(params):
                if (v, p) in jacobian:
                    dx_dp, dy_dp = jacobian[(v, p)]
                    J[2 * i, j] = dx_dp
                    J[2 * i + 1, j] = dy_dp

        # Solve least squares: dp = (J^T J)^-1 J^T dv
        try:
            dp, _, _, _ = np.linalg.lstsq(J, dv, rcond=None)
            return {p: float(dp[i]) for i, p in enumerate(params)}
        except np.linalg.LinAlgError:
            return {}

    def _compact_desc(self, desc: str) -> str:
        return (
            desc
            .replace("moves ", "")
            .replace(" horizontally", " ↔")
            .replace(" vertically", " ↕")
            .replace(" diagonally", " ↗")
        )

    def __panel__(self):
        return pn.Column(
            pn.pane.Markdown("### Prototile Editor"),
            pn.widgets.Checkbox.from_param(self.param.show_curves, name="Show Curves"),
            pn.pane.Bokeh(self._figure),
            self._build_parameter_panel,
            margin=5,
            align=("center", "start"),
        )

    @param.depends("state.draft", "active_param_group")
    def _build_parameter_panel(self):
        info = self.state.type_info
        params = info.get("parameters", [])

        if not params:
            return pn.pane.Markdown("*No adjustable parameters*")

        groups = parameter_groups(self.state.tiling_type)

        # Get active group's parameters
        active_params = set()
        if groups and self.active_param_group < len(groups):
            active_params = set(groups[self.active_param_group]["params"])

        # Build sliders with group-based styling; the track shows the values at which the prototile is invalid
        limits = self._limits()
        sliders = []
        for p in params:
            idx = p["index"]
            is_in_active_group = idx in active_params

            lo, hi = limits[idx]
            slider = pn.widgets.FloatSlider(
                name=f"P{idx}: {self._compact_desc(p['description'])}",
                start=0,
                end=1,
                step=0.01,
                value=self.state.parameters[idx] if idx < len(self.state.parameters) else p["default"],
                # Visual feedback for active group
                stylesheets=[self._get_slider_style(is_in_active_group, lo, hi)],
            )
            slider.param.watch(lambda e, i=idx, lim=(lo, hi): self._on_slider_probe(e, i, lim), "value")
            slider.param.watch(lambda e, i=idx, lim=(lo, hi): self._on_slider_change(e, i, lim), "value_throttled")
            sliders.append(slider)

        # Group cycle button (only if multiple groups exist)
        group_controls = []
        if len(groups) > 1:
            group_controls = [
                pn.widgets.Button(
                    name=f"Group {self.active_param_group + 1}/{len(groups)} ▶",
                    on_click=self._cycle_group,
                    button_type="primary",
                ),
            ]

        return pn.Column(
            *group_controls,
            *sliders,
        )

    def _get_slider_style(self, is_active: bool, lo: float = 0.0, hi: float = 1.0) -> str:
        """Return CSS stylesheet for slider based on active state and the interval ``lo`` to ``hi`` of valid values."""
        track = (
            ".noUi-base { background: linear-gradient(to right, "
            f"{INVALID_TRACK} 0 {lo * 100:.1f}%, transparent {lo * 100:.1f}% {hi * 100:.1f}%, "
            f"{INVALID_TRACK} {hi * 100:.1f}% 100%); }}\n"
        )
        return track + self._slider_colors(is_active)

    def _slider_colors(self, is_active: bool) -> str:
        if is_active:
            return """
            .bk-slider-title {
                font-weight: bold;
                color: #c05000;
            }
            .noUi-connect {
                background: #ff9900;
            }
            """
        else:
            return """
            .bk-slider-title {
                font-weight: normal;
                color: #666;
            }
            .noUi-connect {
                background: #5b9bd5;
            }
            """

    def _cycle_group(self, event):
        """Cycle to next parameter group."""
        groups = parameter_groups(self.state.tiling_type)
        if len(groups) > 1:
            self.active_param_group = (self.active_param_group + 1) % len(groups)

    def _on_slider_probe(self, event, idx: int, limits: tuple[float, float]) -> None:
        """A slider moves: it stays inside the valid interval; the value is checked without committing."""
        slider = event.obj
        clamped = max(limits[0], min(limits[1], event.new))
        if clamped != event.new:
            slider.value = clamped
            return
        values = list(self.state.parameters)
        if idx < len(values) and values[idx] != clamped:
            values[idx] = clamped
            self.state.probe_parameters(values)

    def _on_slider_change(self, event, idx: int, limits: tuple[float, float] = (0.0, 1.0)):
        value = max(limits[0], min(limits[1], event.new))
        if value != event.new:
            event.obj.value = value
        params = self.state.parameters
        if idx < len(params) and params[idx] != value:
            p_desc = self._compact_desc(self.state.type_info["parameters"][idx]["description"])
            # Rapid changes of the same slider coalesce into one history entry; a refused value redraws the sliders
            original_in_drag = self._in_drag
            self._in_drag = True  # block _on_vertex_change while the figure updates
            try:
                self.state.set_parameter(idx, value, f"Adjust {p_desc}")
            finally:
                self._in_drag = original_in_drag
