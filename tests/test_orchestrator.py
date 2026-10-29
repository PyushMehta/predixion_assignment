"""End-to-end orchestrator tests with fake search, scrape and LLM. No network."""

import json

import pytest

from agent.orchestrator import RunResult, run
from agent.schemas import Confidence
from agent.tools.results import PageResult, SearchItem, SearchResult
from llm_fakes import FakeClient, completion, json_completion, provider, timeout_error

QUESTION = "Is nuclear power safer than wind?"
SNIPPET = "Deaths per TWh: nuclear about 0.03, wind about 0.04, according to recent studies."
PAGE_TEXT = "Detailed analysis of deaths per terawatt-hour by energy source. " * 12

PLAN = {
    "question": QUESTION,
    "sub_questions": ["Nuclear deaths per TWh?", "Wind deaths per TWh?"],
    "steps": [
        {"step_id": 1, "goal": "nuclear", "tool": "web_search", "query": "nuclear deaths per TWh"},
        {"step_id": 2, "goal": "wind", "tool": "web_search", "query": "wind deaths per TWh"},
        {"step_id": 3, "goal": "compare", "tool": "none", "query": None},
    ],
}

RESULTS = {
    "nuclear deaths per TWh": [
        SearchItem(title="OWID safety", url="https://ourworldindata.org/safety", snippet=SNIPPET, score=0.95),
        SearchItem(title="Low relevance", url="https://blog.example/x", snippet=SNIPPET, score=0.2),
    ],
    "wind deaths per TWh": [
        SearchItem(title="Wind study", url="https://wind.example/study", snippet=SNIPPET, score=0.85),
        SearchItem(title="Energy stats", url="https://stats.example/energy", snippet=SNIPPET, score=0.75),
    ],
}


def answer(ids=("S1", "S2"), confidence="medium"):
    return {
        "short_answer": "Nuclear and wind have similarly low death rates per TWh.",
        "key_findings": [{"claim": "Both are far safer than fossil fuels.", "source_ids": list(ids)}],
        "confidence": confidence,
        "confidence_reason": "Sources agree.",
        "assumptions": [], "limitations": [], "next_steps": [],
    }


class FakeTools:
    """Fake search_fn and scrape_fn that record calls."""

    def __init__(self, results=None, pages=None, on_search=None):
        self.results = RESULTS if results is None else results
        self.pages = pages or {}
        self.on_search = on_search
        self.searched, self.scraped = [], []

    def search(self, query, max_results=5):
        self.searched.append(query)
        if self.on_search:
            self.on_search()
        items = self.results.get(query, [])
        return SearchResult(query=query, items=items, ok=bool(items), error=None if items else "no search results")

    def scrape(self, url):
        self.scraped.append(url)
        default = PageResult(url=url, title="Page", text=PAGE_TEXT, ok=True)
        return self.pages.get(url, default)


def go(tools, *llm_script, tmp_path=None, question=QUESTION):
    client = FakeClient(*llm_script)
    result = run(question, providers=[provider(client)], search_fn=tools.search, scrape_fn=tools.scrape, runs_dir=tmp_path)
    return result, client


# ---------- required scenarios ----------

def test_happy_path_end_to_end(tmp_path):
    tools = FakeTools()
    result, client = go(tools, json_completion(PLAN), json_completion(answer(("S1", "S2", "S3"), "high")), tmp_path=tmp_path)
    r, t = result.response, result.trace

    assert r.confidence == Confidence.high
    assert [str(s.url) for s in r.sources] == [
        "https://ourworldindata.org/safety", "https://wind.example/study", "https://stats.example/energy",
    ]
    assert sorted(tools.searched) == ["nuclear deaths per TWh", "wind deaths per TWh"]
    assert set(tools.scraped) == {s for s in (str(x.url) for x in r.sources)}  # low-relevance result not scraped
    assert len(client.calls) == 2  # planner + synthesis, the full LLM budget
    assert [c.stage for c in t.llm_calls] == ["plan", "synthesis"]
    assert any(d.reason == "relevance 0.20 below threshold 0.5" for d in t.filter_dropped)
    assert any("reasoning-only step" in s.reason for s in t.skipped)
    assert t.fallbacks == [] and t.guardrails == [] and not t.deadline_hit

    saved = RunResult.model_validate_json((tmp_path / f"{t.run_id}.json").read_text(encoding="utf-8"))
    assert saved.response == r and saved.trace.plan.steps == t.plan.steps


def test_search_returns_nothing_gives_insufficient_evidence_without_synthesis():
    result, client = go(FakeTools(results={}), json_completion(PLAN))
    r = result.response
    assert r.short_answer.startswith("Insufficient evidence")
    assert r.confidence == Confidence.low and r.sources == [] and r.key_findings == []
    assert len(client.calls) == 1  # planner only
    assert "synthesis skipped" in result.trace.guardrails[0]


def test_all_scrapes_fail_answers_from_snippets_with_lower_confidence():
    failing = {u: PageResult(url=u, ok=False, error="HTTP 403") for u in
               ["https://ourworldindata.org/safety", "https://wind.example/study", "https://stats.example/energy"]}
    result, client = go(FakeTools(pages=failing), json_completion(PLAN), json_completion(answer(("S1", "S2", "S3"), "high")))
    r = result.response
    assert len(r.sources) == 3
    assert r.confidence == Confidence.medium  # capped: snippets only
    assert any("only search snippets" in g for g in result.trace.guardrails)
    assert "type: snippet only" in client.calls[1]["messages"][1]["content"]
    assert sum(not c.ok for c in result.trace.tool_calls if c.tool == "scrape_page") == 3


