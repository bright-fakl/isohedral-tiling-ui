#!/bin/bash
set -e

export MPLBACKEND=Agg

APP_PATH=$(python -c "
import importlib.util, pathlib
spec = importlib.util.find_spec('isohedral_tiling_ui')
print(pathlib.Path(spec.origin).parent / 'app.py')
")

exec panel serve "$APP_PATH" \
    --address 0.0.0.0 \
    --port 7860 \
    --allow-websocket-origin "*" \
    --num-procs 1
