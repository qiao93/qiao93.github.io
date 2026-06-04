"""Layer 3 data model — the AI narrative layer.

These types are the *contract* between:
  - the deterministic facts (Layer 1+2)
  - the prompt builder
  - the LLM client
  - the on-disk artifact (<date>_narrative.md)

Everything is frozen + hashable so we can cache by content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class NarrativeContext:
    """Inputs to the LLM prompt. Built from facts.json + recent history + baselines."""
    facts: dict                               # the full facts.json (machine-readable)
    baselines_yaml: str                       # raw YAML, so the LLM sees what we calibrated
    recent_summaries: tuple[dict, ...] = ()  # last N runs' meta + scores (no full laps)
    sop_excerpt: str = ""                     # inline SOP section to anchor tone

    @property
    def facts_compact(self) -> dict:
        """Facts trimmed for the prompt — drop large arrays (laps, recent laps).
        The reader can re-derive detail from facts.json on disk if needed."""
        if not self.facts:
            return {}
        out = dict(self.facts)
        # Keep first 2 laps as flavor, drop the rest
        laps = out.get("laps")
        if isinstance(laps, list) and len(laps) > 2:
            out["laps"] = laps[:2] + [f"... +{len(laps)-2} more"]
        return out


@dataclass(frozen=True)
class CompletionResult:
    """Single LLM completion (input from client.complete())."""
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


@dataclass(frozen=True)
class Narrative:
    """The output artifact. Saved as <date>_<name>_narrative.md alongside facts.md."""
    label_id: str
    markdown: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    generated_at: datetime = field(default_factory=datetime.now)


# --- client port (matches SPEC §6.3) ---


class NarrativeClient(Protocol):
    """Boundary for LLM calls. Tests inject a FakeClient; production uses AnthropicClient."""

    def complete(self, system: str, user: str, max_tokens: int) -> CompletionResult: ...
