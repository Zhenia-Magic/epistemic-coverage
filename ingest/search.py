"""Structured scholarly search (INGESTION, discovery side).

Find candidate sources for a research question via an open academic API (OpenAlex) instead of
an LLM web-search backend or publisher scraping. OpenAlex indexes 250M+ works, needs no key
(a contact email puts you in the faster "polite pool"), and returns title + DOI + year +
venue + citation count directly — so cold-start discovery works even with no LLM key at all.

Output matches the shape pipeline.discover() returns -- [{title, url, year, why}] -- so it is a
drop-in for the existing fetch -> label -> merge flow. Each candidate's url is its DOI, which
extract.py then resolves straight back through OpenAlex (abstract + funders), no scraping.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

_BASE = "https://api.openalex.org/works"


_STOP = {"a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "with", "whether",
         "that", "this", "these", "those", "is", "are", "was", "were", "be", "been", "being",
         "do", "does", "did", "can", "could", "will", "would", "should", "how", "why", "what",
         "which", "who", "whom", "when", "where", "as", "by", "from", "at", "into", "than",
         "then", "there", "it", "its"}


# EFFECT verbs: the subject (exposure) comes BEFORE them — "Does <exposure> increase <outcome>?"
_EFFECT_VERBS = {"increase", "increases", "increased", "increasing", "decrease", "decreases",
                 "reduce", "reduces", "reduced", "raise", "raises", "lower", "lowers", "cause",
                 "causes", "caused", "affect", "affects", "link", "linked", "links", "associate",
                 "associated", "pose", "poses", "arise", "arises", "originate", "originated",
                 "lead", "leads", "contribute", "influence", "influences", "impact", "impacts",
                 "prevent", "prevents", "protect", "protects", "worsen", "improve", "trigger"}

# CONSUMPTION/ACTION verbs: the subject (exposure) is the OBJECT, AFTER them — "...consume alcohol"
_CONSUME_VERBS = {"consume", "consuming", "consumed", "drink", "drinking", "eat", "eating",
                  "ingest", "ingesting", "take", "taking", "using", "have", "having"}

# evaluative / modal / connective filler — never itself a distinguishing subject
_FILLER = {"safe", "safety", "safely", "harmful", "harm", "beneficial", "benefit", "healthy",
           "unhealthy", "dangerous", "danger", "risky", "good", "bad", "okay", "while", "during",
           "among", "when", "after", "before", "versus"}


def _keywords(q):
    """Reduce a question to content keywords for a topical search. OpenAlex's title/abstract
    search is far more precise than full-text relevance, but works best on keywords, not a
    full sentence — so we drop question words and grammatical stopwords (keeping content words
    like 'risk', 'increase', 'lab'). Hyphenated terms (sars-cov-2) are preserved."""
    q = re.sub(r"[^a-z0-9\s-]", " ", re.sub(r"[?*]", " ", (q or "").lower()))
    words = [w for w in q.split() if w not in _STOP and len(w) > 1]
    return " ".join(words) or re.sub(r"\s+", " ", q).strip()


# generic quantity/diet words that are not themselves a distinguishing exposure
_GENERIC_EXPO = {"consumption", "intake", "use", "exposure", "level", "levels", "dose",
                 "dosage", "amount", "amounts", "diet", "dietary", "supplementation"}


def _exposure_terms(kw):
    """The EXPOSURE/subject of the dispute — the content noun(s) a relevant paper must mention.
    Requiring one is stance-neutral (both 'eggs are safe' and 'eggs are harmful' papers say
    'eggs') yet drops papers about the same OUTCOME that aren't about this exposure (a statin
    trial sharing 'cardiovascular risk').

    Question shape matters for WHERE the subject sits:
      * effect verb  ("Does <eggs> increase <cvd>?")     -> subject is BEFORE the verb.
      * consume verb ("Is it safe to consume <alcohol>?") -> subject is the OBJECT, AFTER it.
    We pick the right side, then drop generic/quantity/filler words. Falls back to the leading
    content keywords when there's no verb (e.g. 'micro black holes ...')."""
    toks = kw.split()
    eff = next((i for i, w in enumerate(toks) if w in _EFFECT_VERBS), None)
    con = next((i for i, w in enumerate(toks) if w in _CONSUME_VERBS), None)
    if eff is not None and (con is None or eff < con):
        region = toks[:eff]                 # subject precedes an effect verb
    elif con is not None:
        region = toks[con + 1:]             # object/context follows a consumption verb
    else:
        region = toks                       # no verb: consider all keywords

    drop = _GENERIC_EXPO | _FILLER | _EFFECT_VERBS | _CONSUME_VERBS
    seen, out = set(), []
    for w in region:
        s = w[:-1] if len(w) > 4 and w.endswith("s") else w   # light de-plural
        if s in drop or s in seen:
            continue
        seen.add(s)
        out.append(s)
    if out:
        return out
    # nothing distinctive in the chosen region — fall back to the first two non-filler keywords
    fb = [w for w in toks if w not in drop]
    return fb[:2] or toks[:2]


