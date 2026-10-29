"""Phase 5 fixes 1, 4, 5: length trimming in code, unknowable questions, one action per next step."""

from datetime import date

from agent.filter import FilteredSource
from agent.schemas import Confidence, Source
from agent.synthesizer import (
    SHORT_ANSWER_MAX, UNKNOWABLE_LIMITATION, looks_unknowable, split_next_steps, synthesize_detailed,
)
from agent.textutil import trim_at_sentence
from llm_fakes import FakeClient, json_completion, provider


def fsource(i):
    return FilteredSource(
        source=Source(id=f"S{i}", title=f"T{i}", url=f"https://site{i}.org/a", snippet="s", relevance=0.9),
        text="Evidence " * 20, from_page=True,
    )


SOURCES = [fsource(1), fsource(2), fsource(3)]


def answer(short="An answer.", confidence="medium", next_steps=()):
    return {
        "short_answer": short,
        "key_findings": [{"claim": "c", "source_ids": ["S1"]}],
        "confidence": confidence, "confidence_reason": "r",
        "limitations": [], "next_steps": list(next_steps),
    }


def synth(question, *script):
    client = FakeClient(*script)
    return synthesize_detailed(question, SOURCES, providers=[provider(client)]), client


# ---------- fix 1: short_answer length ----------

def test_trim_at_sentence_boundary():
    text = "First sentence. Second sentence is here. Third one runs on and on."
    assert trim_at_sentence(text, 45) == "First sentence. Second sentence is here."
    assert trim_at_sentence("no sentence end just words " * 5, 40).endswith("…")
    assert trim_at_sentence("short", 100) == "short"


def test_slightly_long_short_answer_is_trimmed_in_code_without_retry():
    long = ("This sentence is about forty characters. " * 30)[:1200]
    outcome, client = synth("Compare A and B?", json_completion(answer(short=long)))
    assert len(client.calls) == 1  # no LLM retry
    assert len(outcome.response.short_answer) <= SHORT_ANSWER_MAX
    assert outcome.response.short_answer.endswith(".")
    assert any("short_answer trimmed from 1200" in a for a in outcome.adjustments)


def test_far_too_long_short_answer_still_goes_to_llm_repair():
    outcome, client = synth("Compare A and B?", json_completion(answer(short="x" * 1600)), json_completion(answer()))
    assert len(client.calls) == 2
    assert "short_answer" in client.calls[1]["messages"][-1]["content"]
    assert not outcome.used_fallback_response


def test_prompt_states_the_limit():
    _, client = synth("Compare A and B?", json_completion(answer()))
    assert "at most 1000 characters" in client.calls[0]["messages"][0]["content"]


# ---------- fix 4: future / unknowable questions ----------

def test_unknowable_detection():
    today = date(2026, 10, 29)
    assert looks_unknowable("Who will win the 2030 FIFA World Cup?", today)
    assert looks_unknowable("What is the forecast for India's HR-tech market?", today)
    assert looks_unknowable("Will Spain win the next World Cup?", today)
    assert looks_unknowable("What are the qzxv ratings for 2031?", today)
    assert not looks_unknowable("Who won the 2022 FIFA World Cup?", today)
    assert not looks_unknowable("Compare the top 3 open-source vector databases.", today)


def test_future_question_forced_low_with_limitation():
    outcome, _ = synth("Who will win the 2030 FIFA World Cup?", json_completion(answer(confidence="high")))
    r = outcome.response
    assert r.confidence == Confidence.low
    assert UNKNOWABLE_LIMITATION in r.limitations
    assert "not facts" in UNKNOWABLE_LIMITATION
    assert any("future/unknowable" in a for a in outcome.adjustments)


def test_future_rule_is_in_the_prompt():
    _, client = synth("Who will win?", json_completion(answer()))
    assert "predictions, odds and forecasts are not facts" in client.calls[0]["messages"][0]["content"]


# ---------- fix 5: next_steps ----------

def test_next_steps_split_into_single_actions():
    steps = ["Benchmark latency; compare recall\nCheck licences", "1. Run a pilot.", "- Run a pilot"]
    assert split_next_steps(steps) == ["Benchmark latency.", "Compare recall.", "Check licences.", "Run a pilot."]


def test_next_steps_capped_at_five():
    assert len(split_next_steps([f"Do thing {i}" for i in range(9)])) == 5


def test_next_steps_split_applied_to_answer():
    outcome, _ = synth("Compare A and B?", json_completion(answer(next_steps=["Read docs; run a benchmark"])))
    assert outcome.response.next_steps == ["Read docs.", "Run a benchmark."]
