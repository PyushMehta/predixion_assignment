"""Source filter: deterministic rules that turn raw tool output into cited Sources.

No LLM. Every candidate that is dropped is recorded with a reason, so the run
trace shows exactly why a source did or didn't reach the synthesizer.

Order of rules: invalid URL -> duplicate -> suspicious -> low relevance ->
too little text -> per-domain cap -> overall cap. Candidates are ranked by
search relevance plus a source-quality adjustment (agent/quality.py), so
social media and forums sink and authoritative sources rise, but nothing is
blocked for its domain alone. Survivors are numbered S1..Sn in that order.
"""

import logging
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ValidationError

from agent.quality import classify_url, quality_adjustment
from agent.schemas import Source
from agent.tools.results import PageResult

log = logging.getLogger(__name__)

MIN_RELEVANCE = 0.5
MAX_SOURCES = 8
MAX_PER_DOMAIN = 2
MIN_PAGE_CHARS = 300  # a "page" shorter than this is usually a cookie wall or error stub
MIN_SNIPPET_CHARS = 40  # a snippet-only source needs at least this much text
USER_URL_RELEVANCE = 1.0  # the user asked for this URL explicitly
SNIPPET_MAX_CHARS = 500  # Source.snippet limit


class Candidate(BaseModel):
    """One potential source: a search hit and/or a page we scraped."""

    url: str
    title: str = ""
    snippet: str = ""
    score: float = 0.0
    origin: str = "search"  # "search" or "user_url"
    snippet_suspicious: bool = False
    snippet_injection_matches: list[str] = []
    page: PageResult | None = None


class FilteredSource(BaseModel):
    source: Source
    text: str  # evidence handed to the synthesizer: page text, or the snippet
    from_page: bool  # False = snippet only (page not scraped, failed or too short)
    quality: str = "other"  # agent/quality.py category
    rank_score: float = 0.0  # relevance + quality adjustment, used for ordering


class DropDecision(BaseModel):
    url: str
    reason: str


class FilterResult(BaseModel):
    sources: list[FilteredSource] = []
    dropped: list[DropDecision] = []
    notes: list[str] = []  # non-fatal decisions, e.g. "page too short, using snippet"


def normalise_url(url: str) -> str:
    """Key for duplicate detection: lower-case scheme/host, no fragment, no trailing slash."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def domain_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


def rank_score(c: Candidate, question: str = "") -> float:
    return c.score + quality_adjustment(c.url, question)


def filter_sources(candidates: list[Candidate], question: str = "") -> FilterResult:
    result = FilterResult()

    def drop(candidate: Candidate, reason: str) -> None:
        result.dropped.append(DropDecision(url=candidate.url, reason=reason))

    # Best first, so duplicate and cap rules keep the strongest candidate.
    ranked = sorted(candidates, key=lambda c: rank_score(c, question), reverse=True)

    kept: list[FilteredSource] = []
    seen_urls: set[str] = set()
    per_domain: dict[str, int] = {}
    for c in ranked:
        if not c.url.lower().startswith(("http://", "https://")):
            drop(c, "invalid URL")
            continue
        key = normalise_url(c.url)
        if key in seen_urls:
            drop(c, "duplicate URL")
            continue
        seen_urls.add(key)

        if c.page is not None and c.page.ok and c.page.suspicious:
            drop(c, f"suspicious page: possible prompt injection {c.page.injection_matches}")
            continue
        if c.snippet_suspicious:
            drop(c, f"suspicious search snippet: possible prompt injection {c.snippet_injection_matches}")
            continue
        if c.origin != "user_url" and c.score < MIN_RELEVANCE:
            drop(c, f"relevance {c.score:.2f} below threshold {MIN_RELEVANCE}")
            continue

        text, from_page = _evidence_text(c, result)
        if text is None:
            drop(c, "too little text (page missing or too short, snippet too short)")
            continue

        domain = domain_of(c.url)
        if per_domain.get(domain, 0) >= MAX_PER_DOMAIN:
            drop(c, f"more than {MAX_PER_DOMAIN} sources from {domain}")
            continue
        if len(kept) >= MAX_SOURCES:
            drop(c, f"over the {MAX_SOURCES}-source limit")
            continue

        try:
            source = Source(
                id=f"S{len(kept) + 1}",
                title=(c.title or (c.page.title if c.page else "") or c.url)[:300],
                url=c.url,
                snippet=(c.snippet or text)[:SNIPPET_MAX_CHARS],
                relevance=min(max(c.score, 0.0), 1.0),
            )
        except ValidationError as exc:
            drop(c, f"invalid source data: {exc.errors()[0]['msg']}")
            continue
        per_domain[domain] = per_domain.get(domain, 0) + 1
        kept.append(FilteredSource(
            source=source, text=text, from_page=from_page,
            quality=classify_url(c.url), rank_score=round(rank_score(c, question), 3),
        ))

    result.sources = kept
    log.info(
        "filter: kept=%d dropped=%d (from %d candidates)", len(kept), len(result.dropped), len(candidates)
    )
    return result


def _evidence_text(c: Candidate, result: FilterResult) -> tuple[str | None, bool]:
    """Pick the evidence for a candidate: full page text if usable, else the snippet."""
    page = c.page
    if page is not None and page.ok:
        if len(page.text) >= MIN_PAGE_CHARS:
            return page.text, True
        result.notes.append(f"{c.url}: page text too short ({len(page.text)} chars), using search snippet")
    if len(c.snippet.strip()) >= MIN_SNIPPET_CHARS:
        return c.snippet.strip(), False
    return None, False
