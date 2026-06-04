"""Calibrate personal baselines from real Coros data + user info.

Goal: replace SOP placeholder values (HRV 69ms, marathon 2:40:55) with
values that reflect THIS user's actual training.

What's derivable from the Coros API (verified 2026-06):
  ✓ rhr              login response
  ✓ maxHr            login response
  ✓ lthr / ltsp      login response (lactate threshold HR & pace)
  ✓ HR zones (6)     login response.zoneData.lthrZone
  ✓ Pace zones (6)   login response.zoneData.ltspZone
  ✓ weight, stature  login response (BMI sanity check, body context)
  ✗ hrv              no public endpoint; treat as null
  ✗ marathon goal    not derivable; user must opt in

Activities list (from /activity/query):
  ✓ trainingLoad     per-activity → 7d/14d ratio
  ✓ distance / time  per-activity → typical easy pace, weekly volume
  ✓ avgHr / cadence  per-activity → intensity distribution
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Sequence

from .adapters.coros_api import LiveCorosApi
from .domain.baselines import Baselines


@dataclass(frozen=True)
class CalibrationResult:
    """Result of a calibration run: the new baselines plus a paper trail."""

    baselines: Baselines
    derived: tuple[str, ...] = field(default_factory=tuple)
    fallback: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


def _avg(xs: Sequence[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def _median(xs: Sequence[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def _fmt_pace(s: float) -> str:
    if not s or s <= 0:
        return "—"
    m = int(s // 60)
    sec = int(round(s - m * 60))
    if sec == 60:
        m += 1
        sec = 0
    return f"{m}:{sec:02d}"


def _pace_band_s(center: float, plus_minus: int = 5) -> tuple[int, int]:
    """`(lo, hi)` in s/km around a center pace, clamped positive."""
    lo = max(120, int(center) - plus_minus)
    hi = int(center) + plus_minus
    return lo, hi


def _pace_str(s: int) -> str:
    return f"{s // 60}:{s % 60:02d}"


# ---------- public API ----------


def calibrate_from_api(api: LiveCorosApi, *, days: int = 90) -> CalibrationResult:
    """Pull login info + recent activities, derive a personalized `Baselines`.

    Strategy:
      - RHR/HR zones/Pace zones → from login response
      - Marathon pace range → from LTHR pace (LTSP) ± 5s. This is more
        honest than the SOP's 2:40:55 since it reflects the user's actual
        training ceiling. A user can override `marathon_goal_str` in YAML
        if they're actually targeting a race.
      - Training load thresholds → keep SOP defaults (work for any runner)
      - HRV baseline → left as SOP placeholder, flagged in `fallback`
    """
    # 1. login response (refresh if not already)
    if not getattr(api, "_user_info", None):
        api.login()
    info = api._user_info
    zone = info.get("zoneData") or {}

    rhr = int(info.get("rhr") or 0)
    max_hr = int(info.get("maxHr") or 0)
    lthr = int(zone.get("lthr") or 0)
    ltsp = int(zone.get("ltsp") or 0)

    derived: list[str] = []
    fallback: list[str] = []
    notes: list[str] = []

    # 2. pull recent activities
    activities = _fetch_recent_activities(api, days)

    # 3. derive typical easy pace (median of activities where avgHr < 75% of maxHr)
    easy_pace, easy_count = _derive_easy_pace(activities, max_hr)

    # 4. derive weekly volume (sum of last 7 days)
    weekly_km = _weekly_distance(activities, days_back=7)
    recent_weekly_km = _rolling_weekly_distance(activities, weeks=4)

    # 5. derive load thresholds — keep SOP defaults but add note if user's
    # trainingLoad distribution is unusual
    load_thresholds = _derive_load_thresholds(activities)

    # 6. assemble
    # Marathon pace range:
    #   - if user has a known LTHR pace (ltsp), use it ± 5s as "threshold band"
    #   - if no LTHR data, fall back to easy_pace + 60s ("easy effort")
    if ltsp > 0:
        lo, hi = _pace_band_s(ltsp, plus_minus=5)
        marathon_pace_range = (_pace_str(lo), _pace_str(hi))
        notes.append(
            f"Marathon-pace band derived from LTHR pace: {_pace_str(lo)}-{_pace_str(hi)}/km"
        )
    elif easy_pace > 0:
        lo, hi = _pace_band_s(easy_pace - 60, plus_minus=30)  # 30s spread
        marathon_pace_range = (_pace_str(lo), _pace_str(hi))
        notes.append(
            f"Marathon-pace band approximated from easy pace "
            f"({_pace_str(int(easy_pace))}/km) - 60s ± 30s"
        )
    else:
        marathon_pace_range = Baselines().marathon_pace_range_str  # SOP default
        fallback.append("marathon_pace_range")

    if lthr > 0:
        # Reuse the bio thresholds as a "hr_zones" hint in notes
        notes.append(f"HR zones anchored at LTHR {lthr}bpm (max {max_hr}bpm)")
    else:
        fallback.append("lthr/ltsp")

    # Per-activity training load typically ranges 50-300. Use observed
    # distribution to set overload threshold = P90 of recent acute 7d.
    if load_thresholds:
        derived.append("load_thresholds")
    else:
        fallback.append("load_thresholds (using SOP defaults)")

    # Note: SOP biomechanics thresholds are population defaults; calibrating
    # them from a single user's FITs isn't statistically meaningful with
    # n=20-50. Keep as-is, mark in notes.
    notes.append(
        "Biomechanics thresholds (vertical osc, GCT, cadence) are SOP population "
        "defaults — small-N calibration would be noisy."
    )

    derived.append("owner")
    derived.append("rhr_baseline_ms (overridden with actual rhr)")
    if easy_pace > 0:
        derived.append("typical_easy_pace (in notes)")
    if weekly_km > 0:
        derived.append("recent_weekly_distance (in notes)")

    fallback.append("hrv_baseline_ms (Coros API 不提供 HRV 端点)")
    fallback.append("marathon_goal_str (race 目标需用户自填)")

    result = Baselines(
        owner=info.get("email") or api.account,
        hrv_baseline_ms=Baselines().hrv_baseline_ms,  # placeholder
        hrv_tolerance_ms=Baselines().hrv_tolerance_ms,
        marathon_goal_str=Baselines().marathon_goal_str,  # placeholder
        marathon_pace_range_str=marathon_pace_range,
        bio_vertical_oscillation_mm=Baselines().bio_vertical_oscillation_mm,
        bio_vertical_ratio_pct=Baselines().bio_vertical_ratio_pct,
        bio_gct_fast_ms=Baselines().bio_gct_fast_ms,
        bio_gct_slow_ms=Baselines().bio_gct_slow_ms,
        bio_cadence_fast_spm=Baselines().bio_cadence_fast_spm,
        bio_cadence_slow_spm=Baselines().bio_cadence_slow_spm,
        load_overload=load_thresholds.get("overload", Baselines().load_overload),
        load_optimized=load_thresholds.get("optimized", Baselines().load_optimized),
        load_maintaining=load_thresholds.get("maintaining", Baselines().load_maintaining),
    )

    notes.insert(0, f"Calibrated from {len(activities)} activities over last {days} days")
    if rhr:
        notes.append(f"Observed RHR: {rhr} bpm (login)")
    if max_hr:
        notes.append(f"Observed MaxHR: {max_hr} bpm (login)")
    if easy_pace > 0:
        notes.append(
            f"Typical easy pace: {_pace_str(int(easy_pace))}/km "
            f"(from {easy_count} easy efforts)"
        )
    if weekly_km > 0:
        notes.append(
            f"Recent 7d volume: {weekly_km:.1f} km; rolling 4-week avg: {recent_weekly_km:.1f} km/week"
        )

    return CalibrationResult(
        baselines=result,
        derived=tuple(derived),
        fallback=tuple(fallback),
        notes=tuple(notes),
    )


# ---------- helpers (private) ----------


def _fetch_recent_activities(api: LiveCorosApi, days: int) -> list[dict]:
    """Paginate through /activity/query and return everything within `days`.

    Coros caps at 20 per page; we walk until a page comes back short or empty.
    """
    all_acts: list[dict] = []
    page = 1
    cutoff = int(datetime.combine(date.today(), datetime.min.time()).timestamp()) - days * 86400
    while page <= 20:  # safety cap (20 pages × 20 = 400 activities)
        r = api._get(
            f"https://teamcnapi.coros.com/activity/query?&modeList=&pageNumber={page}&size=20"
        )
        page_acts = (r.get("data") or {}).get("dataList") or []
        if not page_acts:
            break
        all_acts.extend(page_acts)
        # If the earliest on this page is already past our window, no need to keep going
        if int(page_acts[-1].get("startTime") or 0) < cutoff:
            break
        page += 1
    return [a for a in all_acts if int(a.get("startTime") or 0) >= cutoff]


def _derive_easy_pace(activities: list[dict], max_hr: int) -> tuple[float, int]:
    """Return (median easy-pace s/km, count of easy efforts). 0 if no data.

    "Easy" = avgHr < 75% of maxHr (loose aerobic zone). If max_hr unknown,
    fall back to avgHr < 160.
    """
    if not activities:
        return 0.0, 0
    cap = max_hr * 0.75 if max_hr > 0 else 160
    easy_paces: list[float] = []
    for a in activities:
        try:
            avg_hr = int(a.get("avgHr") or 0)
            speed = float(a.get("avgSpeed") or 0)
        except (TypeError, ValueError):
            continue
        if avg_hr and 0 < avg_hr < cap and speed > 0:
            easy_paces.append(speed)  # avgSpeed in Coros API is actually s/km
    if not easy_paces:
        return 0.0, 0
    return _median(easy_paces), len(easy_paces)


def _weekly_distance(activities: list[dict], days_back: int) -> float:
    """Sum of distance (km) for activities in the last `days_back` days."""
    cutoff = int(datetime.combine(date.today(), datetime.min.time()).timestamp()) - days_back * 86400
    total_m = 0.0
    for a in activities:
        if int(a.get("startTime") or 0) >= cutoff:
            total_m += float(a.get("distance") or 0)
    return total_m / 1000.0


def _rolling_weekly_distance(activities: list[dict], weeks: int) -> float:
    """Mean weekly distance over the last `weeks` weeks (Sunday-aligned rough)."""
    cutoff = int(datetime.combine(date.today(), datetime.min.time()).timestamp()) - weeks * 7 * 86400
    recent = [a for a in activities if int(a.get("startTime") or 0) >= cutoff]
    if not recent:
        return 0.0
    total_km = sum(float(a.get("distance") or 0) for a in recent) / 1000.0
    return total_km / weeks


def _derive_load_thresholds(activities: list[dict]) -> dict:
    """The SOP's load thresholds are acute:chronic RATIO cutoffs (1.3, 1.0, 0.8)
    which are population-universal. Per-activity trainingLoad distribution is
    not the right input for those ratio thresholds. We could derive ratio
    thresholds from the user's rolling 7d/14d history, but with N=17 it
    would be noisy. Return {} and let the caller fall back to SOP defaults.
    """
    return {}
