"""A tiny stdlib web UI for the epistemic CLI — no extra dependencies.

It wraps the same engine the CLI uses (discover/extract are NOT done here; the researcher pastes
the chatbot's JSON back). Endpoints:

  GET  /                 the single-page UI (ui/app.html)
  GET  /viewer           the built viewer (viewer/index.html)
  GET  /api/cases        list cases (id, question, version, #sources)
  POST /api/init         {id, question}            -> create an empty case
  POST /api/research     {id, k}                   -> the one-paste research prompt
  POST /api/add          {id, text}                -> merge pasted JSON (object or array) + diff
  POST /api/build        {}                         -> bake the viewer with every case

Everything that touches numbers stays in engine/*, so the UI is just a thin, deterministic shell.
"""
import base64
import glob
import json
import os
import tempfile
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "cases")
UI = os.path.dirname(os.path.abspath(__file__))

from engine.assess import assess, diff_assessments
from engine.merge import merge_delta, source_key, norm
from engine.schema import empty_kb
from ingest import llm
from ingest.extract import extract_text, clean_url
from ingest.pipeline import (build_research_prompt, build_discover_prompt, build_extract_prompt,
                             build_batch_extract_prompt, extract_prompts, _parse_json)


def has_key():
    return llm.has_key()


# provider id (from the UI dropdown) -> env var the LLM layer reads
_PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY", "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
}


def set_key_op(key, provider=None):
    """Accept an API key pasted in the local UI (this machine, this session) when none is in the
    env. The provider comes from the dropdown; if omitted we guess (sk-ant… = Anthropic, else
    OpenAI). Sets it in-process so the LLM layer picks it up. Not written to disk — put it in .env
    for persistence."""
    key = (key or "").strip()
    if not key:
        raise ValueError("Paste an API key.")
    if provider:
        env = _PROVIDER_ENV.get(provider.lower())
        if not env:
            raise ValueError("Unknown provider: " + provider)
    else:
        env = "ANTHROPIC_API_KEY" if key.startswith("sk-ant") else "OPENAI_API_KEY"
    # clear the other keys we manage so the chosen provider actually wins the dispatch order
    for e in set(_PROVIDER_ENV.values()):
        if e != env:
            os.environ.pop(e, None)
    os.environ[env] = key
    return {"hasKey": has_key(), "model": llm.active_model()}


# --- progress log (visible in the UI via /api/progress, and on the server console) -----------
_PROGRESS = []


def log(msg):
    line = time.strftime("%H:%M:%S") + "  " + msg
    _PROGRESS.append(line)
    if len(_PROGRESS) > 300:
        del _PROGRESS[:-300]
    print(line, flush=True)
    return line


def log_reset():
    _PROGRESS.clear()


llm.LOG = log  # so retry/backoff notices flow into the progress log too


