"""RunAnalyzer — application-layer orchestrator.

Given a labelId, it:
  1. fetches ActivityMeta from the Coros API
  2. fetches BodyStateSnapshot for that date
  3. parses the FIT file via the adapter
  4. calls every domain analyzer
  5. queries the local SQLite for "vs recent N sessions" + 4-week trend context
  6. renders the report via presentation

No I/O specifics leak below this point — domain stays pure, and the
adapter ports are the only thing that knows about HTTP / FIT / FS.
"""
from __future__ import annotations

from datetime import datetime

from ..adapters import CorosApiPort
from ..adapters.activity_history import (
    recent_sessions,
    session_for,
    sessions_before,
)
from ..adapters.fit_parser import parse_fit_laps
from ..domain import (
    ActivityMetrics,
    AnalysisReport,
    Baselines,
    BodyStateSnapshot,
    bucket_sessions_by_week,
    classify_laps,
    compute_biomech_delta,
    compute_hr_drift,
    compute_pace_stats,
    compute_pace_vs_goal,
    compute_trend_report,
    recommendations,
)
from ..presentation.markdown_renderer import render_report


class RunAnalyzer:
    def __init__(self, api: CorosApiPort, baselines: Baselines):
        self._api = api
        self._baselines = baselines

    def analyze(self, label_id: str) -> AnalysisReport:
        # 1. activity-level metadata
        meta = self._api.activity_meta(label_id)

        # 2. body state snapshot
        snap = self._api.body_state(meta.start_date_local.date(), self._baselines.hrv_baseline_ms)

        # 3. parse FIT laps
        fit_path = self._api.fit_path(label_id)
        if not fit_path.exists():
            raise FileNotFoundError(f"FIT not found: {fit_path}")
        laps = parse_fit_laps(fit_path)

        # 4. domain analysis
        categorized = classify_laps(laps)
        all_laps = categorized.all()
        pace = compute_pace_stats(categorized.main, all_laps)
        hr = compute_hr_drift(categorized.main, all_laps)
        bio = compute_biomech_delta(categorized.main, all_laps)
        pvg = compute_pace_vs_goal(pace.mean_s_per_km, self._baselines)
        recs = recommendations(pace, hr, bio, snap, self._baselines)

        metrics = ActivityMetrics(
            categorized=categorized,
            pace=pace,
            hr=hr,
            bio=bio,
            pace_vs_goal=pvg,
            body_state=snap,
            recommendations=recs,
        )

        # 5. recent sessions for "vs N" comparison
        run_date = meta.start_date_local.date()
        prior = recent_sessions(
            before_date=run_date,
            n=5,
            activity_type="Run",
        )
        current = session_for(
            on_date=run_date,
            activity_type="Run",
        )
        # Current goes first; then prior sessions
        recent = [s for s in ([current] if current else []) + list(prior)]

        # 5b. 4-week trend context (Phase 4 part 1). Fetch enough prior
        # sessions to cover 4 ISO weeks. The current run is bucketed in
        # by the "is_current" flag on the matching `session_for` row.
        prior_sessions = sessions_before(
            before_date=run_date,
            n=50,  # ~ 4w × 7 runs/wk, with headroom
            activity_type="Run",
        )
        # Mark the current run in the trend bucket list
        trend_sessions = list(prior_sessions)
        if current is not None:
            trend_sessions.append(current)
        weeks = bucket_sessions_by_week(trend_sessions, today=run_date, n_weeks=4)
        trend = compute_trend_report(weeks, current_run_pace_s_per_km=pace.mean_s_per_km)

        # 6. render markdown
        # md_stem is the slug used for sparkline filenames, e.g. "2026-05-04_08-02km"
        km = meta.total_distance_m / 1000.0
        md_stem = f"{meta.start_date_local.strftime('%Y-%m-%d')}_{km:05.2f}km".replace(".", "-")
        markdown = render_report(
            meta,
            metrics,
            self._baselines,
            recent_sessions=recent,
            trend=trend,
            current_run_pace_s_per_km=pace.mean_s_per_km,
            md_stem=md_stem,
        )

        return AnalysisReport(
            meta=meta,
            metrics=metrics,
            recent_sessions=tuple(recent),
            trend=trend,
            markdown=markdown,
        )
