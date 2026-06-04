"""Prompt templates for Layer 3 (AI narrative).

The system + user prompts are concatenated at runtime. Both are pure functions
of `NarrativeContext` so tests can do golden-file comparison.

Design notes (per SPEC §6.4):
  - System prompt sets persona (running coach, SOP tone).
  - User prompt carries the data, then asks for 3 sections:
      1. 花絮  (a hook, observation, or anomaly)
      2. 2-3 personalized suggestions (cross-run aware)
      3. 1 honest caveat (e.g. "HRV not available")
  - Total target: 200-400 字, 1-2K tokens total context.
  - Output is Markdown; no echo of facts tables.
"""
from __future__ import annotations

from .models import NarrativeContext


SYSTEM_PROMPT = """\
You are an experienced running coach writing a post-run report for a personal runner.
Your tone matches the runner's own training SOP: 亲切、直接、偶尔幽默, like a friend who \
knows the data inside-out and gives honest, practical advice.

Output format:
  - Language: 中文
  - Length: 200-400 字 (count Chinese characters, not bytes)
  - Format: Markdown, 3 short sections in this exact order:
      ## 🎯 花絮
        1 short paragraph. A hook, observation, or anomaly. \
Use specific numbers from the data, not generic encouragement.
      ## 💡 改进建议
        2-3 bullet points, each with a concrete next-week action \
(练什么 / 怎么练 / 注意什么). Tie back to recent trends if visible.
      ## ⚠️ 局限
        1 sentence naming what the data CANNOT tell us right now \
(e.g. HRV 不可用 / 训练负荷比值用 SOP 默认值 / 没有与近 4 周对比).

Strict rules:
  - Do NOT change any number from the input. If a number looks odd, mention \
it in 花絮 but keep the original value.
  - Do NOT repeat the data tables — the reader will see them right below your narrative.
  - Do NOT use bullet walls. 花絮 should be 1-2 sentences; suggestions should be \
terse (1 line each).
  - No emojis in the body text — only in the section headers (🎯 / 💡 / ⚠️).
  - No hashtags, no promotional language.
"""


def build_user_prompt(ctx: NarrativeContext) -> str:
    """Construct the user prompt. Pure function — testable via golden files."""
    import json

    facts_json = json.dumps(ctx.facts_compact, ensure_ascii=False, indent=2)
    recent_json = json.dumps(list(ctx.recent_summaries), ensure_ascii=False, indent=2)

    return f"""\
<facts>
{facts_json}
</facts>

<recent_5_sessions>
{recent_json}
</recent_5_sessions>

<baselines>
{ctx.baselines_yaml.strip()}
</baselines>

{sop_section(ctx.sop_excerpt)}

Based on the data above, write the 3-section narrative per the system format.
Remember: 200-400 字 total. 2-3 suggestions in 💡 section. 1 sentence in ⚠️.
"""


def sop_section(excerpt: str) -> str:
    """If the user has supplied an SOP excerpt, surface it as additional context."""
    if not excerpt:
        return ""
    return (
        "<sop_excerpt>\n"
        "The runner's own training methodology (for tone + grading conventions):\n"
        f"{excerpt.strip()}\n"
        "</sop_excerpt>\n"
    )
