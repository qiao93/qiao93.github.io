"""Pace analysis per SPEC §4.2 and marathon goal comparison."""
from __future__ import annotations

from statistics import mean

from .baselines import Baselines
from .models import Lap, PaceStats, PaceVsGoal, pace_seconds_per_km


def _laps_to_paces(laps: tuple[Lap, ...] | list[Lap]) -> list[float]:
    return [pace_seconds_per_km(lap.avg_speed_mps) for lap in laps if lap.avg_speed_mps]


def _trend(first_half: list[float], second_half: list[float]) -> str:
    if not first_half or not second_half:
        return "even"
    delta = mean(second_half) - mean(first_half)
    if delta < -3:
        return "negative_split"  # second half faster
    if delta > 3:
        return "positive_split"
    return "even"


def compute_pace_stats(
    categorized_main: tuple[Lap, ...],
    categorized_all: tuple[Lap, ...],
) -> PaceStats:
    """Compute pace stats. If `categorized_main` is empty, fall back to all laps.

    Consistency thresholds from SPEC §4.2:
      - has main (interval workout):  range < 10s → excellent, < 20s → good
      - no main (steady run):         range < 20s → consistent
    """
    pool = list(categorized_main) if categorized_main else list(categorized_all)
    paces = _laps_to_paces(pool)
    if not paces:
        return PaceStats(mean_s_per_km=0.0, range_s_per_km=0.0, trend="even", consistency="variable")

    mean_pace = mean(paces)
    range_pace = max(paces) - min(paces)
    has_main = bool(categorized_main)

    if has_main:
        if range_pace < 10:
            consistency = "excellent"
        elif range_pace < 20:
            consistency = "good"
        else:
            consistency = "variable"
    else:
        if range_pace < 20:
            consistency = "consistent"
        else:
            consistency = "variable"

    half = len(paces) // 2
    trend = _trend(paces[:half], paces[half:]) if len(paces) >= 2 else "even"

    return PaceStats(
        mean_s_per_km=mean_pace,
        range_s_per_km=range_pace,
        trend=trend,
        consistency=consistency,
    )


def compute_pace_vs_goal(actual_s_per_km: float, baselines: Baselines) -> PaceVsGoal:
    target = baselines.marathon_pace_target_s_per_km
    delta = actual_s_per_km - target
    lo, hi = baselines.marathon_pace_range_s_per_km
    matches = lo - 5 <= actual_s_per_km <= hi + 5
    return PaceVsGoal(
        target_s_per_km=target,
        actual_s_per_km=actual_s_per_km,
        delta_s_per_km=delta,
        matches=matches,
    )
