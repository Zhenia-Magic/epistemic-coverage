# Epistemic Coverage
### Aggregation weighted by evidence quality, with independence auditing — a spec for compounding, adversarially-robust knowledge bases for research disputes

*FLF "Lab Leaks, Black Holes, and Eggs" Epistemic Case Study Competition. Read with the
prototype in this repo: `README.md` to run it, `WORKFLOW.md` to operate it, `SCHEMA.md` for the
data model. This document is the method.*

---

## 1. The insight, stated once

Ground News aggregates the news: it labels each outlet's lean, shows the distribution of
coverage, and flags "blindspots" — what one side isn't covering. It is deliberately **neutral
on who is right**, which is the correct stance for political framing.

Port that interface to a *research* dispute and the neutrality becomes a bug. In a research
dispute some positions genuinely are better supported, so naive source-counting rewards the
loud, the numerous, and the industry-funded over the correct. That is **false balance** — the
opposite of good epistemics.

So our one design commitment, from which everything else follows:

> **Aggregate, but weight by evidence quality and audit for independence instead of counting
> sources.** If a feature could be gamed by flooding the zone with low-quality or correlated
> papers, it is wrong. Adding correlated evidence to a position must make it look **less**
> settled, not more.

Everything below is in service of that inversion.

---

## 2. What it produces, and why that beats the baseline

The honest baseline for any entry is *"a careful analyst with a deep-research tool on the same
sub-question."* Deep research is very good and improving. It produces **one prose answer, once.**

We produce a different kind of object:

| | Deep-research summary | Epistemic Coverage |
|---|---|---|
| Output | prose, for one reader, one time | a **structured JSON artifact** another team forks and extends |
| Numbers | asserted in text | each recomputed by one **legible function**; inspectable |
| Gaming | a flood of weak papers reads as "growing consensus" | a flood of *correlated* papers **raises the concentration flag** |
| Updating | re-run the whole query | add one source → **O(new) ingest**, deterministic recompute, **diff of what changed** |
| Audit | trust the narrator | every edge carries a **provenance quote**; the metric is a pure function you can re-run |

The value isn't a better paragraph. It's a knowledge base that **compounds**, an audit that
**holds under motivated reading**, and a record of **what each new source did to the picture.**

---

## 3. Architecture: three layers around one artifact

We adopt FLF's own decomposition — **Ingestion → Structure → Assessment** — and add the
property FLF asks for explicitly ("living knowledge bases, not merely snapshots… track how the
structure evolves over time").

```
 question ─► INGESTION ─► (delta) ─► STRUCTURE ─► cases/<id>.kb.json ─► ASSESSMENT ─► DIFF ─► viewer
            search→fetch→label       merge+resolve      the artifact       recompute (pure)
            (API · API · LLM)        (deterministic)    you own & share    (deterministic)
```

Two design decisions carry most of the weight:

**(a) The knowledge base is the source of truth; everything else is a producer or consumer of
it.** Ingestion writes deltas into it; the metrics are pure functions of it; the viewer is a
*baked snapshot* of it. State lives in exactly one place — a single JSON file a judge can read,
diff, and hand to another team.

**(b) Cold-start and incremental update are the *same code path*.** A cold start is the update
loop run N times over discovered sources; an update is the same loop run once. `ingest`/`add` is
the only mutation. There is no separate batch build to drift out of sync with the incremental
path — which is precisely what makes the base "living" rather than a snapshot.

**Ingestion is three steps, and only the last needs a model.** *Finding* sources is a scholarly
search over OpenAlex (`ingest/search.py`); *reading* a source resolves its DOI/PMID/arXiv id back
through open APIs (OpenAlex → arXiv → Semantic Scholar → Europe PMC, `ingest/extract.py`) for a
clean abstract plus funder metadata — both deterministic, keyless, and free of publisher
scraping. Only *labelling* the fetched text (which position, which datasets, funding category,
factor weights) uses an LLM. So cold start works with **no LLM key at all** up to the labelling
step, and candidates are always real, citable works rather than model guesses. Relevance is kept
high by two *stance-neutral* filters that matter for the thesis: a candidate must sit in the
dispute's dominant subject-**topic** cluster (OpenAlex's ML topic classes, corroborated across a
precise and a broad query) *and* mention the question's **exposure** term. Both classify subject,
not stance — so a statin trial that merely shares "cardiovascular risk" is dropped while *both*
sides of the actual dispute are kept (verified: a COVID-origins search returns zoonosis *and*
lab-leak papers). This avoids the false-balance failure of position-keyword filtering, which would
silently favour whichever camp's vocabulary the question happens to use.

