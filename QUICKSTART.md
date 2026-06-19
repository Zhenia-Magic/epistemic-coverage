# Quickstart — common tasks, step by step

> **Prefer clicking to typing?** Run `python cli.py ui` for a small web console that does all of
> this in the browser: pick a question, **Find** sources, **Fetch & label** (we download the real
> article text and the AI only labels what we fetched — it can't invent contents; unreachable
> pages are skipped, not guessed), then **Import**. It also adds a single source from a URL or an
> uploaded PDF/Word file. The CLI steps below are the same operations.

For everything else see `WORKFLOW.md`. This file covers the things you'll do most:
**(A) generate a new report**, **(B) add a source to an existing one**, and **(C) clean it up**
(merge duplicates, tidy labels).

All commands run from the repo root. Replace `<...>` placeholders with your own values.

---

## First, which mode are you in?

**Finding and reading sources no longer needs an LLM.** Discovery uses a scholarly search
(OpenAlex) and reading resolves papers by their DOI/PMID/arXiv id through open APIs — both work
with **no key**. The LLM is needed only to **label** what's fetched (assign position, evidence,
funding, factor weights). Check once whether you have a key for that step:

```bash
echo $ANTHROPIC_API_KEY      # or:  echo $OPENAI_API_KEY
```

- **Prints a key →** you're in **AUTO mode**: the tool labels for you, end to end.
- **Prints nothing →** you're in **MANUAL mode**: the tool still *finds and fetches* sources for
  free, then prints a labelling prompt you paste into any chatbot and feed back. No account needed.

(Optional, one time, for PDF/Word sources: `pip install -r requirements.txt`.)

---

## A. Generate a NEW report (new question)

### Step 1 — create an empty knowledge base

```bash
python cli.py init <id> "<your question>" --out cases/<id>.kb.json
```

Example:

```bash
python cli.py init salt "Does dietary salt raise blood pressure and cardiovascular risk?" --out cases/salt.kb.json
```

You now have an empty `cases/salt.kb.json`. ✔

### Step 2 — fill it with sources

**Simplest — one prompt, one paste (works with any browsing chatbot):**

```bash
python cli.py research cases/salt.kb.json --k 20
```

This writes a single self-contained prompt to `out/research-prompt.txt`. Open it, paste the whole
thing into **Claude or ChatGPT with web search on**, and it will find ~20 sources across the
positions, read them, and return **one JSON array** already labelled with positions, datasets,
funding, evidence type, and factor weights. The chatbot does the browsing, so publisher blocks
aren't your problem. Save what it returns as `sources.json`, then:

```bash
python cli.py add cases/salt.kb.json sources.json --build
```

`add` prints **WHAT CHANGED** per source. The prompt embeds your current KB (positions, datasets,
vocab) so the chatbot reuses your ids, and lists existing sources so it won't re-add them — re-run
`research` anytime to grow the case. **Skip to Step 4.**

> **Have an API key?** One command does the whole thing: `python cli.py research cases/salt.kb.json --k 15 --apply --build`.
> (One LLM call with web search; use a smaller `--k` if the output truncates.)

<details><summary><b>Other ways to fill it</b> (scholarly search + auto harvest, or fetch-it-yourself batches)</summary>

**`harvest`** — the API-backed cold start: search OpenAlex for sources, fetch each by identifier
(no scraping), then label. Fully automatic with an API key:
```bash
python cli.py harvest cases/salt.kb.json --k 10 --build      # --batch 5 = fewer LLM calls
```

**`discover` → `ingest-batch`** — split the steps; `discover` is the OpenAlex search (needs **no
key**), so even in manual mode you get real candidate papers to fetch and label:
```bash
python cli.py discover cases/salt.kb.json --k 10 > candidates.json    # scholarly search, real DOIs
python cli.py ingest-batch cases/salt.kb.json --from candidates.json --dry-run   # writes prompt file(s)
python cli.py add cases/salt.kb.json deltas.json --build          # add accepts one delta or an array
```
Add `--apply` to `ingest-batch` (with a key) to skip the paste loop entirely. `discover` resolves
DOI/PMID/arXiv links through OpenAlex → arXiv → Semantic Scholar → Europe PMC. Candidates are
filtered to the dispute's subject topic + exposure term so they stay on-point (both sides of the
debate are kept); if a niche question returns too few, set `EPISTEMIC_LOOSE_SEARCH=1` to relax it.

**Pick where to search** with `--source`: `api` (OpenAlex only — default, no key), `web` (AI web
search — also finds news/reports an index misses, needs a key), or `both` (merge the two). Add
`--deep` to make the AI web search exhaustive — many searches across every position, for/against,
primary datasets, reviews — rather than one quick pass:

```bash
python cli.py discover cases/salt.kb.json --k 15 --source both --deep > candidates.json
```

The same **Where to look** dropdown and **Deep research** checkbox appear in the UI's Find and
Do-it-all panels.

</details>

### Step 3 — (only if you didn't pass `--build`) bake the viewer

```bash
python cli.py build cases/salt.kb.json
```

### Step 4 — open and read it

```bash
open viewer/index.html        # macOS — or just double-click the file
```

