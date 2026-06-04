"""Tests for domain/pace_analyzer.py."""
import pytest

from run_page.analysis.domain import (
    CategorizedLaps,
    Lap,
    LapCategory,
    PaceStats,
    classify_laps,
    compute_pace_stats,
    compute_pace_vs_goal,
)
from run_page.analysis.domain.baselines import Baselines
from run_page.analysis.tests.conftest import make_lap


def _cat(*pace_seconds_list):
    """Helper: build a CategorizedLaps with main laps at the given paces.

    Each argument is the target pace in s/km; we back-derive distance=1500m
    and elapsed_s = 1500m × pace / 1000 to make the test intent obvious.
    """
    main = tuple(
        make_lap(index=i, distance_m=1500.0, elapsed_s=1500.0 * pace / 1000.0, hr=150)
        for i, pace in enumerate(pace_seconds_list)
    )
    return CategorizedLaps(
        warmup=(), main=main, recovery=(), cooldown=(), other=()
    )


def test_main_lap_pace_is_per_km_seconds():
    # pace = 300 s/km (5:00/km) for 1500m → elapsed = 450s
    cat = _cat(300)
    ps = compute_pace_stats(cat.main, cat.all())
    assert ps.mean_s_per_km == pytest.approx(300.0)


def test_consistency_excellent_when_range_under_10s():
    cat = _cat(300, 305, 302)  # range 5s
    ps = compute_pace_stats(cat.main, cat.all())
    assert ps.range_s_per_km < 10
    assert ps.consistency == "excellent"


def test_consistency_good_when_range_under_20s():
    cat = _cat(300, 315)  # range 15s
    ps = compute_pace_stats(cat.main, cat.all())
    assert 10 <= ps.range_s_per_km < 20
    assert ps.consistency == "good"


def test_consistency_variable_when_range_over_20s():
    cat = _cat(300, 325)  # range 25s
    ps = compute_pace_stats(cat.main, cat.all())
    assert ps.range_s_per_km >= 20
    assert ps.consistency == "variable"


def test_aerobic_run_consistent_threshold_is_20s():
    """No main → uses 20s threshold for 'consistent'."""
    cat = CategorizedLaps(warmup=(), main=(), recovery=(), cooldown=(), other=(
        make_lap(index=0, distance_m=1000, elapsed_s=300),
        make_lap(index=1, distance_m=1000, elapsed_s=315),
    ))
    ps = compute_pace_stats(cat.main, cat.all())
    assert ps.range_s_per_km == 15
    assert ps.consistency == "consistent"


def test_trend_negative_split():
    cat = _cat(320, 310, 290, 280)  # getting faster
    ps = compute_pace_stats(cat.main, cat.all())
    assert ps.trend == "negative_split"


def test_trend_positive_split():
    cat = _cat(280, 290, 310, 320)  # getting slower
    ps = compute_pace_stats(cat.main, cat.all())
    assert ps.trend == "positive_split"


def test_trend_even_when_balanced():
    cat = _cat(300, 300, 300, 300)
    ps = compute_pace_stats(cat.main, cat.all())
    assert ps.trend == "even"


def test_empty_laps_return_safe_default():
    cat = CategorizedLaps(warmup=(), main=(), recovery=(), cooldown=(), other=())
    ps = compute_pace_stats(cat.main, cat.all())
    assert ps.mean_s_per_km == 0.0
    assert ps.consistency == "variable"


def test_pace_vs_goal_in_band():
    b = Baselines()  # marathon 3:48-3:52, target 230s
    pvg = compute_pace_vs_goal(actual_s_per_km=230.0, baselines=b)
    assert pvg.target_s_per_km == 230
    assert pvg.delta_s_per_km == 0
    assert pvg.matches is True


def test_pace_vs_goal_slow_by_5s_still_matches():
    b = Baselines()
    pvg = compute_pace_vs_goal(actual_s_per_km=235.0, baselines=b)  # 3:55, within 5s of 3:52
    assert pvg.matches is True


def test_pace_vs_goal_fast_by_5s_still_matches():
    b = Baselines()
    pvg = compute_pace_vs_goal(actual_s_per_km=223.0, baselines=b)  # 3:43, within 5s of 3:48
    assert pvg.matches is True


def test_pace_vs_goal_way_off_does_not_match():
    b = Baselines()
    pvg = compute_pace_vs_goal(actual_s_per_km=300.0, baselines=b)  # 5:00/km aerobic
    assert pvg.matches is False
    assert pvg.delta_s_per_km == 70
