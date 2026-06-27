"""INGESTION orchestration (Layer 1).

  discover(question)  -> deep-research finds candidate sources (links)         [cold start]
  ingest_source(t,kb) -> extract one link/document -> LLM -> a KB delta        [cold start step / update]

The extraction prompt is built from the KB's current entity tables, so the model proposes
links to EXISTING ids (or "NEW:<label>") and the deterministic merge resolves them. The full
human-readable spec for the extraction contract is prompts/ingest.md; discovery is
prompts/discover.md. The templates below mirror those specs.
"""
import json
import os
import re
import sys

from .extract import extract_text
from . import llm

# --- Shared, strengthened extraction contract (one place, used by all three prompts) ---------
# These hints + rules exist to PREVENT entity proliferation: positions, datasets, factors and
# populations multiplying via paraphrase as a case grows. (See the milk case post-mortem.)
_POS_HINT = ('EXISTING POSITIONS — REUSE an id whenever the source argues a stance already '
             'listed, even if worded differently or framed as a mechanism. Add "NEW:<label>" '
             'only for a genuinely distinct claim; keep the whole set small (~3-5):')
_DS_HINT = ('EXISTING DATASETS — the underlying PRIMARY cohorts / trials / biobanks. REUSE an '
            'id when the source\'s data is one of these (a shared cohort under a new name '
            'defeats the independence audit). A review/meta-analysis rests on the cohorts it '
            'POOLS — never on "the literature":')
_FAC_HINT = ('EXISTING FACTORS — reference by EXACT label; add a new factor only for a genuinely '
             'new dimension (do not restate an existing one in new words):')
_EV_HINT = 'EVIDENCE TYPES in use (choose the closest; "NEW:<label>" only if none fit):'
_POP_HINT = ('POPULATIONS in use — the studied GROUP (region / menopausal status / age, or '
             '"Mice" / "Rats" / "In vitro" for non-human studies), NOT the study design. Reuse a '
             'term, prefer broad buckets, or "—" if not population-specific:')
_SRC_HINT = ('SOURCES ALREADY IN THE KB — if THIS source\'s evidence IS one of these (a commentary '
             'on it, or it cites it as its main support), put "SRC:<id>" in restsOn instead of '
             'inventing a dataset. That is how echo and circular citation get caught:')

