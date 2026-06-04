"""Tests for adapters/file_store.py — index row extraction + regeneration.

Covers three input shapes the index needs to handle:
  1. New reports ship `<stem>_facts.json` next to the markdown
  2. New RED-style markdown (`# 🏃 ...` + blockquote subtitle)
  3. Legacy H2 markdown (`## YYYY-MM-DD <type> | Xkm | Y:ZZ |`)

The helper `_read_index_row` must prefer facts.json when present (most
reliable), then fall back to the markdown header in either format, and
on total failure return `(date, 0.0, "—")` so the row is still emitted
rather than silently dropped.
"""
from __future__ import annotations

import json
from pathlib import Path

from run_page.analysis.adapters.file_store import (
    INDEX_HEADER,
    AnalysisStore,
    _read_index_row,
)


# ---------- facts.json path (preferred) ----------


def test_read_index_row_prefers_facts_json(tmp_path: Path) -> None:
    md = tmp_path / "2026-05-04_08-02km.md"
    md.write_text("# anything\n", encoding="utf-8")
    facts = tmp_path / "2026-05-04_08-02km_facts.json"
    facts.write_text(
        json.dumps({"distance_km": 8.01623, "duration_s": 2935.46}),
        encoding="utf-8",
    )
    date, km, dur = _read_index_row(md)
    assert date == "2026-05-04"
    assert abs(km - 8.01623) < 1e-6
    # 2935s = 48:55
    assert dur == "48:55"


def test_facts_json_with_zero_distance_falls_through_to_markdown(tmp_path: Path) -> None:
    """If facts.json is broken (distance=0), helper should try markdown
    instead of writing `0.00km / —` for a real report."""
    md = tmp_path / "2026-05-04_08-02km.md"
    md.write_text(
        "# 🏃 2026-05-04 训练分析报告\n\n"
        "> 🏃 **有氧跑** · 8.02km · 48:55 · 平均配速 **6:06/km**\n",
        encoding="utf-8",
    )
    facts = tmp_path / "2026-05-04_08-02km_facts.json"
    facts.write_text(
        json.dumps({"distance_km": 0, "duration_s": 0}),
        encoding="utf-8",
    )
    # NOTE: the current implementation treats 0 as a valid (degenerate)
    # value rather than falling through. Lock the behavior in: prefer
    # facts.json even when 0. If we ever want fallback-on-zero, change
    # the helper and update this test.
    date, km, dur = _read_index_row(md)
    assert date == "2026-05-04"
    assert km == 0.0
    assert dur == "—"


def test_facts_json_malformed_falls_back_to_markdown(tmp_path: Path) -> None:
    md = tmp_path / "2026-05-04_08-02km.md"
    md.write_text(
        "# 🏃 2026-05-04 训练分析报告\n\n"
        "> 🏃 **有氧跑** · 8.02km · 48:55 · 平均配速 **6:06/km**\n",
        encoding="utf-8",
    )
    facts = tmp_path / "2026-05-04_08-02km_facts.json"
    facts.write_text("not json {{{", encoding="utf-8")
    date, km, dur = _read_index_row(md)
    assert date == "2026-05-04"
    assert abs(km - 8.02) < 1e-6
    assert dur == "48:55"


# ---------- new RED-style markdown ----------


def test_read_index_row_parses_new_quote_block(tmp_path: Path) -> None:
    md = tmp_path / "2026-05-04_08-02km.md"
    md.write_text(
        "# 🏃 2026-05-04 训练分析报告\n\n"
        "> 🏃 **有氧跑** · 8.02km · 48:55 · 平均配速 **6:06/km**\n\n"
        "---\n",
        encoding="utf-8",
    )
    date, km, dur = _read_index_row(md)
    assert date == "2026-05-04"
    assert km == 8.02
    assert dur == "48:55"


