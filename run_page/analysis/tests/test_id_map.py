"""Tests for adapters/id_map.py — coros_id_map table operations.

These tests use a real SQLite file in tmp_path so the SQL syntax gets
exercised (not just the dataclass layer).
"""
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from run_page.analysis.adapters import id_map


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Create an empty SQLite with the coros_id_map schema."""
    db = tmp_path / "test.db"
    e = create_engine(f"sqlite:///{db}")
    with e.begin() as c:
        c.execute(text("""
            CREATE TABLE coros_id_map (
                label_id TEXT PRIMARY KEY,
                run_id INTEGER,
                fit_filename TEXT NOT NULL,
                sport_type INTEGER,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    return db


def test_record_inserts_new_row(db: Path):
    id_map.record("label-a", "label-a.fit", sport_type=100, db_path=db)
    entries = id_map.all_entries(db)
    assert len(entries) == 1
    e = entries[0]
    assert e.label_id == "label-a"
    assert e.fit_filename == "label-a.fit"
    assert e.sport_type == 100
    assert e.run_id is None
    assert e.downloaded_at is not None


def test_record_creates_table_for_new_db(tmp_path: Path):
    db = tmp_path / "new.db"
    id_map.record("label-a", "label-a.fit", sport_type=100, db_path=db)
    assert id_map.count(db) == 1


def test_record_is_idempotent(db: Path):
    id_map.record("label-a", "label-a.fit", sport_type=100, db_path=db)
    id_map.record("label-a", "label-a.fit", sport_type=100, db_path=db)
    id_map.record("label-a", "label-a.fit", sport_type=100, db_path=db)
    assert id_map.count(db) == 1


def test_record_upserts_filename(db: Path):
    """If we re-download the same labelId and Coros returns a different
    filename, the map should update to the new one."""
    id_map.record("label-a", "old-hash.fit", sport_type=100, db_path=db)
    id_map.record("label-a", "new-hash.fit", sport_type=100, db_path=db)
    e = id_map.all_entries(db)[0]
    assert e.fit_filename == "new-hash.fit"


def test_record_preserves_run_id_if_not_provided(db: Path):
    """If we set run_id on first call and then re-record without it,
    the existing run_id should NOT be wiped out."""
    id_map.record("label-a", "label-a.fit", run_id=12345, db_path=db)
    id_map.record("label-a", "label-a.fit", db_path=db)  # no run_id
    e = id_map.all_entries(db)[0]
    assert e.run_id == 12345


def test_latest_returns_descending(db: Path):
    id_map.record("a", "a.fit", db_path=db)
    id_map.record("b", "b.fit", db_path=db)
    id_map.record("c", "c.fit", db_path=db)
    top3 = id_map.latest(3, db)
    assert [e.label_id for e in top3] == ["c", "b", "a"]


def test_since_filters_correctly(db: Path):
    id_map.record("a", "a.fit", db_path=db)
    e = create_engine(f"sqlite:///{db}")
    with e.begin() as c:
        c.execute(text("UPDATE coros_id_map SET downloaded_at = '2020-01-01 00:00:00' WHERE label_id = 'a'"))
        c.execute(text("INSERT INTO coros_id_map (label_id, fit_filename, downloaded_at) VALUES ('b', 'b.fit', '2025-01-01 00:00:00')"))
    new = id_map.since(datetime(2024, 1, 1), db)
    assert [e.label_id for e in new] == ["b"]


def test_link_to_run_id_backfills(db: Path):
    id_map.record("label-a", "label-a.fit", db_path=db)
    id_map.link_to_run_id("label-a", 99999, db_path=db)
    assert id_map.lookup_run_id("label-a", db) == 99999


def test_lookup_run_id_returns_none_for_unknown(db: Path):
    assert id_map.lookup_run_id("never-recorded", db) is None


def test_count_empty_db(db: Path):
    assert id_map.count(db) == 0


def test_live_coros_api_fit_path_uses_id_map_filename(tmp_path: Path, monkeypatch):
    """Coros saves downloaded FITs under returned hash filenames, not labelId.fit."""
    from run_page.analysis.adapters import coros_api

    data_db = tmp_path / "run_page" / "data.db"
    fit_dir = tmp_path / "FIT_OUT"
    data_db.parent.mkdir(parents=True)
    fit_dir.mkdir()

    e = create_engine(f"sqlite:///{data_db}")
    with e.begin() as c:
        c.execute(text("""
            CREATE TABLE coros_id_map (
                label_id TEXT PRIMARY KEY,
                run_id INTEGER,
                fit_filename TEXT NOT NULL,
                sport_type INTEGER,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    id_map.record("label-a", "coros-returned-name.fit", sport_type=100, db_path=data_db)

    monkeypatch.setattr(coros_api, "REPO_ROOT", tmp_path)
    api = coros_api.LiveCorosApi("account", "password")
    try:
        assert api.fit_path("label-a") == fit_dir / "coros-returned-name.fit"
    finally:
        api.__exit__(None, None, None)


# ---------- CLI resilience (Phase 7) ----------


def test_cli_falls_back_when_coros_unavailable(tmp_path: Path, monkeypatch, capfd):
    """If LiveCorosApi raises on construction, CLI should still produce reports
    using id_map + FIT only, with a warning to stderr."""
    from run_page.analysis import cli

    # Make the real LiveCorosApi blow up (mimics anti-brute-force lockout)
    def _explode(*a, **kw):
        from run_page.analysis.adapters.coros_api import CorosApiError
        raise CorosApiError("login failed: 41C2B95C credentials do not match")
    monkeypatch.setattr(cli, "LiveCorosApi", _explode)

    # Use a real id_map with a synthetic entry pointing to a real FIT
    db = tmp_path / "test.db"
    from sqlalchemy import create_engine, text
    e = create_engine(f"sqlite:///{db}")
    with e.begin() as c:
        c.execute(text("""
            CREATE TABLE coros_id_map (
                label_id TEXT PRIMARY KEY,
                run_id INTEGER, fit_filename TEXT NOT NULL,
                sport_type INTEGER, downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    # Borrow a real FIT (some in the repo are corrupt; pick one that parses)
    import shutil
    real_fits = list((Path(__file__).resolve().parents[3] / "FIT_OUT").glob("*.fit"))
    if not real_fits:
        pytest.skip("no real FITs available")
    from run_page.analysis.adapters.fit_parser import parse_fit_laps
    real_fit = None
    for fp in real_fits:
        try:
            laps = parse_fit_laps(fp)
            if laps and laps[0].start_time:
                real_fit = fp
                break
        except Exception:
            continue
    if real_fit is None:
        pytest.skip("no parseable real FITs available")
    shutil.copy(real_fit, tmp_path / "real.fit")
    id_map.record("label-fake", "real.fit", sport_type=100, db_path=db)

    # Provide env vars so _resolve_account() succeeds
    monkeypatch.setenv("COROS_ACCOUNT", "9996632@qq.com")
    monkeypatch.setenv("COROS_PASSWORD", "x" * 32)  # never used — LiveCorosApi is mocked

    # Point CLI at our test DB and our tmp out dir
    monkeypatch.setattr(cli, "SQL_DB", db)
    monkeypatch.setattr(cli, "FIT_DIR", tmp_path)

    # Provide a labelId and explicit --out (argparse already snapshotted
    # DEFAULT_OUT at import time, so we must pass --out to override)
    out_dir = tmp_path / "out"
    rc = cli.main(["--label-id", "label-fake", "--out", str(out_dir)])
    captured = capfd.readouterr()
    print("STDOUT:", captured.out, file=__import__("sys").stderr)
    print("STDERR:", captured.err, file=__import__("sys").stderr)
    assert "Coros API unavailable" in captured.err
    # The report should be saved even though API failed
    out = tmp_path / "out"
    assert any(out.glob("*.md"))
    assert rc == 0
