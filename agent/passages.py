"""Passage selection: keep the parts of a page that are relevant to the question.

Pages used to be cut to their first few thousand characters, which can lose
the evidence entirely (Our World in Data's energy-safety page gives its
per-TWh figures well after the first 4,000 chars). Instead, the page is split
into ~600-char chunks, each chunk is scored against the question and
sub-questions with BM25 (no LLM), and the best chunks are kept up to a
character budget, in their original order.
"""

import math
import re

CHUNK_CHARS = 600
BM25_K1 = 1.5
BM25_B = 0.75
GAP_MARKER = "\n[…]\n"  # shows the model that text was skipped between chunks

STOPWORDS = set("""
a an and are as at be been but by can could did do does for from had has have how i if in into is it its
me more most my no not of on or our should so than that the their them then there these they this those
to too was we were what when where which while who whom why will with would you your about across after
also any between both each few find give list many much other over same some such through under up very
compare research summarize summarise approaches different using use
""".split())


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens without stopwords, with a light plural strip ("deaths" -> "death")."""
    tokens = []
    for word in re.findall(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*", text.lower()):
        if word in STOPWORDS or len(word) < 2:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        tokens.append(word)
    return tokens


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split on paragraphs, merging short ones and splitting long ones at sentences."""
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            pieces.append(para)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            while len(sentence) > size:  # a single huge "sentence" (tables, lists)
                pieces.append(sentence[:size])
                sentence = sentence[size:]
            if sentence:
                pieces.append(sentence)

    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > size:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def bm25_scores(chunks: list[str], query: str) -> list[float]:
    terms = set(tokenize(query))
    docs = [tokenize(c) for c in chunks]
    if not terms or not docs:
        return [0.0] * len(chunks)
    n = len(docs)
    avg_len = sum(len(d) for d in docs) / n or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in terms}
    scores = []
    for doc in docs:
        score = 0.0
        for t in terms:
            tf = doc.count(t)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * len(doc) / avg_len))
        scores.append(score)
    return scores


def select_passages(text: str, query: str, max_chars: int) -> str:
    """Return the most query-relevant parts of `text`, at most ~`max_chars`, in original order.

    Short texts are returned unchanged. If nothing matches the query, the start
    of the page is used (the old behaviour) rather than an arbitrary chunk.
    """
    if len(text) <= max_chars:
        return text
    chunks = chunk_text(text)
    scores = bm25_scores(chunks, query)
    if not any(scores):
        return text[:max_chars]

    ranked = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
    chosen, used = [], 0

    def take(i: int) -> None:
        nonlocal used
        cost = len(chunks[i]) + len(GAP_MARKER)
        if i not in chosen and used + cost <= max_chars:
            chosen.append(i)
            used += cost

    for i in ranked:  # 1) every matching chunk that fits, best first
        if scores[i] <= 0:
            break
        take(i)
    if not chosen:  # best chunk alone is over budget
        return chunks[ranked[0]][:max_chars]
    # 2) fill the rest of the budget with context nearest the matches, so a single
    #    short matching chunk doesn't leave the source too thin to use
    for i in sorted(range(len(chunks)), key=lambda j: (min(abs(j - c) for c in chosen), j)):
        take(i)

    out, prev = [], None
    for i in sorted(chosen):
        if prev is not None and i != prev + 1:
            out.append(GAP_MARKER.strip())
        out.append(chunks[i])
        prev = i
    return "\n".join(out)
