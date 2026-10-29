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
