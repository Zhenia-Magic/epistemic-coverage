# The Evidence-Independence Mechanism

*The conceptual core of Ground Knowledge, in plain language. This is the spec we label against
and compute against. Read it adversarially — the point is to find the weak spots before they find
us.*

---

## 0. The one-sentence idea

> A position is only as strong as the number of **genuinely independent pieces of evidence** behind
> it — not the number of documents that assert it. The mechanism counts *independent evidentiary
> roots*, collapsing everything that merely echoes, summarizes, or circularly cites the same
> underlying evidence.

Counting sources rewards whoever publishes (or re-publishes) most. We count *roots*.

---

## 1. The problem we are solving

Three failure modes break naive source-counting, and they look identical on a bar chart:

1. **Echo.** Ten review articles all summarizing the same three primary studies are *one* look,
   not ten. Yet each is a separate "source."
2. **Cohort re-use.** One research group publishes eight papers off a single cohort. Eight
   sources, one independent dataset.
3. **Circular corroboration.** Source A's main evidence is Source B; Source B's main evidence is
   Source A. Two sources, *zero* independent grounding. This is the adversarial case — it is
   designed to look like mutual confirmation.

All three are the same disease: **sources that add no new root.** The mechanism is one cure for
all three.

---

## 2. Ontology — the five things that exist

| Thing | Plain meaning | Example |
|---|---|---|
| **Source** | A document that takes a position. | A paper, a report, a commentary, a guideline. |
| **Position** | A stance on the question. | "Zoonotic origin." |
| **Root** (evidentiary basis) | The underlying thing that actually *generates* information. | A dataset, a cohort, an experiment, a model run, a field observation. |
| **Derivation** | What a source draws its support *from*. Points at roots and/or other sources. | "This review rests on the proximal-origin dataset." |
| **Tier** | Does the source *make* evidence or *talk about* evidence? | **Primary** (makes) vs **Secondary** (talks about). |

The key move: **sources and roots are different kinds of node.** Many sources can share one root.
Independence is a property of *roots*, counted *per position*.

---

## 3. What we record when we label a source

For every source, the labeller (human or LLM) records:

1. **Position** — the single stance it argues. (Already in the schema.)
2. **Evidence type** — from the case's controlled vocabulary. This determines the **tier**:
   - **Primary** tiers *make* evidence: `Observational`, `Experimental (RCT)`, `Mechanistic`.
   - **Secondary** tiers *talk about* evidence: `Meta-analysis` / `Systematic review`,
     `Narrative/Commentary`, `Evidence-synthesis`, `Expert advisory`, `Institutional statement`,
     `Editorial/Perspective`. A meta-analysis is a *synthesis* — it only counts as independent if it
     **tags the trials it pools** (then it resolves through them, tier aside); an untagged one is
     echo and collapses into the position's one secondary voice. (This is the common real failure:
     ten meta-analyses of the same overlapping RCTs are one look, not ten.)
   - Tier is a property attached to each vocabulary term, not guessed per source — so it is
     deterministic and auditable, and a case can override it.
3. **`restsOn`** — the heart of the mechanism. A list of what this source draws support from.
   Each entry is one of:
   - a **root** (a dataset / cohort / experiment), by id or `NEW:<label>`;
   - **another source** in this KB, by id, when the source's case *is* that other source
     (`SRC:<id>` or `NEW-SRC:<title>`) — this is how we capture citation/derivation and catch
     circularity;
   - nothing (the list is empty) — the source is **ungrounded**.
4. **Provenance quote** — the verbatim sentence that justifies the position (already in schema;
   also what the *verification* pass checks).

> **Labelling principle for `restsOn`:** tag what the source *actually leans on*, even for reviews.
> A review of the proximal-origin paper **rests on the proximal-origin dataset** — say so, and it
> will collapse into that root automatically. Only leave `restsOn` empty when the source genuinely
> grounds in nothing checkable (pure opinion). This is what makes the mechanism degrade *gracefully*:
> good tagging makes the tier rule unnecessary; the tier rule is the safety net for bad tagging.

---

## 4. How independence is computed (the algorithm, in plain language)

### 4.1 Build the dependency graph
Nodes are **sources** and **roots**. For each source, draw an edge to every thing in its
`restsOn` (a root, or another source). Self-edges (a source resting on itself) are dropped.

### 4.2 Resolve every source to its **root set**
Walk each source's edges down to the primary evidence it ultimately depends on:

- rests on **dataset(s)** → those datasets are its roots.
- rests on **other source(s)** → its roots are the *union of those sources' root sets*
  (follow the chain recursively).
- rests on **nothing**:
  - **primary** tier → the source *is its own root* (a new observation; benefit of the doubt).
  - **secondary** tier → it has **no root of its own**. It joins this position's single
    **ungrounded-secondary pool** — one shared pseudo-root per position. (This is the
    "collapse all echo to one voice" decision.)