_RULES = """Rules (apply to each source):
- relevance FIRST: does this source actually bear on THE QUESTION? If it is about a different
  topic (it neither studies the question's subject nor argues a stance on it), set
  "relevant": false and give a one-line "offTopicReason" — and do NOT invent a position, datasets,
  or factors for it. Only label sources that genuinely speak to the question. When in doubt and
  the source is clearly on-topic, set "relevant": true.
- position: the single DIRECTIONAL stance the source takes ON THE QUESTION — an actual answer to it
  (e.g. increases / decreases / no clear effect / it depends). REUSE an existing position whenever
  the source argues a stance already listed, even if worded differently. "NEW:<label>" only for a
  genuinely distinct directional answer; keep the whole set small (~3-5).
  LITMUS: if you cannot phrase it as an answer to the question, it is NOT a position — it is a FACTOR.
  These are FACTORS, never positions, even when the paper is mostly about them: a mechanism / pathway
  ("IGF-1 raises risk", "receptor genetics modulate MI susceptibility"); a biomarker or surrogate
  ("raises LDL"); a subgroup / moderator / susceptibility factor (diabetics, a gene variant); or a
  meta / framing / funding observation ("industry-funded orgs understate the risk"). For ANY of these,
  assign the source the closest existing position its overall direction supports — or the most neutral
  "no clear effect / it depends" camp if it takes no directional stance on the question — and record
  the specific mechanism / biomarker / subgroup / framing angle as a factorWeight. Do NOT mint a new
  position camp for it.
- restsOn: the underlying PRIMARY evidence — named cohorts, trials, or biobanks (e.g. Nurses'
  Health Study, EPIC, a specific RCT). A review or meta-analysis restsOn the cohorts/trials it
  POOLS — NOT "the literature", "studies through <year>", or a label that just describes this
  paper. Use a short proper NAME (e.g. "Finnish Mobile Clinic cohort"), not an id-style slug or
  a name with sample sizes baked in. The same cohort across sources MUST use the SAME label. If
  the cohorts aren't named, list the few largest you can identify, else leave restsOn empty.
  restsOn may ALSO point at ANOTHER SOURCE when this source's case IS that source — a commentary
  whose evidence is one paper, or two pieces citing each other. Write "SRC:<existing source id>"
  (see SOURCES ALREADY IN THE KB) or "NEW-SRC:<exact title>". This is how echo and circular
  citation are detected, so name the real source rather than inventing a vague dataset for it.
- funding: classify the funder from the funding/COI statement into ONE of: Industry, Advocacy,
  Government/public, Nonprofit/charity, Academic/institutional. Use "Undisclosed" if the text
  states no funding/COI — do NOT assume independence when it is silent.
- evidence: the closest EXISTING evidence type above; "NEW:<label>" only if none fit.
- population: the studied GROUP (region, menopausal status, age band) — NOT the study design (that
  is "evidence"). Reuse an existing term; prefer broad buckets; "—" if not population-specific.
  If the study is NOT in humans, say so here: use "Mice", "Rats", "Animal model", or "In vitro /
  cell" — never leave an animal or cell study looking like human evidence; that distinction is the
  whole point of the population tag for a clinical question.
- confidence: the source's OWN stated strength (high/moderate/low/unstated).
- provenance: for position and restsOn, quote ONE COMPLETE verbatim sentence from the text that
  states the actual finding/stance (the direction of the association or the conclusion) +
  extractionConfidence [0,1]. The quote MUST be a whole sentence, not cut off mid-clause (never
  end on "associated with", "compared to", etc.). NEVER use the paper's title, a heading, or the
  search snippet as the quote. If only the title/abstract is available and no sentence states the
  finding, quote the closest complete statement and set extractionConfidence ≤ 0.4 — never the title.
- factorWeights: reuse a factor label VERBATIM (new only for a genuinely new dimension); for each
  factor the source bears on, how strongly its POSITION weights it (high/med/low) + quote +
  one-line rationale.
- Do NOT fabricate. If the text doesn't support a field, omit it or mark low confidence."""

_SCHEMA = ('{"source":{"title":"...","year":2020,"url":"...",\n'
           '"relevant":true,"offTopicReason":"(only if relevant=false)",\n'
           '"position":"pos_id or NEW:Full label",\n'
           '"positionShort":"≤18-char plain summary of the position DIRECTION for the chart bar '
           '(e.g. \'Increases risk\', \'No clear effect\', \'Protective\') — NEVER one study metric/endpoint or jargon",\n'
           '"authors":["Surname, F.","..."]  (copy from the Authors: line if present),\n'
           '"venue":"journal/source name if shown","retracted":false  (true only if the text flags a retraction),\n'
           '"evidence":"...","funding":"independent|industry","population":"...","confidence":"moderate",\n'
           '"restsOn":["ds_id","NEW:Label","SRC:existing_source_id"],"provenance":{"position":{"quote":"...","extractionConfidence":0.8},\n'
           '"restsOn":{"quote":"...","extractionConfidence":0.8}}},\n'
           '"factorWeights":[{"factor":"exact factor label","weight":"high|med|low","quote":"...","rationale":"..."}]}')

EXTRACT_TEMPLATE = (
    "You extract ONE source into a structured delta for an epistemic knowledge base.\n"
    "Output ONLY JSON matching the schema at the end — no prose.\n\n"
    "CASE QUESTION:\n%QUESTION%\n\n"
    + _POS_HINT + "\n%POSITIONS%\n\n"
    + _DS_HINT + "\n%DATASETS%\n\n"
    + _FAC_HINT + "\n%FACTORS%\n\n"
    + _EV_HINT + "\n%EVIDENCE_VOCAB%\n\n"
    + _POP_HINT + "\n%POPULATION_VOCAB%\n\n"
    + _SRC_HINT + "\n%SOURCES_IN_KB%\n\n"
    "SOURCE TO INGEST\ntitle: %TITLE%\nurl: %URL%\ntext:\n%TEXT%\n\n"
    + _RULES + "\n\nJSON schema:\n" + _SCHEMA + "\n")