Four tabs: **Coverage** (who holds what + funding skew), **Divergence** (the cruxes),
**Independence** (is the consensus real?), **Changes** (history). Prefer the terminal?
`python cli.py show cases/salt.kb.json`.

**Tip — show several disputes in one viewer with a case switcher:**

```bash
python cli.py build cases/salt.kb.json cases/eggs.kb.json cases/covid.kb.json
```

✔ **Done** when `viewer/index.html` opens and your question's tabs are populated.

---

## B. Add a source to an EXISTING report

You have `cases/<id>.kb.json` and a new source — either a **URL** or a **file** (`.pdf`, `.docx`,
`.html`, `.txt`).

**AUTO mode — one command:**

```bash
python cli.py ingest cases/<id>.kb.json "<url-or-path>" --apply --build
```

Examples:

```bash
python cli.py ingest cases/eggs.kb.json "https://www.bmj.com/content/368/bmj.m513" --apply --build
python cli.py ingest cases/eggs.kb.json "./papers/new-study.pdf" --apply --build
```

**MANUAL mode — two steps:**

1. Print the extraction prompt for the source, paste into any chatbot, save its JSON as `delta.json`:
   ```bash
   python cli.py ingest cases/<id>.kb.json "<url-or-path>" --dry-run
   ```
2. Merge it and rebuild:
   ```bash
   python cli.py add cases/<id>.kb.json delta.json --build
   ```

Either way you'll see **WHAT CHANGED** in the terminal, e.g.:

```
WHAT CHANGED
  distribution: Increases risk 2 → 3
  concentration: Increases risk 50% → 33% (top: Lifetime Risk Pooling Project)
```

Refresh `viewer/index.html` and check the **Changes** tab — your source is now in the history. ✔

> A duplicate source (same url, or same title + year) is refused automatically — that's the
> anti-flooding guard, not an error.

---

## C. Clean up a report (merge duplicates, tidy labels)

As a case grows, the AI sometimes coins near-duplicate entities (e.g. three "Women (mixed …)"
populations) or ugly slug labels (`UK_Biobank_206263_women_aged_40_69`). Fix them deterministically
— every metric recomputes.

**In the UI:** open the **Curate** panel → **Load entities**. It lists everything with usage
counts and flags **possible duplicates** (one-click Merge keeps the more-used label). Use **Tidy
slug labels** to clean ugly names, or the manual Merge / Rename controls for anything else.

**In the CLI:**

```bash
python cli.py dups   cases/<id>.kb.json                          # list likely duplicates
python cli.py merge  cases/<id>.kb.json population "Women (diverse…)" "Women (mixed menopausal status)"
python cli.py rename cases/<id>.kb.json dataset "UK_Biobank_206263_women_aged_40_69" "UK Biobank"
python cli.py tidy   cases/<id>.kb.json --build                  # prettify all slug labels at once
```

`<type>` is `position` / `dataset` / `factor` / `evidence` / `population`; refs accept an id,
exact label, or unique substring. Merges learn the folded name as an alias, so future ingests
still resolve to it. ✔

> **Funding & metric notes.** Funding is a fixed set — *Government/public, Nonprofit/charity,
> Academic/institutional, Industry, Advocacy, Undisclosed* — and **defaults to Undisclosed** (the
> tool never assumes "independent"). The Coverage banner flags interested (industry/advocacy)
> money *and* the share of sources that don't disclose. In the Divergence matrix, factors only one
> camp weighs are dimmed as "one side only" — the bold rows are the real cruxes.

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| `No ANTHROPIC_API_KEY or OPENAI_API_KEY set` | You're in MANUAL mode — add `--dry-run` and paste the prompt into any chatbot. Or `export ANTHROPIC_API_KEY=...`. |
| `could not fetch … (HTTP Error 403)` | Only happens for non-academic links now. Academic links (DOI / PubMed / arXiv) are fetched by identifier through open APIs (OpenAlex / arXiv / Semantic Scholar / Europe PMC), which don't bot-wall. For other pages the tool retries through a reader proxy (`r.jina.ai`); if that also fails the source is **skipped**, not fatal. Disable APIs with `EPISTEMIC_NO_API=1`, the proxy with `EPISTEMIC_NO_READER=1`. For a hard paywall, ingest the PDF: `ingest <kb> ./paper.pdf`. |
| `could not fetch … (CERTIFICATE_VERIFY_FAILED)` | Your Python lacks CA certs (common on macOS). Run `/Applications/Python\ 3.x/Install\ Certificates.command`. |
| Dry-run output floods the terminal | It no longer does — `--dry-run` writes each prompt to `out/ingest-prompt*.txt` and prints only the paths. Open those files to copy the prompt. |
| `PDF support needs pypdf` / `DOCX support needs python-docx` | `pip install -r requirements.txt` |
| "Duplicate source — not added" | Expected: that study (by url, or title+year) is already in the KB. |
| Viewer didn't change after `add` | You forgot to rebuild — re-run with `--build`, or `python cli.py build cases/<id>.kb.json`. |
| Model returned prose, not JSON | Re-paste; the prompt ends with "Output ONLY JSON." Trim any extra text before saving `delta.json`. |
| A source got a wrong position/dataset | `cases/<id>.kb.json` is plain JSON — edit the entry by hand, then `python cli.py build ...`. |
