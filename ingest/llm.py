"""Model-agnostic LLM access via the stdlib (no SDK dependency).

Dispatches by environment: ANTHROPIC_API_KEY -> Claude, else OPENAI_API_KEY -> OpenAI.
With no key set, callers should use --dry-run (print the prompt, paste into any tool).
`discover()` requests web-grounded search where the backend supports it (Anthropic's
server-side web_search tool) — that is the "deep research finds its own sources" path.

Override the model with EPISTEMIC_MODEL. Defaults to the latest Claude / a current GPT.
"""
import json
import os
import time
import urllib.error
import urllib.request

MODEL = os.environ.get("EPISTEMIC_MODEL")
RETRY_CODES = {429, 500, 502, 503, 529}  # transient — Anthropic 529 = Overloaded
# Sonnet by default: faster/cheaper and far less prone to 529 "Overloaded" than Opus.
# Override per-run with EPISTEMIC_MODEL=claude-opus-4-8 (or any model id).
_DEFAULT_ANTHROPIC = "claude-sonnet-4-6"
_DEFAULT_OPENAI = "gpt-4o"

# optional hook the server sets so retry/backoff notices show up in the progress log
LOG = None


def _say(msg):
    if LOG:
        try:
            LOG(msg)
        except Exception:
            pass
    print(msg, flush=True)


def active_model():
    """Human-readable description of which model the next call will use."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "Anthropic / " + (MODEL or _DEFAULT_ANTHROPIC)
    if os.environ.get("OPENAI_API_KEY"):
        return "OpenAI / " + (MODEL or _DEFAULT_OPENAI)
    return "manual (no API key)"


def _post(url, headers, body, tries=4):
    """POST with retry+backoff on transient errors (429/5xx/529), so a single 'Overloaded'
    doesn't waste the whole run. Surfaces the API's own error message on permanent failures."""
    data = json.dumps(body).encode("utf-8")
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in RETRY_CODES and attempt < tries - 1:
                wait = 2 ** attempt * 2  # 2s, 4s, 8s
                _say("  LLM API {} — retrying in {}s ({}/{})".format(e.code, wait, attempt + 1, tries - 1))
                time.sleep(wait)
                continue
            raw = ""
            try:
                raw = e.read().decode("utf-8", "ignore")
                msg = json.loads(raw).get("error", {}).get("message", "")
            except Exception:
                msg = ""
            raise SystemExit("LLM API error {}: {}".format(e.code, msg or raw[:500] or e.reason))
        except urllib.error.URLError as e:
            if attempt < tries - 1:
                _say("  network error ({}) — retrying…".format(e.reason))
                time.sleep(2 ** attempt * 2)
                continue
            raise SystemExit("network error reaching the LLM API: {}".format(e.reason))


def _anthropic(prompt, system, web, deep=False):
    model = MODEL or _DEFAULT_ANTHROPIC
    # deep mode: allow far more searches and a longer answer so the model can cover every angle
    body = {"model": model, "max_tokens": 16000 if deep else 8192,
            "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    if web:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search",
                          "max_uses": 18 if deep else 6}]
    headers = {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
               "anthropic-version": "2023-06-01", "content-type": "application/json"}
    resp = _post("https://api.anthropic.com/v1/messages", headers, body)
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


def _openai(prompt, system, web):
    model = MODEL or _DEFAULT_OPENAI
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
               "content-type": "application/json"}
    resp = _post("https://api.openai.com/v1/chat/completions", headers,
                 {"model": model, "messages": msgs})
    return resp["choices"][0]["message"]["content"]


def complete(prompt, system=None, web=False, deep=False):
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _anthropic(prompt, system, web, deep)
    if os.environ.get("OPENAI_API_KEY"):
        return _openai(prompt, system, web)
    raise SystemExit(
        "No ANTHROPIC_API_KEY or OPENAI_API_KEY set.\n"
        "Use --dry-run to print the prompt, paste it into any LLM / deep-research tool,\n"
        "then save the JSON it returns and run:  python cli.py add <kb.json> <delta.json>")


def discover(prompt, deep=False):
    """Find real sources. Try web search first; if the backend rejects it (e.g. web search not
    enabled for this key/org), fall back to the model's own knowledge. The grounded fetch step
    then verifies every URL and skips any that don't resolve, so a bad link can't sneak in.
    deep=True runs a far more thorough, multi-search web pass (see _anthropic)."""
    sysmsg = ("You find real, citable sources for a research dispute. "
              "Prefer primary sources and use web search when available.")
    if deep:
        sysmsg += (" Work like a deep-research agent: run many separate searches, dig past the "
                   "first page, and be exhaustive across every position before answering.")
    try:
        return complete(prompt, system=sysmsg, web=True, deep=deep)
    except SystemExit as web_err:
        try:
            return complete(prompt, system=sysmsg + " Web search is NOT available — list only "
                            "sources you are highly confident exist, with their correct URLs.",
                            web=False)
        except SystemExit:
            raise web_err  # both failed: surface the original (web-search) error
