"""Tests for the evidence-independence mechanism (engine/roots.py + engine/assess.py).

Mirrors the worked examples and adversarial cases in MECHANISM.md, so the spec and the code
can't drift apart silently.
"""
import unittest

from engine.assess import independence, weighted_distribution
from engine.roots import resolve, tier_of


def _kb(sources, positions=("X", "Y"), vocab_evidence=None):
    hues = ["#111", "#222", "#333"]
    return {"positions": [{"id": p, "label": p, "hue": hues[i % 3]} for i, p in enumerate(positions)],
            "datasets": [], "factors": [], "sources": sources,
            "vocab": {"evidence": vocab_evidence or []}}


def _s(sid, pos, evidence, rests):
    return {"id": sid, "position": pos, "evidence": evidence, "title": sid, "restsOn": rests,
            "funding": "Undisclosed", "population": "—", "confidence": "unstated"}


def _roots(res, sid):
    return sorted(res["source_roots"][sid])


class TierTests(unittest.TestCase):
    def test_punctuated_types_classify(self):
        kb = _kb([])
        self.assertEqual(tier_of(kb, {"evidence": "Narrative/Commentary"}), "secondary")
        self.assertEqual(tier_of(kb, {"evidence": "Experimental (RCT)"}), "primary")
        self.assertEqual(tier_of(kb, {"evidence": "Observational"}), "primary")

    def test_unknown_type_defaults_primary(self):
        self.assertEqual(tier_of(_kb([]), {"evidence": "Cliodynamic field survey"}), "primary")

    def test_meta_analysis_is_secondary(self):
        # an untagged meta-analysis is echo, not an independent primary look
        self.assertEqual(tier_of(_kb([]), {"evidence": "Meta-analysis"}), "secondary")

    def test_vocab_tier_override_wins(self):
        kb = _kb([], vocab_evidence=[{"label": "Observational", "aliases": [], "tier": "secondary"}])
        self.assertEqual(tier_of(kb, {"evidence": "Observational"}), "secondary")


class ResolutionTests(unittest.TestCase):
    def test_echo_collapses_to_one_voice(self):
        kb = _kb([_s("a", "X", "Observational", ["D"]),
                  _s("b", "X", "Narrative/Commentary", []),
                  _s("c", "X", "Evidence-synthesis", []),
                  _s("d", "X", "Expert advisory", [])])
        res = resolve(kb)
        self.assertEqual(_roots(res, "a"), ["ds:D"])
        for sid in ("b", "c", "d"):
            self.assertEqual(_roots(res, sid), ["secpool:X"])

    def test_well_tagged_review_collapses_into_the_dataset(self):
        # E2: a review that restsOn the study it summarises needs no tier rule
        kb = _kb([_s("study", "X", "Observational", ["D"]),
                  _s("review", "X", "Evidence-synthesis", ["src:study"])])
        res = resolve(kb)
        self.assertEqual(_roots(res, "review"), ["ds:D"])

    def test_chain_resolves_to_terminal_dataset(self):
        kb = _kb([_s("a", "X", "Narrative/Commentary", ["src:b"]),
                  _s("b", "X", "Narrative/Commentary", ["src:c"]),
                  _s("c", "X", "Evidence-synthesis", ["D"])])
        res = resolve(kb)
        self.assertEqual(_roots(res, "a"), ["ds:D"])

    def test_pure_circular_corroboration_is_flagged(self):
        kb = _kb([_s("a", "X", "Narrative/Commentary", ["src:b"]),
                  _s("b", "X", "Narrative/Commentary", ["src:a"])])
        res = resolve(kb)
        self.assertEqual(_roots(res, "a"), _roots(res, "b"))   # collapse to one loop root
        self.assertEqual(len(res["circular"]), 1)
        self.assertEqual(res["circular"][0]["sources"], ["a", "b"])

    def test_circular_but_grounded_is_not_flagged(self):
        kb = _kb([_s("a", "X", "Observational", ["src:b", "D"]),
                  _s("b", "X", "Narrative/Commentary", ["src:a"])])
        res = resolve(kb)
        self.assertEqual(res["circular"], [])
        self.assertEqual(_roots(res, "a"), ["ds:D"])
        self.assertEqual(_roots(res, "b"), ["ds:D"])

    def test_dataset_via_secondary_only_is_marked(self):
        kb = _kb([_s("rev", "X", "Evidence-synthesis", ["D"])])
        res = resolve(kb)
        self.assertIn("ds:D", res["secondary_only"])

    def test_dataset_with_a_primary_source_is_not_secondary_only(self):
        kb = _kb([_s("study", "X", "Observational", ["D"]),
                  _s("rev", "X", "Evidence-synthesis", ["D"])])
        res = resolve(kb)
        self.assertNotIn("ds:D", res["secondary_only"])

    def test_self_loop_is_ignored(self):
        kb = _kb([_s("a", "X", "Observational", ["src:a"])])
        res = resolve(kb)          # rests only on itself -> ungrounded primary -> own root
        self.assertEqual(_roots(res, "a"), ["prim:a"])


