"""Prompt-injection phrase detection for untrusted web text.

Used on scraped pages and on search titles/snippets. It only flags text;
it never removes it. Flagged text is still data, never instructions, and
later stages decide how much to trust a flagged source.

This is a cheap keyword heuristic, not a security boundary: it catches
common phrasings and misses paraphrases.
"""

import re

INJECTION_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|directions|rules)",
        r"disregard (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|directions|rules)",
        r"forget (all |any )?(your|the|previous|prior) (instructions|prompts|rules)",
        r"you are now (a|an|in) ",
        r"new instructions\s*:",
        r"(reveal|print|show) (your|the) system prompt",
        r"do not (tell|inform) the user",
        r"(as an ai|ai assistant)[, ]+(you must|you should|always)",
    ]
]


def find_injection_phrases(text: str) -> list[str]:
    """Return the distinct prompt-injection-like phrases found in `text`."""
    found = []
    for pattern in INJECTION_PATTERNS:
        match = pattern.search(text)
        if match and match.group(0) not in found:
            found.append(match.group(0))
    return found
