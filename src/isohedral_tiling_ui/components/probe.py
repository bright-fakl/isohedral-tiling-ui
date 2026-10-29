"""A throttled channel from the pan handlers in the browser to a Python callback.

A handler calls ``window._isoProbeSend(channel.in_source, name, payload)`` for every pan event. At most 8 payloads per
second go to Python, one at a time: while an answer is pending, newer payloads replace the waiting one and the
intermediate ones are dropped. Python calls the handler with the decoded payload and answers by writing ``out_source``,
which releases the next payload.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from bokeh.models import ColumnDataSource, CustomJS

MAX_RATE = 8  # payloads per second

# Defines the sender once per page. ``window._isoProbeLog`` collects the round-trip times in ms.
SEND_JS = f"""
if (!window._isoProbeSend) {{
    window._isoProbe = window._isoProbe || {{}};
    window._isoProbeLog = window._isoProbeLog || [];
    window._isoProbeFlush = function(name) {{
        const s = window._isoProbe[name];
        if (s.inflight && performance.now() - s.sent > 1000) s.inflight = false;  // an answer was lost
        if (s.inflight || s.pending === null) return;
        const wait = {1000 // MAX_RATE} - (performance.now() - s.sent);
        if (wait > 0) {{
            if (!s.timer) s.timer = setTimeout(() => {{ s.timer = null; window._isoProbeFlush(name); }}, wait);
            return;
        }}
        s.inflight = true;
        s.sent = performance.now();
        s.seq += 1;
        const payload = s.pending;
        s.pending = null;
        s.src.data = {{seq: [s.seq], payload: [payload]}};
    }};
    window._isoProbeSend = function(src, name, payload) {{
        const s = window._isoProbe[name] = window._isoProbe[name]
            || {{inflight: false, sent: -1e9, pending: null, timer: null, seq: 0}};
        s.src = src;
        s.pending = JSON.stringify(payload);
        window._isoProbeFlush(name);
    }};
    window._isoProbeCancel = function(name) {{
        if (window._isoProbe[name]) window._isoProbe[name].pending = null;
    }};
}}
"""

ACK_JS = """
const s = window._isoProbe && window._isoProbe[name];
if (s) {
    s.inflight = false;
    window._isoProbeLog.push(performance.now() - s.sent);
    window._isoProbeFlush(name);
}
"""


class ProbeChannel:
    """Browser to Python channel named ``name``; ``handler`` receives the decoded payload."""

    def __init__(self, name: str, handler: Callable[[Any], None]):
        self.name = name
        self.handler = handler
        self.in_source = ColumnDataSource(data={"seq": [0], "payload": [""]})
        self.out_source = ColumnDataSource(data={"seq": [0]})
        self.in_source.on_change("data", self._on_in)
        self.out_source.js_on_change("data", CustomJS(args={"name": name}, code=SEND_JS + ACK_JS))
        # Reach ``out_source`` from a model of the document, so that it is sent to the browser.
        self.in_source.tags = [self.out_source]

    def send_js(self, payload_js: str) -> str:
        """JavaScript that sends ``payload_js`` (an expression); it needs ``probe_in`` in the CustomJS args."""
        return f"{SEND_JS}\nwindow._isoProbeSend(probe_in, {json.dumps(self.name)}, {payload_js});"

    @property
    def cancel_js(self) -> str:
        """JavaScript that drops the payload that is waiting (at the end of a drag)."""
        return f"if (window._isoProbeCancel) window._isoProbeCancel({json.dumps(self.name)});"

    def _on_in(self, attr: str, old: Any, new: Any) -> None:
        try:
            payload = json.loads(new["payload"][0])
        except (KeyError, IndexError, ValueError):
            payload = None
        try:
            if payload is not None:
                self.handler(payload)
        finally:
            self.out_source.data = {"seq": [new["seq"][0]]}
