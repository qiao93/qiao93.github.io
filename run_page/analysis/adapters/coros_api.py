"""Coros API adapter.

This is the *Port* — application code depends on `CorosApiPort`. The
concrete `LiveCorosApi` talks to the real endpoints; tests can inject
a fake that returns canned data.

Endpoints verified live (2026-06):
  ✓ POST /account/login                       → token + user info (rhr, maxHr, zones)
  ✓ GET  /activity/query                      → activity list (per-activity HR/cadence/load)
  ✓ POST /activity/detail/download            → FIT file URL
  ✗ POST /activity/detail, /activity/info     → HTTP 500 (apiCode 5C4D208)
  ✗ GET  /hrv/assessment, /rhr/assessment     → HTTP 500
  ✗ GET  /v2/hrv/..., /v2/rhr/...             → HTTP 500
  ✗ GET  /training/load, /user/info           → HTTP 500

Practical consequence: we can compute everything from login + /activity/query
EXCEPT per-day HRV. body_state() returns hrv_today_ms=0 and the renderer
flags it as "不可用".
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy.exc import SQLAlchemyError

from ..domain import ActivityMeta, BodyStateSnapshot

REPO_ROOT = Path(__file__).resolve().parents[3]


# Default endpoint base (matches coros_sync.py).
BASE = "https://teamcnapi.coros.com"


# ---------- port ----------


class CorosApiPort(Protocol):
    def activity_meta(self, label_id: str) -> ActivityMeta: ...
    def body_state(self, on_date: date, baseline_hrv_ms: int) -> BodyStateSnapshot: ...
    def fit_path(self, label_id: str) -> Path: ...


# ---------- live implementation ----------


class CorosApiError(RuntimeError):
    pass


class LiveCorosApi:
    """Thin Coros HTTP client. Login is required first (via login()).

    Public methods are synchronous (blocking) for simplicity — they're
    called from a single thread inside the CLI. If we later need to run
    inside an async context, wrap with `asyncio.to_thread`.
    """

    def __init__(self, account: str, password_plain: str, *, cache_dir: Path | None = None):
        self.account = account
        self.password_md5 = hashlib.md5(password_plain.encode()).hexdigest()
        self._token: str | None = None
        self._user_info: dict = {}  # cache for rhr/maxHr from login response
        self._client = httpx.Client(timeout=30.0)
        self._cache_dir = cache_dir  # optional: persist JSON snapshots for replay
        self._cache_dir.mkdir(parents=True, exist_ok=True) if cache_dir else None

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *exc):
        self._client.close()

    # ----- low level -----

    def _headers(self) -> dict:
        if not self._token:
            raise CorosApiError("not logged in")
        return {
            "accesstoken": self._token,
            "cookie": f"CPL-coros-region=2; CPL-coros-token={self._token}",
            "origin": "https://t.coros.com",
            "referer": "https://t.coros.com/",
            "user-agent": "running_page-analysis/1.0",
        }

    def _get(self, url: str) -> dict:
        r = self._client.get(url, headers=self._headers())
        if r.status_code != 200:
            raise CorosApiError(f"GET {url} → HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def _post(self, url: str, body: dict) -> dict:
        r = self._client.post(url, json=body, headers=self._headers())
        if r.status_code != 200:
            raise CorosApiError(f"POST {url} → HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    # ----- login -----

    def login(self) -> None:
        body = {"account": self.account, "accountType": 2, "pwd": self.password_md5}
        r = self._client.post(
            f"{BASE}/account/login",
            json=body,
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "origin": "https://t.coros.com",
                "referer": "https://t.coros.com/",
                "user-agent": "running_page-analysis/1.0",
            },
        )
        if r.status_code != 200:
            raise CorosApiError(f"login HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json().get("data") or {}
        except json.JSONDecodeError as exc:
            raise CorosApiError(f"login returned non-JSON: {r.text[:200]}") from exc
        token = data.get("accessToken")
        if not token:
            raise CorosApiError(f"login succeeded but no accessToken: {r.json()}")
        self._token = token
        self._user_info = data  # stash full response for rhr/maxHr/zones

    # ----- port impl -----

    def activity_meta(self, label_id: str) -> ActivityMeta:
        """Look up activity metadata from /activity/query or fall back to
        the local activities table if the API is unavailable.

        Note: /activity/detail (HTTP 500, apiCode 5C4D208) is broken and
        must not be called. Use /activity/query?labelId= instead.
        """
        # 1. Try /activity/query — this is the working endpoint per SOP §七
        try:
            r = self._get(f"{BASE}/activity/query?&modeList=&pageNumber=1&size=50")
            activities = (r.get("data") or {}).get("dataList") or []
            for a in activities:
                if str(a.get("labelId")) == label_id:
                    return ActivityMeta(
                        label_id=label_id,
                        sport_type=int(a.get("sportType", 100)),
                        start_date_local=_parse_dt(a.get("startTime")),
                        total_distance_m=float(a.get("distance") or 0),
                        total_elapsed_s=float(a.get("duration") or 0),
                        location=a.get("location"),
                        account=self.account,
                    )
        except CorosApiError:
            pass  # fall through to local DB

        # 2. Fall back to local SQLite activities table
        from sqlalchemy import create_engine, text

        db_path = REPO_ROOT / "run_page" / "data.db"
        e = create_engine(f"sqlite:///{db_path}")
        try:
            run_id = int(label_id)
        except ValueError:
            run_id = None

        with e.connect() as c:
            if run_id is not None:
                row = c.execute(
                    text("SELECT start_date_local, distance, moving_time FROM activities WHERE run_id = :rid"),
                    {"rid": run_id},
                ).fetchone()
            else:
                row = None

        if row is not None:
            start_local = row[0]
            dist = float(row[1]) if row[1] else 0.0
            # moving_time is a DATETIME string like "1970-01-01 00:48:55.480000"
            # — treat it as elapsed seconds-of-day (same convention as
            # activity_history._to_seconds for duration columns).
            dur_s = _to_seconds(row[2]) if row[2] else 0.0
            return ActivityMeta(
                label_id=label_id,
                sport_type=100,
                start_date_local=_parse_dt(start_local) if isinstance(start_local, str) else start_local,
                total_distance_m=dist,
                total_elapsed_s=dur_s,
                location=None,
                account=self.account,
            )

        # 3. Nothing found → raise
        raise CorosApiError(f"activity_meta: no data for labelId={label_id} (API failed and no local record)")

    def body_state(self, on_date: date, baseline_hrv_ms: int) -> BodyStateSnapshot:
        """Aggregate body-state metrics for the day of the activity.

        Reality of the current Coros API (verified 2026-06):
          - HRV: no public endpoint returns it (5C4D208 on all variants).
            We surface `hrv_today_ms = 0` and the renderer flags "不可用".
          - RHR: returned once at login, not per-day. We use the login value.
          - Training load: per-activity `trainingLoad` field in the list.
            We compute acute:chronic as sum(7d) / sum(prior 7d).
        """
        # Pull recent activities (30 days) and bucket by 7-day windows.
        from datetime import timedelta
        r = self._get(f"{BASE}/activity/query?&modeList=&pageNumber=1&size=50")
        activities = (r.get("data") or {}).get("dataList") or []

        end_ts = int(datetime.combine(on_date, datetime.max.time()).timestamp())
        acute_sum = 0.0
        chronic_sum = 0.0
        for a in activities:
            try:
                st = int(a.get("startTime") or 0)
            except (TypeError, ValueError):
                continue
            tl = float(a.get("trainingLoad") or 0)
            if st >= end_ts - 7 * 86400 and st <= end_ts:
                acute_sum += tl
            elif st >= end_ts - 14 * 86400 and st < end_ts - 7 * 86400:
                chronic_sum += tl

        load_ratio = (acute_sum / chronic_sum) if chronic_sum > 0 else 1.0

        # RHR from cached user_info (populated at login)
        rhr_today = int(self._user_info.get("rhr") or 0) if hasattr(self, "_user_info") else 0
        if not rhr_today:
            # Fall back to a fresh login if the cache was lost
            self.login()
            rhr_today = int(self._user_info.get("rhr") or 0)

        return BodyStateSnapshot(
            hrv_today_ms=0,  # explicitly unavailable; renderer will flag
            rhr_today_bpm=rhr_today,
            hrv_baseline_ms=baseline_hrv_ms,
            load_ratio=load_ratio,
        )

    def fit_path(self, label_id: str) -> Path:
        """Look up the actual FIT filename from coros_id_map, then return the
        on-disk path. Falls back to a date-based scan of the most-recently-
        modified FIT files (skipping the slow full-directory scan).
        """
        from . import id_map

        db_path = REPO_ROOT / "run_page" / "data.db"
        FIT_OUT = REPO_ROOT / "FIT_OUT"

        # 1. Try id_map first (correct for all Phase-5+ entries)
        try:
            for entry in id_map.all_entries(db_path=db_path):
                if entry.label_id == label_id and entry.fit_filename:
                    return FIT_OUT / entry.fit_filename
        except SQLAlchemyError:
            pass

        # 2. Fallback for pre-Phase-5 entries: look up activity by run_id
        # (labelId and run_id are the same timestamp-based int in legacy data)
        from sqlalchemy import create_engine, text

        try:
            run_id = int(label_id)
        except ValueError:
            return FIT_OUT / f"{label_id}.fit"

        e = create_engine(f"sqlite:///{db_path}")
        try:
            with e.connect() as c:
                row = c.execute(
                    text("SELECT start_date_local FROM activities WHERE run_id = :rid"),
                    {"rid": run_id},
                ).fetchone()
        except SQLAlchemyError:
            row = None

        if row is not None:
            target_date = str(row[0])[:10]  # YYYY-MM-DD
            # Scan only the 100 most recently modified FIT files (newest first).
            # Today's run will be among them; scanning 100 vs 674 cuts time ~6x.
            recent_fits = sorted(FIT_OUT.glob("*.fit"), key=lambda p: -p.stat().st_mtime)[:100]
            from .fit_parser import parse_fit_laps

            for fp in recent_fits:
                try:
                    laps = parse_fit_laps(fp)
                    if laps and laps[0].start_time:
                        if laps[0].start_time.strftime("%Y-%m-%d") == target_date:
                            return fp
                except Exception:
                    continue

        # 3. Last resort: assume labelId is the filename stem
        return FIT_OUT / f"{label_id}.fit"


# ---------- helpers ----------


def _parse_dt(v) -> "datetime | None":
    from datetime import datetime

    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000.0)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_seconds(v) -> float:
    """Coerce a value to float seconds.

    Handles:
      - float/int → returned as-is
      - datetime object → .timestamp()
      - "HH:MM:SS.fff" or "YYYY-MM-DD HH:MM:SS.fff" → strip date prefix if
        present, then parse as timedelta from midnight
    """
    from datetime import datetime, timedelta

    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, datetime):
        return v.timestamp()
    s = str(v)
    # Strip date prefix if present: "1970-01-01 00:48:55.480000" → "00:48:55.480000"
    if len(s) >= 11 and (s[10] == " " or s[10] == "T") and s[:4].isdigit():
        s = s[11:]
    try:
        parts = s.split(":")
        if len(parts) == 3:
            h, m, sec = float(parts[0]), float(parts[1]), float(parts[2])
            return h * 3600 + m * 60 + sec
        elif len(parts) == 2:
            m, sec = float(parts[0]), float(parts[1])
            return m * 60 + sec
        else:
            return float(s)
    except (ValueError, IndexError):
        return 0.0
