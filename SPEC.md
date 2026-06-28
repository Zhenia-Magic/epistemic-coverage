# Ground Knowledge
### Aggregation weighted by independent evidence, with an independence audit — a spec for compounding, adversarially-robust knowledge bases for research disputes

*FLF "Lab Leaks, Black Holes, and Eggs" Epistemic Case Study Competition. Live at
[groundknowledge.org](https://groundknowledge.org). Read with the prototype in this repo:
`README.md` to run it, `MECHANISM.md` for the independence engine in depth, `WORKFLOW.md` to
operate it, `SCHEMA.md` for the data model. This document is the method.*

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

| | Deep-research summary | Ground Knowledge |
|---|---|---|
| Output | prose, for one reader, one time | a **structured JSON artifact** another team forks and extends |
| Numbers | asserted in text | each recomputed by one **legible function**; inspectable |
| Gaming | a flood of weak papers reads as "growing consensus" | echo, re-used cohorts, and **circular citation collapse to one look** — flooding can't inflate (or deflate) a side |
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

**Ingestion is three steps, and only the last needs a model.** *Finding* sources is **AI-driven by
default** — the model searches the web for real, citable sources, and is told what's already in the
KB so it returns *new* ones (a keyless OpenAlex scholarly search is the fallback, `ingest/search.py`).
*Reading* a source resolves its DOI/PMID/arXiv id back through open APIs (OpenAlex → arXiv →
Semantic Scholar → Europe PMC, `ingest/extract.py`), preferring the **full open-access PDF** when
one exists so the funding/COI statement and named cohorts in the methods are captured — deterministic
and free of publisher scraping. Only *labelling* the fetched text uses an LLM, and the model is the
contributor's choice: **Anthropic, OpenAI, DeepSeek, Mistral, Groq, Gemini, or OpenRouter** (one
stdlib code path, since all but Anthropic speak the OpenAI protocol). The labeller judges, in order:
**relevance** (off-topic sources are refused at merge, like duplicates), then position, evidence
type, funding, population, the datasets the source rests on (or the *other sources* it derives from),
and a verbatim **provenance quote** for each. So cold start works with **no LLM key** up to the
labelling step, and AI web search / deep research is an Anthropic feature while every provider can
label fetched text. The OpenAlex fallback keeps relevance high with two *stance-neutral* filters
(subject-topic cluster + exposure term) so a statin trial sharing "cardiovascular risk" is dropped
while *both* sides are kept.

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

**Distribution — naive *and* independence-weighted.** Two bars of the same split, shown together.
The first is the naive aggregator's view (share of *sources* per position). The second re-sizes
each position by its **effective independent evidence bases** — the Herfindahl numbers-equivalent
over the *resolved roots* its sources reduce to (`weighted_distribution`; see the independence
engine below), so sources sharing a dataset, echoing as reviews, or citing each other in a loop all
collapse toward one "look" and a position propped up by re-used, derivative, or circular data
**shrinks**. Seeing the correlated position contract between the two bars *is* the thesis, rendered.
**Funding skew** then complicates
it further: which position does *interested* money (Industry or
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

**Independence — the anti-false-balance core** (the full mechanism is `engine/roots.py` /
[`MECHANISM.md`](MECHANISM.md)). Echo, cohort re-use, and circular corroboration are one disease —
*a source that adds no new root* — so the metric counts **independent evidentiary roots per
position**, not sources. It resolves every source down to the primary evidence it ultimately
depends on by following its `restsOn` edges (to datasets **and to other sources**), then:
- **shared datasets** collapse to one root (eight papers off one cohort = one look);
- **ungrounded secondary sources** — reviews, commentary, *and untagged meta-analyses* — collapse to
  a single "voice" per position (a pile of reviews can neither inflate a side **nor** tank a rival);
- **strongly-connected citation cycles** (A→B→A with no primary grounding) collapse to one root and
  raise a **circular-corroboration flag** — the adversarial pattern, surfaced loudly;
- a dataset known **only via a review**, or a root backed **only by animal / in-vitro** sources,
  counts at **half** (weak evidence, shown distinctly).

The effective count is the Herfindahl numbers-equivalent (`nEff`) over those resolved roots, and the
audit **shows its work**: each position is broken down into its bases, with the collapsed-source
count surfaced separately. **Adding correlated, derivative, or circular evidence can only leave a
position's independence unchanged or lower — never raise it.** This is the metric that refuses to be
flooded, and it produces honest, differing verdicts (e.g. on the real COVID-origin case the
best-supported camp rests on several genuinely independent primary datasets while others collapse to
a single government-report or commentary voice — invisible to a source count).

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

5. **Adversarial robustness = the thesis, enforced at ingestion + assessment.** Concrete defences
   live in `engine/merge.py` (duplicate / alias / off-topic refusal) and `engine/roots.py` (echo,
   cohort, and circular-corroboration collapse) — see §8 for what they do *and* do not cover.

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

- **COVID — live, contested, expertise-heavy.** The independence audit tells the honest story a
  source count hides: the best-supported camp rests on several genuinely independent primary datasets
  (genomic, market-epidemiology, bat-reservoir), while other camps turn out to rest on *zero* primary
  evidence — government reports or commentary that collapse to a single voice each. The dispute's
  spread localises to a handful of cruxes (prior on lab accidents, furin site, ascertainment bias).

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

**Defended (verified in the prototype, with tests in `tests/test_independence.py`):**
- *Flooding the zone with echo.* A pile of reviews / commentary / untagged meta-analyses on a side
  collapses to **one voice** — so flooding can neither inflate a position **nor** tank a rival
  (collapsing to one voice is symmetric; a count-weighted version would let you attack a side you
  dislike by flooding it). Re-used cohorts collapse the same way.
- *Circular corroboration.* Two sources whose only support is citing each other — the adversarial
  pattern a reviewer flagged — are detected as a strongly-connected cycle, collapsed to one root,
  and **flagged**, instead of reading as two independent confirmations.
- *Alias-splitting.* One cohort submitted under five names is matched to a single dataset by the
  alias table, so it cannot fake independence.
- *Duplicate submission.* Same url, or same **title+year even under a different url** (the same paper
  via PMC vs DOI vs publisher), is refused — a camp can't be inflated by re-submitting a study.
- *Off-topic padding.* A real but tangential source is judged at labelling time and refused at merge,
  so it never pads a position.
- *Tier laundering.* A meta-analysis or review only earns independence if it **names the trials it
  pools** (then it collapses into them); an untagged one is echo, not a free independent base.
- *Animal evidence passed off as human.* A root backed only by animal / in-vitro sources counts at
  half on a clinical question.

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
- *Self-reported citation edges* — the independence engine only sees a dependency if the labeller
  recorded it, so an actor who **omits** a `src:` edge can look more independent than they are. We
  state this in [`MECHANISM.md`](MECHANISM.md) §8 rather than paper over it; a verification pass
  (re-fetch each source, check the stored quote is actually present) is the complementary defence,
  on the roadmap.
- *Tier mislabelling* — the primary/secondary floor depends on the evidence type being right; calling
  opinion "Observational" mints a root it shouldn't. Partial defences (controlled vocab, relevance
  gate, funding-defaults-to-Undisclosed) exist; not airtight.
