"""Shared undo/redo/reset toolbar for the tiling editor."""

from __future__ import annotations

import panel as pn
import param


class HistoryToolbar(pn.viewable.Viewer):
    """Shared history toolbar showing undo/redo/reset controls and the reset target tile."""

    state = param.ClassSelector(class_=param.Parameterized)

    @param.depends(
        "state._history_idx",
        "state._history",
        "state.reset_available",
        "state._initial_prototile_svg",
        "state._initial_preset_name",
        "state.tiling_type",
        "state._type_info",
    )
    def __panel__(self):
        idx = self.state._history_idx
        history = self.state._history

        # Guard against transient inconsistency: _history and _history_idx are
        # set on separate lines in load_preset, so a watcher can fire between
        # them with a stale idx pointing past the (just-truncated) list.
        if idx < 0 or idx >= len(history):
            can_undo = False
            can_redo = False
            undo_desc = ""
            redo_desc = ""
        else:
            can_undo = idx > 0
            can_redo = idx < len(history) - 1
            undo_desc = history[idx].get("description", "") if can_undo else ""
            redo_desc = history[idx + 1].get("description", "") if can_redo else ""

        n_undo = max(0, idx) if idx >= 0 else 0
        n_redo = max(0, len(history) - idx - 1) if idx >= 0 else 0

        # --- Reference tile panel ---
        preset_label = (
            self.state._initial_preset_name.replace("_", " ").title()
            if self.state._initial_preset_name
            else f"IH{self.state.tiling_type}"
        )

        type_info = self.state._type_info
        info_line1 = f"IH{self.state.tiling_type}"
        info_line2 = ""
        if type_info:
            nv = type_info.get("num_vertices", 0)
            na = type_info.get("num_aspects", 0)
            np_ = type_info.get("num_parameters", 0)
            nc = len(type_info.get("customizable_shape_ids", []))
            line1_parts = []
            if nv:
                line1_parts.append(f"{nv} vert{'s' if nv != 1 else ''}")
            if na:
                line1_parts.append(f"{na} aspect{'s' if na != 1 else ''}")
            line2_parts = []
            if np_:
                line2_parts.append(f"{np_} param{'s' if np_ != 1 else ''}")
            if nc:
                line2_parts.append(f"{nc} curve{'s' if nc != 1 else ''}")
            if line1_parts:
                info_line1 += " · " + " · ".join(line1_parts)
            info_line2 = " · ".join(line2_parts)

        # --- Undo / Redo / Reset buttons stacked vertically ---
        buttons = pn.Column(
            pn.widgets.Button(
                name=f"↶ Undo ({n_undo})",
                description=undo_desc or None,
                on_click=lambda e: self.state.undo(),
                disabled=not can_undo,
                width=110,
                button_type="default",
            ),
            pn.widgets.Button(
                name=f"↷ Redo ({n_redo})",
                description=redo_desc or None,
                on_click=lambda e: self.state.redo(),
                disabled=not can_redo,
                width=110,
                button_type="default",
            ),
            pn.widgets.Button(
                name="Reset",
                on_click=lambda e: self.state.reset_parameters(),
                disabled=not self.state.reset_available,
                width=110,
            ),
            margin=0,
        )

        if self.state._initial_prototile_svg:
            info_html = (
                f"<div style='font-size:11px;color:#444;line-height:1.5;white-space:nowrap'>"
                f"<b>{preset_label}</b><br>"
                f"<span style='color:#888;font-size:10px'>{info_line1}</span>"
                + (f"<br><span style='color:#888;font-size:10px'>{info_line2}</span>" if info_line2 else "")
                + "</div>"
            )
            return pn.Row(
                pn.Column(
                    pn.pane.SVG(
                        self.state._initial_prototile_svg,
                        width=52,
                        height=52,
                    ),
                    pn.pane.HTML(info_html, margin=(4, 0, 0, 0)),
                    align="center",
                    margin=0,
                ),
                buttons,
                align="center",
                margin=(4, 5),
                width=300,
            )

        return pn.Column(buttons, align="center", margin=(4, 5), width=300)
