"""STRUCTURE layer (Layer 2): deterministic merge + entity resolution.

The LLM ingestion step (ingest/pipeline.py via prompts/ingest.md) PROPOSES links -- by
existing id, or "NEW:<label>" when it believes nothing matches. This code DISPOSES:
resolution is normalized-string + alias matching, so it is reproducible and auditable,
never dependent on LLM nondeterminism. "Propose, then deterministically resolve" is the
contract that keeps a living KB stable as contributors and models change.

Adversarial defences live here:
  * alias-splitting -- an incoming dataset name is matched against existing labels AND
    learned aliases, so one cohort can't be smuggled in under five names to fake independence.
  * duplicate sources -- same url (or title+year) are refused, so a camp can't be inflated
    by re-submitting the same study. Flooding the zone is refused at the door.
"""
import re
import datetime
import urllib.parse

HUES = ["#2E8B6F", "#B4502E", "#586A7A", "#8a6510", "#2f6296", "#7a4fa3"]


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s if s is not None else "").lower()).strip()


def slug(s):
    return re.sub(r"\s+", "_", norm(s))[:44] or "x"


_FUNDING_FALLBACK = ["Government/public", "Nonprofit/charity", "Academic/institutional",
                     "Industry", "Advocacy", "Undisclosed"]
_SIZE_CLAUSE = re.compile(
    r"[\s,(–-]*\b\d[\d,]*\s+(?:women|men|adults|participants|people|subjects|patients|cases)\b.*$",
    re.I)


def prettify_label(s):
    """Turn an id-like label into something readable for the website: underscores → spaces, and
    strip a trailing sample-size clause (e.g. 'Finnish_cohort_Knekt_1996_4697_women' →
    'Finnish cohort Knekt 1996'). Clean labels pass through unchanged."""
    s = (s or "").strip()
    if "_" in s:
        s = s.replace("_", " ")
    s = _SIZE_CLAUSE.sub("", s)
    return re.sub(r"\s+", " ", s).strip() or (s or "")


def _resolve_funding(kb, value):
    """Snap the funder to the closed funding vocabulary; default 'Undisclosed' (never assume
    independence when the text is silent). Tolerant of free-text / legacy values."""
    terms = (kb.get("vocab", {}) or {}).get("funding") or [{"label": x} for x in _FUNDING_FALLBACK]
    v = value[4:].strip() if str(value).startswith("NEW:") else (value or "")
    probe = norm(v)
    if not probe:
        return "Undisclosed"
    for t in terms:
        if norm(t["label"]) == probe or any(norm(a) == probe for a in t.get("aliases", [])):
            return t["label"]
    if "industr" in probe or "commercial" in probe or "company" in probe:
        return "Industry"
    if "advoca" in probe or "dairy council" in probe or "egg board" in probe \
            or "nutrition center" in probe or "commodity board" in probe or "meat board" in probe:
        return "Advocacy"
    if "govern" in probe or probe.startswith("nih") or "national institute" in probe \
            or "research council" in probe or "public" in probe:
        return "Government/public"
    if "charit" in probe or "foundation" in probe or "nonprofit" in probe or "society" in probe:
        return "Nonprofit/charity"
    if "univers" in probe or "academ" in probe or "institute" in probe:
        return "Academic/institutional"
    return "Undisclosed"  # incl. legacy "independent" — we don't actually know the funder type


def _unique_id(prefix, base, exists):
    cid, n = prefix + base, 2
    while exists(cid):
        cid = prefix + base + "_" + str(n)
        n += 1
    return cid


