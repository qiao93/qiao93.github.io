"""Tests for presentation/markdown_renderer.py — byte stability + SOP §6 sections.

The renderer MUST produce identical output for identical input (no timestamps,
no random IDs, no Python set iteration leaking through). This is enforced by
a byte-equality check on two consecutive calls.
"""
from dataclasses import replace
from datetime import datetime

from run_page.analysis.domain import (
    ActivityMeta,
    ActivityMetrics,
    Baselines,
    BioDelta,
    BodyStateSnapshot,
    CategorizedLaps,
    HrStats,
    Lap,
    PaceStats,
    PaceVsGoal,
    classify_laps,
)
from run_page.analysis.presentation.markdown_renderer import render_report
from run_page.analysis.tests.conftest import make_lap


def _sample_meta() -> ActivityMeta:
    return ActivityMeta(
        label_id="abc",
        sport_type=100,
        start_date_local=datetime(2026, 5, 16, 18, 21),
        total_distance_m=8000.0,
        total_elapsed_s=2400.0,
        location="中国",
    )


def _sample_metrics(hrv: int = 72, load: float = 1.0) -> ActivityMetrics:
    laps = [
        make_lap(index=0, distance_m=2000, elapsed_s=720, hr=140, max_hr=150,
                 cadence=170, vosc=70, gct=210),
        make_lap(index=1, distance_m=1500, elapsed_s=300, hr=160, max_hr=170,
                 cadence=180, vosc=60, gct=190),
        make_lap(index=2, distance_m=1500, elapsed_s=300, hr=162, max_hr=170,
                 cadence=178, vosc=62, gct=192),
        make_lap(index=3, distance_m=1500, elapsed_s=305, hr=164, max_hr=172,
                 cadence=176, vosc=63, gct=194),
        make_lap(index=4, distance_m=2000, elapsed_s=800, hr=145, max_hr=160,
                 cadence=170, vosc=72, gct=215),
        make_lap(index=5, distance_m=100, elapsed_s=40, hr=130, max_hr=140,
                 cadence=160, vosc=None, gct=None),
    ]
    cat = classify_laps(laps)
    pace = PaceStats(mean_s_per_km=300.0, range_s_per_km=10.0, trend="even", consistency="excellent")
    hr = HrStats(mean_bpm=155, max_bpm=170, drift_bpm=2, drift_grade="excellent")
    bio = BioDelta(cadence_spm_first=170, cadence_spm_last=170, vertical_osc_first_mm=70, vertical_osc_last_mm=72,
                   gct_first_ms=210, gct_last_ms=215, fatigue_grade="mild")
    pvg = PaceVsGoal(target_s_per_km=230, actual_s_per_km=300, delta_s_per_km=70, matches=False)
    snap = BodyStateSnapshot(hrv_today_ms=hrv, rhr_today_bpm=50, hrv_baseline_ms=69, load_ratio=load)
    return ActivityMetrics(cat, pace, hr, bio, pvg, snap, recommendations=())


def test_byte_stable_output():
    meta = _sample_meta()
    m1 = _sample_metrics()
    md1 = render_report(meta, m1, Baselines())
    md2 = render_report(meta, m1, Baselines())
    assert md1 == md2, "renderer must be deterministic"


def test_contains_all_sop_sections():
    md = render_report(_sample_meta(), _sample_metrics(), Baselines())
    for header in [
        "训练分析报告",                       # H1
        "课程结构",                            # H2
        "Lap 分段数据",                        # H2
        "核心数据",                             # H2
        "身体状态",                             # H2
        "目标对比",                             # H2
        "改进建议",                             # H2
    ]:
        assert header in md, f"missing section: {header}"


def test_lap_table_contains_all_laps_in_order():
    md = render_report(_sample_meta(), _sample_metrics(), Baselines())
    # First column is "#"; with 6 laps we expect 1..6 in that order
    lap_rows = [line for line in md.splitlines() if line.startswith("| ") and " 距离 " not in line and "---" not in line]
    indices = []
    for row in lap_rows:
        first = row.split("|")[1].strip()
        if first.isdigit():
            indices.append(int(first))
    assert indices == [1, 2, 3, 4, 5, 6]


def test_workout_classification_renders():
    md = render_report(_sample_meta(), _sample_metrics(), Baselines())
    # 3 main laps → "强度课（3 组主课）"
    assert "强度课" in md
    assert "3 组主课" in md
    # Workout type emoji appears
    assert "🔥" in md


def test_pace_match_verdict_uses_callout_for_aerobic():
    """When actual pace is far from LTHR band, the renderer drops a callout
    explaining it's outside the threshold band — phrased as "轻松跑" rather
    than "不匹配"."""
    md = render_report(_sample_meta(), _sample_metrics(), Baselines())
    # Actual 5:00/km, target 3:50, delta +70s → "按轻松跑对待" callout
    assert "按轻松跑对待" in md


def test_no_match_within_band_says_matches():
    """When actual pace matches the LTHR band, the renderer drops a 🎉
    callout rather than printing "匹配度：匹配"."""
    meta = _sample_meta()
    m = _sample_metrics()
    from dataclasses import replace
    m = replace(m, pace_vs_goal=PaceVsGoal(target_s_per_km=230, actual_s_per_km=230, delta_s_per_km=0, matches=True))
    md = render_report(meta, m, Baselines())
    assert "配速命中阈值区间" in md


def test_high_fatigue_recommendation_appears():
    from run_page.analysis.domain.scoring import recommendations
    meta = _sample_meta()
    m = _sample_metrics(hrv=45, load=1.0)  # -24ms from baseline
    recs = recommendations(m.pace, m.hr, m.bio, m.body_state, Baselines())
    m2 = type(m)(**{**m.__dict__, "recommendations": recs})
    md = render_report(meta, m2, Baselines())
    assert "明显低于" in md or "明显疲劳" in md


def test_file_written_by_store(tmp_path):
    from run_page.analysis.adapters import AnalysisStore
    from run_page.analysis.domain import AnalysisReport

    store = AnalysisStore(tmp_path)
    meta = _sample_meta()
    m = _sample_metrics()
    md = render_report(meta, m, Baselines())
    report = AnalysisReport(meta=meta, metrics=m, markdown=md)
    p = store.save_markdown(report, md)
    assert p.exists()
    assert p.read_text(encoding="utf-8") == md


# ---------- ownership mismatch detection ----------


def test_ownership_mismatch_emits_warning():
    meta = replace(_sample_meta(), account="user-a@example.com")
    b = Baselines(owner="user-b@example.com")
    md = render_report(meta, _sample_metrics(), b)
    assert "⚠️" in md
    assert "user-a@example.com" in md
    assert "user-b@example.com" in md


def test_ownership_match_no_warning():
    meta = replace(_sample_meta(), account="chenhaowei93@163.com")
    b = Baselines(owner="chenhaowei93@163.com")
    md = render_report(meta, _sample_metrics(), b)
    assert "⚠️" not in md


def test_ownership_unknown_skips_check():
    # Baselines.owner not set (None) — skip
    meta = replace(_sample_meta(), account="user-a@example.com")
    b = Baselines(owner=None)
    md = render_report(meta, _sample_metrics(), b)
    assert "⚠️" not in md


def test_ownership_activity_unknown_skips_check():
    # ActivityMeta.account not set (None) — skip
    meta = _sample_meta()  # account=None default
    b = Baselines(owner="chenhaowei93@163.com")
    md = render_report(meta, _sample_metrics(), b)
    assert "⚠️" not in md
