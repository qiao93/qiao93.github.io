"""All public data structures for the analysis module.

These are pure data — no methods, no I/O. They are intentionally
frozen and hashable so they can be used as dict keys and compared
across runs in tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


# ---------- inputs ----------


@dataclass(frozen=True)
class Lap:
    """A single FIT Lap record.

    Speeds are stored in m/s (FIT native) and pace is derived as needed
    so we never have two representations of the same thing drifting.
    """

    index: int
    distance_m: float
    elapsed_s: float
    avg_heart_rate: int | None
    max_heart_rate: int | None
    avg_speed_mps: float | None
    avg_running_cadence_spm: int | None  # already doubled (FIT reports single-foot)
    avg_vertical_oscillation_mm: float | None
    avg_ground_contact_time_ms: float | None
    avg_vertical_ratio_pct: float | None
    start_time: datetime | None


@dataclass(frozen=True)
class ActivityMeta:
    label_id: str
    sport_type: int  # 100 = outdoor run, 101 = indoor, 102 = trail, 103 = track
    start_date_local: datetime
    total_distance_m: float
    total_elapsed_s: float
    location: str | None
    account: str | None = None  # which Coros account produced this activity (for ownership check)


@dataclass(frozen=True)
class BodyStateSnapshot:
    """Aggregated 7-day metrics ending on activity date."""

    hrv_today_ms: int
    rhr_today_bpm: int
    hrv_baseline_ms: int  # copied from Baselines for self-containment
    load_ratio: float  # acute:chronic


# ---------- derived (computed by domain) ----------


class LapCategory(str, Enum):
    WARMUP = "warmup"
    MAIN = "main"
    RECOVERY = "recovery"
    COOLDOWN = "cooldown"
    OTHER = "other"


@dataclass(frozen=True)
class CategorizedLaps:
    warmup: tuple[Lap, ...]
    main: tuple[Lap, ...]
    recovery: tuple[Lap, ...]
    cooldown: tuple[Lap, ...]
    other: tuple[Lap, ...]

    def all(self) -> tuple[Lap, ...]:
        return self.warmup + self.main + self.recovery + self.cooldown + self.other

    def by_category(self) -> dict[LapCategory, tuple[Lap, ...]]:
        return {
            LapCategory.WARMUP: self.warmup,
            LapCategory.MAIN: self.main,
            LapCategory.RECOVERY: self.recovery,
            LapCategory.COOLDOWN: self.cooldown,
            LapCategory.OTHER: self.other,
        }


@dataclass(frozen=True)
class PaceStats:
    mean_s_per_km: float
    range_s_per_km: float
    trend: str  # "even" | "negative_split" | "positive_split" | "mixed"
    consistency: str  # "excellent" | "good" | "variable"


@dataclass(frozen=True)
class HrStats:
    mean_bpm: float
    max_bpm: int
    drift_bpm: float  # last_main.avg_hr - first_main.avg_hr (0 if no main)
    drift_grade: str  # "excellent" | "good" | "needs_work"


@dataclass(frozen=True)
class BioDelta:
    cadence_spm_first: int | None
    cadence_spm_last: int | None
    vertical_osc_first_mm: float | None
    vertical_osc_last_mm: float | None
    gct_first_ms: float | None
    gct_last_ms: float | None
    fatigue_grade: str  # "none" | "mild" | "notable"


@dataclass(frozen=True)
class PaceVsGoal:
    target_s_per_km: int  # mid-point of marathon band
    actual_s_per_km: float
    delta_s_per_km: float
    matches: bool  # within ±5s of band


@dataclass(frozen=True)
class ActivityMetrics:
    categorized: CategorizedLaps
    pace: PaceStats
    hr: HrStats
    bio: BioDelta
    pace_vs_goal: PaceVsGoal
    body_state: BodyStateSnapshot
    recommendations: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisReport:
    meta: ActivityMeta
    metrics: ActivityMetrics
    recent_sessions: tuple = ()  # 5 most recent prior activities for the "vs recent N" table
    # 4-week trend aggregates (Phase 4 part 1). Optional so legacy callers
    # that don't compute it still work; the renderer omits the section
    # when this is None.
    trend: "TrendReport | None" = None
    markdown: str = ""  # rendered Markdown, set by presentation layer


# utility used by scoring / presentation


def pace_seconds_per_km(speed_mps: float | None) -> float:
    """Convert m/s to s/km. Returns +inf for missing/zero speed (treated as walking)."""
    if not speed_mps or speed_mps <= 0:
        return float("inf")
    return 1000.0 / speed_mps


def format_pace(s_per_km: float) -> str:
    """`s_per_km` → `'3:48/km'` style string. Returns `'—'` for inf."""
    if s_per_km == float("inf") or s_per_km != s_per_km:  # inf or NaN
        return "—"
    minutes = int(s_per_km // 60)
    seconds = int(round(s_per_km - minutes * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}/km"
