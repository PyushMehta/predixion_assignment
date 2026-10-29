"""Shared HTTP retry helper for the tools.

Retries only failures that are likely to be temporary: timeouts, HTTP 429
and HTTP 5xx. Anything else (4xx, DNS failure, refused connection) is
returned or raised immediately, because retrying would just waste time.

An optional overall `deadline` caps the whole call (all attempts plus
backoff waits): each attempt's timeout shrinks to the time left, and no
retry starts unless at least MIN_ATTEMPT_S would remain for it.
"""

import logging
import time

import httpx

log = logging.getLogger(__name__)

TIMEOUT_S = 10.0
MAX_RETRIES = 2
BACKOFF_S = 1.0  # waits 1s, then 2s
MIN_ATTEMPT_S = 1.0  # don't start a retry with less time than this left

# Indirection so tests can drive a fake clock instead of real time.
_sleep = time.sleep
_monotonic = time.monotonic


def deadline_in(seconds: float) -> float:
    """Return a deadline `seconds` from now, for use with send_with_retry."""
    return _monotonic() + seconds


def time_left(deadline: float) -> float:
    return deadline - _monotonic()


def _is_retryable_status(status: int) -> bool:
    return status == 429 or status >= 500


def _can_retry(attempt: int, retries: int, wait: float, deadline: float | None) -> bool:
    if attempt >= retries:
        return False
    return deadline is None or time_left(deadline) - wait >= MIN_ATTEMPT_S


def send_with_retry(
    client: httpx.Client,
    request: httpx.Request,
    *,
    retries: int = MAX_RETRIES,
    backoff: float = BACKOFF_S,
    deadline: float | None = None,
    stream: bool = False,
) -> httpx.Response:
    """Send `request`, retrying timeouts and 429/5xx with exponential backoff.

    Returns the last response (which may still be an error status), or
    raises the last httpx exception. Callers convert both into result objects.
    """
    for attempt in range(retries + 1):
        if deadline is not None:
            attempt_timeout = min(TIMEOUT_S, max(time_left(deadline), 0.0))
            request.extensions["timeout"] = httpx.Timeout(attempt_timeout).as_dict()
        wait = backoff * 2**attempt
        try:
            response = client.send(request, stream=stream, follow_redirects=True)
        except httpx.TimeoutException as exc:
            if not _can_retry(attempt, retries, wait, deadline):
                raise
            log.warning("timeout on %s (attempt %d/%d): %s", request.url, attempt + 1, retries + 1, exc)
        else:
            if not _is_retryable_status(response.status_code) or not _can_retry(attempt, retries, wait, deadline):
                return response
            response.close()
            log.warning(
                "HTTP %d from %s (attempt %d/%d)",
                response.status_code, request.url, attempt + 1, retries + 1,
            )
        _sleep(wait)
    raise AssertionError("unreachable")
