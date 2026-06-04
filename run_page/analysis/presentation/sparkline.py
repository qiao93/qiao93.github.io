"""Inline SVG sparklines for the "📈 跨课趋势" section.

Three sparklines are generated per report and written as companion
files alongside the Markdown report:

  <stem>_distance.svg   — 4-week distance bar chart
  <stem>_pace.svg      — 4-week pace line chart
  <stem>_consistency.svg — weeks-with-runs ring

Design principles:
  - Dark-background-aware: each SVG has a subtle zinc-950 bg rect so
    text/lines are legible regardless of the surrounding page theme.
  - Color palette: red (#ef4444) for current-week emphasis, zinc tones
    for prior data, matching the site's coral-red accent system.
  - viewBox-based (no fixed px sizing): scales cleanly at any container
    width when rendered as an <img>.
  - Pure functions: input is typed data (WeekAggregate tuple), output is
    an SVG string. No I/O — the caller writes the file.
"""
from __future__ import annotations

from pathlib import Path

from ..domain.trends import TrendReport, WeekAggregate, format_pace

# Palette — matches the site's red-500 / zinc- scheme.
CLR_CURRENT = "#ef4444"   # red-500  (current week emphasis)
CLR_PRIOR   = "#52525b"   # zinc-600 (prior week bars/lines)
CLR_EMPTY   = "#3f3f46"   # zinc-700 (empty week)
CLR_BG      = "#18181b"   # zinc-950 (background)
CLR_TEXT    = "#a1a1aa"   # zinc-400 (labels)
CLR_RING    = "#ef4444"   # red-500
CLR_RING_BG = "#27272a"   # zinc-800 (ring track)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _svg_header(view_box: str, width: int | str = 200, height: int | str = 60) -> str:
    """Minimal SVG document start with a dark-background rect and no
    extra namespaces — keeps the payload small for data-URI embedding."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' viewBox="{view_box}" width="{width}" height="{height}">'
        f'<rect fill="{CLR_BG}" width="100%" height="100%" rx="4"/>'
    )


# ---------------------------------------------------------------------------
# Distance bar chart  (4 bars, one per week)
# ---------------------------------------------------------------------------

def sparkline_distance(weeks: tuple[WeekAggregate, ...]) -> str:
    """4-week distance bar chart.

    Layout: 200×60 viewBox
      - 4 bars, each ~30 wide, 10 gap → 4×30 + 3×10 = 150 → centered
      - Bar height: proportional to max_km in window (0 → 0 height)
      - Current week bar is red; prior week bars are zinc
      - Week label below each bar (mm-dd, 4 chars max)

    Returns a complete SVG string.
    """
    total_km = [w.total_distance_km for w in weeks]
    max_km   = max(total_km) if total_km else 1.0

    # Centering: total width = 4 bars × 30 + 3 gaps × 10 = 150
    # Start X = (200 - 150) / 2 = 25
    BAR_W   = 30
    GAP     = 10
    START_X = 25
    BAR_Y_BOTTOM = 48   # bars grow upward from y=48

    parts = [_svg_header("0 0 200 60")]

    for i, w in enumerate(weeks):
        x     = START_X + i * (BAR_W + GAP)
        h_px  = (w.total_distance_km / max_km) * 40 if max_km > 0 else 0
        y_top = BAR_Y_BOTTOM - h_px

        fill = CLR_CURRENT if w.is_current else (CLR_PRIOR if w.session_count > 0 else CLR_EMPTY)
        parts.append(
            f'<rect x="{x}" y="{y_top}" width="{BAR_W}" height="{h_px:.1f}"'
            f' fill="{fill}" rx="2"/>'
        )
        # Label: mm-dd
        label = w.week_start.strftime("%m-%d")
        parts.append(
            f'<text x="{x + BAR_W / 2}" y="58"'
            f' text-anchor="middle"'
            f' font-size="5.5" fill="{CLR_TEXT}"'
            f' font-family="Arial,sans-serif">{label}</text>'
        )

    # Value label above current bar (if non-zero)
    current = next((w for w in weeks if w.is_current), None)
    if current and current.total_distance_km > 0:
        ci = list(weeks).index(current)
        x  = START_X + ci * (BAR_W + GAP)
        parts.append(
            f'<text x="{x + BAR_W / 2}" y="{BAR_Y_BOTTOM - (current.total_distance_km / max_km) * 40 - 2}"'
            f' text-anchor="middle" font-size="6" fill="{CLR_CURRENT}"'
            f' font-family="Arial,sans-serif" font-weight="bold">'
            f'{current.total_distance_km:.0f}km</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Pace line chart  (4-point polyline)
# ---------------------------------------------------------------------------

def sparkline_pace(
    weeks: tuple[WeekAggregate, ...],
    current_pace_s_per_km: float = 0.0,
) -> str:
    """4-week pace line chart.

    Layout: 200×60 viewBox
      - 4 data points connected by a polyline
      - Y axis = pace (s/km), inverted (faster pace = lower y = higher on chart)
      - Min/max auto-scaled with 5 s/km padding
      - Current week point is larger + red; prior points are zinc dots
      - Week labels as for distance chart
      - Current run's avg pace shown as a dashed horizontal reference line

    When all paces are 0 (no data), draws a flat dashed line with "—" label.
    """
    paces = [w.avg_pace_s_per_km for w in weeks]

    # Scale: 200×60, x positions same as distance bars
    BAR_W   = 30
    GAP     = 10
    START_X = 25
    MARGIN  = 8    # top/bottom margin for labels
    H       = 60 - 2 * MARGIN  # usable height = 44

    # Compute y-scale
    valid = [p for p in paces if p > 0]
    if valid:
        min_p = min(valid)
        max_p = max(valid)
        pad   = max(5.0, (max_p - min_p) * 0.1)
        y_min = min_p - pad
        y_max = max_p + pad
    else:
        y_min = 0.0
        y_max = 600.0  # fallback: 10:00/km

    def _y(pace_s: float) -> float:
        if y_max == y_min:
            return MARGIN
        return MARGIN + (y_max - pace_s) / (y_max - y_min) * H

    # X positions for each week
    xs = [START_X + i * (BAR_W + GAP) + BAR_W // 2 for i in range(len(weeks))]
    ys = [_y(p) for p in paces]

    parts = [_svg_header("0 0 200 60")]

    # Dashed reference line for current run's pace (if non-zero)
    if current_pace_s_per_km > 0:
        ref_y = _y(current_pace_s_per_km)
        parts.append(
            f'<line x1="5" y1="{ref_y:.2f}" x2="195" y2="{ref_y:.2f}"'
            f' stroke="{CLR_CURRENT}" stroke-width="0.8"'
            f' stroke-dasharray="2,2" opacity="0.5"/>'
        )
        parts.append(
            f'<text x="198" y="{ref_y - 1}" text-anchor="start"'
            f' font-size="5" fill="{CLR_CURRENT}" font-family="Arial,sans-serif">'
            f'{format_pace(current_pace_s_per_km)}</text>'
        )

    # Polyline through all non-zero points
    points = " ".join(
        f"{x},{y:.2f}"
        for x, y, p in zip(xs, ys, paces)
        if p > 0
    )
    if points:
        parts.append(
            f'<polyline points="{points}" fill="none"'
            f' stroke="{CLR_PRIOR}" stroke-width="1.2"'
            f' stroke-linejoin="round" stroke-linecap="round"/>'
        )

    # Data points
    for i, (x, y, p, w) in enumerate(zip(xs, ys, paces, weeks)):
        if p <= 0:
            continue
        r   = 3.5 if w.is_current else 2.5
        col = CLR_CURRENT if w.is_current else CLR_PRIOR
        parts.append(
            f'<circle cx="{x}" cy="{y:.2f}" r="{r}" fill="{col}"/>'
        )
        # Week label
        label = w.week_start.strftime("%m-%d")
        parts.append(
            f'<text x="{x}" y="58" text-anchor="middle"'
            f' font-size="5.5" fill="{CLR_TEXT}" font-family="Arial,sans-serif">'
            f'{label}</text>'
        )

    # "pace" axis label
    parts.append(
        f'<text x="4" y="10" font-size="5" fill="{CLR_TEXT}"'
        f' font-family="Arial,sans-serif">s/km</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Consistency ring
# ---------------------------------------------------------------------------

def sparkline_consistency(weeks: tuple[WeekAggregate, ...]) -> str:
    """Weeks-with-runs consistency as a partial ring.

    Layout: 60×60 viewBox
      - cx=30, cy=30, r=22
      - Track: zinc-800 full circle (stroke-dasharray = full circumference)
      - Arc: red, length proportional to weeks_with_runs / len(weeks)
      - Center text: "N/W" (e.g. "4/4") in red, bold
      - Below: small "周有课" label

    The arc is drawn using stroke-dasharray / stroke-dashoffset on a circle,
    which is the standard SVG "donut chart" technique.
    """
    n      = len(weeks)
    active = sum(1 for w in weeks if w.session_count > 0)
    frac   = active / n if n else 0.0

    CX = 30; CY = 30; R = 22
    CIRC = 2 * 3.141592653589793 * R          # ≈ 138.23
    arc_len   = CIRC * frac
    gap_len   = CIRC * (1 - frac)

    parts = [_svg_header("0 0 60 60", width=60, height=60)]

    # Track (full circle in zinc-800)
    parts.append(
        f'<circle cx="{CX}" cy="{CY}" r="{R}" fill="none"'
        f' stroke="{CLR_RING_BG}" stroke-width="4.5"/>'
    )

    if frac > 0:
        # Arc: starts at 12-o'clock, goes clockwise
        # stroke-dasharray = arc_len gap_len → arc_len drawn, gap_len hidden
        parts.append(
            f'<circle cx="{CX}" cy="{CY}" r="{R}" fill="none"'
            f' stroke="{CLR_RING}" stroke-width="4.5"'
            f' stroke-dasharray="{arc_len:.2f} {gap_len:.2f}"'
            f' stroke-dashoffset="{CIRC * 0.25:.2f}"'   # start at 12 o'clock
            f' stroke-linecap="round"/>'
        )

    # Center: "N/W"
    parts.append(
        f'<text x="{CX}" y="{CY - 2}" text-anchor="middle"'
        f' font-size="11" fill="{CLR_CURRENT}" font-weight="bold"'
        f' font-family="Arial,sans-serif">{active}</text>'
    )
    parts.append(
        f'<text x="{CX}" y="{CY + 8}" text-anchor="middle"'
        f' font-size="6" fill="{CLR_TEXT}" font-family="Arial,sans-serif">/{n}周</text>'
    )

    # Label below ring
    parts.append(
        f'<text x="{CX}" y="56" text-anchor="middle"'
        f' font-size="5" fill="{CLR_TEXT}" font-family="Arial,sans-serif">有课</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# File naming helpers
# ---------------------------------------------------------------------------

def sparkline_stem(md_path: Path) -> Path:
    """Return the <stem> shared by all sparkline files for a given report.

    e.g.  /path/to/2026-05-04_08-02km.md  →  /path/to/2026-05-04_08-02km
    """
    return md_path.with_name(md_path.stem)


def write_sparklines(md_path: Path, weeks: tuple[WeekAggregate, ...], current_pace_s: float) -> dict[str, Path]:
    """Generate all 3 sparkline files for a report and write them to disk.

    Returns a dict of {name → path} so the caller can embed references.
    """
    stem = sparkline_stem(md_path)
    files: dict[str, Path] = {}

    svgs = {
        "distance":     sparkline_distance(weeks),
        "pace":        sparkline_pace(weeks, current_pace_s),
        "consistency": sparkline_consistency(weeks),
    }
    for name, svg_content in svgs.items():
        p = stem.with_name(stem.name + f"_{name}.svg")
        p.write_text(svg_content, encoding="utf-8")
        files[name] = p

    return files


__all__ = [
    "sparkline_consistency",
    "sparkline_distance",
    "sparkline_pace",
    "sparkline_stem",
    "write_sparklines",
]
