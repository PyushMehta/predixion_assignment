# Design notes

Running log of design decisions and known limitations, phase by phase.
Source material for the README and the evaluation write-up.

---

## LLM provider: Gemini free tier via the OpenAI SDK

### Design decisions

**Provider set by config, not code.** `agent/llm.py` will use the OpenAI Python
SDK with `base_url`, `api_key` and `model` read from `LLM_BASE_URL`,
`LLM_API_KEY` and `LLM_MODEL`. Any OpenAI-compatible endpoint works (Gemini,
OpenAI, a local server), so switching provider is an `.env` edit. Originally
planned for OpenAI; switched to Gemini because the free tier needs no credits.

**Default: `gemini-3.8-flash` on Gemini's OpenAI-compatible endpoint**
(`https://generativelanguage.googleapis.com/v1beta/openai/`). Checked against
Google's docs on 2026-10-29: it's the latest stable Flash model, has a free
tier, and is the model used in the OpenAI-compatibility examples.
`gemini-3.5-flash-lite` is the faster, cheaper fallback if quality allows.

**Structured output** uses `client.chat.completions.parse` with a Pydantic
model as `response_format`. Gemini's docs show exactly this on the
OpenAI-compatible endpoint. Our own validation stays in place regardless
(e.g. the citation check in `ResearchResponse`), since parsing that succeeds
can still fail our rules.

**Rate limits (429).** The free tier allows roughly 10–15 requests per minute.
The OpenAI SDK's built-in retries back off for only a few seconds, too short to
clear a per-minute window. Plan for `llm.py`: retry 429 with backoff measured
in tens of seconds, honour a server-given retry delay when present, cap the
total wait, and fail fast when the error says the *daily* quota is used up
(retrying that is pointless).

**Observed in a live check (2026-10-29), with a valid key:**
- `models.list` answered in under 1s, so key and endpoint are fine.
- `gemini-3.8-flash` returned **HTTP 503 UNAVAILABLE**, "This model is currently
  experiencing high demand… usually temporary", or hung for 90s with no answer.
- `gemini-3.5-flash-lite` returned a correctly parsed Pydantic object via
  `chat.completions.parse`, but took **~29s** for a one-sentence answer.

What this means for `llm.py`:
- Treat 503 like 429: temporary, so retry with backoff.
- The per-call timeout must be generous (~60s), not 10s like the tools.
- A fallback model (e.g. Flash-Lite when Flash is overloaded) is worth having,
  set by an optional env var so it stays config-only.
- Latency dominates the run time: keep the number of LLM calls per question
  small and fixed (plan → synthesize, maybe one repair retry).

### Known limitations

- **Free-tier data use.** On Gemini's free tier, prompts and responses may be
  used by Google to improve its products. Acceptable here (public web research,
  no personal data), but a real deployment would use a paid tier.
- **Compatibility layer is beta.** Google labels OpenAI-library support as beta;
  some OpenAI parameters may be ignored or behave differently.
- **Rate limits are not published per model.** The docs point to AI Studio for
  actual limits, which vary by account and can change. Each research run must
  fit in a small number of LLM calls.
- **Throughput.** At ~10 requests per minute, the evaluation suite must run
  slowly or in small batches.

---

## Phase 1: Tools (web search + page scraping)

### Design decisions

**Tools never raise; they return typed results.**
`web_search` returns a `SearchResult`, `scrape_page` returns a `PageResult`
(both in `agent/tools/results.py`). Each has `ok: bool` and `error: str | None`.
Every failure (bad input, missing key, HTTP error, timeout, bad JSON, even an
unexpected bug) becomes `ok=False` with a readable error. The orchestrator can
then decide what to do (skip the source, lower confidence) instead of crashing
mid-run.

**`results.py`, not `types.py`.**
Tool result models live in `agent/tools/results.py`. `types.py` would shadow
Python's built-in `types` module whenever a file in `agent/tools/` is run
directly, which breaks imports in confusing ways. General rule adopted: never
name a module after a stdlib module. `agent/schemas.py` is kept for LLM output
models only.

**Tavily via httpx, not the `tavily-python` SDK.**
Tavily is a single POST endpoint, so calling it with httpx costs a few lines
and gives:
- full control over the timeout and the retry policy (the SDK has its own
  defaults that we would have to work around);
- the same HTTP stack and retry helper as the scraper;
- easy tests: tools accept an optional `client=` argument, so tests pass an
  `httpx.MockTransport` and need no API keys or network.

