"""Tests for the Layer 3 narrative package.

Per SPEC §6.8: tests use FakeClient (no real API calls), cover prompt
construction determinism, file output, and graceful degradation.
"""
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from run_page.analysis.narrative import (
    AnthropicClient,
    CompletionResult,
    FakeClient,
    Narrative,
    NarrativeContext,
    generate_narrative,
    write_narrative,
)
from run_page.analysis.narrative.prompt import SYSTEM_PROMPT, build_user_prompt


# ---------- fixtures ----------


@pytest.fixture
def sample_facts() -> dict:
    return {
        "label_id": "test-label-1",
        "date": "2026-05-16",
        "type": "有氧跑",
        "distance_km": 7.19,
        "duration_s": 3012,
        "avg_pace_s": 419,
        "pace_stats": {"mean_s_per_km": 419, "range_s_per_km": 25, "consistency": "consistent"},
        "hr_stats": {"mean_bpm": 154, "drift_bpm": 2.0, "drift_grade": "excellent"},
        "bio": {
            "cadence_spm_first": 178, "cadence_spm_last": 178,
            "vertical_osc_first_mm": 60, "vertical_osc_last_mm": 62,
            "fatigue_grade": "mild",
        },
        "body_state": {"hrv_today_ms": 0, "rhr_today_bpm": 52, "load_ratio": 1.0},
        "pace_vs_goal": {"target_s_per_km": 284, "actual_s_per_km": 419, "matches": False},
        "recent_sessions": [],
        "laps": [{"i": 1, "d": 1000}, {"i": 2, "d": 1000}],  # intentionally small
    }


@pytest.fixture
def sample_recent() -> list[dict]:
    return [
        {"date": "2026-05-10", "distance_km": 8.0, "avg_pace_s": 410, "load_ratio": 1.1},
        {"date": "2026-05-04", "distance_km": 8.02, "avg_pace_s": 366, "load_ratio": 1.0},
        {"date": "2026-04-28", "distance_km": 10.0, "avg_pace_s": 380, "load_ratio": 1.2},
    ]


@pytest.fixture
def sample_baselines_yaml() -> str:
    return (
        "owner: 9996632@qq.com\n"
        "hrv_baseline_ms: 69\n"
        "marathon_pace_range: [4:39, 4:49]\n"
        "load: {overload: 1.3, optimized: 1.0, maintaining: 0.8}\n"
    )


# ---------- prompt construction ----------


def test_system_prompt_includes_sop_tone():
    """System prompt must anchor SOP tone and structure."""
    assert "教练" in SYSTEM_PROMPT or "coach" in SYSTEM_PROMPT.lower()
    assert "🎯" in SYSTEM_PROMPT and "💡" in SYSTEM_PROMPT and "⚠️" in SYSTEM_PROMPT
    assert "200" in SYSTEM_PROMPT and "400" in SYSTEM_PROMPT  # length bound
    assert "Do NOT change" in SYSTEM_PROMPT or "数字" in SYSTEM_PROMPT  # no number-fudging


def test_build_user_prompt_is_deterministic(sample_facts, sample_recent, sample_baselines_yaml):
    """Same inputs → identical output (no random IDs, no timestamps)."""
    ctx = NarrativeContext(
        facts=sample_facts,
        baselines_yaml=sample_baselines_yaml,
        recent_summaries=tuple(sample_recent),
    )
    p1 = build_user_prompt(ctx)
    p2 = build_user_prompt(ctx)
    assert p1 == p2, "prompt construction must be byte-stable"


def test_build_user_prompt_includes_facts_recent_baselines(sample_facts, sample_recent, sample_baselines_yaml):
    """All three sections (facts, recent, baselines) must appear in the user prompt."""
    ctx = NarrativeContext(
        facts=sample_facts,
        baselines_yaml=sample_baselines_yaml,
        recent_summaries=tuple(sample_recent),
    )
    p = build_user_prompt(ctx)
    assert "<facts>" in p
    assert "</facts>" in p
    assert "<recent_5_sessions>" in p
    assert "</recent_5_sessions>" in p
    assert "<baselines>" in p
    assert "</baselines>" in p
    # facts JSON appears verbatim somewhere
    assert '"label_id": "test-label-1"' in p
    # baselines appears verbatim
    assert "9996632@qq.com" in p


def test_facts_compact_truncates_long_lap_arrays(sample_facts):
    """When facts has many laps, the prompt only shows first 2 + summary."""
    sample_facts["laps"] = [{"i": i} for i in range(20)]  # 20 laps
    ctx = NarrativeContext(facts=sample_facts, baselines_yaml="x: 1")
    compacted = ctx.facts_compact
    # First 2 real laps, then a placeholder string
    assert len(compacted["laps"]) == 3
    assert "+18 more" in compacted["laps"][2]


def test_facts_compact_handles_missing_laps(sample_facts):
    ctx = NarrativeContext(facts=sample_facts, baselines_yaml="x: 1")
    del sample_facts["laps"]
    assert ctx.facts_compact == sample_facts  # unchanged


# ---------- graceful degradation (no API call needed) ----------


def test_missing_api_key_returns_none(sample_facts, sample_recent, sample_baselines_yaml, tmp_path):
    """If api_key is None, generator returns None without calling anything."""
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(sample_facts), encoding="utf-8")
    result = generate_narrative(
        facts_path=facts_path,
        baselines_yaml=sample_baselines_yaml,
        recent_summaries=sample_recent,
        api_key=None,
    )
    assert result is None