**The "propose, then deterministically resolve" contract.** The LLM is powerful but
non-deterministic, so we confine it to *proposing*: it reads one fetched source and emits a delta
that links to existing entity ids *or* marks something `"NEW:<label>"`. Deterministic code then
*disposes* — resolving links by normalized-string + alias matching. The reproducible, auditable
parts (which datasets, which positions, counts, metrics) never depend on model temperature; merge,
metrics, and viewer are deterministic and offline. So the conclusions are reproducible regardless
of which model — or no model — produced the inputs.

---

## 4. The assessment metrics — what each computes, and what it resists

Every metric is a pure function over the KB (`engine/assess.py`). Four of them, each chosen to
serve the thesis:

**Distribution + funding skew.** The naive aggregator's view (share of sources per position),
shown *and then immediately complicated*: which position does *interested* money (Industry or
Advocacy) most favour? On the real eggs case the two industry-funded studies (DIABEGG → Australian
Egg Corporation; Blesso → Egg Nutrition Center) both back "context-dependent / safe" — a flag to
weigh before counting heads, not a verdict. Funding is a **closed vocabulary** (Government/public,
Nonprofit/charity, Academic/institutional, Industry, Advocacy, Undisclosed) that **defaults to
Undisclosed, never "independent"** — so the metric also reports how much of a case rests on
sources that don't disclose funding, surfacing the data gap instead of fabricating independence.

