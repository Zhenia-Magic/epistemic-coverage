"""Import the bundled cases/*.kb.json into the store as questions (one-time / idempotent-ish).

Run:  python -m app.seed         (imports any cases/*.kb.json not already present, by question)
"""
import glob
import json
import os

from app import store


def seed_from_cases(pattern="cases/*.kb.json"):
    store.init_db()
    existing = {q["question"] for q in store.list_questions(limit=1000)}
    added = []
    for path in sorted(glob.glob(pattern)):
        kb = json.load(open(path))
        question = kb.get("meta", {}).get("question") or os.path.basename(path)
        if question in existing:
            continue
        q = store.create_question(question)               # makes an empty seeded KB...
        store.save_kb(q["id"], kb, expected_version=0)     # ...then overwrite with the real KB
        existing.add(question)                             # avoid double-import within one run
        added.append(question)
    return added


if __name__ == "__main__":
    names = seed_from_cases()
    if names:
        print("Seeded {} question(s):".format(len(names)))
        for n in names:
            print("  -", n)
    else:
        print("Nothing to seed (all cases already present).")
