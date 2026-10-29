# Research Agent

A production-oriented AI research agent: takes a research question, plans,
searches the web (Tavily), scrapes pages, filters weak sources, and returns a
structured, source-cited answer with confidence, assumptions, limitations and
next steps.

> Status: setup phase. Architecture, reliability notes and evaluation results
> will be documented here as the phases are built.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env            # then add your API keys
python main.py "your research question"
```
