"""Page scraping tool: fetch a URL and extract its main text.

Scraped text is untrusted data. This module does not try to sanitise it;
it flags likely prompt-injection phrases (`suspicious=True`) so later
stages can down-rank the source and must still treat the text as data.
"""

import logging
import re

import httpx
import trafilatura
from bs4 import BeautifulSoup

from agent.tools.injection import find_injection_phrases
from agent.tools.net import TIMEOUT_S, deadline_in, send_with_retry, time_left
from agent.tools.results import PageResult

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
MAX_DOWNLOAD_BYTES = 2_000_000
MAX_TEXT_CHARS = 4000
SCRAPE_RETRIES = 1  # fewer than search: one slow page shouldn't stall the run
SCRAPE_DEADLINE_S = 15.0  # overall cap per call: attempts + backoff + download

# Tags that never hold the main content.
NOISE_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe", "svg"]
AD_PATTERN = re.compile(r"\b(ads?|advert\w*|banner|promo\w*|sponsor\w*|cookie\w*|popup|newsletter)\b", re.I)


def scrape_page(url: str, *, client: httpx.Client | None = None) -> PageResult:
    """Fetch `url` and return its main text. Never raises."""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return _fail(url, "URL must start with http:// or https://")

    deadline = deadline_in(SCRAPE_DEADLINE_S)
    own_client = client is None
    client = client or httpx.Client()
    try:
        request = client.build_request(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=TIMEOUT_S,
        )
        response = send_with_retry(
            client, request, retries=SCRAPE_RETRIES, deadline=deadline, stream=True
        )
        try:
            if response.status_code != 200:
                return _fail(url, f"HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type not in HTML_CONTENT_TYPES:
                return _fail(url, f"non-HTML content type: {content_type or 'missing'}")
            html = _read_capped(response, deadline)
        finally:
            response.close()

        title, text = _extract(html)
        if not text:
            return _fail(url, "no readable text found on page")

        matches = find_injection_phrases(text)
        truncated = len(text) > MAX_TEXT_CHARS
        if truncated:
            text = _truncate(text, MAX_TEXT_CHARS)

        log.info(
            "scrape_page ok: url=%s chars=%d truncated=%s suspicious=%s",
            url, len(text), truncated, bool(matches),
        )
        return PageResult(
            url=url, title=title, text=text, truncated=truncated,
            suspicious=bool(matches), injection_matches=matches, ok=True,
        )
    except httpx.TimeoutException:
        return _fail(url, "request timed out after retries")
    except httpx.HTTPError as exc:
        return _fail(url, f"request failed: {type(exc).__name__}: {exc}")
    except Exception as exc:  # last line of defence: tools never raise
        log.exception("unexpected error in scrape_page")
        return _fail(url, f"unexpected error: {type(exc).__name__}: {exc}")
    finally:
        if own_client:
            client.close()


def _read_capped(response: httpx.Response, deadline: float) -> str:
    """Read the body, stopping at MAX_DOWNLOAD_BYTES or the call's deadline.

    httpx's timeout applies per network read, so a server that drips bytes
    slowly could otherwise hold us far longer than the deadline.
    """
    chunks, size = [], 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_DOWNLOAD_BYTES:
            break
        if time_left(deadline) <= 0:
            raise httpx.ReadTimeout("page download exceeded overall deadline", request=response.request)
    body = b"".join(chunks)
    try:
        return body.decode(response.charset_encoding or "utf-8", errors="replace")
    except LookupError:  # unknown charset name in the header
        return body.decode("utf-8", errors="replace")


def _extract(html: str) -> tuple[str, str]:
    """Return (title, main_text). Tries trafilatura first, BeautifulSoup as fallback."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""

    text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    if not text.strip():
        text = _extract_with_bs(soup)
    return title, _normalise_whitespace(text)


def _extract_with_bs(soup: BeautifulSoup) -> str:
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    ad_like = soup.find_all(
        lambda t: AD_PATTERN.search(" ".join(t.get("class") or []) + " " + (t.get("id") or ""))
    )
    for tag in ad_like:
        if not tag.decomposed:  # may already be gone with its parent
            tag.decompose()
    body = soup.body or soup
    return body.get_text("\n", strip=True)


def _normalise_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _truncate(text: str, limit: int) -> str:
    """Cut at the last word boundary before `limit`."""
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.8 else cut).rstrip() + " …"


def _fail(url: str, error: str) -> PageResult:
    log.warning("scrape_page failed: url=%s error=%s", url, error)
    return PageResult(url=url, ok=False, error=error)
