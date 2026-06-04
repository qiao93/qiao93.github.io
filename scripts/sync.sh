#!/usr/bin/env bash
# Full sync pipeline: clean → Coros fetch → analysis → gen_svg → vite build.
#
# Required env:
#   COROS_ACCOUNT     Coros login email
#   COROS_PASSWORD    Coros login password (plain — MD5-hashed inside)
# Optional env:
#   BIRTHDAY_MONTH    YYYY-MM for month-of-life SVG (default: 1989-03)
#   SYNC_CLEAN=1      Run `pnpm data:clean` first (wipes db, json, FIT_OUT, imported.json)
#   ANALYZE_LATEST=N  Analyze the N most recent activities (default: 5; 0 = all)
#   ANALYZE_SKIP=1    Skip the analysis step entirely
#   CALIBRATE=1       Run --calibrate before analysis to refresh baselines.yaml
#
# Usage:
#   pnpm sync                              # interactive: prompts for password
#   COROS_PASSWORD=... pnpm sync           # non-interactive
#   SYNC_CLEAN=1 ANALYZE_LATEST=10 pnpm sync   # clean + analyze 10 latest

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# --- Python binary: use .venv if present, otherwise fall back to plain python3
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

# --- 0. Sanity checks ---
if [[ -z "${COROS_ACCOUNT:-}" ]]; then
  echo "COROS_ACCOUNT env is required" >&2
  exit 1
fi

# Prompt for password if not provided.
if [[ -z "${COROS_PASSWORD:-}" ]]; then
  if [[ -t 0 ]]; then
    read -r -s -p "Coros password for $COROS_ACCOUNT: " COROS_PASSWORD
    echo
  else
    echo "COROS_PASSWORD env is required (stdin is not a TTY)" >&2
    exit 1
  fi
fi

BIRTHDAY_MONTH="${BIRTHDAY_MONTH:-1989-03}"
ANALYZE_LATEST="${ANALYZE_LATEST:-5}"

# --- 1. Optional clean ---
if [[ "${SYNC_CLEAN:-0}" == "1" ]]; then
  echo ">>> [1/5] pnpm data:clean"
  pnpm data:clean
  pnpm data:clean:svgs
fi

# --- 2. Optional calibrate (refresh baselines from real data) ---
if [[ "${CALIBRATE:-0}" == "1" ]]; then
  echo ">>> [2/5] calibrate baselines"
  "$PYTHON" -m run_page.analysis.cli --calibrate || \
    echo "  (calibrate failed; continuing with existing baselines)"
fi

# --- 3. Coros fetch (uses fast-geocode patch — bypasses Nominatim rate limit) ---
echo ">>> [3/5] coros_sync"
"$PYTHON" -u run_page/_fast_geocode_patch.py \
  "$COROS_ACCOUNT" "$COROS_PASSWORD"

# --- 4. Per-run analysis (skipped if ANALYZE_SKIP=1) ---
if [[ "${ANALYZE_SKIP:-0}" == "1" ]]; then
  echo ">>> [4/5] analysis SKIPPED (ANALYZE_SKIP=1)"
else
  echo ">>> [4/5] analyze latest $ANALYZE_LATEST activities"
  if [[ "$ANALYZE_LATEST" == "0" ]]; then
    "$PYTHON" -m run_page.analysis.cli --all || \
      echo "  (analysis had failures; continuing)"
  else
    "$PYTHON" -m run_page.analysis.cli --latest "$ANALYZE_LATEST" || \
      echo "  (analysis had failures; continuing)"
  fi
fi

# --- 5. Generate all SVG variants + verify build ---
echo ">>> [5/5] gen_svg + vite build"
bash "$REPO_ROOT/scripts/svgs.sh" "$BIRTHDAY_MONTH"
pnpm build

echo ""
echo ">>> Done. artifacts:"
echo "    run_page/data.db"
echo "    src/static/activities.json"
echo "    run_page/analyses/*.md       (per-run Markdown reports)"
echo "    assets/*.svg (github, grid, circular, mol, year_*, year_summary_*)"