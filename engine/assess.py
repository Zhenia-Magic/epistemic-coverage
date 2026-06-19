"""ASSESSMENT layer (Layer 3 of the FLF stack).

Pure, deterministic functions over a knowledge base (KB). No LLM, no I/O, stdlib only.
Adding evidence never re-runs these by hand on old data -- they just recount, so recompute
is O(whole KB) but cheap, while ingestion stays O(new sources). This is the single
implementation of every number the tool reports; the viewer renders these outputs, it does
not recompute them, so there is no drift between pipeline and UI.
"""

# factor weighting vocabulary -> ordinal, for crux spread
WV = {"high": 3, "med": 2, "low": 1, "n/a": 0}


def _ds_label(kb, did):
    for d in kb["datasets"]:
        if d["id"] == did:
            return d["label"]
    return did


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


def independence(kb):
    """The anti-false-balance core. Per position, how concentrated are its sources on a
    single underlying dataset?
        raw        = source count
        distinct   = distinct datasets they rest on
        topDataset = the single most-reused dataset + how many sources lean on it
        conc       = topCount / raw  (share resting on the one most-reused dataset)
        nEff       = Herfindahl numbers-equivalent over dataset incidence shares --
                     effective independent datasets, discounted for concentration.
    Adding correlated evidence (same dataset) pushes conc UP: flooding the zone makes a
    position look LESS independent, not more. That is the design intent.
    """
    out = []
    for p in kb["positions"]:
        mine = [s for s in kb["sources"] if s["position"] == p["id"]]
        counts = {}
        for s in mine:
            for d in s.get("restsOn", []):
                counts[d] = counts.get(d, 0) + 1
        ids = list(counts.keys())
        raw = len(mine)
        top = {"id": None, "count": 0}
        for d in ids:
            if counts[d] > top["count"]:
                top = {"id": d, "count": counts[d]}
        total_inc = sum(counts[d] for d in ids)
        hhi = sum((counts[d] / total_inc) ** 2 for d in ids) if total_inc else 0
        n_eff = 1 / hhi if hhi else 0
        conc = top["count"] / raw if raw else 0
        out.append({
            "id": p["id"], "label": p["label"], "hue": p["hue"],
            "raw": raw, "distinct": len(ids), "nEff": n_eff, "concentration": conc,
            "topDataset": ({"label": _ds_label(kb, top["id"]), "id": top["id"],
                            "count": top["count"]} if top["id"] else None),
            "datasets": [_ds_label(kb, d) for d in ids],
            "concentrated": top["count"] >= 2 and conc >= 0.5,
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
    cand = [x for x in ind if x["concentrated"]]
    cand.sort(key=lambda x: (x["topDataset"]["count"], x["concentration"], x["raw"]),
              reverse=True)
    worst = cand[0] if cand else None
    return {
        "version": kb.get("meta", {}).get("version"),
        "distribution": distribution(kb),
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
