"""Task-dependent tool selection: the planner's needs_full_pages decides whether pages are scraped."""

from agent.orchestrator import run
from agent.planner import SYSTEM_PROMPT, fallback_plan, make_plan_detailed
from agent.schemas import Confidence, Plan
from eval.run_eval import check_response
from llm_fakes import FakeClient, json_completion, provider
from test_orchestrator import PLAN, QUESTION, FakeTools, answer

SNIPPETS_PLAN = dict(PLAN, needs_full_pages=False)


def go(plan, *, question=QUESTION, tools=None, synthesis=None):
    tools = tools or FakeTools()
    client = FakeClient(json_completion(plan), json_completion(synthesis or answer(("S1", "S2", "S3"), "high")))
    result = run(question, providers=[provider(client)], search_fn=tools.search, scrape_fn=tools.scrape, runs_dir=None)
    return result, tools, client


# ---------- planner ----------

def test_planner_passes_the_decision_through():
    outcome = make_plan_detailed(QUESTION, providers=[provider(FakeClient(json_completion(SNIPPETS_PLAN)))])
    assert outcome.plan.needs_full_pages is False


def test_missing_decision_defaults_to_full_pages():
    assert "needs_full_pages" not in PLAN
    outcome = make_plan_detailed(QUESTION, providers=[provider(FakeClient(json_completion(PLAN)))])
    assert outcome.plan.needs_full_pages is True
    assert Plan(question="q", sub_questions=["q"], steps=fallback_plan("q").steps).needs_full_pages is True


def test_fallback_plan_reads_full_pages():
    assert fallback_plan("anything").needs_full_pages is True


def test_prompt_explains_when_snippets_are_enough():
    for phrase in ("needs_full_pages", "simple factual question", "comparisons", "When unsure, set true"):
        assert phrase in SYSTEM_PROMPT


# ---------- orchestrator ----------

def test_snippets_only_plan_skips_scraping_and_caps_confidence():
    result, tools, client = go(SNIPPETS_PLAN)
    t, r = result.trace, result.response
    assert tools.scraped == []
    assert len(tools.searched) == 2  # searching still happens
    assert t.tool_selection.startswith("snippets only: planner set needs_full_pages=false")
    assert "type: snippet only" in client.calls[1]["messages"][1]["content"]
    assert r.confidence == Confidence.medium  # model said high; snippet-only cap applies
    assert any("only search snippets" in g for g in t.guardrails)


def test_full_pages_plan_scrapes_as_before():
    result, tools, _ = go(dict(PLAN, needs_full_pages=True))
    assert len(tools.scraped) == 3
    assert result.trace.tool_selection.startswith("full pages")


def test_url_in_question_is_still_read_when_snippets_suffice():
    q = "What does https://user.example/page say about wind?"
    plan = dict(SNIPPETS_PLAN, question=q)
    result, tools, _ = go(plan, question=q)
    assert tools.scraped == ["https://user.example/page"]


def test_planner_scrape_steps_are_skipped_and_recorded():
    plan = dict(SNIPPETS_PLAN, steps=PLAN["steps"] + [
        {"step_id": 4, "goal": "read", "tool": "scrape_page", "query": "https://ourworldindata.org/safety"},
    ])
    result, tools, _ = go(plan)
    assert tools.scraped == []
    skip = next(s for s in result.trace.skipped if s.url == "https://ourworldindata.org/safety")
    assert skip.reason == "scrape skipped: needs_full_pages=false"


# ---------- eval harness ----------

def test_harness_records_the_decision_and_pages_read():
    snippets, _, _ = go(SNIPPETS_PLAN)
    c = check_response(snippets.response, snippets.trace)
    assert (c.needs_full_pages, c.pages_read) == (False, 0)
    full, _, _ = go(PLAN)
    c = check_response(full.response, full.trace)
    assert (c.needs_full_pages, c.pages_read) == (True, 3)