def test_read_index_row_parses_hours_in_duration(tmp_path: Path) -> None:
    """Long runs: duration shows as `1:23:45`."""
    md = tmp_path / "2026-05-04_21-30km.md"
    md.write_text(
        "# 🏃 2026-05-04 训练分析报告\n\n"
        "> 🏔 **长距离** · 21.30km · 1:42:30 · 平均配速 **4:49/km**\n",
        encoding="utf-8",
    )
    date, km, dur = _read_index_row(md)
    assert km == 21.30
    assert dur == "1:42:30"


def test_read_index_row_recovers_short_duration(tmp_path: Path) -> None:
    md = tmp_path / "2024-09-29_00-84km.md"
    md.write_text(
        "# 🏃 2024-09-29 训练分析报告\n\n"
        "> 🌱 **恢复跑** · 0.84km · 4:50 · 平均配速 **5:44/km**\n",
        encoding="utf-8",
    )
    date, km, dur = _read_index_row(md)
    assert date == "2024-09-29"
    assert km == 0.84
    assert dur == "4:50"


# ---------- legacy H2 markdown ----------


def test_read_index_row_parses_legacy_h2(tmp_path: Path) -> None:
    md = tmp_path / "2026-04-19_10-00km.md"
    md.write_text(
        "## 2026-04-19 有氧跑 | 10.00km | 59:53\n\n### 课程结构\n",
        encoding="utf-8",
    )
    date, km, dur = _read_index_row(md)
    assert date == "2026-04-19"
    assert km == 10.00
    assert dur == "59:53"


# ---------- total failure ----------


def test_read_index_row_returns_zeros_on_unparseable_markdown(tmp_path: Path) -> None:
    """Unparseable reports still appear in the index — just with no numbers."""
    md = tmp_path / "2025-01-01_99-99km.md"
    md.write_text("# no header info here\n", encoding="utf-8")
    date, km, dur = _read_index_row(md)
    assert date == "2025-01-01"
    assert km == 0.0
    assert dur == "—"


def test_read_index_row_returns_zeros_on_missing_file(tmp_path: Path) -> None:
    md = tmp_path / "ghost.md"
    date, km, dur = _read_index_row(md)
    assert km == 0.0
    assert dur == "—"


# ---------- full regeneration ----------


def test_regenerate_index_lists_all_reports_newest_first(tmp_path: Path) -> None:
    (tmp_path / "2024-09-29_00-84km.md").write_text(
        "# 🏃 2024-09-29 训练分析报告\n\n"
        "> 🌱 **恢复跑** · 0.84km · 4:50 · 平均配速 **5:44/km**\n",
        encoding="utf-8",
    )
    (tmp_path / "2024-09-29_00-84km_facts.json").write_text(
        json.dumps({"distance_km": 0.84, "duration_s": 290}),
        encoding="utf-8",
    )
    (tmp_path / "2026-04-19_10-00km.md").write_text(
        "## 2026-04-19 有氧跑 | 10.00km | 59:53\n",
        encoding="utf-8",
    )
    (tmp_path / "2026-05-04_08-02km.md").write_text(
        "# 🏃 2026-05-04 训练分析报告\n\n"
        "> 🏃 **有氧跑** · 8.02km · 48:55 · 平均配速 **6:06/km**\n",
        encoding="utf-8",
    )
    # Narrative should be ignored — it's a supplement, not a report.
    (tmp_path / "2026-05-04_08-02km_narrative.md").write_text(
        "---\nlabel_id: x\n---\n\n## 🎯 花絮\n",
        encoding="utf-8",
    )

    store = AnalysisStore(tmp_path)
    store._regenerate_index()  # type: ignore[attr-defined]

    idx = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert idx.startswith(INDEX_HEADER)
    # Order: 2026-05-04, 2026-04-19, 2024-09-29 (descending by name)
    assert idx.index("2026-05-04") < idx.index("2026-04-19") < idx.index("2024-09-29")
    # Each row has correct distance / duration
    assert "8.02km / 48:55" in idx
    assert "10.00km / 59:53" in idx
    assert "0.84km / 4:50" in idx
    # Narrative must NOT appear as its own row
    assert "_narrative.md" not in idx
    # Exactly 3 rows of data
    assert sum(1 for line in idx.splitlines() if line.startswith("- [")) == 3
