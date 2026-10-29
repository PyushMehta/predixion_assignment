"""CLI entry point for the research agent.

    python main.py "question"          readable answer
    python main.py "question" --json   raw JSON (ResearchResponse)

Every run also saves the answer to outputs/<run_id>.json and .md, and the
full trace to runs/<run_id>.json.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.orchestrator import RunResult, run
from agent.schemas import ResearchResponse

OUTPUTS_DIR = Path("outputs")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI research agent")
    parser.add_argument("question", help="Research question to answer")
    parser.add_argument("--json", action="store_true", help="Print the answer as raw JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.question.strip():
        print("error: question must not be empty", file=sys.stderr)
        return 2

    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # keep stdout clean for --json
    )
    for noisy in ("httpx", "httpx2", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    result = run(args.question)
    as_json = result.response.model_dump_json(indent=2)
    as_text = format_readable(result)
    _save_outputs(result.trace.run_id, as_json, as_text)

    print(as_json if args.json else as_text)
    return 0


def format_readable(result: RunResult) -> str:
    r: ResearchResponse = result.response
    t = result.trace
    lines = [
        f"# {r.question}",
        "",
        "## Answer",
        r.short_answer,
        "",
        f"**Confidence:** {r.confidence.value}: {r.confidence_reason}",
        "",
    ]
    if r.key_findings:
        lines.append("## Key findings")
        lines += [f"- {f.claim} [{', '.join(f.source_ids)}]" for f in r.key_findings]
        lines.append("")
    if r.sources:
        lines.append("## Sources")
        lines += [f"- [{s.id}] {s.title}\n  {s.url}" for s in r.sources]
        lines.append("")
    for heading, items in (("Assumptions", r.assumptions), ("Limitations", r.limitations), ("Next steps", r.next_steps)):
        if items:
            lines.append(f"## {heading}")
            lines += [f"- {item}" for item in items]
            lines.append("")
    llm = ", ".join(f"{c.stage}: {c.provider}/{c.model} {c.latency_ms / 1000:.1f}s" for c in t.llm_calls) or "none"
    lines += [
        "---",
        f"run {t.run_id} | {t.total_ms / 1000:.1f}s | tool calls: {len(t.tool_calls)} | LLM: {llm}",
        f"fallbacks: {', '.join(t.fallbacks) or 'none'} | trace: {result.trace_path or 'not saved'}",
    ]
    return "\n".join(lines)


def _save_outputs(run_id: str, as_json: str, as_text: str) -> None:
    try:
        OUTPUTS_DIR.mkdir(exist_ok=True)
        (OUTPUTS_DIR / f"{run_id}.json").write_text(as_json, encoding="utf-8")
        (OUTPUTS_DIR / f"{run_id}.md").write_text(as_text, encoding="utf-8")
    except OSError as exc:
        logging.getLogger("main").warning("could not save outputs: %s", exc)


if __name__ == "__main__":
    sys.exit(main())
