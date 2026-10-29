"""Phase 5 fix 1: every LLM retry path respects the overall deadline; failover only for availability errors."""

from pydantic import BaseModel

from agent.llm import AVAILABILITY, DEADLINE, MIN_FAILOVER_S, OTHER, VALIDATION, complete_structured
from llm_fakes import FakeClient, completion, json_completion, provider, status_error, timeout_error


class Answer(BaseModel):
    answer: str


GOOD = {"answer": "42"}


def ask(*providers, deadline=None):
    return complete_structured("s", "u", Answer, providers=list(providers), deadline=deadline)


def test_attempt_timeout_shrinks_to_time_left(clock):
    client = FakeClient(json_completion(GOOD))
    assert ask(provider(client), deadline=25.0).ok
    assert client.calls[0]["timeout"] == 25.0


def test_no_request_when_too_little_time_left(clock):
    clock.advance(88)
    client = FakeClient()
    result = ask(provider(client), deadline=90.0)
    assert not result.ok and result.error_kind == DEADLINE
    assert client.calls == []


def test_retry_skipped_when_backoff_would_pass_deadline(clock):
    def slow_timeout(**kw):
        clock.advance(kw["timeout"])
        raise timeout_error()

    client = FakeClient()
    client.chat.completions.create = lambda **kw: (client.calls.append(kw), slow_timeout(**kw))[1]
    result = ask(provider(client), deadline=30.0)
    # attempt 1 uses the full 30s; no time to back off and retry
    assert len(client.calls) == 1
    assert "no time left to retry" in result.error and result.error_kind == AVAILABILITY
    assert clock.now == 30.0


def test_validation_failure_does_not_fail_over():
    primary = FakeClient(completion("bad"), completion("still bad"))
    fallback = FakeClient(json_completion(GOOD))
    result = ask(provider(primary), provider(fallback, name="fallback.test"))
    assert not result.ok and result.error_kind == VALIDATION
    assert fallback.calls == []
    assert "fallback fallback.test not tried" in result.error


def test_bad_request_does_not_fail_over():
    primary = FakeClient(status_error(400, body={"error": "bad key"}))
    fallback = FakeClient(json_completion(GOOD))
    result = ask(provider(primary), provider(fallback, name="fallback.test"))
    assert not result.ok and result.error_kind == OTHER
    assert fallback.calls == []


def test_availability_failure_fails_over_when_time_allows(clock):
    primary = FakeClient(status_error(503), status_error(503), status_error(503))
    fallback = FakeClient(json_completion(GOOD))
    result = ask(provider(primary), provider(fallback, name="fallback.test"), deadline=90.0)
    assert result.ok and result.fallback_used


def test_failover_skipped_when_too_little_time_left(clock):
    def slow_503(**kw):
        clock.advance(20)
        raise status_error(503)

    primary = FakeClient()
    primary.chat.completions.create = lambda **kw: (primary.calls.append(kw), slow_503(**kw))[1]
    fallback = FakeClient(json_completion(GOOD))
    result = ask(provider(primary), provider(fallback, name="fallback.test"), deadline=60.0)
    assert not result.ok
    assert fallback.calls == []
    assert f"s left before the run deadline" in result.error
    assert 60.0 - clock.now < MIN_FAILOVER_S


def test_repair_skipped_when_no_time_left_counts_as_validation(clock):
    def slow_bad(**kw):
        clock.advance(27)
        return completion("not json")

    client = FakeClient()
    client.chat.completions.create = lambda **kw: (client.calls.append(kw), slow_bad(**kw))[1]
    result = ask(provider(client), deadline=30.0)
    assert len(client.calls) == 1  # 3s left: no repair attempt
    assert result.error_kind == VALIDATION
    assert "no time was left to repair" in result.error
