"""LLM wrapper: one function, complete_structured(), used by the rest of the agent.

- Talks to any OpenAI-compatible endpoint through the OpenAI SDK. Providers are
  chosen by env vars only (primary: LLM_*, optional fallback: LLM_FALLBACK_*).
- Asks for JSON matching a Pydantic schema (response_format json_schema,
  non-strict, which both Gemini and Groq accept), then ALWAYS re-validates with
  Pydantic: the API does not enforce min/max lengths or our own validators.
- One repair attempt: if validation fails, the error is sent back to the model.
- Timeouts, 429 and 503 are retried with backoff. The fallback provider is
  tried once, but only for availability failures (timeouts, 429, 5xx,
  connection errors): a schema failure or a bad request would just repeat.
- An optional `deadline` (monotonic time) bounds the whole call: each attempt's
  timeout shrinks to the time left, and no retry, repair or failover starts
  without enough time for it. Never raises: failures come back as
  LLMResult(ok=False) with an `error_kind`.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Generic, TypeVar
from urllib.parse import urlparse

import httpx
import openai
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

TIMEOUT_S = 60.0
MAX_RETRIES = 2  # per provider, on timeout / 429 / 503
BACKOFF_S = 5.0  # waits 5s, then 10s, unless the server says how long
MAX_WAIT_S = 30.0  # cap on any single server-requested wait
RETRYABLE_STATUS = {429, 503}
MAX_ERROR_LINES = 8  # validation errors sent back to the model
MIN_ATTEMPT_S = 5.0  # don't start an LLM request (or retry/repair) with less time than this left
MIN_FAILOVER_S = 20.0  # don't switch provider with less time than this left

# error_kind values
AVAILABILITY = "availability"  # timeout, 429, 5xx, connection: another provider may succeed
VALIDATION = "validation"  # output failed the schema even after repair
DEADLINE = "deadline"  # not enough time left to try
OTHER = "other"  # 4xx and anything else: a config or request problem

JSON_INSTRUCTION = (
    "\n\nRespond with a single JSON object that matches the provided JSON schema. "
    "No prose, no markdown code fences."
)
REPAIR_PROMPT = (
    "Your previous response did not pass validation:\n{errors}\n\n"
    "Return a corrected JSON object that fixes these errors and matches the schema."
)

# Indirection so tests can drive a fake clock instead of real time.
_sleep = time.sleep
_monotonic = time.monotonic


class LLMResult(BaseModel, Generic[T]):
    ok: bool
    error: str | None = None
    error_kind: str | None = None  # AVAILABILITY, VALIDATION, DEADLINE or OTHER
    data: T | None = None
    provider: str = ""
    model: str = ""
    fallback_used: bool = False
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Provider:
    name: str  # host of the base URL, e.g. "api.groq.com"
    model: str
    client: Any  # openai.OpenAI, or a fake with .chat.completions.create(**kwargs)


class _OutageClient:
    """Stands in for a provider that can't be reached: every request fails with a connection error."""

    def __init__(self, base_url: str):
        request = httpx.Request("POST", base_url)

        def create(**kwargs):
            raise openai.APIConnectionError(message="simulated outage: provider unreachable", request=request)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def providers_from_env(*, simulate_primary_outage: bool | None = None) -> list[Provider]:
    """Primary (LLM_*) then optional fallback (LLM_FALLBACK_*), skipping incomplete ones.

    `simulate_primary_outage` makes the primary unreachable (a connection error,
    i.e. an availability failure), to demonstrate failover to the fallback.
    When not given, it comes from LLM_SIMULATE_PRIMARY_OUTAGE (off by default).
    """
    if simulate_primary_outage is None:
        simulate_primary_outage = os.getenv("LLM_SIMULATE_PRIMARY_OUTAGE", "").strip().lower() in {"1", "true", "yes", "on"}
    providers = []
    for prefix in ("LLM_", "LLM_FALLBACK_"):
        base_url = os.getenv(f"{prefix}BASE_URL", "").strip()
        api_key = os.getenv(f"{prefix}API_KEY", "").strip()
        model = os.getenv(f"{prefix}MODEL", "").strip()
        if not (base_url and api_key and model):
            log.debug("%s* not fully configured; skipping that provider", prefix)
            continue
        if simulate_primary_outage and prefix == "LLM_":
            client = _OutageClient(base_url)
            log.warning(
                "SIMULATED OUTAGE: primary LLM provider %s will fail every request (demo switch; "
                "unset LLM_SIMULATE_PRIMARY_OUTAGE to turn off)", base_url,
            )
        else:
            client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT_S, max_retries=0)
        providers.append(Provider(name=urlparse(base_url).hostname or base_url, model=model, client=client))
    return providers


