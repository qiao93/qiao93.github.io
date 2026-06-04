"""Body-state evaluation and recommendations per SPEC §4.4 / §4.5."""
from __future__ import annotations

from enum import Enum

from .baselines import Baselines
from .models import BioDelta, BodyStateSnapshot, HrStats, PaceStats


class BodyStateGrade(str, Enum):
    WELL_RECOVERED = "well_recovered"
    NORMAL = "normal"
    MILD_FATIGUE = "mild_fatigue"
    HIGH_FATIGUE = "high_fatigue"


class LoadGrade(str, Enum):
    OVERLOAD = "overload"
    OPTIMIZED = "optimized"
    MAINTAINING = "maintaining"
    UNDERTRAINED = "undertrained"


def grade_hrv(snap: BodyStateSnapshot, baselines: Baselines) -> BodyStateGrade:
    # hrv_today_ms == 0 means "API didn't return HRV today"; don't pretend
    # we measured 0 and emit a fatigue alert.
    if snap.hrv_today_ms <= 0:
        return BodyStateGrade.NORMAL  # treat as neutral; renderer surfaces "不可用"
    delta = snap.hrv_today_ms - snap.hrv_baseline_ms
    tol = baselines.hrv_tolerance_ms
    if delta > tol:
        return BodyStateGrade.WELL_RECOVERED
    if abs(delta) <= tol:
        return BodyStateGrade.NORMAL
    if snap.hrv_today_ms >= snap.hrv_baseline_ms - 15:
        return BodyStateGrade.MILD_FATIGUE
    return BodyStateGrade.HIGH_FATIGUE


def grade_load(ratio: float, baselines: Baselines) -> LoadGrade:
    if ratio > baselines.load_overload:
        return LoadGrade.OVERLOAD
    if ratio >= baselines.load_optimized:
        return LoadGrade.OPTIMIZED
    if ratio >= baselines.load_maintaining:
        return LoadGrade.MAINTAINING
    return LoadGrade.UNDERTRAINED


def grade_body_state(snap: BodyStateSnapshot, baselines: Baselines):
    """Convenience: return (hrv_grade, load_grade) tuple."""
    return grade_hrv(snap, baselines), grade_load(snap.load_ratio, baselines)


def _format_pace_delta(delta_s: float) -> str:
    sign = "+" if delta_s >= 0 else "−"
    return f"{sign}{abs(delta_s):.0f}s"


def recommendations(
    pace: PaceStats,
    hr: HrStats,
    bio: BioDelta,
    snap: BodyStateSnapshot,
    baselines: Baselines,
) -> tuple[str, ...]:
    """Generate 0-3 short, deterministic recommendation bullets.

    Priority order: HRV fatigue > load overload > HR drift > biomech fatigue.
    """
    bullets: list[str] = []

    hrv_g = grade_hrv(snap, baselines)
    load_g = grade_load(snap.load_ratio, baselines)

    if hrv_g == BodyStateGrade.HIGH_FATIGUE:
        bullets.append(
            f"HRV {snap.hrv_today_ms}ms 明显低于基准 {baselines.hrv_baseline_ms}ms，"
            f"建议仅轻松跑或完全休息。"
        )
    elif hrv_g == BodyStateGrade.MILD_FATIGUE:
        bullets.append(
            f"HRV {snap.hrv_today_ms}ms 轻度低于基准，"
            f"考虑降一档强度或拉长热身。"
        )

    if load_g == LoadGrade.OVERLOAD:
        bullets.append(
            f"训练负荷比值 {snap.load_ratio:.2f} 超过 {baselines.load_overload:.1f}，"
            f"过载风险，本周降量。"
        )

    if hr.drift_grade == "needs_work":
        bullets.append(
            f"主课心率漂移 {hr.drift_bpm:+.0f}bpm 偏大（>20），"
            f"提示有氧基础或补水/天气因素。"
        )

    if bio.fatigue_grade == "notable":
        delta_cad = (bio.cadence_spm_first or 0) - (bio.cadence_spm_last or 0)
        delta_vosc = (bio.vertical_osc_last_mm or 0) - (bio.vertical_osc_first_mm or 0)
        bullets.append(
            f"末段生物力学退化：步频 {delta_cad:+d}spm、振幅 {delta_vosc:+.0f}mm，"
            f"末段体能或技术下降。"
        )

    return tuple(bullets)
