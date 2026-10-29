"""Test configuration: a headless matplotlib backend and quiet output from the GUI modules."""

import os

os.environ.setdefault("MPLBACKEND", "Agg")