def clean_url(u):
    """Unwrap a URL that arrived markdown-wrapped — [text](url) or a bare [url] — and trim stray
    punctuation. Chatbots routinely return links this way in pasted deltas, which would otherwise
    be stored verbatim and break dedup + any later DOI/identifier enrichment."""
    u = (u or "").strip()
    m = re.search(r"\((https?://[^)\s]+)\)", u)   # markdown [text](https://…)
    if m:
        u = m.group(1)
    else:
        m = re.search(r"https?://[^\s\]\)>]+", u)  # first bare URL anywhere in the string
        u = m.group(0) if m else u
    try:
        parsed = urllib.parse.urlsplit(u)
    except ValueError:
        return ""
    # Stored source URLs are rendered as links. Keep only absolute web URLs, never executable
    # schemes (javascript:, data:) or relative values supplied by an untrusted delta.
    return u if parsed.scheme.lower() in ("http", "https") and parsed.hostname else ""


def source_key(s):
    if s.get("url"):
        return "u:" + norm(clean_url(s["url"]))
    return "t:" + norm(s.get("title")) + ":" + str(s.get("year") or "")


def _resolve_dataset(kb, proposed):
    is_new = proposed.startswith("NEW:")
    label = proposed[4:].strip() if is_new else None
    if not is_new:
        for d in kb["datasets"]:
            if d["id"] == proposed:
                return d["id"], False
    probe = norm(label or proposed)
    for d in kb["datasets"]:
        if norm(d["label"]) == probe or any(norm(a) == probe for a in d.get("aliases", [])):
            if label and norm(d["label"]) != probe and \
                    not any(norm(a) == probe for a in d.get("aliases", [])):
                d.setdefault("aliases", []).append(label)  # learn the alias
            return d["id"], False
    nice = prettify_label(label or proposed)
    cid = _unique_id("ds_", slug(nice),
                     lambda x: any(d["id"] == x for d in kb["datasets"]))
    kb["datasets"].append({"id": cid, "label": nice, "aliases": []})
    return cid, True


def _short_label(s, limit=20):
    """Trim a position's short label to <=limit chars at a WORD boundary, so the chart bar / matrix
    header never shows a mid-word cut like 'Cold-chain reintrodu'. A single over-long word is hard-
    cut as a last resort."""
    s = re.sub(r"\s+", " ", str(s if s is not None else "")).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if s[limit] != " " and " " in cut[1:]:   # only trim back when we'd cut mid-word
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:-/")


def _resolve_position(kb, proposed, short_label=None):
    is_new = proposed.startswith("NEW:")
    label = proposed[4:].strip() if is_new else None
    if not is_new:
        for p in kb["positions"]:
            if p["id"] == proposed:
                return p["id"], False
    probe = norm(label or proposed)
    for p in kb["positions"]:
        if norm(p["label"]) == probe:
            return p["id"], False
    nice = prettify_label(label or proposed)
    cid = _unique_id("pos_", slug(nice),
                     lambda x: any(p["id"] == x for p in kb["positions"]))
    entry = {"id": cid, "label": nice, "hue": HUES[len(kb["positions"]) % len(HUES)]}
    if short_label:
        entry["shortLabel"] = _short_label(short_label)  # word-boundary trim, never mid-word
    kb["positions"].append(entry)
    return cid, True


def _resolve_vocab(kb, kind, value):
    """Snap a free-text evidence/population tag to the case's controlled vocabulary
    (normalized-string + alias match), or add it as a new canonical term. Same
    'propose, then deterministically resolve' discipline as datasets: paraphrases collapse
    onto one term instead of multiplying, so the blindspot metric stays meaningful. "—" and
    empty values pass through unchanged (population may be genuinely not-applicable)."""
    if not value or value == "—":
        return value
    vocab = kb.setdefault("vocab", {})
    terms = vocab.setdefault(kind, [])
    is_new = value.startswith("NEW:")
    label = value[4:].strip() if is_new else value
    probe = norm(label)
    for t in terms:
        if norm(t["label"]) == probe or any(norm(a) == probe for a in t.get("aliases", [])):
            return t["label"]  # snap to the canonical label
    terms.append({"label": label, "aliases": []})  # genuinely new term for this case
    return label


