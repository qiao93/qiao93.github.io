# SPEC: Post-Run Analysis

> **Status**: v1.1 (Phase 4 complete)
> **Source SOP**: `/Users/hawei/sisyphus/running-analysis-sop.md` v2.0
> **Last updated**: 2026-06-04
> **Goal**: After every run (manual or scheduled), produce a per-run Markdown
> report that follows the SOP's "Step 6 输出模板" structure, with values
> derived from the FIT Lap data + Coros 7-day metrics.

---

## Roadmap

| Phase | 内容 | Status |
|---|---|---|
| Phase 1 | Core FIT parsing, lap analysis, domain rules | ✓ Complete |
| Phase 2 | CLI tool, Markdown report generation | ✓ Complete |
| Phase 3 | Coros API adapter (activity_meta, body_state, fit_path) | ✓ Complete |
| Phase 4 | Cross-run trends + sparklines + 4-week ISO-week aggregation | ✓ Complete |
| Phase 5 | Frontend `/analysis` route — view reports on website | ✓ Complete |
| Phase 6 | Auto-commit analysis results to GitHub | ✓ Complete |
| Layer 3 | AI narrative (LLM opt-in) | 🔲 Pending |

---

## 1. Architectural principles

| Principle | Implication |
|---|---|
| **Hexagonal / Ports & Adapters** | Domain layer is pure functions; all I/O is behind an interface. |
| **Spec first, code second** | Every public function in this spec is referenced by at least one test. |
| **Configuration is data, not code** | Personal baselines (HRV 69ms, marathon 2:40:55, etc.) live in a `Baselines` dataclass loaded from YAML, never hard-coded in logic. |
| **Reproducibility** | Given the same FIT + same Coros snapshot + same `Baselines`, output is byte-identical. |
| **Layered AI (opt-in)** | Deterministic code produces facts; LLM produces narrative on top. Never let the LLM extract facts from raw FIT (unreliable + expensive). |
| **Graceful degradation** | If Layer 3 (LLM) fails or is disabled, Layer 1+2 still produce a complete report. Narrative is always opt-in. |

```
                    ┌─ Layer 1: data extraction (Python, deterministic) ─┐
                    │   FIT + Coros API → facts.json                         │
                    │   tests: 20+ (laps, paces, HR, bio, body state)      │
adapters ──┐       ├──────────────────────────────────────────────────────────┤
          ├───►─────┤  Layer 2: structured report (Python, deterministic)  │
          │         │   facts.json → facts.md                                │
          │         │   tests: golden file comparison                        │
          │         ├──────────────────────────────────────────────────────────┤
          │         │  Layer 3: AI narrative (LLM, OPT-IN)                 │
          │         │   facts.json + recent 5 sessions → narrative.md      │
          │         │   opt-in via ANTHROPIC_API_KEY; cost ~$0.03/run      │
          └─────────┴──────────────────────────────────────────────────────────┘
                              ▲
                       Baselines (config) ───────────┘
```

