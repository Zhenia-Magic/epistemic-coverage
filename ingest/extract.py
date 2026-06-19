"""Turn a link or a document (PDF / docx / html / txt) into plain text + a title.

URL fetching and txt/html parsing use only the stdlib, so `ingest --dry-run` works with no
third-party packages. PDF (pypdf) and docx (python-docx) are imported lazily, so they are
required only if you actually feed those formats. See requirements.txt.
"""
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_CHARS = 24000  # keep the extraction prompt within a sane token budget
BLOCK_CODES = {401, 403, 429, 451}  # publisher/bot blocks worth a reader-proxy retry
# markers of a bot-wall / CAPTCHA / interstitial page — NOT real article text
_BLOCK_MARKERS = (
    "just a moment", "performing security verification", "attention required",
    "security service to protect against malicious bots", "requiring captcha",
    "enable javascript and cookies", "checking your browser", "verify you are human",
    "access denied", "returned error 403", "cloudflare", "please enable cookies",
)


def _looks_blocked(text, title):
    """True if the fetched content is a bot-wall/verification/redirect stub rather than the
    article — so we skip it instead of feeding garbage to the model (which then guesses)."""
    blob = ((title or "") + " " + (text or "")[:1500]).lower()
    if any(m in blob for m in _BLOCK_MARKERS):
        return True
    return len((text or "").strip()) < 200  # e.g. "Redirecting" — nothing usable


def _http_get(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), (r.headers.get_content_type() or "").lower()


def _reader_proxy(target):
    """Fallback for publisher/bot blocks: fetch via the r.jina.ai reader, which retrieves the
    page server-side and returns clean reader text — it gets through most academic bot-walls
    that refuse a direct urllib request. Sends the (public) URL to a third party; set
    EPISTEMIC_NO_READER=1 to disable. Returns reader text, or None on failure/disabled."""
    if os.environ.get("EPISTEMIC_NO_READER"):
        return None
    try:
        raw, _ = _http_get("https://r.jina.ai/" + target,
                           {"User-Agent": "epistemic-ingest/1.0", "Accept": "text/plain"},
                           timeout=45)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    return raw.decode("utf-8", "ignore")


def _title_from_text(txt):
    m = re.search(r"(?im)^Title:\s*(.+)$", txt)  # reader output leads with "Title: ..."
    if m:
        return m.group(1).strip()
    for line in txt.splitlines():
        if line.strip():
            return line.strip()[:200]
    return None


def _europepmc(target):
    """Rescue a blocked journal link via the Europe PMC abstract API (bot-friendly): pull the
    DOI or PMID from the URL and fetch title + abstract. Turns a hard-walled OUP/LWW/Elsevier
    link into real, citable text instead of a skip. Returns a doc dict or None."""
    doi, pmid = _doi_from(target), _pmid_from(target)
    if doi:
        q = urllib.parse.quote('DOI:"%s"' % doi)
    elif pmid:
        q = urllib.parse.quote('EXT_ID:%s AND SRC:MED' % pmid)
    else:
        return None
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=" + q +
           "&format=json&resultType=core&pageSize=1")
    try:
        raw, _ = _http_get(url, {"User-Agent": "epistemic-ingest/1.0"}, timeout=30)
        res = (json.loads(raw).get("resultList") or {}).get("result") or []
    except Exception:
        return None
    if not res:
        return None
    r = res[0]
    abstract = r.get("abstractText") or ""
    title = r.get("title") or doi or pmid
    if len(abstract.strip()) < 120:  # no usable abstract on file
        return None
    body = title + "\n\n" + _strip_html(abstract)
    if r.get("authorString"):
        body += "\n\nAuthors: " + r["authorString"]
    jnl = (r.get("journalInfo") or {}).get("journal", {}).get("title")
    if jnl:
        body += "\nJournal: {} ({})".format(jnl, r.get("pubYear", ""))
    return {"text": body[:MAX_CHARS], "title": re.sub(r"\s+", " ", title).strip(), "url": target}


# ---- structured academic APIs: get paper metadata by identifier, no scraping ----------
# Resolve a DOI / arXiv id / PubMed id straight from the URL, then ask an open scholarly API
# for title + abstract (+ funders). This sidesteps publisher bot-walls entirely for the most
# common case (an academic link), so it runs *before* we ever try to scrape the page.

def _doi_from(url):
    m = re.search(r"10\.\d{4,9}/[^\s?#\"'<>]+", url)
    return m.group(0).rstrip(").,;'\"") if m else None


def _arxiv_from(url):
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", url, re.I)
    return m.group(1) if m else None


def _pmid_from(url):
    m = re.search(r"(?:pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov/pubmed)/(\d{6,9})", url, re.I)
    return m.group(1) if m else None


