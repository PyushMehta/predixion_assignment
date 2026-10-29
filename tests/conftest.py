"""Shared test fixtures. All network calls are mocked with httpx.MockTransport."""

import httpx
import pytest

from agent import llm
from agent.tools import net


class FakeClock:
    """Stands in for time.monotonic/time.sleep: time only moves when told to."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Replace real time in the tool and LLM retry loops, so tests never wait
    and deadlines can be tested exactly. Handlers call clock.advance() to
    simulate a slow request; backoff sleeps advance it automatically."""
    fake = FakeClock()
    for module in (net, llm):
        monkeypatch.setattr(module, "_monotonic", fake.monotonic)
        monkeypatch.setattr(module, "_sleep", fake.advance)
    return fake


@pytest.fixture
def mock_client():
    """Build an httpx.Client whose requests are answered by `handler`.

    Returns (client, calls) where `calls` records every request made,
    so tests can check how many retries happened.
    """
    clients = []

    def make(handler):
        calls = []

        def recording_handler(request):
            calls.append(request)
            return handler(request)

        client = httpx.Client(transport=httpx.MockTransport(recording_handler))
        clients.append(client)
        return client, calls

    yield make
    for client in clients:
        client.close()
