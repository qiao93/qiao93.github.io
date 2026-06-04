"""Personal baselines — the only file in domain/ that does I/O.

The dataclass `Baselines` itself is pure data; this module loads it from
`baselines.yaml`. If the YAML is missing we fall back to the dataclass
defaults that mirror the SOP §3-§4 thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _parse_pace(s: str) -> int:
    """`'3:48'` → 228 (seconds per km)."""
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


@dataclass(frozen=True)
class Baselines:
    """All thresholds the SOP codifies. See SPEC §2.1 / §6."""

    # Which account / runner these baselines were calibrated for. When set,
    # the renderer cross-checks against `ActivityMeta.account` and emits a
    # warning if the data and the baselines are from different people.
    # Set to `None` to skip the check (e.g. shared / team-wide defaults).
    owner: str | None = None

    hrv_baseline_ms: int = 69
    hrv_tolerance_ms: int = 5
    marathon_goal_str: str = "2:40:55"
    marathon_pace_range_str: tuple[str, str] = ("3:48", "3:52")
    # (excellent_threshold, good_threshold). Lower is better for these.
    bio_vertical_oscillation_mm: tuple[float, float] = (60.0, 70.0)
    bio_vertical_ratio_pct: tuple[float, float] = (5.5, 8.0)
    bio_gct_fast_ms: tuple[float, float] = (190.0, 210.0)
    bio_gct_slow_ms: tuple[float, float] = (210.0, 230.0)
    # Cadence: higher is better. Tuple is (excellent_lo, good_lo).
    bio_cadence_fast_spm: tuple[int, int] = (172, 168)
    bio_cadence_slow_spm: tuple[int, int] = (168, 160)
    load_overload: float = 1.3
    load_optimized: float = 1.0
    load_maintaining: float = 0.8

    @property
    def marathon_pace_range_s_per_km(self) -> tuple[int, int]:
        return (
            _parse_pace(self.marathon_pace_range_str[0]),
            _parse_pace(self.marathon_pace_range_str[1]),
        )

    @property
    def marathon_pace_target_s_per_km(self) -> int:
        lo, hi = self.marathon_pace_range_s_per_km
        return (lo + hi) // 2

    def with_hrv_baseline(self, value: int) -> "Baselines":
        return _replace(self, hrv_baseline_ms=value)


def _replace(b: Baselines, **changes) -> Baselines:
    """dataclasses.replace doesn't work on frozen without it; we re-build."""
    from dataclasses import replace

    return replace(b, **changes)


DEFAULT_PATH = Path(__file__).resolve().parent.parent / "baselines.yaml"


def load_baselines(path: Path | None = None) -> Baselines:
    """Load baselines from YAML. Returns defaults if file is missing.

    Raises ValueError if the file exists but is malformed.
    """
    p = path or DEFAULT_PATH
    if not p.exists():
        return Baselines()

    raw = yaml.safe_load(p.read_text()) or {}
    bio = raw.get("biomechanics", {}) or {}
    load = raw.get("load", {}) or {}

    return Baselines(
        owner=(str(raw["owner"]) if "owner" in raw else None),
        hrv_baseline_ms=int(raw.get("hrv_baseline_ms", 69)),
        hrv_tolerance_ms=int(raw.get("hrv_tolerance_ms", 5)),
        marathon_goal_str=str(raw.get("marathon_goal", "2:40:55")),
        marathon_pace_range_str=tuple(
            raw.get("marathon_pace_range", ["3:48", "3:52"])
        ),
        bio_vertical_oscillation_mm=tuple(bio.get("vertical_oscillation_mm", (60.0, 70.0))),
        bio_vertical_ratio_pct=tuple(bio.get("vertical_ratio_pct", (5.5, 8.0))),
        bio_gct_fast_ms=tuple(bio.get("gct_fast_ms", (190.0, 210.0))),
        bio_gct_slow_ms=tuple(bio.get("gct_slow_ms", (210.0, 230.0))),
        bio_cadence_fast_spm=tuple(bio.get("cadence_fast_spm", (172, 168))),
        bio_cadence_slow_spm=tuple(bio.get("cadence_slow_spm", (168, 160))),
        load_overload=float(load.get("overload", 1.3)),
        load_optimized=float(load.get("optimized", 1.0)),
        load_maintaining=float(load.get("maintaining", 0.8)),
    )
