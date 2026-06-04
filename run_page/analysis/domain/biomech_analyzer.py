"""Biomechanics delta: compare first vs last main lap (or first vs last of all)."""
from __future__ import annotations

from .models import BioDelta, Lap


def _abs_delta_score(delta: float, threshold: float) -> str:
    """Map absolute delta to a fatigue grade."""
    if delta <= threshold * 0.5:
        return "none"
    if delta <= threshold:
        return "mild"
    return "notable"


def compute_biomech_delta(
    main_laps: tuple[Lap, ...], all_laps: tuple[Lap, ...]
) -> BioDelta:
    pool = list(main_laps) if len(main_laps) >= 2 else list(all_laps)
    if len(pool) < 2:
        first, last = (pool[0], pool[0]) if pool else (None, None)
    else:
        first, last = pool[0], pool[-1]

    if first is None:
        return BioDelta(None, None, None, None, None, None, "none")

    cad_first = first.avg_running_cadence_spm
    cad_last = last.avg_running_cadence_spm
    vosc_first = first.avg_vertical_oscillation_mm
    vosc_last = last.avg_vertical_oscillation_mm
    gct_first = first.avg_ground_contact_time_ms
    gct_last = last.avg_ground_contact_time_ms

    # Fatigue heuristics: cadence drops, vosc rises, gct rises.
    cad_drop = (cad_first - cad_last) if (cad_first and cad_last) else 0
    vosc_rise = (vosc_last - vosc_first) if (vosc_first and vosc_last) else 0.0
    gct_rise = (gct_last - gct_first) if (gct_first and gct_last) else 0.0

    # Aggregate: pick worst of three. Cadence drop is the most reliable signal.
    worst = "none"
    if cad_drop > 6 or vosc_rise > 4 or gct_rise > 25:
        worst = "notable"
    elif cad_drop > 3 or vosc_rise > 2 or gct_rise > 12:
        worst = "mild"

    return BioDelta(
        cadence_spm_first=cad_first,
        cadence_spm_last=cad_last,
        vertical_osc_first_mm=vosc_first,
        vertical_osc_last_mm=vosc_last,
        gct_first_ms=gct_first,
        gct_last_ms=gct_last,
        fatigue_grade=worst,
    )
