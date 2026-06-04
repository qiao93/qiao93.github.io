"""Filesystem-backed store for analysis output.

Output path convention (SPEC §7):
    <root>/<YYYY-MM-DD>_<slug>.md
    <root>/<YYYY-MM-DD>_<slug>_facts.json
    <root>/<YYYY-MM-DD>_<slug>_narrative.md   (Layer 3, opt-in)
    <root>/index.md  (auto-generated list)

The slug is a sanitized version of the activity title or distance/time
e.g. `2026-05-16_07-19km.md` for a 7.19 km run.

Index row data is pulled from `<stem>_facts.json` when present (most
reliable), and falls back to parsing the markdown header for legacy
reports that predate the facts.json output.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..domain import AnalysisReport

INDEX_HEADER = "# 跑步分析报告\n\n按日期倒序。\n\n"
INDEX_ROW = "- [{date}]({filename}) — {distance:.2f}km / {duration}\n"

# New RED-style H1:   "# 🏃 YYYY-MM-DD 训练分析报告"
# Subtitle blockquote: "> 🏃 **有氧跑** · 8.02km · 48:55 · 平均配速 **6:06/km**"
_NEW_HEADER_RE = re.compile(
    r"·\s*(?P<distance>[\d.]+)km\s*·\s*(?P<duration>[\d:]+)\s*·"
)
# Legacy H2: "## 2026-04-19 有氧跑 | 10.00km | 59:53"  (no trailing pipe)
_LEGACY_HEADER_RE = re.compile(
    r"\|\s*(?P<distance>[\d.]+)km\s*\|\s*(?P<duration>[\d:]+)"
)


def _slug_from_report(report: AnalysisReport) -> str:
    km = report.meta.total_distance_m / 1000.0
    # `7.19km` → `07-19km` for nicer sort order.
    return f"{km:05.2f}km".replace(".", "-")


def _format_duration(seconds: float) -> str:
    if not seconds:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def safe_filename(date_str: str, slug: str) -> str:
    # Sanity: only alphanumerics, dash, dot.
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{date_str}_{slug}")
    return f"{safe}.md"


def _read_index_row(md_path: Path) -> tuple[str, float, str]:
    """Return (date, distance_km, duration_str) for a single report.

    Lookup order:
      1. `<stem>_facts.json`  — structured source, no parsing
      2. New RED-style header  — blockquote with `· 8.02km · 48:55 ·`
      3. Legacy H2 header      — `## YYYY-MM-DD <type> | 8.02km | 48:55`

    On total failure, returns (date_from_filename, 0.0, "—") so the row
    still appears in the index (just without numbers) rather than being
    silently dropped.
    """
    date_part = md_path.stem.split("_")[0]

    # 1. facts.json (preferred)
    facts_path = md_path.with_name(md_path.stem + "_facts.json")
    if facts_path.exists():
        try:
            data = json.loads(facts_path.read_text(encoding="utf-8"))
            km = float(data.get("distance_km") or 0.0)
            dur = _format_duration(float(data.get("duration_s") or 0.0))
            return date_part, km, dur
        except (json.JSONDecodeError, ValueError, OSError):
            pass  # fall through to markdown

    # 2/3. Markdown header (try both formats)
    try:
        text = md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return date_part, 0.0, "—"

    # New style: the blockquote line has the data
    for line in text.splitlines():
        m = _NEW_HEADER_RE.search(line)
        if m:
            return date_part, float(m.group("distance")), m.group("duration")

    # Legacy H2: the first line has the data
    first = text.splitlines()[0] if text else ""
    m = _LEGACY_HEADER_RE.search(first)
    if m:
        return date_part, float(m.group("distance")), m.group("duration")

    return date_part, 0.0, "—"


class AnalysisStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def report_path(self, report: AnalysisReport) -> Path:
        date_str = report.meta.start_date_local.strftime("%Y-%m-%d")
        slug = _slug_from_report(report)
        return self.root / safe_filename(date_str, slug)

    def save_markdown(self, report: AnalysisReport, markdown: str) -> Path:
        p = self.report_path(report)
        p.write_text(markdown, encoding="utf-8")
        self._regenerate_index()
        return p

    def _regenerate_index(self) -> None:
        index = self.root / "index.md"
        rows = [INDEX_HEADER]
        for md in sorted(self.root.glob("*.md"), reverse=True):
            if md.name == "index.md":
                continue
            if md.stem.endswith("_narrative"):
                # Narrative is a supplement to the main report (Layer 3).
                # Don't list it as its own row — the detail page pairs it
                # with `<stem-without-_narrative>.md` automatically.
                continue
            date_part, distance, duration = _read_index_row(md)
            rows.append(INDEX_ROW.format(
                date=date_part, filename=md.name, distance=distance, duration=duration
            ))
        index.write_text("".join(rows), encoding="utf-8")

    def save_report(self, report: "AnalysisReport", markdown: str) -> tuple[Path, list[Path]]:
        """Write the report markdown, facts.json, and sparkline SVGs.

        Returns (markdown_path, sparkline_paths) so the caller can embed
        image references in the markdown string if needed.
        """
        p = self.report_path(report)
        p.write_text(markdown, encoding="utf-8")
        self._regenerate_index()

        # Write facts.json (for _read_index_row and Layer 3)
        # Lazily import to avoid circular dependency with cli.py
        facts_path = p.with_name(p.stem + "_facts.json")
        try:
            from ..cli import _facts_to_json
            facts = _facts_to_json(report)
            facts_path.write_text(
                json.dumps(facts, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass  # non-fatal

        # Write sparkline SVGs (Phase 4 part 2) if trend data is present
        sparkline_paths: list[Path] = []
        if report.trend is not None:
            from ..presentation.sparkline import write_sparklines
            pace_s = report.metrics.pace.mean_s_per_km if report.metrics.pace else 0.0
            sparkline_paths = write_sparklines(p, report.trend.weeks, pace_s)

        return p, sparkline_paths
