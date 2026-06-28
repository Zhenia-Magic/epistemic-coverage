"""Root-basis resolution — the engine behind the independence metric (see MECHANISM.md).

Resolves every source to the primary EVIDENTIARY ROOTS it ultimately depends on, by following
`restsOn` edges (to datasets AND to other sources), collapsing strongly-connected citation cycles
(circular corroboration) to a single root, and falling back to evidence-tier defaults for ungrounded
sources. Pure functions of the KB; deterministic; no side effects.

Root keys produced:
    ds:<id>          a real dataset / cohort / experiment
    prim:<sourceId>  an ungrounded PRIMARY source — its own root (benefit of the doubt)
    secpool:<posId>  the single 'ungrounded secondary' voice for a position (all echo collapses here)
    cycle:<sourceId> a circular-corroboration loop with no primary grounding (flagged)
"""
import re

def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s if s is not None else "").lower()).strip()


# Evidence type -> tier. Only matters for UNGROUNDED sources: a grounded source resolves through its
# dataset(s) regardless of tier. Unknown types default to PRIMARY (benefit of the doubt). A case can
# override by putting "tier" on the evidence vocab term. Keys are normalised (see _norm) so that
# punctuation like "Experimental (RCT)" or "Narrative/Commentary" still matches.
_TIER = {_norm(k): v for k, v in {
    "observational": "primary", "experimental (rct)": "primary", "experimental": "primary",
    "mechanistic": "primary", "theoretical analysis": "primary",
    "theoretical critique": "primary", "modelling": "primary", "simulation": "primary",
    # A meta-analysis / systematic review is a SYNTHESIS of others' studies, not new primary data.
    # It only counts as independent if it TAGS the trials it pools (then it resolves through them);
    # an untagged one is echo and collapses into the position's one secondary voice (MECHANISM.md §3).
    "meta-analysis": "secondary", "systematic review": "secondary",
    "evidence-synthesis": "secondary", "expert advisory": "secondary", "expert review": "secondary",
    "narrative/commentary": "secondary", "narrative": "secondary", "commentary": "secondary",
    "institutional statement": "secondary", "editorial": "secondary", "perspective": "secondary",
    "review": "secondary", "guideline": "secondary",
}.items()}


def tier_of(kb, source):
    """primary | secondary for a source, from the case vocab's tier if set, else the default map."""
    ev = _norm(source.get("evidence"))
    for t in (kb.get("vocab", {}).get("evidence") or []):
        if _norm(t.get("label")) == ev or any(_norm(a) == ev for a in t.get("aliases", [])):
            if t.get("tier") in ("primary", "secondary"):
                return t["tier"]
            break
    return _TIER.get(ev, "primary")


# population tokens / phrases that mark a NON-human study (animal model or in-vitro). Token match
# for short words (so "moderate" can't match "rat"); phrase match for the multi-word ones.
_NONHUMAN_TOKENS = {"mice", "mouse", "murine", "rat", "rats", "rodent", "rodents", "animal",
                    "animals", "rabbit", "rabbits", "porcine", "swine", "canine", "feline",
                    "zebrafish", "drosophila", "bovine", "ovine", "vitro"}
_NONHUMAN_PHRASES = ("in vitro", "ex vivo", "cell line", "cell culture", "animal model")


def _is_nonhuman(source):
    """True if the source's population marks it as an animal or in-vitro study (see prompt rule)."""
    p = _norm(source.get("population"))
    if not p:
        return False
    return bool(set(p.split()) & _NONHUMAN_TOKENS) or any(ph in p for ph in _NONHUMAN_PHRASES)


def _edges(source):
    """Split a source's restsOn into (dataset ids, source ids). Source edges are stored as
    'src:<id>'; everything else is a dataset root."""
    ds, src = [], []
    for e in source.get("restsOn") or []:
        e = str(e)
        if e.startswith("src:"):
            src.append(e[4:])
        else:
            ds.append(e)
    return ds, src