def test_suspicious_page_is_dropped_and_recorded():
    evil = "https://wind.example/study"
    pages = {evil: PageResult(url=evil, title="Wind", text=PAGE_TEXT, ok=True, suspicious=True,
                              injection_matches=["Ignore previous instructions"])}
    result, client = go(FakeTools(pages=pages), json_completion(PLAN), json_completion(answer()))
    assert evil not in [str(s.url) for s in result.response.sources]
    drop = next(d for d in result.trace.filter_dropped if d.url == evil)
    assert "suspicious page" in drop.reason and "Ignore previous instructions" in drop.reason
    assert evil not in client.calls[1]["messages"][1]["content"]  # never shown to the LLM


def test_fake_citation_is_fixed_on_retry():
    result, client = go(FakeTools(), json_completion(PLAN),
                        json_completion(answer(("S1", "S9"))), json_completion(answer(("S1", "S2"))))
    assert result.trace.fallbacks == []
    assert "unknown source ids ['S9']" in client.calls[2]["messages"][-1]["content"]
    assert result.trace.llm_calls[1].ok


def test_synthesis_fails_completely_gives_fallback_response():
    result, _ = go(FakeTools(), json_completion(PLAN), timeout_error(), timeout_error(), timeout_error())
    r = result.response
    assert r.confidence == Confidence.low
    assert r.key_findings[0].claim.startswith("Unverified excerpt")
    assert any("Automatic synthesis failed" in lim for lim in r.limitations)
    assert "synthesis_fallback_response" in result.trace.fallbacks
    assert len(r.sources) == 3


def test_deadline_exceeded_answers_with_what_was_gathered(clock):
    tools = FakeTools(on_search=lambda: clock.advance(70))  # searches eat past the gathering deadline
    result, _ = go(tools, json_completion(PLAN), json_completion(answer()))
    t, r = result.trace, result.response
    assert t.deadline_hit
    assert tools.scraped == []  # scraping skipped
    assert any("run deadline reached" in s.reason for s in t.skipped)
    assert any("time budget" in lim for lim in r.limitations)
    assert len(r.sources) == 3  # answered from snippets


def test_planner_url_not_from_search_is_not_scraped():
    plan = dict(PLAN, steps=PLAN["steps"] + [
        {"step_id": 4, "goal": "read", "tool": "scrape_page", "query": "https://made-up.example/report"},
    ])
    tools = FakeTools()
    result, _ = go(tools, json_completion(plan), json_completion(answer()))
    assert "https://made-up.example/report" not in tools.scraped
    skip = next(s for s in result.trace.skipped if s.url == "https://made-up.example/report")
    assert "not in search results or the question" in skip.reason


# ---------- more guardrails and edge cases ----------

def test_url_in_users_question_is_scraped_first():
    tools = FakeTools()
    q = "Summarise https://user.example/paper. Is it right about wind?"
    result, _ = go(tools, json_completion(dict(PLAN, question=q)), json_completion(answer()), question=q)
    assert tools.scraped[0] == "https://user.example/paper"
    assert "https://user.example/paper" in [str(s.url) for s in result.response.sources]


def test_single_source_forces_low_confidence():
    results = {"nuclear deaths per TWh": RESULTS["nuclear deaths per TWh"][:1]}
    result, _ = go(FakeTools(results=results), json_completion(PLAN), json_completion(answer(("S1",))))
    assert result.response.confidence == Confidence.low
    assert any("only 1 usable source" in g for g in result.trace.guardrails)


def test_planner_failure_uses_fallback_plan_and_says_so():
    tools = FakeTools(results={QUESTION: RESULTS["wind deaths per TWh"]})
    result, _ = go(tools, completion("x"), completion("y"), json_completion(answer()))
    assert tools.searched == [QUESTION]
    assert "planner_fallback_plan" in result.trace.fallbacks
    assert any("Planning failed" in lim for lim in result.response.limitations)


def test_scrapes_capped_at_four():
    many = {"nuclear deaths per TWh": [
        SearchItem(title=f"T{i}", url=f"https://site{i}.example/x", snippet=SNIPPET, score=0.9) for i in range(7)
    ]}
    tools = FakeTools(results=many)
    go(tools, json_completion(PLAN), json_completion(answer()))
    assert len(tools.scraped) == 4


def test_trace_records_tool_and_llm_details():
    result, _ = go(FakeTools(), json_completion(PLAN), json_completion(answer()))
    t = result.trace
    search_calls = [c for c in t.tool_calls if c.tool == "web_search"]
    assert {c.input for c in search_calls} == {"nuclear deaths per TWh", "wind deaths per TWh"}
    assert all(c.ok and "results" in c.detail for c in search_calls)
    assert t.llm_calls[0].provider == "primary.test" and t.llm_calls[0].input_tokens == 10
    assert len(t.filter_kept) == 3 and t.filter_kept[0].startswith("S1 https://")
    json.loads(result.model_dump_json())  # whole result is JSON-serialisable


@pytest.mark.parametrize("bad_tool", ["search", "scrape"])
def test_a_tool_that_raises_does_not_crash_the_run(bad_tool):
    tools = FakeTools()

    def boom(*args, **kwargs):
        raise RuntimeError("tool bug")

    setattr(tools, bad_tool, boom)
    result, _ = go(tools, json_completion(PLAN), json_completion(answer()))
    assert any(c.error == "RuntimeError: tool bug" for c in result.trace.tool_calls)