BATCH_EXTRACT_TEMPLATE = (
    "You extract SEVERAL sources into structured deltas for an epistemic knowledge base in ONE\n"
    "pass. Output ONLY a JSON array — one delta object per source, in the SAME ORDER as the\n"
    "sources are listed below. No prose, no markdown.\n\n"
    "CASE QUESTION:\n%QUESTION%\n\n"
    + _POS_HINT + "\n%POSITIONS%\n\n"
    + _DS_HINT + " Two sources in THIS batch resting on the same cohort MUST use the same id/label.\n%DATASETS%\n\n"
    + _FAC_HINT + "\n%FACTORS%\n\n"
    + _EV_HINT + "\n%EVIDENCE_VOCAB%\n\n"
    + _POP_HINT + "\n%POPULATION_VOCAB%\n\n"
    + _SRC_HINT + "\n%SOURCES_IN_KB%\n\n"
    "SOURCES (%N% — produce exactly one delta per source, in order):\n%SOURCES%\n\n"
    + _RULES + "\n\nReturn a JSON ARRAY of objects, each matching:\n" + _SCHEMA + "\n")

RESEARCH_TEMPLATE = (
    "You are building a structured, balanced evidence base for a research dispute. Do it in ONE\n"
    "pass: FIND real sources, READ each, and EXTRACT it into a structured delta. Output ONLY a\n"
    "JSON array — no prose, no markdown, no code fences.\n\n"
    "CASE QUESTION:\n%QUESTION%\n\n"
    "First decide the SMALL set of distinct positions (~3-5) people actually hold on this\n"
    "question — collapse restatements and mechanisms into the stance they support. Then use web\n"
    "search to find up to %K% real, citable sources, deliberately SPANNING those positions (not\n"
    "just one side). Prefer primary sources: cohort/trial reports, datasets, systematic reviews,\n"
    "guidelines, well-known critical analyses. Verify each URL resolves — do NOT invent links.\n\n"
    "REUSE the existing structure below: link to an existing id/label when it fits; else \"NEW:<label>\".\n\n"
    + _POS_HINT + "\n%POSITIONS%\n\n"
    + _DS_HINT + "\n%DATASETS%\n\n"
    + _FAC_HINT + "\n%FACTORS%\n\n"
    + _EV_HINT + "\n%EVIDENCE_VOCAB%\n\n"
    + _POP_HINT + "\n%POPULATION_VOCAB%\n\n"
    "ALREADY IN THE KB — do NOT re-add these; find DIFFERENT sources:\n%EXISTING_SOURCES%\n\n"
    + _RULES + "\n\nReturn a JSON ARRAY, one object per source, each matching:\n" + _SCHEMA + "\n")

DISCOVER_TEMPLATE = """Find %COUNT% that bear on this research dispute,
spanning the DIFFERENT positions people hold (not just one side). Prefer primary sources:
papers, datasets, judge decisions, debate transcripts, well-known critical analyses.

QUESTION: %QUESTION%

For each source return an object. Output ONLY a JSON array:
[{"title":"...","url":"...","year":2020,"why":"one line: which position/angle it represents"}]
Aim for coverage across positions and evidence types, and flag any you are unsure are real.
"""

_DEEP_DISCOVER = """
DEEP RESEARCH MODE — be exhaustive, not quick. Run MANY separate web searches; do not stop at
the first page. Search at least: (1) each distinct position by name, (2) the strongest evidence
FOR each side, (3) the strongest criticism AGAINST each side, (4) the primary datasets / cohorts
/ trials the debate rests on, (5) systematic reviews and meta-analyses, (6) notable dissenting or
minority views. Prefer primary sources over news/blogs, deduplicate, and verify each URL resolves
before listing it. Return as many high-quality, genuinely distinct sources as you can find."""


