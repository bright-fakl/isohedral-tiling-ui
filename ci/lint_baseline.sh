#!/usr/bin/env bash
# Fail if ruff reports more errors, or ruff format would change more files, than recorded in ci/lint_baseline.txt.
# The existing findings do not block; new ones do. Run from the repository root with ruff on the PATH.
set -u
cd "$(dirname "$0")/.."
. <(grep -v '^#' ci/lint_baseline.txt)

check_out=$(ruff check --no-fix . 2>&1 || true)
format_out=$(ruff format --check . 2>&1 || true)
echo "$check_out" | tail -5
echo "$format_out" | tail -3

errors=$(echo "$check_out" | sed -n 's/^Found \([0-9]*\) errors\?\..*/\1/p')
files=$(echo "$format_out" | sed -n 's/^\([0-9]*\) files\? would be reformatted.*/\1/p')
errors=${errors:-0}
files=${files:-0}
echo "ruff errors: $errors (baseline $ruff_errors); files to format: $files (baseline $ruff_format_files)"

status=0
if [ "$errors" -gt "$ruff_errors" ]; then echo "::error::ruff errors grew beyond the baseline"; status=1; fi
if [ "$files" -gt "$ruff_format_files" ]; then echo "::error::unformatted files grew beyond the baseline"; status=1; fi
exit $status
