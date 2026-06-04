"""Shared pytest fixtures for the analysis module.

The `lap` factory is the workhorse: tests build synthetic `Lap` records
without filling in fields they don't care about.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

import pytest

from run_page.analysis.domain import Lap


def make_lap(
    index: int = 0,
    distance_m: float = 1000.0,
    elapsed_s: float = 300.0,
    hr: int | None = None,
    max_hr: int | None = None,
    speed_mps: float | None = None,
    cadence: int | None = None,
    vosc: float | None = None,
    gct: float | None = None,
    vratio: float | None = None,
) -> Lap:
    if speed_mps is None and elapsed_s > 0:
        speed_mps = distance_m / elapsed_s
    return Lap(
        index=index,
        distance_m=distance_m,
        elapsed_s=elapsed_s,
        avg_heart_rate=hr,
        max_heart_rate=max_hr,
        avg_speed_mps=speed_mps,
        avg_running_cadence_spm=cadence,
        avg_vertical_oscillation_mm=vosc,
        avg_ground_contact_time_ms=gct,
        avg_vertical_ratio_pct=vratio,
        start_time=datetime(2026, 5, 16, 18, 21),
    )


@pytest.fixture
def lap():
    return make_lap


def laps(*specs) -> list[Lap]:
    """`laps((dist, secs, hr), (dist, secs, hr), ...)` shorthand."""
    out = []
    for i, (d, s, hr) in enumerate(specs):
        out.append(make_lap(index=i, distance_m=float(d), elapsed_s=float(s), hr=hr))
    return out