def _vocab_options(kb, kind):
    """Canonical terms for evidence/population: the case vocab unioned with values already
    present in sources (so the list is useful even for legacy KBs predating the vocab field)."""
    seen, out = set(), []
    for t in kb.get("vocab", {}).get(kind, []):
        if t["label"] not in seen:
            seen.add(t["label"]); out.append(t["label"])
    for s in kb["sources"]:
        v = s.get(kind)
        if v and v != "—" and v not in seen:
            seen.add(v); out.append(v)
    return "\n".join("  " + v for v in out) or "  (none yet)"


def _entity_tables(kb):
    pos = "\n".join("  {} — {}".format(p["id"], p["label"]) for p in kb["positions"]) or "  (none yet)"
    ds = "\n".join(
        "  {} — {}{}".format(d["id"], d["label"],
                             (" (aliases: " + ", ".join(d["aliases"]) + ")") if d.get("aliases") else "")
        for d in kb["datasets"]) or "  (none yet)"
    fac = "\n".join("  " + f["label"] for f in kb["factors"]) or "  (none yet)"
    return pos, ds, fac


def build_extract_prompt(kb, doc):
    pos, ds, fac = _entity_tables(kb)
    return (EXTRACT_TEMPLATE
            .replace("%QUESTION%", kb["meta"]["question"])
            .replace("%POSITIONS%", pos).replace("%DATASETS%", ds).replace("%FACTORS%", fac)
            .replace("%EVIDENCE_VOCAB%", _vocab_options(kb, "evidence"))
            .replace("%POPULATION_VOCAB%", _vocab_options(kb, "population"))
            .replace("%SOURCES_IN_KB%", _sources_for_ref(kb))
            .replace("%TITLE%", doc.get("title") or "")
            .replace("%URL%", doc.get("url") or "(local document)")
            .replace("%TEXT%", doc["text"]))


def _sources_for_ref(kb, limit=50):
    """List existing sources as 'id — title (year)' so the labeller can cite one via SRC:<id> in
    restsOn (the source->source derivation edge that powers circular-corroboration detection).
    Capped to the most recent `limit` to keep the prompt bounded on large cases."""
    srcs = kb.get("sources", [])[-limit:]
    rows = ["  {} — {}{}".format(s["id"], (s.get("title") or "")[:72],
                                 " ({})".format(s["year"]) if s.get("year") else "")
            for s in srcs]
    return "\n".join(rows) or "  (none yet)"


def _existing_sources(kb):
    lines = ["  - {}{} {}".format(s.get("title") or s["id"],
                                  " ({})".format(s["year"]) if s.get("year") else "",
                                  s.get("url") or "").rstrip()
             for s in kb.get("sources", [])]
    return "\n".join(lines) or "  (none yet)"


def build_research_prompt(kb, k=20):
    """One self-contained prompt that does discovery + extraction together — for pasting into a
    browsing chatbot. The chatbot fetches the pages itself (so no publisher 403s on our side)
    and returns a JSON array of deltas that `cli.py add` ingests directly."""
    pos, ds, fac = _entity_tables(kb)
    return (RESEARCH_TEMPLATE
            .replace("%QUESTION%", kb["meta"]["question"]).replace("%K%", str(k))
            .replace("%POSITIONS%", pos).replace("%DATASETS%", ds).replace("%FACTORS%", fac)
            .replace("%EVIDENCE_VOCAB%", _vocab_options(kb, "evidence"))
            .replace("%POPULATION_VOCAB%", _vocab_options(kb, "population"))
            .replace("%EXISTING_SOURCES%", _existing_sources(kb)))


def build_batch_extract_prompt(kb, docs, max_text=4000):
    """One prompt covering several sources — the KB tables appear once, each source's text is
    trimmed to max_text to fit the budget. Cuts LLM calls ~len(docs)x at some cost in
    per-source extraction depth (long full texts are truncated)."""
    pos, ds, fac = _entity_tables(kb)
    blocks = []
    for n, d in enumerate(docs, 1):
        blocks.append("--- SOURCE {} ---\ntitle: {}\nurl: {}\ntext:\n{}".format(
            n, d.get("title") or "", d.get("url") or "(local document)",
            (d.get("text") or "")[:max_text]))
    return (BATCH_EXTRACT_TEMPLATE
            .replace("%QUESTION%", kb["meta"]["question"])
            .replace("%POSITIONS%", pos).replace("%DATASETS%", ds).replace("%FACTORS%", fac)
            .replace("%EVIDENCE_VOCAB%", _vocab_options(kb, "evidence"))
            .replace("%POPULATION_VOCAB%", _vocab_options(kb, "population"))
            .replace("%SOURCES_IN_KB%", _sources_for_ref(kb))
            .replace("%N%", str(len(docs)))
            .replace("%SOURCES%", "\n\n".join(blocks)))


