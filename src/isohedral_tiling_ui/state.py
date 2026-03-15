"""Reactive state container for the tiling application."""

from contextlib import contextmanager
from datetime import datetime, timedelta

import panel as pn
import param

from carto_flow.symbol_cartogram.tiling import IsohedralTiling


class TilingState(param.Parameterized):
    """Reactive state container for the tiling application."""

    _batching = param.Boolean(default=False, precedence=-1)
    _pending_update = param.Boolean(default=False, precedence=-1)

    @contextmanager
    def batch_update(self):
        """Context manager to batch multiple parameter changes.

        Suspend automatic tiling updates during batch operations and
        trigger a single update when the batch completes.
        """
        was_batching = self._batching
        self._batching = True
        try:
            yield
        finally:
            self._batching = was_batching
            if not was_batching:
                # Trigger single update after batch completes
                self._update_tiling()

    def _schedule_async_update(self):
        """Schedule an async update with debouncing.

        Multiple rapid calls are squashed into a single update.
        Uses Panel's scheduling to work in both sync and async contexts.
        """
        if self._pending_update:
            return  # Already have an update scheduled

        self._pending_update = True

        def _do_update():
            self._pending_update = False
            self._do_async_update()

        # Schedule update 50ms from now - debounces rapid changes
        at_time = datetime.now() + timedelta(milliseconds=50)
        pn.state.schedule_task("tiling_update", _do_update, at=at_time)

    tiling_type = param.Integer(default=10, doc="Current tiling type (IH number)")
    parameters = param.List(default=[], doc="Current parameter values")
    parameters_version = param.Integer(default=0, doc="Incremented on reset/load")
    current_preset = param.String(default="regular_hexagon", doc="Current preset name if loaded from gallery")
    _reset_ranges = param.Boolean(default=False, precedence=-1, doc="Internal flag to indicate ranges should be reset")
    edge_curves = param.Dict(default={}, doc="Shape ID -> control points")
    edge_curve_modes = param.Dict(default={}, doc="Shape ID -> linear/spline")
    active_curve_id = param.Integer(default=None, allow_None=True)
    active_param_idx = param.Integer(default=0)

    # Grid size: small=50, medium=100, large=200 tiles
    grid_size = param.Selector(default="Medium", objects=["Small", "Medium", "Large"], doc="Grid size")

    # Color settings
    face_color_mode = param.Selector(default="aspect", objects=["uniform", "aspect", "index"], doc="Face color mode")
    face_color = param.Color(default="#1f77b4")  # , doc="Uniform face color")
    face_colormap = param.Selector(
        default="Category10", objects=["Category10", "Viridis", "Plasma", "Inferno", "Turbo"]
    )
    edge_color = param.Color(default="#333333")  # , doc="Edge color")

    # Computed properties
    _tiling = param.ClassSelector(class_=IsohedralTiling, precedence=-1)
    _type_info = param.Dict(precedence=-1)

    # Initial state for reset
    _initial_parameters = param.List(default=[], precedence=-1, doc="Parameters at selection/load time")
    _initial_edge_curves = param.Dict(default={}, precedence=-1, doc="Edge curves at selection/load time")
    _initial_preset_name = param.String(default="", precedence=-1, doc="Initial preset name for display")
    _initial_prototile_svg = param.String(default="", precedence=-1, doc="SVG of initial prototile")

    # History for undo/redo
    _history = param.List(default=[], precedence=-1)
    _history_idx = param.Integer(default=-1, precedence=-1)

    # Reset button state
    reset_available = param.Boolean(
        default=False, precedence=-1, doc="Whether reset is available (parameters are not initial)"
    )

    def __init__(self, **params):
        super().__init__(**params)
        # Coalescing state for push_history (plain instance attrs, not reactive)
        self._last_coalesce_key = None
        self._last_coalesce_time = None
        # Initialize tiling with a preset - watcher doesn't fire on init
        # Use regular_hexagon preset like the Streamlit app
        tiling = IsohedralTiling.from_preset("regular_hexagon")
        self.tiling_type = tiling._type
        self.parameters = list(tiling._ih.parameters)
        self.edge_curves = dict(tiling._edge_curves) if tiling._edge_curves else {}
        self.edge_curve_modes = dict(tiling._edge_curve_modes)
        self._tiling = tiling
        self._type_info = tiling.type_info()
        # Set active curve to first customizable edge
        customizable = self._type_info.get("customizable_shape_ids", [])
        if customizable:
            self.active_curve_id = customizable[0]
        # Set flag to reset ranges on first draw
        self._reset_ranges = True
        # Save initial state
        self._initial_parameters = list(tiling._ih.parameters)
        self._initial_edge_curves = dict(tiling._edge_curves) if tiling._edge_curves else {}
        self._initial_preset_name = "regular_hexagon"
        self._initial_prototile_svg = self._generate_prototile_svg(tiling)
        # Push initial state to history
        self.push_history("Initial state")
        # Initial reset state
        self.reset_available = False

    @param.depends("tiling_type", "parameters", watch=True)
    def _update_tiling(self):
        """Rebuild tiling when type or parameters change.

        Note: edge_curves changes don't require rebuilding the tiling object,
        they only affect edge shapes which are handled separately.
        """
        if self._batching:
            return  # Skip during batch operations

        # Check if tiling type is changing
        type_changed = hasattr(self, "_tiling") and self.tiling_type != self._tiling._type
        if type_changed:
            self._reset_ranges = True
        # Get expected parameter count for current type
        info = IsohedralTiling.type_info(self.tiling_type)
        expected_len = len(info["parameters"])

        # If parameters length doesn't match, use defaults
        params = [p["default"] for p in info["parameters"]] if len(self.parameters) != expected_len else self.parameters

        self._tiling = IsohedralTiling(tiling_type=self.tiling_type, parameters=params, edge_curves=self.edge_curves)
        new_type_info = self._tiling.type_info()

        # Set active_curve_id BEFORE _type_info so that reactive components
        # (curve editor selector row) always see a consistent pair.
        # On a type change always reset to the first customizable curve so the
        # editor never shows a stale / I-type edge from the previous tile.
        customizable = new_type_info.get("customizable_shape_ids", [])
        if type_changed or self.active_curve_id not in customizable:
            self.active_curve_id = customizable[0] if customizable else None

        self._type_info = new_type_info

        # Update reset button state
        current_snapshot = self._snapshot()
        initial_snapshot = {
            "tiling_type": self.tiling_type,
            "parameters": list(self._initial_parameters)
            if self._initial_parameters
            else [p["default"] for p in info["parameters"]],
            "edge_curves": dict(self._initial_edge_curves) if self._initial_edge_curves else {},
            "edge_curve_modes": dict(self.edge_curve_modes),
            "active_curve_id": self.active_curve_id,
        }
        self.reset_available = not self._snapshot_equals(current_snapshot, initial_snapshot)

    @param.depends("edge_curves", watch=True)
    def _on_edge_curves_change(self):
        """Handle edge curves change with async debounced update.

        This updates the tiling preview asynchronously while keeping
        the UI responsive. Multiple rapid changes are squashed into one.
        """
        if self._batching:
            return
        # Update the tiling's edge curves without full rebuild
        if self._tiling is not None:
            self._tiling._edge_curves = dict(self.edge_curves)
        # Update reset button state
        current_snapshot = self._snapshot()
        initial_snapshot = {
            "tiling_type": self.tiling_type,
            "parameters": list(self._initial_parameters)
            if self._initial_parameters
            else [p["default"] for p in IsohedralTiling.type_info(self.tiling_type)["parameters"]],
            "edge_curves": dict(self._initial_edge_curves) if self._initial_edge_curves else {},
            "edge_curve_modes": dict(self.edge_curve_modes),
            "active_curve_id": self.active_curve_id,
        }
        self.reset_available = not self._snapshot_equals(current_snapshot, initial_snapshot)

    @param.depends("edge_curve_modes", watch=True)
    def _on_edge_curve_modes_change(self):
        """Handle edge curve modes change."""
        if self._batching:
            return
        # Update the tiling's edge curve modes without full rebuild
        if self._tiling is not None:
            self._tiling._edge_curve_modes = dict(self.edge_curve_modes)

    def _do_async_update(self):
        """Perform the actual async update and notify watchers."""
        # Trigger param event to notify watchers (like preview strip)
        self.param.trigger("_tiling")

    def reset_parameters(self):
        """Reset to initial parameters and push the resulting state to history."""
        # Check if already at initial state
        current_snapshot = self._snapshot()
        initial_snapshot = {
            "tiling_type": self.tiling_type,
            "parameters": list(self._initial_parameters)
            if self._initial_parameters
            else [p["default"] for p in IsohedralTiling.type_info(self.tiling_type)["parameters"]],
            "edge_curves": dict(self._initial_edge_curves) if self._initial_edge_curves else {},
            "edge_curve_modes": dict(self.edge_curve_modes),
            "active_curve_id": self.active_curve_id,
        }

        if self._snapshot_equals(current_snapshot, initial_snapshot):
            print("DEBUG: History reset → already at initial state, skipping")
            return

        with self.batch_update():
            if self._initial_parameters:
                self.parameters = list(self._initial_parameters)
                self.edge_curves = dict(self._initial_edge_curves)
            else:
                info = IsohedralTiling.type_info(self.tiling_type)
                self.parameters = [p["default"] for p in info["parameters"]]
        # batch_update.__exit__ calls _update_tiling() once with all values set
        self.parameters_version += 1
        # Push the reset state to history so it is undoable
        reset_label = (
            self._initial_preset_name.replace("_", " ").title()
            if self._initial_preset_name
            else f"IH{self.tiling_type}"
        )
        self.push_history(f"Reset to {reset_label}", force_new=True)
        # Update reset availability
        self.reset_available = False

    def set_initial_state(self, tiling):
        """Set initial state from a tiling instance (for type selection)."""
        self._initial_parameters = list(tiling._ih.parameters)
        self._initial_edge_curves = dict(tiling._edge_curves) if tiling._edge_curves else {}
        self._initial_preset_name = ""
        self._initial_prototile_svg = self._generate_prototile_svg(tiling)
        # Reset button should be disabled when at initial state
        self.reset_available = False

    def _generate_prototile_svg(self, tiling) -> str:
        """Generate SVG of prototile for reference image."""
        verts = list(tiling._unit_tile.exterior.coords[:-1])
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]

        span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
        size = 60
        pad = 2
        scale = (size - 4) / span

        pts = " ".join(f"{pad + (x - min(xs)) * scale:.1f},{pad + (max(ys) - y) * scale:.1f}" for x, y in verts)

        num_vertices = tiling.type_info().get("num_vertices")
        colors = {3: "#f5b7b1", 4: "#aed6f1", 5: "#f9e79f", 6: "#abebc6"}
        fill_color = colors.get(num_vertices, "#d5d8dc")

        return (
            f'<svg width="{size}" height="{size}" xmlns="http://www.w3.org/2000/svg">'
            f'<polygon points="{pts}" fill="{fill_color}" '
            f'stroke="#1971c2" stroke-width="1"/>'
            f"</svg>"
        )

    def load_preset(self, preset_name: str):
        """Load a preset tiling configuration."""
        tiling = IsohedralTiling.from_preset(preset_name)

        with self.batch_update():
            self.tiling_type = tiling._type
            self.parameters = list(tiling._ih.parameters)
            self.edge_curves = dict(tiling._edge_curves) if tiling._edge_curves else {}
            self.edge_curve_modes = dict(tiling._edge_curve_modes)
            self.current_preset = preset_name
            # Reset active_curve_id to first customizable curve
            info = tiling.type_info()
            customizable = info.get("customizable_shape_ids", [])
            if customizable:
                self.active_curve_id = customizable[0]
            self._reset_ranges = True  # Set flag inside batch update
            self.parameters_version += 1
            # Save initial state for this preset
            self._initial_parameters = list(tiling._ih.parameters)
            self._initial_edge_curves = dict(tiling._edge_curves) if tiling._edge_curves else {}
            self._initial_preset_name = preset_name
            self._initial_prototile_svg = self._generate_prototile_svg(tiling)
            # Reset button should be disabled when at initial state
            self.reset_available = False
        # Clear history and make new preset the first (and only) entry
        preset_label = preset_name.replace("_", " ").title()
        initial_snapshot = self._snapshot(f"Load preset '{preset_label}'")
        self._history = [initial_snapshot]
        self._history_idx = 0
        self._last_coalesce_key = None
        self._last_coalesce_time = None
        print(f"DEBUG: History cleared and load_preset:{preset_name} → index 0 (length: 1)")

    def _snapshot(self, description: str = "") -> dict:
        """Create a state snapshot for undo/redo.

        Args:
            description: Human-readable label for what action produced this state,
                         shown in the undo/redo toolbar buttons.
        """
        return {
            "tiling_type": self.tiling_type,
            "parameters": list(self.parameters),
            "edge_curves": {k: list(v) for k, v in self.edge_curves.items()},
            "edge_curve_modes": dict(self.edge_curve_modes),
            "active_curve_id": self.active_curve_id,
            "description": description,
        }

    def push_history(
        self,
        description: str = "",
        coalesce_key: str | None = None,
        coalesce_window_ms: int = 1500,
        force_new: bool = False,
    ):
        """Save current state to history.

        Args:
            description: Human-readable label shown in the undo/redo toolbar.
            coalesce_key: If set and the previous push used the same key within
                          ``coalesce_window_ms`` ms, the last entry is replaced
                          in-place rather than appending a new one (idle-window
                          coalescing).
            coalesce_window_ms: Maximum gap between two pushes for them to
                                coalesce. The window is measured between any two
                                consecutive pushes, not from the first one.
            force_new: If True, always append a new entry, bypassing both the
                       duplicate check and coalescing.
        """
        snapshot = self._snapshot(description)

        # 1. Deduplication guard (preserves fix for double-fire bug)
        if (
            not force_new
            and self._history
            and self._history_idx >= 0
            and self._snapshot_equals(snapshot, self._history[self._history_idx])
        ):
            print(f"DEBUG: History '{description}' → skipping duplicate")
            return

        now = datetime.now()

        # 2. Coalescing: replace last entry if same key within the idle window
        should_coalesce = (
            not force_new
            and coalesce_key is not None
            and self._last_coalesce_key == coalesce_key
            and self._last_coalesce_time is not None
            and (now - self._last_coalesce_time).total_seconds() * 1000 < coalesce_window_ms
            and self._history_idx >= 0
        )

        if should_coalesce:
            self._history[self._history_idx] = snapshot
            print(f"DEBUG: History '{description}' → coalesced at index {self._history_idx}")
        else:
            # 3. Normal append
            self._history = self._history[: self._history_idx + 1]
            self._history.append(snapshot)
            self._history_idx += 1
            print(f"DEBUG: History '{description}' → added at index {self._history_idx} (length: {len(self._history)})")

        self._last_coalesce_time = now
        self._last_coalesce_key = coalesce_key

    def _snapshot_equals(self, state1, state2):
        """Check if two state snapshots are equals for history purposes."""
        # Compare important fields with tolerance
        if state1["tiling_type"] != state2["tiling_type"]:
            return False
        if len(state1["parameters"]) != len(state2["parameters"]):
            return False
        for p1, p2 in zip(state1["parameters"], state2["parameters"], strict=False):
            if abs(p1 - p2) > 1e-9:
                return False
        # Other fields can be compared directly
        return (
            state1["edge_curves"] == state2["edge_curves"]
            and state1["edge_curve_modes"] == state2["edge_curve_modes"]
            and state1["active_curve_id"] == state2["active_curve_id"]
        )

    def undo(self):
        if self._history_idx > 0:
            prev_idx = self._history_idx
            self._history_idx -= 1
            state = self._history[self._history_idx]
            print(f"DEBUG: History undo → from index {prev_idx} to {self._history_idx} (length: {len(self._history)})")
            print(f"DEBUG: Restoring parameters: {state['parameters']}")
            self._restore(state)

    def redo(self):
        if self._history_idx < len(self._history) - 1:
            prev_idx = self._history_idx
            self._history_idx += 1
            state = self._history[self._history_idx]
            print(f"DEBUG: History redo → from index {prev_idx} to {self._history_idx} (length: {len(self._history)})")
            print(f"DEBUG: Restoring parameters: {state['parameters']}")
            self._restore(state)

    def _restore(self, state: dict):
        """Restore state from snapshot."""
        with self.batch_update():
            self.tiling_type = state["tiling_type"]
            self.parameters = state["parameters"]
            self.edge_curves = state["edge_curves"]
            self.edge_curve_modes = state["edge_curve_modes"]
            self.active_curve_id = state["active_curve_id"]
            self._reset_ranges = True
        # batch_update.__exit__ calls _update_tiling() exactly once
