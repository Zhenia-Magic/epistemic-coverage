"""ASSESSMENT layer (Layer 3 of the FLF stack).

Pure, deterministic functions over a knowledge base (KB). No LLM, no I/O, stdlib only.
Adding evidence never re-runs these by hand on old data -- they just recount, so recompute
is O(whole KB) but cheap, while ingestion stays O(new sources). This is the single
implementation of every number the tool reports; the viewer renders these outputs, it does
not recompute them, so there is no drift between pipeline and UI.
"""

from engine import roots as _roots

# factor weighting vocabulary -> ordinal, for crux spread
WV = {"high": 3, "med": 2, "low": 1, "n/a": 0}


def _ds_label(kb, did):
    for d in kb["datasets"]:
        if d["id"] == did:
            return d["label"]
    return did


def _root_incidence(kb, res):
    """Per position, the weighted incidence of each resolved evidentiary ROOT (see MECHANISM.md):
    sources collapse onto shared datasets, secondary echo collapses to one voice, citation cycles
    collapse to one loop-root. Returns {posId: {rootKey: weight}}, the basis for nEff and the bar."""
    src_by_pos = {}
    for s in kb["sources"]:
        src_by_pos.setdefault(s["position"], []).append(s)
    secondary_only = res["secondary_only"]
    nonhuman_only = res.get("nonhuman_only", frozenset())
    per_pos = {}
    for p in kb["positions"]:
        weights = {}
        for s in src_by_pos.get(p["id"], []):
            for r in res["source_roots"].get(s["id"], ()):
                if r.startswith("secpool:") or r.startswith("cycle:"):
                    weights[r] = 1            # a COLLAPSED voice counts once, no matter how many
                else:                          #   sources fell into it (robust to echo-flooding both
                    weights[r] = weights.get(r, 0) + _roots.root_strength(
                        r, secondary_only, nonhuman_only)   # halved for review-only / animal roots
        per_pos[p["id"]] = weights             #   ways); the source count is surfaced separately
    return per_pos


def _n_eff(weights):
    total = sum(weights.values())
    hhi = sum((w / total) ** 2 for w in weights.values()) if total else 0
    return (1 / hhi) if hhi else 0


def weighted_distribution(kb):
    """Distribution WEIGHTED BY INDEPENDENCE — the portal's thesis made visual. Each position is
    sized not by raw source count but by its effective number of independent evidence ROOTS: the
    Herfindahl numbers-equivalent over resolved roots (MECHANISM.md). Sources sharing a dataset,
    echoing as secondary reviews, or citing each other in a loop all collapse toward one 'look'. A
    position propped up by re-used, derivative, or circular evidence shrinks vs. its raw bar."""
    res = _roots.resolve(kb)
    inc = _root_incidence(kb, res)
    out, weights = [], []
    for p in kb["positions"]:
        mine = [s for s in kb["sources"] if s["position"] == p["id"]]
        n_eff = _n_eff(inc[p["id"]])
        weights.append(n_eff)
        out.append({"id": p["id"], "label": p["label"], "hue": p["hue"],
                    "raw": len(mine), "weight": round(n_eff, 2)})
    tot = sum(weights) or 1
    for o, w in zip(out, weights):
        o["pct"] = round(100 * w / tot)
    return out


def distribution(kb):
    """Share of sources by position. The naive aggregator's view -- shown, then
    immediately complicated by funding skew and the independence audit."""
    by = {p["id"]: 0 for p in kb["positions"]}
    for s in kb["sources"]:
        by[s["position"]] = by.get(s["position"], 0) + 1
    total = len(kb["sources"]) or 1
    return [{"id": p["id"], "label": p["label"], "hue": p["hue"],
             "count": by[p["id"]], "pct": round(100 * by[p["id"]] / total)}
            for p in kb["positions"]]




def _low(s):
    return str(s or "").strip().lower()


def funding_skew(kb):
    """Which position *interested* money (industry or advocacy) most favours, plus how much of
    the case rests on sources that don't disclose funding. Defaulting unclear funding to
    'Undisclosed' (not 'independent') is what makes this honest: it surfaces the gap instead of
    fabricating independence."""
    interested = [s for s in kb["sources"] if _low(s["funding"]) in ("industry", "advocacy")]
    undisclosed = sum(1 for s in kb["sources"] if _low(s["funding"]) == "undisclosed")
    total = len(kb["sources"])
    if not interested and not undisclosed:
        return None
    top = None
    if interested:
        by = {p["id"]: 0 for p in kb["positions"]}
        for s in interested:
            by[s["position"]] = by.get(s["position"], 0) + 1
        tp = max(kb["positions"], key=lambda p: by[p["id"]])
        top = {"id": tp["id"], "label": tp["label"]}
    return {"n": len(interested), "top": top, "undisclosed": undisclosed, "total": total}


