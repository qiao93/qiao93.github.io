"""Tests for domain/baselines.py — YAML loading and defaults."""
import textwrap
from pathlib import Path

import pytest

from run_page.analysis.domain.baselines import Baselines, load_baselines


def test_defaults_when_file_missing(tmp_path: Path):
    b = load_baselines(tmp_path / "nonexistent.yaml")
    assert b.hrv_baseline_ms == 69
    assert b.marathon_goal_str == "2:40:55"
    assert b.marathon_pace_range_str == ("3:48", "3:52")
    assert b.bio_vertical_oscillation_mm == (60.0, 70.0)


def test_loads_from_yaml(tmp_path: Path):
    yaml = tmp_path / "b.yaml"
    yaml.write_text(textwrap.dedent("""
        hrv_baseline_ms: 75
        hrv_tolerance_ms: 7
        marathon_goal: "3:00:00"
        marathon_pace_range: ["4:00", "4:10"]
        biomechanics:
          vertical_oscillation_mm: [55, 65]
          vertical_ratio_pct:     [5.0, 7.5]
          gct_fast_ms:            [185, 205]
          gct_slow_ms:            [205, 225]
          cadence_fast_spm:       [175, 170]
        load:
          overload:    1.4
          optimized:   1.05
          maintaining: 0.85
    """))
    b = load_baselines(yaml)
    assert b.hrv_baseline_ms == 75
    assert b.hrv_tolerance_ms == 7
    assert b.marathon_goal_str == "3:00:00"
    assert b.marathon_pace_range_str == ("4:00", "4:10")
    assert b.bio_vertical_oscillation_mm == (55.0, 65.0)
    assert b.bio_vertical_ratio_pct == (5.0, 7.5)
    assert b.bio_gct_fast_ms == (185.0, 205.0)
    assert b.bio_gct_slow_ms == (205.0, 225.0)
    assert b.bio_cadence_fast_spm == (175, 170)
    assert b.load_overload == 1.4
    assert b.load_optimized == 1.05
    assert b.load_maintaining == 0.85


def test_derived_marathon_pace_in_seconds_per_km():
    b = Baselines()  # 3:48-3:52 → 228-232
    assert b.marathon_pace_range_s_per_km == (228, 232)
    assert b.marathon_pace_target_s_per_km == 230


def test_partial_yaml_uses_defaults_for_missing_fields(tmp_path: Path):
    yaml = tmp_path / "b.yaml"
    yaml.write_text("hrv_baseline_ms: 80\n")
    b = load_baselines(yaml)
    assert b.hrv_baseline_ms == 80
    # Everything else from defaults
    assert b.marathon_goal_str == "2:40:55"
    assert b.bio_vertical_oscillation_mm == (60.0, 70.0)
