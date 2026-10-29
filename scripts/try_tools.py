"""Manual smoke test: run web_search on a query, then scrape the top hits.

Usage:  python scripts/try_tools.py "your query" [--scrape 2]
Needs TAVILY_API_KEY in .env. Makes real network calls.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `agent` importable

from dotenv import load_dotenv  # noqa: E402

from agent.tools.scraper import scrape_page  # noqa: E402
from agent.tools.search import web_search  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Try the search and scrape tools")
    parser.add_argument("query")
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--scrape", type=int, default=2, help="How many top results to scrape")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252: curly quotes print as �
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # our tools already log one line per call

    search = web_search(args.query, max_results=args.max_results)
    print(f"\n=== SEARCH  ok={search.ok}  error={search.error}")
    for i, item in enumerate(search.items, 1):
        print(f"[{i}] score={item.score:.2f}  {item.title}\n    {item.url}\n    {item.snippet[:160]}")

    for item in search.items[: args.scrape]:
        page = scrape_page(item.url)
        print(f"\n=== PAGE  ok={page.ok}  error={page.error}  url={page.url}")
        if page.ok:
            print(f"title: {page.title}")
            print(f"chars: {len(page.text)}  truncated={page.truncated}  suspicious={page.suspicious} {page.injection_matches}")
            print(page.text[:500] + ("…" if len(page.text) > 500 else ""))

    return 0 if search.ok else 1


if __name__ == "__main__":
    sys.exit(main())
