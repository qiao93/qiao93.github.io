"""Read recent activities from the local SQLite for cross-run comparison.

The `activities` table holds the per-activity summary (distance, time, HR).
For "vs recent N sessions" comparisons in the report, we don't need FIT
details — just the aggregates the runner wants to compare.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data.db"


@dataclass(frozen=True)
class SessionSummary:
    """One row of the "vs recent N sessions" comparison table."""
    date: str               # YYYY-MM-DD
    distance_km: float
    duration_s: float
    avg_pace_s: float       # s/km
    avg_hr: Optional[int]
    activity_type: str      # 'Run', 'Hike', ...
    is_current: bool = False


def _engine(db_path: Path | None = None):
    return create_engine(f"sqlite:///{db_path or DEFAULT_DB}")


def recent_sessions(
    before_date: date,
    n: int = 5,
    activity_type: str = "Run",
    db_path: Path | None = None,
) -> list[SessionSummary]:
    """Return the `n` most recent activities of `activity_type` strictly
    before `before_date`, plus the requested `current` activity if any.
    """
    e = _engine(db_path)
    with e.connect() as c:
        rows = c.execute(
            text("""
                SELECT start_date_local, distance, moving_time, type
                FROM activities
                WHERE type = :t
                  AND date(start_date_local) < :d
                ORDER BY start_date_local DESC
                LIMIT :n
            """),
            {"t": activity_type, "d": before_date.isoformat(), "n": n},
        ).fetchall()
    out: list[SessionSummary] = []
    for start_str, dist_m, dur_s, atype in rows:
        # start_date_local is a string like '2026-05-04 19:17:33' from sqlite text
        d = _parse_dist(dist_m)
        # `moving_time` is an Interval column — SQLite may return it as a
        # string (e.g. "0:48:55") or timedelta. Coerce defensively.
        dur_s_safe = _to_seconds(dur_s)
        p = _parse_pace(d, dur_s_safe)
        out.append(SessionSummary(
            date=str(start_str)[:10],
            distance_km=d,
            duration_s=dur_s_safe,
            avg_pace_s=p,
            avg_hr=None,  # not stored in activities table
            activity_type=str(atype or ""),
            is_current=False,
        ))
    return out


def session_for(
    on_date: date,
    activity_type: str = "Run",
    db_path: Path | None = None,
) -> Optional[SessionSummary]:
    """Lookup the activity that took place on `on_date` (used to mark the
    current row in the comparison table). Returns None if no match.
    """
    e = _engine(db_path)
    with e.connect() as c:
        row = c.execute(
            text("""
                SELECT start_date_local, distance, moving_time, type
                FROM activities
                WHERE type = :t AND date(start_date_local) = :d
                ORDER BY start_date_local DESC
                LIMIT 1
            """),
            {"t": activity_type, "d": on_date.isoformat()},
        ).fetchone()
    if not row:
        return None
    start_str, dist_m, dur_s, atype = row
    d = _parse_dist(dist_m)
    dur_s_safe = _to_seconds(dur_s)
    return SessionSummary(
        date=str(start_str)[:10],
        distance_km=d,
        duration_s=dur_s_safe,
        avg_pace_s=_parse_pace(d, dur_s_safe),
        avg_hr=None,
        activity_type=str(atype or ""),
        is_current=True,
    )


def sessions_before(
    before_date: date,
    n: int = 50,
    activity_type: str = "Run",
    db_path: Path | None = None,
) -> list[SessionSummary]:
    """Return the N most recent `activity_type` sessions strictly before
    `before_date`, ordered oldest → newest. Used for 4-week trend
    aggregation (where we want all sessions, not just a windowed count).
    """
    e = _engine(db_path)
    with e.connect() as c:
        rows = c.execute(
            text("""
                SELECT start_date_local, distance, moving_time, type
                FROM activities
                WHERE type = :t
                  AND date(start_date_local) < :d
                ORDER BY start_date_local DESC
                LIMIT :n
            """),
            {"t": activity_type, "d": before_date.isoformat(), "n": n},
        ).fetchall()
    out: list[SessionSummary] = []
    for start_str, dist_m, dur_s, atype in rows:
        d = _parse_dist(dist_m)
        dur_s_safe = _to_seconds(dur_s)
        out.append(SessionSummary(
            date=str(start_str)[:10],
            distance_km=d,
            duration_s=dur_s_safe,
            avg_pace_s=_parse_pace(d, dur_s_safe),
            avg_hr=None,
            activity_type=str(atype or ""),
            is_current=False,
        ))
    # Return oldest → newest (chronological) so bucketing reads naturally
    return list(reversed(out))


def _parse_dist(m) -> float:
    try:
        return float(m) / 1000.0 if m else 0.0
    except (TypeError, ValueError):
        return 0.0


def _to_seconds(v) -> float:
    """Coerce an Interval column value to seconds.

    SQLite DATETIME columns may come back as:
      - a Python `datetime` (the running_page schema uses DATETIME for
        moving_time/elapsed_time, and the time is encoded as
        "1970-01-01 HH:MM:SS.sss" — i.e. time-of-day since epoch)
      - a `timedelta`
      - a string like "0:48:55" or "48:55"
      - a plain number of seconds
    All four are handled below.
    """
    if v is None or v == "":
        return 0.0
    if isinstance(v, datetime):
        t = v.time()
        return float(t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1_000_000)
    if isinstance(v, time):
        return float(v.hour * 3600 + v.minute * 60 + v.second + v.microsecond / 1_000_000)
    if isinstance(v, (int, float)):
        return float(v)
    if hasattr(v, "total_seconds"):  # timedelta
        try:
            return float(v.total_seconds())
        except Exception:  # noqa: BLE001
            return 0.0
    s = str(v).strip()
    # ISO-style datetime: "1970-01-01 00:50:11.810000" — strip the date.
    # The time-of-day on a 1970-01-01 epoch is the actual duration.
    if len(s) >= 11 and (s[10] == " " or s[10] == "T") and s[:4].isdigit():
        s = s[11:]
    # "H:MM:SS" or "HH:MM:SS" or "H:MM:SS.fff"
    if ":" in s:
        parts = s.split(":")
        try:
            h, m, sec = (float(p) for p in parts)
            return h * 3600 + m * 60 + sec
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _parse_pace(dist_km: float, dur_s) -> float:
    d = _to_seconds(dur_s)
    if dist_km <= 0 or d <= 0:
        return 0.0
    return d / dist_km
