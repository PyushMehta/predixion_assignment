"""Phase 5 behaviour seen end to end through the orchestrator (fakes only)."""

from agent.orchestrator import RUN_DEADLINE_S, run
from agent.schemas import Confidence
from agent.tools.results import PageResult, SearchItem
from llm_fakes import FakeClient, json_completion, provider
from test_orchestrator import PLAN, QUESTION, RESULTS, SNIPPET, FakeTools, answer


def go(tools, client):
    return run(QUESTION, providers=[provider(client)], search_fn=tools.search, scrape_fn=tools.scrape, runs_dir=None)


def test_synthesis_skipped_when_run_deadline_leaves_no_time(clock):
    # Gathering finishes inside its own deadline, but then the clock jumps past the run deadline.
    tools = FakeTools()
    original_scrape = tools.scrape

    def slow_scrape(url):
        clock.advance(30)  # 4 scrapes in parallel threads, ~120s of fake time in total
        return original_scrape(url)

    tools.scrape = slow_scrape
    client = FakeClient(json_completion(PLAN))  # no synthesis response scripted: it must not be called
    result = go(tools, client)
    assert len(client.calls) == 1
    assert result.response.confidence == Confidence.low
    assert "synthesis_fallback_response" in result.trace.fallbacks
    assert result.trace.deadline_hit
    assert result.trace.llm_calls[1].error_kind == "deadline"
    assert clock.now >= RUN_DEADLINE_S


def test_relevant_passage_from_deep_in_page_reaches_the_llm():
    long_page = ("Unrelated history of electricity markets and grid policy. " * 80
                 + "Deaths per TWh: nuclear 0.03, wind 0.04. ")
    url = "https://ourworldindata.org/safety"
    tools = FakeTools(pages={url: PageResult(url=url, title="OWID", text=long_page, ok=True)})
    client = FakeClient(json_completion(PLAN), json_completion(answer()))
    result = go(tools, client)
    prompt = client.calls[1]["messages"][1]["content"]
    assert "nuclear 0.03, wind 0.04" in prompt
    assert any("relevant passages" in n for n in result.trace.filter_notes)


def test_synthesizer_adjustments_recorded_in_trace():
    client = FakeClient(json_completion(PLAN), json_completion(dict(answer(), next_steps=["Do A; do B"])))
    result = go(FakeTools(), client)
    assert result.response.next_steps == ["Do A.", "Do B."]
    assert any(g.startswith("synthesizer: next_steps split") for g in result.trace.guardrails)


def test_scrape_targets_prefer_quality_over_raw_score():
    results = {"nuclear deaths per TWh": [
        SearchItem(title="FB", url="https://www.facebook.com/p/1", snippet=SNIPPET, score=0.95),
        SearchItem(title="YT", url="https://www.youtube.com/watch?v=1", snippet=SNIPPET, score=0.94),
        SearchItem(title="A", url="https://a.example/x", snippet=SNIPPET, score=0.8),
        SearchItem(title="B", url="https://b.example/x", snippet=SNIPPET, score=0.8),
        SearchItem(title="C", url="https://c.example/x", snippet=SNIPPET, score=0.8),
        SearchItem(title="OWID", url="https://ourworldindata.org/x", snippet=SNIPPET, score=0.7),
    ]}
    tools = FakeTools(results=results)
    go(tools, FakeClient(json_completion(PLAN), json_completion(answer())))
    assert "https://ourworldindata.org/x" in tools.scraped
    assert "https://www.facebook.com/p/1" not in tools.scraped
    assert RESULTS  # shared fixtures still importable
