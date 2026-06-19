"""Curation ops (workstream B): merge near-duplicate entities and rename labels.

Deterministic, no LLM. These clean up the residue the string-matching resolver can't catch on
its own — paraphrase duplicates that slip in as a case grows (e.g. three "Women (mixed …)"
population terms). Every op leaves the KB internally consistent, so the metrics in assess.py
just recompute. Each op bumps the version and appends a log entry, so the Changes tab records
the curation alongside source additions.
"""
from .merge import norm, now_iso, prettify_label


# ---- reference resolution: accept an id, an exact label, or a unique substring -------------
def _resolve(items, ref, kind):
    ref_s = str(ref or "").strip()
    for it in items:                                   # exact id
        if it.get("id") == ref_s:
            return it
    n = norm(ref_s)
    exact = [it for it in items if norm(it["label"]) == n]
    if exact:
        return exact[0]
    sub = [it for it in items if n and n in norm(it["label"])]
    if len(sub) == 1:
        return sub[0]
    if len(sub) > 1:
        raise ValueError("'{}' is ambiguous among {} {}s — use the id".format(ref, len(sub), kind))
    raise ValueError("no {} matches '{}'".format(kind, ref))


def _vocab_kind(kind):
    if kind not in ("evidence", "population"):
        raise ValueError("vocab kind must be 'evidence' or 'population'")
    return kind


def _commit(kb, action, summary):
    v = (kb["meta"].get("version", 0) or 0) + 1
    kb["meta"]["version"] = v
    kb["meta"]["updated"] = now_iso()
    kb.setdefault("log", []).append(
        {"version": v, "action": action, "summary": summary, "ts": kb["meta"]["updated"]})
    return {"version": v, "summary": summary}


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---- merges -------------------------------------------------------------------------------
def merge_positions(kb, src_ref, dst_ref):
    """Fold position src into dst: reassign its sources, move its factor weights (dst wins on
    conflict), re-point provenance, drop src."""
    src = _resolve(kb["positions"], src_ref, "position")
    dst = _resolve(kb["positions"], dst_ref, "position")
    if src["id"] == dst["id"]:
        raise ValueError("source and target are the same position")
    moved = 0
    for s in kb["sources"]:
        if s["position"] == src["id"]:
            s["position"] = dst["id"]
            moved += 1
    for f in kb["factors"]:
        w = f.get("weights", {})
        if src["id"] in w:
            w.setdefault(dst["id"], w[src["id"]])     # keep dst's weight if it already had one
            del w[src["id"]]
        for p in f.get("provenance", []):
            if p.get("pos") == src["id"]:
                p["pos"] = dst["id"]
    kb["positions"] = [p for p in kb["positions"] if p["id"] != src["id"]]
    return _commit(kb, "merge-position",
                   "merged position “{}” → “{}” ({} sources)".format(src["label"], dst["label"], moved))


def merge_datasets(kb, src_ref, dst_ref):
    """Fold dataset src into dst: rewrite restsOn references, learn src's label as a dst alias
    (so future ingests of the same name resolve correctly), drop src. This is what restores an
    honest independence/concentration reading when one cohort got split under two names."""
    src = _resolve(kb["datasets"], src_ref, "dataset")
    dst = _resolve(kb["datasets"], dst_ref, "dataset")
    if src["id"] == dst["id"]:
        raise ValueError("source and target are the same dataset")
    n = 0
    for s in kb["sources"]:
        ro = s.get("restsOn", [])
        if src["id"] in ro:
            s["restsOn"] = _dedup([dst["id"] if d == src["id"] else d for d in ro])
            n += 1
    dst.setdefault("aliases", [])
    for al in [src["label"]] + src.get("aliases", []):
        if al not in dst["aliases"]:
            dst["aliases"].append(al)
    kb["datasets"] = [d for d in kb["datasets"] if d["id"] != src["id"]]
    return _commit(kb, "merge-dataset",
                   "merged dataset “{}” → “{}” ({} sources)".format(src["label"], dst["label"], n))


def merge_factors(kb, src_ref, dst_ref):
    """Fold factor src into dst: merge weights (dst wins on conflict) and provenance, drop src."""
    src = _resolve(kb["factors"], src_ref, "factor")
    dst = _resolve(kb["factors"], dst_ref, "factor")
    if src["id"] == dst["id"]:
        raise ValueError("source and target are the same factor")
    for pos, w in src.get("weights", {}).items():
        dst.setdefault("weights", {}).setdefault(pos, w)
    dst.setdefault("provenance", []).extend(src.get("provenance", []))
    if not dst.get("rationale") and src.get("rationale"):
        dst["rationale"] = src["rationale"]
    kb["factors"] = [f for f in kb["factors"] if f["id"] != src["id"]]
    return _commit(kb, "merge-factor",
                   "merged factor “{}” → “{}”".format(src["label"], dst["label"]))