class MetricTests(unittest.TestCase):
    def test_review_flood_cannot_inflate_independence(self):
        # one real study + a flood of ungrounded reviews -> ~2 effective bases, not many
        srcs = [_s("study", "X", "Observational", ["D"])]
        srcs += [_s("r%d" % i, "X", "Narrative/Commentary", []) for i in range(50)]
        ind = {p["id"]: p for p in independence(_kb(srcs))}["X"]
        self.assertLessEqual(ind["nEff"], 2.01)
        self.assertEqual(ind["collapsedSecondary"], 50)

    def test_review_flood_cannot_tank_a_rivals_independence(self):
        # flooding a position that has 3 distinct primary datasets with reviews must not crash it
        srcs = [_s("p1", "X", "Observational", ["D1"]),
                _s("p2", "X", "Observational", ["D2"]),
                _s("p3", "X", "Observational", ["D3"])]
        base = {p["id"]: p for p in independence(_kb(srcs))}["X"]["nEff"]
        srcs += [_s("r%d" % i, "X", "Evidence-synthesis", []) for i in range(40)]
        flooded = {p["id"]: p for p in independence(_kb(srcs))}["X"]["nEff"]
        self.assertGreaterEqual(flooded, base)         # adding echo never lowers independence

    def test_cohort_reuse_still_concentrates(self):
        srcs = [_s("p%d" % i, "X", "Observational", ["D"]) for i in range(8)]
        ind = {p["id"]: p for p in independence(_kb(srcs))}["X"]
        self.assertAlmostEqual(ind["nEff"], 1.0, places=6)   # 8 papers, one dataset = one look

    def test_weighted_distribution_sums_to_about_100(self):
        kb = _kb([_s("a", "X", "Observational", ["D1"]),
                  _s("b", "Y", "Narrative/Commentary", [])])
        self.assertEqual(sum(d["pct"] for d in weighted_distribution(kb)), 100)


class GapTests(unittest.TestCase):
    def test_thin_position_with_no_primary_is_flagged_severe(self):
        from engine.gaps import find_gaps
        kb = _kb([_s("a", "X", "Narrative/Commentary", []),
                  _s("b", "X", "Expert advisory", []),
                  _s("c", "Y", "Observational", ["D1"]),
                  _s("d", "Y", "Observational", ["D2"])])
        gaps = find_gaps(kb)
        thin = [g for g in gaps if g["kind"] == "thin-position"]
        self.assertTrue(any(g["positionId"] == "X" and g["severity"] == 3 for g in thin))
        self.assertFalse(any(g["positionId"] == "Y" for g in thin))   # Y has 2 primary bases

    def test_secondary_only_dataset_becomes_a_gap(self):
        from engine.gaps import find_gaps
        kb = _kb([_s("rev", "X", "Evidence-synthesis", ["D"])])
        self.assertTrue(any(g["kind"] == "unsourced-dataset" and g["datasetId"] == "D"
                            for g in find_gaps(kb)))

    def test_gap_queries_are_nonempty_strings(self):
        from engine.gaps import find_gaps, gap_queries
        kb = _kb([_s("a", "X", "Narrative/Commentary", [])])
        kb["meta"] = {"question": "Does X cause Y?"}
        for q in gap_queries(kb, find_gaps(kb)):
            self.assertTrue(q["query"].strip())