- *Curated factor weights* — positions' factor weightings are a human/LLM summary, not mechanical;
  they are the softest input. The *mechanical* parts (counts, datasets, funding category, the
  independence resolution) are what resist gaming.

Naming these is the point: the metrics are **heuristics that redirect scrutiny**, not oracles.

---

## 9. What we are *not* claiming

We are not claiming the tool decides who is right. It claims something narrower and, we think,
more useful: that **counting sources is the wrong primitive for a research dispute**, and that a
small set of computable, gaming-resistant metrics — independent-evidence-bases, funding skew, crux
localisation, blindspots — re-aim a reader's scrutiny at the places that actually move the
conclusion. The seed weights are illustrative; the architecture, the metrics, and the eggs
evidence base are real and runnable.

---

## 10. Artifacts in this repo

| file | what |
|---|---|
| `engine/assess.py` | the metrics — every number the tool reports |
| `engine/roots.py` | the independence engine — tier-aware root resolution + circular-corroboration detection ([`MECHANISM.md`](MECHANISM.md)) |
| `engine/gaps.py` | gap analysis — where a position's evidence is thin — that steers gap-driven deep search |
| `engine/merge.py` | deterministic merge + entity resolution + duplicate / alias / off-topic defences + source→source edges |
| `engine/curate.py` | curation ops — merge / rename / tidy duplicates + duplicate suggester |
| `ingest/extract.py` | fetch text by identifier (OpenAlex / arXiv / Semantic Scholar / Europe PMC); full open-access PDF when available |
| `ingest/llm.py` | model-agnostic LLM access — Anthropic / OpenAI / DeepSeek / Mistral / Groq / Gemini / OpenRouter |
| `ingest/search.py` + `prompts/` | keyless OpenAlex fallback search; the labelling / discovery / research prompts |
| `cli.py` | `new · init · show · assess · gaps · deepen · add · build · ingest · ingest-batch · discover · research · harvest · merge · rename · tidy · dups · ui · pull · push · questions · import-citations · export` |
| `app/` | the deployed keyless **portal** ([groundknowledge.org](https://groundknowledge.org)) + a portable store (sqlite local / Postgres prod) the CLI pushes & pulls to |
| `ui/` | the local web console (`python cli.py ui`): find → fetch → label → import, **gap-driven deepen**, Curate, and pull/push |
| `cases/eggs.kb.json` | **real, url-cited** evidence base; pull the COVID / milk / black-hole cases from the portal |
| `viewer/index.html` | self-contained, render-only viewer (Coverage · Divergence · Independence · Changes) |
| `MECHANISM.md` / `SCHEMA.md` / `QUICKSTART.md` / `WORKFLOW.md` | the independence mechanism / data model / step-by-step tasks / operator runbook |

Run `python cli.py show cases/eggs.kb.json`, then `python cli.py build cases/eggs.kb.json` and open
the viewer — or just visit [groundknowledge.org](https://groundknowledge.org). Total time to a
running local demo on a fresh machine: about a minute, no dependencies.
