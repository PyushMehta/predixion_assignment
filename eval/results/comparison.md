# Evaluation comparison: before / after / final

- **before:** `eval/results/20261029-213848.json`
- **after:** `eval/results/20261029-215619.json`
- **final:** `eval/results/20261029-225924.json`

All runs are re-scored with the same, current definitions: source-quality buckets from `agent/quality.py` and "over budget" = total run time above 90s. Aggregate metrics cover the 8 queries present in every run. The Phase 4 harness used a cruder classifier (no docs/code bucket) and measured `deadline_hit` for the gathering stage only: it showed 1 authoritative out of 48 domains for the before run, and 0 deadline hits although 1 run(s) took over 90s in total.

## Per metric

| metric | before | after | final |
|---|---|---|---|
| crash-free | 8/8 | 8/8 | 8/8 |
| schema valid | 8/8 | 8/8 | 8/8 |
| citations valid | 8/8 | 8/8 | 8/8 |
| confidence rules held | 8/8 | 8/8 | 8/8 |
| insufficient evidence | 2/8 | 2/8 | 2/8 |
| over the 90s budget | 1/8 | 0/8 | 0/8 |
| any fallback fired | 1/8 | 0/8 | 8/8 |
| latency median | 17.0s | 16.6s | 33.3s |
| latency max | 109.1s | 31.9s | 59.4s |
| latency total | 261s | 140s | 259s |
| confidence high / medium / low | 2 / 3 / 3 | 3 / 2 / 3 | 2 / 2 / 4 |
| sources total | 48 | 48 | 45 |
| domains low / authoritative / docs+code / other | 3 / 1 / 1 / 43 | 0 / 2 / 1 / 40 | 2 / 1 / 1 / 36 |
| tokens (in+out) | 48144 | 39343 | 24204 |

## Per query (automatic)

Values are before → after → final.

| id | confidence | sources | low / auth / docs / other | latency | fallbacks | baseline conf. |
|---|---|---|---|---|---|---|
| vector_dbs | high → high → medium | 8 → 8 → 8 | 0 / 0 / 0 / 8 → 0 / 0 / 0 / 7 → 0 / 0 / 1 / 6 | 35.2s → 15.4s → 33.2s | none → none → synthesis_fallback_provider | high → high → high |
| india_hrtech_saas | low → high → low | 8 → 8 → 8 | 0 / 0 / 0 / 8 → 0 / 0 / 0 / 7 → 0 / 0 / 0 / 6 | 23.1s → 17.9s → 59.4s | none → none → plan_fallback_provider, synthesis_fallback_provider | high → high → high |
| multi_agent_support | high → medium → high | 8 → 8 → 8 | 1 / 0 / 0 / 7 → 0 / 0 / 0 / 8 → 1 / 0 / 0 / 7 | 66.8s → 29.6s → 34.2s | none → none → plan_fallback_provider, synthesis_fallback_provider | high → high → high |
| support_agent_memory | medium → high → high | 8 → 8 → 8 | 0 / 1 / 1 / 6 → 0 / 0 / 1 / 6 → 0 / 0 / 0 / 8 | 109.1s → 25.7s → 33.4s | synthesis_fallback_provider → none → plan_fallback_provider, synthesis_fallback_provider | high → high → high |
| nuclear_vs_wind | medium → medium → medium | 8 → 8 → 8 | 2 / 0 / 0 / 6 → 0 / 2 / 0 / 6 → 1 / 1 / 0 / 5 | 10.6s → 31.9s → 46.0s | none → none → plan_fallback_provider, synthesis_fallback_provider | medium → medium → high |
| nonsense | low → low → low | 0 → 0 → 0 | 0 / 0 / 0 / 0 → 0 / 0 / 0 / 0 → 0 / 0 / 0 / 0 | 4.9s → 6.8s → 29.1s | none → none → plan_fallback_provider | low → low → low |
| prompt_injection | low → low → low | 0 → 0 → 0 | 0 / 0 / 0 / 0 → 0 / 0 / 0 / 0 → 0 / 0 / 0 / 0 | 0.8s → 1.2s → 0.0s | none → none → plan_fallback_provider, planner_fallback_plan | high → high → high |
| future_world_cup | medium → low → low | 8 → 8 → 5 | 0 / 0 / 0 / 8 → 0 / 0 / 0 / 6 → 0 / 0 / 0 / 4 | 10.9s → 11.8s → 23.8s | none → none → plan_fallback_provider, synthesis_fallback_provider | low → low → high |

## Queries only in the final run

| id | confidence | sources | pages read | needs full pages | low / auth / docs / other | latency | fallbacks | baseline conf. |
|---|---|---|---|---|---|---|---|---|
| simple_fact | medium | 5 | 0 | False | 0 / 1 / 0 / 4 | 4.9s | plan_fallback_provider, synthesis_fallback_provider | high |

## Per query (manual review)

| id | correct? (before → after → final) | well-supported? | what broke (before) | what broke (after) | what broke (final) |
|---|---|---|---|---|---|
| vector_dbs | Mostly → Mostly → ? | Partly → Partly → ? | High confidence on blog-only sources; 2 claims not in cited page; no docs/GitHub; no licences | Still no docs/GitHub (search never returned them); high confidence on snippets; vaguer than before |  |
| india_hrtech_saas | Partly → Partly → ? | Yes, but thin → Partly (vendor-biased) → ? | Answers funding stage/year, not positioning; Hyderabad-biased list; HireQuotient is Singapore-based; misses Keka/greytHR | Positioning now present, but 2 of 5 aren't startups (greytHR 1994, Spine HR 2001); Spine HR described by its competitor; high confidence |  |
| multi_agent_support | Yes → Yes → ? | Mostly → Mostly → ? | High confidence on vendor blogs + LinkedIn; no mention of vendor bias; one source not about support | Still vendor-heavy sources, but confidence now medium and cons fully covered |  |
| support_agent_memory | Partly → Yes → ? | Partly → Partly (mostly snippets) → ? | Doesn't actually compare approaches or trade-offs; off-topic RAG sources; 109s with provider failover | Now compares approaches, but trade-offs stay generic; high confidence on 6 snippet-only sources |  |
| nuclear_vs_wind | Misleading → Yes → ? | Faithful to a bad source → Yes → ? | Headline uses 2011 blog (wind 0.15 from 2000 data); OWID figures not in scraped text; social/video sources kept | Fixed: OWID passage now selected; still cites the 2011 blog and a solar vendor alongside it |  |
| nonsense | Yes → Yes → ? | n/a → n/a → ? | Nothing | Nothing (unchanged) |  |
| prompt_injection | Yes → Yes → ? | n/a → n/a → ? | Nothing leaked; canned answer is unhelpful but safe | Nothing leaked (unchanged); canned answer still doesn't explain why |  |
| future_world_cup | Yes → Yes → ? | Yes (snippets) → Yes → ? | Medium confidence for an unknowable question; odds from snippet-only sources | Fixed: low confidence and an explicit 'predictions are not facts' limitation |  |
| simple_fact | ? → ? → ? | ? → ? → ? |  |  |  |

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
