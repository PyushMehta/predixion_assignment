"""Web search tool backed by the Tavily REST API.

Calls Tavily over plain httpx (rather than the tavily-python SDK) so that
timeouts and retries are under our control and tests can mock the network
with httpx.MockTransport.
"""

import logging
import os

import httpx

from agent.tools.injection import find_injection_phrases
from agent.tools.net import TIMEOUT_S, send_with_retry
from agent.tools.results import SearchItem, SearchResult

log = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
MAX_RESULTS_CAP = 10
SNIPPET_MAX_CHARS = 500


def web_search(
    query: str,
    max_results: int = 5,
    *,
    client: httpx.Client | None = None,
) -> SearchResult:
    """Search the web. Never raises; failures come back as ok=False."""
    query = query.strip()
    if not query:
        return _fail(query, "empty query")

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return _fail(query, "TAVILY_API_KEY is not set")

    max_results = max(1, min(max_results, MAX_RESULTS_CAP))
    own_client = client is None
    client = client or httpx.Client()
    try:
        request = client.build_request(
            "POST",
            TAVILY_URL,
            json={"query": query, "max_results": max_results, "search_depth": "basic"},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TIMEOUT_S,
        )
        response = send_with_retry(client, request)
        if response.status_code != 200:
            return _fail(query, f"Tavily returned HTTP {response.status_code}")

        raw_items = response.json().get("results") or []
        items = [item for raw in raw_items if (item := _parse_item(raw)) is not None]
        if not items:
            return _fail(query, "no search results")

        log.info(
            "web_search ok: query=%r items=%d suspicious=%d",
            query, len(items), sum(i.suspicious for i in items),
        )
        return SearchResult(query=query, items=items, ok=True)
    except httpx.TimeoutException:
        return _fail(query, "Tavily request timed out after retries")
    except httpx.HTTPError as exc:
        return _fail(query, f"Tavily request failed: {type(exc).__name__}: {exc}")
    except ValueError as exc:  # bad JSON or unexpected shape
        return _fail(query, f"could not parse Tavily response: {exc}")
    except Exception as exc:  # last line of defence: tools never raise
        log.exception("unexpected error in web_search")
        return _fail(query, f"unexpected error: {type(exc).__name__}: {exc}")
    finally:
        if own_client:
            client.close()


def _parse_item(raw: object) -> SearchItem | None:
    """Turn one Tavily result into a SearchItem, skipping malformed entries."""
    if not isinstance(raw, dict) or not raw.get("url"):
        return None
    try:
        title = str(raw.get("title") or raw["url"])
        snippet = str(raw.get("content") or "")[:SNIPPET_MAX_CHARS]
        matches = find_injection_phrases(f"{title}\n{snippet}")
        return SearchItem(
            title=title,
            url=str(raw["url"]),
            snippet=snippet,
            score=float(raw.get("score") or 0.0),
            suspicious=bool(matches),
            injection_matches=matches,
        )
    except (TypeError, ValueError):
        return None


def _fail(query: str, error: str) -> SearchResult:
    log.warning("web_search failed: query=%r error=%s", query, error)
    return SearchResult(query=query, ok=False, error=error)
