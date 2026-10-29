"""Source quality: a simple, visible prior on where a URL comes from.

Used by the filter (and scrape-target choice) to adjust ranking, never to
block a source outright, and by the eval harness to count source types, so
the agent and its evaluation use the same definitions.

Categories:
- low: social media, video, forums
- authoritative: government, academic, international bodies, research
  publishers, known data organisations
- docs_code: official documentation and code hosting (GitHub etc.), favoured
  only for technical questions
- other: everything else (news, vendors, blogs, consultancies)
"""

import re
from urllib.parse import urlsplit

LOW_QUALITY_DOMAINS = {
    "facebook.com", "instagram.com", "tiktok.com", "twitter.com", "x.com", "threads.net",
    "linkedin.com", "pinterest.com", "youtube.com", "youtu.be", "vimeo.com",
    "reddit.com", "quora.com", "stackexchange.com", "answers.com",
}
AUTHORITATIVE_DOMAINS = {
    # data and international organisations
    "ourworldindata.org", "who.int", "un.org", "oecd.org", "worldbank.org", "imf.org", "iea.org",
    "irena.org", "ipcc.ch", "pewresearch.org", "rand.org", "brookings.edu", "nber.org",
    "nasscom.in", "rbi.org.in",
    # research publishers and preprint servers
    "nature.com", "science.org", "sciencedirect.com", "springer.com", "wiley.com", "arxiv.org",
    "acm.org", "ieee.org", "plos.org", "pnas.org", "cell.com", "thelancet.com", "bmj.com",
    "semanticscholar.org", "openreview.net", "aclanthology.org", "ssrn.com",
}
AUTHORITATIVE_SUFFIXES = (
    ".gov", ".edu", ".int", ".mil", ".gov.uk", ".ac.uk", ".gov.in", ".nic.in", ".ac.in", ".edu.au", ".gov.au",
)
CODE_DOMAINS = {
    "github.com", "gitlab.com", "huggingface.co", "pypi.org", "npmjs.com", "readthedocs.io",
    "readthedocs.org", "crates.io", "pkg.go.dev",
}
DOCS_PATH = re.compile(r"^/(docs|documentation|developers?|reference|api)(/|$)", re.I)
DOCS_HOST_PREFIXES = ("docs.", "developer.", "developers.")

ADJUSTMENT = {"low": -0.30, "authoritative": 0.15, "docs_code": 0.15, "other": 0.0}

TECHNICAL_TERMS = set("""
api sdk database databases db vector rag llm llms embedding embeddings framework frameworks library
libraries open-source opensource python javascript typescript java rust go kubernetes docker cloud
architecture architectures agent agents multi-agent model models inference latency benchmark benchmarks
index indexing query queries search retrieval memory deployment devops microservice microservices
""".split())


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def _matches(domain: str, names: set[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in names)


def classify_url(url: str) -> str:
    """Return "low", "authoritative", "docs_code" or "other" for a URL."""
    parts = urlsplit(url)
    domain = (parts.hostname or "").removeprefix("www.")
    if _matches(domain, LOW_QUALITY_DOMAINS):
        return "low"
    if _matches(domain, AUTHORITATIVE_DOMAINS) or domain.endswith(AUTHORITATIVE_SUFFIXES):
        return "authoritative"
    if _matches(domain, CODE_DOMAINS) or domain.startswith(DOCS_HOST_PREFIXES) or DOCS_PATH.match(parts.path or ""):
        return "docs_code"
    return "other"


_TWO_PART_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.in", "org.in", "ac.in", "gov.in", "com.au", "org.au",
    "edu.au", "gov.au", "co.jp", "com.br", "co.nz", "com.sg", "co.za",
}
_OWNER_HOSTS = ("github.com", "gitlab.com", "huggingface.co")


def site_name(url: str) -> str:
    """The organisation a URL belongs to, as one lower-case word.

    The registrable domain's name ("blog.zilliz.com" -> "zilliz"), or the
    repository owner on code hosts ("github.com/qdrant/qdrant" -> "qdrant").
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if host in _OWNER_HOSTS:
        owner = parts.path.strip("/").split("/")[0]
        if owner:
            return owner.lower().replace("-", "")
    if host.endswith(".readthedocs.io"):
        return host.split(".")[0].replace("-", "")
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_PART_SUFFIXES:
        return labels[-3].replace("-", "")
    return (labels[-2] if len(labels) >= 2 else host).replace("-", "")


def named_entities(text: str) -> set[str]:
    """Proper names in `text`, lower-cased: words written with capitals that never appear in lower case.

    "Milvus", "greytHR" and "Zilliz" qualify; "Wind" does not if "wind" also
    appears, which keeps ordinary words from matching sites like medium.com.
    """
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]*[A-Za-z0-9]", text)
    lowercase = {w for w in words if w.islower()}
    return {w.lower().replace("-", "") for w in words if not w.islower() and w.lower() not in lowercase and len(w) >= 3}


def is_vendor_source(url: str, entities: set[str]) -> bool:
    """True if the source is the site of something the question or answer names (its vendor).

    Authoritative sources (government, academic, research publishers) never count as vendors.
    """
    if classify_url(url) == "authoritative":
        return False
    return site_name(url) in entities


def is_technical(question: str) -> bool:
    words = set(re.findall(r"[a-z][a-z0-9-]*", question.lower()))
    return bool(words & TECHNICAL_TERMS)


def quality_adjustment(url: str, question: str = "") -> float:
    """Ranking bonus/penalty for a source. Docs/code only get a bonus for technical questions."""
    category = classify_url(url)
    if category == "docs_code" and not is_technical(question):
        return 0.0
    return ADJUSTMENT[category]
