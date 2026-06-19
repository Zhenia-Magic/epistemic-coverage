# Discovery prompt — deep research finds the sources (cold start)

This is the front of the **Ingestion layer**: given only the case question, find candidate
sources spanning the *different* positions, so the knowledge base isn't seeded from one
side. It is the "deep research feature" the cold start relies on. When an API key with web
search is configured (`ANTHROPIC_API_KEY` → Claude's `web_search` tool), discovery is
web-grounded; otherwise the model proposes from its own knowledge and flags uncertainty —
verify before ingesting.

Run:

```
python cli.py discover cases/eggs.kb.json --k 8          # uses your API key (web search)
python cli.py discover cases/eggs.kb.json --k 8 --dry-run # prints the prompt to paste anywhere
```

## Prompt (mirrors `ingest/pipeline.py: DISCOVER_TEMPLATE`)

```
Find up to {K} real, citable sources that bear on this research dispute, spanning the
DIFFERENT positions people hold (not just one side). Prefer primary sources: papers,
datasets, judge decisions, debate transcripts, well-known critical analyses.

QUESTION: {question}

For each source return an object. Output ONLY a JSON array:
[{"title":"...","url":"...","year":2020,"why":"one line: which position/angle it represents"}]
Aim for coverage across positions and evidence types, and flag any you are unsure are real.
```

## Why discovery is deliberately coverage-seeking

The whole thesis is anti-false-balance — but that lives in the *assessment*, not here. At
ingestion we want **maximal honest coverage** of positions and evidence types, because a
blindspot the KB never ingested can't be surfaced. The metrics then weight and audit what
was found. So discovery optimizes for breadth across camps; `independence()` and
`fundingSkew()` defend against that breadth being gamed. Keeping the two concerns separate
is what makes the pipeline both thorough and hard to flood.

## Cold start, end to end

```
python cli.py init eggs "Do eggs raise cardiovascular risk?" --out cases/eggs.kb.json
python cli.py discover cases/eggs.kb.json --k 10            # -> list of links
# for each link the discovery returned:
python cli.py ingest cases/eggs.kb.json <link> --apply       # extract -> delta -> merge -> diff
python cli.py build  cases/eggs.kb.json                       # bake the viewer
```

Each `ingest --apply` prints what the new source changed and appends to the Changes tab.
The run is resumable: the KB on disk is the checkpoint.
