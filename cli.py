#!/usr/bin/env python3
"""epistemic -- CLI orchestrator tying the three layers together.

  INGESTION (ingest/)   discover sources or turn one link/document into a delta
  STRUCTURE (engine/merge.py)   fold the delta into the KB (entity resolution)
  ASSESSMENT (engine/assess.py) recompute every metric, deterministically
  DIFF + BUILD          report what changed; bake a self-contained viewer

Cold start = `discover` then `ingest` looped over the results.  An update = one `ingest`
(of a link or a PDF/docx).  Same path either way -- that is what makes the KB "living".

  python cli.py init <id> "<question>"            > cases/<id>.kb.json
  python cli.py discover <kb.json> [--k N] [--dry-run]
  python cli.py ingest  <kb.json> <link-or-file> [--dry-run] [--apply]
  python cli.py add     <kb.json> <delta.json>      # merge + recompute + diff (in place)
  python cli.py show    <kb.json>
  python cli.py assess  <kb.json>
  python cli.py build   <kb.json> [<kb2.json> ...] [--out viewer/index.html]
"""
import argparse
import json
import os
import sys

from engine.assess import assess, diff_assessments
from engine.merge import merge_delta
from engine.render import json_for_script
from engine.schema import empty_kb

ROOT = os.path.dirname(os.path.abspath(__file__))


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(p, o):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(o, f, indent=2, ensure_ascii=False)
        f.write("\n")


def pct(x):
    return str(round(x * 100)) + "%"


def pad(s, n):
    s = str(s)
    return s if len(s) >= n else s + " " * (n - len(s))


# ---------------------------------------------------------------- show
def cmd_show(args):
    kb = read_json(args.kb)
    a = assess(kb)
    L = ["# {}   (v{})".format(kb["meta"]["question"], kb["meta"]["version"]),
         "{} sources · {} positions · {} factors · {} datasets".format(
             len(kb["sources"]), len(kb["positions"]), len(kb["factors"]), len(kb["datasets"])),
         "", "DISTRIBUTION"]
    for d in a["distribution"]:
        L.append("  " + pad(d["label"], 22) + pad(d["count"], 3) + "  " + str(d["pct"]) + "%")
    if a["fundingSkew"]:
        L.append('  funding skew: industry money most backs "{}" ({} industry sources)'.format(
            a["fundingSkew"]["top"]["label"], a["fundingSkew"]["n"]))
    L += ["", "INDEPENDENCE  (concentration on single most-reused dataset)"]
    for p in a["independence"]:
        t = "{} {}/{}".format(p["topDataset"]["label"], p["topDataset"]["count"], p["raw"]) \
            if p["topDataset"] else "—"
        L.append("  " + pad(p["label"], 22) + pad(pct(p["concentration"]), 5) + " on " +
                 pad(t, 34) + " nEff≈{:.1f}".format(p["nEff"]) +
                 ("   [CONCENTRATED]" if p["concentrated"] else ""))
    if a["dominantDataset"]:
        dd = a["dominantDataset"]
        L.append("  most reused case-wide: {} — {}/{} ({})".format(
            " / ".join(dd["labels"]), dd["count"], dd["total"], pct(dd["share"])))
    if a["worstConcentration"]:
        w = a["worstConcentration"]
        L.append('\n  ⚠ worst: "{}" lists {} sources but rests {} on {} — ≈{:.1f} independent '
                 'looks, not {}.'.format(w["label"], w["raw"], pct(w["concentration"]),
                                         w["topDataset"]["label"], w["nEff"], w["raw"]))
    L += ["", "CRUXES  (● crux = spread ≥2 ; factors only one camp weighs are [1-sided])"]
    order = sorted(a["cruxes"], key=lambda c: (0 if c["isCrux"] else (1 if c["engaged"] >= 2 else 2),
                                               -c["spread"]))
    for c in order:
        one = c["engaged"] < 2
        mark = "●" if c["isCrux"] else (" " if one else "·")
        L.append("  " + mark + " " + c["label"] +
                 "  (spread {}{})".format(c["spread"], "  [1-sided]" if one else ""))
    L += ["", "BLINDSPOTS"]
    for p in a["blindspots"]:
        miss = p["missingEvidence"] + p["missingPop"]
        L.append("  " + pad(p["label"], 22) +
                 ("skips: " + ", ".join(miss) if miss else "covers every type & subgroup"))
    print("\n".join(L))