def ingest_batch(targets, kb, dry_run=False, batch=5, max_text=4000):
    """Fetch and extract MANY sources with FEWER LLM calls: each group of up to `batch`
    sources becomes ONE call returning an array of deltas. Fetch failures are skipped, not
    fatal. Returns the list of deltas; in dry_run it RETURNS the list of combined prompt
    strings (one per group) so the caller can write them to files rather than flood the
    terminal. Entity resolution is still deterministic at merge time, so two sources that
    independently propose "NEW:<same cohort>" collapse onto one dataset."""
    docs = []
    for t in targets:
        try:
            d = extract_text(t)
        except SystemExit as e:
            print("  skipped (fetch failed): {} — {}".format(t, e))
            continue
        docs.append(d)
    if not docs:
        return [] if dry_run else []
    if dry_run:
        return [build_batch_extract_prompt(kb, docs[i:i + batch], max_text)
                for i in range(0, len(docs), batch)]
    deltas = []
    for i in range(0, len(docs), batch):
        group = docs[i:i + batch]
        prompt = build_batch_extract_prompt(kb, group, max_text)
        arr = _parse_json(llm.complete(prompt))
        if isinstance(arr, dict):
            arr = [arr]
        for delta, doc in zip(arr, group):
            _carry_meta(delta, doc)
            deltas.append(delta)
    return None if dry_run else deltas


def _parse_json(raw):
    """Extract the first JSON value (object or array) from a model response. Tolerates
    markdown ```json fences and trailing content after the value (citations, prose, or a
    second value) — web-search responses routinely append such text, which plain json.loads
    rejects as 'Extra data'. raw_decode reads exactly one value and ignores the rest."""
    text = re.sub(r"^\s*```[a-zA-Z]*\n?|```\s*$", "", raw.strip())  # drop code fences
    start = next((i for i, ch in enumerate(text) if ch in "[{"), None)
    if start is None:
        raise SystemExit("Model did not return JSON. First 500 chars:\n" + raw[:500])
    try:
        value, _ = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError as e:
        raise SystemExit("Could not parse model JSON ({}). First 500 chars:\n{}".format(e, raw[:500]))
    return value


def _carry_meta(delta, doc):
    """Copy fetch-derived metadata onto the delta's source when the labeller didn't supply it —
    so url/title/authors/venue/citations/retraction are captured deterministically from the API,
    not left to the model."""
    src = delta.setdefault("source", {})
    for k in ("url", "title", "authors", "venue"):
        if doc.get(k) and not src.get(k):
            src[k] = doc[k]
    if doc.get("citations") is not None and src.get("citations") is None:
        src["citations"] = doc["citations"]
    if "retracted" in doc and "retracted" not in src:
        src["retracted"] = doc["retracted"]


def ingest_source(target, kb, dry_run=False):
    """Extract one link/document into a delta. In dry_run, RETURNS the prompt string (the
    caller writes it to a file) instead of a delta."""
    doc = extract_text(target)
    prompt = build_extract_prompt(kb, doc)
    if dry_run:
        return prompt
    delta = _parse_json(llm.complete(prompt))
    _carry_meta(delta, doc)
    return delta


def build_discover_prompt(question, k=8, deep=False, exclude=None):
    try:
        k = int(k)
    except (TypeError, ValueError):
        k = 0
    count = ("as many real, citable sources as you can find" if k <= 0
             else "up to {} real, citable sources".format(k))   # k<=0 -> no limit
    prompt = DISCOVER_TEMPLATE.replace("%COUNT%", count).replace("%QUESTION%", question)
    if exclude:
        have = "\n".join("  - " + t for t in exclude[:250] if t)
        if have:
            prompt += ("\n\nALREADY IN THE KNOWLEDGE BASE — do NOT return any of these; find "
                       "DIFFERENT, genuinely new sources:\n" + have + "\n")
    return prompt + (_DEEP_DISCOVER if deep else "")


