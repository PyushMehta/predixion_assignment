"""Phase 5 fix 3: source quality adjusts ranking without blocking."""

from agent.filter import Candidate, filter_sources
from agent.quality import classify_url, is_technical, quality_adjustment

SNIPPET = "A search snippet that is long enough to count as evidence on its own."


def test_classify_url_categories():
    assert classify_url("https://www.facebook.com/post/1") == "low"
    assert classify_url("https://m.youtube.com/watch?v=x") == "low"
    assert classify_url("https://www.reddit.com/r/x") == "low"
    assert classify_url("https://ourworldindata.org/safety") == "authoritative"
    assert classify_url("https://energy.gov/report") == "authoritative"
    assert classify_url("https://cs.stanford.edu/paper") == "authoritative"
    assert classify_url("https://arxiv.org/abs/1234") == "authoritative"
    assert classify_url("https://github.com/qdrant/qdrant") == "docs_code"
    assert classify_url("https://docs.weaviate.io/weaviate") == "docs_code"
    assert classify_url("https://milvus.io/docs/overview.md") == "docs_code"
    assert classify_url("https://qdrant.tech/documentation/") == "docs_code"
    assert classify_url("https://nextbigfuture.com/2011/x") == "other"
    assert classify_url("https://notfacebook.com/x") == "other"


def test_docs_and_code_only_boosted_for_technical_questions():
    assert is_technical("Compare the top 3 open-source vector databases for RAG")
    assert not is_technical("Who will win the 2030 FIFA World Cup?")
    gh = "https://github.com/milvus-io/milvus"
    assert quality_adjustment(gh, "Compare vector databases") > 0
    assert quality_adjustment(gh, "Who will win the World Cup?") == 0


def test_low_quality_sinks_but_is_not_blocked():
    result = filter_sources([
        Candidate(url="https://www.facebook.com/post", title="FB", snippet=SNIPPET, score=0.9),
        Candidate(url="https://blog.example/post", title="Blog", snippet=SNIPPET, score=0.8),
        Candidate(url="https://ourworldindata.org/x", title="OWID", snippet=SNIPPET, score=0.7),
    ], question="Is nuclear safer than wind?")
    urls = [str(s.source.url) for s in result.sources]
    assert urls == ["https://ourworldindata.org/x", "https://blog.example/post", "https://www.facebook.com/post"]
    assert [s.quality for s in result.sources] == ["authoritative", "other", "low"]
    assert result.sources[0].source.relevance == 0.7  # Source.relevance stays the search score


def test_low_quality_loses_its_slot_when_sources_are_capped():
    candidates = [Candidate(url=f"https://site{i}.example/x", title="t", snippet=SNIPPET, score=0.7) for i in range(8)]
    candidates.append(Candidate(url="https://www.youtube.com/watch?v=1", title="YT", snippet=SNIPPET, score=0.9))
    result = filter_sources(candidates)
    assert "youtube" not in " ".join(str(s.source.url) for s in result.sources)
    assert any("over the 8-source limit" in d.reason and "youtube" in d.url for d in result.dropped)
