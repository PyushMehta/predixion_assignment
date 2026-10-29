"""Orchestrator: plan -> search -> scrape -> filter -> synthesize -> guardrails.

A single-agent planner-executor with fixed budgets:
- at most MAX_LLM_CALLS logical LLM calls per run (planner + synthesis),
- at most MAX_SCRAPES pages, scraped in parallel,
- an overall run deadline covering the WHOLE run: planning and gathering must
  finish by the gathering deadline, and synthesis gets the rest. LLM calls are
  given the time left (llm.py shrinks timeouts and skips retries/failover that
  don't fit); if synthesis can't fit, a low-confidence fallback answer is used.

Scraped pages are reduced to their most question-relevant passages
(agent/passages.py) before filtering and synthesis.

Only URLs that came from search results or from the user's question are
scraped. The planner may suggest a URL, but an invented one is never fetched.

Every run returns a RunResult (answer + trace) and saves it to runs/<run_id>.json.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from agent.filter import (
    MAX_PER_DOMAIN, MIN_RELEVANCE, USER_URL_RELEVANCE, Candidate, DropDecision,
    domain_of, filter_sources, normalise_url,
)
from agent.llm import FailedAttempt, LLMResult, Provider
from agent.passages import select_passages
from agent.planner import make_plan_detailed
from agent.quality import is_vendor_source, named_entities, quality_adjustment, site_name
from agent.schemas import Confidence, Plan, ResearchResponse
from agent.synthesizer import (
    MAX_EVIDENCE_CHARS, fallback_response, insufficient_evidence_response, synthesize_detailed,
)
from agent.tools import net
from agent.tools.results import PageResult, SearchItem, SearchResult
from agent.tools.scraper import scrape_page
from agent.tools.search import web_search

log = logging.getLogger(__name__)

MAX_LLM_CALLS = 2  # planner + synthesis (each may retry/repair inside llm.py)
MAX_SCRAPES = 4
MAX_WORKERS = 4
SEARCH_RESULTS_PER_QUERY = 5
RUN_DEADLINE_S = 90.0
SYNTHESIS_RESERVE_S = 30.0  # gathering must stop this long before the deadline
MIN_INDEPENDENT_FULL_PAGES = 3  # "high" confidence needs this many cited, full-page, non-vendor sites
URL_PATTERN = re.compile(r"https?://[^\s<>\"'()\[\]]+")

# Questions that need full pages whatever the planner says: comparisons and numbers.
# Snippets rarely carry tables, figures or both sides of a comparison.
_UNITS = (
    r"%|percent|kg|g|km|m|mi|mph|kwh|mwh|gwh|twh|kw|mw|gw|mb|gb|tb|ms|sec|seconds?|minutes?|hours?|"
    r"days?|years?|usd|eur|inr|million|billion|trillion"
)
FULL_PAGE_PATTERNS = [
    ("comparison", re.compile(r"\bcompar\w*", re.I)),
    ("vs/versus", re.compile(r"\b(vs\.?|versus)(?=\s|$)", re.I)),
    ("pros and cons", re.compile(r"\bpros\s+(and|&)\s+cons\b", re.I)),
    ("top N", re.compile(r"\btop\s+(\d+|three|five|ten)\b", re.I)),
    ("best", re.compile(r"\bbest\b", re.I)),
    ("per-unit rate", re.compile(r"\bper\b", re.I)),
    ("percentage", re.compile(r"%|\bpercent(age)?\b", re.I)),
    ("number with unit", re.compile(r"[$€₹]\s?\d|\b\d+(?:[.,]\d+)?\s?(?:" + _UNITS + r")\b", re.I)),
    ("how many/much", re.compile(r"\bhow\s+(many|much)\b", re.I)),
]

_monotonic = net._monotonic  # replaced by the fake clock in tests


# ---------- trace models ----------

class ToolCall(BaseModel):
    tool: str  # "web_search" or "scrape_page"
    input: str
    ok: bool
    error: str | None = None
    latency_ms: int = 0
    detail: str = ""


class LLMCall(BaseModel):
    stage: str  # "plan" or "synthesis"
    ok: bool
    error: str | None = None
    provider: str = ""
    model: str = ""
    error_kind: str | None = None
    fallback_provider_used: bool = False
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failed_attempts: list[FailedAttempt] = []  # every failed request (provider, model, error, latency, failover?)


class Trace(BaseModel):
    run_id: str
    question: str
    started_at: str
    plan: Plan | None = None
    tool_calls: list[ToolCall] = []
    tool_selection: str = ""  # whether pages were read, and why (planner's needs_full_pages)
    skipped: list[DropDecision] = []  # steps/URLs deliberately not executed
    filter_kept: list[str] = []  # "S1 https://..."
    filter_dropped: list[DropDecision] = []
    filter_notes: list[str] = []
    llm_calls: list[LLMCall] = []
    guardrails: list[str] = []  # what the post-synthesis guardrails changed
    fallbacks: list[str] = []  # which fallbacks fired during the run
    deadline_hit: bool = False
    total_ms: int = 0


class RunResult(BaseModel):
    response: ResearchResponse
    trace: Trace
    trace_path: str | None = None


# ---------- main entry point ----------

def run(
    question: str,
    *,
    providers: list[Provider] | None = None,
    search_fn: Callable[..., SearchResult] = web_search,
    scrape_fn: Callable[[str], PageResult] = scrape_page,
    runs_dir: Path | None = Path("runs"),
) -> RunResult:
    """Research `question` end to end. Never raises."""
    question = question.strip()
    start = _monotonic()
    deadline = start + RUN_DEADLINE_S
    gather_deadline = deadline - SYNTHESIS_RESERVE_S
    now = datetime.now()
    trace = Trace(run_id=f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}", question=question, started_at=now.isoformat(timespec="seconds"))

    try:
        response = _run_steps(question, trace, providers, search_fn, scrape_fn, gather_deadline, deadline)
    except Exception as exc:  # last line of defence: a bug must not lose the run
        log.exception("orchestrator: unexpected error")
        trace.fallbacks.append("unexpected_error")
        response = insufficient_evidence_response(question, f"The run failed unexpectedly: {type(exc).__name__}: {exc}")

    trace.total_ms = _ms_since(start)
    result = RunResult(response=response, trace=trace)
    if runs_dir is not None:
        result.trace_path = _save(result, runs_dir)
    log.info(
        "run done: id=%s confidence=%s sources=%d total_ms=%d fallbacks=%s",
        trace.run_id, response.confidence.value, len(response.sources), trace.total_ms, trace.fallbacks or "none",
    )
    return result


def _run_steps(question, trace, providers, search_fn, scrape_fn, gather_deadline, deadline) -> ResearchResponse:
    # 1. Plan (LLM call 1), bounded by the gathering deadline
    planned = make_plan_detailed(question, providers=providers, deadline=gather_deadline)
    plan = trace.plan = planned.plan
    _record_llm(trace, "plan", planned.llm)
    if planned.used_fallback_plan:
        trace.fallbacks.append("planner_fallback_plan")
    log.info("step plan: steps=%d fallback_plan=%s", len(plan.steps), planned.used_fallback_plan)

    # 2. Search
    queries = list(dict.fromkeys(s.query.strip() for s in plan.steps if s.tool == "web_search" and s.query))
    for step in plan.steps:
        if step.tool == "none":
            trace.skipped.append(DropDecision(url=f"step {step.step_id}", reason="reasoning-only step, no tool"))
    searches = _run_parallel(
        lambda q: search_fn(q, max_results=SEARCH_RESULTS_PER_QUERY), queries, gather_deadline, "web_search", trace,
    )
    items = [item for r in searches.values() if r.ok for item in r.items]
    log.info("step search: queries=%d ok=%d items=%d", len(queries), sum(r.ok for r in searches.values()), len(items))

    # 3. Scrape (only URLs from search results or the user's question), if the plan needs full pages
    user_urls = _urls_in(question)
    override = full_pages_required(question)
    if not plan.needs_full_pages and override:
        plan = trace.plan = plan.model_copy(update={"needs_full_pages": True})
        trace.tool_selection = (
            f"full pages: forced by code rule ({override}); the planner had set needs_full_pages=false"
        )
        log.info("step tool selection: overriding planner, full pages needed (%s)", override)
        targets = _scrape_targets(plan, items, user_urls, trace)
    elif plan.needs_full_pages:
        trace.tool_selection = "full pages: planner set needs_full_pages=true"
        targets = _scrape_targets(plan, items, user_urls, trace)
    else:
        trace.tool_selection = (
            "snippets only: planner set needs_full_pages=false (simple factual question); "
            "only URLs written in the question are read"
        )
        targets = user_urls[:MAX_SCRAPES]
        for step in plan.steps:
            if step.tool == "scrape_page" and step.query and step.query not in targets:
                trace.skipped.append(DropDecision(url=step.query, reason="scrape skipped: needs_full_pages=false"))
    log.info("step tool selection: needs_full_pages=%s scrape_targets=%d", plan.needs_full_pages, len(targets))
    pages = _run_parallel(scrape_fn, targets, gather_deadline, "scrape_page", trace)
    log.info("step scrape: targets=%d ok=%d", len(targets), sum(p.ok for p in pages.values()))

    # 3b. Keep only the most relevant passages of each page
    relevance_query = " ".join([question, *plan.sub_questions])
    pages = {url: _select(page, relevance_query, trace) for url, page in pages.items()}

    # 4. Filter
    filtered = filter_sources(_candidates(items, pages, user_urls), question=question)
    trace.filter_kept = [f"{s.source.id} {s.source.url}" for s in filtered.sources]
    trace.filter_dropped = filtered.dropped
    trace.filter_notes.extend(filtered.notes)
    log.info("step filter: kept=%d dropped=%d", len(filtered.sources), len(filtered.dropped))

    # 5. Guardrail: nothing usable -> answer without calling the LLM
    if not filtered.sources:
        trace.guardrails.append("no usable sources: insufficient-evidence response, synthesis skipped")
        log.info("step guardrails: no usable sources, skipping synthesis")
        detail = "The run deadline was reached while gathering sources." if trace.deadline_hit else ""
        return insufficient_evidence_response(question, detail)

    # 6. Synthesize (LLM call 2)
    if len(trace.llm_calls) >= MAX_LLM_CALLS:
        trace.fallbacks.append("synthesis_fallback_response")
        response = fallback_response(question, filtered.sources, "LLM call budget exhausted")
    else:
        synthesis = synthesize_detailed(question, filtered.sources, providers=providers, deadline=deadline)
        _record_llm(trace, "synthesis", synthesis.llm)
        trace.guardrails.extend(f"synthesizer: {a}" for a in synthesis.adjustments)
        if synthesis.used_fallback_response:
            trace.fallbacks.append("synthesis_fallback_response")
            if synthesis.llm is not None and synthesis.llm.error_kind == "deadline":
                trace.deadline_hit = True
        response = synthesis.response
    log.info("step synthesize: confidence=%s findings=%d", response.confidence.value, len(response.key_findings))

    # 7. Guardrails
    return _apply_guardrails(response, filtered.sources, planned.used_fallback_plan, trace)


# ---------- steps ----------

def full_pages_required(question: str) -> str | None:
    """Name the first comparison/numeric pattern in `question`, or None if there is none."""
    for name, pattern in FULL_PAGE_PATTERNS:
        if pattern.search(question):
            return name
    return None


def _scrape_targets(plan: Plan, items: list[SearchItem], user_urls: list[str], trace: Trace) -> list[str]:
    """Pick up to MAX_SCRAPES URLs: user URLs, then planner URLs we can vouch for, then top results."""
    search_urls = {normalise_url(i.url) for i in items}
    allowed = search_urls | {normalise_url(u) for u in user_urls}
    targets: list[str] = []
    per_domain: dict[str, int] = {}

    def add(url: str) -> None:
        key, domain = normalise_url(url), domain_of(url)
        if key in {normalise_url(t) for t in targets}:
            return
        if len(targets) >= MAX_SCRAPES:
            trace.skipped.append(DropDecision(url=url, reason=f"scrape budget of {MAX_SCRAPES} pages used"))
            return
        targets.append(url)
        per_domain[domain] = per_domain.get(domain, 0) + 1

    for url in user_urls:
        add(url)
    for step in plan.steps:
        if step.tool != "scrape_page" or not step.query:
            continue
        if normalise_url(step.query) in allowed:
            add(step.query)
        else:
            trace.skipped.append(DropDecision(
                url=step.query, reason="planner URL not in search results or the question: not scraped",
            ))
    # Best search results that would survive the filter anyway, quality-adjusted.
    for item in sorted(items, key=lambda i: i.score + quality_adjustment(i.url, plan.question), reverse=True):
        if len(targets) >= MAX_SCRAPES:
            break
        if item.score < MIN_RELEVANCE or item.suspicious or per_domain.get(domain_of(item.url), 0) >= MAX_PER_DOMAIN:
            continue
        add(item.url)
    return targets


def _candidates(items: list[SearchItem], pages: dict[str, PageResult], user_urls: list[str]) -> list[Candidate]:
    pages_by_key = {normalise_url(url): page for url, page in pages.items()}
    candidates = [
        Candidate(
            url=i.url, title=i.title, snippet=i.snippet, score=i.score, origin="search",
            snippet_suspicious=i.suspicious, snippet_injection_matches=i.injection_matches,
            page=pages_by_key.get(normalise_url(i.url)),
        )
        for i in items
    ]
    search_keys = {normalise_url(i.url) for i in items}
    for url in user_urls:
        if normalise_url(url) in search_keys:
            continue
        page = pages_by_key.get(normalise_url(url))
        candidates.append(Candidate(
            url=url,
            title=page.title if page and page.ok else "",
            snippet=page.text[:500] if page and page.ok else "",
            score=USER_URL_RELEVANCE,
            origin="user_url",
            page=page,
        ))
    return candidates


def _apply_guardrails(response: ResearchResponse, sources, used_fallback_plan: bool, trace: Trace) -> ResearchResponse:
    if len(sources) < 2:
        response = _cap_confidence(response, Confidence.low, f"only {len(sources)} usable source", trace)
    if not any(s.from_page for s in sources):
        response = _cap_confidence(response, Confidence.medium, "no full pages could be read, only search snippets", trace)
    if response.confidence == Confidence.high:
        supported, why = high_confidence_support(response, sources)
        if not supported:
            response = _cap_confidence(response, Confidence.medium, why, trace)
    extra = []
    if trace.deadline_hit:
        extra.append("The run hit its time budget, so the answer uses only the sources gathered before then.")
    if used_fallback_plan:
        extra.append("Planning failed, so a simple one-search plan was used; coverage may be narrow.")
    if extra:
        response = response.model_copy(update={"limitations": response.limitations + extra})
        trace.guardrails.extend(f"limitation added: {e}" for e in extra)
    return response


def high_confidence_support(response: ResearchResponse, sources) -> tuple[bool, str]:
    """Does the answer have enough independent, full-page support for "high"?

    Counts the sources the findings actually cite that were read as full pages,
    one per organisation (site_name), excluding the vendor of anything the
    question or answer names. Returns (enough?, explanation).
    """
    cited = {sid for f in response.key_findings for sid in f.source_ids}
    text = " ".join([response.question, response.short_answer, *(f.claim for f in response.key_findings)])
    entities = named_entities(text)
    counted: dict[str, str] = {}  # organisation -> domain
    excluded = []
    for s in sources:
        if s.source.id not in cited:
            continue
        url, domain = str(s.source.url), domain_of(str(s.source.url))
        if not s.from_page:
            excluded.append(f"{domain} (snippet only)")
        elif is_vendor_source(url, entities):
            excluded.append(f"{domain} (vendor of something the answer evaluates)")
        elif site_name(url) in counted:
            excluded.append(f"{domain} (same organisation as {counted[site_name(url)]})")
        else:
            counted[site_name(url)] = domain
    if len(counted) >= MIN_INDEPENDENT_FULL_PAGES:
        return True, ""
    why = (
        f"high confidence needs {MIN_INDEPENDENT_FULL_PAGES}+ cited full-page sources from different, "
        f"non-vendor sites; found {len(counted)}"
        + (f" ({', '.join(counted.values())})" if counted else "")
        + (f"; not counted: {', '.join(excluded)}" if excluded else "")
    )
    return False, why


_ORDER = [Confidence.low, Confidence.medium, Confidence.high]


def _cap_confidence(response: ResearchResponse, cap: Confidence, why: str, trace: Trace) -> ResearchResponse:
    if _ORDER.index(response.confidence) <= _ORDER.index(cap):
        return response
    trace.guardrails.append(f"confidence {response.confidence.value} -> {cap.value}: {why}")
    return response.model_copy(update={
        "confidence": cap,
        "confidence_reason": f"{response.confidence_reason} (Capped at {cap.value}: {why}.)",
        "limitations": response.limitations + [f"Confidence capped at {cap.value}: {why}."],
    })


# ---------- helpers ----------

def _run_parallel(fn, inputs: list[str], deadline: float, tool: str, trace: Trace) -> dict:
    """Run fn over inputs in a small thread pool, stopping at the deadline."""
    if not inputs:
        return {}
    remaining = deadline - _monotonic()
    if remaining <= 0:
        trace.deadline_hit = True
        trace.skipped.extend(DropDecision(url=x, reason=f"{tool} skipped: run deadline reached") for x in inputs)
        return {}

    def timed(x):
        t = _monotonic()
        return fn(x), _ms_since(t)

    pool = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(inputs)))
    futures = {pool.submit(timed, x): x for x in inputs}
    done, not_done = wait(futures, timeout=remaining)
    pool.shutdown(wait=False, cancel_futures=True)

    results = {}
    for future in futures:
        x = futures[future]
        if future in not_done:
            trace.deadline_hit = True
            trace.tool_calls.append(ToolCall(tool=tool, input=x, ok=False, error="not finished before run deadline"))
            continue
        try:
            result, latency_ms = future.result()
        except Exception as exc:  # tools never raise, but don't trust that blindly
            trace.tool_calls.append(ToolCall(tool=tool, input=x, ok=False, error=f"{type(exc).__name__}: {exc}"))
            continue
        results[x] = result
        trace.tool_calls.append(ToolCall(
            tool=tool, input=x, ok=result.ok, error=result.error, latency_ms=latency_ms, detail=_detail(result),
        ))
    return results


def _detail(result) -> str:
    if isinstance(result, SearchResult):
        flagged = sum(i.suspicious for i in result.items)
        return f"{len(result.items)} results" + (f", {flagged} suspicious" if flagged else "")
    if isinstance(result, PageResult) and result.ok:
        return f"{len(result.text)} chars" + (", suspicious" if result.suspicious else "")
    return ""


def _record_llm(trace: Trace, stage: str, llm: LLMResult | None) -> None:
    if llm is None:
        return
    trace.llm_calls.append(LLMCall(
        stage=stage, ok=llm.ok, error=llm.error, error_kind=llm.error_kind, provider=llm.provider, model=llm.model,
        fallback_provider_used=llm.fallback_used, latency_ms=llm.latency_ms,
        input_tokens=llm.input_tokens, output_tokens=llm.output_tokens,
        failed_attempts=llm.failed_attempts,
    ))
    if llm.fallback_used:
        trace.fallbacks.append(f"{stage}_fallback_provider")


def _select(page: PageResult, query: str, trace: Trace) -> PageResult:
    """Replace a page's text with its most query-relevant passages."""
    if not page.ok or len(page.text) <= MAX_EVIDENCE_CHARS:
        return page
    selected = select_passages(page.text, query, MAX_EVIDENCE_CHARS)
    trace.filter_notes.append(f"{page.url}: kept {len(selected)} of {len(page.text)} chars (relevant passages)")
    return page.model_copy(update={"text": selected})


def _urls_in(text: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,;:!?") for u in URL_PATTERN.findall(text)))


def _ms_since(t: float) -> int:
    return int((_monotonic() - t) * 1000)


def _save(result: RunResult, runs_dir: Path) -> str | None:
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / f"{result.trace.run_id}.json"
        path.write_text(result.model_dump_json(indent=2, exclude={"trace_path"}), encoding="utf-8")
        return str(path)
    except OSError as exc:
        log.warning("could not save run trace: %s", exc)
        return None
