"""Phase 5 fix 2: passage selection keeps the relevant parts of a page, not its first N chars."""

from agent.passages import chunk_text, select_passages, tokenize

# Shaped like Our World in Data's "safest sources of energy" page: a long
# introduction about fossil fuels and air pollution, with the per-TWh
# figures for nuclear and wind only much further down.
INTRO = "\n\n".join(
    f"Paragraph {i}. Fossil fuels such as coal, oil and gas cause air pollution that harms human health. "
    "Energy production has driven economic growth for two centuries, and the transition to low-carbon "
    "sources is one of the defining challenges of our time. Accidents at mines and refineries add to the toll."
    for i in range(20)
)
FIGURES = (
    "Death rates per terawatt-hour of electricity: nuclear energy causes about 0.03 deaths per TWh, "
    "wind about 0.04 deaths per TWh and solar about 0.02 deaths per TWh. These are far lower than coal at 24.6."
)
OUTRO = "\n\n".join(
    f"Closing note {i}. Readers can download the data and charts under a Creative Commons licence."
    for i in range(5)
)
OWID_LIKE = f"{INTRO}\n\n{FIGURES}\n\n{OUTRO}"
QUERY = (
    "Is nuclear power safer than wind per TWh of electricity? "
    "Deaths per TWh for nuclear power? Deaths per TWh for wind power?"
)


def test_owid_case_figures_survive_where_head_truncation_lost_them():
    assert len(INTRO) > 4000
    assert "0.04" not in OWID_LIKE[:4000]  # the old behaviour never saw the figures
    selected = select_passages(OWID_LIKE, QUERY, max_chars=2000)
    assert len(selected) <= 2000
    assert "nuclear energy causes about 0.03 deaths per TWh" in selected
    assert "wind about 0.04 deaths per TWh" in selected


def test_short_text_is_returned_unchanged():
    assert select_passages("short page", QUERY, 2000) == "short page"


def test_no_matching_terms_falls_back_to_page_start():
    text = "x " * 3000
    assert select_passages(text, "quantum chromodynamics", 500) == text[:500]


def test_selected_chunks_keep_original_order_with_gap_markers():
    text = "\n\n".join(["alpha wind topic. " * 20, "filler text here. " * 100, "beta wind topic. " * 20])
    selected = select_passages(text, "wind topic", max_chars=1400)  # room for two ~600-char chunks
    assert selected.index("alpha") < selected.index("beta")
    assert "[…]" in selected


def test_chunks_respect_size_and_cover_text():
    chunks = chunk_text(OWID_LIKE, size=600)
    assert all(len(c) <= 600 for c in chunks)
    assert "".join(chunks).replace("\n", "").replace(" ", "") == OWID_LIKE.replace("\n", "").replace(" ", "")


def test_tokenize_drops_stopwords_and_plurals():
    assert tokenize("What are the deaths per TWh for wind turbines?") == ["death", "per", "twh", "wind", "turbine"]