**Divergence / cruxes** (intellectual lineage: Heuer's *Analysis of Competing Hypotheses*). A
factors × positions matrix; a factor is a **crux** when its weighting spreads ≥2 levels across
positions. The point is to show that most disagreement is *local* — camps agree on most factors
and diverge on a few. Each factor also reports `engaged` (how many positions weighed it) so the
view separates cruxes from **shared** factors and **one-sided** ones (a dimension only one camp
raises — dimmed, not a point of disagreement). A subtle property: a factor becomes a crux only
once enough positions have weighed in, so cruxes **emerge as the base grows** (visible in the
black-hole case: the three cruxes appeared only when the dissenting source arrived).

**Independence / concentration — the anti-false-balance core.** Per position we compute, over the
datasets its sources rest on: the single most-reused dataset and the share of sources resting on
it (`concentration`), plus a Herfindahl numbers-equivalent (`nEff`) = effective independent
datasets discounted for concentration. **Adding a source that rests on an already-used dataset
pushes concentration up** — so correlated evidence makes a position look *less* independent. This
is the metric that refuses to be flooded. It produces honest, differing verdicts:
- COVID "Zoonosis": 5 sources, **100%** on Huanan-market data ≈ 2.5 independent looks, not 5.
- Black holes "No risk": 3 sources, **100%** on the cosmic-ray argument ≈ 2.6 looks — the single
  load-bearing dependency of the settled consensus.
- Eggs "No association": a **modest** 50% on NHS (Hu 1999 + Drouin 2020, the same Harvard cohorts
  two decades apart) — and case-wide only 33%, far below COVID. **The tool does not manufacture a
  concentration problem where none exists.**

**Blindspots.** Evidence types and populations present elsewhere in the case but absent from a
position's own sources — operationalising FLF's "surface what's missing." Two data-quality
guards make this meaningful at scale: it computes over a *controlled vocabulary* (§8), and a type
counts as "present" only if **≥2 sources** use it, so one source's hyper-specific population isn't
flagged as every other position's blindspot.

---

## 5. The five hard problems, and the tradeoffs we accept

This is the part to read for the method. None are solved by the prototype's seed data; they are
structural and named honestly.

1. **Entity resolution on merge.** Is "the Nurses' Health Study" the existing `ds_nhs` or new?
   Handled by normalized-string + learned-alias matching in `engine/merge.py`. *Tradeoff:* robust
   to casing/aliases, **not** to paraphrase ("Wuhan market dataset" vs "Huanan seafood market").
   We chose deterministic string matching first because reproducibility matters more than recall
   at this layer — a wrong silent merge corrupts every downstream metric. Two mitigations close
   the gap without giving up determinism: strong **prompt discipline** (reuse aggressively; a
   mechanism is a factor not a position; `restsOn` is a named cohort, never "the literature"),
   which cut a 31-source test case from 6 positions / 24 datasets to 3 / 10; and explicit
   **curation ops** (`engine/curate.py`: merge, rename, tidy, plus a token-overlap duplicate
   suggester) exposed in the CLI and the UI Curate panel, so a human resolves the residual
   paraphrases a string match can't. Embedding-assisted suggestions are the next step.

2. **Determinism & cost (scalability).** All assessment is pure counting over the KB. Ingestion is
   **O(new sources)**; recompute is **O(whole KB) but cheap**. Adding the 1000th source never
   re-reasons over the first 999. That is the scalability story: it gets better with more
   contributors and compute, not bottlenecked on re-doing prior work.

3. **Provenance per edge.** Every extracted field carries a quote + extraction-confidence back to
   the source (see the real eggs entries). Without this the base can't be audited and fails
   "withstands motivated reading." *Tradeoff:* extraction confidence is the model's self-report;
   it bounds, but does not eliminate, extraction error.

4. **Open schema — interoperability vs nuance** (a tension FLF names explicitly). A small fixed
   *core* the metrics operate on (source, position, dataset, factor, edge) plus *open
   vocabularies* (evidence, funding, population) as tags. New domains add vocabulary, not code —
   which is why one engine serves eggs, COVID, and black holes unchanged. *Tradeoff:* fully open
   vocabularies degrade the blindspot metric; a light controlled vocabulary is the working
   compromise (§8).

5. **Adversarial robustness = the thesis, enforced at ingestion.** Three concrete defences live in
   `merge.js`/`merge.py` (§8 for what they do *not* cover).

---

## 6. Generalization: one engine, three differently-shaped cases

FLF chose three cases with deliberately different profiles; a general tool must handle all three
with **only the data changing.** It does:

- **Eggs — contested, mundane, malformed question.** The tool's headline findings fall out of the
  data, not the curation: real funding skew toward "safe," a real but *modest* NHS over-reliance,
  and — the insight — the lone non-crux factor is "subgroups," weighted `med` by *all three* camps
  because Hu 1999, DIABEGG and the AHA advisory all concede diabetics/hyper-responders differ. The
  tool is flagging that "are eggs healthy?" is mis-posed: the answer is "for whom?" (9 real,
  url-cited sources; a judge can click each.)

- **COVID — live, contested, expertise-heavy.** Five "zoonosis" sources collapsing to ~2.5
  independent looks on the shared Huanan dataset; case-wide 88% concentration; the famous
  23-orders-of-magnitude spread localised to a handful of cruxes (prior on lab accidents, furin
  site, ascertainment bias).

- **Black holes — essentially settled.** Distribution collapses to "No risk" (3 of 4), but
  independence shows those rest **100%** on the cosmic-ray argument, and the lone dissent attacks
  exactly that argument plus Hawking-radiation reliability. The tool surfaces the single
  load-bearing dependency of a settled consensus — FLF's explicit ask for this case.

Same `assess()`; same renderer; three lines of `build`. That is the generalization evidence.

---

## 7. Scalability, compounding, shareability

- **Scales with better models** (ingestion/extraction quality), **more compute** (broader
  discovery), and **more contributors** (each adds sources through the same merge). The
  bottleneck is never a single hand-designed human step.
- **Compounds:** the artifact is a JSON file another team forks and keeps growing; nothing is
  locked in the UI. The `Changes` tab and append-only log make the evolution legible.
- **Shareable two ways:** `viewer/index.html` is self-contained (double-click, no server) for a
  reader; `cases/<id>.kb.json` is the portable artifact for an extender.
- **No drift:** because the metrics are computed once in Python and the viewer only renders, what
  the pipeline concludes and what a reader sees are guaranteed identical.

---

## 8. Adversarial robustness — failure modes named and bounded

**Defended (verified in the prototype):**
- *Flooding the zone.* Adding correlated sources raises `concentration`; a position propped up by
  re-used data reads as *fewer* independent looks. (Demonstrated: adding an NHS/HPFS egg study
  moves "No association" 67%→75%; adding an independent cohort moves it the other way.)
- *Alias-splitting.* One cohort submitted under five names is matched to a single dataset by the
  alias table, so it cannot fake independence.
- *Duplicate submission.* Same url, or title+year, is refused — a camp can't be inflated by
  re-submitting a study.

**Partially addressed since first draft:**
- *Paraphrase-level entity collision* — a determined actor describing the same dataset in novel
  prose can still split it on ingestion, but the **curation ops** (§5.1: merge/rename/tidy + a
  duplicate suggester) let a human collapse it deterministically after the fact; full automation
  awaits embedding-based resolution.
- *Advocacy / undisclosed funding* — the `funding` axis is no longer binary. It is a closed
  vocabulary (Industry, Advocacy, Government/public, Nonprofit/charity, Academic/institutional,
  Undisclosed) that captures *advocacy* stake (e.g. a dairy council) separately, and **defaults
  to Undisclosed rather than independent** — so the skew metric reports the disclosure gap instead
  of asserting independence it can't verify. (It still can't read a funder's true intent from an
  abstract that omits the funding statement — those land in Undisclosed, honestly.)

