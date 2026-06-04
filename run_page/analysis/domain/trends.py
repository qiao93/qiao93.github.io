"""Cross-run trend aggregation (Phase 4 part 1).

For each new run, we compute a 4-week rolling window of aggregates and
report how this run sits relative to recent history. Pure functions,
no I/O — the caller hands us a list of prior `SessionSummary` records
and we bucket them by ISO week (Mon-Sun).

Design:
  - "近 4 周" = the 4 weeks ending on the run's start date
  - Current week contains the run being analyzed (marked `is_current=True`)
  - 4-week average pace is **distance-weighted** (more accurate than
    simple mean — a 20km easy run shouldn't be drowned out by 5×1km
    intervals)
  - Trend grade compares the current run's pace to the **3 weeks prior
    to the current week** to avoid self-reference when this is the
    only run of the current week
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from .models import format_pace


# Grade thresholds — kept here so tests can pin them in one place.
PACE_DELTA_THRESHOLD_S = 5.0     # ±5 s/km = "improving" / "declining"
VOLUME_DELTA_THRESHOLD = 0.15    # ±15% = "increasing" / "decreasing"
MIN_PRIOR_WEEKS_FOR_GRADE = 3    # need ≥3 prior weeks to grade a trend


# ---------- aggregates ----------


@dataclass(frozen=True)
class WeekAggregate:
    """One ISO week (Mon–Sun) of running volume."""
    week_start: date       # Monday
    week_end: date         # Sunday
    session_count: int
    total_distance_km: float
    total_duration_s: float
    # Distance-weighted average pace; 0.0 means "no runs" (sentinel — see
    # the renderer, which formats it as "—").
    avg_pace_s_per_km: float
    is_current: bool       # week containing the run being analyzed


@dataclass(frozen=True)
class TrendReport:
    """All aggregates the renderer needs to draw the trend section."""
    weeks: tuple[WeekAggregate, ...]  # oldest → newest; len = n_weeks (default 4)
    four_week_total_km: float
    four_week_total_sessions: int
    four_week_avg_pace_s_per_km: float  # distance-weighted, 0.0 if no data
    weeks_with_runs: int                # 0..N — consistency count
    pace_grade: str                     # "improving" | "maintaining" | "declining" | "insufficient_data"
    volume_grade: str                   # "increasing" | "stable" | "decreasing" | "insufficient_data"
    consistency_pct: int                # 0..100 = weeks_with_runs / len(weeks) * 100


# ---------- week bucketing ----------


def week_bounds_for(d: date) -> tuple[date, date]:
    """Return (Monday, Sunday) of the ISO week containing `d`."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def bucket_sessions_by_week(
    sessions: Iterable,  # Iterable[SessionSummary] — kept untyped to avoid
                         # the adapter→domain import; structure is
                         # (.date, .distance_km, .duration_s, .avg_pace_s)
    today: date,
    n_weeks: int = 4,
) -> list[WeekAggregate]:
    """Group sessions into the N weeks ending on the week of `today`.

    Weeks are returned oldest → newest. Empty weeks are included as
    zero-aggregate entries (so the renderer always shows N rows).
    `is_current` is set on the week containing `today`.
    """
    # Build a lookup: week_start -> list[sessions in that week]
    today_monday, _ = week_bounds_for(today)
    weeks: list[date] = [today_monday - timedelta(weeks=i) for i in range(n_weeks - 1, -1, -1)]
    buckets: dict[date, list] = {w: [] for w in weeks}

    for s in sessions:
        try:
            d = date.fromisoformat(str(s.date)[:10])
        except ValueError:
            continue
        w_monday, _ = week_bounds_for(d)
        if w_monday in buckets:
            buckets[w_monday].append(s)

    out: list[WeekAggregate] = []
    for w_monday in weeks:
        ss = buckets[w_monday]
        if ss:
            total_dist = sum(s.distance_km for s in ss)
            total_dur = sum(s.duration_s for s in ss)
            # Distance-weighted avg pace
            if total_dist > 0:
                avg_pace = total_dur / total_dist
            else:
                avg_pace = 0.0
        else:
            total_dist = 0.0
            total_dur = 0.0
            avg_pace = 0.0
        out.append(WeekAggregate(
            week_start=w_monday,
            week_end=w_monday + timedelta(days=6),
            session_count=len(ss),
            total_distance_km=total_dist,
            total_duration_s=total_dur,
            avg_pace_s_per_km=avg_pace,
            is_current=(w_monday == today_monday),
        ))
    return out


# ---------- trend computation ----------


def _grade_pace(
    current_pace_s: float,
    prior_avg_pace_s: float,
    has_enough_history: bool,
) -> str:
    if not has_enough_history or current_pace_s <= 0 or prior_avg_pace_s <= 0:
        return "insufficient_data"
    # Negative delta = current is faster (good)
    delta = current_pace_s - prior_avg_pace_s
    if delta <= -PACE_DELTA_THRESHOLD_S:
        return "improving"
    if delta >= PACE_DELTA_THRESHOLD_S:
        return "declining"
    return "maintaining"