def blindspots(kb, min_support=2):
    """Evidence types / populations present in the case but absent from a position's own
    sources. Operationalises FLF's 'surface what's missing'.

    Only types backed by >= min_support sources case-wide count as 'present' — otherwise a
    single source with a hyper-specific population (e.g. one cohort's exact group) becomes a
    'blindspot' for every other position, drowning the real gaps in noise. (Workstream C.)"""
    evc, popc = {}, {}
    for s in kb["sources"]:
        evc[s["evidence"]] = evc.get(s["evidence"], 0) + 1
        if s["population"] and s["population"] != "—":
            popc[s["population"]] = popc.get(s["population"], 0) + 1
    all_ev = [e for e in dict.fromkeys(s["evidence"] for s in kb["sources"])
              if evc.get(e, 0) >= min_support]
    all_pop = [x for x in dict.fromkeys(s["population"] for s in kb["sources"])
               if x and x != "—" and popc.get(x, 0) >= min_support]
    out = []
    for p in kb["positions"]:
        mine = [s for s in kb["sources"] if s["position"] == p["id"]]
        ev = {s["evidence"] for s in mine}
        pop = {s["population"] for s in mine}
        out.append({"id": p["id"], "label": p["label"], "hue": p["hue"],
                    "missingEvidence": [e for e in all_ev if e not in ev],
                    "missingPop": [x for x in all_pop if x not in pop]})
    return out


def crux_score(factor, positions):
    """Spread of a factor's weighting across positions (0..3). A factor becomes a crux only
    once enough positions have weighed in -- so cruxes emerge as the KB grows."""
    vals = [WV.get(factor["weights"].get(p["id"])) for p in positions]
    vals = [v for v in vals if v]  # drop None (no weight) and 0 (n/a)
    if len(vals) < 2:
        return 0
    return max(vals) - min(vals)


def cruxes(kb):
    """Per factor: the weighting spread, whether it's a crux (spread >= 2), and how many
    positions actually weighed it (`engaged`). A factor only one position weighed is not a
    point of divergence at all — it's a dimension one camp raises and the others ignore — so
    the viewer can separate those out instead of cluttering the matrix with spread-0 rows."""
    out = []
    for f in kb["factors"]:
        engaged = sum(1 for p in kb["positions"] if WV.get(f["weights"].get(p["id"])))
        sp = crux_score(f, kb["positions"])
        out.append({"id": f["id"], "label": f["label"], "spread": sp,
                    "isCrux": sp >= 2, "engaged": engaged})
    return out


def _root_label(kb, rk, weight, secondary_only, nonhuman=frozenset()):
    """Human-readable description of a resolved root for the 'show your work' breakdown."""
    notes = []
    if rk in secondary_only:
        notes.append("cited only via a review")
    if rk in nonhuman:
        notes.append("animal / in-vitro")
    suffix = (" — " + ", ".join(notes)) if notes else ""
    if rk.startswith("ds:"):
        return _ds_label(kb, rk[3:]) + suffix
    if rk.startswith("prim:"):
        sid = rk[5:]
        t = next((s.get("title") for s in kb["sources"] if s["id"] == sid), sid)
        return (t or sid)[:60] + (suffix or " — its own primary observation")
    if rk.startswith("secpool:"):
        return "secondary literature (reviews/commentary counted as one voice)"
    if rk.startswith("cycle:"):
        return "circular citation loop (no primary grounding)"
    return rk


def independence(kb):
    """The anti-false-balance core. Per position, how many INDEPENDENT evidentiary roots actually
    support it — after collapsing shared datasets, secondary echo, and circular citation (see
    MECHANISM.md and engine/roots.py).
        raw          = source count
        distinct     = number of distinct resolved roots
        nEff         = Herfindahl numbers-equivalent over root incidence -> effective independent bases
        concentration= share resting on the single most-relied-on root
        bases        = the full 'show your work' breakdown (label, kind, weighted count)
        collapsedSecondary = how many secondary sources folded into the one 'secondary voice'
        circular     = circular-corroboration loops touching this position
    Adding correlated, derivative, or circular evidence pushes concentration UP: flooding the zone
    makes a position look LESS independent, not more. That is the design intent."""
    res = _roots.resolve(kb)
    inc = _root_incidence(kb, res)
    sec_only = res["secondary_only"]
    nonhuman = res.get("nonhuman_only", frozenset())
    circ_by_pos = {}
    for c in res["circular"]:
        for pid in c["positions"]:
            circ_by_pos.setdefault(pid, []).append(c["sources"])
    out = []
    for p in kb["positions"]:
        mine = [s for s in kb["sources"] if s["position"] == p["id"]]
        weights = inc[p["id"]]
        raw = len(mine)
        n_eff = _n_eff(weights)
        total_w = sum(weights.values())
        top_key, top_w = None, 0.0
        for rk, w in weights.items():
            if w > top_w:
                top_key, top_w = rk, w
        conc = (top_w / total_w) if total_w else 0
        bases = sorted(
            ({"key": rk, "label": _root_label(kb, rk, w, sec_only, nonhuman), "kind": res["kind"][rk],
              "weight": round(w, 2), "secondaryOnly": rk in sec_only, "nonHuman": rk in nonhuman}
             for rk, w in weights.items()), key=lambda b: -b["weight"])
        collapsed_secondary = sum(1 for s in mine
                                  if ("secpool:" + p["id"]) in res["source_roots"].get(s["id"], ()))
        # back-compat 'topDataset' only when the dominant root is an actual dataset
        top_ds = None
        if top_key and top_key.startswith("ds:"):
            top_ds = {"label": _ds_label(kb, top_key[3:]), "id": top_key[3:],
                      "count": round(top_w, 2)}
        out.append({
            "id": p["id"], "label": p["label"], "hue": p["hue"],
            "raw": raw, "distinct": len(weights), "nEff": n_eff, "concentration": conc,
            "topDataset": top_ds,
            "datasets": [b["label"] for b in bases],
            "bases": bases,
            "collapsedSecondary": collapsed_secondary,
            "circular": circ_by_pos.get(p["id"], []),
            "concentrated": top_w >= 2 and conc >= 0.5,
        })
    return out


