# Knowledge-base schema (v2) and the design problems it answers

The KB is a single JSON document — the **compounding artifact**. Everything else
(ingestion, the viewer, the metrics) produces or consumes it; nothing else owns state.
A new case is a new KB with the same shape, so the tooling is domain-general: only the
data changes, never the code.

## Shape

```jsonc
{
  "meta":      { "id", "question", "version", "updated", "note" },
  "positions": [ { "id", "label", "hue" } ],                 // the camps
  "datasets":  [ { "id", "label", "aliases": [..] } ],       // underlying evidence bases
  "vocab":     { "evidence":   [ { "label", "aliases": [..] } ],   // per-case controlled
                 "population": [ { "label", "aliases": [..] } ],   //   tag vocabularies
                 "funding":    [ { "label", "aliases": [..] } ] }, // closed funder categories
  "factors":   [ { "id", "label", "weights": {posId: high|med|low|n/a},
                   "rationale", "provenance": [{source, pos, quote}] } ],
  "sources":   [ { "id", "title", "year", "url",
                   "position": posId,
                   "evidence",                                // controlled (see vocab.evidence)
                   "funding",   // Government/public | Nonprofit/charity | Academic/institutional
                                //   | Industry | Advocacy | Undisclosed  (default Undisclosed)
                   "population", "confidence",
                   "restsOn": [datasetId, ...],              // powers independence
                   "provenance": { field: {quote, extractionConfidence} },
                   "addedIn": version } ],                    // powers the diff
  "log":       [ { "version", "action", "source", "ts", ... } ]  // audit trail
}
```

Three entity tables (`positions`, `datasets`, `factors`) carry **stable IDs**; sources
and factor-weights reference those IDs. That indirection is what makes the KB mergeable.

## The five hard problems, and where each is handled

1. **Entity resolution on merge.** When a new source cites "the Nurses' Health Study",
   is that `ds_nhs` or a new dataset? Handled in `engine/merge.py`: the ingestion LLM
   *proposes* (`existing_id` or `"NEW:<label>"`); the code *disposes* by normalized-string
   + alias matching. Deterministic and auditable — never dependent on LLM nondeterminism.
   String matching can't catch *paraphrase* duplicates, though (three "Women (mixed …)" terms),
   so `engine/curate.py` adds explicit, deterministic **merge / rename / tidy** ops (CLI `merge`,
   `rename`, `tidy`, `dups`; the UI Curate panel) plus a token-overlap `suggest_duplicates` to
   flag likely pairs. Each merge learns the folded label as an alias, so future ingests resolve
   correctly. New labels are also run through `prettify_label` so id-style slugs
   (`Finnish_cohort_Knekt_1996_4697_women`) become readable names on creation.

2. **Determinism & cost.** All assessment (`engine/assess.py`) is pure functions of the KB.
   Ingestion is O(new sources); recompute is O(whole KB) but just counting. Adding the
   1000th source never re-reasons over the first 999. That is the scalability story.

3. **Provenance per edge.** Every extracted field carries a `quote` + `extractionConfidence`
   back to the source. Without it the KB can't be audited and fails "withstands motivated
   reading." (Seed data leaves these empty and says so; real ingestion fills them.)

4. **Open schema (interoperability vs nuance).** A small fixed *core* the metrics operate on
   (source, position, dataset, factor, edge) plus *open vocabularies* as tags (`evidence`,
   `funding`, `population`). New domains add vocabulary, not new code — this is what lets one
   renderer/engine serve COVID, black holes, and eggs alike. The catch: the **blindspot**
   metric compares the *set* of evidence/population values across positions, so fully free-text
   tags make every camp "miss" everything (one source = one unique string). The fix is a
   **per-case controlled vocabulary** (`kb.vocab`), resolved by the *same* "propose, then
   deterministically resolve" discipline as datasets: `merge._resolve_vocab` snaps an incoming
   tag onto the case's canonical term (normalized + alias match) or adds a new one. A small
   global base for `evidence` is seeded into every case (`engine/schema.py:BASE_EVIDENCE`);
   `population` starts empty and grows per topic. The vocabulary lives in the artifact, not in
   code — so it is per-domain by construction while staying small enough for blindspots to mean
   something. The ingestion prompt shows the model the current vocabulary so it reuses terms.
   `funding` is a **closed** vocabulary (`BASE_FUNDING`: Government/public, Nonprofit/charity,
   Academic/institutional, Industry, Advocacy, Undisclosed) — `merge._resolve_funding` snaps to
   it and **defaults to "Undisclosed", never "independent"**, so a missing funding statement
   surfaces the gap instead of fabricating independence.

   *Two blindspot/crux refinements keep this readable at scale (`engine/assess.py`):* a type
   counts as "present in the case" only if ≥2 sources use it (`blindspots(min_support=2)`), so a
   single source's hyper-specific population isn't everyone's blindspot; and each factor reports
   `engaged` (how many positions weighed it), so the divergence view separates **cruxes** (spread
   ≥2) from **shared** factors and **one-sided** ones (only one camp engages).

5. **Adversarial robustness = the thesis, enforced at ingestion.** Three defences live in
   `engine/merge.py`: (a) **alias-splitting** — incoming dataset names match existing labels *and*
   learned aliases, so one cohort can't be smuggled in under many names to fake independence;
   (b) **duplicate sources** — same url, or same title+year, are refused, so a camp can't be
   inflated by re-submitting a study; (c) because `independence()` measures concentration,
   **adding correlated evidence pushes the concentration flag UP, not down** — flooding the
   zone makes a position look *less* independent. Verified in the README walkthrough.

## Why cold-start and update are the same path

A cold start is the update loop run N times over discovered sources; an update is the same
loop run once. `python cli.py ingest` (or `add`) is the only mutation. This is what makes the
KB "living, not a snapshot" (FLF) by construction — there is no separate batch build to drift
out of sync with the incremental path.
