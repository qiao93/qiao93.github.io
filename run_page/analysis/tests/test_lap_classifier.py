"""Tests for domain/lap_classifier.py.

Rules (SPEC §4.1 / SOP §2):
  - i == 0 and distance_m > 1500          → warmup
  - distance_m >= 1500 and pace < 4:00/km  → main
  - distance_m >= 1500 and pace >= 4:00/km → cooldown
  - distance_m < 200                       → recovery
  - else                                   → other
"""
from run_page.analysis.domain import LapCategory, classify_laps
from run_page.analysis.tests.conftest import make_lap


def test_first_lap_over_1500m_is_warmup():
    laps = [make_lap(index=0, distance_m=1600, elapsed_s=480)]
    cat = classify_laps(laps)
    assert len(cat.warmup) == 1
    assert cat.warmup[0].index == 0
    assert cat.main == ()
    assert cat.cooldown == ()


def test_first_lap_under_1500m_is_other():
    # 1000m is not warmup-sized; gets classified per the other rules
    laps = [make_lap(index=0, distance_m=1000, elapsed_s=300)]
    cat = classify_laps(laps)
    assert cat.warmup == ()
    assert cat.main == ()  # 1000 < 1500m
    assert cat.cooldown == ()
    assert len(cat.other) == 1


def test_high_pace_long_lap_is_main():
    # 1500m at 3:30/km = 210s/km, 315s lap
    laps = [make_lap(index=1, distance_m=1500, elapsed_s=315)]
    cat = classify_laps(laps)
    assert len(cat.main) == 1
    assert cat.cooldown == ()


def test_slow_pace_long_lap_is_cooldown():
    # 1500m at 4:40/km (280 s/km) — well above 4:00 cutoff to dodge float noise
    laps = [make_lap(index=1, distance_m=1500, elapsed_s=420)]
    cat = classify_laps(laps)
    assert cat.main == ()
    assert len(cat.cooldown) == 1


def test_short_lap_is_recovery():
    # 150m recovery jog
    laps = [make_lap(index=1, distance_m=150, elapsed_s=80)]
    cat = classify_laps(laps)
    assert len(cat.recovery) == 1
    assert cat.main == ()
    assert cat.cooldown == ()


def test_medium_distance_low_pace_is_other():
    # 800m at 5:00/km — too short for main/cooldown, too long for recovery
    laps = [make_lap(index=1, distance_m=800, elapsed_s=240)]
    cat = classify_laps(laps)
    assert cat.main == ()
    assert cat.cooldown == ()
    assert cat.recovery == ()
    assert len(cat.other) == 1


def test_mixed_workout_classifies_each_segment():
    laps = [
        make_lap(index=0, distance_m=2000, elapsed_s=720),     # warmup
        make_lap(index=1, distance_m=1600, elapsed_s=320),     # main (3:20/km)
        make_lap(index=2, distance_m=1600, elapsed_s=324),     # main
        make_lap(index=3, distance_m=150, elapsed_s=60),       # recovery
        make_lap(index=4, distance_m=2000, elapsed_s=800),     # cooldown (6:40/km)
    ]
    cat = classify_laps(laps)
    assert len(cat.warmup) == 1
    assert len(cat.main) == 2
    assert len(cat.recovery) == 1
    assert len(cat.cooldown) == 1
    assert cat.other == ()


def test_zero_speed_lap_does_not_crash():
    # Lap with no speed (e.g. paused) should be classed by distance alone.
    # index=1 so the warmup rule (i==0 and dist>1500) does not preempt.
    lap = make_lap(index=1, distance_m=2000, elapsed_s=600, speed_mps=0)
    cat = classify_laps([lap])
    # 0 m/s → pace is inf → falls through to cooldown (>= 4:00/km)
    assert len(cat.cooldown) == 1
    assert cat.main == ()


def test_categorized_laps_all_returns_all_in_order():
    laps = [
        make_lap(index=0, distance_m=1600, elapsed_s=480),
        make_lap(index=1, distance_m=100, elapsed_s=30),
    ]
    cat = classify_laps(laps)
    indices = [l.index for l in cat.all()]
    assert indices == [0, 1]