def _scholar_id(target):
    """A Semantic-Scholar-style id (DOI:/arXiv:/PMID:) if the URL carries one, else None.
    Doubles as the 'is this an academic link worth an API lookup?' test."""
    doi = _doi_from(target)
    if doi:
        return "DOI:" + doi
    ax = _arxiv_from(target)
    if ax:
        return "arXiv:" + ax
    pm = _pmid_from(target)
    if pm:
        return "PMID:" + pm
    return None


def _semantic_scholar(target):
    """Semantic Scholar Graph API — all disciplines, clean plaintext abstract, no auth.
    Set SEMANTIC_SCHOLAR_API_KEY for higher rate limits. Returns a doc dict or None."""
    sid = _scholar_id(target)
    if not sid:
        return None
    fields = "title,abstract,year,venue,authors,openAccessPdf,externalIds"
    url = ("https://api.semanticscholar.org/graph/v1/paper/"
           + urllib.parse.quote(sid, safe=":") + "?fields=" + fields)
    headers = {"User-Agent": "epistemic-ingest/1.0"}
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        headers["x-api-key"] = key
    r = None
    for attempt in range(2):  # the keyless shared pool 429s often; one short retry
        try:
            raw, _ = _http_get(url, headers, timeout=30)
            r = json.loads(raw)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(3)
                continue
            return None
        except Exception:
            return None
    if r is None:
        return None
    abstract = (r.get("abstract") or "").strip()
    title = (r.get("title") or "").strip()
    if len(abstract) < 120:  # no usable abstract on file
        return None
    body = title + "\n\n" + abstract
    auth = ", ".join(a.get("name", "") for a in (r.get("authors") or [])[:8] if a.get("name"))
    if auth:
        body += "\n\nAuthors: " + auth
    if r.get("venue"):
        body += "\nVenue: {} ({})".format(r["venue"], r.get("year", ""))
    return {"text": body[:MAX_CHARS], "title": re.sub(r"\s+", " ", title).strip() or target,
            "url": target}


def _deinvert(inv):
    """OpenAlex ships abstracts as an inverted index {word: [positions]}; rebuild the text."""
    if not inv:
        return ""
    words = [(p, w) for w, positions in inv.items() for p in positions]
    words.sort()
    return " ".join(w for _, w in words)


def _oa_pdf_url(r):
    """A direct open-access PDF link from an OpenAlex work, if one is on file."""
    for loc in (r.get("best_oa_location"), r.get("primary_location")):
        if loc and loc.get("pdf_url"):
            return loc["pdf_url"]
    for loc in (r.get("locations") or []):
        if loc.get("is_oa") and loc.get("pdf_url"):
            return loc["pdf_url"]
    oa = (r.get("open_access") or {}).get("oa_url")
    return oa if oa and oa.lower().endswith(".pdf") else None


def _pdf_text(url):
    """Fetch an OA PDF and extract its text. Returns text or None — graceful when pypdf is
    absent, the link isn't really a PDF, or the fetch fails (we fall back to the abstract).
    Set EPISTEMIC_ABSTRACT_ONLY=1 to skip full-text entirely (smaller prompts / faster)."""
    if os.environ.get("EPISTEMIC_ABSTRACT_ONLY"):
        return None
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        raw, ctype = _http_get(url, {"User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}, timeout=45)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    if not (raw[:5] == b"%PDF-" or "pdf" in ctype):     # a landing page / paywall, not a PDF
        return None
    try:
        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(raw)).pages)
    except Exception:
        return None
    return re.sub(r"[ \t]+", " ", text).strip() or None


# headings that introduce a funding / conflict-of-interest declaration in a paper's body
_FUND_HDR = re.compile(
    r"(?i)\b(funding|financial support|role of the funding source|acknowledg(?:e)?ments?|"
    r"conflicts? of interest|competing interests?|declaration of (?:competing )?interests?|"
    r"disclosure(?:s)?)\b")


def _funding_block(text):
    """Pull the funding / acknowledgments / COI passages out of full text (they usually sit near
    the end, beyond any truncation), so the labeller sees them regardless of length."""
    out = []
    for m in _FUND_HDR.finditer(text):
        seg = re.sub(r"\s+", " ", text[m.start():m.start() + 400]).strip()
        if seg not in out:
            out.append(seg)
        if len(out) >= 3:
            break
    return " … ".join(out)[:1200]


