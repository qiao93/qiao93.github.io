"""Markdown rendering of an AnalysisReport — 小红书 / RED style.

Output is byte-stable for the same input. Sections:
  H1   训练分析报告             + subtitle (课型 / 距离 / 时长 / 平均配速)
  H2   🎯 核心数据              pace / HR / biomech with emoji grades
  H2   🏃 课程结构              block-quote structure
  H2   📊 Lap 分段数据          compact table (drop useless columns)
  H2   📈 与近 N 课对比        side-by-side comparison
  H2   💪 身体状态              3-row table with grade badges
  H2   🏁 目标对比              target vs actual delta
  H2   💡 改进建议              numbered list with severity emoji
"""
from __future__ import annotations

from typing import Iterable

from ..adapters import SessionSummary
from ..domain import (
    ActivityMeta,
    ActivityMetrics,
    Baselines,
    format_pace,
    pace_seconds_per_km,
)
from ..domain.scoring import grade_hrv, grade_load, BodyStateGrade, LoadGrade
from ..domain.trends import (
    TrendReport,
    WeekAggregate,
    format_consistency_grade_zh,
    format_pace_grade_zh,
    format_volume_grade_zh,
    format_week_label,
    format_week_pace_delta,
)


# ---------- formatters ----------


def _fmt_distance(m: float) -> str:
    return f"{m/1000.0:.2f}km"


def _fmt_duration(s: float) -> str:
    if not s:
        return "—"
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _hrv_text(snap, b) -> tuple[str, str]:
    """Returns (value_str, grade_str) for the HRV row."""
    if snap.hrv_today_ms <= 0:
        return ("⚠️ 不可用", "Coros API 未提供")
    g = grade_hrv(snap, b)
    delta = snap.hrv_today_ms - b.hrv_baseline_ms
    grade = {
        BodyStateGrade.WELL_RECOVERED: "✅ 充分准备",
        BodyStateGrade.NORMAL: "✅ 正常",
        BodyStateGrade.MILD_FATIGUE: "🟡 轻度疲劳",
        BodyStateGrade.HIGH_FATIGUE: "🔴 明显疲劳",
    }[g]
    val = f"{snap.hrv_today_ms}ms（基准 {b.hrv_baseline_ms}ms，{delta:+d}ms）"
    return (val, grade)


def _load_text(snap, b) -> tuple[str, str]:
    g = grade_load(snap.load_ratio, b)
    if g == LoadGrade.OVERLOAD:
        return (f"{snap.load_ratio:.2f}", f"🔴 过载（> {b.load_overload:.1f}）")
    if g == LoadGrade.OPTIMIZED:
        return (f"{snap.load_ratio:.2f}", f"✅ 适度超量（{b.load_optimized:.1f}–{b.load_overload:.1f}）")
    if g == LoadGrade.MAINTAINING:
        return (f"{snap.load_ratio:.2f}", f"🟡 维持（{b.load_maintaining:.1f}–{b.load_optimized:.1f}）")
    return (f"{snap.load_ratio:.2f}", f"🔵 训练不足（< {b.load_maintaining:.1f}）")


def _pace_grade(range_s: float, has_main: bool) -> str:
    if has_main:
        if range_s < 10: return "⭐⭐⭐ 优秀"
        if range_s < 20: return "⭐⭐ 良好"
        return "🟡 变化大"
    if range_s < 20: return "✅ 稳定"
    return "🟡 变化大"


def _hr_drift_emoji(grade: str) -> str:
    return {"excellent": "⭐⭐⭐ 完美", "good": "⭐⭐ 良好", "needs_work": "🔴 需关注"}.get(grade, grade)


def _bio_fatigue_emoji(grade: str) -> str:
    return {"none": "✅ 无", "mild": "🟡 轻微", "notable": "🔴 明显"}.get(grade, grade)


def _maybe(v, fmt: str) -> str:
    if v is None:
        return "—"
    return fmt.format(v)


def _classify_workout(meta: ActivityMeta, m: ActivityMetrics) -> tuple[str, str]:
    """Returns (short_type, emoji) for the subtitle."""
    n_main = len(m.categorized.main)
    if n_main >= 3:
        return (f"强度课（{n_main} 组主课）", "🔥")
    if meta.total_distance_m >= 20000:
        return ("长距离", "🏔")
    if meta.total_distance_m < 5000:
        return ("恢复跑", "🌱")
    return ("有氧跑", "🏃")


def _ownership_warning(meta: ActivityMeta, b: Baselines) -> str | None:
    if not b.owner or not meta.account:
        return None
    if b.owner == meta.account:
        return None
    return (
        f"> ⚠️ **基线不匹配**：当前基线适用于 `{b.owner}`，"
        f"本次数据来自 `{meta.account}`。HRV / 目标对比参考价值有限。"
    )


