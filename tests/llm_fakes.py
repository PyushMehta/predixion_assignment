"""Fake OpenAI-style client for LLM tests: scripted responses, no network."""

import copy
import json
from types import SimpleNamespace

import httpx
import openai

from agent.llm import Provider

_REQUEST = httpx.Request("POST", "https://llm.test/v1/chat/completions")


def completion(content: str, prompt_tokens: int = 10, completion_tokens: int = 20):
    """A minimal stand-in for a ChatCompletion response."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def json_completion(obj: dict, **kwargs):
    return completion(json.dumps(obj), **kwargs)


def timeout_error():
    return openai.APITimeoutError(request=_REQUEST)


def status_error(code: int, body=None, headers: dict | None = None):
    response = httpx.Response(code, request=_REQUEST, headers=headers or {})
    cls = {400: openai.BadRequestError, 429: openai.RateLimitError}.get(code, openai.InternalServerError)
    return cls(f"Error code: {code}", response=response, body=body)


class FakeClient:
    """Answers chat.completions.create() from a script of responses or exceptions."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []  # kwargs of every create() call, deep-copied
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if not self.script:
            raise AssertionError("FakeClient called more times than scripted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def provider(client: FakeClient, name: str = "primary.test", model: str = "fake-model") -> Provider:
    return Provider(name=name, model=model, client=client)
