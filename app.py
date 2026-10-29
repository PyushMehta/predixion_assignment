"""Thin Streamlit demo UI for the research agent.

    streamlit run app.py

No agent logic lives here: the page reads providers from the environment,
calls orchestrator.run() once per click, and displays the RunResult. The
CLI (main.py) remains the main interface.
"""

import logging

import streamlit as st
from dotenv import load_dotenv

from agent import orchestrator
from agent.llm import providers_from_env

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "httpx2", "trafilatura"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

BADGE_COLOURS = {"high": "green", "medium": "orange", "low": "red"}


def _md(text: str) -> str:
    """Neutralise characters that would break inline Markdown links."""
    return text.replace("[", "(").replace("]", ")")


st.set_page_config(page_title="Research Agent", layout="wide")

# ---------- sidebar ----------
with st.sidebar:
    st.header("Settings")
    simulate_outage = st.checkbox(
        "Simulate primary LLM outage",
        help="The primary provider fails with a connection error for this run, so the fallback provider is used.",
    )
    configured = providers_from_env()
    st.caption("LLM providers (from .env):")
    for label, p in zip(("primary", "fallback"), configured):
        st.caption(f"- {label}: `{p.name}` / `{p.model}`")
    if not configured:
        st.error("No LLM provider configured. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env.")
    elif simulate_outage and len(configured) < 2:
        st.warning("No fallback provider is configured, so the run will use its non-LLM fallbacks.")

# ---------- question ----------
st.title("Research Agent")
question = st.text_area(
    "Research question", placeholder="e.g. Is nuclear power safer than wind per TWh of electricity?", height=90,
)
if st.button("Run", type="primary", disabled=not question.strip()):
    with st.spinner("Researching: planning, searching, reading and synthesizing (up to ~90s)…"):
        providers = providers_from_env(simulate_primary_outage=simulate_outage)
        st.session_state["result"] = orchestrator.run(question, providers=providers)
        st.session_state["simulated_outage"] = simulate_outage

result = st.session_state.get("result")
if result is None:
    st.stop()

r, t = result.response, result.trace

# ---------- answer ----------
st.subheader("Answer")
if st.session_state.get("simulated_outage"):
    st.info("This run simulated an outage of the primary LLM provider.")
st.write(r.short_answer)
colour = BADGE_COLOURS[r.confidence.value]
st.markdown(f":{colour}-background[**{r.confidence.value.upper()} confidence**]  {r.confidence_reason}")

if r.key_findings:
    st.subheader("Key findings")
    for f in r.key_findings:
        st.markdown(f"- {f.claim} " + " ".join(f"`{sid}`" for sid in f.source_ids))

if r.sources:
    st.subheader("Sources")
    for s in r.sources:
        st.markdown(f"- `{s.id}` [{_md(s.title)}]({s.url}) (relevance {s.relevance:.2f})")

# ---------- trace ----------
with st.expander("Plan"):
    if t.plan:
        st.markdown(f"**Tool selection:** {t.tool_selection or 'n/a'}")
        st.markdown("**Sub-questions**")
        for sq in t.plan.sub_questions:
            st.markdown(f"- {sq}")
        st.markdown("**Steps**")
        st.table([{"step": s.step_id, "tool": s.tool, "goal": s.goal, "query": s.query or ""} for s in t.plan.steps])
    if "planner_fallback_plan" in t.fallbacks:
        st.warning("The planner LLM call failed, so a one-search fallback plan was used.")

with st.expander("Filter decisions"):
    st.markdown(f"**Kept ({len(t.filter_kept)})**")
    for k in t.filter_kept:
        st.markdown(f"- {k}")
    st.markdown(f"**Dropped ({len(t.filter_dropped)})**")
    if t.filter_dropped:
        st.table([{"url": d.url, "reason": d.reason} for d in t.filter_dropped])
    if t.skipped:
        st.markdown("**Not executed**")
        st.table([{"item": d.url, "reason": d.reason} for d in t.skipped])
    if t.filter_notes:
        st.markdown("**Notes**")
        for n in t.filter_notes:
            st.markdown(f"- {n}")

with st.expander("Timings, LLM calls and fallbacks"):
    c1, c2, c3 = st.columns(3)
    c1.metric("Total time", f"{t.total_ms / 1000:.1f}s")
    c2.metric("Tool calls", len(t.tool_calls))
    c3.metric("Deadline hit", "yes" if t.deadline_hit else "no")
    st.markdown(f"**Fallbacks fired:** {', '.join(t.fallbacks) or 'none'}")
    st.markdown("**LLM calls**")
    st.table([
        {"stage": c.stage, "ok": c.ok, "provider": c.provider, "model": c.model,
         "fallback provider": c.fallback_provider_used, "latency": f"{c.latency_ms / 1000:.1f}s",
         "tokens in/out": f"{c.input_tokens}/{c.output_tokens}", "error": (c.error or "")[:200]}
        for c in t.llm_calls
    ])
    st.markdown("**Tool calls**")
    st.table([
        {"tool": c.tool, "input": c.input, "ok": c.ok, "latency": f"{c.latency_ms / 1000:.1f}s",
         "detail / error": c.detail if c.ok else (c.error or "")}
        for c in t.tool_calls
    ])
    if t.guardrails:
        st.markdown("**Guardrails applied**")
        for g in t.guardrails:
            st.markdown(f"- {g}")
    st.caption(f"Run {t.run_id} · trace saved to `{result.trace_path or 'not saved'}`")

# ---------- assumptions, limitations, next steps ----------
for heading, items in (("Assumptions", r.assumptions), ("Limitations", r.limitations), ("Next steps", r.next_steps)):
    if items:
        st.subheader(heading)
        for item in items:
            st.markdown(f"- {item}")