def _dedupe_title(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def discover(question, k=8, dry_run=False, source="web", deep=False, exclude=None):
    """Discover candidate sources for a question. `source` chooses the engine(s):

      "api"  — OpenAlex scholarly search only (no key, structured, fast).
      "web"  — LLM web search only (needs a key; deep=True = exhaustive multi-search pass).
      "both" — run both and MERGE, deduped by title (API hits listed first, web fills the gaps).

    The API part never needs a key; the web part does (or prints a paste prompt in dry_run). If
    "api" yields nothing we fall back to web, so cold start stays robust. Returns a candidate
    list; in a web-only dry_run with nothing else to show, returns None after printing the prompt.
    """
    source = (source or "api").lower()
    want_api = source in ("api", "both") and not os.environ.get("EPISTEMIC_NO_API")
    want_web = source in ("web", "both")

    out, seen = [], set()
    seen |= {_dedupe_title(t) for t in (exclude or []) if t}   # never re-surface what we already have

    def _merge(cands):
        for c in cands or []:
            if not isinstance(c, dict) or not c.get("url"):
                continue
            key = _dedupe_title(c.get("title")) or c.get("url")
            if key in seen:
                continue
            seen.add(key)
            out.append(c)

    if want_api:
        try:
            from ingest.search import search_openalex
            api_cands = search_openalex(question, k if (k and k > 0) else 200)  # k<=0 -> wide pool
        except Exception:
            api_cands = []
        if api_cands:
            print("Found {} source(s) via OpenAlex.".format(len(api_cands)), file=sys.stderr)
            _merge(api_cands)
        elif source == "api":
            want_web = True  # api came up empty — fall back to web so cold start still works
            print("OpenAlex returned nothing; falling back to LLM web search.", file=sys.stderr)

    if want_web:
        prompt = build_discover_prompt(question, k, deep=deep, exclude=exclude)
        if dry_run:
            print(prompt)
            print("\n# ---- paste the model's JSON array of sources here; ingest each with:")
            print("#      python cli.py ingest <kb.json> <url> --apply")
            return out or None
        try:
            print("Searching the web via {}{}…".format(
                llm.active_model(), " (deep research)" if deep else ""), file=sys.stderr)
            web_cands = _parse_json(llm.discover(prompt, deep=deep))
            before = len(out)
            _merge(web_cands)
            print("Web search added {} new source(s).".format(len(out) - before), file=sys.stderr)
        except SystemExit as e:
            if not out:
                raise
            print("Web search failed ({}); keeping API results.".format(e), file=sys.stderr)

    return out


def fetch_docs(targets):
    """Fetch real text for each URL/path (urllib + reader-proxy fallback in extract.py).
    Returns (docs, skipped) — skipped lists what couldn't be fetched, so the caller can report
    it honestly rather than letting the model guess at unreachable content."""
    docs, skipped = [], []
    for t in targets:
        try:
            docs.append(extract_text(t))
        except (SystemExit, Exception) as e:  # block, SSL, 404, bad path, missing dep — all skippable
            skipped.append({"target": t, "error": str(e)})
    return docs, skipped


def extract_prompts(kb, docs, batch=5, max_text=4000):
    """Build the grounded extraction prompt(s) over already-fetched docs (real text embedded)."""
    return [build_batch_extract_prompt(kb, docs[i:i + batch], max_text)
            for i in range(0, len(docs), batch)]


def deltas_from_docs(kb, docs, batch=5, max_text=4000):
    """AUTO path: extract deltas from already-fetched docs via the LLM (one call per batch)."""
    deltas = []
    for i in range(0, len(docs), batch):
        group = docs[i:i + batch]
        arr = _parse_json(llm.complete(build_batch_extract_prompt(kb, group, max_text)))
        if isinstance(arr, dict):
            arr = [arr]
        for delta, doc in zip(arr, group):
            _carry_meta(delta, doc)
            deltas.append(delta)
    return deltas
