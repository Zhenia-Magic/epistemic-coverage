# Ingestion prompt — one raw source → one KB delta

This is the **Ingestion layer** (Layer 1). It runs in Claude Code (or any Claude call):
given the current KB's entity tables plus one raw source, it emits a `delta.json` that
`src/epistemic.js add` folds into the KB. It is deliberately **single-source**: the cold
start is this prompt looped over discovered sources; an update is one run. Same path.

The prompt never invents IDs. It *proposes* links to existing entities, or marks something
`"NEW:<label>"`; the deterministic merge code decides. Keep extraction conservative —
every field that isn't clearly supported by the text should lower its `extractionConfidence`.

---

## System / instruction

````
You extract ONE source into a structured delta for an epistemic knowledge base.
You are given the case question and the KB's current entity tables. Output ONLY JSON
matching the schema below — no prose.

CASE QUESTION:
{{question}}

EXISTING POSITIONS (reuse an id when the source argues this stance; else "NEW:<label>"):
{{positions as id — label}}

EXISTING DATASETS (the underlying evidence bases; reuse an id when the source's data is
one of these; else "NEW:<label>". Prefer reuse — a shared cohort under a new name
defeats the independence audit):
{{datasets as id — label (aliases)}}

EXISTING FACTORS (dimensions the camps weigh; reference by exact label, or introduce a
new one only if the source genuinely raises a new dimension):
{{factors as label}}

EVIDENCE TYPES in use for this case (pick the closest; "NEW:<label>" only if none fit):
{{evidence vocabulary}}

POPULATIONS in use for this case (pick the closest; "NEW:<label>" for a new group; "—"
if not population-specific):
{{population vocabulary}}

SOURCE TO INGEST:
title, url, and full text / transcript / abstract:
{{source text}}

Rules (these exist to PREVENT entity proliferation — positions/datasets/factors/populations
multiplying via paraphrase as a case grows):
- position: the DIRECTIONAL answer the source gives to the QUESTION (increases / decreases / no
  clear effect / it depends). REUSE an existing position whenever the source argues a stance already
  listed; "NEW:<label>" only for a genuinely distinct directional answer; keep the set small (~3-5).
  LITMUS: if you can't phrase it as an answer to the question, it is NOT a position — it is a FACTOR.
  A mechanism/pathway ("receptor genetics modulate MI susceptibility"), a biomarker ("raises LDL"), a
  subgroup/susceptibility factor (diabetics, a gene variant), or a framing/funding point are all
  FACTORS — assign the closest existing position the source's direction supports (or the neutral
  "no clear effect / it depends" camp) and record the specifics as a factorWeight; never a new camp.
  Don't split one stance into several positions by its CONDITIONS (e.g. two "conditionally safe"
  camps) — use ONE "it depends" position and make each condition a FACTOR. If a new label would
  share its stance word with an existing position, reuse that position.
- restsOn: the underlying PRIMARY evidence — named cohorts / trials / biobanks. A review or
  meta-analysis restsOn the cohorts it POOLS, NOT "the literature" / "studies through <year>" /
  a label describing the paper itself. Same cohort across sources => SAME label. If the cohorts
  aren't named, list the few largest you can identify, else leave restsOn empty.
  restsOn may also reference ANOTHER SOURCE when this source's case IS that source (a commentary on
  one paper; two pieces citing each other). Write "SRC:<existing source id>" or "NEW-SRC:<title>".
  This is how the independence audit detects echo and circular corroboration — name the source
  rather than fabricating a dataset for it.
- funding: inspect funding statement, affiliations, and COI disclosures. "industry" if any
  funder/affiliation has a commercial stake (trade groups, food/pharma; for reviews weigh
  author COI); else "independent".
- evidence: the closest EXISTING evidence type; "NEW:<label>" only if none fit.
- population: the studied GROUP (region / menopausal status / age) — NOT the study design
  (that is `evidence`). Reuse a term; prefer broad buckets; "—" if not population-specific. For a
  non-human study say so: "Mice" / "Rats" / "Animal model" / "In vitro / cell" — never let an
  animal or cell study look like human evidence.
- confidence: the source's OWN stated strength (high/moderate/low/unstated), not yours.
- provenance: for position and restsOn, quote ONE COMPLETE verbatim sentence that states the
  actual finding/stance (direction of the association or the conclusion) + extractionConfidence
  [0,1]. The quote MUST be a whole sentence, not cut off mid-clause (never end on "associated
  with", "compared to", …). NEVER use the paper's title, a heading, or the search snippet. If only
  the title/abstract is available and no sentence states the finding, quote the closest complete
  statement and set extractionConfidence ≤ 0.4 — never the title.
- factorWeights: a factor is a DIMENSION THE CAMPS DISAGREE ON (a crux), e.g. "weight given to
  industry funding", "biomarkers vs hard outcomes" — NOT a study parameter / subgroup / outcome
  (gestational age, parity, dose, cesarean rate: those describe a study, skip them). Name the
  DIMENSION, never a value ("Gestational age at induction", not "(39 weeks)" — the number goes in
  the quote). REUSE an existing factor label VERBATIM; a real factor is one MORE THAN ONE camp
  weighs — if only one side could engage it, it's a descriptive tag, not a crux. For each: how
  strongly its POSITION weights it (high/med/low) + quote + rationale.
- Do NOT fabricate. If the text doesn't support a field, omit it or mark low confidence.
````

## Output schema

```jsonc
{
  "source": {
    "title": "...", "year": 2020, "url": "...",
    "position": "pos_none" | "NEW:Some new stance",
    "evidence": "Observational", "funding": "independent" | "industry",
    "population": "US health professionals", "confidence": "moderate",
    "restsOn": ["ds_nhs", "NEW:US pooled meta"],
    "provenance": {
      "position": { "quote": "...", "extractionConfidence": 0.85 },
      "restsOn":  { "quote": "...", "extractionConfidence": 0.9 }
    }
  },
  "factorWeights": [
    { "factor": "Discount for healthy-user confounding", "weight": "low",
      "quote": "...", "rationale": "..." }
  ]
}
```

See `cases/eggs.delta-zhuang2021.json` for a filled example. Feed the result to:

```
python cli.py add cases/eggs.kb.json delta.json
```

## Cold start (deep research), as orchestration

1. `python cli.py init eggs "Do eggs raise cardiovascular risk?" --out cases/eggs.kb.json`
2. Agent searches for sources bearing on the question and its sub-questions
   (`python cli.py discover` / `harvest`).
3. For each source: run this prompt → `delta.json` → `add`. The KB and every metric grow
   incrementally; the run is resumable and each `add` prints what the new source changed.