def _openalex(target):
    """OpenAlex by DOI or PMID — 250M+ works. Uses the FULL open-access PDF text when one is
    available (richer for labelling, and carries the funding/COI statement the abstract omits);
    falls back to the abstract otherwise. Returns a doc dict or None."""
    doi, pmid = _doi_from(target), _pmid_from(target)
    if doi:
        ident = "https://doi.org/" + urllib.parse.quote(doi)
    elif pmid:
        ident = "pmid:" + pmid
    else:
        return None
    mail = os.environ.get("EPISTEMIC_CONTACT_EMAIL", "epistemic-ingest@example.org")
    url = "https://api.openalex.org/works/" + ident + "?mailto=" + urllib.parse.quote(mail)
    try:
        raw, _ = _http_get(url, {"User-Agent": "epistemic-ingest/1.0"}, timeout=30)
        r = json.loads(raw)
    except Exception:
        return None
    title = (r.get("title") or "").strip()
    abstract = _deinvert(r.get("abstract_inverted_index"))
    pdf_url = _oa_pdf_url(r)
    full = _pdf_text(pdf_url) if pdf_url else None
    if len(abstract) < 120 and not (full and len(full) >= 400):
        return None                                     # nothing usable to label

    # Assemble so the key signals survive truncation: title, abstract, and funding come FIRST
    # (kept even in batch mode's tighter per-source cap); the full body follows for richer labels.
    parts = [title]
    if abstract:
        parts.append(abstract)
    funding = _funding_block(full) if full else ""
    if not funding:
        grants = [g.get("funder_display_name") for g in (r.get("grants") or [])
                  if g.get("funder_display_name")]
        if grants:
            funding = "; ".join(dict.fromkeys(grants))
    if funding:
        parts.append("Funding / disclosures: " + funding)
    venue = ((r.get("primary_location") or {}).get("source") or {}).get("display_name")
    if venue:
        parts.append("Venue: {} ({})".format(venue, r.get("publication_year", "")))
    if full:
        parts.append("--- full text ---\n" + full)
    body = "\n\n".join(parts)
    return {"text": body[:MAX_CHARS], "title": re.sub(r"\s+", " ", title).strip() or target,
            "url": target}


def _arxiv(target):
    """arXiv Atom API — clean abstract for physics/CS preprints, no auth. arXiv papers often
    lack a DOI, so OpenAlex/Europe PMC miss them; this is the reliable path. Returns a doc/None."""
    ax = _arxiv_from(target)
    if not ax:
        return None
    try:
        raw, _ = _http_get("http://export.arxiv.org/api/query?id_list=" + ax,
                           {"User-Agent": "epistemic-ingest/1.0"}, timeout=30)
        xml = raw.decode("utf-8", "ignore")
    except Exception:
        return None
    entry = re.search(r"<entry>(.*?)</entry>", xml, re.S)  # skip the feed-level title
    if not entry:
        return None
    blob = entry.group(1)
    tm = re.search(r"<title>(.*?)</title>", blob, re.S)
    sm = re.search(r"<summary>(.*?)</summary>", blob, re.S)
    title = re.sub(r"\s+", " ", (tm.group(1) if tm else "")).strip()
    abstract = re.sub(r"\s+", " ", (sm.group(1) if sm else "")).strip()
    if len(abstract) < 120:
        return None
    body = title + "\n\n" + abstract
    authors = re.findall(r"<author>\s*<name>(.*?)</name>", blob, re.S)
    if authors:
        body += "\n\nAuthors: " + ", ".join(a.strip() for a in authors[:8])
    return {"text": body[:MAX_CHARS], "title": title or ("arXiv:" + ax), "url": target}


_API_LABELS = {"_semantic_scholar": "Semantic Scholar", "_openalex": "OpenAlex",
               "_europepmc": "Europe PMC", "_arxiv": "arXiv"}


def _crossref_funders(doi):
    """Funder names for a DOI from Crossref's `funder` array — keyless, and more populated than
    OpenAlex grants. Lets the labeller classify funding (Industry/Government/…) even when the
    abstract carries no funding statement. Returns a de-duped list of names (possibly empty)."""
    mail = os.environ.get("EPISTEMIC_CONTACT_EMAIL", "epistemic-ingest@example.org")
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi) + "?mailto=" + \
        urllib.parse.quote(mail)
    try:
        raw, _ = _http_get(url, {"User-Agent": "epistemic-ingest/1.0 (mailto:%s)" % mail}, timeout=20)
        msg = json.loads(raw).get("message", {})
    except Exception:
        return []
    names = [f.get("name") for f in (msg.get("funder") or []) if f.get("name")]
    return list(dict.fromkeys(n.strip() for n in names if n.strip()))  # de-dup, keep order


def _enrich_funding(doc, target):
    """If the fetched text has no funding statement, look up funders by DOI (Crossref) and append
    a 'Funding:' line so the labeller has something to classify. No-op when already present."""
    if not doc or re.search(r"Funding(?: / disclosures)?:", doc.get("text", "")):
        return doc                                       # already carries a funding line
    doi = _doi_from(target)
    if not doi:
        return doc
    funders = _crossref_funders(doi)
    if funders:
        doc["text"] = (doc["text"] + "\n\nFunding: " + "; ".join(funders))[:MAX_CHARS]
    return doc


