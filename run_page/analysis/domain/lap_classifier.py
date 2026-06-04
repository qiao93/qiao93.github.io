"""Lap classification per SPEC §4.1.

Pure function: list[Lap] → CategorizedLaps.

Rules (from SOP §2 Step 2):
  - i == 0 and distance_m > 1500  → warmup
  - distance_m >= 1500 and pace < 4:00/km  → main
  - distance_m >= 1500 and pace >= 4:00/km → cooldown
  - distance_m < 200             → recovery
  - else                         → other
"""
from __future__ import annotations

from .models import CategorizedLaps, Lap, LapCategory, pace_seconds_per_km


def _is_high_intensity(pace_s: float) -> bool:
    return pace_s < 240  # 4:00/km


def classify_laps(laps: list[Lap]) -> CategorizedLaps:
    buckets: dict[LapCategory, list[Lap]] = {c: [] for c in LapCategory}
    for lap in laps:
        pace = pace_seconds_per_km(lap.avg_speed_mps)
        if lap.index == 0 and lap.distance_m > 1500:
            tag = LapCategory.WARMUP
        elif lap.distance_m >= 1500 and _is_high_intensity(pace):
            tag = LapCategory.MAIN
        elif lap.distance_m >= 1500 and not _is_high_intensity(pace):
            tag = LapCategory.COOLDOWN
        elif lap.distance_m < 200:
            tag = LapCategory.RECOVERY
        else:
            tag = LapCategory.OTHER
        buckets[tag].append(lap)

    return CategorizedLaps(
        warmup=tuple(buckets[LapCategory.WARMUP]),
        main=tuple(buckets[LapCategory.MAIN]),
        recovery=tuple(buckets[LapCategory.RECOVERY]),
        cooldown=tuple(buckets[LapCategory.COOLDOWN]),
        other=tuple(buckets[LapCategory.OTHER]),
    )
