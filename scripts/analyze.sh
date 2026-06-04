#!/usr/bin/env bash
# Stand-alone analysis: assumes Coros sync has already populated FIT_OUT + id_map.
#
# Required env:
#   COROS_ACCOUNT
#   COROS_PASSWORD
# Optional env:
#   LATEST=N        analyze N most recent (default: 5; 0 = all)
#   OUT=path        output directory (default: run_page/analyses)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# --- Python binary: use .venv if present, otherwise fall back to plain python3
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

if [[ -z "${COROS_ACCOUNT:-}" || -z "${COROS_PASSWORD:-}" ]]; then
  echo "COROS_ACCOUNT and COROS_PASSWORD env are required" >&2
  exit 1
fi

LATEST="${LATEST:-5}"
OUT="${OUT:-$REPO_ROOT/run_page/analyses}"

export COROS_ACCOUNT COROS_PASSWORD
if [[ "$LATEST" == "0" ]]; then
  "$PYTHON" -m run_page.analysis.cli --all --out "$OUT"
else
  "$PYTHON" -m run_page.analysis.cli --latest "$LATEST" --out "$OUT"
fi