# ---------- main renderer ----------


def _prior_avg_pace_s(weeks: tuple[WeekAggregate, ...]) -> float:
    """Distance-weighted avg pace of weeks BEFORE the current one.
    Returns 0.0 if no prior data — caller should treat as '—'."""
    prior = [w for w in weeks if not w.is_current]
    total_dur = sum(w.total_duration_s for w in prior)
    total_km = sum(w.total_distance_km for w in prior)
    return total_dur / total_km if total_km > 0 else 0.0


def _format_week_row(
    w: WeekAggregate,
    prev_pace: float,
    prior_avg_pace: float,
    current_pace: float,
) -> str:
    pace_str = format_pace(w.avg_pace_s_per_km) if w.avg_pace_s_per_km > 0 else "—"
    if w.is_current:
        # Compare the current run's pace to the prior 3-week baseline
        delta_str = format_week_pace_delta(current_pace, prior_avg_pace)
        trend_cell = f"**← 本课** vs 3w 均 {delta_str}"
    else:
        # Week-over-week delta (this week vs previous week in the table)
        delta_str = format_week_pace_delta(w.avg_pace_s_per_km, prev_pace)
        if prev_pace <= 0:
            trend_cell = "—"
        else:
            trend_cell = delta_str
    return (
        f"| {format_week_label(w)} | {w.session_count} | "
        f"{w.total_distance_km:.1f}km | {pace_str} | {trend_cell} |"
    )


def render_trend_section(
    trend: TrendReport,
    current_run_pace_s_per_km: float,
    md_stem: str | None = None,
) -> str:
    """Render the "📈 跨课趋势（近 4 周）" section.

    `current_run_pace_s_per_km` is the current run's avg pace — used
    for the "← 本课 vs 3w 均" cell on the current week's row. Pass 0.0
    to skip the comparison (e.g. when the run was a recovery jog).

    `md_stem` — the filename stem (e.g. `2026-05-04_08-02km`) used to
    build sparkline image URLs `![](/analyses/<stem>_distance.svg)`. If
    None, sparkline images are omitted (useful when trend data is
    present but rendering without file output).
    """
    parts: list[str] = []
    parts.append("## 📈 跨课趋势（近 4 周）")
    parts.append("")
    parts.append("| 周 | 课次 | 距离 | 平均配速 | 周对比 |")
    parts.append("|:---|---:|---:|---:|:---|")

    prior_avg = _prior_avg_pace_s(trend.weeks)
    prev_pace = 0.0  # for week-over-week in the prior rows
    for w in trend.weeks:
        parts.append(_format_week_row(w, prev_pace, prior_avg, current_run_pace_s_per_km))
        # Update prev_pace only for non-current rows (don't let the
        # current week's "self" pace leak into a subsequent week, even
        # though there shouldn't be one)
        if not w.is_current and w.avg_pace_s_per_km > 0:
            prev_pace = w.avg_pace_s_per_km
    parts.append("")

    # 4-week summary line
    avg_pace_str = format_pace(trend.four_week_avg_pace_s_per_km) if trend.four_week_avg_pace_s_per_km > 0 else "—"
    parts.append(
        f"**4 周合计**: {trend.four_week_total_km:.1f}km · "
        f"{trend.four_week_total_sessions} 课 · 平均配速 **{avg_pace_str}**"
    )
    parts.append("")

    # Trend grades
    parts.append("**趋势判断**:")
    parts.append(
        f"- 配速: {format_pace_grade_zh(trend.pace_grade)}"
    )
    parts.append(
        f"- 训练量: {format_volume_grade_zh(trend.volume_grade)}"
    )
    parts.append(
        f"- 一致性: {format_consistency_grade_zh(trend.weeks_with_runs, len(trend.weeks))}"
    )
    parts.append("")

    # Sparkline images (Phase 4 part 2) — embedded via relative path
    # so the Vite/dev server serves them from public/analyses/
    if md_stem:
        for name in ("distance", "pace", "consistency"):
            alt = {"distance": "距离趋势", "pace": "配速趋势", "consistency": "一致性"}[name]
            parts.append(
                f"![{alt}](/analyses/{md_stem}_{name}.svg)"
            )
        parts.append("")

    return "\n".join(parts)


