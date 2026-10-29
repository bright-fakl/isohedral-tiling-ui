"""State layer of the editor: no Panel, no Bokeh, only isotile and numpy.

``Draft``
    The editable content: type number, parameters, edge curves (the full control polygon per shape id, both end points
    included, as the curve editor draws them) and modes. Frozen and hashable, so it is the key of the caches and the
    unit of the history. A draft may be invalid; an ``isotile.IsohedralTiling`` cannot be.
``Draft.report()``
    ``isotile.check_prototile`` of the draft, cached by draft.
``Draft.tiling()``
    The ``IsohedralTiling`` of the draft, cached by draft; raises ``ValueError`` for an invalid draft.
``EditorModel``
    The current draft, the baseline that Reset returns to, and the history. An edit that yields an invalid draft is not
    committed: ``commit`` returns ``Result(committed=False, report=...)`` and the model keeps the previous draft.

The functions ``move_node``, ``add_node`` and ``remove_node`` are the node rules of the curve editor on top of
``isotile.enforce_symmetry``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import cache, lru_cache
from typing import Any

import numpy as np
from isotile import (
    InvalidPrototileError,
    IsohedralTiling,
    PrototileReport,
    check_prototile,
    enforce_symmetry,
    get_type,
)

from isohedral_tiling_ui.utils.curve_presets import get_curve_preset, list_curve_presets

Point = tuple[float, float]
Curve = tuple[Point, ...]

STRAIGHT: Curve = ((0.0, 0.0), (1.0, 0.0))
DEFAULT_PRESET = "regular_hexagon"


def _curve(points: Any) -> Curve:
    return tuple((float(x), float(y)) for x, y in points)


@dataclass(frozen=True)
class Draft:
    """An editable design: type, parameters, curves and modes. May be invalid, see ``report``.

    ``curves`` and ``modes`` are tuples of ``(shape id, value)`` sorted by shape id. A curve that is the straight
    segment is not stored. A mode may exist for a shape id without a curve; it applies when a curve is added.
    """

    type_number: int
    parameters: tuple[float, ...]
    curves: tuple[tuple[int, Curve], ...] = ()
    modes: tuple[tuple[int, str], ...] = ()

    @classmethod
    def of(
        cls,
        type_number: int,
        parameters: Any = None,
        curves: Mapping[int, Any] | None = None,
        modes: Mapping[int, str] | None = None,
    ) -> Draft:
        """The draft of a type at ``parameters`` (the type's defaults when None) with the given curves and modes."""
        params = get_type(type_number).default_params if parameters is None else parameters
        kept = {int(k): _curve(v) for k, v in (curves or {}).items()}
        return cls(
            int(type_number),
            tuple(float(v) for v in params),
            tuple(sorted((k, v) for k, v in kept.items() if v != STRAIGHT)),
            tuple(sorted((int(k), str(v)) for k, v in (modes or {}).items())),
        )

    @classmethod
    def from_tiling(cls, tiling: IsohedralTiling) -> Draft:
        return cls.of(tiling.type.number, tiling.parameters, tiling.edge_curves, tiling.edge_curve_modes)

    @property
    def curve_dict(self) -> dict[int, Curve]:
        return dict(self.curves)

    @property
    def mode_dict(self) -> dict[int, str]:
        return dict(self.modes)

    def curve(self, shape_id: int) -> Curve:
        """The control polygon of a shape id; the straight segment if it has no curve."""
        return self.curve_dict.get(shape_id, STRAIGHT)

    def mode(self, shape_id: int) -> str:
        return self.mode_dict.get(shape_id, "spline")

    def with_curve(self, shape_id: int, curve: Any) -> Draft:
        curves = self.curve_dict
        curves[shape_id] = _curve(curve)
        return replace(self, curves=tuple(sorted((k, v) for k, v in curves.items() if v != STRAIGHT)))

    def with_mode(self, shape_id: int, mode: str) -> Draft:
        modes = self.mode_dict
        modes[shape_id] = mode
        return replace(self, modes=tuple(sorted(modes.items())))

    def with_parameters(self, parameters: Any) -> Draft:
        return replace(self, parameters=tuple(float(v) for v in parameters))

    def report(self) -> PrototileReport:
        """``check_prototile`` of the draft; a malformed draft is reported as invalid with the message of the error."""
        return _report(self)

    def tiling(self) -> IsohedralTiling:
        """The tiling of the draft; raises ``ValueError`` if the draft is invalid."""
        return _tiling(self)


def _arguments(d: Draft) -> dict[str, Any]:
    curves = d.curve_dict
    modes = {k: v for k, v in d.modes if k in curves}
    return {"edge_curves": curves or None, "edge_curve_modes": modes or None}


@lru_cache(maxsize=256)
def _report(d: Draft) -> PrototileReport:
    try:
        return check_prototile(d.type_number, d.parameters, **_arguments(d))
    except ValueError as e:
        return PrototileReport(False, (), str(e))


@lru_cache(maxsize=64)
def _tiling(d: Draft) -> IsohedralTiling:
    return IsohedralTiling(d.type_number, d.parameters, **_arguments(d))


# ----------------------------------------------------------------------------------------------------------------------
# Reading designs


def load_design(data: Any) -> tuple[Draft, list[str]]:
    """Read a saved design (isotile format 1.x, or a file of the earlier versions of this GUI).

    Returns the draft, not yet checked for validity, and the notes on what was repaired:

    - a curve on a straight (I) edge is dropped,
    - a parameter list of the wrong length is replaced by the type's defaults,
    - a mode for a shape id without a curve is kept for when a curve is added.

    Curves get the symmetry of their edge shape. Raises ``ValueError`` for data that is not a design (not a mapping, no
    or unknown ``tiling_type``, version other than 1.x, malformed curves).
    """
    if not isinstance(data, Mapping):
        raise ValueError("a design is a JSON object")  # noqa: TRY004 (callers catch ValueError)
    version = str(data.get("version", "1.0"))
    if version.split(".")[0] != "1":
        raise ValueError(f"unsupported design version {version!r}")
    if "tiling_type" not in data:
        raise ValueError("the design has no 'tiling_type'")
    try:
        t = get_type(int(data["tiling_type"]))
        curves = {int(k): v for k, v in (data.get("edge_curves") or {}).items()}
        modes = {int(k): str(v) for k, v in (data.get("edge_curve_modes") or {}).items()}
    except (TypeError, ValueError, AttributeError) as e:
        raise ValueError(f"unreadable design: {e}") from e

    notes: list[str] = []
    straight_ids = sorted(k for k in curves if 0 <= k < len(t.shape_types) and t.shape_types[k] == "I")
    if straight_ids:
        notes.append(
            f"dropped the curve on the straight edge shape id{'s' if len(straight_ids) > 1 else ''} "
            f"{', '.join(map(str, straight_ids))}"
        )
    curves = {k: v for k, v in curves.items() if k not in straight_ids}
    for k, v in curves.items():
        if not 0 <= k < len(t.shape_types):
            raise ValueError(f"IH{t.number} has no edge shape id {k}")
        curves[k] = enforce_symmetry(v, t.shape_types[k])

    parameters = data.get("parameters")
    if parameters is not None and not isinstance(parameters, list | tuple):
        raise ValueError("'parameters' must be a list of numbers")
    if parameters is not None and len(parameters) != t.n_params:
        if len(parameters) > 0:
            notes.append(
                f"used the default parameters (IH{t.number} takes {t.n_params}, the file has {len(parameters)})"
            )
        parameters = None
    curved = {i for i, v in curves.items() if _curve(v) != STRAIGHT}
    unused = sorted(k for k, mode in modes.items() if k not in curved and mode != "spline")
    if unused:
        notes.append(
            f"the mode of shape id{'s' if len(unused) > 1 else ''} {', '.join(map(str, unused))} has no curve; "
            "it applies when a curve is added"
        )
    return Draft.of(t.number, parameters, curves, modes), notes


# ----------------------------------------------------------------------------------------------------------------------
# Type data


@cache
def type_info(type_number: int) -> dict[str, Any]:
    """``get_type(n).info()``, cached per type. The result is shared: do not modify it."""
    return get_type(type_number).info()


def shape_of(type_number: int, shape_id: int) -> str:
    """The edge shape (J, U, S or I) of a shape id."""
    return get_type(type_number).shape_types[shape_id]


@cache
def parameter_info(type_number: int) -> tuple[dict[str, Any], ...]:
    """The ``parameters`` entries of ``info()`` with the exact derivative of each affected vertex."""
    t = get_type(type_number)
    entries = []
    for p in type_info(type_number)["parameters"]:
        affected = [
            {"vertex": a["vertex"], "dx_dp": float(dx), "dy_dp": float(dy)}
            for a in p["affected_vertices"]
            for dx, dy in [t.vertex_coeffs[a["vertex"], :, p["index"]]]
        ]
        entries.append({**p, "affected_vertices": affected})
    return tuple(entries)


@cache
def parameter_groups(type_number: int) -> tuple[dict[str, Any], ...]:
    """Groups of parameters that the prototile editor moves together by dragging one vertex.

    Two parameters are in the same group if they share a vertex, are orthogonal at every shared vertex, and each new
    parameter is orthogonal to all members. A group has ``params`` (sorted indices), ``vertices`` (set) and
    ``jacobian`` (``(vertex, parameter) -> (dx/dp, dy/dp)``).
    """
    params = parameter_info(type_number)
    if not params:
        return ()

    vertex_params: dict[int, list[tuple[int, float, float]]] = {}
    param_vertices: dict[int, set[int]] = {}
    for p in params:
        param_vertices[p["index"]] = {a["vertex"] for a in p["affected_vertices"]}
        for a in p["affected_vertices"]:
            vertex_params.setdefault(a["vertex"], []).append((p["index"], a["dx_dp"], a["dy_dp"]))

    def orthogonal(dx1: float, dy1: float, dx2: float, dy2: float, threshold: float = 0.3) -> bool:
        mag1, mag2 = (dx1**2 + dy1**2) ** 0.5, (dx2**2 + dy2**2) ** 0.5
        if mag1 < 1e-9 or mag2 < 1e-9:
            return True
        return bool(abs(dx1 * dx2 + dy1 * dy2) / (mag1 * mag2) < threshold)

    def can_merge(index: int, members: set[int]) -> bool:
        for other in members:
            for v in param_vertices[index] & param_vertices[other]:
                d1 = next((dx, dy) for pi, dx, dy in vertex_params[v] if pi == index)
                d2 = next((dx, dy) for pi, dx, dy in vertex_params[v] if pi == other)
                if not orthogonal(*d1, *d2):
                    return False
        return True

    groups: list[dict[str, Any]] = []
    visited: set[int] = set()
    for p in params:
        if p["index"] in visited:
            continue
        members = {p["index"]}
        queue = [p["index"]]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for v in param_vertices.get(current, set()):
                for other, _, _ in vertex_params.get(v, []):
                    if other not in visited and other not in members and can_merge(other, members):
                        members.add(other)
                        queue.append(other)
        vertices: set[int] = set()
        jacobian: dict[tuple[int, int], tuple[float, float]] = {}
        for index in members:
            for a in params[index]["affected_vertices"]:
                vertices.add(a["vertex"])
                jacobian[(a["vertex"], index)] = (a["dx_dp"], a["dy_dp"])
        groups.append({"params": sorted(members), "vertices": vertices, "jacobian": jacobian})
    return tuple(groups)


# ----------------------------------------------------------------------------------------------------------------------
# Curve edits: the node rules of the curve editor


def move_node(curve: Curve, shape: str, i: int, x: float, y: float) -> Curve:
    """Move node ``i`` to ``(x, y)``; for S and U the partner node follows."""
    c = [list(p) for p in curve]
    c[i] = [x, y]
    return _curve(enforce_symmetry(c, shape))


def add_node(curve: Curve, shape: str, x: float, y: float) -> Curve:
    """Insert a node at ``(x, y)``.

    J: after the last node, the end point excluded, whose x is at most ``x``. S and U: in the first half, after the
    first-half nodes whose x is at most ``x``; the partner node is inserted at the mirrored index, so the number of nodes
    grows by two.
    """
    m = len(curve)
    c = [list(p) for p in curve]
    if shape in ("S", "U"):
        h = m // 2  # nodes 0 .. h - 1 are the first half; an odd curve has its middle node at index h
        k = min(1 + sum(1 for i in range(1, h) if curve[i][0] <= x), h)
        c = [*c[:k], [x, y], *c[k : m - k], [0.0, 0.0], *c[m - k :]]
    else:
        k = 1 + max([i for i in range(m - 1) if curve[i][0] <= x], default=0)
        c.insert(k, [x, y])
    return _curve(enforce_symmetry(c, shape))


def remove_node(curve: Curve, shape: str, i: int) -> Curve:
    """Remove node ``i``; for S and U its partner is removed too. End points and a 2-node curve are kept."""
    m = len(curve)
    if i <= 0 or i >= m - 1 or m <= 2:
        return curve
    c = [list(p) for p in curve]
    if shape in ("S", "U"):
        j = m - 1 - i
        for r in sorted({i, j}, reverse=True):
            del c[r]
    else:
        del c[i]
    return _curve(enforce_symmetry(c, shape))


def editable(shape: str, n_nodes: int, i: int) -> bool:
    """Whether the curve editor lets the user drag node ``i``."""
    if shape == "I" or i in (0, n_nodes - 1):
        return False
    if shape == "J":
        return True
    half = (n_nodes - 1) / 2
    if abs(i - half) < 1e-9:
        return shape == "U"
    return i < half


def preset_curve(name: str, shape: str) -> np.ndarray:
    """The control points of a curve preset for an edge shape.

    A preset is defined over the whole edge and ends at ``(1, 0)``. ``J`` takes it unchanged, and so do ``S`` and ``U``
    if the preset already has the symmetry of the shape.

    ``S`` (rotation by 180 degrees about ``(0.5, 0)``): the whole preset is compressed into the first half of the
    edge (``x`` to ``x / 2``, ``y`` kept) and the second half is its rotated image. The preset ends at ``y = 0``, so
    the curve passes through the center. The edge gets ``2 n - 1`` points for a preset of ``n`` points.

    ``U`` (mirror ``x = 0.5``): the first half of the points of the preset, completed by the mirror image
    (``enforce_symmetry``). Compressing the whole preset would give a notch at the mirror line. If the first half is
    straight for a preset that is not (``ramp_up``), each point is averaged with the image of the point at the
    mirrored index: the symmetric part of the preset.
    """
    p = np.array(get_curve_preset(name), dtype=float)
    if shape not in ("S", "U") or np.allclose(p, enforce_symmetry(p, shape)):
        return np.asarray(enforce_symmetry(p, shape))
    if shape == "S":
        half = p.copy()
        half[:, 0] /= 2
        image = half[-2::-1].copy()
        image[:, 0] = 1.0 - image[:, 0]
        image[:, 1] = -image[:, 1]
        return np.asarray(enforce_symmetry(np.vstack([half, image]), shape))
    out = np.asarray(enforce_symmetry(p, shape))
    if name != "straight" and np.abs(out[:, 1]).max() < 1e-9:
        image = p[::-1].copy()
        image[:, 0] = 1.0 - image[:, 0]
        out = np.asarray(enforce_symmetry((p + image) / 2, shape))
    return out


def preset_availability(draft: Draft, shape_id: int) -> dict[str, str]:
    """Which curve presets can be applied to a shape id of the draft.

    Maps each preset name to ``""`` if applying it (with the symmetry of the edge shape, in the current mode, with the
    other curves and parameters as they are) gives a valid prototile that differs from the current one, else to the
    reason: the curve is already the current one, or the message of ``check_prototile``.
    """
    return dict(_preset_availability(draft, shape_id))


@lru_cache(maxsize=128)
def _preset_availability(draft: Draft, shape_id: int) -> tuple[tuple[str, str], ...]:
    shape = shape_of(draft.type_number, shape_id)
    out = []
    for name in list_curve_presets():
        curve = preset_curve(name, shape)
        applied = draft.with_curve(shape_id, curve)
        if applied == draft:
            out.append((name, "already the current curve"))
            continue
        report = applied.report()
        out.append((name, "" if report.valid else f"the prototile would be invalid: {report.message}"))
    return tuple(out)


# ----------------------------------------------------------------------------------------------------------------------
# The model


@dataclass
class Result:
    committed: bool
    report: PrototileReport


@dataclass
class Entry:
    label: str
    draft: Draft
    time: float = field(default_factory=time.monotonic)
    key: str | None = None


class EditorModel:
    """Current draft, baseline and history. The draft is always valid."""

    COALESCE_SECONDS = 1.0

    def __init__(
        self,
        draft: Draft | None = None,
        clock: Callable[[], float] = time.monotonic,
        label: str = "Initial state",
        baseline_label: str = DEFAULT_PRESET,
    ):
        self._clock = clock
        if draft is None:
            draft = Draft.from_tiling(IsohedralTiling.from_preset(DEFAULT_PRESET))
        draft.tiling()  # the first draft must be valid
        self._history = [Entry(label, draft, clock())]
        self._index = 0
        self.baseline = draft
        self.baseline_label = baseline_label

    @property
    def draft(self) -> Draft:
        return self._history[self._index].draft

    @property
    def tiling(self) -> IsohedralTiling:
        return self.draft.tiling()

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._history) - 1

    @property
    def can_reset(self) -> bool:
        return self.draft != self.baseline

    @property
    def labels(self) -> list[str]:
        return [e.label for e in self._history]

    @property
    def n_undo(self) -> int:
        return self._index

    @property
    def n_redo(self) -> int:
        return len(self._history) - 1 - self._index

    @property
    def undo_label(self) -> str:
        return self._history[self._index].label if self.can_undo else ""

    @property
    def redo_label(self) -> str:
        return self._history[self._index + 1].label if self.can_redo else ""

    def probe(self, draft: Draft) -> PrototileReport:
        """Validity of a draft that is not committed."""
        return draft.report()

    def commit(self, label: str, draft: Draft, key: str | None = None, window: float | None = None) -> Result:
        """Commit a valid draft.

        An entry whose ``key`` equals the key of the newest entry, and which follows it within ``window`` seconds,
        replaces that entry. An invalid draft is not committed.
        """
        report = draft.report()
        if not report.valid:
            return Result(False, report)
        if draft == self.draft:
            return Result(True, report)
        now = self._clock()
        last = self._history[self._index]
        at_end = self._index == len(self._history) - 1
        within = now - last.time < (window or self.COALESCE_SECONDS)
        if key is not None and at_end and last.key == key and within:
            self._history[self._index] = Entry(label, draft, now, key)
        else:
            del self._history[self._index + 1 :]
            self._history.append(Entry(label, draft, now, key))
            self._index += 1
        return Result(True, report)

    def undo(self) -> None:
        if self.can_undo:
            self._index -= 1

    def redo(self) -> None:
        if self.can_redo:
            self._index += 1

    def reset(self) -> Result:
        """Commit the baseline as a new history entry."""
        return self.commit(f"Reset to {self.baseline_label}", self.baseline)

    def load(self, draft: Draft, label: str, baseline_label: str | None = None) -> None:
        """Start a new history with ``draft`` (valid) as its only entry and as the baseline."""
        draft.tiling()
        self._history = [Entry(label, draft, self._clock())]
        self._index = 0
        self.baseline = draft
        self.baseline_label = baseline_label if baseline_label is not None else label

    def load_type(self, number: int) -> None:
        self.load(Draft.of(number), f"IH{number}")

    def load_preset(self, name: str) -> None:
        label = name.replace("_", " ").title()
        self.load(Draft.from_tiling(IsohedralTiling.from_preset(name)), f"Load preset '{label}'", label)

    def load_data(self, data: Any, name: str = "") -> list[str]:
        """Load a design (see ``load_design``); returns the notes. Raises ``ValueError`` if it is unreadable or invalid."""
        draft, notes = load_design(data)
        try:
            draft.tiling()
        except InvalidPrototileError as e:
            raise ValueError(f"the design is not a valid prototile: {e}") from e
        label = name.replace("_", " ").title() if name else f"IH{draft.type_number}"
        self.load(draft, f"Load '{label}'", label)
        return notes