def _resolve_factor(kb, f_label):
    # The factor convention is "reference by exact label", but models often copy the
    # "NEW:<label>" convention used for positions/datasets. Strip it so a decorated label
    # resolves to the existing factor instead of minting a phantom duplicate.
    label = f_label[4:].strip() if f_label.startswith("NEW:") else f_label
    probe = norm(label)
    for f in kb["factors"]:
        if norm(f["label"]) == probe:
            return f["id"], False
    nice = prettify_label(label)
    cid = _unique_id("f_", slug(nice),
                     lambda x: any(f["id"] == x for f in kb["factors"]))
    kb["factors"].append({"id": cid, "label": nice, "weights": {},
                          "rationale": "", "provenance": []})
    return cid, True


def merge_delta(kb, delta):
    """Fold one ingestion delta into the KB in place. Returns a change report.
    delta = {"source": {...}, "factorWeights": [...]} as produced by prompts/ingest.md."""
    report = {"addedSource": None, "duplicate": False, "offTopic": False, "newDatasets": [],
              "newPositions": [], "newFactors": [], "updatedFactors": []}
    src = delta["source"]

    # The labeller (which saw the real fetched text) can flag a source that doesn't bear on the
    # question; refuse it here, like a duplicate — so off-topic sources never pollute the metrics.
    if src.get("relevant") is False:
        report["offTopic"] = True
        report["reason"] = src.get("offTopicReason") or "not relevant to the question"
        return report

    if any(source_key(s) == source_key(src) for s in kb["sources"]):
        report["duplicate"] = True
        return report

    version = (kb["meta"].get("version", 0) or 0) + 1
    pos_id, pos_new = _resolve_position(kb, src["position"], src.get("positionShort"))
    if pos_new:
        report["newPositions"].append(pos_id)

    rests_on = []
    for d in src.get("restsOn", []):
        did, created = _resolve_dataset(kb, d)
        if created:
            report["newDatasets"].append(did)
        rests_on.append(did)

    sid = _unique_id("src_", slug(src["title"]) + "_" + str(src.get("year") or "0"),
                     lambda x: any(s["id"] == x for s in kb["sources"]))
    kb["sources"].append({
        "id": sid, "title": src["title"], "year": src.get("year"),
        "url": clean_url(src.get("url")) or None,
        "authors": [a for a in (src.get("authors") or []) if a],  # citation metadata
        "venue": src.get("venue") or "",
        "citations": src.get("citations"),
        "retracted": bool(src.get("retracted")),
        "position": pos_id,
        "evidence": _resolve_vocab(kb, "evidence", src.get("evidence", "Unspecified")),
        "funding": _resolve_funding(kb, src.get("funding")),
        "population": _resolve_vocab(kb, "population", src.get("population", "—")),
        "confidence": src.get("confidence", "unstated"),
        "restsOn": rests_on, "provenance": src.get("provenance", {}), "addedIn": version,
    })
    report["addedSource"] = sid

    for fw in delta.get("factorWeights", []):
        fid, created = _resolve_factor(kb, fw.get("factorLabel") or fw.get("factor"))
        (report["newFactors"] if created else report["updatedFactors"]).append(fid)
        factor = next(f for f in kb["factors"] if f["id"] == fid)
        factor["weights"][pos_id] = fw["weight"]
        if fw.get("rationale") and not factor.get("rationale"):
            factor["rationale"] = fw["rationale"]
        factor.setdefault("provenance", []).append(
            {"source": sid, "pos": pos_id, "quote": fw.get("quote", "")})

    kb["meta"]["version"] = version
    kb["meta"]["updated"] = now_iso()
    kb.setdefault("log", []).append({
        "version": version, "action": "add-source", "source": sid,
        "title": src["title"], "ts": kb["meta"]["updated"],
        "newDatasets": report["newDatasets"], "newPositions": report["newPositions"],
    })
    return report