def _academic(target):
    """Try the structured scholarly APIs in order. Returns (doc, human_label) or (None, None).
    Order (see loop below): OpenAlex → arXiv → Semantic Scholar → Europe PMC. The returned doc is
    enriched with Crossref funders when its abstract carries no funding statement."""
    if os.environ.get("EPISTEMIC_NO_API"):
        return None, None
    # OpenAlex first: reliable on the polite pool (mailto), and adds funders. arXiv API for
    # preprints (often DOI-less). Semantic Scholar next (broad, but keyless pool is throttled).
    # Europe PMC last (biomedical, abstract-only).
    for fn in (_openalex, _arxiv, _semantic_scholar, _europepmc):
        doc = fn(target)
        if doc:
            return _enrich_funding(doc, target), _API_LABELS.get(fn.__name__, fn.__name__)
    return None, None


def _fallback(target):
    """When a direct fetch is blocked/empty: try the reader proxy, then the scholarly APIs.
    Returns a usable doc, or raises SystemExit so fetch_docs records an honest skip."""
    txt = _reader_proxy(target)
    if txt and not _looks_blocked(txt, _title_from_text(txt)):
        print("  (fetched via reader proxy)")
        return {"text": txt[:MAX_CHARS], "title": _title_from_text(txt), "url": target}
    doc, label = _academic(target)
    if doc:
        print("  (blocked → fetched abstract via {})".format(label))
        return doc
    raise SystemExit("blocked / no readable text (bot-wall or CAPTCHA): " + target)


def _strip_html(html):
    html = re.sub(r"(?is)<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _title_from_html(html):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def clean_url(u):
    """Unwrap a URL that arrived wrapped in markdown — [text](url), <url>, or a bare [url] —
    and trim stray punctuation. Models routinely return links this way inside JSON fields,
    which would otherwise look like a local path and fail to fetch."""
    u = (u or "").strip()
    m = re.search(r"\((https?://[^)\s]+)\)", u)   # markdown [text](https://…)
    if m:
        return m.group(1)
    m = re.search(r"https?://[^\s\]\)>]+", u)      # first bare URL anywhere in the string
    return m.group(0) if m else u


def extract_text(target):
    """target = http(s) URL or a local file path. Returns {text, title, url}."""
    target = clean_url(target)
    if re.match(r"^https?://", target, re.I):
        # Academic link? Resolve it through a structured scholarly API first — this returns a
        # clean abstract (+ funders) and never trips a publisher bot-wall. Falls through to
        # scraping only if the APIs have nothing on file. Set EPISTEMIC_NO_API=1 to disable.
        if _scholar_id(target):
            doc, label = _academic(target)
            if doc:
                print("  (fetched metadata via {})".format(label))
                return doc
        headers = {
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            raw, ctype = _http_get(target, headers)
        except urllib.error.HTTPError as e:
            if e.code in BLOCK_CODES:
                return _fallback(target)            # 403/429 etc. → proxy, then Europe PMC
            raise SystemExit("could not fetch {} ({})".format(target, e))
        except urllib.error.URLError:
            return _fallback(target)                # SSL/connection error
        if target.lower().endswith(".pdf") or ctype == "application/pdf":
            return _from_pdf(raw, target, url=target)
        html = raw.decode("utf-8", "ignore")
        text, title = _strip_html(html)[:MAX_CHARS], _title_from_html(html)
        if _looks_blocked(text, title):            # 200 OK but it's a Cloudflare interstitial
            return _fallback(target)
        return {"text": text, "title": title, "url": target}

    ext = os.path.splitext(target)[1].lower()
    if ext == ".pdf":
        with open(target, "rb") as f:
            return _from_pdf(f.read(), os.path.basename(target), url=None)
    if ext == ".docx":
        return _from_docx(target)
    with open(target, encoding="utf-8", errors="ignore") as f:
        txt = f.read()
    if ext in (".html", ".htm"):
        return {"text": _strip_html(txt)[:MAX_CHARS], "title": _title_from_html(txt), "url": None}
    return {"text": txt[:MAX_CHARS], "title": os.path.basename(target), "url": None}


def _from_pdf(data, title, url):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("PDF support needs pypdf:  pip install pypdf")
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
    return {"text": text[:MAX_CHARS], "title": title, "url": url}


def _from_docx(path):
    try:
        import docx
    except ImportError:
        raise SystemExit("DOCX support needs python-docx:  pip install python-docx")
    d = docx.Document(path)
    text = "\n".join(p.text for p in d.paragraphs)
    return {"text": text[:MAX_CHARS], "title": os.path.basename(path), "url": None}
