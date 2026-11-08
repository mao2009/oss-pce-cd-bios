"""Host discrimination tests for the CD_BASE LBA coordinate candidates (Issue #186).

Synthetic only: these tests prove which probe tuples separate the candidate block-number
coordinate systems on two original synthetic discs, that wrong-base implementations and
mutated layouts/records are detected, and that the reference-JSR gate refuses slot $02
fail-closed. They do NOT run, emulate or describe the System Card CD_BASE / CD_READ callee,
establish no BIOS ABI, and never emit VERIFIED. Genuine-callee observation is BLOCKED by the
#173 safety boundary; see docs/research/186-cd-base-lba-discrimination.md.

Reuse: the four ledger §4 candidate lambdas and the SyntheticCD fixture model come from
tests/host/test_cd_base_abi_candidates.py (#138) and run unchanged through this module's engine.
D1 is that in-memory fixture (index00=0 added); build_d2 below is a NEW two-track CUE/BIN builder
that reuses make_cd_image.raw_sector and tools/cue_analysis.py (the #138 one-track builder in
tools/cd_base_probe.py is not duplicated).
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "host"))

import cd_base_lba_candidates as C  # noqa: E402
import cue_analysis  # noqa: E402
import reference_jsr_gate as G  # noqa: E402
from cd_base_model import LEAD_IN_FRAMES  # noqa: E402
from make_cd_image import raw_sector, sector_payload  # noqa: E402
from test_cd_base_abi_candidates import CANDIDATE_MODELS, PROBES, SyntheticCD  # noqa: E402

REJECT = None  # this module's unreadable-READ marker (the #138 test spells it "REJECT")
D1 = SyntheticCD(150)
D1.index00 = 0  # virtual PREGAP 00:02:00: the data track's INDEX 00 / PREGAP start is disc LBA 0


class TwoTrackDisc:
    """D2 (2ISO-shaped, original): track 1 = 150 MODE1 user sectors at LBA 0..149; track 2 =
    data, virtual PREGAP 150 (LBA 150..299, not stored), INDEX 01 at LBA 300, 16 sectors.
    A first track's INDEX 00 is always disc LBA 0 (tools/cue_analysis.py starts[0]==0 rule), so
    the INDEX 00 / PREGAP-inclusive candidate is only separable from ABS_LBA on a later data
    track -- the layout the HuCC 2ISO caller (ledger S1/S3) and a second data track imply."""

    index00, index01 = 150, 300

    @staticmethod
    def read(lba):
        return lba if 0 <= lba < 150 or 300 <= lba < 316 else REJECT


DISCS = [("D1", D1), ("D2", TwoTrackDisc())]
UNIVERSE = [(b, base) for b in (5, 145, 150, 155, 300, 305, 315) for base in (0, 150, 300)]


def registry(disc):
    """The five registry entries plus CALLER_FIXUP instances for the evidence-relevant deltas
    (0, -150, and the data track's INDEX 01 / INDEX 00 constants)."""
    models = dict(C.MODELS)
    models["CALLER_FIXUP_0"] = C.caller_fixup(0)
    models["CALLER_FIXUP_-LEAD_IN"] = C.caller_fixup(-LEAD_IN_FRAMES)
    models["CALLER_FIXUP_+INDEX01"] = C.caller_fixup(disc.index01)
    models["CALLER_FIXUP_+INDEX00"] = C.caller_fixup(disc.index00)
    return models


def build_d2(directory: Path) -> dict:
    """Write the D2 CUE/BIN (150 + 16 self-naming MODE1 sectors) and return cue_analysis' report."""
    sectors = [raw_sector(l) for l in range(150)] + [raw_sector(300 + j) for j in range(16)]
    (directory / "cd-base-2track-index01-300.bin").write_bytes(b"".join(sectors))
    cue = directory / "cd-base-2track-index01-300.cue"
    cue.write_text('FILE "cd-base-2track-index01-300.bin" BINARY\n'
                   '  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                   '  TRACK 02 MODE1/2352\n    PREGAP 00:02:00\n    INDEX 01 00:02:00\n')
    return cue_analysis.analyze(cue)


def _class_groups(disc_label):
    """Equivalence classes expected on one disc (models no READ-LBA probe can separate)."""
    if disc_label == "D1":  # INDEX 00 is LBA 0: INDEX00_START and CALLER_FIXUP(0) join ABS_LBA
        return [{"ABS_LBA", "CALLER_FIXUP_0", "CALLER_FIXUP_+INDEX00", "INDEX00_START_PLUS_BLOCK"},
                {"CALLER_FIXUP_+INDEX01", "TRACK_START_PLUS_BLOCK"},
                {"CALLER_FIXUP_-LEAD_IN", "MSF_FRAMES_FROM_00_00_00"}]
    return [{"ABS_LBA", "CALLER_FIXUP_0"},
            {"CALLER_FIXUP_+INDEX00", "INDEX00_START_PLUS_BLOCK"},
            {"CALLER_FIXUP_+INDEX01", "TRACK_START_PLUS_BLOCK"},
            {"CALLER_FIXUP_-LEAD_IN", "MSF_FRAMES_FROM_00_00_00"}]


def _class_pairs(disc_label):
    """Every unordered within-class pair, as frozensets (what separation() reports as inseparable)."""
    pairs = set()
    for group in _class_groups(disc_label):
        for i, a in enumerate(sorted(group)):
            for b in sorted(group)[i + 1:]:
                pairs.add(frozenset((a, b)))
    return pairs


class CandidateModelTest(unittest.TestCase):
    def test_ledger_four_models_reproduce_the_documented_table(self):
        # The #138 lambdas run through this module's engine and reproduce ledger §4 exactly
        # (REJECT -> None); the canonical registry agrees with them signature-for-signature.
        expected = {
            "ABS_LBA": (REJECT, REJECT, 155, REJECT),
            "BASE_PLUS_BLOCK": (REJECT, 155, 155, REJECT),
            "TRACK_START_PLUS_BLOCK": (155, 155, REJECT, 155),
            "MSF_FRAMES_FROM_00_00_00": (REJECT, REJECT, REJECT, REJECT),
        }
        for name, fn in CANDIDATE_MODELS.items():
            self.assertEqual(C.signature(fn, D1, PROBES), expected[name], name)
            self.assertEqual(C.signature(fn, D1, PROBES), C.signature(C.MODELS[name], D1, PROBES), name)
        # Overflow edge: the MSF candidate rejects (never wraps) past the 24-bit space, the #138
        # lambda agrees, and the registry matches the lambdas signature-for-signature on D2 too.
        overflow = (0x1000000 + LEAD_IN_FRAMES, 0)
        self.assertIsNone(C.evaluate(C.MODELS["MSF_FRAMES_FROM_00_00_00"], D1, *overflow))
        with self.assertRaises(ValueError):
            CANDIDATE_MODELS["MSF_FRAMES_FROM_00_00_00"](*overflow, D1)
        d2 = TwoTrackDisc()
        for name, fn in CANDIDATE_MODELS.items():
            self.assertEqual(C.signature(fn, d2, UNIVERSE), C.signature(C.MODELS[name], d2, UNIVERSE), name)

    def test_non_int_inputs_and_observations_are_rejected(self):
        # bool/float/str probes and observation payloads fail closed (repo judge convention).
        models = registry(D1)
        for bad in (True, 5.0, "5"):
            with self.assertRaises(ValueError):
                C.evaluate(models["ABS_LBA"], D1, bad, 0)
            with self.assertRaises(ValueError):
                C.evaluate(models["ABS_LBA"], D1, 5, bad)
        for bad_obs in ([(5, 0, True)], [(True, 0, None)], [(5, 0, 5.0)], [(5, "0", None)]):
            with self.assertRaises(ValueError):
                C.consistent(models, D1, bad_obs)

    def test_d2_two_track_cue_is_valid_and_self_naming(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_d2(Path(tmp))
            self.assertEqual(report.get("findings"), [])
            t1, t2 = report["tracks"]
            self.assertEqual((t1["start_lba"], t1["length_frames"]), (0, 150))
            self.assertEqual((t2["pregap_start_lba"], t2["start_lba"], t2["pregap_frames"], t2["length_frames"]),
                             (150, 300, 150, 16))
            cue = Path(tmp) / "cd-base-2track-index01-300.cue"
            self.assertEqual(cue_analysis.user_sector(cue, report, 305), sector_payload(305))
            self.assertEqual(cue_analysis.user_sector(cue, report, 5), sector_payload(5))  # track 1
            for bad in (150, 299, 316, 450):  # virtual PREGAP, track gap, past end, non-existent LBA
                with self.assertRaises(cue_analysis.CueError):
                    cue_analysis.user_sector(cue, report, bad)

    def test_distinct_offset_families_separate_pairwise(self):
        # All ten pairs of the five registry entries have a separating probe on D1 or D2,
        # including BASE_PLUS_BLOCK (separated by varying the base input, not the block).
        for i, a in enumerate(C.CANDIDATE_NAMES):
            for b in C.CANDIDATE_NAMES[i + 1:]:
                witness = C.detection_witness(C.MODELS, DISCS, UNIVERSE, a, b)
                self.assertIsNotNone(witness, f"{a} vs {b}")

    def test_equivalence_classes_are_reported_inseparable(self):
        for disc_label, disc in DISCS:
            models = registry(disc)
            for a, b in (("ABS_LBA", "CALLER_FIXUP_0"),
                         ("TRACK_START_PLUS_BLOCK", "CALLER_FIXUP_+INDEX01"),
                         ("MSF_FRAMES_FROM_00_00_00", "CALLER_FIXUP_-LEAD_IN"),
                         ("INDEX00_START_PLUS_BLOCK", "CALLER_FIXUP_+INDEX00")):
                self.assertIsNone(C.detection_witness(models, [(disc_label, disc)], UNIVERSE, a, b),
                                  f"{a} vs {b} on {disc_label}")
        # ABS_LBA vs INDEX00_START: inseparable on D1 (INDEX 00 is LBA 0), separated on D2.
        self.assertIsNone(C.detection_witness(registry(D1), [("D1", D1)], UNIVERSE,
                                              "ABS_LBA", "INDEX00_START_PLUS_BLOCK"))
        self.assertIsNotNone(C.detection_witness(registry(TwoTrackDisc()), [("D2", TwoTrackDisc())],
                                                 UNIVERSE, "ABS_LBA", "INDEX00_START_PLUS_BLOCK"))

    def test_minimal_probe_set_separates_every_separable_pair(self):
        for disc_label, disc in DISCS:
            models = registry(disc)
            chosen, residual = C.minimal_probe_set(models, [(disc_label, disc)], UNIVERSE)
            self.assertTrue(set(chosen) <= set(UNIVERSE))
            self.assertGreaterEqual(len(chosen), 2)
            self.assertLessEqual(len(chosen), 5, f"{disc_label}: {chosen}")
            # The chosen subset's residual is exactly the equivalence classes, and the full
            # universe agrees on which pairs are inseparable.
            self.assertEqual({frozenset(p) for p in residual}, _class_pairs(disc_label))
            full = C.separation(models, [(disc_label, disc)], UNIVERSE)
            self.assertEqual({frozenset(p) for p in full["inseparable"]}, _class_pairs(disc_label))
            # Every chosen probe is load-bearing: removing one re-merges a separable pair.
            for dropped in chosen:
                subset = [p for p in chosen if p != dropped]
                report = C.separation(models, [(disc_label, disc)], subset)
                self.assertGreater(len(report["inseparable"]), len(residual), f"{disc_label} drop {dropped}")

    def test_consistent_returns_the_equivalence_class_never_a_verdict(self):
        observations = [(5, 0, 155), (5, 150, 155), (6, 0, 156)]  # a TRACK_START callee on D1
        out = C.consistent(registry(D1), D1, observations)
        self.assertEqual(out, ["CALLER_FIXUP_+INDEX01", "TRACK_START_PLUS_BLOCK"])
        self.assertNotIn("BASE_PLUS_BLOCK", out)
        self.assertNotIn("VERIFIED", json.dumps(out))

    def test_wrong_base_implementations_have_detection_witnesses(self):
        cases = [("TRACK_START_PLUS_BLOCK", "INDEX00_START_PLUS_BLOCK"),  # base off by the PREGAP
                 ("ABS_LBA", "MSF_FRAMES_FROM_00_00_00"),                  # lead-in sign error
                 ("TRACK_START_PLUS_BLOCK", "ABS_LBA"),                    # absolute assumed
                 ("BASE_PLUS_BLOCK", "ABS_LBA")]                           # base input ignored
        for true_name, wrong_name in cases:
            witness = C.detection_witness(C.MODELS, DISCS, UNIVERSE, true_name, wrong_name)
            self.assertIsNotNone(witness, f"{true_name} vs {wrong_name}")

    def test_a_wrapping_implementation_is_detected(self):
        # Candidates reject past the 24-bit space; an implementation that wraps would read a
        # low LBA. On D2 the wrapped READ is track-1 user data, the candidate rejects: detected.
        def wrapping(block, base, disc):
            return (base + block) & 0xFFFFFF
        models = {C.BASE_PLUS_BLOCK: C.MODELS[C.BASE_PLUS_BLOCK], "WRAPPING_BASE": wrapping}
        witness = C.detection_witness(models, [("D2", TwoTrackDisc())],
                                      UNIVERSE + [(0xFFFFFF, 1)], C.BASE_PLUS_BLOCK, "WRAPPING_BASE")
        self.assertIsNotNone(witness)
        self.assertEqual(C.evaluate(wrapping, TwoTrackDisc(), 0xFFFFFF, 1), 0)  # wraps to LBA 0
        self.assertIsNone(C.evaluate(C.MODELS[C.BASE_PLUS_BLOCK], TwoTrackDisc(), 0xFFFFFF, 1))

    def test_index01_zero_control_cannot_separate_absolute_from_track_start(self):
        cd = SyntheticCD(0)
        cd.index00 = 0
        for name in ("TRACK_START_PLUS_BLOCK", "INDEX00_START_PLUS_BLOCK"):
            self.assertEqual(C.signature(C.MODELS["ABS_LBA"], cd, UNIVERSE),
                             C.signature(C.MODELS[name], cd, UNIVERSE), name)


class MutationTest(unittest.TestCase):
    def test_mutated_layout_merges_the_index00_class(self):
        # D2 with the virtual PREGAP removed (INDEX 00 == INDEX 01 == LBA 300): INDEX00_START
        # and TRACK_START become inseparable, so the fixture's nonzero PREGAP is load-bearing.
        mutated = TwoTrackDisc()
        mutated.index00 = 300
        self.assertIsNone(C.detection_witness(registry(mutated), [("D2m", mutated)], UNIVERSE,
                                              "INDEX00_START_PLUS_BLOCK", "TRACK_START_PLUS_BLOCK"))
        self.assertIsNotNone(C.detection_witness(registry(mutated), [("D2m", mutated)], UNIVERSE,
                                                 "ABS_LBA", "INDEX00_START_PLUS_BLOCK"))

    def test_mutated_observation_is_not_consistent_with_the_true_model(self):
        models = registry(D1)
        forged = [(5, 0, 155), (5, 150, 156)]  # second payload off by one
        self.assertNotIn("TRACK_START_PLUS_BLOCK", C.consistent(models, D1, forged))
        self.assertEqual(C.consistent(models, D1, [(5, 0, 155), (5, 150, 155)]),
                         ["CALLER_FIXUP_+INDEX01", "TRACK_START_PLUS_BLOCK"])

    def test_tampered_gate_ledger_fails_closed(self):
        ledger, contracts = G.load()
        bad = copy.deepcopy(ledger)
        bad["slots"]["2"] = dict(bad["slots"]["1"])  # slot $01's entry under slot $02's key
        with self.assertRaises(ValueError):
            G.validate_ledger(bad, contracts)
        bad2 = copy.deepcopy(ledger)
        bad2["schema"] = "tampered"
        with self.assertRaises(ValueError):
            G.validate_ledger(bad2, contracts)


class ReferenceGateTest(unittest.TestCase):
    def test_slot_02_cd_base_is_refused_fail_closed(self):
        ledger, contracts = G.load()
        record = G.resolve(2, ledger, contracts)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIn("NO_REVIEWED_GATE_RECORD", record["reasons"])
        self.assertFalse(G.is_execution_permitted(record["execution"]))
        with self.assertRaises(G.GateRefused):
            G.require_allowed("slot-02")

    def test_forged_complete_row_and_approval_never_allow(self):
        ledger, contracts = G.load()
        forged = copy.deepcopy(ledger)
        forged["slots"]["2"] = {"candidate_entry": "0xE006", "execution": "BLOCKED",
                                "release_eligible": False,
                                "hazard_controls": {h: "CONTROLLED" for h in G.HAZARDS}}
        G.validate_ledger(forged, contracts)
        approval = {"reviewer": "self", "pr": 186, "abi_status": "REVIEWED",
                    "hazard_controls": {h: "CONTROLLED" for h in G.HAZARDS},
                    "callee_run_evidence": ["synthetic-record"]}
        record = G.resolve(2, forged, contracts, approval)
        self.assertNotEqual(record["execution"], "ALLOWED")
        self.assertFalse(record["execution_permitted"])
        self.assertEqual((record["runner"], record["jsr_count"], record["release_eligible"]),
                         ("NONE", 0, False))

    def test_every_slot_is_blocked_without_approval(self):
        ledger, contracts = G.load()
        for slot in range(len(contracts["apis"])):
            self.assertEqual(G.resolve(slot, ledger, contracts)["execution"], "BLOCKED", slot)


if __name__ == "__main__":
    unittest.main()