### 4.3 Collapse cycles — *circular corroboration*
While following source→source edges we may hit a **cycle**: A→B→A, or longer. Compute the
**strongly connected components** (SCCs) of the source graph. For each SCC of size > 1 (a group of
sources that mutually depend):

- The whole component **collapses to one root**, because no member adds independence the others
  don't already have.
- If the component **also** reaches a real dataset (someone in the loop is actually grounded),
  it collapses *into that dataset's root* — redundant, not vacuous.
- If the component reaches **no** dataset and **no** primary source — it is **pure circular
  corroboration**. It collapses to one pool root **and raises a `circularCorroboration` flag**
  naming the loop. This is the adversarial pattern, surfaced loudly.

### 4.4 Count effective independent roots per position
For each position, take all its sources, map each to its resolved root(s), and tally how many
sources land on each root. Then compute the **Herfindahl numbers-equivalent**:

```
nEff = 1 / Σ (share_of_root_i)²
```

This is the "effective number of independent looks." One root used by everyone → nEff ≈ 1. Ten
roots each used once → nEff = 10. A pile on one root collapses toward 1 even if a few others exist.

### 4.5 The two bars the reader sees
- **By source count** — the naive tally (kept, honestly labelled as naive).
- **By independent evidence** — sized by `nEff` over resolved roots.
- Plus, always, **the derivation shown**: "25 sources → 7 independent bases (15 secondary reviews
  counted as 1 voice; furin-cleavage + proximal-origin share the Andersen dataset)." The number is
  never a black box.

---

## 5. Worked examples

**E1 — Echo.** Position has 1 primary study (dataset D) + 12 narrative reviews, all `restsOn`
empty, all secondary. → roots = {D, secondary-pool}. nEff ≈ 2. The 12 reviews are one voice.

**E2 — Echo, well-tagged.** Same, but each review `restsOn` D (we tagged what they summarize). →
every node resolves to D. nEff = 1. Even cleaner — and no tier rule was needed.

**E3 — Cohort re-use.** 8 papers, all `restsOn` cohort C. → nEff = 1. (Existing behavior.)

**E4 — Pure circular corroboration.** A `restsOn` [SRC:B], B `restsOn` [SRC:A], no datasets. →
SCC {A,B}, no dataset → collapses to 1 pool root, **flagged**. nEff contribution = 1, with a
warning the reader can see.

**E5 — Circular but grounded.** A `restsOn` [SRC:B, dataset D]; B `restsOn` [SRC:A]. → SCC {A,B}
reaches D → collapses into D. nEff via D. Redundant, not flagged vacuous.

**E6 — Chain.** A `restsOn` [SRC:B]; B `restsOn` [SRC:C]; C `restsOn` [dataset D]. → all resolve
to D. A review-of-a-review-of-a-study counts as the study.

**E7 — Contested dataset.** Dataset D is cited by a source under Position X *and* a source under
Position Y. → D is one root under X and one root under Y (independence is per-position). Optional
meta-flag: "this root is read both ways" (contested evidence).

---

## 6. Edge cases and how each resolves

1. **Ungrounded primary source** (a real observation, no dataset tagged) → its own root (benefit
   of the doubt). *Weak spot:* someone could mislabel a commentary as `Observational` to mint a
   free root. *Defense:* tier comes from the controlled vocab; the verification pass; the relevance
   gate; funding-defaults-to-Undisclosed. Flagged below as an open risk.

2. **Meta-analysis** — *secondary or primary?* If it produces a **new pooled dataset** (re-analyzes
   raw data), it is primary: tag that pooled dataset as a root and it counts. If it only narrates
   others' conclusions, it is secondary. **The `restsOn` tag decides, not the word "meta."**

3. **Self-citing research group** → shared cohort root → collapses to 1. (Alias resolution stops
   the cohort being smuggled in under many names.)

4. **Long / transitive chains** → resolve to the terminal root. (E6.)

5. **One source resting on many roots** (a broad synthesis on 10 datasets) → contributes those 10
   roots. *Weak spot:* a single review could *assert* breadth that isn't independently present in
   the KB. *Proposed handling:* a root supported **only** by secondary sources is marked
   "asserted, not directly present" and can be down-weighted or shown distinctly. (Open: do we
   count it fully in v1?)

5b. **Non-human evidence on a clinical question.** A root backed **only** by animal / in-vitro
   sources (per the `population` tag: Mice, Rats, In vitro, …) is weaker evidence for a *human*
   question, so it counts at **half** — same mechanism as the secondary-only halving, and the two
   stack. A root that any human source also rests on keeps full weight. This needs the population
   tag to be set, so it only fires on sources labelled with the animal/in-vitro convention.

6. **Same dataset under multiple positions** → counts once per position; optional "contested"
   meta-flag. Not double-counting — independence is a within-position question.

7. **Cross-position circular corroboration** (A in X cites B in Y, B cites A) → an SCC spanning
   positions. v1: collapse to one root, contribute to each touched position, flag. *Weak spot:*
   semantics of a cross-side loop are genuinely odd; flagged as open.

