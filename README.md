# Epistemic Coverage — a living, recomputing knowledge base for research disputes

> *Ground News, but for research disputes — with the aggregator's neutrality inverted.*
> Ground News counts and labels sources but stays neutral on who's right. In a research
> dispute, naive source-counting rewards the loud, the numerous, and the industry-funded.
> So we **aggregate, but weight by evidence quality and audit for independence** instead of
> counting heads. Adding more *correlated* papers makes a position look **less** settled, not more.

A Python pipeline that maps onto FLF's three-layer stack and produces a **living** knowledge
base (not a snapshot): AI **discovers** sources → **extracts** each link/document into a
structured delta → **merges** deterministically → **recomputes** every metric → shows a
**diff** of what changed → **bakes** a self-contained viewer. Cold-start and incremental
update are the *same code path*, run N times or once.

## Layout

| path | layer | what it is |
|------|-------|-----------|
| `ingest/pipeline.py` · `prompts/` | **Ingestion** | discovery + one-source extraction (link / PDF / docx / txt) |
| `ingest/search.py` | Ingestion | scholarly search via OpenAlex (no key) — finds candidate papers by question, filtered to the dispute's subject topic + exposure |
| `ingest/extract.py` | Ingestion | fetch text by identifier — OpenAlex / arXiv / Semantic Scholar / Europe PMC; uses the **full open-access PDF** when available (+ Crossref funder lookup); no scraping |
| `ingest/llm.py` | Ingestion | model-agnostic LLM access (Claude *or* OpenAI), used only to *label* fetched text; `--dry-run` needs no key |
| `engine/merge.py` | **Structure** | deterministic merge + entity resolution + funding/label normalization (LLM proposes ids, code disposes) |
| `engine/curate.py` | Structure | curation ops: merge / rename / tidy duplicate entities (+ duplicate suggester) |
| `engine/assess.py` | **Assessment** | the only place numbers are computed: distribution, funding skew, blindspots, cruxes, **concentration** |
| `cli.py` | orchestrator | `init · discover · research · ingest · ingest-batch · add · merge · tidy · dups · harvest · show · build · ui · pull · push · questions` |
| `ui/` (`cli.py ui`) | UI | local **workstation** console: find → fetch → label → import, Curate, and **pull/push** to a portal |
| `app/` (`python -m app.portal`) | **Deployment** | a shared multi-user **portal** (browse/search questions, contribute keyless via your own chatbot) + a portable store (sqlite local / Postgres prod) the CLI pushes & pulls to |
| `viewer/template.html` → `viewer/index.html` | UI | render-only; baked by `build`; opens with a double-click |
| `cases/*.kb.json` | artifact | the knowledge bases (eggs is real & sourced; covid/blackhole are seeds) |
| `SCHEMA.md` | spec | the schema and the design problems it answers |

**Core (engine + viewer build + URL/txt ingestion + `--dry-run` + the local portal store) is
pure stdlib — no `pip install`.** Full-text PDF labelling needs `pypdf` and Postgres-in-prod needs
`psycopg` (`requirements.txt`); both degrade gracefully without. Tested on Python 3.9–3.10.

> ### Reviewers — start here
> Everything runs locally with **no API key** (discovery + fetch are keyless; only *labelling*
> needs a model — paste into your own chatbot, or set a key for auto mode).
> ```bash
> python cli.py show  cases/eggs.kb.json                 # every metric, recomputed from the KB
> python cli.py build cases/*.kb.json && open viewer/index.html   # the baked viewer (4 tabs)
> python -m app.seed && python -m app.portal             # the multi-user portal → localhost:8800
> python cli.py ui                                       # the local workstation console
> ```
> Then exercise it on a question you care about: in the portal, **+ New question → Find →
> Fetch → label in your chatbot → Import**; or in the CLI, `harvest`/`ingest` then `push`.
>
> **Reading for the method?** [`SPEC.md`](SPEC.md) is the ≤10-page written submission — thesis,
> architecture, metrics, the five hard problems, generalization, and failure modes.
> [`QUICKSTART.md`](QUICKSTART.md) is task-by-task; [`WORKFLOW.md`](WORKFLOW.md) the operator runbook.

## Run it (≈1 minute, no API key)

```bash
# See every metric, recomputed from a KB:
python cli.py show cases/eggs.kb.json

# Bake the viewer and open it:
python cli.py build cases/eggs.kb.json cases/covid.kb.json
open viewer/index.html            # or just double-click it
```