**Retry rules (`agent/tools/net.py`, `send_with_retry`).**
- Every request has a 10s timeout.
- Retry only failures that are likely temporary: timeouts, HTTP 429, HTTP 5xx.
- Do not retry 4xx, DNS failures or refused connections; retrying would only
  waste time.
- Exponential backoff between attempts: 1s, then 2s.
- Per tool:

  | Tool | Retries | Overall deadline | Worst case |
  |---|---|---|---|
  | `web_search` | 2 | none | ~33s (3 × 10s + 1s + 2s) |
  | `scrape_page` | 1 | 15s per call | ~15s (see limitation below) |

  Scraping gets fewer retries and a hard budget because the agent scrapes many
  pages per question and one slow site should not stall the run. Search is
  called less often and every later step depends on it, so it gets one more try.
- Under a deadline, each attempt's timeout shrinks to the time left, and a
  retry is skipped unless at least 1s would remain for it after the backoff wait.

**Download limits (scraper).**
- Bodies are streamed and cut at 2 MB, so a huge page cannot exhaust memory.
- The download must finish within the call's 15s deadline. httpx's timeout
  applies to each network read, so a server that drips one byte every few
  seconds would otherwise never time out.
  (Phase 1 originally used a separate 10s download limit; it was folded into
  the 15s per-call deadline.)
- Non-HTML responses (PDFs, images, JSON) are skipped by `Content-Type`.

**Text extraction.**
trafilatura extracts the main article text; if it finds nothing, a
BeautifulSoup fallback strips scripts, styles, nav, header, footer, aside,
forms, iframes and ad-like blocks (class/id matching ad, banner, promo,
sponsor, cookie, popup, newsletter). Text is truncated to ~4000 characters at
a word boundary and `truncated=True` is set.

**Prompt-injection flagging (`agent/tools/injection.py`).**
Web text is untrusted data. One shared keyword check runs on scraped page text
and on search result titles and snippets. A match sets `suspicious=True` and
lists the phrases in `injection_matches`. The text is still returned, not
removed: deleting it would hide the signal, and later stages (filter,
synthesizer) decide how far to trust a flagged source. The page is scanned in
full, before truncation, so an injection beyond the 4000-character cut still
flags the page.

**Logging.** Each tool call logs one line with its outcome
(`web_search ok: query=... items=5 suspicious=0`, `scrape_page failed: url=... error=HTTP 403`).
Retries log one warning each. The try-out script lowers httpx's own logger to
WARNING so each request is not logged twice.

**Testing.** Network is mocked with `httpx.MockTransport`. A fake clock replaces
`time.monotonic`/`time.sleep` inside the retry helper, so retry and deadline
behaviour is tested exactly (e.g. "the second attempt gets only the 4s left")
with no real waiting. Covered: dead URL, timeout, non-HTML, empty search, 4xx
not retried, 5xx recovery, bad JSON, malformed items, missing key, invalid URL,
truncation, slow-drip download, injection flagging in pages and snippets.

### Known limitations

- **CSS-hidden text is not removed.** Text hidden with `display:none`,
  `visibility:hidden`, tiny fonts or off-screen positioning is still extracted,
  because we parse HTML without rendering it. This is a classic place to hide
  prompt injections. Mitigated only by the keyword flag; accepted for now.
- **Injection detection is a keyword heuristic.** It catches common phrasings
  ("ignore previous instructions", "you are now a ...") and misses paraphrases,
  other languages and encoded text. It is a signal, not a security boundary;
  the real defence is that later stages treat web text as data.
- **The 15s scrape deadline is slightly soft.** The deadline is enforced before
  each attempt, between attempts and during the download. Within one attempt,
  httpx applies the timeout per phase (connect, then waiting for the first
  response bytes), so a server that is slow in both phases could push a single
  call somewhat past 15s. A truly hard cap would need a thread or async
  cancellation, which isn't worth the complexity yet.
- **Search worst case is ~33s.** Kept deliberately (2 retries, no deadline), as
  search is called rarely and everything depends on it.
- **No JavaScript rendering.** Pages that build their content in the browser
  return little or no text and fail with "no readable text found on page".
- **Paywalls and bot blocking.** Some sites answer 403 or a cookie wall to
  non-browser clients despite the realistic user agent. These come back as
  clean failures; there is no workaround by design.
- **Empty search counts as a failure** (`ok=False`, "no search results"), as per
  the Phase 1 spec, so callers can't mistake "nothing found" for success.
