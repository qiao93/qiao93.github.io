"""CLI entry point: `python -m run_page.analysis.cli`.

Examples:
    python -m run_page.analysis.cli --label-id 465911765287337995
    python -m run_page.analysis.cli --latest 5
    python -m run_page.analysis.cli --all
    python -m run_page.analysis.cli --label-id X --out run_page/analyses/foo.md
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .adapters import AnalysisStore, LiveCorosApi
from .adapters.coros_api import CorosApiError
from .application import RunAnalyzer
from .domain import AnalysisReport, load_baselines
from .presentation.markdown_renderer import render_report
import json
import shutil


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "run_page" / "analyses"
FIT_DIR = REPO_ROOT / "FIT_OUT"
SQL_DB = REPO_ROOT / "run_page" / "data.db"
IMPORTED_JSON = REPO_ROOT / "imported.json"


def _load_label_ids_from_db(limit: int | None) -> list[tuple[str, str]]:
    """Read (label_id, start_date_local) from the id_map table, descending by date.

    Prefers the new `coros_id_map` table (Phase 5); falls back to the
    legacy `activities` table for users who haven't run a sync with the
    new coros_sync yet.
    """
    from sqlalchemy import create_engine, text

    e = create_engine(f"sqlite:///{SQL_DB}")
    # Try the new id_map first (Phase 5+).
    with e.connect() as c:
        has_id_map = c.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='coros_id_map'")
        ).fetchone() is not None
    if has_id_map:
        from .adapters import id_map
        entries = id_map.latest(limit or 10_000_000, db_path=SQL_DB)
        if entries:
            return [(e.label_id, "") for e in entries]
        # id_map table exists but is empty (old data, pre-Phase 5 sync) —
        # fall through to the legacy path.

    # Legacy fallback: synthesize label_id from run_id (won't round-trip with Coros
    # but at least lets the CLI run end-to-end on old data).
    sql = "SELECT run_id, start_date_local FROM activities WHERE type='Run' ORDER BY start_date_local DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with e.connect() as c:
        rows = c.execute(text(sql)).fetchall()
    return [(str(r[0]), r[1]) for r in rows]


def _resolve_account() -> tuple[str, str]:
    account = os.environ.get("COROS_ACCOUNT")
    password = os.environ.get("COROS_PASSWORD")
    if account and password:
        return account, password
    raise SystemExit(
        "Set COROS_ACCOUNT and COROS_PASSWORD env vars (use a password manager)."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="run-analysis",
        description="Generate per-run Markdown analysis from FIT + Coros 7-day data.",
    )
    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument("--label-id", help="Analyze a single Coros labelId.")
    src.add_argument("--latest", type=int, help="Analyze the N most recent runs from data.db.")
    src.add_argument("--all", action="store_true", help="Analyze every Run in data.db.")
    src.add_argument(
        "--since-last-sync",
        action="store_true",
        help="Only analyze runs downloaded after the last successful sync (uses imported.json mtime).",
    )
    p.add_argument(
        "--calibrate",
        action="store_true",
        help="Pull login + recent activities from Coros, derive a personalized Baselines, "
             "and write to --baselines. Skips the analysis step.",
    )
    p.add_argument(
        "--calibrate-dry-run",
        action="store_true",
        help="Like --calibrate, but print what would change instead of writing.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output directory (default: {DEFAULT_OUT})",
    )
    p.add_argument(
        "--baselines",
        type=Path,
        default=None,
        help="Path to baselines.yaml (default: analysis/baselines.yaml)",
    )
    p.add_argument(
        "--narrative",
        action="store_true",
        help="After facts.md, also generate AI narrative (requires ANTHROPIC_API_KEY).",
    )
    args = p.parse_args(argv)

    account, password = _resolve_account()
    baselines = load_baselines(args.baselines)
    store = AnalysisStore(args.out)

    def _run_calibration(
        account: str,
        password: str,
        baselines_path: Path | None,
        dry_run: bool,
    ) -> None:
        """Run the calibrate-from-API flow and optionally write the merged baselines."""
        from .calibration import calibrate_from_api
        from .domain.baselines import Baselines as _B
        import yaml

        with LiveCorosApi(account, password) as api:
            result = calibrate_from_api(api)
        b = result.baselines
        print("\n=== calibration result ===")
        print(f"  owner:                {b.owner!r}")
        print(f"  marathon_pace_range:  {b.marathon_pace_range_str[0]}–{b.marathon_pace_range_str[1]}/km")
        print(f"  load_overload:        {b.load_overload}")
        print(f"  load_optimized:       {b.load_optimized}")
        print(f"  load_maintaining:     {b.load_maintaining}")
        print(f"\n  derived:   {', '.join(result.derived)}")
        print(f"  fallback:  {', '.join(result.fallback)}")
        print(f"\n  notes:")
        for n in result.notes:
            print(f"    - {n}")

        if not dry_run:
            yaml_path = baselines_path or (REPO_ROOT / "run_page" / "analysis" / "baselines.yaml")
            existing = load_baselines(yaml_path) if yaml_path.exists() else _B()
            merged = _B(
                owner=b.owner,
                hrv_baseline_ms=existing.hrv_baseline_ms,
                hrv_tolerance_ms=existing.hrv_tolerance_ms,
                marathon_goal_str=existing.marathon_goal_str,
                marathon_pace_range_str=b.marathon_pace_range_str,
                bio_vertical_oscillation_mm=existing.bio_vertical_oscillation_mm,
                bio_vertical_ratio_pct=existing.bio_vertical_ratio_pct,
                bio_gct_fast_ms=existing.bio_gct_fast_ms,
                bio_gct_slow_ms=existing.bio_gct_slow_ms,
                bio_cadence_fast_spm=existing.bio_cadence_fast_spm,
                bio_cadence_slow_spm=existing.bio_cadence_slow_spm,
                load_overload=b.load_overload,
                load_optimized=b.load_optimized,
                load_maintaining=b.load_maintaining,
            )
            data = {
                "owner": merged.owner,
                "hrv_baseline_ms": merged.hrv_baseline_ms,
                "hrv_tolerance_ms": merged.hrv_tolerance_ms,
                "marathon_goal": merged.marathon_goal_str,
                "marathon_pace_range": list(merged.marathon_pace_range_str),
                "biomechanics": {
                    "vertical_oscillation_mm": list(merged.bio_vertical_oscillation_mm),
                    "vertical_ratio_pct":     list(merged.bio_vertical_ratio_pct),
                    "gct_fast_ms":            list(merged.bio_gct_fast_ms),
                    "gct_slow_ms":            list(merged.bio_gct_slow_ms),
                    "cadence_fast_spm":       list(merged.bio_cadence_fast_spm),
                    "cadence_slow_spm":       list(merged.bio_cadence_slow_spm),
                },
                "load": {
                    "overload":    merged.load_overload,
                    "optimized":   merged.load_optimized,
                    "maintaining": merged.load_maintaining,
                },
            }
            yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
            print(f"\n✓ wrote {yaml_path}")

    # Calibrate is a stand-alone operation; doesn't need an analysis target.
    if args.calibrate or args.calibrate_dry_run:
        _run_calibration(account, password, args.baselines, args.calibrate_dry_run)
        return 0

    targets: list[str]
    if not any([args.label_id, args.latest, args.all, args.since_last_sync]):
        p.error("one of --label-id / --latest / --all / --since-last-sync is required (or use --calibrate)")
    if args.label_id:
        targets = [args.label_id]
    elif args.latest:
        targets = [lid for lid, _ in _load_label_ids_from_db(args.latest)]
    elif args.since_last_sync:
        from datetime import datetime
        from .adapters import id_map
        # imported.json mtime ≈ last successful sync
        mtime = datetime.fromtimestamp(IMPORTED_JSON.stat().st_mtime) if IMPORTED_JSON.exists() else datetime.min
        entries = id_map.since(mtime, db_path=SQL_DB)
        targets = [e.label_id for e in entries]
        if not targets:
            print(f"no new mappings since {mtime.isoformat()}", file=sys.stderr)
    else:
        targets = [lid for lid, _ in _load_label_ids_from_db(None)]

    if not targets:
        print("no activities found", file=sys.stderr)
        return 1

    successes = 0
    failures: list[tuple[str, str]] = []

    # Try to log in to Coros. If the API is rate-limited (e.g. anti-brute-force
    # lockout, apiCode 41C2B95C) we still want to produce analyses for each
    # FIT — just without the body_state (HRV/RHR/load) numbers.
    api: LiveCorosApi | None = None
    api_unavailable_reason: str | None = None
    try:
        api = LiveCorosApi(account, password)
        api.__enter__()
    except CorosApiError as exc:
        api_unavailable_reason = str(exc)
        print(f"[warn] Coros API unavailable: {exc}", file=sys.stderr)
        print("  analyses will use synthetic body state (HRV=0, RHR=0, load=1.0)", file=sys.stderr)

    try:
        analyzer = RunAnalyzer(api, baselines) if api else None
        for label_id in targets:
            try:
                if analyzer is not None:
                    report = analyzer.analyze(label_id)
                else:
                    report = _analyze_without_api(label_id, baselines, account)
            except FileNotFoundError as exc:
                failures.append((label_id, f"FIT not found: {exc}"))
                continue
            except CorosApiError as exc:
                print(f"  [warn] {label_id}: Coros API error: {exc}; using fallback", file=sys.stderr)
                try:
                    report = _analyze_without_api(label_id, baselines, account)
                except Exception as exc2:  # noqa: BLE001
                    failures.append((label_id, f"fallback also failed: {exc2}"))
                    continue
            except Exception as exc:  # noqa: BLE001
                failures.append((label_id, f"{type(exc).__name__}: {exc}"))
                continue

            path, sparkline_paths = store.save_report(report, report.markdown)
            # Also dump facts.json so the narrative CLI / external tools can
            # consume the structured data without re-parsing the markdown.
            facts_path = _write_facts_json(report, path)
            try:
                rel = path.relative_to(REPO_ROOT)
            except ValueError:
                rel = path  # path is outside REPO_ROOT (e.g. test tmp dir)
            print(
                f"✓ {report.meta.start_date_local.strftime('%Y-%m-%d')} "
                f"{report.meta.total_distance_m/1000:.2f}km → {rel}"
            )

            # Optional Layer 3 narrative
            if args.narrative and os.environ.get("ANTHROPIC_API_KEY"):
                try:
                    import subprocess
                    nargs = [
                        sys.executable,
                        "-m", "run_page.analysis.narrative",
                        "--facts", str(facts_path) if facts_path else "",
                        "--out", str(path).replace(".md", "_narrative.md"),
                    ]
                    if args.baselines:
                        nargs += ["--baselines", str(args.baselines)]
                    # Pass up to 5 most recent facts.json files for cross-run context
                    recents = sorted(
                        Path(args.out).glob("*_facts.json"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )[:5]
                    for r in recents:
                        if str(r) != str(facts_path):
                            nargs += ["--recent", str(r)]
                    subprocess.run(nargs, check=False, timeout=180)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [warn] narrative skipped: {exc}", file=__import__("sys").stderr)
            successes += 1
    finally:
        if api is not None:
            try:
                api.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    # Also stage a copy in public/analyses/ so the Vite build picks them up
    # (the React /analysis route reads them via fetch at runtime).
    public_dir = REPO_ROOT / "public" / "analyses"
    public_dir.mkdir(parents=True, exist_ok=True)
    for f in args.out.glob("*"):
        if f.suffix in (".md", ".json", ".svg"):
            try:
                shutil.copy(f, public_dir / f.name)
            except shutil.SameFileError:
                pass  # CLI was invoked with OUT=public/analyses; nothing to copy
    print(f"  staged {sum(1 for _ in args.out.glob('*.md'))} reports in {public_dir.relative_to(REPO_ROOT)}")

    print(f"\n{successes} succeeded, {len(failures)} failed.")
    if api_unavailable_reason:
        print(f"  (Coros API was unavailable: {api_unavailable_reason})")
    for lid, err in failures:
        print(f"  ✗ {lid}: {err}")
    return 0 if not failures else 2


def _analyze_without_api(label_id, baselines, account):
    """Fallback when the Coros API is unavailable: use FIT + id_map only.

    Strategy:
      1. If id_map has this labelId → use its FIT path
      2. Else if labelId parses as a timestamp-based run_id → look up
         the activity in `activities` table, then find a FIT by date
         in FIT_DIR (slow but works for legacy data)
      3. Else: give up
    """
    from .adapters.fit_parser import parse_fit_laps
    from .adapters import id_map
    from .domain import (
        ActivityMeta, BodyStateSnapshot,
        classify_laps, compute_pace_stats, compute_hr_drift, compute_biomech_delta,
        compute_pace_vs_goal, recommendations, ActivityMetrics, AnalysisReport,
    )
    from sqlalchemy import create_engine, text

    # 1. Try id_map
    entry = next((e for e in id_map.all_entries(db_path=SQL_DB) if e.label_id == label_id), None)
    if entry is not None and entry.fit_filename:
        fit_path = FIT_DIR / entry.fit_filename
        if not fit_path.exists():
            raise FileNotFoundError(f"FIT not found: {fit_path}")
        sport_type = entry.sport_type or 100
    else:
        # 2. Legacy: treat labelId as run_id, look up in activities, then find FIT by date
        try:
            run_id = int(label_id)
        except ValueError:
            raise FileNotFoundError(
                f"no id_map entry for labelId {label_id!r} (and not a run_id)"
            )
        e = create_engine(f"sqlite:///{SQL_DB}")
        with e.connect() as c:
            row = c.execute(
                text("SELECT start_date_local FROM activities WHERE run_id = :rid"),
                {"rid": run_id},
            ).fetchone()
        if row is None:
            raise FileNotFoundError(f"no activity with run_id {run_id}")
        target_date = str(row[0])[:10]  # YYYY-MM-DD
        # Scan FIT_OUT for a file whose start_time matches
        fit_path = None
        for fp in FIT_DIR.glob("*.fit"):
            try:
                laps = parse_fit_laps(fp)
            except Exception:
                continue
            if laps and laps[0].start_time and laps[0].start_time.strftime("%Y-%m-%d") == target_date:
                fit_path = fp
                break
        if fit_path is None:
            raise FileNotFoundError(f"no FIT found for date {target_date}")
        sport_type = 100

    laps = parse_fit_laps(fit_path)
    if not laps or not laps[0].start_time:
        raise ValueError(f"could not parse laps from {fit_path}")

    meta = ActivityMeta(
        label_id=label_id,
        sport_type=sport_type,
        start_date_local=laps[0].start_time,
        total_distance_m=sum(l.distance_m for l in laps),
        total_elapsed_s=sum(l.elapsed_s for l in laps),
        location=None,
        account=account,
    )
    snap = BodyStateSnapshot(
        hrv_today_ms=0, rhr_today_bpm=0,
        hrv_baseline_ms=baselines.hrv_baseline_ms, load_ratio=1.0,
    )
    cat = classify_laps(laps)
    pace = compute_pace_stats(cat.main, cat.all())
    hr = compute_hr_drift(cat.main, cat.all())
    bio = compute_biomech_delta(cat.main, cat.all())
    pvg = compute_pace_vs_goal(pace.mean_s_per_km, baselines)
    recs = recommendations(pace, hr, bio, snap, baselines)
    m = ActivityMetrics(cat, pace, hr, bio, pvg, snap, recs)

    # Recent sessions for the "vs N" comparison
    from .adapters import recent_sessions, session_for, sessions_before
    run_date = meta.start_date_local.date()
    prior = recent_sessions(before_date=run_date, n=5, activity_type="Run")
    current = session_for(on_date=run_date, activity_type="Run")
    recent = [s for s in ([current] if current else []) + list(prior)]

    # 4-week trend context (Phase 4 part 1) — same as the online path.
    from .domain import bucket_sessions_by_week, compute_trend_report
    prior_sessions = sessions_before(before_date=run_date, n=50, activity_type="Run")
    trend_sessions = list(prior_sessions)
    if current is not None:
        trend_sessions.append(current)
    weeks = bucket_sessions_by_week(trend_sessions, today=run_date, n_weeks=4)
    trend = compute_trend_report(weeks, current_run_pace_s_per_km=pace.mean_s_per_km)

    total_km = meta.total_distance_m / 1000.0
    md_stem = f"{meta.start_date_local.strftime('%Y-%m-%d')}_{total_km:05.2f}km".replace(".", "-")
    md = render_report(
        meta, m, baselines,
        recent_sessions=recent,
        trend=trend,
        current_run_pace_s_per_km=pace.mean_s_per_km,
        md_stem=md_stem,
    )
    return AnalysisReport(
        meta=meta, metrics=m, recent_sessions=tuple(recent), trend=trend, markdown=md,
    )


def _facts_to_json(report: AnalysisReport) -> dict:
    """Serialize an AnalysisReport to a JSON-friendly dict for Layer 3 consumption.

    Structure mirrors what the narrative prompt expects. Stable, version-friendly.
    """
    m = report.metrics
    return {
        "label_id": report.meta.label_id,
        "account": report.meta.account,
        "date": report.meta.start_date_local.strftime("%Y-%m-%d %H:%M:%S"),
        "type": "Run" if report.meta.sport_type == 100 else f"sport-{report.meta.sport_type}",
        "distance_km": report.meta.total_distance_m / 1000.0,
        "duration_s": report.meta.total_elapsed_s,
        "avg_pace_s": (
            report.meta.total_elapsed_s / (report.meta.total_distance_m / 1000.0)
            if report.meta.total_distance_m > 0
            else 0.0
        ),
        "location": report.meta.location,
        "pace_stats": {
            "mean_s_per_km": m.pace.mean_s_per_km,
            "range_s_per_km": m.pace.range_s_per_km,
            "trend": m.pace.trend,
            "consistency": m.pace.consistency,
        },
        "hr_stats": {
            "mean_bpm": m.hr.mean_bpm,
            "max_bpm": m.hr.max_bpm,
            "drift_bpm": m.hr.drift_bpm,
            "drift_grade": m.hr.drift_grade,
        },
        "bio": {
            "cadence_spm_first": m.bio.cadence_spm_first,
            "cadence_spm_last": m.bio.cadence_spm_last,
            "vertical_osc_first_mm": m.bio.vertical_osc_first_mm,
            "vertical_osc_last_mm": m.bio.vertical_osc_last_mm,
            "gct_first_ms": m.bio.gct_first_ms,
            "gct_last_ms": m.bio.gct_last_ms,
            "fatigue_grade": m.bio.fatigue_grade,
        },
        "body_state": {
            "hrv_today_ms": m.body_state.hrv_today_ms,
            "rhr_today_bpm": m.body_state.rhr_today_bpm,
            "hrv_baseline_ms": m.body_state.hrv_baseline_ms,
            "load_ratio": m.body_state.load_ratio,
        },
        "pace_vs_goal": {
            "target_s_per_km": m.pace_vs_goal.target_s_per_km,
            "actual_s_per_km": m.pace_vs_goal.actual_s_per_km,
            "delta_s_per_km": m.pace_vs_goal.delta_s_per_km,
            "matches": m.pace_vs_goal.matches,
        },
        "recent_sessions": [
            {
                "date": s.date,
                "distance_km": s.distance_km,
                "duration_s": s.duration_s,
                "avg_pace_s": s.avg_pace_s,
                "is_current": s.is_current,
            }
            for s in report.recent_sessions
        ],
        "laps": [
            {
                "index": l.index,
                "distance_m": l.distance_m,
                "elapsed_s": l.elapsed_s,
                "avg_heart_rate": l.avg_heart_rate,
                "max_heart_rate": l.max_heart_rate,
                "avg_speed_mps": l.avg_speed_mps,
                "avg_running_cadence_spm": l.avg_running_cadence_spm,
                "avg_vertical_oscillation_mm": l.avg_vertical_oscillation_mm,
                "avg_ground_contact_time_ms": l.avg_ground_contact_time_ms,
                "avg_vertical_ratio_pct": l.avg_vertical_ratio_pct,
            }
            for l in m.categorized.all()
        ],
    }


def _write_facts_json(report: AnalysisReport, md_path) -> "Path | None":
    """Write structured facts.json alongside the markdown. Returns path or None.

    Markdown 2026-05-04_08-02km.md → facts 2026-05-04_08-02km_facts.json
    """
    try:
        md_path = Path(md_path)
        facts_path = md_path.with_name(md_path.stem + "_facts.json")
        facts = _facts_to_json(report)
        facts_path.write_text(
            json.dumps(facts, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return facts_path
    except Exception as exc:  # noqa: BLE001
        print(
            f"  [warn] facts.json write failed: {exc}",
            file=__import__("sys").stderr,
        )
        return None


if __name__ == "__main__":
    sys.exit(main())
