import pytest
from pydantic import BaseModel, Field

from agent import llm
from agent.llm import complete_structured, providers_from_env
from llm_fakes import FakeClient, completion, json_completion, provider, status_error, timeout_error


class Answer(BaseModel):
    answer: str
    tags: list[str] = Field(min_length=1, max_length=3)


GOOD = {"answer": "42", "tags": ["math"]}
TOO_MANY_TAGS = {"answer": "42", "tags": ["a", "b", "c", "d"]}


def ask(*providers):
    return complete_structured("Be helpful.", "What is 6x7?", Answer, providers=list(providers))


# ---------- success, request shape ----------

def test_primary_succeeds_and_fallback_is_not_called():
    primary = FakeClient(json_completion(GOOD, prompt_tokens=11, completion_tokens=7))
    fallback = FakeClient()
    result = ask(provider(primary), provider(fallback, name="fallback.test"))
    assert result.ok and result.error is None
    assert result.data == Answer(**GOOD)
    assert (result.provider, result.model, result.fallback_used) == ("primary.test", "fake-model", False)
    assert (result.input_tokens, result.output_tokens) == (11, 7)
    assert fallback.calls == []


def test_request_uses_json_schema_temperature_0_and_60s_timeout():
    client = FakeClient(json_completion(GOOD))
    ask(provider(client))
    call = client.calls[0]
    assert call["temperature"] == 0.0
    assert call["timeout"] == 60.0
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["schema"] == Answer.model_json_schema()
    assert call["messages"][0]["role"] == "system" and "JSON" in call["messages"][0]["content"]
    assert call["messages"][1] == {"role": "user", "content": "What is 6x7?"}


def test_code_fences_around_json_are_tolerated():
    client = FakeClient(completion('```json\n{"answer": "42", "tags": ["math"]}\n```'))
    assert ask(provider(client)).ok


# ---------- validation + one repair ----------

def test_invalid_json_is_repaired_on_retry():
    client = FakeClient(completion("Sure! The answer is 42."), json_completion(GOOD))
    result = ask(provider(client))
    assert result.ok
    assert len(client.calls) == 2
    repair_messages = client.calls[1]["messages"]
    assert repair_messages[2] == {"role": "assistant", "content": "Sure! The answer is 42."}
    assert "Invalid JSON" in repair_messages[3]["content"]


def test_schema_violation_is_fixed_on_retry_with_error_sent_back():
    client = FakeClient(json_completion(TOO_MANY_TAGS), json_completion(GOOD))
    result = ask(provider(client))
    assert result.ok and result.data.tags == ["math"]
    repair_prompt = client.calls[1]["messages"][-1]["content"]
    assert "tags" in repair_prompt and "at most 3" in repair_prompt
    assert (result.input_tokens, result.output_tokens) == (20, 40)  # both calls counted


def test_schema_violation_twice_is_clean_error():
    client = FakeClient(json_completion(TOO_MANY_TAGS), json_completion(TOO_MANY_TAGS))
    result = ask(provider(client))
    assert not result.ok and result.data is None
    assert "failed validation after one repair attempt" in result.error
    assert len(client.calls) == 2  # exactly one repair, no more


# ---------- retries ----------

def test_timeout_is_retried_twice_then_clean_error(clock):
    client = FakeClient(timeout_error(), timeout_error(), timeout_error())
    result = ask(provider(client))
    assert not result.ok
    assert "timed out after 60s" in result.error
    assert len(client.calls) == 3
    assert clock.now == 15.0  # backoff 5s + 10s


def test_503_is_retried_and_recovers():
    client = FakeClient(status_error(503), json_completion(GOOD))
    result = ask(provider(client))
    assert result.ok
    assert len(client.calls) == 2


def test_429_honours_retry_after_header(clock):
    client = FakeClient(status_error(429, headers={"retry-after": "7"}), json_completion(GOOD))
    assert ask(provider(client)).ok
    assert clock.now == 7.0


