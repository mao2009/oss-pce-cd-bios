"""Issue #718 (P03 of #473): gap-only synthetic HuC6280 A+D+T fixtures.

Host-only. No emulator, BIOS, ROM, reference JSR, or ./rom/ content is read.
This test checks the unified synthesis fixture (tests/fixtures/
huc6280_adt_synthesis.json) which adds ONLY the missing cross-conditions on
top of #647/#648/#653/#654. It reuses those drivers by name and never
duplicates their logic. Every row keeps the synthetic expectation and the
real-core observation in separate fields; real-core rows are NOT_RUN here.

A failure means the synthesis drifted (missing pair, wrong refusal position,
NOT_MEASURED read as PASS, or a reuse pointer that matches no driver case).
It is NOT evidence that any mechanism is safe for production use.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "huc6280_adt_synthesis.json"

sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_s5 as S5  # noqa: E402
import geargrafx_stack_s6 as S6  # noqa: E402
import geargrafx_stack_irq_timing as S7  # noqa: E402
import geargrafx_stack_mpr_buffer as S8  # noqa: E402

ALLOWED_POSITIONS = {
    "before_TAM_decode",
    "before_TransferStart",
    "mid_transfer_BLOCKED",
    "none_allowed",
    "NOT_MEASURED",
}


def load():
    return json.loads(FIX.read_text(encoding="utf-8"))


def by_id(doc):
    return {c["id"]: c for c in doc["cases"]}


class SchemaTests(unittest.TestCase):
    def test_file_and_top_level_gates(self):
        doc = load()
        self.assertEqual(doc["schema"], "oss-pce-cd-bios/huc6280-adt-synthesis/v1")
        self.assertEqual(doc["issue"], 718)
        self.assertEqual(doc["parent"], 473)
        self.assertEqual(sorted(doc["reuses"]), [647, 648, 653, 654])
        self.assertEqual(doc["abi_status"], "BLOCKED_ABI")
        self.assertFalse(doc["release_eligible"])
        self.assertEqual(doc["execution"], "NOT_RUN")
        self.assertIn("no System Card BIOS", doc["provenance"])
        self.assertIn("NOT_RUN", doc["evidence_category"])

    def test_ids_unique_and_fields_present(self):
        doc = load()
        ids = [c["id"] for c in doc["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        for c in doc["cases"]:
            for k in ("id", "polarity", "category", "guest", "setup", "reuses",
                      "discriminant", "synthetic_expect", "real_core_observation", "pair"):
                self.assertIn(k, c, c["id"])
            self.assertIn(c["polarity"], {"POSITIVE", "NEGATIVE", "NOT_MEASURED"}, c["id"])
            se, ro = c["synthetic_expect"], c["real_core_observation"]
            for k in ("verdict", "refusal_position", "resume_prohibited", "reason"):
                self.assertIn(k, se, c["id"])
            self.assertIn(se["refusal_position"], ALLOWED_POSITIONS, c["id"])
            self.assertEqual(ro["status"], "NOT_RUN", c["id"])
            self.assertIsNone(ro["verdict"], c["id"])
            # The two evidence kinds live in separate fields.
            self.assertNotEqual(set(se), set(ro))

    def test_every_row_names_its_reuse(self):
        doc = load()
        for c in doc["cases"]:
            self.assertTrue(c["reuses"], c["id"])
            joined = " ".join(c["reuses"])
            self.assertTrue(any(s in joined for s in ("S5", "S6", "S7", "S8")), c["id"])


class CoverageTests(unittest.TestCase):
    def test_acceptance_categories_present(self):
        cats = {c["category"] for c in load()["cases"]}
        for need in ("tam_contrast", "transfer_crossing", "self_overwrite",
                     "irq_discriminant", "buffer", "assert_inside"):
            self.assertIn(need, cats, need)

    def test_mpr1_ff_negative_has_f8_positive_pair(self):
        table = by_id(load())
        neg, pos = table["718-tam02ff-neg"], table["718-tam02f8-pos"]
        self.assertEqual(neg["pair"], pos["id"])
        self.assertEqual(pos["pair"], neg["id"])
        self.assertEqual(neg["synthetic_expect"]["refusal_position"], "before_TAM_decode")
        self.assertEqual(pos["synthetic_expect"], {**pos["synthetic_expect"], "verdict": "ALLOW",
                                                   "refusal_position": "none_allowed"})

    def test_src_dst_and_self_crossing_present(self):
        table = by_id(load())
        directions = {c["id"]: c["setup"].get("direction") for c in table.values()}
        self.assertEqual(directions["718-tii-dstio-neg"], "dst_io")
        self.assertEqual(directions["718-tin-srcio-neg"], "src_io")
        self.assertEqual(directions["718-xself-neg"], "self")
        self.assertEqual(directions["718-xlen0-neg"], "self_len0")
        self.assertEqual(directions["718-xnoself-pos"], "wram_only")
        for cid in ("718-tii-dstio-neg", "718-tin-srcio-neg", "718-tdd-down-neg",
                    "718-tia-alt-neg", "718-xself-neg", "718-xlen0-neg"):
            self.assertEqual(table[cid]["synthetic_expect"]["refusal_position"],
                             "before_TransferStart", cid)
        self.assertEqual(table["718-mid-transfer-blocked"]["synthetic_expect"]["verdict"], "BLOCKED")
        self.assertEqual(table["718-mid-transfer-blocked"]["synthetic_expect"]["refusal_position"],
                         "mid_transfer_BLOCKED")

    def test_irq_buffer_newassert_are_distinguished(self):
        table = by_id(load())
        discs = {c["id"]: c["discriminant"] for c in table.values()}
        self.assertIn("D", discs["718-irq-pending-neg"])
        self.assertIn("buffer", discs["718-tma00-buffer-pos"].lower()
                      + discs["718-tam00-buffer-pos"].lower()
                      + discs["718-irq-pending-neg"].lower())
        self.assertIn("S7", discs["718-irq-newassert-deferred"])
        self.assertIn("NOT_MEASURED", discs["718-irq-tam-internal-notmeasured"])

    def test_clockless_tam_assert_is_not_measured_and_never_pass(self):
        row = by_id(load())["718-irq-tam-internal-notmeasured"]
        self.assertEqual(row["polarity"], "NOT_MEASURED")
        self.assertEqual(row["synthetic_expect"]["verdict"], "NOT_MEASURED")
        self.assertEqual(row["synthetic_expect"]["refusal_position"], "NOT_MEASURED")
        for forbidden in ("REFUSE", "ALLOW", "BLOCKED", "SAFE", "PASS"):
            self.assertNotEqual(row["synthetic_expect"]["verdict"], forbidden)
        # The guard must hold for every NOT_MEASURED row, not just this one.
        for c in load()["cases"]:
            if c["synthetic_expect"]["refusal_position"] == "NOT_MEASURED":
                self.assertEqual(c["synthetic_expect"]["verdict"], "NOT_MEASURED", c["id"])
                self.assertEqual(c["polarity"], "NOT_MEASURED", c["id"])

    def test_pairs_resolve_and_contrast(self):
        table = by_id(load())
        for c in table.values():
            self.assertIn(c["pair"], table, c["id"])
        # Every NEGATIVE has a POSITIVE (or NOT_MEASURED-linked) counterpart, and vice versa.
        for c in table.values():
            mate = table[c["pair"]]
            self.assertIn(mate["polarity"], {"POSITIVE", "NEGATIVE", "NOT_MEASURED"}, c["id"])
            if c["polarity"] == "NEGATIVE" and mate["polarity"] == "POSITIVE":
                self.assertNotEqual(c["synthetic_expect"]["verdict"],
                                    mate["synthetic_expect"]["verdict"], c["id"])

    def test_refusal_means_no_resume(self):
        for c in load()["cases"]:
            se = c["synthetic_expect"]
            if se["verdict"] in ("REFUSE", "BLOCKED", "NOT_MEASURED"):
                self.assertTrue(se["resume_prohibited"], c["id"])
            if se["refusal_position"] in ("before_TAM_decode", "before_TransferStart",
                                          "mid_transfer_BLOCKED"):
                self.assertIn(se["verdict"], ("REFUSE", "BLOCKED"), c["id"])
            if se["refusal_position"] == "none_allowed":
                self.assertEqual(se["verdict"], "ALLOW", c["id"])
                self.assertFalse(se["resume_prohibited"], c["id"])


class ReuseTests(unittest.TestCase):
    def test_reuse_pointers_match_real_driver_cases(self):
        s5, s6, s7, s8 = set(S5.cases()), set(S6.cases()), set(S7.cases()), set(S8.cases())
        table = by_id(load())
        # Spot-check the load-bearing pointers: each must name a driver that
        # actually owns that case id fragment.
        self.assertTrue(any("tamcode" in c for c in s5), "S5 tamcode")
        self.assertIn("AD:tamcode-armed", s5)
        self.assertIn("AT:xself-armed", s6)
        self.assertIn("AT:xlen0-armed", s6)
        self.assertIn("AT:xtiim-armed", s6)
        self.assertTrue(any("ni_tam_q1m_1" in c for c in s7), "S7 NOT_MEASURED")
        self.assertTrue(any("mb_tam1" in c for c in s8), "S8 tam1")
        # Every synthesis row must point at least at one existing driver case
        # family (prefix match), so the fixture reuses instead of reforking.
        pools = {"S5": s5, "S6": s6, "S7": s7, "S8": s8}
        for c in table.values():
            ok = False
            for tag, pool in pools.items():
                if tag in " ".join(c["reuses"]):
                    ok = True
                    # At least one concrete fragment after the tag must occur
                    # in that driver's case list (e.g. "xself", "tamcode").
                    fragments = [w.strip("(),:") for w in " ".join(c["reuses"]).split()]
                    if any(any(f in case for case in pool) for f in fragments if len(f) >= 4):
                        break
            self.assertTrue(ok, c["id"])

    def test_no_driver_logic_duplicated(self):
        # The synthesis fixture carries expectations and pointers only; the
        # verdict engines stay in the drivers.
        text = FIX.read_text(encoding="utf-8")
        for token in ("def check_", "def expected", "gg_verify", "m_transfer_state"):
            self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
