"""Read/write helpers for the `coros_id_map` table.

This table joins the three worlds:
  - Coros's `labelId`         (used by the Coros API for activity detail)
  - Strava-style `run_id`     (used by `activities` table; timestamp-based int)
  - FIT filename              (Coros's hash-based basename, what's on disk)

The mapping is written by `coros_sync.py` immediately after each successful
download, so analysis can go from a DB row → FIT file → Coros API call.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text

# Project-relative DB path; matches coros_sync.py's SQL_FILE
DEFAULT_DB = Path(__file__).resolve().parents[2] / "data.db"


@dataclass(frozen=True)
class IdMapEntry:
    label_id: str
    run_id: int | None
    fit_filename: str
    sport_type: int | None
    downloaded_at: datetime | None


def _engine(db_path: Path | None = None):
    return create_engine(f"sqlite:///{db_path or DEFAULT_DB}")


def _ensure_schema(engine) -> None:
    with engine.begin() as c:
        c.execute(
            text("""
                CREATE TABLE IF NOT EXISTS coros_id_map (
                    label_id TEXT PRIMARY KEY,
                    run_id INTEGER,
                    fit_filename TEXT NOT NULL,
                    sport_type INTEGER,
                    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        )


def record(
    label_id: str,
    fit_filename: str,
    sport_type: int | None = None,
    run_id: int | None = None,
    db_path: Path | None = None,
) -> None:
    """Upsert a single mapping. Idempotent: re-running sync is safe."""
    e = _engine(db_path)
    _ensure_schema(e)
    with e.begin() as c:
        c.execute(
            text("""
                INSERT INTO coros_id_map (label_id, run_id, fit_filename, sport_type)
                VALUES (:label_id, :run_id, :fit_filename, :sport_type)
                ON CONFLICT(label_id) DO UPDATE SET
                    run_id       = COALESCE(excluded.run_id,       coros_id_map.run_id),
                    fit_filename = excluded.fit_filename,
                    sport_type   = COALESCE(excluded.sport_type,   coros_id_map.sport_type),
                    downloaded_at = CURRENT_TIMESTAMP
            """),
            {
                "label_id": label_id,
                "run_id": run_id,
                "fit_filename": fit_filename,
                "sport_type": sport_type,
            },
        )


def all_entries(db_path: Path | None = None) -> list[IdMapEntry]:
    """Return every mapping, ordered by downloaded_at desc."""
    e = _engine(db_path)
    _ensure_schema(e)
    with e.connect() as c:
        rows = c.execute(
            text("""
                SELECT label_id, run_id, fit_filename, sport_type, downloaded_at
                FROM coros_id_map
                ORDER BY datetime(downloaded_at) DESC, label_id DESC
            """)
        ).fetchall()
    return [
        IdMapEntry(
            label_id=r[0],
            run_id=r[1],
            fit_filename=r[2],
            sport_type=r[3],
            downloaded_at=datetime.fromisoformat(r[4]) if r[4] else None,
        )
        for r in rows
    ]


def latest(limit: int = 5, db_path: Path | None = None) -> list[IdMapEntry]:
    """Return the N most recent mappings."""
    return all_entries(db_path)[:limit]


def since(ts: datetime, db_path: Path | None = None) -> list[IdMapEntry]:
    """Mappings downloaded strictly after `ts`."""
    e = _engine(db_path)
    _ensure_schema(e)
    with e.connect() as c:
        rows = c.execute(
            text("""
                SELECT label_id, run_id, fit_filename, sport_type, downloaded_at
                FROM coros_id_map
                WHERE datetime(downloaded_at) > :ts
                ORDER BY datetime(downloaded_at) DESC
            """),
            {"ts": ts.isoformat()},
        ).fetchall()
    return [
        IdMapEntry(r[0], r[1], r[2], r[3],
                   datetime.fromisoformat(r[4]) if r[4] else None)
        for r in rows
    ]


def link_to_run_id(label_id: str, run_id: int, db_path: Path | None = None) -> None:
    """Backfill: once the FIT parser has produced a run_id, link it to label_id."""
    e = _engine(db_path)
    _ensure_schema(e)
    with e.begin() as c:
        c.execute(
            text("UPDATE coros_id_map SET run_id = :rid WHERE label_id = :lid"),
            {"rid": run_id, "lid": label_id},
        )


def lookup_run_id(label_id: str, db_path: Path | None = None) -> int | None:
    e = _engine(db_path)
    _ensure_schema(e)
    with e.connect() as c:
        r = c.execute(
            text("SELECT run_id FROM coros_id_map WHERE label_id = :lid"),
            {"lid": label_id},
        ).fetchone()
    return r[0] if r else None


def count(db_path: Path | None = None) -> int:
    e = _engine(db_path)
    _ensure_schema(e)
    with e.connect() as c:
        return c.execute(text("SELECT count(*) FROM coros_id_map")).scalar() or 0
