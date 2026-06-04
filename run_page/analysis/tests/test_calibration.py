"""Tests for calibration.py — derive baselines from real Coros data."""
import math
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from run_page.analysis.calibration import (
    _derive_easy_pace,
    _derive_load_thresholds,
    _fetch_recent_activities,
    _median,
    _pace_band_s,
    _pace_str,
    _rolling_weekly_distance,
    _weekly_distance,
    calibrate_from_api,
)


# ---------- pure helpers ----------


def test_median_empty_returns_zero():
    assert _median([]) == 0.0


def test_median_basic():
    assert _median([300, 310, 320]) == 310.0


def test_pace_str_basic():
    assert _pace_str(228) == "3:48"
    assert _pace_str(284) == "4:44"
    assert _pace_str(60) == "1:00"


def test_pace_band_clamps_low():
    lo, hi = _pace_band_s(100, plus_minus=10)
    assert lo == 120  # clamped to min 2:00/km


def test_pace_band_default_5s():
    lo, hi = _pace_band_s(284, plus_minus=5)
    assert lo == 279
    assert hi == 289


# ---------- _derive_easy_pace ----------


def _act(avg_hr, avg_speed, days_ago=0):
    """Synthesize a Coros activity list entry."""
    ts = int((datetime.now() - timedelta(days=days_ago)).timestamp())
    return {
        "labelId": f"x-{days_ago}-{avg_hr}-{avg_speed}",
        "sportType": 100,
        "startTime": ts,
        "distance": 5000,
        "avgHr": avg_hr,
        "avgSpeed": avg_speed,  # Coros reports pace in s/km
        "trainingLoad": 80,
    }


def test_derive_easy_pace_filters_by_max_hr_threshold():
    acts = [
        _act(avg_hr=130, avg_speed=300, days_ago=0),  # easy
        _act(avg_hr=140, avg_speed=310, days_ago=1),  # easy
        _act(avg_hr=180, avg_speed=240, days_ago=2),  # hard, excluded
        _act(avg_hr=185, avg_speed=230, days_ago=3),  # hard, excluded
    ]
    pace, count = _derive_easy_pace(acts, max_hr=200)  # 75% × 200 = 150
    assert count == 2
    assert pace == pytest.approx(305.0)  # median of 300, 310


def test_derive_easy_pace_with_unknown_max_hr_falls_back_to_160():
    acts = [_act(avg_hr=155, avg_speed=300, days_ago=0)]
    pace, count = _derive_easy_pace(acts, max_hr=0)
    assert count == 1
    assert pace == 300.0


def test_derive_easy_pace_empty():
    assert _derive_easy_pace([], 200) == (0.0, 0)


# ---------- _weekly_distance / _rolling_weekly_distance ----------


def test_weekly_distance_7d():
    acts = [
        _act(avg_hr=150, avg_speed=300, days_ago=1),
        _act(avg_hr=150, avg_speed=300, days_ago=3),
        _act(avg_hr=150, avg_speed=300, days_ago=8),  # outside 7d window
    ]
    # 2 activities × 5000m = 10km
    assert _weekly_distance(acts, days_back=7) == pytest.approx(10.0)


def test_rolling_weekly_distance():
    acts = [
        _act(avg_hr=150, avg_speed=300, days_ago=i)
        for i in range(28)  # 4 weeks of one 5km run per day
    ]
    # 28 × 5km = 140km over 4 weeks = 35 km/week avg
    assert _rolling_weekly_distance(acts, weeks=4) == pytest.approx(35.0)


# ---------- _derive_load_thresholds ----------


def test_load_thresholds_always_empty_sop_dominates():
    """SOP ratio thresholds (1.3/1.0/0.8) are universal; per-user
    calibration would be noisy. So we always return {} and the caller
    falls back to SOP defaults."""
    acts = [_act(150, 300, days_ago=i) for i in range(20)]
    for i, a in enumerate(acts):
        a["trainingLoad"] = 50 + i * 10
    assert _derive_load_thresholds(acts) == {}


# ---------- calibrate_from_api with fake API ----------


def _fake_api(*, rhr=52, max_hr=196, lthr=173, ltsp=284, activities=None):
    api = MagicMock()
    api.account = "user@example.com"
    api._user_info = {
        "email": "user@example.com",
        "rhr": rhr,
        "maxHr": max_hr,
        "zoneData": {"lthr": lthr, "ltsp": ltsp},
    }
    api._get = MagicMock(
        return_value={"data": {"dataList": activities or []}}
    )
    return api


def test_calibrate_uses_login_values():
    api = _fake_api()
    result = calibrate_from_api(api)
    b = result.baselines
    assert b.owner == "user@example.com"
    # Marathon pace range derived from LTHR (LTSP=284 → 4:44/km, ± 5s)
    assert b.marathon_pace_range_str == ("4:39", "4:49")


def test_calibrate_falls_back_when_no_ltsp():
    api = _fake_api(ltsp=0)
    result = calibrate_from_api(api)
    assert "marathon_pace_range" in result.fallback


def test_calibrate_includes_derived_and_fallback():
    api = _fake_api()
    result = calibrate_from_api(api)
    assert "owner" in result.derived
    assert "hrv_baseline_ms (Coros API 不提供 HRV 端点)" in result.fallback
    assert "marathon_goal_str (race 目标需用户自填)" in result.fallback


def test_calibrate_includes_typical_pace_note_with_easy_runs():
    activities = [
        _act(avg_hr=140, avg_speed=300, days_ago=0),
        _act(avg_hr=145, avg_speed=310, days_ago=1),
        _act(avg_hr=150, avg_speed=305, days_ago=2),
    ]
    api = _fake_api(activities=activities)
    result = calibrate_from_api(api)
    notes_blob = " ".join(result.notes)
    assert "Typical easy pace" in notes_blob
    assert "5:00" in notes_blob or "5:05" in notes_blob or "5:10" in notes_blob


def test_calibrate_includes_weekly_volume_note():
    activities = [
        _act(avg_hr=150, avg_speed=300, days_ago=0),  # today
        _act(avg_hr=150, avg_speed=300, days_ago=1),  # yesterday
    ]
    # Pagination: first call returns the activities, subsequent calls return empty
    api = _fake_api(activities=activities)
    api._get = MagicMock(side_effect=[
        {"data": {"dataList": activities}},  # page 1
        {"data": {"dataList": []}},           # page 2 — empty, loop exits
    ])
    result = calibrate_from_api(api)
    notes_blob = " ".join(result.notes)
    assert "Recent 7d volume" in notes_blob
    assert "10.0" in notes_blob  # 2 × 5km