def render_report(
    meta: ActivityMeta,
    m: ActivityMetrics,
    b: Baselines,
    recent_sessions: Iterable[SessionSummary] = (),
    trend: TrendReport | None = None,
    current_run_pace_s_per_km: float = 0.0,
    md_stem: str | None = None,
) -> str:
    date_str = meta.start_date_local.strftime("%Y-%m-%d")
    work_type, type_emoji = _classify_workout(meta, m)
    mean_pace = pace_seconds_per_km(
        m.categorized.main[0].avg_speed_mps if m.categorized.main else None
    ) if m.categorized.main else 0
    if mean_pace == 0:
        # fall back to overall mean
        all_paces = [l.avg_speed_mps for l in m.categorized.all() if l.avg_speed_mps]
        if all_paces:
            total_dist = sum(l.distance_m for l in m.categorized.all())
            total_s = sum(l.elapsed_s for l in m.categorized.all())
            mean_pace = total_s / (total_dist / 1000.0) if total_dist else 0
    mean_pace_str = format_pace(mean_pace) if mean_pace else "—"

    parts: list[str] = []
    warn = _ownership_warning(meta, b)
    if warn:
        parts.append(warn)
        parts.append("")

    # H1 — date + workout type as the hero
    parts.append(f"# 🏃 {date_str} 训练分析报告")
    parts.append("")
    parts.append(
        f"> {type_emoji} **{work_type}** · "
        f"{_fmt_distance(meta.total_distance_m)} · "
        f"{_fmt_duration(meta.total_elapsed_s)} · "
        f"平均配速 **{mean_pace_str}**"
    )
    parts.append("")
    parts.append("---")
    parts.append("")

    # H2 1 — 核心数据 (4 stats in one table)
    parts.append("## 🎯 核心数据")
    parts.append("")
    parts.append("| 维度 | 数值 | 评级 |")
    parts.append("|:---|---:|:---|")
    parts.append(
        f"| 配速极差 | {m.pace.range_s_per_km:.0f} s/km | "
        f"{_pace_grade(m.pace.range_s_per_km, bool(m.categorized.main))} |"
    )
    parts.append(
        f"| 配速趋势 | {'后半程更快' if m.pace.trend == 'negative_split' else '前半程更快' if m.pace.trend == 'positive_split' else '匀速' if m.pace.trend == 'even' else '混合'} | "
        f"{'✅' if m.pace.trend == 'negative_split' else '🟡' if m.pace.trend == 'even' else '⚠️'} |"
    )
    parts.append(
        f"| 心率漂移 | {m.hr.drift_bpm:+.0f} bpm | "
        f"{_hr_drift_emoji(m.hr.drift_grade)} |"
    )
    if m.bio.cadence_spm_first and m.bio.cadence_spm_last:
        d = m.bio.cadence_spm_last - m.bio.cadence_spm_first
        sign = "🔴" if d <= -3 else "🟡" if d <= 0 else "✅"
        parts.append(f"| 步频变化 | {d:+d} spm | {sign} |")
    if m.bio.vertical_osc_first_mm and m.bio.vertical_osc_last_mm:
        d = m.bio.vertical_osc_last_mm - m.bio.vertical_osc_first_mm
        sign = "🔴" if d >= 3 else "🟡" if d >= 1 else "✅"
        parts.append(f"| 振幅变化 | {d:+.0f} mm | {sign} |")
    parts.append(
        f"| 末段疲劳 | {m.bio.fatigue_grade} | "
        f"{_bio_fatigue_emoji(m.bio.fatigue_grade)} |"
    )
    parts.append("")

    # H2 2 — 课程结构 (block-quote)
    parts.append("## 🏃 课程结构")
    parts.append("")
    parts.append(f"> {_describe_structure(m)}")
    parts.append("")

    # H2 3 — Lap 分段数据 (compact)
    parts.append("## 📊 Lap 分段数据")
    parts.append("")
    parts.append(
        "| # | 距离 | 配速 | HR | 步频 | 振幅 | 垂直比 |"
    )
    parts.append("|:--|---:|---:|---:|---:|---:|---:|")
    by_cat = m.categorized.by_category()
    cat_lookup: dict[int, str] = {lap.index: cn.value for cn, laps in by_cat.items() for lap in laps}
    for lap in sorted(m.categorized.all(), key=lambda l: l.index):
        pace_str = format_pace(pace_seconds_per_km(lap.avg_speed_mps))
        parts.append(
            f"| {lap.index+1} | {lap.distance_m:.0f}m | {pace_str} | "
            f"{lap.avg_heart_rate or '—'} | "
            f"{lap.avg_running_cadence_spm or '—'} | "
            f"{_maybe(lap.avg_vertical_oscillation_mm, '{:.0f}mm')} | "
            f"{_maybe(lap.avg_vertical_ratio_pct, '{:.1f}%')} |"
        )
    parts.append("")

    # H2 4 — 与近 N 课对比 (only if we have any)
    recent_list = list(recent_sessions)
    if recent_list:
        parts.append("## 📈 与近 5 课对比")
        parts.append("")
        parts.append("| 日期 | 距离 | 配速 | HR | 备注 |")
        parts.append("|:---|---:|---:|---:|:---|")
        # Show recent_list in chronological order (oldest first within window)
        for s in recent_list:
            marker = "**← 本课**" if s.is_current else ""
            avg_pace = format_pace(s.avg_pace_s) if s.avg_pace_s else "—"
            hr_str = f"{s.avg_hr}" if s.avg_hr else "—"
            parts.append(
                f"| {s.date} | {s.distance_km:.2f}km | {avg_pace} | "
                f"{hr_str} | {marker} |"
            )
        parts.append("")

    # H2 4b — 跨课趋势（近 4 周） (optional, only when caller computed it)
    if trend is not None:
        parts.append(render_trend_section(trend, current_run_pace_s_per_km, md_stem=md_stem))

    # H2 5 — 身体状态 (3-row table)
    parts.append("## 💪 身体状态")
    parts.append("")
    parts.append("| 指标 | 数值 | 评价 |")
    parts.append("|:---|---:|:---|")
    hrv_val, hrv_grade = _hrv_text(m.body_state, b)
    parts.append(f"| HRV | {hrv_val} | {hrv_grade} |")
    parts.append(
        f"| RHR | {m.body_state.rhr_today_bpm} bpm | "
        f"{'✅ 优秀' if m.body_state.rhr_today_bpm and m.body_state.rhr_today_bpm < 60 else '🟡 正常'} |"
    )
    load_val, load_grade = _load_text(m.body_state, b)
    parts.append(f"| 训练负荷比值 | {load_val} | {load_grade} |")
    parts.append("")

    # H2 6 — 目标对比
    parts.append("## 🏁 目标对比")
    parts.append("")
    pvg = m.pace_vs_goal
    target = format_pace(float(pvg.target_s_per_km))
    actual = format_pace(pvg.actual_s_per_km) if pvg.actual_s_per_km > 0 else "—"
    parts.append("| | 目标 | 实际 | 差值 |")
    parts.append("|:---|---:|---:|---:|")
    if pvg.actual_s_per_km > 0:
        sign = "+" if pvg.delta_s_per_km >= 0 else "−"
        if pvg.matches:
            verdict = "✅ 匹配"
        elif pvg.delta_s_per_km > 60:
            verdict = f"轻松跑区间"
        else:
            verdict = "未匹配"
        parts.append(f"| 配速 | {target} | {actual} | {sign}{abs(pvg.delta_s_per_km):.0f}s/km |")
    parts.append("")
    if pvg.actual_s_per_km > 0 and pvg.matches:
        parts.append(f"> 🎉 配速命中阈值区间（{target} ± 5s）")
    elif pvg.actual_s_per_km > 0 and pvg.delta_s_per_km > 60:
        parts.append(f"> 💡 本课不在阈值配速区间（差 {abs(pvg.delta_s_per_km):.0f}s），按轻松跑对待")
    parts.append("")

    # H2 7 — 改进建议 (with severity emoji)
    parts.append("## 💡 改进建议")
    parts.append("")
    if m.recommendations:
        for i, rec in enumerate(m.recommendations, 1):
            # Replace the words 高/低/疲 with emoji
            emoji = "🔴" if "明显" in rec or "疲" in rec else "🟡" if "轻度" in rec or "略" in rec else "🔵" if "不足" in rec else "✅"
            parts.append(f"{i}. {emoji} {rec}")
    else:
        parts.append("1. ✅ 各项指标均在合格区间，保持当前训练节奏。")
    parts.append("")

    return "\n".join(parts)


