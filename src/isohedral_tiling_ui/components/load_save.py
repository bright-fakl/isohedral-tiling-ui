"""Load and save tiling configurations."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import panel as pn
import param


class LoadSavePanel(pn.viewable.Viewer):
    """Load and save tiling configurations."""

    state = param.ClassSelector(class_=param.Parameterized)

    def __init__(self, **params):
        super().__init__(**params)

        # Hidden TextInput — receives raw JSON content from the JS FileReader.
        # Passing it as a Bokeh model arg to js_on_click lets JS write to it
        # via the Bokeh WebSocket without needing to find it in the shadow DOM.
        self._file_json = pn.widgets.TextInput(name="", visible=False, margin=0)
        self._file_json.param.watch(self._on_file_content, "value")

        # Load Design button — opens a dynamically-created <input type="file">
        # via pure JS (within the user-gesture context of the click event) and
        # sends the file text back through the hidden TextInput.
        self._load_btn = pn.widgets.Button(
            name="📂 Load Design",
            button_type="default",
            sizing_mode="stretch_width",
        )
        self._load_btn.js_on_click(
            args={"file_json": self._file_json},
            code="""
            const inp = document.createElement('input');
            inp.type = 'file';
            inp.accept = '.json';
            inp.style.display = 'none';
            document.body.appendChild(inp);
            inp.addEventListener('change', function() {
                const file = inp.files[0];
                document.body.removeChild(inp);
                if (!file) return;
                const reader = new FileReader();
                reader.addEventListener('load', function(evt) {
                    file_json.value = evt.target.result;
                });
                reader.readAsText(file);
            });
            inp.click();
            """,
        )

        # Filename input (without extension) — auto-populated, user-editable
        self._filename_input = pn.widgets.TextInput(
            value=self._default_filename(),
            placeholder="filename",
            sizing_mode="stretch_width",
        )
        self._filename_input.param.watch(self._on_filename_change, "value")
        self.state.param.watch(self._update_filename, ["tiling_type", "current_preset"])

        # Save Design button
        self._json_download = pn.widgets.FileDownload(
            callback=self._save_config,
            filename=self._filename_input.value + ".json",
            label="💾 Save Design",
            sizing_mode="stretch_width",
        )
        self._json_download.param.watch(
            lambda e: pn.state.notifications.success(f"'{self._json_download.filename}' saved to downloads folder"),
            "_clicks",
        )

    def _default_filename(self) -> str:
        preset = getattr(self.state, "current_preset", "")
        return preset if preset else f"IH{self.state.tiling_type}"

    def _update_filename(self, *events):
        """Auto-populate filename when tiling type or preset changes."""
        new_name = self._default_filename()
        self._filename_input.value = new_name
        self._json_download.filename = new_name + ".json"

    def _on_filename_change(self, event):
        name = event.new.strip() or self._default_filename()
        self._json_download.filename = name + ".json"

    def _on_file_content(self, event):
        """Handle JSON content received from JS FileReader."""
        if not event.new:
            return
        try:
            from carto_flow.symbol_cartogram.tiling import IsohedralTiling

            data = json.loads(event.new)
            tiling_type = data["tiling_type"]
            parameters = data.get("parameters", [])
            edge_curves = {int(k): [tuple(p) for p in v] for k, v in data.get("edge_curves", {}).items()}
            edge_curve_modes = {int(k): v for k, v in data.get("edge_curve_modes", {}).items()}
            desc = data.get("description", "")

            # Build tiling so we can generate the reference SVG and get type info.
            tiling = IsohedralTiling(
                tiling_type=tiling_type,
                parameters=parameters,
                edge_curves=edge_curves,
            )
            info = tiling.type_info()
            customizable = info.get("customizable_shape_ids", [])

            # Mirror what load_preset does so all dependent views update correctly.
            with self.state.batch_update():
                self.state.tiling_type = tiling_type
                self.state.parameters = parameters
                self.state.edge_curves = edge_curves
                self.state.edge_curve_modes = edge_curve_modes
                self.state.current_preset = desc
                if customizable:
                    self.state.active_curve_id = customizable[0]
                self.state._reset_ranges = True  # resets prototile axis limits
                self.state.parameters_version += 1
                # Update initial-state bookmarks (reference tile SVG, reset target)
                self.state._initial_parameters = parameters
                self.state._initial_edge_curves = edge_curves
                self.state._initial_preset_name = desc
                self.state._initial_prototile_svg = self.state._generate_prototile_svg(tiling)
                self.state.reset_available = False

            # Clear undo/redo history; make the loaded design the first entry.
            label = desc.replace("_", " ").title() if desc else f"IH{tiling_type}"
            self.state._history = [self.state._snapshot(f"Load '{label}'")]
            self.state._history_idx = 0
            self.state._last_coalesce_key = None
            self.state._last_coalesce_time = None

            if desc:
                self._filename_input.value = desc
            pn.state.notifications.success("Design loaded")
        except Exception as e:
            pn.state.notifications.error(f"Failed to load: {e}")

    def _save_config(self):
        """Generate and download JSON config."""
        config = {
            "version": "1.0",
            "tiling_type": self.state.tiling_type,
            "parameters": self.state.parameters,
            "edge_curves": {str(k): [list(p) for p in v] for k, v in self.state.edge_curves.items()},
            "edge_curve_modes": {str(k): v for k, v in self.state.edge_curve_modes.items()},
            "description": self._filename_input.value.strip() or self._default_filename(),
            "metadata": {"created": datetime.now(timezone.utc).isoformat()},
        }
        return io.StringIO(json.dumps(config, indent=2))

    def __panel__(self):
        # _file_json must be in the layout so its Bokeh model is created and
        # connected to the WebSocket session — even though it's invisible.
        return pn.Column(
            self._file_json,
            pn.Card(
                self._load_btn,
                pn.layout.Divider(margin=(6, 0)),
                pn.Row(
                    pn.pane.HTML("<small style='line-height:30px'>Filename:</small>", width=60),
                    self._filename_input,
                ),
                self._json_download,
                title="Load / Save",
                collapsed=True,
            ),
        )