`cases/eggs.kb.json` is a **real, verifiable** evidence base — 9 sourced studies, each with a
citable url, its actual funding disclosure, and the underlying cohort/trial it rests on (Zhong
2019 JAMA, Drouin-Chartier 2020 BMJ, Dehghan 2020 PURE, Qin 2018 China Kadoorie, Fuller 2018
DIABEGG, Blesso/Fernandez 2013, Carson 2020 AHA, Barnard 2020, Hu 1999). Two findings fall out
of the data, not the curation:

- **Funding skew (real):** the only two **industry-funded** studies (DIABEGG → Australian Egg
  Corporation; Blesso → Egg Nutrition Center) both back *"Context-dependent / safe."* Barnard's
  review quantifies the pattern: 49% of industry-funded egg studies reported conclusions
  discordant with their own data, vs 13% of independent ones.
- **Independence (real):** the US *"No association"* conclusion rests **2 of 4** on the same two
  Harvard cohorts (NHS+HPFS — Hu 1999 and Drouin-Chartier 2020, two decades apart); the
  *"Context-dependent"* camp rests **67%** on feeding trials. Case-wide concentration is a modest
  33% — honestly *far* below COVID's 88% Huanan reliance. The tool doesn't manufacture a
  concentration problem where none exists.

### The update loop — recalculation made visible

```bash
cp cases/eggs.kb.json /tmp/eggs.json
python cli.py add   /tmp/eggs.json cases/eggs.delta-zhuang2021.json   # a real, independent cohort
python cli.py build /tmp/eggs.json cases/covid.kb.json                # rebuild; see the Changes tab
```

The added source is real — Zhuang 2021 (PLOS Medicine, NIH-AARP, 521,120 people, egg/cholesterol
→ higher mortality). It argues *"Increases risk"* and brings a **genuinely independent** cohort,
so the recompute *diversifies* the minority camp rather than padding a count:

```
distribution : Increases risk 2 → 3
concentration: Increases risk 50% → 33%   (a new independent dataset — NIH-AARP)
```

A naive aggregator just logs "+1 source." Here the **independence metric distinguishes
independent evidence from correlated evidence**, and the **Changes tab** records the recompute as
a diff (`viewer/index.html` ships built from exactly this lifecycle).

### Ingest a real link or document

```bash
# With an API key (ANTHROPIC_API_KEY or OPENAI_API_KEY): fully automatic
python cli.py ingest cases/eggs.kb.json https://www.ahajournals.org/doi/10.1161/CIR.0000000000000743 --apply
python cli.py ingest cases/eggs.kb.json ./some-paper.pdf --apply        # PDF / docx / txt too

# Without a key: print the extraction prompt, paste into any LLM, save its JSON, then:
python cli.py ingest cases/eggs.kb.json ./some-paper.pdf --dry-run
python cli.py add    cases/eggs.kb.json delta.json
```

### Cold start — let a scholarly search find the sources

```bash
python cli.py init eggs "Do eggs increase cardiovascular disease risk?" --out cases/eggs.kb.json
python cli.py discover cases/eggs.kb.json --k 10                  # OpenAlex scholarly search — no key
python cli.py discover cases/eggs.kb.json --k 10 --source both --deep   # + exhaustive AI web search
# ...then ingest each returned link with `ingest ... --apply`
```

**Choose your engine** with `--source` (on `discover` and `harvest`): `api` (OpenAlex only, the
default — no key, structured, fast), `web` (LLM web search — finds news/reports/non-indexed
sources too, needs a key), or `both` (run both and merge, deduped). Add `--deep` to the web
search for a **deep-research pass**: the model runs many separate searches across every
position — for/against, primary datasets, reviews, dissents — instead of one quick lookup.

`discover` is **API-first**: it queries [OpenAlex](https://openalex.org) (250M+ works, no key
required — a contact email just speeds you up) and returns real papers with DOIs, so cold start
works even with no LLM key. Each returned link is a DOI/PMID/arXiv id, which `ingest` then
resolves straight back through the same scholarly APIs (OpenAlex → arXiv → Semantic Scholar →
Europe PMC) for a clean abstract + funders — **no publisher scraping, no bot-walls**. Only if a
search returns nothing does it fall back to the LLM web-search path (which needs a key, or
`--dry-run` prints a prompt for any deep-research tool). Set `EPISTEMIC_NO_API=1` to force the
LLM path; `EPISTEMIC_CONTACT_EMAIL`/`SEMANTIC_SCHOLAR_API_KEY` raise rate limits.

