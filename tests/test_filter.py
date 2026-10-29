from agent.filter import MAX_SOURCES, MIN_PAGE_CHARS, Candidate, filter_sources, normalise_url
from agent.tools.results import PageResult

SNIPPET = "A search snippet that is long enough to count as evidence on its own."
PAGE_TEXT = "Full page text. " * 40  # 640 chars


def page(url, text=PAGE_TEXT, **kw):
    return PageResult(url=url, title="Page title", text=text, ok=True, **kw)


def cand(url, score=0.9, **kw):
    kw.setdefault("snippet", SNIPPET)
    return Candidate(url=url, title=f"Title {url}", score=score, **kw)


def dropped_reasons(result):
    return {d.url: d.reason for d in result.dropped}


def test_ranks_by_relevance_and_assigns_ids():
    result = filter_sources([cand("https://a.org/1", 0.6), cand("https://b.org/1", 0.95), cand("https://c.org/1", 0.8)])
    assert [(s.source.id, str(s.source.url)) for s in result.sources] == [
        ("S1", "https://b.org/1"), ("S2", "https://c.org/1"), ("S3", "https://a.org/1"),
    ]
    assert result.dropped == []


def test_duplicate_urls_removed_keeping_best():
    result = filter_sources([
        cand("https://a.org/page/", 0.6),
        cand("https://A.org/page#section", 0.9),
        cand("https://a.org/page", 0.7),
    ])
    assert len(result.sources) == 1 and result.sources[0].source.relevance == 0.9
    assert [d.reason for d in result.dropped] == ["duplicate URL", "duplicate URL"]
    assert normalise_url("https://A.org/page/#x") == "https://a.org/page"


def test_at_most_two_sources_per_domain():
    result = filter_sources([cand(f"https://www.same.org/{i}", 0.9 - i / 10) for i in range(4)])
    assert len(result.sources) == 2
    assert all("more than 2 sources from same.org" in d.reason for d in result.dropped)


def test_low_relevance_dropped_with_reason_but_user_urls_exempt():
    result = filter_sources([
        cand("https://low.org/x", 0.3),
        cand("https://user.org/x", 0.0, origin="user_url", page=page("https://user.org/x")),
    ])
    assert [str(s.source.url) for s in result.sources] == ["https://user.org/x"]
    assert dropped_reasons(result) == {"https://low.org/x": "relevance 0.30 below threshold 0.5"}


def test_suspicious_page_and_snippet_dropped_and_recorded():
    bad_page = page("https://evil.org/1", suspicious=True, injection_matches=["ignore previous instructions"])
    result = filter_sources([
        cand("https://evil.org/1", page=bad_page),
        cand("https://evil2.org/1", snippet_suspicious=True, snippet_injection_matches=["you are now a "]),
        cand("https://good.org/1"),
    ])
    assert [str(s.source.url) for s in result.sources] == ["https://good.org/1"]
    reasons = dropped_reasons(result)
    assert "suspicious page" in reasons["https://evil.org/1"] and "ignore previous instructions" in reasons["https://evil.org/1"]
    assert "suspicious search snippet" in reasons["https://evil2.org/1"]


def test_short_page_falls_back_to_snippet_with_note():
    result = filter_sources([cand("https://a.org/1", page=page("https://a.org/1", text="Accept cookies?"))])
    source = result.sources[0]
    assert not source.from_page and source.text == SNIPPET
    assert "page text too short" in result.notes[0]


def test_full_page_used_as_evidence():
    result = filter_sources([cand("https://a.org/1", page=page("https://a.org/1"))])
    assert result.sources[0].from_page and len(result.sources[0].text) >= MIN_PAGE_CHARS


def test_too_little_text_dropped():
    result = filter_sources([cand("https://a.org/1", snippet="tiny")])
    assert result.sources == []
    assert "too little text" in result.dropped[0].reason


def test_at_most_eight_sources():
    result = filter_sources([cand(f"https://site{i}.org/x", 0.9) for i in range(12)])
    assert len(result.sources) == MAX_SOURCES
    assert [s.source.id for s in result.sources] == [f"S{i}" for i in range(1, 9)]
    assert sum("over the 8-source limit" in d.reason for d in result.dropped) == 4


def test_invalid_url_dropped():
    result = filter_sources([cand("ftp://files.org/x"), cand("https://ok.org/x")])
    assert dropped_reasons(result)["ftp://files.org/x"] == "invalid URL"
    assert len(result.sources) == 1
