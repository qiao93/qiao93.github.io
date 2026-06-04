"""FIT file → list[Lap].

Thin wrapper around `fitparse` that:
  - filters to Lap messages
  - normalizes field names to our `Lap` dataclass
  - drops zero-distance laps (Coros sometimes inserts them as markers)
  - is forgiving: missing fields become None, never raises
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fitparse import FitFile

from ..domain import Lap


# FIT field name → our attribute name. Values that need conversion
# (e.g. cadence single→double) are handled in _build_lap.
_FIELD_MAP = {
    "total_distance": "distance_m",  # FIT is in cm or m depending on version
    "total_elapsed_time": "elapsed_s",
    "avg_heart_rate": "avg_heart_rate",
    "max_heart_rate": "max_heart_rate",
    "avg_speed": "avg_speed_mps",
    "avg_running_cadence": "avg_running_cadence_spm",  # doubled below
    "avg_vertical_oscillation": "avg_vertical_oscillation_mm",
    "avg_ground_contact_time": "avg_ground_contact_time_ms",
    "avg_vertical_ratio": "avg_vertical_ratio_pct",
    "start_time": "start_time",
}


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        f = _to_float(v)
        return int(f) if f is not None else None


def _to_dt(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_lap(index: int, fields: dict) -> Lap | None:
    dist = _to_float(fields.get("total_distance")) or 0.0
    # FIT can report distance in m directly (Garmin) or 100*m (some Coros exports).
    # Heuristic: anything > 1e6 is in cm.
    if dist > 1_000_000:
        dist = dist / 100.0

    elapsed = _to_float(fields.get("total_elapsed_time")) or 0.0
    if dist < 1.0 and elapsed < 1.0:
        return None  # zero-lap marker

    cad = _to_int(fields.get("avg_running_cadence"))
    if cad is not None and cad > 0:
        cad = cad * 2  # FIT reports single-foot, double for display per SOP

    # vertical ratio in FIT is a fraction (0.072 = 7.2%); convert to percent
    vratio = _to_float(fields.get("avg_vertical_ratio"))
    if vratio is not None and vratio <= 1.0:
        vratio = vratio * 100.0

    return Lap(
        index=index,
        distance_m=dist,
        elapsed_s=elapsed,
        avg_heart_rate=_to_int(fields.get("avg_heart_rate")),
        max_heart_rate=_to_int(fields.get("max_heart_rate")),
        avg_speed_mps=_to_float(fields.get("avg_speed")),
        avg_running_cadence_spm=cad,
        avg_vertical_oscillation_mm=_to_float(fields.get("avg_vertical_oscillation")),
        avg_ground_contact_time_ms=_to_float(fields.get("avg_ground_contact_time")),
        avg_vertical_ratio_pct=vratio,
        start_time=_to_dt(fields.get("start_time")),
    )


def parse_fit_laps(fit_path: Path) -> list[Lap]:
    """Parse a .fit file and return a list of Laps in order."""
    ff = FitFile(str(fit_path))
    out: list[Lap] = []
    for idx, msg in enumerate(ff.get_messages("lap")):
        fields = {f.name: f.value for f in msg.fields}
        lap = _build_lap(idx, fields)
        if lap is not None:
            out.append(lap)
    return out
