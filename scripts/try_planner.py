"""Manual smoke test: make a plan for a question with the real LLM.

Usage:  python scripts/try_planner.py "your question"
Needs LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in .env (fallback LLM_FALLBACK_* optional).
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `agent` importable

from dotenv import load_dotenv  # noqa: E402

from agent.planner import make_plan_detailed  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Try the planner")
    parser.add_argument("question")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2"):  # the OpenAI SDK logs each request; llm.py already logs one line per call
        logging.getLogger(noisy).setLevel(logging.WARNING)

    outcome = make_plan_detailed(args.question)
    llm, plan = outcome.llm, outcome.plan

    print("\n=== LLM")
    print(f"ok={llm.ok}  provider={llm.provider or '-'}  model={llm.model or '-'}  fallback_provider_used={llm.fallback_used}")
    print(f"latency={llm.latency_ms / 1000:.1f}s  tokens in/out={llm.input_tokens}/{llm.output_tokens}")
    if llm.error:
        print(f"error: {llm.error}")

    print(f"\n=== PLAN  {'(deterministic fallback plan)' if outcome.used_fallback_plan else ''}")
    print(f"question: {plan.question}")
    print("sub-questions:")
    for sq in plan.sub_questions:
        print(f"  - {sq}")
    print("steps:")
    for step in plan.steps:
        query = f"  query={step.query!r}" if step.query else ""
        print(f"  {step.step_id}. [{step.tool}] {step.goal}{query}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
