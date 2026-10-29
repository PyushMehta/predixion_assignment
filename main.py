"""CLI entry point for the research agent.

Setup phase: only parses arguments and loads configuration. The agent
pipeline is wired in a later phase.
"""

import argparse
import logging
import sys

from dotenv import load_dotenv


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI research agent")
    parser.add_argument("question", help="Research question to answer")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("main")
    log.info("received question: %r", args.question)
    log.info("agent pipeline not implemented yet (setup phase)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