8. **Missing/garbled `restsOn`** → tier default kicks in (primary→own root, secondary→pool).
   Graceful degradation; never crashes the metric.

9. **Duplicate roots under different names** → relies on root-identity resolution
   (normalized-string + learned aliases). *Weak spot:* a brand-new alias nobody has seen can slip
   through until curated. *Defense:* `dups`/`merge` tools + verification.

10. **Self-loop** (A rests on A) → edge dropped; A treated by its other edges / tier default.

11. **Empty position** (0 sources) → nEff 0, 0%.

12. **A secondary source that is the *only* thing citing an otherwise-absent primary study** → see
    (5). The primary study isn't in the KB; we only have a claim about it.

13. **Position whose entire support is one SCC of commentaries** → nEff 1 + circular/secondary
    flag. The chart shows "1 independent voice" — which is the honest answer.

---

## 7. Adversarial robustness (attack → defense)

| Attack | What the attacker wants | Defense |
|---|---|---|
| Flood with review articles | Inflate independence with echo | Secondary tier collapses to one voice per position |
| Re-submit one cohort under many names | Fake many independent datasets | Normalized + alias root resolution; concentration *rises*, not falls |
| Mutual citation ring (A↔B↔C↔A) | Manufacture corroboration from nothing | SCC collapse to one root + `circularCorroboration` flag |
| Mislabel a commentary as `Observational` | Mint a free independent root | Tier from controlled vocab; verification pass; relevance gate |
| Single review asserting broad dataset support | Fake breadth | "Root present only via secondary" mark (proposed) |
| Re-submit the same study | Inflate count | Duplicate refusal (same url / title+year) |
| Add an off-topic but real study | Pad a position | Relevance gate refuses it at merge |

The deep property: **adding correlated or derivative evidence cannot raise a position's
independence; at best it leaves it unchanged, and circular evidence raises a warning.** Flooding
the zone makes a side look *less* independent, never more.

---

## 8. Weak spots we acknowledge (open problems — document, don't hide)

1. **Tier mislabelling.** The whole "primary vs secondary" floor depends on the evidence type
   being right. If a contributor (or model) calls opinion "Observational," it earns a root it
   shouldn't. *Partial defenses exist; not airtight.* This is the single biggest lever and the
   first place to attack the system.
2. **Roots asserted only by secondary sources.** We may credit a dataset that no primary source in
   the KB actually instantiates (edge case 5/12). v1 may over-count these.
3. **Citation data is self-reported.** We only know A rests on B because the labeller said so.
   We do not crawl real citation graphs. A truly adversarial actor can *omit* a `SRC:` edge to hide
   a dependency and look more independent than they are. The verification pass can catch *false*
   quotes but not *missing* derivation edges.
4. **Cross-position cycles** have genuinely ambiguous semantics (edge case 7).
5. **Alias gaps.** A novel name for an existing cohort counts as new until someone curates it.
6. **"One voice" is a modelling choice, not a measurement.** Two truly independent review teams
   *might* deserve >1. We chose the conservative floor (B) on purpose; it is a stance, not a fact.

If we publish the system, we publish this list. That is the epistemically honest move — the same
instinct as documenting dataset errors instead of silently fixing them.

---

## 9. Why it is novel

Most tools in this space do one of: (a) count studies; (b) score each study's *internal* quality
(risk-of-bias, GRADE); or (c) build a citation graph for *influence* (who's cited most). None of
them measure **how many independent evidentiary roots actually support a claim**, and none treat
**echo, cohort re-use, and circular citation as one phenomenon** resolved by collapsing a
derivation graph to its roots. The combination — *tier-aware grounding* + *root-resolution over a
derivation graph* + *strongly-connected-component collapse for circular corroboration* + *Herfindahl
effective-count* — is, as far as we know, new as a single, deterministic, auditable metric.

## 10. Why it is general

The mechanism never looks at domain content. It operates on the abstract ontology of §2 —
source, position, root, derivation, tier. The *vocabulary* (what counts as a dataset, what evidence
types exist) lives in the per-case KB, not in code. So the identical engine maps a virology dispute,
a nutrition dispute, and a physics dispute. A new field adds vocabulary, never code. That is what
lets one renderer and one metric serve every question on the portal.

---

## 11. Data-model change this requires

`restsOn` today holds dataset ids. We extend each entry to be **either** a dataset id **or** a
reference to another source (`SRC:<id>` / `NEW-SRC:<title>`). Everything else (positions, factors,
provenance) is unchanged. Migration is trivial: existing KBs have only dataset entries, which are a
valid subset, so old data resolves exactly as before — except that ungrounded *secondary* sources
now collapse (the §4.2 rule), which is the intended re-weighting.

**Computation lands in `engine/assess.py`** (pure functions, recomputed from the KB — no drift).
**Labelling guidance lands in `prompts/ingest.md` + `ingest/pipeline.py`** (the `restsOn` rule and
the tier table). **Verification** is a separate axis (truthfulness of a quote), not part of this
file.
