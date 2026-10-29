"""Load and save tiling configurations."""

from __future__ import annotations

import io
import json

import panel as pn
import param

from isohedral_tiling_ui.exports import design_json


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
        self.state.param.watch(self._update_filename, ["source_label", "tiling_type"])

        # Save Design button
        self._json_download = pn.widgets.FileDownload(
            callback=self._save_config,
            filename=self._filename_input.value + ".json",
            label="💾 Save Design",
            sizing_mode="stretch_width",
        )
        self._json_download.param.watch(
            lambda e: self.state.notify("success", f"'{self._json_download.filename}' saved to downloads folder"),
            "_clicks",
        )

    def _default_filename(self) -> str:
        return self.state.default_name

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
            data = json.loads(event.new)
        except ValueError as e:
            self.state.notify("error", f"Failed to load: not a JSON file ({e})")
            return
        desc = str(data.get("description", "")) if isinstance(data, dict) else ""
        if self.state.load_data(data, desc):
            if desc:
                self._filename_input.value = desc
            self.state.notify("success", "Design loaded")

    def _save_config(self):
        """Generate and download the design as JSON (isotile format 1.1 with description and metadata)."""
        description = self._filename_input.value.strip() or self._default_filename()
        return io.StringIO(design_json(self.state.tiling, description))

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