def _deinvert(inv):
    """Rebuild abstract text from OpenAlex's inverted index {word: [positions]}."""
    if not inv:
        return ""
    words = [(p, w) for w, positions in inv.items() for p in positions]
    words.sort()
    return " ".join(w for _, w in words)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "epistemic-ingest/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _why(w):
    """One-line provenance hint shown next to each candidate (topic · venue · citations)."""
    bits = []
    topic = (w.get("primary_topic") or {}).get("display_name")
    if topic:
        bits.append("topic: " + topic)
    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
    if venue:
        bits.append(venue)
    bits.append("{} citations".format(w.get("cited_by_count", 0)))
    return " · ".join(bits)


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


# Small, GENERAL cross-cutting facet synonyms (not question-specific) so a conjunction gate
# ("alcohol" AND "breastfeeding") doesn't drop a paper that says "lactation" instead.
_SYN = {
    "breastfeeding": {"breastfeeding", "breastfeed", "breastfed", "lactation", "lactating",
                      "lactational", "nursing", "breastmilk", "breast milk"},
    "pregnancy": {"pregnancy", "pregnant", "gestational", "gestation", "prenatal", "antenatal",
                  "perinatal", "maternal"},
    "children": {"children", "child", "childhood", "pediatric", "paediatric", "infant",
                 "infancy", "neonatal", "newborn"},
}


def _variants(term):
    t = _norm_title(term)
    for group in _SYN.values():
        if t in group:
            return group
    return {t}


def _facets_matched(hay, exposure):
    """How many distinct exposure facets (with synonym tolerance) appear in the text."""
    return sum(1 for term in exposure if any(v in hay for v in _variants(term)))


def _primary_topic(w):
    return (w.get("primary_topic") or {}).get("id")


def _on_topic_set(focused, broad):
    """Identify the dispute's SUBJECT topic cluster(s).

    A legitimate subject cluster shows up in BOTH searches: the precise title/abstract search
    (`focused`) finds it, and the broad full-text search (`broad`) corroborates it with volume.
    A merely-tangential cluster (statin trials sharing 'cardiovascular', a GBD megastudy)
    appears only in the broad drift. So: take the focused topics that broad corroborates
    (>=2 hits). This keeps a debate that legitimately spans adjacent topics (milk->cancer:
    Nutrition + Cancer Risks) while dropping the drift.

    Fallback: if focused was thin or off-target (its topics aren't corroborated — e.g. a single
    media-studies essay that name-dropped the virus), use the dominant broad cluster instead."""
    broad_counts = Counter(t for t in (_primary_topic(w) for w in broad) if t)
    focused_topics = {t for t in (_primary_topic(w) for w in focused) if t}
    on = {t for t in focused_topics if broad_counts.get(t, 0) >= 2}
    if on:
        return on
    if not broad_counts:
        return set()
    top = broad_counts.most_common(1)[0][1]            # thin/off focused -> dominant broad cluster
    return {t for t, n in broad_counts.items() if n >= max(2, top)}