if __name__ == "__main__":
    unittest.main()


class NonHumanTests(unittest.TestCase):
    def _s2(self, sid, pos, ev, rests, pop):
        d = _s(sid, pos, ev, rests); d["population"] = pop; return d

    def test_animal_only_root_is_halved(self):
        from engine.roots import resolve, root_strength
        kb = _kb([self._s2("m", "X", "Mechanistic", ["Dmouse"], "Mice")])
        res = resolve(kb)
        self.assertIn("ds:Dmouse", res["nonhuman_only"])
        self.assertEqual(root_strength("ds:Dmouse", res["secondary_only"], res["nonhuman_only"]), 0.5)

    def test_human_source_on_root_keeps_full_weight(self):
        from engine.roots import resolve, root_strength
        kb = _kb([self._s2("h", "X", "Observational", ["D"], "US adults"),
                  self._s2("m", "X", "Mechanistic", ["D"], "Mice")])
        res = resolve(kb)
        self.assertNotIn("ds:D", res["nonhuman_only"])
        self.assertEqual(root_strength("ds:D", res["secondary_only"], res["nonhuman_only"]), 1.0)

    def test_population_word_does_not_falsematch(self):
        from engine.roots import _is_nonhuman
        self.assertFalse(_is_nonhuman({"population": "moderate-risk adults"}))   # 'rat' in 'moderate'
        self.assertTrue(_is_nonhuman({"population": "Rats"}))
        self.assertTrue(_is_nonhuman({"population": "In vitro / cell"}))


class BudgetAndFundingTests(unittest.TestCase):
    def test_funding_blindspot_gap_fires_when_all_undisclosed(self):
        from engine.gaps import find_gaps
        srcs = [_s("s%d" % i, "X", "Observational", ["D%d" % i]) for i in range(5)]
        for s in srcs:
            s["funding"] = "Undisclosed"
        self.assertTrue(any(g["kind"] == "funding-blindspot" for g in find_gaps(_kb(srcs))))

    def test_funding_blindspot_silent_when_interested_funding_present(self):
        from engine.gaps import find_gaps
        srcs = [_s("s%d" % i, "X", "Observational", ["D%d" % i]) for i in range(5)]
        srcs[0]["funding"] = "Industry"
        self.assertFalse(any(g["kind"] == "funding-blindspot" for g in find_gaps(_kb(srcs))))

    def test_usage_accumulates_and_prices(self):
        from ingest import llm
        llm.reset_usage()
        llm._record_usage("claude-sonnet-4-6", {"usage": {"input_tokens": 1_000_000, "output_tokens": 0}})
        self.assertAlmostEqual(llm.usage()["usd"], 3.0, places=4)   # $3 / 1M input on sonnet
        self.assertEqual(llm.usage()["calls"], 1)


class DedupTests(unittest.TestCase):
    def test_same_paper_two_urls_is_a_duplicate(self):
        from engine.schema import empty_kb
        from engine.merge import merge_delta
        kb = empty_kb("t", "Q?")
        merge_delta(kb, {"source": {"title": "Alcohol and CVD", "year": 2020,
                                    "url": "https://doi.org/10.1161/X", "position": "NEW:Decreases",
                                    "evidence": "Observational", "relevant": True}})
        rep = merge_delta(kb, {"source": {"title": "Alcohol and CVD: the full subtitle", "year": 2020,
                                          "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/?doi=10.1161/X",
                                          "position": "Decreases", "evidence": "Observational", "relevant": True}})
        self.assertTrue(rep.get("duplicate"))
        self.assertEqual(len(kb["sources"]), 1)

    def test_dedupe_sources_removes_title_truncation_dups(self):
        from engine import curate
        kb = _kb([_s("a", "X", "Observational", ["D1"]),
                  _s("b", "X", "Observational", ["D2"])])
        kb["sources"][0].update(title="Alcohol consumption and cardiovascular disease", year=2017)
        kb["sources"][1].update(title="Alcohol consumption and cardiovascular disease: an update", year=2017)
        kb["meta"] = {"version": 1}
        rep = curate.dedupe_sources(kb)
        self.assertEqual(len(rep["removed"]), 1)
        self.assertEqual(len(kb["sources"]), 1)
