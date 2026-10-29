"""Overconfidence fix: "high" only with 3+ cited full-page sources from different, non-vendor sites."""

from agent.filter import FilteredSource
from agent.orchestrator import MIN_INDEPENDENT_FULL_PAGES, high_confidence_support, run
from agent.quality import is_vendor_source, named_entities, site_name
from agent.schemas import Confidence, Finding, ResearchResponse, Source
from agent.tools.results import PageResult, SearchItem
from llm_fakes import FakeClient, json_completion, provider
from test_orchestrator import PLAN, SNIPPET, FakeTools

QUESTION = "Compare Milvus, Qdrant and Weaviate for RAG"


def fs(i, url, from_page=True):
    return FilteredSource(
        source=Source(id=f"S{i}", title=f"T{i}", url=url, snippet="s", relevance=0.9),
        text="t" * 400, from_page=from_page,
    )


def resp(sources, cited=None, claim="Each database has different strengths for retrieval."):
    ids = cited or [s.source.id for s in sources]
    return ResearchResponse(
        question=QUESTION, short_answer="They differ.", sources=[s.source for s in sources],
        key_findings=[Finding(claim=claim, source_ids=ids)], confidence=Confidence.high, confidence_reason="r",
    )


# ---------- building blocks ----------

def test_site_name():
    assert site_name("https://blog.zilliz.com/post") == "zilliz"
    assert site_name("https://www.bbc.co.uk/news") == "bbc"
    assert site_name("https://github.com/qdrant/qdrant") == "qdrant"
    assert site_name("https://docs.weaviate.io/weaviate") == "weaviate"
    assert site_name("https://milvus.io/docs/overview.md") == "milvus"
    assert site_name("https://pymilvus.readthedocs.io/en/latest") == "pymilvus"


def test_named_entities_ignore_ordinary_words():
    text = "Milvus vs Qdrant: greytHR and Zilliz. Wind power and wind farms. Medium confidence."
    ents = named_entities(text)
    assert {"milvus", "qdrant", "greythr", "zilliz"} <= ents
    assert "wind" not in ents  # also written in lower case
    # Known false-positive risk (documented in NOTES.md): a word that only appears capitalised,
    # e.g. "Medium" starting a sentence, counts as a name, so medium.com could be treated as a vendor.
    assert "medium" in ents


def test_vendor_detection():
    ents = named_entities(QUESTION)
    assert is_vendor_source("https://milvus.io/docs/overview.md", ents)
    assert is_vendor_source("https://github.com/qdrant/qdrant", ents)
    assert not is_vendor_source("https://www.zenml.io/blog/vector-databases-for-rag", ents)
    assert not is_vendor_source("https://arxiv.org/abs/1", named_entities("Arxiv paper"))  # authoritative


# ---------- the rule ----------

INDEPENDENT = [fs(1, "https://zenml.io/a"), fs(2, "https://pingcap.com/b"), fs(3, "https://elest.io/c")]


def test_three_independent_full_pages_keep_high():
    ok, why = high_confidence_support(resp(INDEPENDENT), INDEPENDENT)
    assert ok and why == ""
    assert MIN_INDEPENDENT_FULL_PAGES == 3


def test_snippet_only_source_does_not_count():
    sources = INDEPENDENT[:2] + [fs(3, "https://elest.io/c", from_page=False)]
    ok, why = high_confidence_support(resp(sources), sources)
    assert not ok
    assert "found 2" in why and "elest.io (snippet only)" in why


def test_same_organisation_counts_once():
    sources = INDEPENDENT[:2] + [fs(3, "https://blog.pingcap.com/x")]
    ok, why = high_confidence_support(resp(sources), sources)
    assert not ok and "same organisation as pingcap.com" in why


def test_vendor_of_evaluated_product_does_not_count():
    sources = INDEPENDENT[:2] + [fs(3, "https://milvus.io/docs/overview.md")]
    ok, why = high_confidence_support(resp(sources), sources)
    assert not ok and "milvus.io (vendor of something the answer evaluates)" in why


def test_vendor_named_only_in_the_answer_is_detected():
    sources = INDEPENDENT[:2] + [fs(3, "https://www.greythr.com/blog/greythr-vs-spinehr")]
    r = resp(sources, claim="greytHR positions itself on Indian payroll compliance.")
    ok, why = high_confidence_support(r.model_copy(update={"question": "Find 5 Indian HR SaaS startups"}), sources)
    assert not ok and "greythr.com (vendor" in why


def test_uncited_sources_do_not_count():
    sources = INDEPENDENT + [fs(4, "https://x.org/d")]
    ok, why = high_confidence_support(resp(sources, cited=["S1", "S2"]), sources)
    assert not ok and "found 2" in why


# ---------- end to end ----------

def test_orchestrator_downgrades_high_to_medium_and_explains_why():
    results = {"nuclear deaths per TWh": [
        SearchItem(title="Milvus docs", url="https://milvus.io/docs/overview.md", snippet=SNIPPET, score=0.95),
        SearchItem(title="ZenML", url="https://zenml.io/blog/x", snippet=SNIPPET, score=0.9),
        SearchItem(title="PingCAP", url="https://pingcap.com/compare/x", snippet=SNIPPET, score=0.85),
    ]}
    tools = FakeTools(results=results)
    high = {
        "short_answer": "Milvus scales best.",
        "key_findings": [{"claim": "Milvus targets billion-scale workloads.", "source_ids": ["S1", "S2", "S3"]}],
        "confidence": "high", "confidence_reason": "Three sources agree.",
    }
    client = FakeClient(json_completion(dict(PLAN, question=QUESTION)), json_completion(high))
    result = run(QUESTION, providers=[provider(client)], search_fn=tools.search, scrape_fn=tools.scrape, runs_dir=None)
    r = result.response
    assert r.confidence == Confidence.medium
    assert "Three sources agree." in r.confidence_reason
    assert "Capped at medium: high confidence needs 3+ cited full-page sources" in r.confidence_reason
    assert "milvus.io (vendor of something the answer evaluates)" in r.confidence_reason
    assert any(g.startswith("confidence high -> medium") for g in result.trace.guardrails)


def test_orchestrator_keeps_high_with_independent_full_pages():
    tools = FakeTools(pages={})  # default: every page scrapes as a full page
    high = {
        "short_answer": "Nuclear and wind are both very safe per TWh.",
        "key_findings": [{"claim": "Both have low death rates.", "source_ids": ["S1", "S2", "S3"]}],
        "confidence": "high", "confidence_reason": "Three independent sources agree.",
    }
    client = FakeClient(json_completion(PLAN), json_completion(high))
    result = run(PLAN["question"], providers=[provider(client)], search_fn=tools.search, scrape_fn=tools.scrape, runs_dir=None)
    assert result.response.confidence == Confidence.high
    assert PageResult  # imported for parity with other orchestrator tests