## How it generalises across case shapes

Same engine, only the KB JSON differs (`python cli.py show cases/covid.kb.json`):

- **Eggs (real data, malformed question):** funding skew and a modest real NHS/feeding-trial
  concentration (above); and the lone non-crux factor — subgroups, weighted `med` by *all* camps
  because Hu 1999, DIABEGG and the AHA advisory all concede diabetics/hyper-responders differ —
  is the tool flagging that "are eggs healthy?" is mis-posed: the answer is "for whom?"
- **COVID (contested):** five "Zoonosis" sources all rest on Huanan-market data → 100%
  concentration, ≈2.5 independent looks not 5; case-wide Huanan is **88%** of sources vs eggs'
  **33%** — the metric reports, honestly, that COVID's consensus is far more concentrated.
- **Black holes (settled):** distribution collapses to "No risk" (3 of 4 sources), but the
  independence audit shows those rest **100% on one argument** — the cosmic-ray empirical bound
  (≈2.6 independent looks, not 3) — and the lone dissent attacks exactly that. The tool surfaces
  the single load-bearing dependency of even a settled consensus (FLF's ask for this case).

## Two surfaces, one compounding knowledge base

The same engine drives two ways to contribute, so a knowledge base **compounds across people**:

- **The portal** (`python -m app.portal`) — a shared, multi-user site: browse and search
  questions, open the live report, and add sources **with no API key** (find via OpenAlex →
  we fetch the real text → label it in *your own* chatbot via one downloadable file → import).
  The server does **no LLM work and holds no key** — merging is the deterministic stdlib function,
  so the hosted instance is cheap and has no key-trust problem. Stores KBs as JSON documents
  (sqlite locally, Postgres in production — e.g. Railway).
- **The local workstation** (`python cli.py ui`, or the CLI) — for power users with their own
  key: it's **git for knowledge bases**. `pull` a question, `harvest` it automatically with your
  own compute/key, `push` the result back (optimistic version-locked). Discovery and fetch are
  keyless; only labelling uses your key, on your machine.

Both write the same portable `cases/<id>.kb.json` through the same merge. Labelling reads the
**full open-access PDF** when one exists (richer positions, named datasets from the methods, and
the funding/COI statement the abstract omits), with a Crossref funder lookup as backup — so the
independence and funding-skew metrics get real signal, not just abstracts.

## Why this beats off-the-shelf deep research (the baseline)

Deep research answers *this* question *once*, as prose. This produces a **structured artifact**
another team can extend; every number is recomputed by one legible function; it **resists being
gamed by volume**; it **shows what each new source changed**; and it **compounds across
contributors** through the portal. The value isn't a better paragraph — it's a knowledge base
that holds up under motivated reading and gets better as more people add to it.

## Honest limitations

- `cases/eggs.kb.json` is real and sourced (every entry has a citable url + funding disclosure +
  underlying dataset). `cases/covid.kb.json` is still an illustrative seed with empty provenance
  and says so. Positions and factor *weights* are a curator's faithful summary of each camp — the
  reproducible, mechanical parts (which datasets, funding, source counts) are what the metrics run on.
- Entity resolution is normalized-string + alias matching — robust to casing/aliases, not to
  paraphrase ("Wuhan market dataset" vs "Huanan seafood market"). Embedding-match + human
  confirmation on low-confidence merges is the next step.
- Discovery is API-first (OpenAlex), so candidates are real, citable works — no hallucinated
  links. Relevance is tightened by two stance-neutral filters: results must sit in the dispute's
  dominant subject-**topic** cluster (OpenAlex's ML topic classes) *and* mention the question's
  **exposure** term (e.g. "egg"), which drops same-outcome-different-subject papers (a statin
  trial sharing "cardiovascular risk") while keeping *both* sides of the debate. Coverage is
  scholarly (it won't surface news/blog/forum sources an LLM web search can); set
  `EPISTEMIC_LOOSE_SEARCH=1` to relax the filters, or fall back to the LLM web-search path for
  non-indexed sources. Review the candidate list before importing either way.
- Identifier-based fetch returns the **abstract** (plus funders, where listed), not the full
  text — enough to label position/evidence/funding for most papers, but methods-level detail in
  the body is not captured. For full text, ingest the PDF directly.
- The viewer is render-only by design; to see an update you re-run `build`. (A `--watch` mode
  is a small future add.)