def test_429_honours_gemini_retry_delay_in_body_capped(clock):
    body = [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                       "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "45s"}]}}]
    client = FakeClient(status_error(429, body=body), json_completion(GOOD))
    assert ask(provider(client)).ok
    assert clock.now == 30.0  # 45s requested, capped at MAX_WAIT_S


def test_429_daily_quota_is_not_retried():
    body = {"error": {"message": "Quota exceeded for metric: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}
    client = FakeClient(status_error(429, body=body))
    result = ask(provider(client))
    assert not result.ok
    assert "daily quota exhausted" in result.error
    assert len(client.calls) == 1


def test_400_is_not_retried():
    client = FakeClient(status_error(400, body={"error": "bad request"}))
    result = ask(provider(client))
    assert not result.ok and "HTTP 400" in result.error
    assert len(client.calls) == 1


# ---------- fallback provider ----------

def test_primary_fails_fallback_provider_succeeds():
    primary = FakeClient(status_error(503), status_error(503), status_error(503))
    fallback = FakeClient(json_completion(GOOD))
    result = ask(provider(primary), provider(fallback, name="fallback.test", model="fallback-model"))
    assert result.ok
    assert (result.provider, result.model, result.fallback_used) == ("fallback.test", "fallback-model", True)
    assert len(primary.calls) == 3 and len(fallback.calls) == 1


def test_both_providers_fail_is_clean_error():
    primary = FakeClient(timeout_error(), timeout_error(), timeout_error())
    fallback = FakeClient(status_error(400, body={"error": "nope"}))
    result = ask(provider(primary), provider(fallback, name="fallback.test"))
    assert not result.ok and result.data is None
    assert result.error.startswith("all LLM providers failed")
    assert "primary.test/fake-model: timed out" in result.error
    assert "fallback.test/fake-model: HTTP 400" in result.error


def test_no_provider_configured_is_clean_error():
    result = complete_structured("s", "u", Answer, providers=[])
    assert not result.ok and "no LLM provider configured" in result.error


def test_unexpected_client_bug_is_clean_error():
    client = FakeClient(completion(None))  # content=None, e.g. a refusal
    client.script.append(json_completion(GOOD))
    assert ask(provider(client)).ok  # empty content is treated as invalid output and repaired

    broken = FakeClient()
    broken.chat.completions.create = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    result = ask(provider(broken))
    assert not result.ok and "RuntimeError: boom" in result.error


# ---------- configuration ----------

@pytest.fixture
def clean_env(monkeypatch):
    for name in ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
                 "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_MODEL", "LLM_TEMPERATURE"]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_providers_from_env_primary_and_fallback(clean_env):
    clean_env.setenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
    clean_env.setenv("LLM_API_KEY", "k1")
    clean_env.setenv("LLM_MODEL", "gemini-x")
    clean_env.setenv("LLM_FALLBACK_BASE_URL", "https://api.groq.com/openai/v1")
    clean_env.setenv("LLM_FALLBACK_API_KEY", "k2")
    clean_env.setenv("LLM_FALLBACK_MODEL", "openai/gpt-oss-120b")
    providers = providers_from_env()
    assert [(p.name, p.model) for p in providers] == [
        ("generativelanguage.googleapis.com", "gemini-x"), ("api.groq.com", "openai/gpt-oss-120b"),
    ]
    assert providers[0].client.max_retries == 0  # we do our own retries
    assert providers[0].client.timeout == 60.0


def test_fallback_skipped_when_not_fully_configured(clean_env):
    clean_env.setenv("LLM_BASE_URL", "https://example.test/v1")
    clean_env.setenv("LLM_API_KEY", "k1")
    clean_env.setenv("LLM_MODEL", "m")
    clean_env.setenv("LLM_FALLBACK_BASE_URL", "https://api.groq.com/openai/v1")  # no key, no model
    assert [p.name for p in providers_from_env()] == ["example.test"]


def test_temperature_can_be_overridden(clean_env):
    clean_env.setenv("LLM_TEMPERATURE", "1.0")
    client = FakeClient(json_completion(GOOD))
    ask(provider(client))
    assert client.calls[0]["temperature"] == 1.0
    assert llm.TIMEOUT_S == 60.0
