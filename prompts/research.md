# Research prompt — discovery + extraction in one pass

This is the **single-operation cold start** for a human using a browsing chatbot (Claude or
ChatGPT with web search). Where `discover` (find links) and `ingest` (read one link) are two
separate LLM steps, this prompt asks the model to do **both at once**: find real sources across
the positions, read each, and emit one `sources.json` — a JSON array of deltas in the exact shape
`cli.py add` already merges.

Generate it from the current KB so the model reuses your ids and skips sources already present:

```
python cli.py research cases/<id>.kb.json --k 20      # writes out/research-prompt.txt
# paste into a browsing chatbot → save the JSON array it returns → :
python cli.py add cases/<id>.kb.json sources.json --build
```

The template lives in `ingest/pipeline.py` (`RESEARCH_TEMPLATE` / `build_research_prompt`). It
injects, from the live KB: the question, existing **positions / datasets / factors**, the
controlled **evidence / population** vocabularies, and the list of **sources already in the KB**.

## Why the chatbot fetches, not us

The chatbot retrieves each page server-side, so publisher bot-blocks (the 403s that stop our own
`urllib` fetch) aren't in the loop. The cost: the model must actually open and read each URL —
so use a model with web search/browsing, and treat low-`extractionConfidence` rows as leads to
verify, not facts.

## The contract (same "propose, then deterministically resolve" as every other path)

The prompt only *proposes*: each source links to an existing id/label or marks `"NEW:<label>"`,
and tags evidence/population from the shown vocabulary (or `NEW:`). Nothing it returns is trusted
as final — `engine/merge.py` deterministically resolves every link (normalized-string + alias
match), snaps vocab to canonical terms, refuses duplicates (same url, or title+year), and
recomputes the metrics. So a sloppy or adversarial array can add noise but cannot silently fake
independence or smuggle in a duplicate.

## Output schema (one object per source)

```jsonc
[
  {
    "source": {
      "title": "...", "year": 2020, "url": "https://...",
      "position": "pos_id" | "NEW:Some stance",
      "evidence": "Observational", "funding": "independent" | "industry",
      "population": "General adults" | "—", "confidence": "moderate",
      "restsOn": ["ds_id", "NEW:Cohort name"],
      "provenance": {
        "position": { "quote": "...", "extractionConfidence": 0.85 },
        "restsOn":  { "quote": "...", "extractionConfidence": 0.9 }
      }
    },
    "factorWeights": [
      { "factor": "exact factor label", "weight": "high|med|low",
        "quote": "...", "rationale": "..." }
    ]
  }
]
```

A single object is also accepted by `add` (it wraps it in a one-element array).