def dominant_dataset(kb):
    """Case-wide, which single dataset underlies the most sources. The Huanan-market /
    NHS-HPFS detector. Ties returned together."""
    counts = {}
    for s in kb["sources"]:
        for d in s.get("restsOn", []):
            counts[d] = counts.get(d, 0) + 1
    if not counts:
        return None
    mx = max(counts.values())
    n = len(kb["sources"])
    return {"labels": [_ds_label(kb, d) for d in counts if counts[d] == mx],
            "count": mx, "total": n, "share": mx / n if n else 0}


def assess(kb):
    """The whole Assessment artifact -- one dict, diffable across versions."""
    ind = independence(kb)
    # worst offender = among CONCENTRATED positions, the one with the most sources resting
    # on a single dataset (then by concentration, then raw). None if nothing is concentrated.
    cand = [x for x in ind if x["concentrated"] and x["topDataset"]]
    cand.sort(key=lambda x: (x["topDataset"]["count"], x["concentration"], x["raw"]),
              reverse=True)
    worst = cand[0] if cand else None
    return {
        "version": kb.get("meta", {}).get("version"),
        "distribution": distribution(kb),
        "weightedDistribution": weighted_distribution(kb),
        "fundingSkew": funding_skew(kb),
        "blindspots": blindspots(kb),
        "cruxes": cruxes(kb),
        "independence": ind,
        "dominantDataset": dominant_dataset(kb),
        "worstConcentration": worst,
    }


def _pct(x):
    return str(round(x * 100)) + "%"


def diff_assessments(before, after):
    """Structured, human-readable diff of two assessment dicts -- the 'what changed' that
    makes each update's epistemic effect visible. Returns a list of lines."""
    lines = []
    bd = {d["id"]: d["count"] for d in before["distribution"]}
    for d in after["distribution"]:
        was = bd.get(d["id"], 0)
        if d["count"] != was:
            lines.append("distribution: {} {} → {}".format(d["label"], was, d["count"]))
    bi = {p["id"]: p for p in before["independence"]}
    for p in after["independence"]:
        b = bi.get(p["id"])
        if not b:
            lines.append("+ new position: " + p["label"])
            continue
        if abs(p["concentration"] - b["concentration"]) > 1e-9:
            top = p["topDataset"]["label"] if p["topDataset"] else "—"
            lines.append("concentration: {} {} → {} (top: {})".format(
                p["label"], _pct(b["concentration"]), _pct(p["concentration"]), top))
        if (not b["concentrated"]) and p["concentrated"]:
            lines.append("⚠ {} crossed into CONCENTRATED".format(p["label"]))
    bdom, adom = before["dominantDataset"], after["dominantDataset"]
    if adom and (not bdom or "/".join(bdom["labels"]) != "/".join(adom["labels"])
                 or bdom["count"] != adom["count"]):
        prev = ("/".join(bdom["labels"]) + " " + str(bdom["count"])) if bdom else "—"
        lines.append("most-reused case-wide: {} → {} {}/{}".format(
            prev, "/".join(adom["labels"]), adom["count"], adom["total"]))
    bc = {c["id"] for c in before["cruxes"] if c["isCrux"]}
    for c in after["cruxes"]:
        if c["isCrux"] and c["id"] not in bc:
            lines.append("+ new crux: " + c["label"])
    bb = {p["id"]: set(p["missingEvidence"]) | set(p["missingPop"]) for p in before["blindspots"]}
    for p in after["blindspots"]:
        was = bb.get(p["id"])
        if was is None:
            continue
        now = set(p["missingEvidence"]) | set(p["missingPop"])
        for x in was - now:
            lines.append('blindspot closed: {} now covers "{}"'.format(p["label"], x))
        for x in now - was:
            lines.append('blindspot opened: {} missing "{}"'.format(p["label"], x))
    return lines
