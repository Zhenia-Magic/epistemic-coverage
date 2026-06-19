"""The hosted portal API (deployment layer, Phase 2).

A thin, store-backed aggregator. It holds the canonical knowledge bases, lets people browse and
search questions, and accepts pushed KBs from local CLIs (`cli.py push`). It deliberately does
**no LLM work and holds no API key** — the expensive discover/fetch/label happens locally in the
contributor's CLI with their own env key; the portal only stores and serves the deterministic
result. Merging is pure stdlib, so the server stays cheap and key-free.

Endpoints (JSON):
  GET  /api/questions?search=&limit=     -> [{id, question, counts, ...}]
  POST /api/questions  {question}        -> {id, ...}                     (create)
  GET  /api/questions/{id}               -> {id, question, version, kb}   (pull)
  PUT  /api/questions/{id}  {kb, expected_version, contributor} -> {version}   (push)
  GET  /api/questions/{id}/log           -> [contributions]
  GET  /healthz                          -> ok

Run locally:  python -m app.portal --port 8800     (sqlite store, no key, no deps)
Production:   gunicorn-style not needed; ThreadingHTTPServer behind Railway is fine for this load.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import store, web


def _read_question(qid):
    q = store.get_question(qid, with_kb=True)
    if not q:
        return None
    return {"id": q["id"], "question": q["question"], "version": q["version"], "kb": q["kb"]}


def _norm_delta(it):
    """Accept either a full delta {source, factorWeights} or a bare source object."""
    if not isinstance(it, dict):
        return None
    if "source" in it:
        return it
    if it.get("title") and it.get("position"):
        return {"source": it, "factorWeights": it.get("factorWeights", [])}
    return None


def _apply_delta(qid, q, delta, contributor):
    """Merge one or many deltas into the question's KB (deterministic, no key), version-checked."""
    from engine.merge import merge_delta
    kb, base = q["kb"], q["version"]
    items = delta if isinstance(delta, list) else [delta]
    added = dups = 0
    for it in items:
        d = _norm_delta(it)
        if not d:
            continue
        rep = merge_delta(kb, d)
        if rep.get("duplicate"):
            dups += 1
        elif rep.get("addedSource"):
            added += 1
    try:
        version = store.save_kb(qid, kb, base)
    except store.Conflict:
        return {"error": "someone else updated this question — reload and re-import"}
    store.log_contribution(qid, contributor or "anonymous", "add-sources",
                           "{} added, {} duplicate".format(added, dups))
    return {"added": added, "duplicates": dups, "version": version}


class Handler(BaseHTTPRequestHandler):
    server_version = "epistemic-portal/0.1"

    # -- helpers --
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")  # browser clients
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html):
        body = (html or "<h1>Not found</h1>").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _get_q(self, qid):
        return store.get_question(qid, with_kb=True)

    def _parts(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        return [p for p in path.split("/") if p]

    def _query(self):
        if "?" not in self.path:
            return {}
        from urllib.parse import parse_qs
        return {k: v[0] for k, v in parse_qs(self.path.split("?", 1)[1]).items()}

    def log_message(self, *a):  # quieter
        pass

    def do_OPTIONS(self):
        self._send(200, {})

    # -- routes --
    def do_GET(self):
        p = self._parts()
        if p == ["healthz"]:
            return self._send(200, {"ok": True})
        # --- browser pages ---
        if p == []:
            return self._send_html(200, web.home_html())
        if len(p) == 2 and p[0] == "q":
            return self._send_html(200, web.viewer_html(p[1], self._get_q) or "")
        if len(p) == 3 and p[0] == "q" and p[2] == "add":
            return self._send_html(200, web.contribute_html(p[1], self._get_q) or "")
        # --- JSON API ---
        if p == ["api", "questions"]:
            q = self._query()
            limit = int(q.get("limit") or 100)
            return self._send(200, {"questions": store.list_questions(q.get("search"), limit)})
        if len(p) == 3 and p[:2] == ["api", "questions"]:
            data = _read_question(p[2])
            return self._send(200, data) if data else self._send(404, {"error": "no such question"})
        if len(p) == 4 and p[:2] == ["api", "questions"] and p[3] == "log":
            return self._send(200, {"log": store.contributions(p[2])})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self._parts()
        if p == ["api", "questions"]:
            body = self._body()
            question = (body.get("question") or "").strip()
            if not question:
                return self._send(400, {"error": "question text required"})
            q = store.create_question(question, body.get("contributor") or "anonymous")
            return self._send(201, {"id": q["id"], "question": q["question"], "version": 0})
        # contribute flow: /api/questions/{id}/{discover|fetch|delta} — all keyless server-side
        if len(p) == 4 and p[:2] == ["api", "questions"]:
            qid, action = p[2], p[3]
            q = self._get_q(qid)
            if not q:
                return self._send(404, {"error": "no such question"})
            body = self._body()
            if action == "discover":
                from ingest.search import search_openalex
                cands = search_openalex(q["question"], int(body.get("k") or 10))
                return self._send(200, {"candidates": cands})
            if action == "fetch":
                from ingest.pipeline import fetch_docs, build_batch_extract_prompt
                urls = [u for u in (body.get("urls") or []) if u]
                docs, skipped = fetch_docs(urls)
                # ONE bundle over all fetched sources -> one file to upload, one JSON array back.
                # Richer per-source text than the old multi-prompt batches (it's a file, not a
                # paste box), so labelling sees more of each paper.
                bundle = build_batch_extract_prompt(q["kb"], docs, max_text=8000) if docs else ""
                return self._send(200, {"bundle": bundle, "fetched": len(docs),
                                        "skipped": skipped})
            if action == "delta":
                return self._send(200, _apply_delta(qid, q, body.get("delta"),
                                                    body.get("contributor")))
            return self._send(404, {"error": "unknown action"})
        self._send(404, {"error": "not found"})

    def do_PUT(self):
        p = self._parts()
        if len(p) == 3 and p[:2] == ["api", "questions"]:
            body = self._body()
            kb = body.get("kb")
            if not isinstance(kb, dict):
                return self._send(400, {"error": "kb (object) required"})
            try:
                version = store.save_kb(p[2], kb, int(body.get("expected_version", 0)))
            except store.Conflict as e:
                return self._send(409, {"error": "version conflict", "detail": str(e)})
            store.log_contribution(p[2], body.get("contributor") or "anonymous",
                                   "push-kb", "version -> {}".format(version))
            return self._send(200, {"version": version})
        self._send(404, {"error": "not found"})


def run(port=8800):
    from app.env import load_dotenv
    load_dotenv()
    store.init_db()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("Portal on http://0.0.0.0:{}  (store: {})".format(
        port, "Postgres" if store._IS_PG else store._SQLITE_PATH))
    httpd.serve_forever()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    run(ap.parse_args().port)
