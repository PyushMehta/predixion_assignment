"""The eval harness's automatic checks must not report false passes."""

from agent.orchestrator import LLMCall, Trace
from agent.schemas import Confidence, Finding, ResearchResponse, Source
from eval.run_eval import QueryResult, EvalQuery, check_response, classify_domain, load_queries, to_markdown


def src(i, url=None):
    return Source(id=f"S{i}", title=f"T{i}", url=url or f"https://site{i}.org/a", snippet="s", relevance=0.9)


def response(n_sources=3, confidence=Confidence.medium, cite=("S1",)):
    return ResearchResponse(
        question="Q?", short_answer="A.", sources=[src(i) for i in range(1, n_sources + 1)],
        key_findings=[Finding(claim="c", source_ids=list(cite))] if cite else [],
        confidence=confidence, confidence_reason="r",
    )


TRACE = Trace(run_id="r", question="Q?", started_at="t", total_ms=12_345, fallbacks=["synthesis_fallback_provider"],
              llm_calls=[LLMCall(stage="plan", ok=True, provider="p", model="m", input_tokens=5, output_tokens=7)])


def test_good_response_passes_all_checks():
    c = check_response(response(), TRACE)
    assert c.schema_valid and c.citations_valid and c.confidence_rules_ok
    assert (c.n_sources, c.latency_s, c.tokens_in, c.tokens_out) == (3, 12.3, 5, 7)
    assert c.fallbacks == ["synthesis_fallback_provider"] and c.llm_calls == ["plan: p/m"]


def test_unknown_citation_is_caught():
    # model_copy(update=...) skips validation, simulating a response that slipped past the schema
    bad = response().model_copy(update={"key_findings": [Finding(claim="c", source_ids=["S1", "S9"])]})
    c = check_response(bad, TRACE)
    assert not c.citations_valid and c.unknown_citations == ["S9"]
    assert not c.schema_valid


def test_confidence_rule_violations_are_caught():
    c = check_response(response(n_sources=1).model_copy(update={"confidence": Confidence.medium}), TRACE)
    assert not c.confidence_rules_ok
    assert c.confidence_rule_violations == ["1 sources but confidence medium (should be low)"]
    high_two = response(n_sources=2).model_copy(update={"confidence": Confidence.high})
    assert "high confidence with 2 sources (needs 3+)" in check_response(high_two, TRACE).confidence_rule_violations


def test_insufficient_evidence_detected():
    c = check_response(response(n_sources=0, confidence=Confidence.low, cite=()), TRACE)
    assert c.insufficient_evidence and c.confidence_rules_ok and c.citations_valid


def test_domain_quality_buckets():
    assert classify_domain("facebook.com") == "low"
    assert classify_domain("m.youtube.com") == "low"
    assert classify_domain("energy.gov") == "authoritative"
    assert classify_domain("cs.stanford.edu") == "authoritative"
    assert classify_domain("ourworldindata.org") == "authoritative"
    assert classify_domain("nextbigfuture.com") == "other"
    assert classify_domain("notfacebook.com") == "other"  # suffix match needs a dot boundary


def test_queries_file_is_valid_and_has_required_cases():
    queries = load_queries()
    assert len(queries) == 8 and len({q.id for q in queries}) == 8
    assert {"nonsense", "prompt_injection", "future_world_cup", "nuclear_vs_wind"} <= {q.id for q in queries}
    assert sum(q.category == "assignment" for q in queries) == 4
    assert all(q.expected.strip() for q in queries)


def test_markdown_has_empty_manual_review_columns():
    r = QueryResult(query=EvalQuery(id="x", category="c", question="Q?", expected="E"), checks=check_response(response(), TRACE))
    md = to_markdown([r], {"started_at": "now", "baseline": False})
    header = next(line for line in md.splitlines() if line.startswith("| id |"))
    assert header.endswith("| correct? | well-supported? | what broke? |")
    row = next(line for line in md.splitlines() if line.startswith("| x |"))
    assert row.endswith("|  |  |  |")