def complete_structured(
    system: str,
    user: str,
    schema: type[T],
    *,
    providers: list[Provider] | None = None,
    deadline: float | None = None,
) -> LLMResult[T]:
    """Get a validated `schema` instance from the LLM. Never raises.

    `providers` defaults to providers_from_env(); tests pass fakes.
    `deadline` is a _monotonic() timestamp the whole call must finish by.
    """
    start = _monotonic()
    try:
        providers = providers_from_env() if providers is None else providers
        if not providers:
            return LLMResult[schema](
                ok=False, error_kind=OTHER,
                error="no LLM provider configured (set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)",
            )

        errors = []
        result = None
        for index, provider in enumerate(providers):
            if index > 0:
                blocker = _failover_blocker(result, deadline)
                if blocker:
                    log.warning("llm: not failing over to %s: %s", provider.name, blocker)
                    errors.append(f"fallback {provider.name} not tried: {blocker}")
                    break
                log.warning("llm provider %s unavailable; trying fallback %s", providers[index - 1].name, provider.name)
            result = _complete_with_provider(provider, system, user, schema, deadline)
            result.fallback_used = index > 0
            result.latency_ms = int((_monotonic() - start) * 1000)
            if result.ok:
                return result
            errors.append(f"{provider.name}/{provider.model}: {result.error}")
        result.error = "LLM call failed: " + " | ".join(errors)
        return result
    except Exception as exc:  # last line of defence: never raise
        log.exception("unexpected error in complete_structured")
        return LLMResult[schema](
            ok=False, error_kind=OTHER, error=f"unexpected error: {type(exc).__name__}: {exc}",
            latency_ms=int((_monotonic() - start) * 1000),
        )


def _failover_blocker(result: LLMResult | None, deadline: float | None) -> str | None:
    """Why the next provider should NOT be tried, or None if it should."""
    if result is None or result.error_kind != AVAILABILITY:
        kind = result.error_kind if result else "unknown"
        return f"primary failed with a {kind} error, which another provider would not fix"
    if deadline is not None and deadline - _monotonic() < MIN_FAILOVER_S:
        return f"only {max(deadline - _monotonic(), 0):.0f}s left before the run deadline"
    return None


def _complete_with_provider(
    provider: Provider, system: str, user: str, schema: type[T], deadline: float | None
) -> LLMResult[T]:
    """Call one provider: request, validate, and repair once if needed."""
    result = LLMResult[schema](ok=False, provider=provider.name, model=provider.model)
    messages = [
        {"role": "system", "content": system + JSON_INSTRUCTION},
        {"role": "user", "content": user},
    ]
    for validation_attempt in (1, 2):
        response, error, kind = _call_with_retry(provider, messages, schema, deadline)
        if response is None:
            if validation_attempt == 2 and kind == DEADLINE:
                kind = VALIDATION  # the first answer was invalid; there was just no time to repair it
                error = f"response failed validation and no time was left to repair it ({error})"
            result.error, result.error_kind = error, kind
            return result
        usage = getattr(response, "usage", None)
        result.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
        result.output_tokens += getattr(usage, "completion_tokens", 0) or 0

        content = response.choices[0].message.content or ""
        try:
            result.data = schema.model_validate_json(_strip_code_fences(content))
            result.ok = True
            return result
        except ValidationError as exc:
            summary = _summarise_validation_error(exc)
            if validation_attempt == 2:
                result.error = f"response failed validation after one repair attempt: {summary}"
                result.error_kind = VALIDATION
                log.warning("llm output still invalid after repair: provider=%s error=%s", provider.name, summary)
                return result
            log.warning("llm output invalid, asking model to repair: provider=%s error=%s", provider.name, summary)
            messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": REPAIR_PROMPT.format(errors=summary)},
            ]
    raise AssertionError("unreachable")