def _grade_volume(
    current_week_km: float,
    prior_avg_km: float,
    has_enough_history: bool,
) -> str:
    if not has_enough_history or prior_avg_km <= 0:
        return "insufficient_data"
    if current_week_km <= 0:
        return "decreasing"
    ratio = current_week_km / prior_avg_km
    if ratio >= 1 + VOLUME_DELTA_THRESHOLD:
        return "increasing"
    if ratio <= 1 - VOLUME_DELTA_THRESHOLD:
        return "decreasing"
    return "stable"


def compute_trend_report(
    weeks: list[WeekAggregate],
    current_run_pace_s_per_km: float,
) -> TrendReport:
    """Aggregate the bucketed weeks into a single `TrendReport`.

    The trend grade for pace/volume uses the prior weeks (everything
    before the current one) as the baseline, so the current run isn't
    compared to itself.
    """
    if not weeks:
        # Defensive: caller passed no weeks at all.
        return TrendReport(
            weeks=(), four_week_total_km=0.0, four_week_total_sessions=0,
            four_week_avg_pace_s_per_km=0.0, weeks_with_runs=0,
            pace_grade="insufficient_data", volume_grade="insufficient_data",
            consistency_pct=0,
        )

    prior_weeks = [w for w in weeks if not w.is_current]
    has_history = len(prior_weeks) >= MIN_PRIOR_WEEKS_FOR_GRADE

    total_km = sum(w.total_distance_km for w in weeks)
    total_sessions = sum(w.session_count for w in weeks)
    weeks_with = sum(1 for w in weeks if w.session_count > 0)
    consistency_pct = round(100 * weeks_with / len(weeks))

    # 4-week weighted avg pace (total time / total distance)
    total_dur = sum(w.total_duration_s for w in weeks)
    four_w_avg = (total_dur / total_km) if total_km > 0 else 0.0

    # Baseline = prior weeks only (exclude current to avoid self-reference)
    prior_km = sum(w.total_distance_km for w in prior_weeks)
    prior_dur = sum(w.total_duration_s for w in prior_weeks)
    prior_avg_pace = (prior_dur / prior_km) if prior_km > 0 else 0.0
    prior_avg_km = prior_km / len(prior_weeks) if prior_weeks else 0.0

    # Current week aggregates (for the volume grade)
    current_week = next((w for w in weeks if w.is_current), None)
    current_week_km = current_week.total_distance_km if current_week else 0.0

    return TrendReport(
        weeks=tuple(weeks),
        four_week_total_km=total_km,
        four_week_total_sessions=total_sessions,
        four_week_avg_pace_s_per_km=four_w_avg,
        weeks_with_runs=weeks_with,
        pace_grade=_grade_pace(current_run_pace_s_per_km, prior_avg_pace, has_history),
        volume_grade=_grade_volume(current_week_km, prior_avg_km, has_history),
        consistency_pct=consistency_pct,
    )


# ---------- pretty-printing helpers (used by the renderer) ----------


def format_week_label(w: WeekAggregate) -> str:
    """Short label: `05-04~05-10`."""
    return f"{w.week_start.strftime('%m-%d')}~{w.week_end.strftime('%m-%d')}"


def format_week_pace_delta(current_pace: float, prior_avg_pace: float) -> str:
    """Pretty week-over-week pace delta: `±5s` or `持平`."""
    if current_pace <= 0 or prior_avg_pace <= 0:
        return "—"
    delta = current_pace - prior_avg_pace
    if abs(delta) < PACE_DELTA_THRESHOLD_S:
        return "持平"
    sign = "+" if delta > 0 else "−"
    return f"{sign}{abs(delta):.0f}s/km"


def format_pace_grade_zh(grade: str) -> str:
    return {
        "improving": "✅ 进步",
        "maintaining": "🟡 持平",
        "declining": "🔴 退步",
        "insufficient_data": "⚠️ 数据不足",
    }.get(grade, grade)


def format_volume_grade_zh(grade: str) -> str:
    return {
        "increasing": "📈 增加",
        "stable": "🟡 稳定",
        "decreasing": "📉 减少",
        "insufficient_data": "⚠️ 数据不足",
    }.get(grade, grade)


def format_consistency_grade_zh(weeks_with: int, total: int) -> str:
    pct = round(100 * weeks_with / total) if total else 0
    if pct >= 75:
        return f"✅ 稳定 ({weeks_with}/{total} 周有课)"
    if pct >= 50:
        return f"🟡 一般 ({weeks_with}/{total} 周有课)"
    return f"🔴 不足 ({weeks_with}/{total} 周有课)"


__all__ = [
    "MIN_PRIOR_WEEKS_FOR_GRADE",
    "PACE_DELTA_THRESHOLD_S",
    "TrendReport",
    "VOLUME_DELTA_THRESHOLD",
    "WeekAggregate",
    "bucket_sessions_by_week",
    "compute_trend_report",
    "format_consistency_grade_zh",
    "format_pace_grade_zh",
    "format_volume_grade_zh",
    "format_week_label",
    "format_week_pace_delta",
    "week_bounds_for",
    "format_pace",  # re-export for convenience
]
