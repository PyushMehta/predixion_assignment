"""The Streamlit page only calls orchestrator.run() and displays the RunResult (no network, no LLM)."""

from pathlib import Path

import pytest
from pydantic import BaseModel
from streamlit.testing.v1 import AppTest

from agent import orchestrator
from agent.llm import complete_structured, providers_from_env
from llm_fakes import FakeClient, json_completion
from test_orchestrator import PLAN, FakeTools, answer

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://primary.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "k1")
    monkeypatch.setenv("LLM_MODEL", "primary-model")
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "https://fallback.example/v1")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "k2")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "fallback-model")
    return monkeypatch


# ---------- the outage switch (lives in llm.py, not in the UI) ----------

class Answer(BaseModel):
    answer: str


def test_simulated_outage_fails_over_to_fallback(env):
    providers = providers_from_env(simulate_primary_outage=True)
    fallback = FakeClient(json_completion({"answer": "42"}))
    providers[1] = providers[1].__class__(name=providers[1].name, model=providers[1].model, client=fallback)
    result = complete_structured("s", "u", Answer, providers=providers)
    assert result.ok and result.fallback_used and result.provider == "fallback.example"
    assert len(fallback.calls) == 1


def test_simulated_outage_never_touches_the_fallback(env):
    env.delenv("LLM_API_KEY")  # primary not configured: the fallback must stay real
    providers = providers_from_env(simulate_primary_outage=True)
    assert [p.name for p in providers] == ["fallback.example"]
    assert providers[0].client.__class__.__name__ == "OpenAI"


# ---------- the page ----------

@pytest.fixture
def fake_run(monkeypatch, env, tmp_path):
    """Replace orchestrator.run with a real run on fake tools and a fake LLM; record its arguments."""
    calls = []
    real_run = orchestrator.run

    def run(question, *, providers=None, **kwargs):
        calls.append({"question": question, "providers": providers})
        tools = FakeTools()
        client = FakeClient(json_completion(PLAN), json_completion(answer(("S1", "S2"))))
        from llm_fakes import provider
        return real_run(question, providers=[provider(client)], search_fn=tools.search,
                        scrape_fn=tools.scrape, runs_dir=tmp_path)

    monkeypatch.setattr(orchestrator, "run", run)
    return calls


def page_text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [m.value for m in at.caption]
    return "\n".join(parts)


def test_page_loads_without_running_the_agent(fake_run):
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert at.button[0].disabled  # no question yet
    assert fake_run == []


def test_run_shows_answer_badge_findings_sources_and_trace(fake_run):
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.text_area[0].input("Is nuclear power safer than wind?").run()
    at.button[0].click().run()
    assert not at.exception
    assert len(fake_run) == 1 and fake_run[0]["question"] == "Is nuclear power safer than wind?"

    text = page_text(at)
    assert ":orange-background[**MEDIUM confidence**]" in text
    assert "`S1` `S2`" in text  # finding with its source ids
    assert "[OWID safety](https://ourworldindata.org/safety)" in text  # clickable source
    assert [e.label for e in at.expander] == ["Plan", "Filter decisions", "Timings, LLM calls and fallbacks"]
    assert "relevance 0.20 below threshold 0.5" in str([t.value for t in at.table])
    assert [s.value for s in at.subheader][:3] == ["Answer", "Key findings", "Sources"]


def test_outage_checkbox_is_passed_through_to_the_run(fake_run):
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.checkbox[0].check().run()
    at.text_area[0].input("Q?").run()
    at.button[0].click().run()
    assert not at.exception
    primary = fake_run[0]["providers"][0]
    assert primary.client.__class__.__name__ == "_OutageClient"
    assert "simulated an outage" in " ".join(i.value for i in at.info)


# ---------- the outage switch via environment (CLI, eval, any run) ----------

@pytest.mark.parametrize("value, simulated", [("1", True), ("true", True), ("ON", True), ("0", False), ("", False)])
def test_outage_switch_from_environment(env, value, simulated):
    env.setenv("LLM_SIMULATE_PRIMARY_OUTAGE", value)
    primary = providers_from_env()[0]
    assert (primary.client.__class__.__name__ == "_OutageClient") is simulated


def test_outage_switch_is_off_by_default(env):
    env.delenv("LLM_SIMULATE_PRIMARY_OUTAGE", raising=False)
    assert providers_from_env()[0].client.__class__.__name__ == "OpenAI"


def test_explicit_argument_overrides_environment(env):
    env.setenv("LLM_SIMULATE_PRIMARY_OUTAGE", "1")
    assert providers_from_env(simulate_primary_outage=False)[0].client.__class__.__name__ == "OpenAI"


def test_simulated_outage_is_logged_loudly(env, caplog):
    env.setenv("LLM_SIMULATE_PRIMARY_OUTAGE", "1")
    with caplog.at_level("WARNING", logger="agent.llm"):
        providers_from_env()
    assert "SIMULATED OUTAGE" in caplog.text and "LLM_SIMULATE_PRIMARY_OUTAGE" in caplog.text
