"""Tests for domain/scoring.py — HRV 4-tier, Load 4-tier, recommendations."""
import pytest

from run_page.analysis.domain import (
    BioDelta,
    BodyStateSnapshot,
    CategorizedLaps,
    HrStats,
    PaceStats,
)
from run_page.analysis.domain.baselines import Baselines
from run_page.analysis.domain.scoring import (
    BodyStateGrade,
    LoadGrade,
    grade_hrv,
    grade_load,
    recommendations,
)
from run_page.analysis.tests.conftest import make_lap


# ---------- HRV ----------


def test_hrv_well_recovered_when_above_tolerance():
    b = Baselines()  # baseline 69, tolerance 5
    snap = BodyStateSnapshot(hrv_today_ms=80, rhr_today_bpm=50, hrv_baseline_ms=69, load_ratio=1.0)
    assert grade_hrv(snap, b) == BodyStateGrade.WELL_RECOVERED


def test_hrv_normal_within_tolerance():
    b = Baselines()
    snap = BodyStateSnapshot(hrv_today_ms=72, rhr_today_bpm=50, hrv_baseline_ms=69, load_ratio=1.0)
    assert grade_hrv(snap, b) == BodyStateGrade.NORMAL


def test_hrv_mild_fatigue_within_15ms_below():
    b = Baselines()
    snap = BodyStateSnapshot(hrv_today_ms=58, rhr_today_bpm=50, hrv_baseline_ms=69, load_ratio=1.0)  # -11
    assert grade_hrv(snap, b) == BodyStateGrade.MILD_FATIGUE


def test_hrv_high_fatigue_below_15ms():
    b = Baselines()
    snap = BodyStateSnapshot(hrv_today_ms=48, rhr_today_bpm=50, hrv_baseline_ms=69, load_ratio=1.0)  # -21
    assert grade_hrv(snap, b) == BodyStateGrade.HIGH_FATIGUE


# ---------- Load ----------


def test_load_overload_above_threshold():
    b = Baselines()  # overload 1.3
    assert grade_load(1.4, b) == LoadGrade.OVERLOAD


def test_load_optimized_at_or_above_one():
    b = Baselines()
    assert grade_load(1.0, b) == LoadGrade.OPTIMIZED
    assert grade_load(1.2, b) == LoadGrade.OPTIMIZED


def test_load_maintaining_between_08_and_1():
    b = Baselines()
    assert grade_load(0.9, b) == LoadGrade.MAINTAINING


def test_load_undertrained_below_08():
    b = Baselines()
    assert grade_load(0.5, b) == LoadGrade.UNDERTRAINED


# ---------- Recommendations ----------


def _empty_metrics(hr_drift: float = 5.0, fatigue: str = "none", hrv_today: int = 70, load: float = 1.0):
    main = (make_lap(index=0, distance_m=1500, elapsed_s=300, hr=150),) if hr_drift else ()
    cat = CategorizedLaps(warmup=(), main=main, recovery=(), cooldown=(), other=())
    return cat, PaceStats(mean_s_per_km=300.0, range_s_per_km=5.0, trend="even", consistency="excellent"), \
        HrStats(mean_bpm=150, max_bpm=160, drift_bpm=hr_drift, drift_grade="excellent"), \
        BioDelta(cadence_spm_first=180, cadence_spm_last=180, vertical_osc_first_mm=60, vertical_osc_last_mm=60,
                 gct_first_ms=200, gct_last_ms=200, fatigue_grade=fatigue), \
        BodyStateSnapshot(hrv_today_ms=hrv_today, rhr_today_bpm=50, hrv_baseline_ms=69, load_ratio=load)


def test_recommendations_empty_when_all_good():
    cat, pace, hr, bio, snap = _empty_metrics(hrv_today=72, load=1.0, hr_drift=5)
    recs = recommendations(pace, hr, bio, snap, Baselines())
    assert recs == ()


def test_recommendations_for_high_fatigue():
    cat, pace, hr, bio, snap = _empty_metrics(hrv_today=48, load=1.0)  # -21ms
    recs = recommendations(pace, hr, bio, snap, Baselines())
    assert len(recs) >= 1
    assert any("明显低于" in r for r in recs)


def test_recommendations_for_overload():
    cat, pace, hr, bio, snap = _empty_metrics(hrv_today=72, load=1.5)  # > 1.3
    recs = recommendations(pace, hr, bio, snap, Baselines())
    assert any("过载" in r or "降量" in r for r in recs)


def test_recommendations_for_hr_drift():
    cat, pace, hr, bio, snap = _empty_metrics(hrv_today=72, load=1.0)
    hr_obj = HrStats(mean_bpm=150, max_bpm=180, drift_bpm=25, drift_grade="needs_work")
    recs = recommendations(pace, hr_obj, bio, snap, Baselines())
    assert any("心率漂移" in r for r in recs)


def test_recommendations_for_notable_biomech_fatigue():
    cat, pace, hr, bio, snap = _empty_metrics(hrv_today=72, load=1.0, fatigue="notable")
    recs = recommendations(pace, hr, bio, snap, Baselines())
    assert any("生物力学退化" in r for r in recs)
