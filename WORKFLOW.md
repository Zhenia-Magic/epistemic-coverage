# WORKFLOW — how to use the tool

A practical runbook. Pick the section that matches what you want to do. Every command below
is real and runnable from the repo root.

---

## 0. One-time setup

- **Python 3.10+.** The core (build a viewer, read metrics, merge sources, `--dry-run`
  ingestion) needs **no packages**.
- **Optional — document ingestion:** `pip install -r requirements.txt` (only for `.pdf` / `.docx`).
- **Finding and reading sources need no key.** Discovery is a scholarly search (OpenAlex) and
  reading resolves a paper by its DOI/PMID/arXiv id through open APIs — both keyless and free of
  publisher scraping. A key is only needed to **label** the fetched text automatically.
- **Optional — automatic labelling:** set one API key. Without it you use the *manual path* (the
  tool finds + fetches sources for free, then prints a labelling prompt to paste into any LLM).

  ```bash
  export ANTHROPIC_API_KEY=sk-...     # Claude (recommended), or
  export OPENAI_API_KEY=sk-...        # OpenAI
  # export EPISTEMIC_MODEL=claude-opus-4-8       # optional model override
  # export EPISTEMIC_CONTACT_EMAIL=you@org       # faster OpenAlex "polite pool"
  # export EPISTEMIC_NO_API=1                     # force the LLM web-search path instead
  ```

**Two modes — know which you're in:**

| | With an API key | Without a key |
|---|---|---|
| Find sources | `harvest` / `discover` — OpenAlex scholarly search | **same** — `discover` works keyless |
| Read a source | by identifier via OpenAlex/arXiv/etc — **same** | **same** — fetch is keyless |
| Label a source | `ingest <link/doc> --apply` is automatic | `ingest --dry-run` prints a prompt; you paste, save JSON, `add` it |
| Everything else | identical | identical |

The mechanical layers (merge, metrics, viewer) are **always** deterministic and offline; finding
and reading are keyless API calls. Only *labelling* a fetched source uses an LLM.

---

## 1. Mental model (the whole tool in one picture)

```
                    you pick a question
                            │
          ┌─────────────────▼─────────────────┐
          │   discover → OpenAlex finds papers   │   Ingestion
          │   fetch    → API reads by identifier │   (search·API, fetch·API,
          │   label    → LLM tags the text       │    label·LLM)
          └─────────────────┬─────────────────┘
                            │  delta (one structured source)
                            ▼
                  merge  →  cases/<id>.kb.json      ← the artifact you own & share
                  (entity resolution; dedupe)        (one JSON file)
                            │
                            ▼
                  assess → every metric, recomputed (deterministic)
                            │
                            ▼
                  build  →  viewer/index.html         ← self-contained; double-click to open
```

**`cases/<id>.kb.json` is the thing you own.** Commands read and update it; the viewer is just a
baked snapshot of it. Cold-start and "add one source later" are the *same* action repeated.

---

## A. Look at what's already here (30 seconds)

```bash
python cli.py show  cases/eggs.kb.json                      # metrics in the terminal
python cli.py build cases/eggs.kb.json cases/covid.kb.json  # bake the viewer
open viewer/index.html                                      # or double-click it
```

The viewer has four tabs — Coverage, Divergence, Independence, Changes (see §D).

---

## B. Start a NEW question from scratch (cold start)

```bash
python cli.py init eggs "Do eggs increase cardiovascular disease risk?" --out cases/eggs.kb.json
```

Then populate it. **With an API key — one command:**

```bash
python cli.py harvest cases/eggs.kb.json --k 10 --build
```

`harvest` = OpenAlex finds candidate papers → fetch each by identifier (no scraping) → label +
merge → rebuild the viewer. It's resumable: re-run it and already-known sources are skipped.

**Without a key — the manual path (finding + fetching are still automatic):**

