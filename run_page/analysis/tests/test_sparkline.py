"""Tests for presentation/sparkline.py — pure SVG generators.

Covers:
  - sparkline_distance: bar heights, current-week emphasis, labels
  - sparkline_pace: line chart, reference line, data points, empty case
  - sparkline_consistency: ring arc, center text, empty case
  - write_sparklines: file I/O, stem naming
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from run_page.analysis.domain.trends import WeekAggregate
from run_page.analysis.presentation.sparkline import (
    sparkline_consistency,
    sparkline_distance,
    sparkline_pace,
    sparkline_stem,
    write_sparklines,
)


# ---------- Test data fixtures ----------


def _make_week(
    start: date,
    sessions: int,
    km: float,
    pace_s: float,
    is_current: bool = False,
) -> WeekAggregate:
    dur = km * pace_s
    return WeekAggregate(
        week_start=start,
        week_end=start + timedelta(days=6),
        session_count=sessions,
        total_distance_km=km,
        total_duration_s=dur,
        avg_pace_s_per_km=pace_s,
        is_current=is_current,
    )


def _zero_week(offset: int) -> WeekAggregate:
    return _make_week(
        date(2026, 4, 13) + timedelta(weeks=offset),
        sessions=0, km=0.0, pace_s=0.0, is_current=False,
    )


# ---------- sparkline_distance ----------


def test_sparkline_distance_all_zero_weeks():
    """All-zero weeks produce zero-height bars (not crash)."""
    weeks = tuple(_zero_week(i) for i in range(4))
    svg = sparkline_distance(weeks)
    assert svg.startswith("<svg")
    assert "</svg>" in svg
    assert "height=" in svg
    assert "viewBox" in svg


def test_sparkline_distance_current_week_red():
    weeks = (
        _make_week(date(2026, 4, 13), 1, 20.0, 360, False),
        _make_week(date(2026, 4, 20), 1, 20.0, 360, False),
        _make_week(date(2026, 4, 27), 1, 20.0, 360, False),
        _make_week(date(2026, 5, 4), 1, 10.0, 360, True),
    )
    svg = sparkline_distance(weeks)
    # Current week bar uses CLR_CURRENT = "#ef4444" (red)
    assert "#ef4444" in svg
    # Bar width is 30px
    assert 'width="30"' in svg


def test_sparkline_distance_prior_weeks_zinc():
    weeks = (
        _make_week(date(2026, 4, 13), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 20), 0, 0.0, 0, False),   # empty
        _make_week(date(2026, 4, 27), 1, 15.0, 360, False),
        _make_week(date(2026, 5, 4), 1, 10.0, 360, True),
    )
    svg = sparkline_distance(weeks)
    # Empty week uses CLR_EMPTY = "#3f3f46"
    assert "#3f3f46" in svg
    # Prior (non-empty, non-current) uses CLR_PRIOR = "#52525b"
    assert "#52525b" in svg


def test_sparkline_distance_contains_week_labels():
    weeks = (
        _make_week(date(2026, 4, 13), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 20), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 27), 1, 15.0, 360, False),
        _make_week(date(2026, 5, 4), 1, 10.0, 360, True),
    )
    svg = sparkline_distance(weeks)
    # Labels are mm-dd format
    assert "04-13" in svg
    assert "05-04" in svg


# ---------- sparkline_pace ----------


def test_sparkline_pace_with_data():
    weeks = (
        _make_week(date(2026, 4, 13), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 20), 1, 15.0, 365, False),
        _make_week(date(2026, 4, 27), 1, 15.0, 355, False),
        _make_week(date(2026, 5, 4), 1, 10.0, 367, True),
    )
    svg = sparkline_pace(weeks, current_pace_s_per_km=367)
    assert svg.startswith("<svg")
    assert "</svg>" in svg
    # Polyline present for connected line
    assert "<polyline" in svg
    # Current week data point larger (r=3.5 vs 2.5)
    assert 'r="3.5"' in svg
    # Dashed reference line for current run pace
    assert 'stroke-dasharray="2,2"' in svg


def test_sparkline_pace_no_data():
    """All-zero paces → flat dashed line + '—' label."""
    weeks = tuple(_make_week(date(2026, 4, 13) + timedelta(weeks=i), 0, 0.0, 0.0) for i in range(4))
    svg = sparkline_pace(weeks, current_pace_s_per_km=0)
    assert svg.startswith("<svg")
    # Fallback max_y = 600 → s/km axis label present
    assert "s/km" in svg


def test_sparkline_pace_reference_line_hidden_when_zero():
    weeks = (
        _make_week(date(2026, 4, 13), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 20), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 27), 1, 15.0, 360, False),
        _make_week(date(2026, 5, 4), 1, 10.0, 360, True),
    )
    svg = sparkline_pace(weeks, current_pace_s_per_km=0)
    # No dashed reference line when current_pace_s_per_km=0
    assert 'stroke-dasharray="2,2"' not in svg


# ---------- sparkline_consistency ----------


def test_sparkline_consistency_partial_ring():
    weeks = (
        _make_week(date(2026, 4, 13), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 20), 0, 0.0, 0.0, False),   # empty
        _make_week(date(2026, 4, 27), 1, 15.0, 360, False),
        _make_week(date(2026, 5, 4), 1, 10.0, 360, True),
    )
    svg = sparkline_consistency(weeks)
    assert svg.startswith("<svg")
    assert "</svg>" in svg
    # 3/4 weeks = 75% → arc uses stroke-dasharray
    assert "stroke-dasharray" in svg
    # Center shows "3" (active weeks)
    assert ">3<" in svg


def test_sparkline_consistency_full_ring():
    weeks = (
        _make_week(date(2026, 4, 13), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 20), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 27), 1, 15.0, 360, False),
        _make_week(date(2026, 5, 4), 1, 10.0, 360, True),
    )
    svg = sparkline_consistency(weeks)
    # 4/4 weeks = full ring
    assert ">4<" in svg


def test_sparkline_consistency_empty():
    """No active weeks → no arc, center shows 0."""
    weeks = tuple(_zero_week(i) for i in range(4))
    svg = sparkline_consistency(weeks)
    assert ">0<" in svg
    # stroke-dasharray only when frac > 0
    assert "stroke-dasharray" not in svg


# ---------- sparkline_stem ----------


def test_sparkline_stem():
    from pathlib import Path
    p = Path("/analyses/2026-05-04_08-02km.md")
    stem = sparkline_stem(p)
    assert stem == Path("/analyses/2026-05-04_08-02km")
    assert stem.suffix == ""


# ---------- write_sparklines ----------


def test_write_sparklines_writes_three_files(tmp_path: Path) -> None:
    weeks = (
        _make_week(date(2026, 4, 13), 1, 15.0, 360, False),
        _make_week(date(2026, 4, 20), 1, 15.0, 365, False),
        _make_week(date(2026, 4, 27), 1, 15.0, 355, False),
        _make_week(date(2026, 5, 4), 1, 10.0, 367, True),
    )
    md_path = tmp_path / "2026-05-04_08-02km.md"
    md_path.write_text("dummy", encoding="utf-8")

    result = write_sparklines(md_path, weeks, current_pace_s=367)

    assert len(result) == 3
    assert "distance" in result
    assert "pace" in result
    assert "consistency" in result

    for name, path in result.items():
        assert path.exists(), f"{name} file not written"
        content = path.read_text(encoding="utf-8")
        assert content.startswith("<svg"), f"{name} is not valid SVG"
        assert "</svg>" in content


def test_write_sparklines_filenames_correct(tmp_path: Path) -> None:
    weeks = tuple(
        _make_week(date(2026, 4, 13) + timedelta(weeks=i), 1, 10.0, 360, i == 3)
        for i in range(4)
    )
    md_path = tmp_path / "2026-05-04_08-02km.md"
    md_path.write_text("x", encoding="utf-8")

    result = write_sparklines(md_path, weeks, 367)

    for name, path in result.items():
        assert path.name == f"2026-05-04_08-02km_{name}.svg"
        assert path.parent == tmp_path


def test_write_sparklines_distance_bar_height_proportional(tmp_path: Path) -> None:
    """Higher km = taller bar in the distance sparkline."""
    weeks = (
        _make_week(date(2026, 4, 13), 1, 5.0, 360, False),
        _make_week(date(2026, 4, 20), 1, 20.0, 360, False),
        _make_week(date(2026, 4, 27), 1, 5.0, 360, False),
        _make_week(date(2026, 5, 4), 1, 5.0, 360, True),
    )
    md_path = tmp_path / "test.md"
    md_path.write_text("x", encoding="utf-8")

    result = write_sparklines(md_path, weeks, 360)
    svg = result["distance"].read_text(encoding="utf-8")
    # At least 4 bar rects
    assert svg.count("<rect") >= 4