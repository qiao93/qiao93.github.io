"""Tests for domain/trends.py and presentation's render_trend_section.

Covers:
  - week_bounds_for: Mon–Sun of an ISO week
  - bucket_sessions_by_week: 4-week bucketing, current-week marker, empty weeks
  - compute_trend_report: 4-week aggregates, trend grades (pace/volume/consistency)
  - format helpers: pretty-printing of labels, pace deltas, grade zh text
  - render_trend_section: Markdown shape + edge cases (insufficient data)
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from run_page.analysis.adapters.activity_history import SessionSummary
from run_page.analysis.domain.trends import (
    PACE_DELTA_THRESHOLD_S,
    TrendReport,
    WeekAggregate,
    bucket_sessions_by_week,
    compute_trend_report,
    format_consistency_grade_zh,
    format_pace_grade_zh,
    format_volume_grade_zh,
    format_week_label,
    format_week_pace_delta,
    week_bounds_for,
)
from run_page.analysis.presentation.markdown_renderer import render_trend_section


# ---------- week_bounds_for ----------


def test_week_bounds_monday_returns_same_week() -> None:
    mon, sun = week_bounds_for(date(2026, 5, 4))  # Monday
    assert mon == date(2026, 5, 4)
    assert sun == date(2026, 5, 10)


def test_week_bounds_sunday_returns_same_week() -> None:
    mon, sun = week_bounds_for(date(2026, 5, 10))  # Sunday
    assert mon == date(2026, 5, 4)
    assert sun == date(2026, 5, 10)


def test_week_bounds_wednesday() -> None:
    mon, sun = week_bounds_for(date(2026, 5, 6))  # Wednesday
    assert mon == date(2026, 5, 4)
    assert sun == date(2026, 5, 10)


# ---------- bucket_sessions_by_week ----------


def _sess(date_str: str, km: float, pace_s: float, is_current: bool = False) -> SessionSummary:
    """Build a SessionSummary with a derived duration from pace × distance."""
    dur = km * pace_s
    return SessionSummary(
        date=date_str, distance_km=km, duration_s=dur,
        avg_pace_s=pace_s, avg_hr=None, activity_type="Run", is_current=is_current,
    )


def test_bucket_sessions_by_week_returns_n_weeks() -> None:
    weeks = bucket_sessions_by_week([], today=date(2026, 5, 4), n_weeks=4)
    assert len(weeks) == 4
    # All empty
    assert all(w.session_count == 0 for w in weeks)


def test_bucket_sessions_by_week_marks_current_week() -> None:
    weeks = bucket_sessions_by_week([], today=date(2026, 5, 4), n_weeks=4)
    currents = [w for w in weeks if w.is_current]
    assert len(currents) == 1
    assert currents[0].week_start == date(2026, 5, 4)


def test_bucket_sessions_by_week_groups_correctly() -> None:
    sess = [
        _sess("2026-04-08", 10, 360),  # week 04-06 — OUTSIDE 4w window, dropped
        _sess("2026-04-15", 8, 375),   # week 04-13
        _sess("2026-04-19", 10, 360),  # week 04-13 (same week, 2nd session)
        _sess("2026-04-22", 15, 360),  # week 04-20
        _sess("2026-05-04", 8, 367, is_current=True),  # week 05-04 (current)
    ]
    weeks = bucket_sessions_by_week(sess, today=date(2026, 5, 4), n_weeks=4)
    counts = [w.session_count for w in weeks]
    # 4-week window (oldest → newest): 04-13, 04-20, 04-27, 05-04
    # 04-08 (week 04-06) is outside the window → dropped
    assert counts == [2, 1, 0, 1]


def test_bucket_sessions_by_week_outside_window_dropped() -> None:
    sess = [
        _sess("2026-01-01", 5, 360),  # way too old
        _sess("2026-04-15", 8, 360),  # in window
    ]
    weeks = bucket_sessions_by_week(sess, today=date(2026, 5, 4), n_weeks=4)
    total = sum(w.session_count for w in weeks)
    assert total == 1


def test_bucket_sessions_by_week_weighted_pace() -> None:
    """The week's avg pace should be distance-weighted, not simple mean.

    A 20km run at 6:00 + a 1km run at 4:00 should average close to 6:00
    (because the 20km run dominates), not 5:00.
    """
    sess = [_sess("2026-05-04", 20, 360), _sess("2026-05-05", 1, 240)]
    weeks = bucket_sessions_by_week(sess, today=date(2026, 5, 5), n_weeks=1)
    assert weeks[0].avg_pace_s_per_km > 350  # weighted towards 360, not 300


# ---------- compute_trend_report ----------


def test_trend_report_empty_weeks() -> None:
    r = compute_trend_report([], current_run_pace_s_per_km=300)
    assert r.four_week_total_km == 0.0
    assert r.weeks_with_runs == 0
    assert r.consistency_pct == 0
    assert r.pace_grade == "insufficient_data"


def test_trend_report_insufficient_history_yields_insufficient_grade() -> None:
    """Need ≥3 prior weeks in the window to grade a trend. With n_weeks=4
    and all 3 prior weeks empty, there's only 1 current week → grade
    'insufficient_data' because the prior weeks count is 3 (threshold = 3)
    but only 1 has data... wait: len(prior_weeks)=3 ≥ MIN=3, so it grades.

    The right insufficient case is n_weeks=3 → prior_weeks=2 < 3.
    """
    # 3-week window: [04-20, 04-27, 05-04(current)] → prior=[04-20, 04-27]
    weeks = [
        WeekAggregate(date(2026, 4, 20), date(2026, 4, 26), 3, 30, 10800, 360, False),
        WeekAggregate(date(2026, 4, 27), date(2026, 5, 3), 0, 0, 0, 0, False),
        WeekAggregate(date(2026, 5, 4), date(2026, 5, 10), 1, 8, 2935, 367, True),
    ]
    r = compute_trend_report(weeks, current_run_pace_s_per_km=367)
    assert r.pace_grade == "insufficient_data"
    assert r.volume_grade == "insufficient_data"


def test_trend_report_pace_improving() -> None:
    """Current run is 15s/km faster than 3-week prior avg → improving."""
    weeks = [
        # Prior weeks at slow pace (~6:00/km = 360s/km)
        WeekAggregate(date(2026, 4, 13), date(2026, 4, 19), 3, 30, 10800, 360, False),
        WeekAggregate(date(2026, 4, 20), date(2026, 4, 26), 3, 30, 10800, 360, False),
        WeekAggregate(date(2026, 4, 27), date(2026, 5, 3), 3, 30, 10800, 360, False),
        # Current: 5:30/km = 330s/km — 30s faster than prior avg
        WeekAggregate(date(2026, 5, 4), date(2026, 5, 10), 1, 10, 3300, 330, True),
    ]
    r = compute_trend_report(weeks, current_run_pace_s_per_km=330)
    assert r.pace_grade == "improving"


def test_trend_report_pace_declining() -> None:
    """Current run is 15s/km slower than prior avg → declining."""
    weeks = [
        WeekAggregate(date(2026, 4, 13), date(2026, 4, 19), 3, 30, 9000, 300, False),
        WeekAggregate(date(2026, 4, 20), date(2026, 4, 26), 3, 30, 9000, 300, False),
        WeekAggregate(date(2026, 4, 27), date(2026, 5, 3), 3, 30, 9000, 300, False),
        WeekAggregate(date(2026, 5, 4), date(2026, 5, 10), 1, 10, 3900, 390, True),
    ]
    r = compute_trend_report(weeks, current_run_pace_s_per_km=390)
    assert r.pace_grade == "declining"


def test_trend_report_pace_maintaining() -> None:
    """Current run within ±PACE_DELTA_THRESHOLD_S → maintaining."""
    weeks = [
        WeekAggregate(date(2026, 4, 13), date(2026, 4, 19), 3, 30, 10800, 360, False),
        WeekAggregate(date(2026, 4, 20), date(2026, 4, 26), 3, 30, 10800, 360, False),
        WeekAggregate(date(2026, 4, 27), date(2026, 5, 3), 3, 30, 10800, 360, False),
        WeekAggregate(date(2026, 5, 4), date(2026, 5, 10), 1, 10, 3620, 362, True),
    ]
    r = compute_trend_report(weeks, current_run_pace_s_per_km=362)
    assert r.pace_grade == "maintaining"


def test_trend_report_volume_increasing() -> None:
    """Current week has 20% more km than 3-week prior avg → increasing."""
    weeks = [
        WeekAggregate(date(2026, 4, 13), date(2026, 4, 19), 3, 20, 7200, 360, False),
        WeekAggregate(date(2026, 4, 20), date(2026, 4, 26), 3, 20, 7200, 360, False),
        WeekAggregate(date(2026, 4, 27), date(2026, 5, 3), 3, 20, 7200, 360, False),
        # Current week: 25km (25% above 20km avg)
        WeekAggregate(date(2026, 5, 4), date(2026, 5, 10), 2, 25, 9000, 360, True),
    ]
    r = compute_trend_report(weeks, current_run_pace_s_per_km=360)
    assert r.volume_grade == "increasing"


def test_trend_report_consistency_75_pct() -> None:
    weeks = [
        WeekAggregate(date(2026, 4, 13), date(2026, 4, 19), 3, 20, 7200, 360, False),
        WeekAggregate(date(2026, 4, 20), date(2026, 4, 26), 0, 0, 0, 0, False),
        WeekAggregate(date(2026, 4, 27), date(2026, 5, 3), 3, 20, 7200, 360, False),
        WeekAggregate(date(2026, 5, 4), date(2026, 5, 10), 1, 8, 2880, 360, True),
    ]
    r = compute_trend_report(weeks, current_run_pace_s_per_km=360)
    assert r.weeks_with_runs == 3
    assert r.consistency_pct == 75


# ---------- format helpers ----------


def test_format_week_label() -> None:
    w = WeekAggregate(date(2026, 5, 4), date(2026, 5, 10), 1, 8, 2880, 360, True)
    assert format_week_label(w) == "05-04~05-10"


def test_format_week_pace_delta_missing_data() -> None:
    assert format_week_pace_delta(0, 360) == "—"
    assert format_week_pace_delta(360, 0) == "—"


def test_format_week_pace_delta_within_threshold() -> None:
    # Within PACE_DELTA_THRESHOLD_S = 5s → "持平"
    assert format_week_pace_delta(360, 362) == "持平"
    assert format_week_pace_delta(362, 360) == "持平"


def test_format_week_pace_delta_outside_threshold() -> None:
    # Outside threshold → signed delta in s/km
    assert format_week_pace_delta(350, 360) == "−10s/km"  # faster
    assert format_week_pace_delta(370, 360) == "+10s/km"  # slower


def test_format_pace_grade_zh() -> None:
    assert "进步" in format_pace_grade_zh("improving")
    assert "持平" in format_pace_grade_zh("maintaining")
    assert "退步" in format_pace_grade_zh("declining")
    assert "不足" in format_pace_grade_zh("insufficient_data")


def test_format_volume_grade_zh() -> None:
    assert "增加" in format_volume_grade_zh("increasing")
    assert "稳定" in format_volume_grade_zh("stable")
    assert "减少" in format_volume_grade_zh("decreasing")


def test_format_consistency_grade_zh_thresholds() -> None:
    # 100% → ✅ 稳定
    assert "稳定" in format_consistency_grade_zh(4, 4)
    # 75% → ✅ 稳定
    assert "稳定" in format_consistency_grade_zh(3, 4)
    # 50% → 🟡 一般
    assert "一般" in format_consistency_grade_zh(2, 4)
    # 25% → 🔴 不足
    assert "不足" in format_consistency_grade_zh(1, 4)


# ---------- render_trend_section ----------


def _full_report() -> TrendReport:
    """A complete 4-week report for rendering tests."""
    weeks = [
        WeekAggregate(date(2026, 4, 13), date(2026, 4, 19), 3, 23, 8400, 365, False),
        WeekAggregate(date(2026, 4, 20), date(2026, 4, 26), 2, 23, 8280, 360, False),
        WeekAggregate(date(2026, 4, 27), date(2026, 5, 3), 3, 28, 9800, 350, False),
        WeekAggregate(date(2026, 5, 4), date(2026, 5, 10), 1, 8, 2935, 367, True),
    ]
    return compute_trend_report(weeks, current_run_pace_s_per_km=367)


def test_render_trend_section_contains_required_sections() -> None:
    md = render_trend_section(_full_report(), current_run_pace_s_per_km=367)
    assert "## 📈 跨课趋势（近 4 周）" in md
    assert "| 周 | 课次 | 距离 | 平均配速 | 周对比 |" in md
    assert "**4 周合计**" in md
    assert "**趋势判断**" in md
    assert "- 配速:" in md
    assert "- 训练量:" in md
    assert "- 一致性:" in md


def test_render_trend_section_marks_current_row() -> None:
    md = render_trend_section(_full_report(), current_run_pace_s_per_km=367)
    assert "**← 本课** vs 3w 均" in md


def test_render_trend_section_handles_no_history() -> None:
    """Empty weeks (no prior sessions) should still render with '—' placeholders."""
    weeks = [WeekAggregate(date(2026, 4, 13) + timedelta(weeks=i),
                            date(2026, 4, 19) + timedelta(weeks=i),
                            0, 0, 0, 0, i == 3)
             for i in range(4)]
    report = compute_trend_report(weeks, current_run_pace_s_per_km=0)
    md = render_trend_section(report, current_run_pace_s_per_km=0)
    assert "## 📈 跨课趋势（近 4 周）" in md
    assert "0.0km" in md  # zero distances
    assert "数据不足" in md  # pace/volume grades say "insufficient_data"
