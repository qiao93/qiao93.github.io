#!/usr/bin/env bash
# Regenerate every SVG variant the page consumes.
# Reads birthday month from $1 (default 1989-03).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

BIRTHDAY_MONTH="${1:-${BIRTHDAY_MONTH:-1989-03}}"

gen() {
  local type="$1" output="$2"; shift 2
  echo "    --type $type -> $output"
  .venv/bin/python run_page/gen_svg.py --from-db --type "$type" --output "$output" "$@" 2>&1 | tail -2
}

gen github      assets/github.svg
gen grid        assets/grid.svg
gen circular    assets/circular.svg
gen year_summary assets/year_summary.svg
gen monthoflife  assets/mol.svg          --birth "$BIRTHDAY_MONTH"

echo ">>> svgs done."
