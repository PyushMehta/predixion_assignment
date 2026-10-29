"""Small deterministic text helpers shared by the synthesizer and the eval harness."""

import re

_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def trim_at_sentence(text: str, limit: int) -> str:
    """Shorten `text` to at most `limit` chars, ending at a sentence boundary if possible.

    Falls back to the last word boundary (plus "…") when no sentence ends in
    the second half of the allowed length.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    if ends and ends[-1] >= limit // 2:
        return window[: ends[-1]].strip()
    cut = window[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"
