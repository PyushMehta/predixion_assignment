"""Before/after comparison of two eval result files.

    python eval/compare.py eval/results/BEFORE.json eval/results/AFTER.json [-o eval/results/comparison.md]

Both runs are re-scored with the CURRENT definitions (agent/quality.py buckets,
deadline_hit = total run time over the run budget), so a change in a metric
reflects the agent, not a change in how it is measured. Where the old harness
measured something differently, the old figure is shown too.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `agent` importable

from agent.orchestrator import RUN_DEADLINE_S  # noqa: E402
from agent.quality import classify_url, domain_of  # noqa: E402

BUCKETS = ("low", "authoritative", "docs_code", "other")


def rescore(result: dict) -> dict:
    """Per-query metrics recomputed from the saved response and checks."""
    c, resp = result["checks"], result.get("response") or {}
    quality = {b: 0 for b in BUCKETS}
    seen = set()
    for s in resp.get("sources", []):
        domain = domain_of(s["url"])
        if domain not in seen:
            seen.add(domain)
            quality[classify_url(s["url"])] += 1
    baseline = result.get("baseline") or {}
    return {
        "crash_free": not c["crashed"],
        "schema": c["schema_valid"],
        "citations": c["citations_valid"],
        "rules": c["confidence_rules_ok"],
        "confidence": c["confidence"],
        "sources": c["n_sources"],
        "quality": quality,
        "latency": c["latency_s"],
        "over_budget": c["latency_s"] > RUN_DEADLINE_S,
        "fallbacks": c["fallbacks"],
        "tokens": c["tokens_in"] + c["tokens_out"],
        "baseline_conf": baseline.get("confidence") if baseline.get("ok") else None,
        "review": result.get("manual_review") or {},
        "old_quality": (len(c.get("quality_low", [])), len(c.get("quality_authoritative", [])), len(c.get("quality_other", []))),
    }


def aggregate(rows: dict) -> dict:
    vals = list(rows.values())
    n = len(vals)
    lat = [v["latency"] for v in vals]

    def frac(key):
        return f"{sum(1 for v in vals if v[key])}/{n}"

    return {
        "crash-free": frac("crash_free"),
        "schema valid": frac("schema"),
        "citations valid": frac("citations"),
        "confidence rules held": frac("rules"),
        "insufficient evidence": f"{sum(1 for v in vals if v['sources'] == 0)}/{n}",
        f"over the {RUN_DEADLINE_S:.0f}s budget": frac("over_budget"),
        "any fallback fired": f"{sum(1 for v in vals if v['fallbacks'])}/{n}",
        "latency median": f"{statistics.median(lat):.1f}s",
        "latency max": f"{max(lat):.1f}s",
        "latency total": f"{sum(lat):.0f}s",
        "confidence high / medium / low": " / ".join(
            str(sum(1 for v in vals if v["confidence"] == lvl)) for lvl in ("high", "medium", "low")),
        "sources total": str(sum(v["sources"] for v in vals)),
        "domains low / authoritative / docs+code / other": " / ".join(
            str(sum(v["quality"][b] for v in vals)) for b in BUCKETS),
        "tokens (in+out)": str(sum(v["tokens"] for v in vals)),
    }


def cell(x) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ")


def build(before_path: Path, after_path: Path) -> str:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    b_rows = {r["query"]["id"]: rescore(r) for r in before["results"]}
    a_rows = {r["query"]["id"]: rescore(r) for r in after["results"]}
    ids = [i for i in a_rows if i in b_rows]
    b_agg, a_agg = aggregate({i: b_rows[i] for i in ids}), aggregate({i: a_rows[i] for i in ids})
    old_auth = sum(b_rows[i]["old_quality"][1] for i in ids)
    old_total = sum(sum(b_rows[i]["old_quality"]) for i in ids)

    lines = [
        "# Before / after: Phase 5 evaluation-driven fixes",
        "",
        f"- **Before:** `{before_path.as_posix()}` (combined baseline, before any fixes)",
        f"- **After:** `{after_path.as_posix()}` (after Phase 5 fixes)",
        "",
        "Both runs are re-scored with the same, current definitions: source-quality buckets from "
        "`agent/quality.py` and \"over budget\" = total run time above "
        f"{RUN_DEADLINE_S:.0f}s. The Phase 4 harness used a cruder classifier (no docs/code bucket) and "
        f"reported `deadline_hit` for the gathering stage only: it showed {old_auth} authoritative out of "
        f"{old_total} domains for the before run, and 0/{len(ids)} deadline hits although "
        f"{b_agg[f'over the {RUN_DEADLINE_S:.0f}s budget'].split('/')[0]} run(s) took over {RUN_DEADLINE_S:.0f}s "
        "in total.",
        "",
        "## Per metric",
        "",
        "| metric | before | after |",
        "|---|---|---|",
    ]
    lines += [f"| {k} | {b_agg[k]} | {a_agg[k]} |" for k in b_agg]

    lines += [
        "",
        "## Per query (automatic)",
        "",
        "| id | confidence | sources | low / auth / docs / other | latency | fallbacks | baseline conf. |",
        "|---|---|---|---|---|---|---|",
    ]
    for i in ids:
        b, a = b_rows[i], a_rows[i]

        def arrow(key, fmt=str):
            return f"{fmt(b[key])} → {fmt(a[key])}"

        q = lambda r: " / ".join(str(r["quality"][x]) for x in BUCKETS)  # noqa: E731
        fb = lambda r: ", ".join(r["fallbacks"]) or "none"  # noqa: E731
        lines.append("| " + " | ".join(cell(x) for x in [
            i, arrow("confidence"), arrow("sources"), f"{q(b)} → {q(a)}",
            f"{b['latency']}s → {a['latency']}s", f"{fb(b)} → {fb(a)}",
            f"{b['baseline_conf']} → {a['baseline_conf']}",
        ]) + " |")

    lines += [
        "",
        "## Per query (manual review)",
        "",
        "| id | correct? before → after | well-supported? before → after | what broke (before) | what broke (after) |",
        "|---|---|---|---|---|",
    ]
    for i in ids:
        br, ar = b_rows[i]["review"], a_rows[i]["review"]
        lines.append("| " + " | ".join(cell(x) for x in [
            i,
            f"{br.get('correct') or '?'} → {ar.get('correct') or '?'}",
            f"{br.get('well_supported') or '?'} → {ar.get('well_supported') or '?'}",
            br.get("what_broke") or "", ar.get("what_broke") or "",
        ]) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare two eval result files")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path(__file__).with_name("results") / "comparison.md")
    parser.add_argument("--notes", type=Path, help="Hand-written Markdown appended after the generated tables")
    args = parser.parse_args(argv)
    text = build(args.before, args.after)
    if args.notes:
        text += "\n" + args.notes.read_text(encoding="utf-8")
    args.output.write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    print(f"Saved {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
