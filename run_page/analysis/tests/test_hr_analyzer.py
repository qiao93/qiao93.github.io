"""Tests for domain/hr_analyzer.py."""
import pytest

from run_page.analysis.domain import (
    CategorizedLaps,
    compute_hr_drift,
)
from run_page.analysis.tests.conftest import make_lap


def _main_laps(*hrs):
    return tuple(
        make_lap(index=i, distance_m=1500, elapsed_s=300, hr=h, max_hr=h + 10)
        for i, h in enumerate(hrs)
    )


def test_drift_excellent_under_15():
    main = _main_laps(150, 155, 160)  # drift +10
    cat = CategorizedLaps(warmup=(), main=main, recovery=(), cooldown=(), other=())
    hr = compute_hr_drift(cat.main, cat.all())
    assert hr.drift_bpm == 10
    assert hr.drift_grade == "excellent"


def test_drift_good_between_15_and_20():
    main = _main_laps(150, 168)  # drift +18
    cat = CategorizedLaps(warmup=(), main=main, recovery=(), cooldown=(), other=())
    hr = compute_hr_drift(cat.main, cat.all())
    assert 15 <= hr.drift_bpm < 20
    assert hr.drift_grade == "good"


def test_drift_needs_work_over_20():
    main = _main_laps(150, 175)  # drift +25
    cat = CategorizedLaps(warmup=(), main=main, recovery=(), cooldown=(), other=())
    hr = compute_hr_drift(cat.main, cat.all())
    assert hr.drift_bpm >= 20
    assert hr.drift_grade == "needs_work"


def test_drift_zero_when_no_main():
    # Falls back to all laps; with a single lap, drift is 0
    cat = CategorizedLaps(warmup=(), main=(), recovery=(), cooldown=(), other=(
        make_lap(index=0, distance_m=1000, elapsed_s=300, hr=140, max_hr=150),
    ))
    hr = compute_hr_drift(cat.main, cat.all())
    assert hr.drift_bpm == 0
    assert hr.drift_grade == "excellent"


def test_mean_hr_averaged():
    main = _main_laps(140, 160, 180)
    cat = CategorizedLaps(warmup=(), main=main, recovery=(), cooldown=(), other=())
    hr = compute_hr_drift(cat.main, cat.all())
    assert hr.mean_bpm == pytest.approx(160.0)


def test_max_hr_uses_max_field():
    main = (
        make_lap(index=0, distance_m=1500, elapsed_s=300, hr=150, max_hr=180),
        make_lap(index=1, distance_m=1500, elapsed_s=300, hr=160, max_hr=190),
    )
    cat = CategorizedLaps(warmup=(), main=main, recovery=(), cooldown=(), other=())
    hr = compute_hr_drift(cat.main, cat.all())
    assert hr.max_bpm == 190


def test_missing_hr_data_returns_safe_defaults():
    cat = CategorizedLaps(warmup=(), main=(
        make_lap(index=0, distance_m=1500, elapsed_s=300, hr=None, max_hr=None),
    ), recovery=(), cooldown=(), other=())
    hr = compute_hr_drift(cat.main, cat.all())
    assert hr.mean_bpm == 0.0
    assert hr.max_bpm == 0
    assert hr.drift_grade == "good"