**Not defended (stated plainly):**
- *Curated factor weights* — positions' factor weightings are a human/LLM summary, not mechanical;
  they are the softest input and should be treated as such. The *mechanical* parts (counts,
  datasets, funding category, concentration) are what resist gaming.
- *Controlled-vocabulary dependence* — blindspots degrade to noise under free-text evidence/
  population values; the tool needs a light canonical vocabulary at ingestion, which is itself a
  surface a careless contributor can corrupt.

Naming these is the point: the metrics are **heuristics that redirect scrutiny**, not oracles.

---

## 9. What we are *not* claiming

We are not claiming the tool decides who is right. It claims something narrower and, we think,
more useful: that **counting sources is the wrong primitive for a research dispute**, and that a
small set of computable, gameable-resistant metrics — concentration, funding skew, crux
localisation, blindspots — re-aim a reader's scrutiny at the places that actually move the
conclusion. The seed weights are illustrative; the architecture, the metrics, and the eggs
evidence base are real and runnable.

---

## 10. Artifacts in this repo

| file | what |
|---|---|
| `engine/assess.py` | the metrics — every number the tool reports |
| `engine/merge.py` | deterministic merge + entity resolution + funding/label normalization (the adversarial defences) |
| `engine/curate.py` | curation ops — merge / rename / tidy duplicates + duplicate suggester |
| `ingest/search.py` | scholarly search (OpenAlex) — finds candidate papers by question, no key |
| `ingest/extract.py` | fetch text by identifier (OpenAlex / arXiv / Semantic Scholar / Europe PMC), reader-proxy fallback |
| `ingest/` + `prompts/` | LLM *labelling* of fetched text, batch extraction, one-shot research (model-agnostic) |
| `cli.py` | `init · discover · research · ingest · ingest-batch · add · merge · rename · tidy · dups · harvest · show · build · ui` |
| `ui/` | the local web console (`python cli.py ui`): research → fetch → label → import, plus Curate |
| `cases/eggs.kb.json` | **real, url-cited** evidence base; `covid` / `blackhole` are worked seeds |
| `viewer/index.html` | self-contained, render-only viewer (Coverage · Divergence · Independence · Changes) |
| `QUICKSTART.md` / `WORKFLOW.md` / `SCHEMA.md` | step-by-step tasks / operator runbook / data-model spec |

Run `python cli.py show cases/eggs.kb.json`, then `python cli.py build cases/*.kb.json` and open
the viewer. Total time to a running demo on a fresh machine: about a minute, no dependencies.