def _call_with_retry(provider: Provider, messages: list[dict], schema: type[BaseModel], deadline: float | None):
    """One logical LLM request with retries. Returns (response, None, None) or (None, error, error_kind)."""
    for attempt in range(MAX_RETRIES + 1):
        timeout = TIMEOUT_S
        if deadline is not None:
            left = deadline - _monotonic()
            if left < MIN_ATTEMPT_S:
                return None, f"not enough time left before the run deadline ({max(left, 0):.0f}s)", DEADLINE
            timeout = min(TIMEOUT_S, left)
        start = _monotonic()
        try:
            response = provider.client.chat.completions.create(
                model=provider.model,
                messages=messages,
                temperature=_temperature(),
                response_format=_response_format(schema),
                timeout=timeout,
            )
        except Exception as exc:
            latency_ms = int((_monotonic() - start) * 1000)
            error, wait, kind = _classify_error(exc, attempt, timeout)
            log.warning(
                "llm call failed: provider=%s model=%s latency_ms=%d attempt=%d/%d error=%s",
                provider.name, provider.model, latency_ms, attempt + 1, MAX_RETRIES + 1, error,
            )
            if wait is None or attempt == MAX_RETRIES:
                return None, error, kind
            if deadline is not None and deadline - _monotonic() - wait < MIN_ATTEMPT_S:
                return None, f"{error} (no time left to retry before the run deadline)", kind
            _sleep(wait)
            continue
        usage = getattr(response, "usage", None)
        log.info(
            "llm call ok: provider=%s model=%s latency_ms=%d tokens_in=%s tokens_out=%s",
            provider.name, provider.model, int((_monotonic() - start) * 1000),
            getattr(usage, "prompt_tokens", "?"), getattr(usage, "completion_tokens", "?"),
        )
        return response, None, None
    raise AssertionError("unreachable")


def _classify_error(exc: Exception, attempt: int, timeout: float = TIMEOUT_S) -> tuple[str, float | None, str]:
    """Return (error message, seconds to wait before retrying or None, error_kind)."""
    backoff = BACKOFF_S * 2**attempt
    if isinstance(exc, openai.APITimeoutError):
        return f"timed out after {timeout:.0f}s", backoff, AVAILABILITY
    if isinstance(exc, openai.APIStatusError):
        body = json.dumps(exc.body, default=str) if exc.body is not None else str(exc)
        error = f"HTTP {exc.status_code}: {body[:300]}"
        kind = AVAILABILITY if exc.status_code == 429 or exc.status_code >= 500 else OTHER
        if exc.status_code not in RETRYABLE_STATUS:
            return error, None, kind
        if exc.status_code == 429 and re.search(r"per ?day", body, re.I):
            return f"daily quota exhausted (not retrying): {body[:300]}", None, AVAILABILITY
        hinted = _server_retry_delay(exc, body)
        return error, min(hinted, MAX_WAIT_S) if hinted is not None else backoff, AVAILABILITY
    if isinstance(exc, openai.APIConnectionError):
        return f"connection error: {exc}", None, AVAILABILITY
    return f"{type(exc).__name__}: {exc}", None, OTHER


def _server_retry_delay(exc: openai.APIStatusError, body: str) -> float | None:
    """Seconds the server asked us to wait: Retry-After header (Groq) or retryDelay in the body (Gemini)."""
    header = exc.response.headers.get("retry-after") if exc.response is not None else None
    try:
        if header is not None:
            return float(header)
    except ValueError:
        pass
    match = re.search(r'retryDelay"?\s*:\s*"?(\d+(?:\.\d+)?)s', body)
    return float(match.group(1)) if match else None


def _response_format(schema: type[BaseModel]) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
    }


def _temperature() -> float:
    try:
        return float(os.getenv("LLM_TEMPERATURE", "0"))
    except ValueError:
        return 0.0


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return match.group(1) if match else text


def _summarise_validation_error(exc: ValidationError) -> str:
    lines = [
        f"- {'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
        for err in exc.errors(include_input=False)[:MAX_ERROR_LINES]
    ]
    return "\n".join(lines)