def search_openalex(question, k=20, from_year=None, min_citations=0, topic_filter=True):
    """Return up to k candidate sources for the question.

    Two-stage for precision WITHOUT position bias: (1) a precise title+abstract search and (2) a
    broad full-text search for recall; their union is then filtered to the dispute's dominant
    SUBJECT topic cluster (see _on_topic_set). Topic filtering drops tangential papers (a statin
    trial that merely shares the word "cardiovascular", a media-studies essay that name-drops the
    virus) while keeping BOTH sides of the debate — topics classify subject, not stance.
    Set topic_filter=False to skip it.

    We deliberately do NOT pre-balance across positions: harvest broadly and let the independence
    / distribution metrics surface any skew. Only works WITH an abstract are returned.
    """
    mail = os.environ.get("EPISTEMIC_CONTACT_EMAIL", "epistemic-ingest@example.org")
    kw = _keywords(question)
    base_filters = ["has_abstract:true", "is_retracted:false"]
    if from_year:
        base_filters.append("from_publication_date:{}-01-01".format(int(from_year)))
    if min_citations:
        base_filters.append("cited_by_count:>{}".format(int(min_citations) - 1))

    def _query(use_focused, per_page):
        filters = list(base_filters)
        params = {"per-page": min(max(per_page, 1), 50), "sort": "relevance_score:desc",
                  "mailto": mail}
        if use_focused:
            filters.append("title_and_abstract.search:" + kw)
        else:
            params["search"] = kw
        params["filter"] = ",".join(filters)
        try:
            return _get(_BASE + "?" + urllib.parse.urlencode(params)).get("results", [])
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
            return []

    topic_filter = topic_filter and not os.environ.get("EPISTEMIC_LOOSE_SEARCH")  # escape hatch
    # Over-fetch a generous pool (independent of k) so the topic signal is robust and enough
    # candidates survive filtering; we still return only the top k.
    focused = _query(use_focused=True, per_page=max(k, 15))   # precise: ordered first
    broad = _query(use_focused=False, per_page=50)            # broad: recall + topic signal
    on_topic = _on_topic_set(focused, broad) if topic_filter else set()
    exposure = _exposure_terms(kw) if topic_filter else []

    # Rank by how squarely a candidate is about the EXPOSURE. The hard gate (recall) keeps any
    # paper that mentions a facet at all. The STRONG tag (precision) is stricter:
    #   * single-facet question (eggs)      -> a facet anywhere (title/abstract) is strong.
    #   * multi-facet question (alcohol+bf) -> ALL facets must appear in the TITLE. A paper merely
    #     *mentioning* both somewhere is weak; one primarily ABOUT both says so in its title.
    # Strong come first (pre-selected in the UI); weak fill the rest, so nothing on-subject is lost.
    n_facets = len(exposure)
    full, partial, seen = [], [], set()
    for w in focused + broad:
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", w.get("title") or "")).strip()
        link = w.get("doi") or ((w.get("primary_location") or {}).get("landing_page_url"))
        if not title or not link:
            continue
        key = _norm_title(title)                         # collapse preprint+published dups
        if key in seen:
            continue
        if on_topic and _primary_topic(w) not in on_topic:
            continue                                     # off the dispute's subject cluster
        is_full = True
        if exposure:
            t_norm = _norm_title(title)
            hay = _norm_title(title + " " + _deinvert(w.get("abstract_inverted_index")))
            if _facets_matched(hay, exposure) == 0:     # not about the exposure at all -> drop
                continue
            is_full = (_facets_matched(t_norm, exposure) == n_facets if n_facets >= 2
                       else _facets_matched(hay, exposure) == n_facets)
        seen.add(key)
        rec = {"title": title, "url": link, "year": w.get("publication_year"),
               "why": _why(w), "cited_by": w.get("cited_by_count", 0),
               "relevance": "full" if is_full else "partial"}
        (full if is_full else partial).append(rec)
    return (full + partial)[:k]
