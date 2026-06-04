"""Layer 3: AI narrative over the deterministic facts.

Public API (mirrors SPEC §6.3):
    from run_page.analysis.narrative import (
        NarrativeContext, Narrative, CompletionResult,
        AnthropicClient, OpenAICompatibleClient, FakeClient,
        get_client,
        generate_narrative, write_narrative,
    )

CLI:
    python -m run_page.analysis.narrative --facts X.json --out narrative.md
"""
from .client import (
    AnthropicClient,
    FakeClient,
    OpenAICompatibleClient,
    get_client,
)
from .generator import (
    build_context,
    generate_narrative,
    load_facts,
    write_narrative,
)
from .models import CompletionResult, Narrative, NarrativeClient, NarrativeContext
from .prompt import SYSTEM_PROMPT, build_user_prompt

__all__ = [
    "AnthropicClient",
    "CompletionResult",
    "FakeClient",
    "Narrative",
    "NarrativeClient",
    "NarrativeContext",
    "OpenAICompatibleClient",
    "SYSTEM_PROMPT",
    "build_context",
    "build_user_prompt",
    "generate_narrative",
    "get_client",
    "load_facts",
    "write_narrative",
]