def _read(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(p, o):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(o, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _case_path(cid):
    return os.path.join(CASES, cid + ".kb.json")


def list_cases():
    out = []
    for p in sorted(glob.glob(os.path.join(CASES, "*.kb.json"))):
        try:
            kb = _read(p)
        except Exception:
            continue
        # id is the FILENAME (so _case_path round-trips) — NOT kb.meta.id, which can differ for
        # pulled portal cases (file is named by portal id, but meta.id keeps the original slug).
        cid = os.path.basename(p)[:-len(".kb.json")]
        out.append({"id": cid, "question": kb["meta"].get("question", ""),
                    "version": kb["meta"].get("version", 0), "sources": len(kb.get("sources", []))})
    return out


def init_case(cid, question):
    cid = "".join(c for c in (cid or "").strip().lower().replace(" ", "_") if c.isalnum() or c == "_")
    if not cid:                                  # no id given -> derive one from the question
        from engine.merge import slug
        cid = slug(question or "")[:40]
    if not cid:
        raise ValueError("Please enter a question (the id is derived from it).")
    path = _case_path(cid)
    if os.path.exists(path):                      # keep it unique rather than erroring
        n = 2
        while os.path.exists(_case_path("{}_{}".format(cid, n))):
            n += 1
        cid = "{}_{}".format(cid, n)
        path = _case_path(cid)
    os.makedirs(CASES, exist_ok=True)
    _write(path, empty_kb(cid, question or ""))
    return {"id": cid}


def research_prompt(cid, k):
    return {"prompt": build_research_prompt(_read(_case_path(cid)), k=int(k or 20))}


def _merge_list(cid, deltas):
    """Merge a list of deltas one at a time. Returns per-source result dicts with recomputed
    diff lines — never raises; per-item errors are reported in the result."""
    path = _case_path(cid)
    results = []
    for d in deltas:
        title = (d.get("source") or {}).get("title") if isinstance(d, dict) else None
        try:
            kb = _read(path)
            before = assess(kb)
            rep = merge_delta(kb, d)
            if rep.get("offTopic"):
                results.append({"status": "off-topic", "title": title,
                                "error": rep.get("reason", "doesn't bear on the question")})
                continue
            if rep["duplicate"]:
                results.append({"status": "duplicate", "title": title})
                continue
            after = assess(kb)
            lines = diff_assessments(before, after)
            kb["log"][-1]["diff"] = lines
            _write(path, kb)
            results.append({"status": "added", "title": title, "id": rep["addedSource"],
                            "version": kb["meta"]["version"], "diff": lines,
                            "newDatasets": rep["newDatasets"], "newPositions": rep["newPositions"],
                            "newFactors": rep["newFactors"]})
        except Exception as e:
            results.append({"status": "error", "title": title, "error": str(e)})
    return results


def add_payload(cid, text):
    """Parse pasted JSON (one delta or an array) and merge each."""
    data = _parse_json(text)  # tolerant: code fences / trailing prose ok
    return {"results": _merge_list(cid, data if isinstance(data, list) else [data])}


def discover_op(cid, k, apply, source="api", deep=False):
    """Find candidate sources. `source` picks the engine: 'api' = OpenAlex (no key, works in
    manual mode too); 'web' = LLM web search (needs a key — or paste prompt in manual mode);
    'both' = merge the two, deduped. deep=True runs an exhaustive multi-search web pass."""
    q = _read(_case_path(cid))["meta"]["question"]
    k = int(k or 8)
    source = (source or "api").lower()
    want_api = source in ("api", "both") and not os.environ.get("EPISTEMIC_NO_API")
    want_web = source in ("web", "both")

    cands, seen = [], set()

    def _merge(arr):
        for c in arr or []:
            if not isinstance(c, dict) or not c.get("url"):
                continue
            key = norm(c.get("title") or "") or c.get("url")
            if key in seen:
                continue
            seen.add(key)
            cands.append(c)

    if want_api:
        try:
            from ingest.search import search_openalex
            api_cands = search_openalex(q, k)
        except Exception:
            api_cands = []
        if api_cands:
            log("found {} candidate(s) via OpenAlex.".format(len(api_cands)))
            _merge(api_cands)
        elif source == "api":
            want_web = True
            log("OpenAlex returned nothing; falling back to LLM web search.")

    if want_web:
        prompt = build_discover_prompt(q, k, deep=deep)
        if not apply:  # manual: can't call the model — hand back the prompt to paste
            return {"mode": "manual", "prompt": prompt, "candidates": cands}
        log("searching the web via {}{}…".format(
            llm.active_model(), " (deep research)" if deep else ""))
        before = len(cands)
        _merge([c for c in (_parse_json(llm.discover(prompt, deep=deep)) or []) if isinstance(c, dict)])
        log("web search added {} new candidate(s).".format(len(cands) - before))

    log("{} candidate(s) total.".format(len(cands)))
    return {"mode": "auto", "candidates": cands}


def extract_op(cid, urls, apply, batch=5, max_text=4000):
    """The grounded step: WE fetch each URL's real text, then build the extraction prompt
    (MANUAL) or run it through the model (AUTO). Two reliability features:
      * skip URLs already in the KB BEFORE fetching/labelling — so re-running after a failure
        never re-spends credits on sources already added.
      * AUTO merges batch-by-batch, persisting after each, so a mid-run error keeps finished work.
    Unfetchable pages are skipped and reported, never guessed."""
    batch, max_text = int(batch or 5), int(max_text or 4000)
    urls = [clean_url(u) for u in (urls or []) if u]
    if not urls:
        raise ValueError("No source URLs to fetch.")
    path = _case_path(cid)
    existing = {source_key(s) for s in _read(path)["sources"]}
    todo, already = [], 0
    for u in urls:
        if ("u:" + norm(u)) in existing:
            already += 1
        else:
            todo.append(u)
    if already:
        log("skipping {} source(s) already in the KB (no re-spend).".format(already))
    if not todo:
        log("nothing new to do — all selected sources are already in the KB.")
        return {"mode": "auto" if apply else "manual", "fetched": 0, "skipped": [],
                "results": [], "prompts": []}

    log("fetching real text for {} source(s)…".format(len(todo)))
    docs, skipped = [], []
    for i, u in enumerate(todo, 1):
        try:
            docs.append(extract_text(u))
            log("  fetched {}/{}: {}".format(i, len(todo), u))
        except (Exception, SystemExit) as e:
            skipped.append({"target": u, "error": str(e)})
            log("  skipped {}/{}: {} — {}".format(i, len(todo), u, e))
    if not docs:
        return {"mode": "auto" if apply else "manual", "fetched": 0, "skipped": skipped,
                "results": [], "prompts": []}

    if not apply:
        prompts = extract_prompts(_read(path), docs, batch=batch, max_text=max_text)
        log("built {} extraction prompt(s) to paste.".format(len(prompts)))
        return {"mode": "manual", "fetched": len(docs), "skipped": skipped, "prompts": prompts}

    nbatches = (len(docs) + batch - 1) // batch
    results = []
    for bi in range(0, len(docs), batch):
        group = docs[bi:bi + batch]
        n = bi // batch + 1
        log("labelling batch {}/{} ({} sources) via {}…".format(n, nbatches, len(group), llm.active_model()))
        kbnow = _read(path)  # fresh each batch so the prompt sees entities added earlier → reuse
        arr = _parse_json(llm.complete(build_batch_extract_prompt(kbnow, group, max_text)))
        if isinstance(arr, dict):
            arr = [arr]
        for delta, doc in zip(arr, group):
            delta.setdefault("source", {})
            if doc.get("url") and not delta["source"].get("url"):
                delta["source"]["url"] = doc["url"]
            if doc.get("title") and not delta["source"].get("title"):
                delta["source"]["title"] = doc["title"]
        res = _merge_list(cid, arr)  # persists to disk immediately → resume-safe
        added = sum(1 for r in res if r.get("status") == "added")
        log("  batch {}/{}: +{} added".format(n, nbatches, added))
        results += res
    return {"mode": "auto", "fetched": len(docs), "skipped": skipped, "results": results}


def run_all_op(cid, k, source="api", deep=False):
    """One click: discover → fetch real text → label → merge. AUTO only (needs an API key);
    manual mode can't run unattended because each step is a copy/paste. Re-runnable: if it
    fails partway, finished sources are saved and skipped on the next run."""
    if not has_key():
        raise ValueError("'Do it all' needs an API key (Anthropic, OpenAI, DeepSeek, Mistral, "
                         "Groq, Gemini, or OpenRouter). Without one, use Find → Fetch & label to "
                         "run the steps by hand.")
    log("=== Do it all · model: {} ===".format(llm.active_model()))
    cands = discover_op(cid, k, apply=True, source=source, deep=deep).get("candidates", [])
    urls = [c.get("url") for c in cands if c.get("url")]
    if not urls:
        log("no candidate URLs returned.")
        return {"candidates": 0, "fetched": 0, "skipped": [], "results": []}
    ex = extract_op(cid, urls, apply=True, batch=8)
    added = sum(1 for r in ex.get("results", []) if r.get("status") == "added")
    log("=== done — {} source(s) added ===".format(added))
    return {"candidates": len(cands), "fetched": ex.get("fetched", 0),
            "skipped": ex.get("skipped", []), "results": ex.get("results", [])}


def add_file_op(cid, filename, b64, apply):
    """Add a single source from an uploaded document (PDF / docx / txt). We extract the real
    text, then build the extraction prompt (MANUAL) or run it (AUTO)."""
    ext = os.path.splitext(filename or "")[1] or ".txt"
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode((b64 or "").split(",")[-1]))
        doc = extract_text(tmp)
    finally:
        os.unlink(tmp)
    doc["title"] = os.path.basename(filename) if filename else (doc.get("title") or "document")
    kb = _read(_case_path(cid))
    if not apply:
        return {"mode": "manual", "prompt": build_extract_prompt(kb, doc), "title": doc["title"]}
    from ingest import llm
    delta = _parse_json(llm.complete(build_extract_prompt(kb, doc)))
    delta.setdefault("source", {}).setdefault("title", doc["title"])
    return {"mode": "auto", "results": _merge_list(cid, [delta])}


def _counts(kb):
    posc, dsc, popc, evc = {}, {}, {}, {}
    for s in kb["sources"]:
        posc[s["position"]] = posc.get(s["position"], 0) + 1
        for d in s.get("restsOn", []):
            dsc[d] = dsc.get(d, 0) + 1
        if s.get("population"):
            popc[s["population"]] = popc.get(s["population"], 0) + 1
        if s.get("evidence"):
            evc[s["evidence"]] = evc.get(s["evidence"], 0) + 1
    return posc, dsc, popc, evc


def entities_op(cid):
    """Every entity with usage counts + likely-duplicate suggestions, for the Curate panel."""
    from engine import curate
    kb = _read(_case_path(cid))
    posc, dsc, popc, evc = _counts(kb)
    vocab = kb.get("vocab", {})
    return {
        "position": [{"id": p["id"], "label": p["label"], "count": posc.get(p["id"], 0)} for p in kb["positions"]],
        "dataset": [{"id": d["id"], "label": d["label"], "count": dsc.get(d["id"], 0)} for d in kb["datasets"]],
        "factor": [{"id": f["id"], "label": f["label"], "count": 0} for f in kb["factors"]],
        "population": [{"id": t["label"], "label": t["label"], "count": popc.get(t["label"], 0)} for t in vocab.get("population", [])],
        "evidence": [{"id": t["label"], "label": t["label"], "count": evc.get(t["label"], 0)} for t in vocab.get("evidence", [])],
        "suggestions": curate.suggest_duplicates(kb),
    }


def merge_op(cid, kind, src, dst):
    from engine import curate
    kb = _read(_case_path(cid))
    if kind in ("position", "dataset", "factor"):
        fn = {"position": curate.merge_positions, "dataset": curate.merge_datasets,
              "factor": curate.merge_factors}[kind]
        rep = fn(kb, src, dst)
    else:
        rep = curate.merge_vocab(kb, kind, src, dst)
    _write(_case_path(cid), kb)
    log(rep["summary"])
    try:
        build_all()
    except Exception:
        pass
    return {"report": rep, "entities": entities_op(cid)}


def rename_op(cid, kind, ref, label):
    from engine import curate
    kb = _read(_case_path(cid))
    rep = curate.rename(kb, kind, ref, label)
    _write(_case_path(cid), kb)
    log(rep["summary"])
    try:
        build_all()
    except Exception:
        pass
    return {"report": rep, "entities": entities_op(cid)}


def tidy_op(cid):
    from engine import curate
    kb = _read(_case_path(cid))
    rep = curate.tidy_labels(kb)
    _write(_case_path(cid), kb)
    log(rep["summary"])
    try:
        build_all()
    except Exception:
        pass
    return {"report": rep, "entities": entities_op(cid)}


# ---- portal sync (push / pull) -------------------------------------------------------------
# This local console is the power-user workstation: pull a question from a shared portal, harvest
# / label it here with your own env key (fully automatic), then push the result back. The portal
# itself stays key-free; the LLM work happens locally. Mirrors `cli.py pull/push`.

def _portal_base(url):
    from app import client
    return client.portal_url(url)   # explicit url, else EPISTEMIC_PORTAL; raises if neither


def portal_list_op(url):
    from app import client
    base = _portal_base(url)
    return {"url": base, "questions": client.list_questions(base)}


def portal_pull_op(url, qid):
    """Fetch a portal question into a LOCAL case file, stamping its lineage so push knows it."""
    from app import client
    base = _portal_base(url)
    data = client.get_question(base, qid)
    kb = data["kb"]
    kb.setdefault("meta", {})["portal"] = {"id": data["id"], "baseVersion": data["version"],
                                           "url": base}
    _write(_case_path(data["id"]), kb)
    try:
        build_all()
    except Exception:
        pass
    return {"id": data["id"], "question": data["question"], "version": data["version"]}


def portal_push_op(url, cid):
    """Push a local case to the portal — create if it has no lineage, else version-checked update."""
    from app import client
    base = _portal_base(url)
    token = client.admin_token()
    kb = _read(_case_path(cid))
    pm = (kb.get("meta") or {}).get("portal") or {}
    qid, expected = pm.get("id"), pm.get("baseVersion", 0)
    if not qid:
        created = client.create_question(base, kb["meta"]["question"])
        qid, expected = created["id"], 0
    res = client.put_kb(base, qid, kb, expected, token=token)
    kb["meta"]["portal"] = {"id": qid, "baseVersion": res["version"], "url": base}
    _write(_case_path(cid), kb)
    return {"id": qid, "version": res["version"], "question": kb["meta"]["question"]}


def build_all():
    from cli import _build_viewer  # lazy: avoid import cycle at module load
    paths = sorted(glob.glob(os.path.join(CASES, "*.kb.json")))
    _build_viewer(paths)
    return {"cases": len(paths)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")  # never serve a stale page/state
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, ctype):
        if not os.path.exists(path):
            return self._send(404, {"error": "not found"})
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._file(os.path.join(UI, "app.html"), "text/html; charset=utf-8")
        if self.path.startswith("/viewer"):
            try:
                build_all()  # rebuild so the viewer always reflects the latest KBs
            except Exception:
                pass
            return self._file(os.path.join(ROOT, "viewer", "index.html"), "text/html; charset=utf-8")
        if self.path == "/api/cases":
            return self._send(200, {"cases": list_cases(), "hasKey": has_key(),
                                    "model": llm.active_model(),
                                    "portal": os.environ.get("EPISTEMIC_PORTAL", "")})
        if self.path == "/api/progress":
            return self._send(200, {"lines": list(_PROGRESS)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "invalid request body"})
        if self.path in ("/api/run-all", "/api/discover", "/api/extract"):
            log_reset()  # fresh progress log per user-initiated long operation
        try:
            if self.path == "/api/init":
                return self._send(200, init_case(body.get("id"), body.get("question")))
            if self.path == "/api/research":
                return self._send(200, research_prompt(body.get("id"), body.get("k")))
            if self.path == "/api/discover":
                return self._send(200, discover_op(body.get("id"), body.get("k"), body.get("apply"),
                                                   body.get("source", "api"), body.get("deep", False)))
            if self.path == "/api/extract":
                return self._send(200, extract_op(body.get("id"), body.get("urls"), body.get("apply"),
                                                  body.get("batch", 5), body.get("maxText", 4000)))
            if self.path == "/api/add-file":
                return self._send(200, add_file_op(body.get("id"), body.get("filename"),
                                                   body.get("b64"), body.get("apply")))
            if self.path == "/api/run-all":
                return self._send(200, run_all_op(body.get("id"), body.get("k"),
                                                  body.get("source", "api"), body.get("deep", False)))
            if self.path == "/api/add":
                return self._send(200, add_payload(body.get("id"), body.get("text") or ""))
            if self.path == "/api/entities":
                return self._send(200, entities_op(body.get("id")))
            if self.path == "/api/merge":
                return self._send(200, merge_op(body.get("id"), body.get("type"),
                                                body.get("src"), body.get("dst")))
            if self.path == "/api/rename":
                return self._send(200, rename_op(body.get("id"), body.get("type"),
                                                 body.get("ref"), body.get("label")))
            if self.path == "/api/tidy":
                return self._send(200, tidy_op(body.get("id")))
            if self.path == "/api/key":
                return self._send(200, set_key_op(body.get("key"), body.get("provider")))
            if self.path == "/api/portal/list":
                return self._send(200, portal_list_op(body.get("url")))
            if self.path == "/api/portal/pull":
                return self._send(200, portal_pull_op(body.get("url"), body.get("id")))
            if self.path == "/api/portal/push":
                return self._send(200, portal_push_op(body.get("url"), body.get("id")))
            if self.path == "/api/build":
                return self._send(200, build_all())
        except (Exception, SystemExit) as e:  # the LLM layer raises SystemExit with the API reason
            return self._send(400, {"error": str(e)})
        self._send(404, {"error": "not found"})


def run(port=8765, open_browser=True):
    from app.env import load_dotenv
    load_dotenv()  # keys/config from .env
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://localhost:{}/".format(port)
    print("Epistemic Coverage UI → {}   (Ctrl-C to stop)".format(url))
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        srv.server_close()
