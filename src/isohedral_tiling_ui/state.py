"""Reactive adapter between the editor model and the Panel components.

The design lives in an immutable ``Draft`` held by an ``EditorModel``. ``TilingState`` publishes it as the single
parameter ``draft``; the components redraw when it changes. An edit that would give an invalid prototile is refused with
a notification and the previous draft stays.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import panel as pn
import param
from isotile import IsohedralTiling, enforce_symmetry

from isohedral_tiling_ui.editor_model import (
    DEFAULT_PRESET,
    Draft,
    EditorModel,
    ParameterRange,
    add_node,
    edge_shape_ids,
    move_node,
    parameter_ranges,
    placed_curve,
    remove_node,
    shape_of,
    slider_limits,
    type_info,
)
from isohedral_tiling_ui.exports import Appearance
from isohedral_tiling_ui.utils.icons import prototile_svg

logger = logging.getLogger(__name__)

Notify = Callable[[str, str], None]


def panel_notify(level: str, message: str) -> None:
    """Show a Panel notification (level: success, info, warning or error); log it outside a server session."""
    notifications = pn.state.notifications
    if notifications is None:
        logger.log(logging.ERROR if level == "error" else logging.INFO, "%s: %s", level, message)
    else:
        getattr(notifications, level)(message)


HOLD_MS = 1500  # how long the display of an invalid edit stays after the edit was refused


@dataclass(frozen=True)
class Validity:
    """An invalid draft that is shown: the edges and shape ids that the check names, and its message."""

    bad_edges: tuple[int, ...]
    shape_ids: tuple[int, ...]
    message: str

    def describe(self) -> str:
        """``message`` with the curves and edges that it concerns, for a notification."""
        where = ""
        if self.shape_ids:
            curves = ", ".join(f"C{i}" for i in self.shape_ids)
            edges = ", ".join(str(e) for e in self.bad_edges)
            where = f" (curve {curves}, edge{'s' if len(self.bad_edges) != 1 else ''} {edges})"
        return f"{self.message}{where}"


class ViewSettings(param.Parameterized):
    """Settings of the preview and the exports. They are not part of the design and not in the history."""

    grid_size = param.Selector(default="Medium", objects=["Small", "Medium", "Large"], doc="Grid size")
    face_color_mode = param.Selector(default="aspect", objects=["uniform", "aspect", "coloring"], doc="Face color mode")
    face_color = param.Color(default="#1f77b4")
    face_colormap = param.Selector(
        default="Category10", objects=["Category10", "Viridis", "Plasma", "Inferno", "Turbo"]
    )
    edge_color = param.Color(default="#333333")

    def appearance(self) -> Appearance:
        return Appearance(self.face_color_mode, self.face_color, self.face_colormap, self.edge_color)


class TilingState(param.Parameterized):
    """The design, its history and the selection, as seen by the components."""

    draft = param.ClassSelector(class_=Draft, doc="The current design; always a valid prototile")
    # Parts of the draft, set together with it so that a component can depend on the part it draws.
    tiling_type = param.Integer(doc="Type number of the draft")
    parameters = param.Parameter(doc="Tuple of the parameters of the draft")
    edge_curve_modes = param.Dict(doc="Shape id -> linear or spline, of the draft")
    history_rev = param.Integer(default=0, doc="Incremented when the draft, the history or the baseline changes")
    source_label = param.String(default=DEFAULT_PRESET, doc="Preset name or file description; stem of file names")
    active_curve_id = param.Integer(default=None, allow_None=True)
    view = param.ClassSelector(class_=ViewSettings, constant=True)
    live_outline = param.Parameter(
        default=None, doc="Edge index -> (xs, ys): the curve being dragged, placed on its edges (not committed)"
    )
    validity = param.ClassSelector(
        class_=Validity, default=None, allow_None=True, doc="The invalid draft that is shown (probe or refused edit)"
    )

    def __init__(self, notify: Notify | None = None, **params: Any):
        model = EditorModel()
        customizable = type_info(model.draft.type_number)["customizable_shape_ids"]
        super().__init__(
            draft=model.draft,
            tiling_type=model.draft.type_number,
            parameters=model.draft.parameters,
            edge_curve_modes=model.draft.mode_dict,
            active_curve_id=customizable[0] if customizable else None,
            view=ViewSettings(),
            **params,
        )
        self.model = model
        self.notify: Notify = notify or panel_notify
        self.reset_ranges = True  # the prototile editor fits its axes to the next draft, then clears the flag
        self._prototile_svg = self._baseline_icon()
        self._hold_token = 0

    # -- views of the draft ----------------------------------------------------------------------------

    @property
    def tiling(self) -> IsohedralTiling:
        return self.draft.tiling()

    @property
    def type_info(self) -> dict[str, Any]:
        """The metadata of the current type (shared, do not modify)."""
        return type_info(self.draft.type_number)

    @property
    def edge_curves(self) -> dict[int, tuple[tuple[float, float], ...]]:
        return self.draft.curve_dict

    def shape_type(self, shape_id: int | None) -> str:
        """The edge shape of a shape id of the current type; J if the id is None."""
        return "J" if shape_id is None else shape_of(self.draft.type_number, shape_id)

    @property
    def default_name(self) -> str:
        """Stem of file names: the preset name or file description, else IH<n>."""
        return self.source_label or f"IH{self.draft.type_number}"

    # -- history and baseline --------------------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return self.model.can_undo

    @property
    def can_redo(self) -> bool:
        return self.model.can_redo

    @property
    def reset_available(self) -> bool:
        return self.model.can_reset

    @property
    def baseline_label(self) -> str:
        return self.model.baseline_label

    @property
    def baseline_svg(self) -> str:
        """SVG icon of the prototile of the baseline (the target of Reset)."""
        return self._prototile_svg

    def undo(self) -> None:
        self.model.undo()
        self._publish(reset_ranges=True)

    def redo(self) -> None:
        self.model.redo()
        self._publish(reset_ranges=True)

    def reset(self) -> None:
        self._commit(self.model.baseline, f"Reset to {self.baseline_label}")

    # -- loading ---------------------------------------------------------------------------------------

    def load_type(self, number: int) -> None:
        self.model.load_type(number)
        self._publish(reset_ranges=True, new_baseline=True, source_label="")

    def load_preset(self, name: str) -> None:
        self.model.load_preset(name)
        self._publish(reset_ranges=True, new_baseline=True, source_label=name)

    def load_data(self, data: Any, name: str = "") -> bool:
        """Load a design from parsed JSON. Notifies the repairs; on an error notifies it and keeps the state."""
        try:
            notes = self.model.load_data(data, name)
        except ValueError as e:
            self.notify("error", f"Failed to load: {e}")
            return False
        self._publish(reset_ranges=True, new_baseline=True, source_label=name)
        for note in notes:
            self.notify("warning", f"Design repaired: {note}")
        return True

    # -- validity of a draft that is not committed ------------------------------------------------------

    def probe(self, draft: Draft) -> bool:
        """Check a draft without committing it and show the result; True if it is valid."""
        report = self.model.probe(draft)
        self._show_validity(None if report.valid else self._validity(draft, report))
        return report.valid

    def probe_curve(self, shape_id: int, points: Any) -> bool:
        """Probe a curve of a shape id (control points as dragged; the symmetry is enforced)."""
        curve = enforce_symmetry(points, self.shape_type(shape_id))
        valid = self.probe(self.draft.with_curve(shape_id, curve))
        self.live_outline = placed_curve(self.draft, shape_id, curve)
        return valid

    def probe_parameters(self, values: Any) -> bool:
        return self.probe(self.draft.with_parameters(values))

    def parameter_limits(self) -> list[tuple[float, float]]:
        """The interval of each parameter in 0 to 1 in which the draft stays valid (``slider_limits``)."""
        return [slider_limits(self.draft, k) for k in range(len(self.draft.parameters))]

    def parameter_ranges(self, zoom: int = 0) -> tuple[ParameterRange, ...]:
        """Valid interval and slider window of every parameter, see ``editor_model.parameter_ranges``."""
        return parameter_ranges(self.draft, zoom)

    def clear_validity(self, token: int | None = None) -> None:
        """Stop showing an invalid draft (a hold that was superseded by a newer display is not cleared)."""
        if token is None or token == self._hold_token:
            self._show_validity(None)

    def _validity(self, draft: Draft, report: Any) -> Validity:
        return Validity(tuple(report.bad_edges), edge_shape_ids(draft.type_number, report.bad_edges), report.message)

    def _show_validity(self, validity: Validity | None) -> None:
        self._hold_token += 1
        if validity != self.validity:
            self.validity = validity

    def _hold_validity(self, validity: Validity) -> None:
        """Show the invalid draft of a refused edit for ``HOLD_MS``."""
        self._show_validity(validity)
        doc = pn.state.curdoc
        if doc is not None:
            token = self._hold_token
            doc.add_timeout_callback(lambda: self.clear_validity(token), HOLD_MS)

    # -- edits -----------------------------------------------------------------------------------------

    def set_parameter(self, index: int, value: float, label: str) -> bool:
        params = list(self.draft.parameters)
        params[index] = float(value)
        return self._commit(self.draft.with_parameters(params), label, f"param{index}", 1.0)

    def set_parameters(self, values: Any, label: str = "Move prototile vertices") -> bool:
        return self._commit(self.draft.with_parameters(values), label)

    def set_curve(
        self, shape_id: int, curve: Any, label: str, key: str | None = None, window: float | None = None
    ) -> bool:
        """Set the control polygon of a shape id; the symmetry of its edge shape is enforced."""
        curve = enforce_symmetry(curve, self.shape_type(shape_id))
        return self._commit(self.draft.with_curve(shape_id, curve), label, key, window)

    def move_node(
        self, shape_id: int, i: int, x: float, y: float, label: str, key: str | None = None, window: float | None = None
    ) -> bool:
        curve = move_node(self.draft.curve(shape_id), self.shape_type(shape_id), i, x, y)
        return self._commit(self.draft.with_curve(shape_id, curve), label, key, window)

    def add_node(self, shape_id: int, x: float, y: float) -> bool:
        curve = add_node(self.draft.curve(shape_id), self.shape_type(shape_id), x, y)
        return self._commit(self.draft.with_curve(shape_id, curve), f"Add point to curve {shape_id}")

    def remove_node(self, shape_id: int, i: int) -> bool:
        curve = remove_node(self.draft.curve(shape_id), self.shape_type(shape_id), i)
        return self._commit(self.draft.with_curve(shape_id, curve), f"Remove point from curve {shape_id}")

    def set_mode(self, shape_id: int, mode: str) -> bool:
        return self._commit(self.draft.with_mode(shape_id, mode), f"Curve {shape_id}: {mode}")

    def copy_curve(self, source_id: int, target_id: int) -> bool:
        return self.set_curve(target_id, self.draft.curve(source_id), f"Copy curve {source_id} → {target_id}")

    # -- internals -------------------------------------------------------------------------------------

    def _commit(self, draft: Draft, label: str, key: str | None = None, window: float | None = None) -> bool:
        result = self.model.commit(label, draft, key, window)
        if result.committed:
            if self.model.draft != self.draft:
                self._publish()
            return True
        validity = self._validity(draft, result.report)
        self.notify("error", f"Not applied ({label}): {validity.describe()}")
        # The views may show the refused input; redraw them from the current draft
        self.param.trigger("draft", "parameters", "edge_curve_modes")
        self.live_outline = None
        self._hold_validity(validity)
        return False

    def _baseline_icon(self) -> str:
        draft = self.model.baseline
        return prototile_svg(
            draft.tiling().prototile(normalized=True), type_info(draft.type_number)["num_vertices"], 60
        )

    def _publish(self, reset_ranges: bool = False, new_baseline: bool = False, source_label: str | None = None) -> None:
        draft = self.model.draft
        if reset_ranges:
            self.reset_ranges = True
        if new_baseline:
            self._prototile_svg = self._baseline_icon()
        customizable = type_info(draft.type_number)["customizable_shape_ids"]
        active = self.active_curve_id
        if new_baseline or draft.type_number != self.draft.type_number or active not in customizable:
            active = customizable[0] if customizable else None
        same = draft is self.draft  # param fires for any other object, equal or not
        updates: dict[str, Any] = {
            "draft": draft,
            "tiling_type": draft.type_number,
            "parameters": draft.parameters,
            "edge_curve_modes": draft.mode_dict,
            "active_curve_id": active,
            "history_rev": self.history_rev + 1,
        }
        if source_label is not None:
            updates["source_label"] = source_label
        self._show_validity(None)
        updates["live_outline"] = None
        self.param.update(**updates)
        if same:
            self.param.trigger("draft", "parameters", "edge_curve_modes")