def _tarjan(adj):
    """Strongly-connected components of the source->source graph. Returns (sccs, comp_of)."""
    index = {}; low = {}; onstack = {}; stack = []; counter = [0]; sccs = []

    def strong(v):
        # iterative Tarjan to avoid recursion limits on long chains
        work = [(v, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index[node] = low[node] = counter[0]; counter[0] += 1
                stack.append(node); onstack[node] = True
            recurse = False
            neighbours = adj.get(node, [])
            for i in range(pi, len(neighbours)):
                w = neighbours[i]
                if w not in index:
                    work[-1] = (node, i + 1)
                    work.append((w, 0)); recurse = True; break
                elif onstack.get(w):
                    low[node] = min(low[node], index[w])
            if recurse:
                continue
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop(); onstack[w] = False; comp.append(w)
                    if w == node:
                        break
                sccs.append(comp)
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])

    for v in adj:
        if v not in index:
            strong(v)
    comp_of = {}
    for i, comp in enumerate(sccs):
        for sid in comp:
            comp_of[sid] = i
    return sccs, comp_of


def resolve(kb):
    """Resolve every source to its set of root keys.

    Returns a dict:
      source_roots : {source_id: set(root_key)}
      circular     : [ {sources:[ids], positions:[ids]} ]  pure circular-corroboration loops
      secondary_only : set(root_key)  dataset roots asserted ONLY via secondary sources
      kind         : {root_key: 'dataset'|'primary'|'secondary'|'cycle'}
    """
    sources = {s["id"]: s for s in kb["sources"]}
    adj = {sid: [t for t in _edges(s)[1] if t in sources] for sid, s in sources.items()}
    sccs, comp_of = _tarjan(adj)

    circular = []
    memo = {}

    def comp_roots(ci):
        if ci in memo:
            return memo[ci]
        memo[ci] = set()                      # guard against accidental re-entry
        comp = sccs[ci]
        roots = set()
        for sid in comp:                      # dataset roots from any member
            for d in _edges(sources[sid])[0]:
                roots.add("ds:" + d)
        for sid in comp:                      # external source edges -> other components' roots
            for t in _edges(sources[sid])[1]:
                if t in comp_of and comp_of[t] != ci:
                    roots |= comp_roots(comp_of[t])
        is_cycle = len(comp) > 1
        if not roots:                         # ungrounded component
            if is_cycle:                      # circular corroboration with no grounding -> flag
                roots = {"cycle:" + min(comp)}
                circular.append({"sources": sorted(comp),
                                 "positions": sorted({sources[s]["position"] for s in comp})})
            else:
                s = sources[comp[0]]
                roots = {"prim:" + comp[0]} if tier_of(kb, s) == "primary" \
                    else {"secpool:" + s["position"]}
        memo[ci] = roots
        return roots

    source_roots = {sid: comp_roots(comp_of[sid]) for sid in sources}

    # a dataset root is 'asserted only via secondary' if no PRIMARY source rests on it directly
    primary_ds = set()
    for s in kb["sources"]:
        if tier_of(kb, s) == "primary":
            for d in _edges(s)[0]:
                primary_ds.add("ds:" + d)
    all_ds = {r for rs in source_roots.values() for r in rs if r.startswith("ds:")}
    secondary_only = all_ds - primary_ds

    # a root is 'non-human only' if EVERY source resting on it is an animal / in-vitro study — it's
    # weaker evidence for a human/clinical question, so it counts at half (like secondary-only).
    human, animal = set(), set()
    for s in kb["sources"]:
        target = animal if _is_nonhuman(s) else human
        for r in source_roots[s["id"]]:
            if not (r.startswith("secpool:") or r.startswith("cycle:")):  # collapsed voices: n/a
                target.add(r)
    nonhuman_only = animal - human

    def kind_of(r):
        return {"d": "dataset", "p": "primary", "s": "secondary", "c": "cycle"}[
            ("d" if r.startswith("ds:") else "p" if r.startswith("prim:")
             else "s" if r.startswith("secpool:") else "c")]
    kinds = {r: kind_of(r) for rs in source_roots.values() for r in rs}

    return {"source_roots": source_roots, "circular": circular,
            "secondary_only": secondary_only, "nonhuman_only": nonhuman_only, "kind": kinds}


def root_strength(root_key, secondary_only, nonhuman_only=frozenset()):
    """Independence weight a root contributes. Halved for a dataset known only through a secondary
    source (we heard about it, no primary source brought it in) AND halved for a root backed only by
    animal / in-vitro studies (weak evidence for a human question). See MECHANISM.md §6."""
    w = 1.0
    if root_key in secondary_only:
        w *= 0.5
    if root_key in nonhuman_only:
        w *= 0.5
    return w
