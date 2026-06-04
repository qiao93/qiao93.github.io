"""CLI entry point for Layer 3.

Usage:
    python -m run_page.analysis.narrative --facts 2026-05-16_facts.json --out narrative.md

Environment:
    ANTHROPIC_API_KEY  — required for actual LLM calls; if missing, exits 0
                            with a notice (Layer 1+2 still produced facts).
    NARRATIVE_MODEL    — Claude model ID (default: claude-haiku-4-5)
    NARRATIVE_MAX_TOKENS — default 1500
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .generator import generate_narrative, write_narrative


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASELINES = REPO_ROOT / "run_page" / "analysis" / "baselines.yaml"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run-analysis-narrative")
    p.add_argument("--facts", required=True, help="path to facts.json")
    p.add_argument("--out", required=True, help="output .md path")
    p.add_argument("--baselines", default=None, help="path to baselines.yaml")
    p.add_argument(
        "--recent",
        nargs="*",
        default=[],
        help="paths to recent facts.json files (most recent first)",
    )
    p.add_argument("--model", default=os.environ.get("NARRATIVE_MODEL", "claude-haiku-4-5"))
    p.add_argument("--max-tokens", type=int, default=int(os.environ.get("NARRATIVE_MAX_TOKENS", "1500")))
    args = p.parse_args(argv)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[narrative] ANTHROPIC_API_KEY not set, skipping", file=sys.stderr)
        return 0

    baselines_path = args.baselines or DEFAULT_BASELINES
    baselines_yaml = baselines_path.read_text(encoding="utf-8") if baselines_path.exists() else ""

    # Load recent summaries (just the meta, not full laps)
    import json
    recent = []
    for path in args.recent:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            # Compact: only the meta + scores, not the full laps
            recent.append({
                "date": data.get("date"),
                "type": data.get("type"),
                "distance_km": data.get("distance_km"),
                "duration_s": data.get("duration_s"),
                "avg_pace_s": data.get("avg_pace_s"),
                "pace_stats": data.get("pace_stats"),
                "hr_stats": data.get("hr_stats"),
                "load_ratio": data.get("load_ratio"),
            })
        except Exception:  # noqa: BLE001
            continue

    narrative = generate_narrative(
        facts_path=Path(args.facts),
        baselines_yaml=baselines_yaml,
        recent_summaries=recent,
        api_key=api_key,
        model=args.model,
        max_tokens=args.max_tokens,
    )

    if narrative is None:
        print("[narrative] generation failed (see warnings above); skipping", file=sys.stderr)
        return 0

    out_path = write_narrative(narrative, Path(args.out))
    print(
        f"✓ narrative → {out_path}  "
        f"({narrative.prompt_tokens}+{narrative.completion_tokens} tokens, "
        f"model={narrative.model})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
