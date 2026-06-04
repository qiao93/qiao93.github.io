"""Heart-rate analysis per SPEC §4.3."""
from __future__ import annotations

from statistics import mean

from .models import HrStats, Lap


def compute_hr_drift(main_laps: tuple[Lap, ...], all_laps: tuple[Lap, ...]) -> HrStats:
    """drift_bpm = last_main.avg_hr - first_main.avg_hr (0 if no main)."""
    pool = list(main_laps) if main_laps else list(all_laps)
    hr_values = [lap.avg_heart_rate for lap in pool if lap.avg_heart_rate]
    if not hr_values:
        return HrStats(mean_bpm=0.0, max_bpm=0, drift_bpm=0.0, drift_grade="good")

    mean_hr = mean(hr_values)
    max_hr = max((lap.max_heart_rate or lap.avg_heart_rate or 0) for lap in pool)

    drift = 0.0
    if main_laps and len(main_laps) >= 2:
        first_hr = main_laps[0].avg_heart_rate
        last_hr = main_laps[-1].avg_heart_rate
        if first_hr is not None and last_hr is not None:
            drift = float(last_hr - first_hr)

    if drift < 15:
        grade = "excellent"
    elif drift < 20:
        grade = "good"
    else:
        grade = "needs_work"

    return HrStats(
        mean_bpm=mean_hr,
        max_bpm=int(max_hr),
        drift_bpm=drift,
        drift_grade=grade,
    )
