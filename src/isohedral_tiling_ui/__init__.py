"""Isohedral Tiling UI - Panel + Bokeh-based interactive tiling generator.

This module provides a web-based UI for creating and customizing isohedral
tilings. Users can select tiling types, adjust parameters, and create custom
edge curves.

Usage
-----
Run from command line:

    panel serve --show src/isohedral_tiling_ui/app.py

Or use the Python API:

    from isohedral_tiling_ui import launch_ui
    launch_ui(port=5006)

Or via the installed console script:

    isohedral-tiling-ui
"""

from __future__ import annotations

__all__ = ["launch_ui"]


def launch_ui(port: int = 5006, show: bool = True) -> None:
    """Launch the tiling UI web application.

    Parameters
    ----------
    port : int
        Port number for the Panel server.
    show : bool
        If True, open browser automatically.
    """
    import subprocess
    import sys
    from pathlib import Path

    app_path = Path(__file__).parent / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "panel",
        "serve",
        str(app_path),
        "--port",
        str(port),
        "--num-procs",
        "1",
    ]
    if show:
        cmd.append("--show")

    subprocess.run(cmd)