def _describe_structure(m: ActivityMetrics) -> str:
    cat = m.categorized
    pieces: list[str] = []
    if cat.warmup:
        d = sum(l.distance_m for l in cat.warmup)
        pieces.append(f"热身 **{_fmt_distance(d)}**")
    if cat.main:
        d = sum(l.distance_m for l in cat.main)
        pieces.append(f"**主课 {len(cat.main)}×段 {_fmt_distance(d)}**")
    elif cat.other:
        groups: list[list] = []
        cur: list = []
        for lap in sorted(cat.other, key=lambda l: l.index):
            if not cur or lap.index == cur[-1].index + 1:
                cur.append(lap)
            else:
                groups.append(cur); cur = [lap]
        if cur:
            groups.append(cur)
        for grp in groups:
            d = sum(l.distance_m for l in grp)
            pieces.append(f"**{len(grp)}×{d/len(grp):.0f}m 段**")
    if cat.recovery:
        d = sum(l.distance_m for l in cat.recovery)
        pieces.append(f"组间休息 **{_fmt_distance(d)}**")
    if cat.cooldown:
        d = sum(l.distance_m for l in cat.cooldown)
        pieces.append(f"放松 **{_fmt_distance(d)}**")
    return " → ".join(pieces) if pieces else "无分段数据"
