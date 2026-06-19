"""Tiny stdlib HTTP client the CLI uses to sync with a portal (`cli.py push` / `pull`).

Portal URL comes from --portal or the EPISTEMIC_PORTAL env var. No dependencies.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


def portal_url(explicit=None):
    url = explicit or os.environ.get("EPISTEMIC_PORTAL")
    if not url:
        raise SystemExit("No portal URL. Pass --portal <url> or set EPISTEMIC_PORTAL.")
    return url.rstrip("/")


def _request(method, url, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": e.reason}
    except urllib.error.URLError as e:
        raise SystemExit("Could not reach portal {}: {}".format(url, e.reason))


def list_questions(base, search=None):
    q = "?search=" + urllib.parse.quote(search) if search else ""
    _, body = _request("GET", base + "/api/questions" + q)
    return body.get("questions", [])


def get_question(base, qid):
    code, body = _request("GET", base + "/api/questions/" + qid)
    if code == 404:
        raise SystemExit("No question {} on the portal.".format(qid))
    return body


def create_question(base, question, contributor="anonymous"):
    code, body = _request("POST", base + "/api/questions",
                          {"question": question, "contributor": contributor})
    if code not in (200, 201):
        raise SystemExit("Create failed: {}".format(body.get("error") or code))
    return body


def put_kb(base, qid, kb, expected_version, contributor="anonymous"):
    code, body = _request("PUT", base + "/api/questions/" + qid,
                          {"kb": kb, "expected_version": expected_version, "contributor": contributor})
    if code == 409:
        raise SystemExit("Version conflict — someone pushed first. Run `pull` to get the latest, "
                         "re-apply your sources, then push again.")
    if code != 200:
        raise SystemExit("Push failed: {}".format(body.get("error") or code))
    return body
