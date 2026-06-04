"""Tests for domain/models.py utilities (format_pace, pace_seconds_per_km)."""
import math

from run_page.analysis.domain import format_pace, pace_seconds_per_km


def test_pace_seconds_per_km_basic():
    # 5 m/s → 200 s/km
    assert pace_seconds_per_km(5.0) == 200.0


def test_pace_seconds_per_km_walking_speed():
    # 1.4 m/s → ~714 s/km
    assert pace_seconds_per_km(1.4) == pytest_approx(714.285, 0.01)


def test_pace_seconds_per_km_zero_returns_inf():
    assert pace_seconds_per_km(0) == math.inf
    assert pace_seconds_per_km(None) == math.inf
    assert pace_seconds_per_km(-1) == math.inf


def test_format_pace_minutes_seconds():
    assert format_pace(228.0) == "3:48/km"
    assert format_pace(300.0) == "5:00/km"
    assert format_pace(232.0) == "3:52/km"


def test_format_pace_rounds_seconds():
    # 230.6 should round to 31 → 3:51
    assert format_pace(230.6) == "3:51/km"


def test_format_pace_carries_over_to_next_minute():
    # 179.5 → rounds to 180 → 3:00 (not 2:60)
    assert format_pace(179.5) == "3:00/km"


def test_format_pace_inf_returns_em_dash():
    assert format_pace(math.inf) == "—"


def pytest_approx(value, tol=0.0):
    import pytest
    return pytest.approx(value, abs=tol)
