"""Orchestrator for Layer 3: build context → call LLM → return Narrative.

Per SPEC §6.3 / §6.5: returns `None` on any failure (missing key, API
timeout, rate limit, malformed response, context too long). The CLI treats
`None` as "skip narrative, facts report is enough".
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .client import AnthropicClient, get_client
from .models import Narrative, NarrativeClient, NarrativeContext
from .prompt import SYSTEM_PROMPT, build_user_prompt

log = logging.getLogger(__name__)


def load_facts(path: Path) -> dict:
    """Read facts.json. Raises if the file is missing or not valid JSON.
    Callers should wrap in try/except if they want graceful degradation."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_context(
    facts: dict,
    baselines_yaml: str,
    recent_summaries: list[dict] | None = None,
    sop_excerpt: str = "",
) -> NarrativeContext:
    return NarrativeContext(
        facts=facts,
        baselines_yaml=baselines_yaml,
        recent_summaries=tuple(recent_summaries or ()),
        sop_excerpt=sop_excerpt,
    )


def generate_narrative(
    facts_path: Path,
    *,
    baselines_yaml: str,
    recent_summaries: list[dict] | None = None,
    sop_excerpt: str = "",
    api_key: str | None = None,
    model: str = "claude-haiku-4-5",
    max_tokens: int = 2000,
    now: datetime | None = None,
    client: NarrativeClient | None = None,
) -> Narrative | None:
    """Read facts.json, call LLM, return Narrative. Returns None on any failure.

    Args:
        facts_path: path to facts.json (or the .md file with embedded JSON,
            we accept both).
        baselines_yaml: raw YAML text, inlined into the prompt.
        recent_summaries: list of past facts (compact), so the LLM has cross-run
            context. Empty list is fine.
        sop_excerpt: optional SOP text to anchor the LLM's tone.
        api_key: DEPRECATED. Use `client` or set OPENAI_API_KEY/ANTHROPIC_API_KEY
            env vars and let `get_client()` pick the right provider.
        model: used only if `client` is None AND `get_client()` is used; the
            model env var wins when set.
        max_tokens: response cap. 2000 by default (1500 was too tight).
        now: injected for testability (default datetime.now).
        client: pre-built client (for tests); if None, falls through to
            `get_client()` which uses NARRATIVE_PROVIDER env.
    """
    # 1. Load facts first (independent of client)
    try:
        facts = load_facts(facts_path)
    except FileNotFoundError:
        log.warning("narrative: facts file not found: %s", facts_path)
        return None
    except json.JSONDecodeError as exc:
        log.warning("narrative: facts file is not valid JSON: %s", exc)
        return None

    # 2. Build context + prompt
    ctx = build_context(facts, baselines_yaml, recent_summaries, sop_excerpt)
    user_prompt = build_user_prompt(ctx)

    # 3. Build / inject client
    if client is None:
        try:
            client = get_client()  # picks provider from NARRATIVE_PROVIDER
        except ValueError as exc:
            # Provider env not set → graceful skip (Layer 1+2 still produced facts)
            log.info("narrative: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("narrative: failed to build client: %s", exc)
            return None

    # 4. Call LLM with graceful degradation
    try:
        result = client.complete(SYSTEM_PROMPT, user_prompt, max_tokens)
    except Exception as exc:  # noqa: BLE001
        log.warning("narrative: LLM call failed (%s): %s", type(exc).__name__, exc)
        return None

    if not result.text or not result.text.strip():
        log.warning("narrative: LLM returned empty text")
        return None

    return Narrative(
        label_id=str(facts.get("label_id", facts_path.stem)),
        markdown=result.text,
        model=result.model or model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        generated_at=now or datetime.now(),
    )


def write_narrative(narrative: Narrative, out_path: Path) -> Path:
    """Save Narrative to disk. Creates parent dirs if needed.

    Prepends a small YAML-style frontmatter so future tooling can parse
    out metadata without re-running the LLM.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = (
        "---\n"
        f"label_id: {narrative.label_id}\n"
        f"model: {narrative.model}\n"
        f"prompt_tokens: {narrative.prompt_tokens}\n"
        f"completion_tokens: {narrative.completion_tokens}\n"
        f"generated_at: {narrative.generated_at.isoformat()}\n"
        "---\n\n"
    )
    out_path.write_text(frontmatter + narrative.markdown, encoding="utf-8")
    return out_path