- **adapters/** know how to talk to FIT files, Coros API, the filesystem, the LLM API.
- **domain/** has zero I/O. It consumes typed records and produces typed records.
- **application/** orchestrates: "given a labelId, fetch → parse → analyze → render [optional: narrate]".
- **presentation/** formats `AnalysisReport` → Markdown string.

---

## 2. Data model

All public types live in `domain/models.py`. They are `@dataclass(frozen=True)`
so they can be hashed and compared cleanly.

### 2.1 Inputs

```python
@dataclass(frozen=True)
class Lap:
    index: int
    distance_m: float
    elapsed_s: float
    avg_heart_rate: int | None
    max_heart_rate: int | None
    avg_speed_mps: float | None          # m/s, used to derive pace
    avg_running_cadence_spm: int | None  # FIT reports single-foot, double for display
    avg_vertical_oscillation_mm: float | None
    avg_ground_contact_time_ms: float | None
    avg_vertical_ratio_pct: float | None
    start_time: datetime | None

@dataclass(frozen=True)
class ActivityMeta:
    label_id: str
    sport_type: int                       # 100 = outdoor run, 101 = indoor, etc.
    start_date_local: datetime
    total_distance_m: float
    total_elapsed_s: float
    location: str | None                  # city or "中国"

@dataclass(frozen=True)
class BodyStateSnapshot:
    """Aggregated over the 7 days ending on activity date."""
    hrv_today_ms: int
    rhr_today_bpm: int
    hrv_baseline_ms: int                  # copied from Baselines for self-containment
    load_ratio: float                     # acute:chronic

@dataclass(frozen=True)
class Baselines:
    hrv_baseline_ms: int = 69
    marathon_goal_str: str = "2:40:55"
    marathon_pace_range_s_per_km: tuple[int, int] = (228, 232)  # 3:48, 3:52
    # Section 3 of SOP: biomechanics thresholds (lower_is_better for some)
    bio_vertical_oscillation_mm: tuple[float, float] = (60.0, 70.0)   # (excellent, good)
    bio_vertical_ratio_pct:     tuple[float, float] = (5.5, 8.0)
    bio_gct_fast_ms:            tuple[float, float] = (190.0, 210.0)  # high-pace laps
    bio_gct_slow_ms:            tuple[float, float] = (210.0, 230.0)  # low-pace laps
    bio_cadence_fast_spm:       tuple[int, int]    = (172, 168)       # (excellent_lo, good_lo)
    # Section 4 of SOP: body state thresholds
    hrv_tolerance_ms: int = 5
    load_overload: float = 1.3
    load_optimized: float = 1.0
    load_maintaining: float = 0.8
```

### 2.2 Derived (computed by domain)

```python
@dataclass(frozen=True)
class CategorizedLaps:
    warmup: list[Lap]
    main: list[Lap]
    recovery: list[Lap]   # short rests between intervals
    cooldown: list[Lap]
    other: list[Lap]

@dataclass(frozen=True)
class PaceStats:
    mean_s_per_km: float
    range_s_per_km: float          # max - min
    is_consistent: bool            # range < 10s for intervals, < 20s for aerobic
    trend: str                     # "even", "negative_split", "positive_split", "mixed"

@dataclass(frozen=True)
class HrStats:
    mean_bpm: float
    max_bpm: int
    drift_bpm: float               # main_laps: last - first avg_hr
    drift_grade: str               # "excellent" (<15), "good" (<20), "needs_work"

@dataclass(frozen=True)
class BioDelta:
    cadence_spm_first: int | None
    cadence_spm_last: int | None
    vertical_osc_first_mm: float | None
    vertical_osc_last_mm: float | None
    gct_first_ms: float | None
    gct_last_ms: float | None
    fatigue_grade: str             # "none", "mild", "notable" based on first→last deltas

@dataclass(frozen=True)
class PaceVsGoal:
    target_s_per_km: int
    actual_s_per_km: float
    delta_s_per_km: float
    matches: bool                  # within ±5s of target band

@dataclass(frozen=True)
class ActivityMetrics:
    categorized: CategorizedLaps
    pace: PaceStats
    hr: HrStats
    bio: BioDelta
    pace_vs_goal: PaceVsGoal
    body_state: BodyStateSnapshot
    recommendations: list[str]     # plain-English, sourced from thresholds
```

### 2.3 Top-level result

```python
@dataclass(frozen=True)
class AnalysisReport:
    meta: ActivityMeta
    metrics: ActivityMetrics
    markdown: str                  # rendered output (cacheable, byte-stable)
```

---

## 3. Public API

```python
# application/analyzer.py
def analyze_activity(
    label_id: str,
    *,
    fit_loader: FitLoader,             # adapter port
    coros_api: CorosApiPort,           # adapter port
    baselines: Baselines,
    now: datetime,                     # injected for determinism
) -> AnalysisReport: ...

# adapters/fit_parser.py
def parse_fit_laps(fit_path: Path) -> list[Lap]: ...

# adapters/coros_api.py
class CorosApiPort(Protocol):
    def activity_meta(self, label_id: str) -> ActivityMeta: ...
    def body_state(self, on_date: date) -> BodyStateSnapshot: ...
    def fit_path(self, label_id: str) -> Path: ...

# presentation/markdown_renderer.py
def render_report(meta: ActivityMeta, m: ActivityMetrics, baselines: Baselines) -> str: ...
```

---

## 4. Domain rules

### 4.1 Lap classification (SOP Step 2 课型自动分类)

For each lap, in order:
- If `i == 0` and `distance_m > 1500` → `warmup`
- If `distance_m >= 1500` and `pace_s_per_km < 240` (i.e. < 4:00/km) → `main`
- If `distance_m >= 1500` and `pace_s_per_km >= 240` → `cooldown` (slow long segment)
- If `distance_m < 200` → `recovery` (interval rest)
- Else → `other`

### 4.2 Pace consistency

- For `main` laps: range < 10 s/km → `excellent`; < 20 s/km → `good`; else `mixed`.
- For non-main runs: range < 20 s/km → `consistent`; else `variable`.

### 4.3 HR drift

`drift_bpm = last_main.avg_hr - first_main.avg_hr`
- < 15 bpm → `excellent`
- < 20 bpm → `good`
- else → `needs_work`

### 4.4 Body state evaluation (SOP §4)

- HRV:  |today - baseline| ≤ tolerance → "normal"
        |today - baseline| ≤ 15  AND today < baseline → "mild_fatigue"
        today < baseline - 15 → "high_fatigue"
        today > baseline + tolerance → "well_recovered"
- Load:  > 1.3 → "overload"
        ≥ 1.0 → "optimized"
        ≥ 0.8 → "maintaining"
        else → "undertrained"

### 4.5 Recommendations (deterministic, threshold-driven)

Emit 1–3 short bullets based on the worst-graded dimension. Examples:
- "HR drift > 15 bpm in main set — consider adding 1 more recovery day."
- "Vertical oscillation crept up +2mm in last lap — fatigue management."

---

## 5. Output schema (Markdown)

Must follow SOP §6 template. Sections, in order:

```
## <YYYY-MM-DD> <课型> | <距离> | <时长>
### 课程结构
<warmup 段>
### Lap 分段数据
| table |
### 关键指标
- 配速极差/稳定性:
- 心率漂移:
- 生物力学首尾对比:
- 热身/休息质量（强度课）:
### 身体状态
- HRV: X ms（基准 Xms, 状态）
- RHR: X bpm
- 负荷比值: X.XX
### 与目标赛事对比
- 课型目标配速: X:XX/km
- 实际配速: X:XX/km
- 匹配度:
### 改进建议
1. ...
2. ...
```

The Markdown must be **byte-stable** for the same input (no timestamps, no
random IDs, no Python `set` iteration leaking through).

---

## 6. Layer 3: AI narrative (optional)

**Goal**: Add a personalized, contextual, in-SOP-tone narrative on top of the
deterministic facts. This is what the SOP calls the "花絮" / 深入洞察 — the
human-judgment layer that a coach would add after seeing the raw numbers.

The LLM **never** reads the FIT file directly. It receives:
- the structured `facts.json` from Layer 1 (a few KB)
- the user's `baselines.yaml`
- the last 5 sessions' summaries (also from `facts.json`s)
- a short system prompt (SOP tone + coaching guidelines)

### 6.1 Why layered

| Concern | Best handled by |
|---|---|
| Parse FIT binary | Python (deterministic, fits in 100 lines) |
| Compute pace / HR / GCT | Python (testable, byte-stable) |
| Score vs thresholds | Python (rules-based) |
| "this is your fastest 10k in 8 weeks, watch out for next week" | LLM (cross-run pattern) |
| "HRV not available, so we can't say much" | LLM (transparent caveats) |
| "建议加 1-2 次 8-10km 轻松跑" | LLM (personalized recommendation) |

### 6.2 Data model

```python
@dataclass(frozen=True)
class NarrativeContext:
    """Inputs to the LLM prompt. Built from facts.json + recent history."""
    facts: dict                  # the structured facts.json (machine-readable)
    baselines: Baselines        # user's personal calibration
    recent_summaries: list[dict]  # 5 most recent facts.json (just the meta + score, not full laps)
    sop_excerpt: str            # inline copy of the relevant SOP section (~500 tokens)

@dataclass(frozen=True)
class Narrative:
    """Output of Layer 3. Saved as <date>_<name>_narrative.md alongside facts.md."""
    label_id: str
    markdown: str                # 200-400 字 Markdown, includes "花絮" + 2-3 改进建议
    model: str                   # which model produced it
    prompt_tokens: int           # for cost tracking
    completion_tokens: int
    generated_at: datetime       # for staleness checks
```

### 6.3 Public API

```python
# narrative/generator.py
def generate_narrative(
    facts_path: Path,
    *,
    baselines: Baselines,
    api_key: str | None = None,
    model: str = "claude-haiku-4-5",
    max_tokens: int = 2000,
    now: datetime | None = None,        # injected for testability
    client: NarrativeClient | None = None,    # injected for tests
) -> Narrative | None:
    """Read facts.json, build prompt, call LLM, return Narrative.
    Returns None if api_key is missing or API call fails (graceful degradation).
    """

# narrative/prompt.py
SYSTEM_PROMPT: str  # ~500 tokens, includes SOP tone, style, output format
def build_user_prompt(ctx: NarrativeContext) -> str: ...   # pure function, testable

# narrative/client.py
class NarrativeClient(Protocol):
    def complete(self, system: str, user: str, max_tokens: int) -> CompletionResult: ...

class AnthropicClient:           # Anthropic + Anthropic-compatible proxies
    def __init__(self, api_key, model="claude-haiku-4-5", timeout=120.0): ...

class OpenAICompatibleClient:    # OpenAI + DeepSeek + Zhipu + Ollama + DashScope
    def __init__(self, api_key, base_url, model, timeout=120.0, *, _client=None): ...

class FakeClient:               # for tests
    def __init__(self, canned: CompletionResult | None = None, raise_exc: Exception | None = None): ...

def get_client(provider: str | None = None) -> NarrativeClient:
    """Pick a client based on NARRATIVE_PROVIDER env var. Raises with a
    human-readable error if the required API key is missing or the provider
    is unknown. Lazy-imports the SDK so unused providers don't fail at import.
    """
```

`provider` values:
- `"anthropic"` (default): uses `AnthropicClient`. Reads `ANTHROPIC_API_KEY` + `ANTHROPIC_BASE_URL`.
- `"openai"` (alias for any OpenAI-compatible): uses `OpenAICompatibleClient`. Reads `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `OPENAI_MODEL`.
- `"fake"` (test only): returns a FakeClient with a canned response.

For the OpenAI path, a single client class works with all of:
- OpenAI proper (`https://api.openai.com/v1`, model `gpt-4o-mini`)
- DeepSeek (`https://api.deepseek.com/v1`, model `deepseek-chat`)
- Zhipu GLM (`https://open.bigmodel.cn/api/paas/v4/`, model `glm-4-flash`)
- DashScope (Qwen, `https://dashscope.aliyuncs.com/compatible-mode/v1`, model `qwen-plus`)
- Ollama (`http://localhost:11434/v1`, model `qwen2.5:7b`)
- Any other provider that exposes `POST /v1/chat/completions`

User specifies provider via `NARRATIVE_PROVIDER` env var; the rest is `*_API_KEY` / `*_BASE_URL` / `*_MODEL`.

CLI:
```bash
python -m run_page.analysis.narrative --facts path/to/facts.json --out narrative.md
# Or, when env var ANTHROPIC_API_KEY is unset, prints a notice and exits 0
# (graceful degradation — Layer 1+2 already produced the facts report).
```

### 6.4 Prompt design (system + user)

**System** (~500 tokens):
- You are an experienced running coach.
- Tone: 亲切、直接、偶尔幽默, like a friend who knows the data.
- Output format: Markdown. Length 200-400 字. No bullet walls.
- Strict rule: do NOT change any numbers from the facts. If a number is missing, say so honestly.
- Language: matches `baselines.owner` locale hint (default: 中文).

**User** (~600-800 tokens):
```
<facts>
{{ facts_json }}
</facts>

<recent_5_sessions>
{{ recent_summaries }}
</recent_sessions>

<baselines>
{{ baselines_yaml }}
</baselines>

Based on the data above, write a 200-400 字 narrative covering:
1. 一个 "花絮" 段 (观察/亮点/异常)
2. 2-3 条个性化改进建议 (结合近期趋势)
3. 1 个坦诚的局限声明 (例如 HRV 不可用时说明)

Do NOT echo the facts tables. The reader will see those below your narrative.
```

### 6.5 Failure modes (all → graceful degradation, return None)

| Failure | Behavior |
|---|---|
| `ANTHROPIC_API_KEY` missing | Skip narrative, facts report still complete |
| API timeout (30s) | Skip, log warning, facts report still complete |
| API 429 (rate limit) | Skip, log warning |
| API 500 (transient) | Skip, log warning |
| Malformed response (no JSON) | Skip, log warning |
| Context too long | Skip, log warning |

The narrative is **always** optional. Layer 1+2 never depends on it.

### 6.6 Output file convention

```
run_page/analyses/
├── 2026-05-16_07-19km.md          # facts report (Layer 2)
└── 2026-05-16_07-19km_narrative.md  # AI narrative (Layer 3, optional)
```

The frontend detail page fetches BOTH if available, renders narrative ABOVE
facts with a "🤖 AI 解读" header to set expectation.

### 6.7 Cost & rate limits

- Model default: `claude-haiku-4-5` (~$0.001/MTok input, ~$0.005/MTok output)
- Expected per run: ~$0.001 (1K input + 0.5K output)
- For 4 cron runs/day: ~$0.12/day = $3.6/year. Negligible.
- Set `NARRATIVE_MODEL=claude-sonnet-4-6` for higher quality at ~10x cost.
- Rate limits: handled by graceful degradation. No retry storm (single attempt).

### 6.8 Testing strategy

| What | How |
|---|---|
| Prompt construction is deterministic | Golden-file comparison (input facts → expected prompt) |
| Output file written correctly | Mock client returns canned response, check file contents |
| API failure → None returned | Mock client raises, generator returns None |
| Missing API key → None returned | Skip without calling |
| Output is **not** byte-stable (it's LLM) | Don't test exact output; test structure (has 花絮 section, has 2-3 suggestions, mentions HRV caveat when missing) |

Tests use `FakeClient` with a hand-written `CompletionResult`. No real API calls in CI.

### 6.9 Multi-provider support

**Why**: One provider is a single point of failure. If Anthropic has a regional
outage, rate limit, or pricing change, the entire narrative pipeline goes
down. Adding OpenAI-compatible support gives a free, drop-in fallback
(DeepSeek, Zhipu, Ollama) and lets users pick by cost/quality preference.

**Design**:
- The `NarrativeClient` Protocol is provider-agnostic — takes `(system, user, max_tokens)`, returns `CompletionResult`. The narrative generator doesn't know or care which provider.
- `get_client(provider=None)` factory dispatches based on `NARRATIVE_PROVIDER` env var.
- Each real client is **lazy-imported** (the `openai` and `anthropic` SDKs are imported only when that provider is selected), so users who pick one provider don't have to install the other's SDK.
- Per-provider env vars are namespaced (`ANTHROPIC_*`, `OPENAI_*`) so the two providers can coexist.

**Cost comparison (1 narrative call, ~2K input + 1K output)**:

| Provider | Model | Cost / call |
|---|---|---|
| Anthropic | `claude-haiku-4-5` | ~$0.001 |
| Anthropic | `claude-sonnet-4-6` | ~$0.015 |
| OpenAI | `gpt-4o-mini` | ~$0.0005 |
| DeepSeek | `deepseek-chat` | ~$0.0003 |
| Zhipu | `glm-4-flash` | ~$0.0001 (几乎免费) |
| Ollama (local) | `qwen2.5:7b` | **$0** |

**Failure isolation**:
- Provider SDK missing → clear error message telling user to `pip install X`
- Provider key missing → factory raises with a hint about which env var to set
- API call fails → `generate_narrative` returns `None`; Layer 1+2 facts report is unaffected (graceful degradation per §6.5)

**Out of scope** (don't add):
- Streaming responses (overkill for a 200-400 字 narrative; would need UI changes)
- Function calling (no tool use needed for narrative)
- Google Gemini (different SDK, different API shape, smaller audience — punt to v2)
- Local model management (Ollama is already OpenAI-compatible; use that)
- Automatic fallback (e.g. "try Anthropic, on 429 try DeepSeek") — can be added later as a wrapper, but adds complexity. Today: user picks one provider in env.

---

---

## 6. Configuration

`run_page/analysis/baselines.yaml`:

```yaml
hrv_baseline_ms: 69
hrv_tolerance_ms: 5
marathon_goal: "2:40:55"
marathon_pace_range: ["3:48", "3:52"]   # s/km = 228..232
biomechanics:
  vertical_oscillation_mm: [60, 70]
  vertical_ratio_pct:     [5.5, 8.0]
  gct_fast_ms:            [190, 210]
  gct_slow_ms:            [210, 230]
  cadence_fast_spm:       [172, 168]
load:
  overload: 1.3
  optimized: 1.0
  maintaining: 0.8
```

Loading: `load_baselines(path: Path | None = None) -> Baselines`
- Path default: `run_page/analysis/baselines.yaml`
- If file missing → use dataclass defaults (which mirror the YAML above).
- Schema validated via Pydantic (already a transitive dep via `sqlalchemy`).

### Environment variables (Layer 3 opt-in)

**Provider selection** (mutually exclusive via `NARRATIVE_PROVIDER`):

| Var | Default | Effect |
|---|---|---|
| `NARRATIVE_PROVIDER` | `anthropic` | `anthropic` / `openai` / `fake` |

**Anthropic path** (when `NARRATIVE_PROVIDER=anthropic`):

| Var | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. If unset, narrative step is skipped. |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | Override for Anthropic-compatible proxies (e.g. `https://api.minimaxi.com/anthropic`) |
| `NARRATIVE_MODEL` | `claude-haiku-4-5` | Model ID (Anthropic, OpenAI, or any provider's model name) |

**OpenAI-compatible path** (when `NARRATIVE_PROVIDER=openai`):

| Var | Default | Effect |
|---|---|---|
| `OPENAI_API_KEY` | — | Required. If unset, factory raises. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Override for any OpenAI-compatible service (DeepSeek, Zhipu, Ollama, DashScope, …) |
| `NARRATIVE_MODEL` | `gpt-4o-mini` | Model name (e.g. `deepseek-chat`, `glm-4-flash`, `qwen-plus`, `qwen2.5:7b`) |

**General** (both paths):

| Var | Default | Effect |
|---|---|---|
| `NARRATIVE_MAX_TOKENS` | `2000` | Cap on response length. Bumped from 1500 → 2000 because 1500 was hitting cap mid-sentence on haiku 4.5. |

---

## 7. CLI

```bash
# one shot
python -m run_page.analysis.cli --label-id 465911765287337995

# batch — latest N
python -m run_page.analysis.cli --latest 5

# all (backfill)
python -m run_page.analysis.cli --all

# refresh baselines from Coros (updates baselines.yaml)
python -m run_page.analysis.cli --calibrate

# output target
python -m run_page.analysis.cli --label-id X --out run_page/analyses/2026-05-16.md
```

Stdout: short status (1 line per analysis: "✓ 2026-05-16 7.19km → .../2026-05-16_07-19km.md")
Stderr: errors with full traceback. Narrative failures print `[warn] narrative skipped: ...` but don't affect exit code.

---

## 8. Phase 4 — Cross-Run Trends & Sparklines (COMPLETE)

### 8.1 4-Week ISO-Week Aggregation

Activities are bucketed by ISO week (Monday–Sunday). A week is included if
it has at least 1 run with `distance_m >= 1000`. Each bucket computes:
- Total distance (km), number of runs, average pace (s/km), standard deviation
- Weekly average of daily `load_ratio` and `hrv_today_ms` (both optional / may be zero)

### 8.2 Sparkline SVGs

Three SVG sparklines are generated per activity and embedded in the Markdown
report via `write_sparklines(md_stem, out_dir, analysis_id)`:

| File | Type | Description |
|---|---|---|
| `{md_stem}_distance.svg` | Bar chart | Distance per run, last 4 weeks |
| `{md_stem}_pace.svg` | Line chart | Pace trend (s/km), last 4 weeks |
| `{md_stem}_consistency.svg` | Ring chart | Consistency % = 100 − (std_dev_pace / mean_pace × 100) |

Images are referenced as `../analyses/{md_stem}_*.svg` from the report file.
If no prior data exists for a week, the bucket is omitted (no empty/spacer bars).

### 8.3 Cross-Run Trend in Markdown Report

The report footer includes a "4-week trend" block:
```
### 近4周趋势
- 周跑量: X.X km（近1周） vs X.X km（上周均） ↑↓
- 配速: X:XX/km（均） ± Xs
- 稳定性: XX%（一致性指数）
```

### 8.4 Coros API Known Issues (as of 2026-06)

The following endpoints are confirmed **non-functional** (HTTP 500, apiCode 5C4D208):

| Endpoint | Status | Workaround |
|---|---|---|
| `POST /activity/detail` | Broken | Use `/activity/query` + local `activities` table fallback |
| `POST /activity/info` | Broken | Same fallback |
| `GET /hrv/assessment` | Broken | `hrv_today_ms = 0`, renderer shows "不可用" |
| `GET /rhr/assessment` | Broken | RHR read from login response `data.rhr` |
| `GET /v2/hrv/...` | Broken | Same as above |
| `GET /training/load` | Broken | Per-activity `trainingLoad` field from `/activity/query` list |
| `GET /user/info` | Broken | Same as above |
| `POST /activity/detail/download` | **Working** | Used by `coros_sync.py` to fetch FIT files |

`activity_meta()` returns from `/activity/query` first, then falls back to the
local `activities` SQLite table using `run_id = label_id`.

`body_state()` computes `load_ratio` as `sum(trainingLoad, 7d acute) / sum(trainingLoad, prior 7d chronic)` from the activity list. RHR comes from the cached login response.

### 8.5 Path Conventions

- `REPO_ROOT = Path(__file__).resolve().parents[3]` — repo root, not `run_page/`
- FIT_OUT: `{REPO_ROOT}/FIT_OUT` (674 files, 2026-06)
- id_map SQLite: `{REPO_ROOT}/run_page/data.db` (table: `coros_id_map`)
- Analyses output: `{REPO_ROOT}/run_page/analyses/`
- baselines.yaml: `{REPO_ROOT}/run_page/analysis/baselines.yaml`

---

## 9. Test plan

Each rule in §4 gets a test that constructs synthetic `Lap` records and
asserts the expected category / grade. Tests live in
`run_page/analysis/tests/` and run with `pytest` (already in `requirements-dev.txt`).
