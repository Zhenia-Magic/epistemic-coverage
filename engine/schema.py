"""Schema helpers: construct an empty KB. See SCHEMA.md for the full shape."""
from .merge import now_iso

# Science-general evidence types, seeded into every new case so blindspots are meaningful
# from the first source. `population` is intentionally NOT seeded: it is domain-specific and
# grows per case (a black-hole case has no populations). New cases inherit this base; the
# vocabulary then accretes per topic as sources are ingested (see merge._resolve_vocab).
BASE_EVIDENCE = [
    "Observational", "Experimental (RCT)", "Meta-analysis", "Evidence-synthesis",
    "Mechanistic", "Expert advisory", "Narrative/Commentary",
]

# Funder type, a closed vocabulary. "Industry" and "Advocacy" have a stake in the answer; the
# default is "Undisclosed" (never assume independence when the text is silent). See
# merge._resolve_funding and assess.funding_skew.
BASE_FUNDING = [
    "Government/public", "Nonprofit/charity", "Academic/institutional",
    "Industry", "Advocacy", "Undisclosed",
]


def empty_kb(case_id, question):
    return {
        "meta": {"id": case_id, "question": question or "", "version": 0, "updated": now_iso()},
        "positions": [],
        "datasets": [],
        "factors": [],
        "sources": [],
        "vocab": {
            "evidence": [{"label": e, "aliases": []} for e in BASE_EVIDENCE],
            "population": [],
            "funding": [{"label": f, "aliases": []} for f in BASE_FUNDING],
        },
        "log": [],
    }