# ---------------------------------------------------------------- assess
def cmd_assess(args):
    print(json.dumps(assess(read_json(args.kb)), indent=2, ensure_ascii=False))


def cmd_gaps(args):
    """Show where this KB's evidence is thin — the steering wheel for gap-driven deep search."""
    from engine.gaps import find_gaps, gap_queries
    kb = read_json(args.kb)
    queries = gap_queries(kb, find_gaps(kb))
    if args.json:
        print(json.dumps(queries, indent=2, ensure_ascii=False))
        return
    if not queries:
        print("No gaps found — every position rests on independent primary evidence.")
        return
    print("{} gap(s) — aim the next search here:\n".format(len(queries)))
    icon = {3: "!!", 2: "! ", 1: "  "}
    for q in queries:
        g = q["gap"]
        print("  [{}] {:18} {}".format(icon.get(g["severity"], "  "), g["kind"], g["why"]))
        print("       search: {}".format(q["query"]))


# ---------------------------------------------------------------- add
def _apply_delta(kb_path, delta):
    kb = read_json(kb_path)
    before = assess(kb)
    report = merge_delta(kb, delta)
    if report.get("offTopic"):
        print("Off-topic — not added: {}".format(report.get("reason", "doesn't bear on the question")))
        return False
    if report["duplicate"]:
        print("Duplicate source — already in KB, not added. (anti-flooding)")
        return False
    after = assess(kb)
    lines = diff_assessments(before, after)
    kb["log"][-1]["diff"] = lines  # persist the diff so the viewer can show a changelog
    write_json(kb_path, kb)
    print("Added {}  (KB now v{})".format(report["addedSource"], kb["meta"]["version"]))
    for k, lbl in (("newDatasets", "new datasets"), ("newPositions", "new positions"),
                   ("newFactors", "new factors")):
        if report[k]:
            print("  {}: {}".format(lbl, ", ".join(report[k])))
    print("\nWHAT CHANGED")
    print("\n".join("  " + x for x in lines) if lines else "  (no metric changes)")
    return True


def _merge_deltas(kb_path, deltas):
    """Merge a list of deltas one at a time (each recomputes + diffs against the prior KB)."""
    added = 0
    for d in deltas:
        if _apply_delta(kb_path, d):
            added += 1
        print("")
    return added


# ---------------------------------------------------------------- curate (merge / rename)
def _curate_write(args, report):
    print(report["summary"] + "  (KB now v{})".format(report["version"]))
    if getattr(args, "build", False):
        _build_viewer([args.kb])


def cmd_merge(args):
    from engine import curate
    kb = read_json(args.kb)
    if args.type in ("position", "dataset", "factor"):
        fn = {"position": curate.merge_positions, "dataset": curate.merge_datasets,
              "factor": curate.merge_factors}[args.type]
        report = fn(kb, args.src, args.dst)
    else:
        report = curate.merge_vocab(kb, args.type, args.src, args.dst)
    write_json(args.kb, kb)
    _curate_write(args, report)


def cmd_rename(args):
    from engine import curate
    kb = read_json(args.kb)
    report = curate.rename(kb, args.type, args.ref, args.label)
    write_json(args.kb, kb)
    _curate_write(args, report)


def cmd_tidy(args):
    from engine import curate
    kb = read_json(args.kb)
    report = curate.tidy_labels(kb)
    write_json(args.kb, kb)
    _curate_write(args, report)


def cmd_dups(args):
    from engine import curate
    sug = curate.suggest_duplicates(read_json(args.kb), threshold=args.threshold)
    if not sug:
        print("No likely duplicates above threshold {}.".format(args.threshold))
        return
    for kind, pairs in sug.items():
        print("\n{}:".format(kind.upper()))
        for p in pairs:
            print('  {:.2f}  "{}"  ⇄  "{}"'.format(p["sim"], p["a"]["label"], p["b"]["label"]))
    print('\nMerge with:  python cli.py merge <kb> <type> "<source label>" "<target label>"')


def cmd_add(args):
    data = read_json(args.delta)
    deltas = data if isinstance(data, list) else [data]  # accept one delta or a batch array
    if len(deltas) == 1:
        _apply_delta(args.kb, deltas[0])
    else:
        print("Merging {} deltas from {}\n".format(len(deltas), args.delta))
        added = _merge_deltas(args.kb, deltas)
        print("Done: {} of {} added.".format(added, len(deltas)))
    if getattr(args, "build", False):
        _build_viewer([args.kb])


