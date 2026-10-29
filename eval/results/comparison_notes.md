## Reading the comparison (hand-written)

**Fix 1: latency cascade. Fixed.** Max latency 109.1s → 31.9s, total for 8 queries 261s → 140s,
0 runs over the 90s budget, 0 provider failovers. No `short_answer` length repairs were needed: the
1,000-char limit plus code-side trimming removed the trigger. Remaining LLM warnings in the after run
were Groq 429s (per-minute token limit), each retried inside the deadline. Caveat: one run per query,
and free-tier rate limits vary, so the latency gain is directionally solid but not a precise number.

**Fix 2: passage selection. Fixed on the known case.** The nuclear vs wind answer went from
"misleading" (2011 blog, wind 0.15/TWh) to correct ("comparable, nuclear at least as safe"), because
Our World in Data's relevant passage is now selected: 1,871 of 19,995 chars, figures included.

**Fix 3: source quality. Partly.** Social/video/forum domains 3 → 0 and authoritative 1 → 2. But
docs/GitHub stayed at 1 → 1: the boost can only re-rank what search returns, and for the vector-DB
question Tavily returned no official docs or GitHub pages. Honest caveat on the "before" figure: the
Phase 4 harness reported "1 authoritative out of 44", and part of that was the crude classifier
itself. It had no docs/code category, so GitHub and official docs counted as "other". Both runs are
re-scored here with the new classifier, which moves one before-run source (GitHub) into docs/code.
The classifier also still misses some forums (community.openai.com counts as "other").

**Fix 4: unknowable questions. Fixed.** World Cup confidence medium → low, with an explicit
"predictions, odds and forecasts are not facts" limitation.

**Fix 5: next_steps. Fixed.** Every after-run next step is a single action.

**New problem the fixes exposed: confidence calibration.** High-confidence answers went 2 → 3, and
manual review judges 3 of them overconfident (vector_dbs, india_hrtech_saas, support_agent_memory).
India HR-tech jumped low → high on press releases, a competitor's blog and directory snippets. The
"3+ independent sources" rule counts snippet-only sources and doesn't consider vendor bias.
Candidate next fix: allow "high" only with 3+ *full-page*, non-vendor sources, enforced in code.

**Agent vs no-tools baseline, by manual review:**

| query | before | after |
|---|---|---|
| vector_dbs | roughly equal | baseline better (more specific) |
| india_hrtech_saas | baseline better | comparable (agent traceable but vendor-biased) |
| multi_agent_support | about equal | agent better |
| support_agent_memory | baseline clearly better | roughly equal |
| nuclear_vs_wind | neither right | agent better |
| nonsense / prompt_injection | equal | equal |
| future_world_cup | baseline better calibrated | agent better (calibrated and sourced) |

So the agent went from never clearly better than a single LLM call to clearly better on 3 of 8 and
worse on 1. Its main advantage remains traceability: this review could only find the
competitor-written positioning because the agent cited its sources.

## Final run (`20261029-225924`): overconfidence guardrail + tool selection, 9 queries

**Read this column with care: the run is confounded.**
- **Different LLM.** Groq's *daily* token quota ran out after a day of testing,
  so nearly every planner and synthesis call was answered by the Gemini
  fallback (`daily quota exhausted (not retrying)` → failover, as designed).
  "Before" and "after" were mostly Groq. Latency and answer differences in
  this column are largely Groq-vs-Gemini, not the code changes.
- **Unstable local network.** During india_hrtech_saas, searches took ~16s
  (normally ~2s) and all 4 scrapes timed out. During prompt_injection, DNS
  resolution failed for every host (`getaddrinfo failed`), so both LLM
  providers and Tavily failed within milliseconds. The agent degraded
  correctly (fallback plan, then insufficient evidence), but those two rows
  don't reflect normal operation.
- Manual review was **not** done for this run because of the confounds
  (shown as "?"). A clean re-run after the Groq quota resets is needed before
  judging answer quality.

What the run still shows (deterministic code, not provider-dependent):
- **Overconfidence guardrail works:** vector_dbs was answered "high" and
  downgraded to medium. Only 1 cited full-page source was independent: the
  GitHub repos are the evaluated products' own (vendor) and the Medium
  post was snippet-only. The reason is written into `confidence_reason`.
  multi_agent_support and support_agent_memory kept "high" legitimately
  under the rule (3–4 cited, full-page, non-vendor sites). Manual-review
  caveat: those sites are mostly vendors of *adjacent* products and
  consultancies, which a rule based on named products can't detect.
- **Tool selection:** simple_fact (new) → `needs_full_pages=false`, 0 pages,
  "Canberra", medium (snippet cap), 4.9s. world_cup and nonsense also went
  snippets-only, which is reasonable. **But nuclear_vs_wind got
  `needs_full_pages=false` from the Gemini planner**, so Our World in Data
  was not read. That's a wrong decision on a question that needs figures,
  and it shows the decision is model-dependent (Groq set true for comparison
  questions in the live check).
- Failover under quota exhaustion worked on every call: no crashes,
  schema/citations/confidence rules 9/9, 0 runs over the 90s budget (max 59.4s).
