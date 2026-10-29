"""Failed LLM attempts are recorded: provider, model, error type, latency, and whether failover followed."""

import json

from pydantic import BaseModel

from agent.llm import AVAILABILITY, OTHER, VALIDATION, complete_structured
from agent.orchestrator import RunResult, run
from llm_fakes import FakeClient, completion, json_completion, provider, status_error, timeout_error
from test_orchestrator import PLAN, QUESTION, FakeTools, answer


class Answer(BaseModel):
    answer: str


GOOD = {"answer": "42"}


def ask(*providers, deadline=None):
    return complete_structured("s", "u", Answer, providers=list(providers), deadline=deadline)


def slow(client, clock, seconds, error):
    """Make every call on `client` take `seconds` of fake time and then raise `error`."""
    def create(**kwargs):
        client.calls.append(kwargs)
        clock.advance(seconds)
        raise error
    client.chat.completions.create = create
    return client


def test_success_has_no_failed_attempts():
    result = ask(provider(FakeClient(json_completion(GOOD))))
    assert result.ok and result.failed_attempts == []


def test_retried_errors_are_recorded_with_kind_and_latency(clock):
    client = slow(FakeClient(), clock, 7, timeout_error())
    result = ask(provider(client))
    assert not result.ok
    assert [(a.provider, a.model, a.error_kind, a.latency_ms, a.failover_followed) for a in result.failed_attempts] == [
        ("primary.test", "fake-model", AVAILABILITY, 7000, False),
    ] * 3
    assert "timed out" in result.failed_attempts[0].error


def test_failover_is_marked_on_the_primary_attempts():
    primary = FakeClient(status_error(503), status_error(503), status_error(503))
    fallback = FakeClient(json_completion(GOOD))
    result = ask(provider(primary), provider(fallback, name="fallback.test", model="fb-model"))
    assert result.ok and result.fallback_used and result.provider == "fallback.test"
    assert len(result.failed_attempts) == 3
    assert all(a.provider == "primary.test" and a.error_kind == AVAILABILITY and a.failover_followed
               for a in result.failed_attempts)
    assert "HTTP 503" in result.failed_attempts[0].error


def test_both_providers_failing_records_both_and_only_the_primary_led_to_failover():
    primary = FakeClient(timeout_error(), timeout_error(), timeout_error())
    fallback = FakeClient(status_error(400, body={"error": "bad request"}))
    result = ask(provider(primary), provider(fallback, name="fallback.test"))
    assert not result.ok
    summary = [(a.provider, a.error_kind, a.failover_followed) for a in result.failed_attempts]
    assert summary == [("primary.test", AVAILABILITY, True)] * 3 + [("fallback.test", OTHER, False)]


def test_validation_failures_are_recorded_and_do_not_fail_over():
    primary = FakeClient(completion("not json"), completion("still not json"))
    fallback = FakeClient(json_completion(GOOD))
    result = ask(provider(primary), provider(fallback, name="fallback.test"))
    assert not result.ok and fallback.calls == []
    assert [(a.error_kind, a.failover_followed) for a in result.failed_attempts] == [(VALIDATION, False)] * 2
    assert result.failed_attempts[0].error.startswith("output failed validation")


def test_repaired_output_keeps_the_record_of_the_first_failure():
    client = FakeClient(completion("not json"), json_completion(GOOD))
    result = ask(provider(client))
    assert result.ok
    assert [a.error_kind for a in result.failed_attempts] == [VALIDATION]


def test_skipped_request_for_lack_of_time_is_not_an_attempt(clock):
    clock.advance(88)
    result = ask(provider(FakeClient()), deadline=90.0)
    assert not result.ok and result.failed_attempts == []


def test_failed_attempts_appear_in_the_run_trace(tmp_path):
    tools = FakeTools()
    client = FakeClient(
        json_completion(PLAN),
        status_error(503), json_completion(answer()),  # synthesis: one 503, then success
    )
    result = run(QUESTION, providers=[provider(client)], search_fn=tools.search, scrape_fn=tools.scrape, runs_dir=tmp_path)
    plan_call, synth_call = result.trace.llm_calls
    assert plan_call.failed_attempts == []
    assert [(a.error_kind, a.failover_followed) for a in synth_call.failed_attempts] == [(AVAILABILITY, False)]
    saved = RunResult.model_validate_json((tmp_path / f"{result.trace.run_id}.json").read_text(encoding="utf-8"))
    assert saved.trace.llm_calls[1].failed_attempts[0].provider == "primary.test"
    assert "failed_attempts" in json.loads(saved.model_dump_json())["trace"]["llm_calls"][1]
