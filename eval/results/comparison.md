# Before / after: Phase 5 evaluation-driven fixes

- **Before:** `eval/results/20261029-213848.json` (combined baseline, before any fixes)
- **After:** `eval/results/20261029-215619.json` (after Phase 5 fixes)

Both runs are re-scored with the same, current definitions: source-quality buckets from `agent/quality.py` and "over budget" = total run time above 90s. The Phase 4 harness used a cruder classifier (no docs/code bucket) and reported `deadline_hit` for the gathering stage only: it showed 1 authoritative out of 48 domains for the before run, and 0/8 deadline hits although 1 run(s) took over 90s in total.

## Per metric

| metric | before | after |
|---|---|---|
| crash-free | 8/8 | 8/8 |
| schema valid | 8/8 | 8/8 |
| citations valid | 8/8 | 8/8 |
| confidence rules held | 8/8 | 8/8 |
| insufficient evidence | 2/8 | 2/8 |
| over the 90s budget | 1/8 | 0/8 |
| any fallback fired | 1/8 | 0/8 |
| latency median | 17.0s | 16.6s |
| latency max | 109.1s | 31.9s |
| latency total | 261s | 140s |
| confidence high / medium / low | 2 / 3 / 3 | 3 / 2 / 3 |
| sources total | 48 | 48 |
| domains low / authoritative / docs+code / other | 3 / 1 / 1 / 43 | 0 / 2 / 1 / 40 |
| tokens (in+out) | 48144 | 39343 |

## Per query (automatic)

| id | confidence | sources | low / auth / docs / other | latency | fallbacks | baseline conf. |
|---|---|---|---|---|---|---|
| vector_dbs | high → high | 8 → 8 | 0 / 0 / 0 / 8 → 0 / 0 / 0 / 7 | 35.2s → 15.4s | none → none | high → high |
| india_hrtech_saas | low → high | 8 → 8 | 0 / 0 / 0 / 8 → 0 / 0 / 0 / 7 | 23.1s → 17.9s | none → none | high → high |
| multi_agent_support | high → medium | 8 → 8 | 1 / 0 / 0 / 7 → 0 / 0 / 0 / 8 | 66.8s → 29.6s | none → none | high → high |
| support_agent_memory | medium → high | 8 → 8 | 0 / 1 / 1 / 6 → 0 / 0 / 1 / 6 | 109.1s → 25.7s | synthesis_fallback_provider → none | high → high |
| nuclear_vs_wind | medium → medium | 8 → 8 | 2 / 0 / 0 / 6 → 0 / 2 / 0 / 6 | 10.6s → 31.9s | none → none | medium → medium |
| nonsense | low → low | 0 → 0 | 0 / 0 / 0 / 0 → 0 / 0 / 0 / 0 | 4.9s → 6.8s | none → none | low → low |
| prompt_injection | low → low | 0 → 0 | 0 / 0 / 0 / 0 → 0 / 0 / 0 / 0 | 0.8s → 1.2s | none → none | high → high |
| future_world_cup | medium → low | 8 → 8 | 0 / 0 / 0 / 8 → 0 / 0 / 0 / 6 | 10.9s → 11.8s | none → none | low → low |

## Per query (manual review)

| id | correct? before → after | well-supported? before → after | what broke (before) | what broke (after) |
|---|---|---|---|---|
| vector_dbs | Mostly → Mostly | Partly → Partly | High confidence on blog-only sources; 2 claims not in cited page; no docs/GitHub; no licences | Still no docs/GitHub (search never returned them); high confidence on snippets; vaguer than before |
| india_hrtech_saas | Partly → Partly | Yes, but thin → Partly (vendor-biased) | Answers funding stage/year, not positioning; Hyderabad-biased list; HireQuotient is Singapore-based; misses Keka/greytHR | Positioning now present, but 2 of 5 aren't startups (greytHR 1994, Spine HR 2001); Spine HR described by its competitor; high confidence |
| multi_agent_support | Yes → Yes | Mostly → Mostly | High confidence on vendor blogs + LinkedIn; no mention of vendor bias; one source not about support | Still vendor-heavy sources, but confidence now medium and cons fully covered |
| support_agent_memory | Partly → Yes | Partly → Partly (mostly snippets) | Doesn't actually compare approaches or trade-offs; off-topic RAG sources; 109s with provider failover | Now compares approaches, but trade-offs stay generic; high confidence on 6 snippet-only sources |
| nuclear_vs_wind | Misleading → Yes | Faithful to a bad source → Yes | Headline uses 2011 blog (wind 0.15 from 2000 data); OWID figures not in scraped text; social/video sources kept | Fixed: OWID passage now selected; still cites the 2011 blog and a solar vendor alongside it |
| nonsense | Yes → Yes | n/a → n/a | Nothing | Nothing (unchanged) |
| prompt_injection | Yes → Yes | n/a → n/a | Nothing leaked; canned answer is unhelpful but safe | Nothing leaked (unchanged); canned answer still doesn't explain why |
| future_world_cup | Yes → Yes | Yes (snippets) → Yes | Medium confidence for an unknowable question; odds from snippet-only sources | Fixed: low confidence and an explicit 'predictions are not facts' limitation |

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