def merge_vocab(kb, kind, src_label, dst_label):
    """Fold one evidence/population term into another: re-point sources, learn src as an alias
    of dst, drop src."""
    kind = _vocab_kind(kind)
    terms = kb.setdefault("vocab", {}).setdefault(kind, [])
    src = _resolve(terms, src_label, kind)
    dst = _resolve(terms, dst_label, kind)
    if norm(src["label"]) == norm(dst["label"]):
        raise ValueError("source and target are the same term")
    n = 0
    for s in kb["sources"]:
        if s.get(kind) == src["label"]:
            s[kind] = dst["label"]
            n += 1
    dst.setdefault("aliases", [])
    for al in [src["label"]] + src.get("aliases", []):
        if al not in dst["aliases"]:
            dst["aliases"].append(al)
    kb["vocab"][kind] = [t for t in terms if norm(t["label"]) != norm(src["label"])]
    return _commit(kb, "merge-vocab",
                   "merged {} “{}” → “{}” ({} sources)".format(kind, src["label"], dst["label"], n))


def rename(kb, kind, ref, new_label):
    """Rename a position / dataset / factor / evidence / population label. For datasets and
    vocab the old label is kept as an alias so future ingests still resolve to it. Good for the
    ugly auto-generated labels (e.g. 'UK_Biobank_206263_women_aged_40_69')."""
    new_label = (new_label or "").strip()
    if not new_label:
        raise ValueError("new label is empty")
    if kind == "position":
        e = _resolve(kb["positions"], ref, "position")
        old = e["label"]; e["label"] = new_label
    elif kind == "factor":
        e = _resolve(kb["factors"], ref, "factor")
        old = e["label"]; e["label"] = new_label
    elif kind == "dataset":
        e = _resolve(kb["datasets"], ref, "dataset")
        old = e["label"]
        e.setdefault("aliases", [])
        if old not in e["aliases"]:
            e["aliases"].append(old)
        e["label"] = new_label
    elif kind in ("evidence", "population"):
        terms = kb.setdefault("vocab", {}).setdefault(kind, [])
        t = _resolve(terms, ref, kind)
        old = t["label"]
        for s in kb["sources"]:
            if s.get(kind) == old:
                s[kind] = new_label
        t.setdefault("aliases", [])
        if old not in t["aliases"]:
            t["aliases"].append(old)
        t["label"] = new_label
    else:
        raise ValueError("unknown type '{}'".format(kind))
    return _commit(kb, "rename", "renamed {} “{}” → “{}”".format(kind, old, new_label))


def tidy_labels(kb):
    """Prettify any id-style / slug labels across positions, datasets, and factors (underscores
    → spaces, drop trailing sample-size clauses). For datasets the old label is kept as an alias."""
    changed = []
    for d in kb["datasets"]:
        nice = prettify_label(d["label"])
        if nice and nice != d["label"]:
            d.setdefault("aliases", [])
            if d["label"] not in d["aliases"]:
                d["aliases"].append(d["label"])
            changed.append("{} → {}".format(d["label"], nice))
            d["label"] = nice
    for coll in ("positions", "factors"):
        for e in kb[coll]:
            nice = prettify_label(e["label"])
            if nice and nice != e["label"]:
                changed.append("{} → {}".format(e["label"], nice))
                e["label"] = nice
    if not changed:
        return {"version": kb["meta"].get("version", 0), "summary": "labels already clean"}
    return _commit(kb, "tidy", "tidied {} label(s)".format(len(changed)))


# ---- duplicate suggestions (token Jaccard) ------------------------------------------------
def _tokens(s):
    return set(t for t in norm(s).split() if t)


def _similarity(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def suggest_duplicates(kb, threshold=0.4):
    """Flag entity pairs whose labels look like paraphrases (token-overlap ≥ threshold), so a
    curator doesn't have to hunt. Suggestions only — the merge is always explicit."""
    groups = {
        "position": [(p["id"], p["label"]) for p in kb["positions"]],
        "dataset": [(d["id"], d["label"]) for d in kb["datasets"]],
        "factor": [(f["id"], f["label"]) for f in kb["factors"]],
        "population": [(t["label"], t["label"]) for t in kb.get("vocab", {}).get("population", [])],
        "evidence": [(t["label"], t["label"]) for t in kb.get("vocab", {}).get("evidence", [])],
    }
    out = {}
    for kind, items in groups.items():
        pairs = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                sim = _similarity(items[i][1], items[j][1])
                if sim >= threshold:
                    pairs.append({"a": {"ref": items[i][0], "label": items[i][1]},
                                  "b": {"ref": items[j][0], "label": items[j][1]},
                                  "sim": round(sim, 2)})
        if pairs:
            out[kind] = sorted(pairs, key=lambda x: -x["sim"])
    return out
