---
title: Isohedral Tiling Generator
emoji: 🔷
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
license: mit
---

# isohedral-tiling-ui

Interactive Panel/Bokeh UI for designing isohedral tilings, powered by [isotile](https://github.com/bright-fakl/isotile).

**Try it out:** https://devfakl-isohedral-tiling-ui.hf.space/

## Usage

```bash
pip install isohedral-tiling-ui
isohedral-tiling-ui
```

Or from Python:

```python
from isohedral_tiling_ui import launch_ui

launch_ui(port=5006)
```

## Development

```bash
uv venv && uv pip install -e . --group dev -c ci/constraints.txt
uv run --no-sync pytest -q --ignore=tests/browser   # headless tests
uv pip install playwright && uv run --no-sync playwright install chromium
uv run --no-sync pytest -q tests/browser            # browser tests (Chromium)
```

CI (`.github/workflows/ci.yml`) runs on pull requests and on pushes to `main`: a lint check that must not exceed the recorded baseline (`ci/lint_baseline.txt`), the tests on Python 3.10 to 3.13, the browser tests, and a Docker build whose container must answer HTTP 200 without a traceback in its log.

Pull requests are checked by the Docker build and smoke-test job; there is no staging Space because the free plan runs one Docker Space at a time.

The live Space (`devfakl/isohedral-tiling-ui`) is fed a single snapshot commit (without `tests/` and `ci/`) on every push to `main`, after all of those jobs pass. The job then waits until the Space runs that commit and answers HTTP 200, and fails with the Space's stage (for example `BUILD_ERROR`) otherwise. To redeploy or roll back, run the CI workflow manually on `main` and give a commit of `main` as `ref`; a manual run skips the CI gate for that ref.

A weekly job runs the tests against isotile `main` instead of the PyPI release and only reports.