# ---------------------------------------------------------------- init
def cmd_init(args):
    kb = empty_kb(args.id, args.question)
    if args.out:
        write_json(args.out, kb)
        print("wrote " + args.out)
    else:
        print(json.dumps(kb, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- build (viewer)
def _build_viewer(kb_paths, out=None):
    cases, order = {}, []
    for p in kb_paths:
        kb = read_json(p)
        cid = kb["meta"]["id"]
        cases[cid] = {"kb": kb, "assessment": assess(kb)}
        order.append(cid)
    bundle = {"order": order, "cases": cases}
    with open(os.path.join(ROOT, "viewer", "template.html"), encoding="utf-8") as f:
        tpl = f.read()
    html = tpl.replace("/*__DATA__*/null", json_for_script(bundle))
    out = out or os.path.join(ROOT, "viewer", "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("Built {} ({} case{}) — open it in a browser.".format(
        out, len(order), "" if len(order) == 1 else "s"))


def cmd_build(args):
    _build_viewer(args.kb, args.out)


# ---------------------------------------------------------------- portal sync (push / pull)
# Git-like: do the LLM work locally with your own env key, then push the structured result to a
# shared portal. The portal does NO LLM work and holds no key — see app/portal.py.

def cmd_pull(args):
    """Fetch a question's canonical KB from the portal into a local file, stamping its lineage
    (meta.portal = {id, baseVersion}) so a later `push` knows what it's updating."""
    from app import client
    base = client.portal_url(args.portal)
    data = client.get_question(base, args.id)
    kb = data["kb"]
    kb.setdefault("meta", {})["portal"] = {"id": data["id"], "baseVersion": data["version"], "url": base}
    out = args.out or "cases/{}.kb.json".format(data["id"])
    write_json(out, kb)
    print("Pulled '{}' (v{}) -> {}".format(data["question"], data["version"], out))


def cmd_push(args):
    """Push a local KB to the portal. Creates a new question if the file has no portal lineage,
    otherwise updates it (optimistic version check on the baseVersion we pulled)."""
    from app import client
    base = client.portal_url(args.portal)
    token = client.admin_token(args.token)
    kb = read_json(args.kb)
    who = args.as_ or "anonymous"
    portal_meta = (kb.get("meta") or {}).get("portal") or {}
    qid, expected = portal_meta.get("id"), portal_meta.get("baseVersion", 0)
    if not qid:                                   # first push -> create the question, then upload
        created = client.create_question(base, kb["meta"]["question"], who)
        qid, expected = created["id"], 0
        print("Created question {} on the portal.".format(qid))
    res = client.put_kb(base, qid, kb, expected, who, token=token)
    kb["meta"]["portal"] = {"id": qid, "baseVersion": res["version"], "url": base}
    write_json(args.kb, kb)                       # restamp lineage so the next push is clean
    print("Pushed '{}' -> {} (now v{}).".format(kb["meta"]["question"], qid, res["version"]))


def cmd_new(args):
    """Create a new question LOCALLY (git-style): a local case file you can harvest/deepen, then
    `push` to the portal when ready. The id is derived from the question unless you pass --id."""
    import uuid
    qid = args.id or uuid.uuid4().hex[:12]        # same id format the portal mints
    out = args.out or "cases/{}.kb.json".format(qid)
    if os.path.exists(out) and not args.force:
        raise SystemExit("{} already exists — pass --id, --out, or --force.".format(out))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    write_json(out, empty_kb(qid, args.question))
    print("Created local question '{}' -> {}".format(args.question, out))
    print("Next:  python cli.py harvest {}   then   python cli.py push {}".format(out, out))


def cmd_questions(args):
    """List/search questions on the portal."""
    from app import client
    base = client.portal_url(args.portal)
    rows = client.list_questions(base, args.search)
    if not rows:
        print("No questions found.")
        return
    for q in rows:
        c = q.get("counts", {})
        print("  {}  v{:<3} {:>3} sources  {}".format(
            q["id"], c.get("version", 0), c.get("sources", 0), q["question"]))


# ---------------------------------------------------------------- ingest / discover
def _write_prompt_files(prompts, stem, tail=True):
    """Write dry-run extraction prompt(s) to out/ and print short pointers — keeps huge
    prompts out of the terminal. tail=False suppresses the generic paste instructions when the
    caller prints its own."""
    if not prompts:
        print("No prompts written (no sources fetched successfully).")
        return
    outdir = os.path.join(ROOT, "out")
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for i, p in enumerate(prompts, 1):
        name = stem + ".txt" if len(prompts) == 1 else "{}-{}.txt".format(stem, i)
        path = os.path.join(outdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(p)
        paths.append(os.path.relpath(path, ROOT))
    print("Wrote {} file(s):".format(len(paths)))
    for p in paths:
        print("  " + p)
    if tail:
        print("\nPaste each file's contents into any LLM. Save the JSON it returns (one object, or")
        print("an array for a batch), then:  python cli.py add <kb.json> <saved.json> --build")


def cmd_ingest(args):
    from ingest.pipeline import ingest_source
    kb = read_json(args.kb)
    res = ingest_source(args.target, kb, dry_run=args.dry_run)
    if args.dry_run:  # res is the prompt string
        _write_prompt_files([res], "ingest-prompt")
        return
    delta = res
    if args.apply:
        # write delta then merge so there is an auditable artifact on disk
        dpath = args.target.split("/")[-1].split("?")[0] or "source"
        dpath = os.path.join(ROOT, "cases", "delta-" + dpath + ".json")
        write_json(dpath, delta)
        print("delta → " + dpath)
        _apply_delta(args.kb, delta)
        if getattr(args, "build", False):
            _build_viewer([args.kb])
    else:
        print(json.dumps(delta, indent=2, ensure_ascii=False))


def cmd_discover(args):
    from ingest.pipeline import discover
    kb = read_json(args.kb)
    found = discover(kb["meta"]["question"], k=args.k, dry_run=args.dry_run,
                     source=args.source, deep=args.deep)
    if found is None:
        return
    print(json.dumps(found, indent=2, ensure_ascii=False))


def _targets_from_args(args):
    """Collect ingest targets from positional URLs/paths and/or a --from candidates file
    (the JSON array produced by `discover`, or {"sources":[...]})."""
    targets = list(getattr(args, "target", None) or [])
    if getattr(args, "from_file", None):
        data = read_json(args.from_file)
        items = data if isinstance(data, list) else data.get("sources", [])
        for it in items:
            u = it.get("url") if isinstance(it, dict) else it
            if u:
                targets.append(u)
    return targets


def cmd_ui(args):
    """Launch the simple web console (no extra dependencies)."""
    from ui.server import run
    run(port=args.port, open_browser=not args.no_open)


def cmd_research(args):
    """The single-operation cold start. Emits ONE self-contained prompt (discovery + extraction)
    to paste into a browsing chatbot; it returns a JSON array you save and `add`. With --apply
    and an API key, runs it through web search and merges directly."""
    from ingest.pipeline import build_research_prompt
    prompt = build_research_prompt(read_json(args.kb), k=args.k)
    if not args.apply:
        _write_prompt_files([prompt], "research-prompt")
        print("\nThen save the JSON array the chatbot returns and run:")
        print("  python cli.py add {} <sources.json> --build".format(args.kb))
        return
    from ingest import llm
    from ingest.pipeline import _parse_json
    print("Researching (web search) — this is one LLM call; use a smaller --k if it truncates.")
    arr = _parse_json(llm.complete(prompt, web=True))
    if isinstance(arr, dict):
        arr = [arr]
    dpath = os.path.join(ROOT, "cases", "sources-research.json")
    write_json(dpath, arr)
    print("sources → {} ({} found)\n".format(dpath, len(arr)))
    added = _merge_deltas(args.kb, arr)
    print("Research complete: {} of {} added to {}.".format(added, len(arr), args.kb))
    if args.build:
        _build_viewer([args.kb])


def cmd_ingest_batch(args):
    """Ingest many sources with FEWER LLM calls — `--batch N` sources per call. Feed a
    discover JSON with --from, and/or list URLs/paths. --dry-run prints the combined prompt(s)
    to paste; --apply fetches, calls the model, and merges. Dedupe makes it resumable."""
    from ingest.pipeline import ingest_batch
    targets = _targets_from_args(args)
    if not targets:
        raise SystemExit("No sources. Pass URLs/paths and/or --from <candidates.json>.")
    # --bundle (manual path): fetch ALL sources and write ONE labelling file to upload to a
    # chatbot once, instead of several paste-sized prompts. Richer per-source text (it's a file).
    if args.dry_run and args.bundle:
        from ingest.pipeline import fetch_docs, build_batch_extract_prompt
        print("Fetching real text for {} source(s)…".format(len(targets)))
        docs, skipped = fetch_docs(targets)
        for s in skipped:
            print("  skipped (couldn't fetch): {}".format(s["target"]))
        if not docs:
            raise SystemExit("Nothing fetched — no labelling file written.")
        mt = args.max_text if args.max_text != 4000 else 8000   # default richer for a file
        bundle = build_batch_extract_prompt(read_json(args.kb), docs, max_text=mt)
        _write_prompt_files([bundle], "label-sources", tail=False)
        print("\nUpload that ONE file to Claude/ChatGPT, tell it to follow the instructions "
              "inside,\nthen save the JSON array it returns and run:  "
              "python cli.py add {} <delta.json> --build".format(args.kb))
        return
    print("Sources to ingest: {}  (batch size {})".format(len(targets), args.batch))
    res = ingest_batch(targets, read_json(args.kb), dry_run=args.dry_run,
                       batch=args.batch, max_text=args.max_text)
    if args.dry_run:  # res is a list of combined prompt strings (one per batch)
        _write_prompt_files(res, "ingest-batch-prompt")
        return
    deltas = res
    if args.apply:
        dpath = os.path.join(ROOT, "cases", "deltas-batch.json")
        write_json(dpath, deltas)
        print("deltas → {} ({} source(s))\n".format(dpath, len(deltas)))
        added = _merge_deltas(args.kb, deltas)
        print("Batch complete: {} of {} added to {}.".format(added, len(deltas), args.kb))
        if args.build:
            _build_viewer([args.kb])
    else:
        print(json.dumps(deltas, indent=2, ensure_ascii=False))


def cmd_import_citations(args):
    """Import a Zotero/Mendeley/EndNote export (.ris / .bib / .csl-json) as sources. Each entry's
    DOI/URL is fetched and labelled through the normal pipeline. --dry-run --bundle writes one
    labelling file; --apply auto-labels with a key; default prints the deltas."""
    from ingest import citations
    from ingest.pipeline import ingest_batch, fetch_docs, build_batch_extract_prompt
    with open(args.file, encoding="utf-8", errors="ignore") as f:
        cands = citations.parse(f.read(), filename=args.file)
    urls = [c["url"] for c in cands if c.get("url")]
    print("Parsed {} citation(s); {} have a DOI/URL to fetch.".format(len(cands), len(urls)))
    if not urls:
        raise SystemExit("No DOIs/URLs in that file — nothing to fetch.")
    if args.dry_run:
        docs, skipped = fetch_docs(urls)
        for s in skipped:
            print("  skipped (couldn't fetch): {}".format(s["target"]))
        if not docs:
            raise SystemExit("Nothing fetched.")
        mt = args.max_text if args.max_text != 4000 else 8000
        bundle = build_batch_extract_prompt(read_json(args.kb), docs, max_text=mt)
        _write_prompt_files([bundle], "label-sources", tail=False)
        print("\nUpload that ONE file to your chatbot; paste the JSON array; then:  "
              "python cli.py add {} <delta.json> --build".format(args.kb))
        return
    deltas = ingest_batch(urls, read_json(args.kb), batch=args.batch, max_text=args.max_text)
    if args.apply:
        added = _merge_deltas(args.kb, deltas)
        print("Imported {} of {} into {}.".format(added, len(deltas), args.kb))
        if args.build:
            _build_viewer([args.kb])
    else:
        print(json.dumps(deltas, indent=2, ensure_ascii=False))


def cmd_export(args):
    """Export a question's sources as a citation file (BibTeX / RIS / CSL-JSON) for Zotero etc."""
    from ingest import citations
    kb = read_json(args.kb)
    text, _mime, ext = citations.export(kb, args.format)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("Wrote {} source(s) → {} ({})".format(len(kb.get("sources", [])), args.out, ext))
    else:
        print(text)


def cmd_harvest(args):
    """Cold start in one command: discover candidate sources, then ingest+merge each.
    Needs an API key (it makes LLM calls). Without one, use the manual path:
    `discover --dry-run` then `ingest <link> --dry-run` per source. Resumable — re-running
    skips sources already in the KB (dedupe)."""
    from ingest.pipeline import discover, ingest_source, ingest_batch
    kb0 = read_json(args.kb)
    question = kb0["meta"]["question"]
    have = [s.get("title") for s in kb0["sources"] if s.get("title")]
    print("Discovering sources for: " + question)
    found = discover(question, k=args.k, dry_run=False,
                     source=args.source, deep=args.deep, exclude=have) or []
    urls = [it.get("url") for it in found if it.get("url")]
    print("Found {} candidate source(s).\n".format(len(urls)))
    if args.batch > 1:
        # fewer LLM calls: extract several sources per call (trims per-source text)
        deltas = ingest_batch(urls, read_json(args.kb), batch=args.batch, max_text=args.max_text)
        added = _merge_deltas(args.kb, deltas)
    else:
        # one call per source, full text — highest extraction fidelity
        added = 0
        for url in urls:
            print("=== {} ===".format(url))
            try:
                delta = ingest_source(url, read_json(args.kb), dry_run=False)
            except SystemExit as e:
                print("  skipped (extract/LLM failed): {}\n".format(e))
                continue
            if _apply_delta(args.kb, delta):
                added += 1
            print("")
    print("Harvest complete: {} source(s) added to {}.".format(added, args.kb))
    if args.build:
        _build_viewer([args.kb])


def _choose_gaps(queries, tried, width, interactive):
    """Pick which thin spots to pursue this round. Non-interactive: the worst `width` untried.
    Interactive: list them and let the user choose (all / a subset / quit). Returns the chosen
    list, [] if none left, or None if the user asked to stop."""
    avail = [q for q in queries if q["query"].lower() not in tried]
    if not avail:
        return []
    if not interactive:
        return avail[:width]
    sev = {3: "!!", 2: "! ", 1: "  "}
    print("\nThin spots found:")
    for i, q in enumerate(avail, 1):
        g = q["gap"]
        print("  {:>2}. [{}] {:18} {}".format(i, sev.get(g["severity"], "  "), g["kind"], g["why"]))
    raw = input("\nWhich to explore? [Enter=all · e.g. 1,3,5 · q=quit]: ").strip().lower()
    if raw in ("q", "quit"):
        return None
    if raw in ("", "all", "a"):
        return avail
    picks = []
    for tok in raw.replace(",", " ").split():
        if tok.isdigit() and 1 <= int(tok) <= len(avail):
            picks.append(avail[int(tok) - 1])
    return picks or avail


def cmd_deepen(args):
    """Gap-driven deep search. Each round: find where evidence is THIN (engine/gaps.py), search
    those gaps, ingest what's new, then re-check. Stops when a round adds nothing or the round
    budget is hit — and ALWAYS reports the gaps still open. A plateau is a diagnostic, never a
    claim that the evidence is exhausted (more may be findable with new search angles)."""
    from engine.gaps import find_gaps, gap_queries
    from ingest.pipeline import discover, ingest_batch
    from engine.merge import source_key
    from ingest import llm

    budget = getattr(args, "budget", None)            # estimated USD cap; runs to cap or saturation
    if budget:
        llm.reset_usage()
        print("Thorough search — spending up to ${:.2f} (estimated), then stopping.".format(budget))
    max_rounds = 100 if budget else args.rounds       # budget mode: bounded by money, not rounds
    tried, total_added = set(), 0
    for rnd in range(1, max_rounds + 1):
        if budget and llm.usage()["usd"] >= budget:
            print("\nBudget reached (~${:.2f}). Stopping.".format(llm.usage()["usd"]))
            break
        gaps = gap_queries(read_json(args.kb), find_gaps(read_json(args.kb)))
        if budget and getattr(args, "broad", False):
            # broad mode: one wide sweep of the QUESTION per round (re-search; exclude grows).
            question = read_json(args.kb)["meta"]["question"]
            batch_q = [{"query": question, "gap": {"kind": "broad"}}]
        elif budget:
            # gaps mode: search the worst gaps; re-search is allowed (exclude grows each round).
            if not gaps:
                print("No gaps left — every position rests on independent primary evidence.")
                break
            batch_q = gaps[:args.width]
        else:
            if not gaps:
                print("No gaps left — every position rests on independent primary evidence.")
                break
            interactive = sys.stdin.isatty() and not args.all
            batch_q = _choose_gaps(gaps, tried, args.width, interactive)
            if batch_q is None:                  # user chose to quit
                print("Stopped at your request.")
                break
            if not batch_q:
                print("Round {}: every current gap-query already tried; {} gap(s) remain but need "
                      "fresh search angles (try --source both).".format(rnd, len(gaps)))
                break
            for q in batch_q:
                tried.add(q["query"].lower())
        print("\n=== Round {} — {} search(es) ===".format(rnd, len(batch_q)))
        kb_now = read_json(args.kb)
        existing = {source_key(s) for s in kb_now["sources"]}
        have = [s.get("title") for s in kb_now["sources"] if s.get("title")]
        urls = []
        for q in batch_q:
            if budget and llm.usage()["usd"] >= budget:
                break
            print("  search [{}]: {}".format(q["gap"]["kind"], q["query"][:68]))
            qk = max(args.per, 15) if q["gap"]["kind"] == "broad" else args.per  # wider for broad
            try:
                cands = discover(q["query"], k=qk, source=args.source, deep=False,
                                 exclude=have) or []
            except Exception as e:               # one bad/slow search must not stall the run
                print("    search failed, skipping: {}".format(str(e)[:80]))
                continue
            for it in cands:
                u = it.get("url")
                if u and source_key({"url": u}) not in existing:
                    existing.add(source_key({"url": u})); urls.append(u)
        if not urls:
            print("  no NEW candidates this round — saturated.")
            break
        print("  ingesting {} new candidate(s)…".format(len(urls)))
        added = _merge_deltas(args.kb, ingest_batch(urls, read_json(args.kb),
                                                    batch=args.batch, max_text=args.max_text))
        total_added += added
        remaining = find_gaps(read_json(args.kb))
        spent = "  ~${:.2f} spent".format(llm.usage()["usd"]) if budget else ""
        print("  round {}: +{} source(s); {} gap(s) remaining.{}".format(
            rnd, added, len(remaining), spent))
        if added == 0:
            print("  nothing merged — stopping (diminishing returns).")
            break

    final = gap_queries(read_json(args.kb), find_gaps(read_json(args.kb)))
    spend = "  Estimated spend: ${:.2f} over {} LLM call(s).".format(
        llm.usage()["usd"], llm.usage()["calls"]) if budget else ""
    print("\nDeep search done: +{} source(s) total. {} gap(s) still open.{}".format(
        total_added, len(final), spend))
    if final:
        print("Still thin (more may be findable — NOT a completeness claim):")
        for q in final[:6]:
            print("  - {}: {}".format(q["gap"]["kind"], q["gap"].get("why", "")))
    if args.build:
        _build_viewer([args.kb])


def main():
    from app.env import load_dotenv
    load_dotenv()  # pick up keys/config from .env before any command (and before llm import)
    ap = argparse.ArgumentParser(prog="epistemic")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("id"); s.add_argument("question")
    s.add_argument("--out"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("show"); s.add_argument("kb"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("assess"); s.add_argument("kb"); s.set_defaults(fn=cmd_assess)
    s = sub.add_parser("gaps", help="show where evidence is thin (steers gap-driven deep search)")
    s.add_argument("kb"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_gaps)
    s = sub.add_parser("deepen", help="gap-driven deep search: find thin spots, search them, repeat")
    s.add_argument("kb"); s.add_argument("--rounds", type=int, default=3)
    s.add_argument("--width", type=int, default=4, help="thin spots targeted per round")
    s.add_argument("--per", type=int, default=6, help="candidates fetched per gap search")
    s.add_argument("--source", choices=["api", "web", "both"], default="web")
    s.add_argument("--budget", type=float,
                   help="THOROUGH mode: keep going until ~$N (estimated) is spent or it saturates")
    s.add_argument("--broad", action="store_true",
                   help="with --budget: wide-harvest the QUESTION instead of searching gaps")
    s.add_argument("--all", action="store_true", help="pursue all thin spots without prompting")
    s.add_argument("--batch", type=int, default=5)
    s.add_argument("--max-text", dest="max_text", type=int, default=4000)
    s.add_argument("--build", action="store_true"); s.set_defaults(fn=cmd_deepen)
    s = sub.add_parser("add"); s.add_argument("kb"); s.add_argument("delta")
    s.add_argument("--build", action="store_true"); s.set_defaults(fn=cmd_add)
    s = sub.add_parser("build"); s.add_argument("kb", nargs="+"); s.add_argument("--out")
    s.set_defaults(fn=cmd_build)
    s = sub.add_parser("ingest"); s.add_argument("kb"); s.add_argument("target")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--apply", action="store_true")
    s.add_argument("--build", action="store_true"); s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("discover"); s.add_argument("kb"); s.add_argument("--k", type=int, default=8)
    s.add_argument("--source", choices=["api", "web", "both"], default="web",
                   help="api=OpenAlex (no key), web=LLM web search, both=merge")
    s.add_argument("--deep", action="store_true", help="thorough multi-search web pass (with --source web/both)")
    s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_discover)
    s = sub.add_parser("research"); s.add_argument("kb"); s.add_argument("--k", type=int, default=20)
    s.add_argument("--apply", action="store_true"); s.add_argument("--build", action="store_true")
    s.set_defaults(fn=cmd_research)
    s = sub.add_parser("ingest-batch"); s.add_argument("kb"); s.add_argument("target", nargs="*")
    s.add_argument("--from", dest="from_file", help="discover JSON (array of {url,...}) to ingest")
    s.add_argument("--batch", type=int, default=5, help="sources per LLM call (default 5)")
    s.add_argument("--max-text", dest="max_text", type=int, default=4000,
                   help="chars of each source's text per call (default 4000)")
    s.add_argument("--bundle", action="store_true",
                   help="with --dry-run: fetch all sources into ONE labelling file to upload to a chatbot")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--apply", action="store_true")
    s.add_argument("--build", action="store_true"); s.set_defaults(fn=cmd_ingest_batch)
    s = sub.add_parser("ui"); s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true"); s.set_defaults(fn=cmd_ui)
    TYPES = ["position", "dataset", "factor", "evidence", "population"]
    s = sub.add_parser("merge", help="fold a duplicate entity into another (id, label, or substring)")
    s.add_argument("kb"); s.add_argument("type", choices=TYPES)
    s.add_argument("src"); s.add_argument("dst"); s.add_argument("--build", action="store_true")
    s.set_defaults(fn=cmd_merge)
    s = sub.add_parser("rename"); s.add_argument("kb"); s.add_argument("type", choices=TYPES)
    s.add_argument("ref"); s.add_argument("label"); s.add_argument("--build", action="store_true")
    s.set_defaults(fn=cmd_rename)
    s = sub.add_parser("dups", help="list likely-duplicate entities to merge")
    s.add_argument("kb"); s.add_argument("--threshold", type=float, default=0.4)
    s.set_defaults(fn=cmd_dups)
    s = sub.add_parser("tidy", help="prettify id-style / slug labels for display")
    s.add_argument("kb"); s.add_argument("--build", action="store_true"); s.set_defaults(fn=cmd_tidy)
    s = sub.add_parser("harvest"); s.add_argument("kb"); s.add_argument("--k", type=int, default=8)
    s.add_argument("--batch", type=int, default=1, help="sources per LLM call; >1 = fewer calls")
    s.add_argument("--max-text", dest="max_text", type=int, default=4000)
    s.add_argument("--source", choices=["api", "web", "both"], default="web",
                   help="api=OpenAlex (no key), web=LLM web search, both=merge")
    s.add_argument("--deep", action="store_true", help="thorough multi-search web pass (with --source web/both)")
    s.add_argument("--build", action="store_true"); s.set_defaults(fn=cmd_harvest)

    # portal sync (push/pull) — portal URL from --portal or EPISTEMIC_PORTAL
    s = sub.add_parser("pull"); s.add_argument("id"); s.add_argument("--out")
    s.add_argument("--portal"); s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("push"); s.add_argument("kb")
    s.add_argument("--portal"); s.add_argument("--as", dest="as_", help="contributor name")
    s.add_argument("--token", help="portal admin token (or set EPISTEMIC_ADMIN_TOKEN)")
    s.set_defaults(fn=cmd_push)
    s = sub.add_parser("new", help="create a new question locally (git-style); push when ready")
    s.add_argument("question"); s.add_argument("--id"); s.add_argument("--out")
    s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_new)
    s = sub.add_parser("questions"); s.add_argument("--search"); s.add_argument("--portal")
    s.set_defaults(fn=cmd_questions)

    # citation interchange (Zotero / Mendeley / EndNote: .ris / .bib / .csl-json)
    s = sub.add_parser("import-citations"); s.add_argument("kb"); s.add_argument("file")
    s.add_argument("--batch", type=int, default=5)
    s.add_argument("--max-text", dest="max_text", type=int, default=4000)
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--apply", action="store_true")
    s.add_argument("--build", action="store_true"); s.set_defaults(fn=cmd_import_citations)
    s = sub.add_parser("export"); s.add_argument("kb")
    s.add_argument("--format", choices=["bibtex", "ris", "csl"], default="bibtex")
    s.add_argument("--out"); s.set_defaults(fn=cmd_export)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