def test_missing_facts_file_returns_none(tmp_path, sample_recent, sample_baselines_yaml):
    """facts.json not present → None, not exception."""
    result = generate_narrative(
        facts_path=tmp_path / "does-not-exist.json",
        baselines_yaml=sample_baselines_yaml,
        recent_summaries=sample_recent,
        api_key="sk-fake-key",
    )
    assert result is None


def test_invalid_json_facts_returns_none(tmp_path, sample_recent, sample_baselines_yaml):
    """facts.json is not valid JSON → None, not exception."""
    facts_path = tmp_path / "facts.json"
    facts_path.write_text("not valid json {", encoding="utf-8")
    result = generate_narrative(
        facts_path=facts_path,
        baselines_yaml=sample_baselines_yaml,
        recent_summaries=sample_recent,
        api_key="sk-fake-key",
    )
    assert result is None


def test_client_exception_returns_none(sample_facts, sample_recent, sample_baselines_yaml, tmp_path):
    """Client raises → generator returns None, doesn't crash."""
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(sample_facts), encoding="utf-8")
    fake = FakeClient(raise_exc=RuntimeError("simulated 500"))
    result = generate_narrative(
        facts_path=facts_path,
        baselines_yaml=sample_baselines_yaml,
        recent_summaries=sample_recent,
        client=fake,
    )
    assert result is None
    assert len(fake.calls) == 1, "client should have been called once before failing"


def test_empty_response_returns_none(sample_facts, sample_recent, sample_baselines_yaml, tmp_path):
    """Client returns empty text → None, not crash."""
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(sample_facts), encoding="utf-8")
    fake = FakeClient(canned=CompletionResult(text=""))
    result = generate_narrative(
        facts_path=facts_path,
        baselines_yaml=sample_baselines_yaml,
        recent_summaries=sample_recent,
        client=fake,
    )
    assert result is None


# ---------- success path ----------


def test_successful_call_returns_narrative(sample_facts, sample_recent, sample_baselines_yaml, tmp_path):
    """Client returns valid text → Narrative with all metadata."""
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(sample_facts), encoding="utf-8")
    canned = CompletionResult(
        text="## 🎯 花絮\n本课配速 6:59/km\n\n## 💡 改进建议\n- 跑休\n\n## ⚠️ 局限\nHRV 不可用",
        prompt_tokens=1200,
        completion_tokens=250,
        model="claude-haiku-4-5",
    )
    fake = FakeClient(canned=canned)
    result = generate_narrative(
        facts_path=facts_path,
        baselines_yaml=sample_baselines_yaml,
        recent_summaries=sample_recent,
        client=fake,
    )
    assert result is not None
    assert result.label_id == "test-label-1"
    assert result.markdown == canned.text
    assert result.prompt_tokens == 1200
    assert result.completion_tokens == 250
    assert result.model == "claude-haiku-4-5"


def test_generated_at_can_be_injected(sample_facts, sample_recent, sample_baselines_yaml, tmp_path):
    """`now` parameter is injectable for testability."""
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(sample_facts), encoding="utf-8")
    fake = FakeClient(canned=CompletionResult(text="ok"))
    fixed_now = datetime(2026, 5, 16, 18, 21, 0)
    result = generate_narrative(
        facts_path=facts_path,
        baselines_yaml=sample_baselines_yaml,
        recent_summaries=sample_recent,
        client=fake,
        now=fixed_now,
    )
    assert result.generated_at == fixed_now


# ---------- file output ----------


def test_write_narrative_creates_frontmatter(tmp_path):
    """write_narrative prepends YAML frontmatter with metadata."""
    from run_page.analysis.narrative import Narrative

    n = Narrative(
        label_id="test-label-1",
        markdown="## 🎯 花絮\nbody",
        model="claude-haiku-4-5",
        prompt_tokens=100,
        completion_tokens=200,
        generated_at=datetime(2026, 5, 16, 18, 21, 0),
    )
    out = tmp_path / "narrative.md"
    write_narrative(n, out)

    content = out.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "label_id: test-label-1" in content
    assert "model: claude-haiku-4-5" in content
    assert "prompt_tokens: 100" in content
    assert "completion_tokens: 200" in content
    assert "generated_at: 2026-05-16T18:21:00" in content
    assert "## 🎯 花絮" in content


def test_write_narrative_creates_parent_dirs(tmp_path):
    n = Narrative(label_id="x", markdown="body")
    out = tmp_path / "deep" / "nested" / "narrative.md"
    write_narrative(n, out)
    assert out.exists()


# ---------- client ----------


def test_anthropic_client_requires_api_key():
    with pytest.raises(ValueError, match="api_key"):
        AnthropicClient(api_key="", model="x")


def test_fake_client_records_calls(sample_facts, sample_baselines_yaml, tmp_path):
    """FakeClient records call args so tests can assert what was sent."""
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(sample_facts), encoding="utf-8")
    fake = FakeClient(canned=CompletionResult(text="ok"))
    generate_narrative(
        facts_path=facts_path,
        baselines_yaml=sample_baselines_yaml,
        client=fake,
        max_tokens=500,
    )
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["max_tokens"] == 500
    assert call["system_chars"] > 100  # system prompt was passed
    assert call["user_chars"] > 100  # user prompt was passed
