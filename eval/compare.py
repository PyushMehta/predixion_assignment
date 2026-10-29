"""Compare two or more eval result files side by side (e.g. before / after / final).

    python eval/compare.py BEFORE.json AFTER.json [FINAL.json ...] [--labels before,after,final]
                           [--notes eval/results/comparison_notes.md] [-o eval/results/comparison.md]

All runs are re-scored with the CURRENT definitions (agent/quality.py buckets,
over budget = total run time above the run deadline), so a change in a metric
reflects the agent, not a change in how it is measured. Aggregate metrics use
only the queries present in every run; queries added later get their own table.
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
DEFAULT_LABELS = ["before", "after", "final", "run4", "run5"]


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
        "pages_read": c.get("pages_read"),
        "needs_full_pages": c.get("needs_full_pages"),
        "baseline_conf": baseline.get("confidence") if baseline.get("ok") else None,
        "review": result.get("manual_review") or {},
        "old_quality": (len(c.get("quality_low", [])), len(c.get("quality_authoritative", [])), len(c.get("quality_other", []))),
    }


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    lat = [v["latency"] for v in rows]

    def frac(key):
        return f"{sum(1 for v in rows if v[key])}/{n}"

    return {
        "crash-free": frac("crash_free"),
        "schema valid": frac("schema"),
        "citations valid": frac("citations"),
        "confidence rules held": frac("rules"),
        "insufficient evidence": f"{sum(1 for v in rows if v['sources'] == 0)}/{n}",
        f"over the {RUN_DEADLINE_S:.0f}s budget": frac("over_budget"),
        "any fallback fired": f"{sum(1 for v in rows if v['fallbacks'])}/{n}",
        "latency median": f"{statistics.median(lat):.1f}s",
        "latency max": f"{max(lat):.1f}s",
        "latency total": f"{sum(lat):.0f}s",
        "confidence high / medium / low": " / ".join(
            str(sum(1 for v in rows if v["confidence"] == lvl)) for lvl in ("high", "medium", "low")),
        "sources total": str(sum(v["sources"] for v in rows)),
        "domains low / authoritative / docs+code / other": " / ".join(
            str(sum(v["quality"][b] for v in rows)) for b in BUCKETS),
        "tokens (in+out)": str(sum(v["tokens"] for v in rows)),
    }


def cell(x) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ")


def quality_str(r: dict) -> str:
    return " / ".join(str(r["quality"][b]) for b in BUCKETS)


def build(paths: list[Path], labels: list[str]) -> str:
    runs = [{r["query"]["id"]: rescore(r) for r in json.loads(p.read_text(encoding="utf-8"))["results"]} for p in paths]
    order = list(runs[-1])  # query order of the newest run
    common = [i for i in order if all(i in run for run in runs)]
    extra = [i for i in order if i not in common]
    aggs = [aggregate([run[i] for i in common]) for run in runs]
    first = runs[0]
    old_auth = sum(first[i]["old_quality"][1] for i in common)
    old_total = sum(sum(first[i]["old_quality"]) for i in common)
    over_key = f"over the {RUN_DEADLINE_S:.0f}s budget"

    head = "| metric | " + " | ".join(labels) + " |"
    sep = "|---|" + "---|" * len(labels)
    lines = [
        "# Evaluation comparison: " + " / ".join(labels),
        "",
        *[f"- **{label}:** `{p.as_posix()}`" for label, p in zip(labels, paths)],
        "",
        "All runs are re-scored with the same, current definitions: source-quality buckets from "
        f"`agent/quality.py` and \"over budget\" = total run time above {RUN_DEADLINE_S:.0f}s. "
        f"Aggregate metrics cover the {len(common)} queries present in every run. The Phase 4 harness used a "
        f"cruder classifier (no docs/code bucket) and measured `deadline_hit` for the gathering stage only: it "
        f"showed {old_auth} authoritative out of {old_total} domains for the {labels[0]} run, and 0 deadline "
        f"hits although {aggs[0][over_key].split('/')[0]} run(s) took over {RUN_DEADLINE_S:.0f}s in total.",
        "",
        "## Per metric",
        "",
        head,
        sep,
    ]
    lines += ["| " + k + " | " + " | ".join(a[k] for a in aggs) + " |" for k in aggs[0]]

    lines += [
        "",
        "## Per query (automatic)",
        "",
        "Values are " + " → ".join(labels) + ".",
        "",
        "| id | confidence | sources | low / auth / docs / other | latency | fallbacks | baseline conf. |",
        "|---|---|---|---|---|---|---|",
    ]
    for i in common:
        rs = [run[i] for run in runs]
        lines.append("| " + " | ".join(cell(x) for x in [
            i,
            " → ".join(str(r["confidence"]) for r in rs),
            " → ".join(str(r["sources"]) for r in rs),
            " → ".join(quality_str(r) for r in rs),
            " → ".join(f"{r['latency']}s" for r in rs),
            " → ".join(", ".join(r["fallbacks"]) or "none" for r in rs),
            " → ".join(str(r["baseline_conf"]) for r in rs),
        ]) + " |")

    if extra:
        lines += [
            "",
            f"## Queries not in every run (values from the {labels[-1]} run)",
            "",
            "| id | confidence | sources | pages read | needs full pages | low / auth / docs / other | latency | fallbacks | baseline conf. |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for i in extra:
            r = runs[-1][i]
            lines.append("| " + " | ".join(cell(x) for x in [
                i, r["confidence"], r["sources"], r["pages_read"], r["needs_full_pages"], quality_str(r),
                f"{r['latency']}s", ", ".join(r["fallbacks"]) or "none", r["baseline_conf"],
            ]) + " |")

    lines += [
        "",
        "## Per query (manual review)",
        "",
        "| id | correct? (" + " → ".join(labels) + ") | well-supported? | "
        + " | ".join(f"what broke ({label})" for label in labels) + " |",
        "|---|---|---|" + "---|" * len(labels),
    ]
    for i in common + extra:
        revs = [run[i]["review"] if i in run else {} for run in runs]
        lines.append("| " + " | ".join(cell(x) for x in [
            i,
            " → ".join(r.get("correct") or "?" for r in revs),
            " → ".join(r.get("well_supported") or "?" for r in revs),
            *[r.get("what_broke") or "" for r in revs],
        ]) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare eval result files")
    parser.add_argument("runs", type=Path, nargs="+", help="Result .json files, oldest first")
    parser.add_argument("--labels", help="Comma-separated column labels (default: before,after,final,...)")
    parser.add_argument("-o", "--output", type=Path, default=Path(__file__).with_name("results") / "comparison.md")
    parser.add_argument("--notes", type=Path, help="Hand-written Markdown appended after the generated tables")
    args = parser.parse_args(argv)
    if len(args.runs) < 2:
        parser.error("give at least two result files")
    labels = args.labels.split(",") if args.labels else DEFAULT_LABELS[: len(args.runs)]
    if len(labels) != len(args.runs):
        parser.error("--labels must have one label per result file")
    text = build(args.runs, labels)
    if args.notes:
        text += "\n" + args.notes.read_text(encoding="utf-8")
    args.output.write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    print(f"Saved {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