```bash
python cli.py discover cases/eggs.kb.json --k 10 > candidates.json   # 1. OpenAlex search, no key
#    (real papers with DOIs — no chatbot needed for this step)
python cli.py ingest cases/eggs.kb.json <link> --dry-run     # 2. per link: fetches text, prints a labelling prompt
#    paste that → save the JSON it returns as delta.json
python cli.py add cases/eggs.kb.json delta.json --build      # 3. merge it + rebuild
#    repeat 2–3 for each link
```

---

## C. Add a source you already have (a link OR a document)

**With a key (automatic):**

```bash
python cli.py ingest cases/eggs.kb.json https://www.bmj.com/content/368/bmj.m513 --apply --build
python cli.py ingest cases/eggs.kb.json ./paper.pdf  --apply --build     # PDF / docx / html / txt
```

**Without a key (manual):**

```bash
python cli.py ingest cases/eggs.kb.json ./paper.pdf --dry-run   # prints the extraction prompt
#   paste into any LLM → save its JSON as delta.json
python cli.py add cases/eggs.kb.json delta.json --build
```

Either way the terminal prints **WHAT CHANGED** (which metrics moved), and it's appended to the
viewer's **Changes** tab. Duplicate sources (same url, or title+year) are refused automatically.

---

## D. Read the outputs — what each view answers

| View | Question it answers | Watch for |
|------|--------------------|-----------|
| **Coverage** | Who holds which position? Where's the industry money? | the **funding-skew** banner — which camp industry funding favours |
| **Divergence** | What do the camps *actually* disagree on? | rows badged **CRUX** (spread ≥2); the rest is hidden agreement |
| **Independence** | Is a consensus real, or the same data counted twice? | a camp marked **CONCENTRATED** + the case-wide most-reused dataset |
| **Changes** | What did each new source do to the picture? | concentration/​distribution shifts, new cruxes, blindspots opening/closing |

Reading rules of thumb:
- **High concentration = weak consensus.** "5 sources, 80% on one dataset" ≈ ~2 independent looks.
  Adding *correlated* sources makes this **worse**, not better — by design.
- **Funding skew** is a flag to weigh, not a verdict: it tells you where to apply scrutiny.
- **Few cruxes** is the normal, healthy finding: most disagreement is local.

Prefer the terminal? `python cli.py show <kb>` prints the same summary;
`python cli.py assess <kb>` dumps the full metrics as JSON (for scripting / diffing).

---

## E. Share it / hand it off

- **For a person:** send `viewer/index.html` — it's self-contained, opens with a double-click,
  no server or internet needed.
- **For another team to extend:** send `cases/<id>.kb.json` — the portable artifact. They run
  `build` to view it, and `ingest`/`add` to keep growing it. Nothing is locked in the UI.

---

## Command reference

| Command | What it does |
|---------|-------------|
| `init <id> "<question>" --out cases/<id>.kb.json` | create an empty knowledge base |
| `harvest <kb> [--k N] [--source api\|web\|both] [--deep] [--build]` | **cold start in one go** (key for labelling): search → fetch all → label → build |
| `discover <kb> [--k N] [--source api\|web\|both] [--deep] [--dry-run]` | find candidates: OpenAlex (default, no key), AI web search, or both; `--deep` = exhaustive web pass; `> file.json` to save |
| `ingest <kb> <link-or-file> [--apply] [--build] [--dry-run]` | fetch one source (by identifier, no scraping) → label → delta (→ merge → build) |
| `add <kb> <delta.json> [--build]` | merge a delta you already have; prints WHAT CHANGED |
| `show <kb>` | metrics summary in the terminal |
| `assess <kb>` | full metrics as JSON |
| `build <kb> [<kb2> ...] [--out FILE]` | bake the viewer (multiple KBs ⇒ a case switcher) |

## Recipes

- **See an update in the viewer:** add `--build` to any `add`/`ingest`, or re-run `build`.
- **Resume an interrupted harvest:** just run `harvest` again — done sources are skipped.
- **Curate by hand:** `cases/<id>.kb.json` is plain JSON. Edit it, then `build`. (Keep `restsOn`
  pointing at real dataset ids; see `SCHEMA.md`.)
- **One viewer, several disputes:** `build cases/eggs.kb.json cases/covid.kb.json` → switcher.
