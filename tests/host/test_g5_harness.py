"""Host tests for the G5 boot-progress measurement harness (Agent C, parent #635).

Pure model tests only: no core, no cc65, no media, no builds. All public
tests remain reproducible without private media.

G5 convergence (PR #778): real return, fake RTS, stub halt, screen change,
and title arrival stay strictly separated. Manifest declaration alone never
yields REAL_SERVICE_RETURN; title string alone never yields G5_OBSERVED.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import g5_progress_model as M  # noqa: E402

STUB = 0xE245


def run(reason, pc=STUB, x=2, confirmed=True, truncated=False, cycle="cold", step=100):
    return {"cycle": cycle, "reason": reason, "pc": pc, "x": x,
            "halt_confirmed": confirmed, "apicall_truncated": truncated, "step": step}


def full_proof():
    return M.build_real_service_proof(
        arrived_at_callee=True, post_return_position_observed=True,
        sp_restored=True, mpr_consistent=True, order_matches_expected=True)


def full_title_evidence():
    return M.build_title_evidence(
        source="human-confirmed frame inspection",
        target="synthetic title screen marker",
        cold_warm=True,
        confirmation_method="human-confirmed frame hash + state note",
        fake_rts_relation="no fabrication (measured real service)")


class VariantSpecTests(unittest.TestCase):
    def test_abc_specs(self):
        a, b, c = M.variant_spec("A"), M.variant_spec("B"), M.variant_spec("C")
        self.assertEqual(a["slots_patched"], ())
        self.assertEqual(b["slots_patched"], (0x02,))
        self.assertEqual(c["slots_patched"], (0x02, 0x33))
        self.assertEqual(a["return_kind"], "NONE")
        self.assertEqual(b["return_kind"], "FAKE_RTS")
        self.assertEqual(c["return_kind"], "FAKE_RTS")

    def test_unknown_variant_refused(self):
        with self.assertRaises(ValueError):
            M.variant_spec("Z")

    def test_return_kind_d_needs_real_manifest(self):
        man = {"slot_wiring": {"02": {"kind": "real", "description": "CD_BASE test wiring"}},
               "schema": "x", "rom_name": "exp.pce", "branch": "b",
               "head": "0" * 40, "pinned_cc65": M.PINNED_CC65,
               "pinned_geargrafx": M.PINNED_GEARGRAFX,
               "release_eligible": False, "dev_callable": False}
        self.assertEqual(M.return_kind_for_variant("D", man), "REAL_SERVICE")
        with self.assertRaises(ValueError):
            M.return_kind_for_variant("D", None)
        with self.assertRaises(ValueError):
            M.return_kind_for_variant("A", man)

    def test_return_kind_d_notation_drift(self):
        # MEDIUM-1: "02" vs "0x02" vs "$02" must not KeyError; same slot.
        for key in ("02", "0x02", "$02", " 0X02 ", 0x02):
            man = {"slot_wiring": {key: {"kind": "real", "description": "wiring"}},
                   "schema": "x", "rom_name": "exp.pce", "branch": "b",
                   "head": "0" * 40, "pinned_cc65": M.PINNED_CC65,
                   "pinned_geargrafx": M.PINNED_GEARGRAFX,
                   "release_eligible": False, "dev_callable": False}
            self.assertEqual(M.return_kind_for_variant("D", man), "REAL_SERVICE")

    def test_pins(self):
        self.assertTrue(M.PINNED_GEARGRAFX.startswith("b49ae82a"))
        self.assertTrue(M.PINNED_CC65.startswith("71746c8"))


class SlotNormalizationTests(unittest.TestCase):
    def test_normalize_variants(self):
        for key, want in (("02", 0x02), ("0x02", 0x02), ("$02", 0x02),
                          (" 0X02 ", 0x02), (0x02, 0x02), (2, 0x02),
                          ("33", 0x33), ("0x33", 0x33), ("$33", 0x33)):
            self.assertEqual(M.normalize_slot_key(key), want)

    def test_normalize_bad_keys_refused(self):
        for bad in ("", "  ", "$", "0x", "zz", "0x100", -1, 0x51, None, 2.5, True):
            with self.assertRaises(ValueError, msg=f"{bad!r}"):
                M.normalize_slot_key(bad)

    def test_normalize_out_of_range_blocked(self):
        with self.assertRaises(ValueError) as cm:
            M.normalize_slot_key("0x51")
        self.assertIn("BLOCKED", str(cm.exception))


class ManifestTests(unittest.TestCase):
    def good(self):
        return {"schema": "exp/v1", "rom_name": "exp-test.pce", "branch": "agent-a/x",
                "head": "a" * 40,
                "slot_wiring": {"02": {"kind": "real", "description": "CD_BASE read path test"}},
                "pinned_cc65": M.PINNED_CC65, "pinned_geargrafx": M.PINNED_GEARGRAFX,
                "release_eligible": False, "dev_callable": False}

    def test_valid_manifest(self):
        out = M.validate_experimental_manifest(self.good())
        self.assertEqual(out["slots"], (0x02,))

    def test_fake_wiring_refused(self):
        m = self.good()
        m["slot_wiring"] = {"02": {"kind": "fake", "description": "RTS"}}
        with self.assertRaises(ValueError):
            M.validate_experimental_manifest(m)

    def test_wrong_pin_refused(self):
        m = self.good()
        m["pinned_cc65"] = "0" * 40
        with self.assertRaises(ValueError):
            M.validate_experimental_manifest(m)

    def test_release_eligible_must_be_false(self):
        m = self.good()
        m["release_eligible"] = True
        with self.assertRaises(ValueError):
            M.validate_experimental_manifest(m)

    def test_missing_head_refused(self):
        m = self.good()
        del m["head"]
        with self.assertRaises(ValueError):
            M.validate_experimental_manifest(m)

    def test_notation_drift_same_slot(self):
        # MEDIUM-1: drifted notations normalize to the same slot.
        for key in ("02", "0x02", "$02"):
            m = self.good()
            m["slot_wiring"] = {key: {"kind": "real", "description": "wiring"}}
            out = M.validate_experimental_manifest(m)
            self.assertEqual(out["slots"], (0x02,))

    def test_duplicate_slot_rejected(self):
        # MEDIUM-1: post-normalization duplicates are BLOCKED, never merged.
        m = self.good()
        m["slot_wiring"] = {"02": {"kind": "real", "description": "a"},
                            "0x02": {"kind": "real", "description": "b"}}
        with self.assertRaises(ValueError) as cm:
            M.validate_experimental_manifest(m)
        self.assertIn("BLOCKED", str(cm.exception))
        self.assertIn("duplicate", str(cm.exception).lower())

    def test_native_cd_base_slots_derived(self):
        # MAJOR-4: read existing Agent-A block (never edit A sources).
        m = {"rom_name": "experimental-dev-only-not-compatible-syscard-sized.pce",
             "rom_sha256": "a" * 64, "rom_bytes": 256 * 1024,
             "pinned_cc65": M.PINNED_CC65, "pinned_geargrafx": M.PINNED_GEARGRAFX,
             "release_eligible": False, "dev_callable": False,
             "branch": "agent-a/cd-base-proto-g5", "head": "b" * 40,
             "labels": {"api_unimplemented": 0xE245},
             "selection_contract": {"lba": 265, "media_identity": "synthetic-test"},
             "cd_base_proto": {"slot": "$02", "entry": "$E006",
                               "proto_entry": "$F000"}}
        out = M.validate_experimental_manifest(m)
        self.assertEqual(out["slots"], (0x02,))
        self.assertTrue(out["native"])

    def test_native_missing_fields_reported_not_guessed(self):
        # MAJOR-4: absent G5-required fields are reported, never generated.
        m = {"rom_name": "experimental-dev-only-not-compatible-syscard-sized.pce",
             "rom_sha256": "a" * 64, "rom_bytes": 256 * 1024,
             "pinned_cc65": M.PINNED_CC65,
             "release_eligible": False, "dev_callable": False,
             "cd_base_proto": {"slot": "02", "entry": "E006"}}
        with self.assertRaises(ValueError) as cm:
            M.validate_experimental_manifest(m)
        msg = str(cm.exception)
        self.assertIn("BLOCKED", msg)
        self.assertIn("branch", msg)
        self.assertIn("pinned_geargrafx", msg)


class RealServiceProofTests(unittest.TestCase):
    def stub_runs(self, x=3):
        return [run("api_unimplemented", x=x),
                run("api_unimplemented", x=x, cycle="warm")]

    def test_manifest_only_must_not_yield_real_service(self):
        # MAJOR-2: declaration + stub-halt alone is never REAL_SERVICE_RETURN.
        v, d = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=self.stub_runs(3),
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=None)
        self.assertNotEqual(v, "REAL_SERVICE_RETURN")
        self.assertEqual(v, "UNKNOWN")
        self.assertIn("unproven", d)

    def test_single_api_nonreturning_stub_is_not_success(self):
        # MAJOR-2: D reaching a non-returning stub on the first API is entry,
        # never a real return.
        v, d = M.classify_g5(variant_key="D", calls=[(2, "JSR")],
                              runs=[run("api_unimplemented", x=2),
                                    run("api_unimplemented", x=2, cycle="warm")],
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=full_proof())
        self.assertNotEqual(v, "REAL_SERVICE_RETURN")
        self.assertEqual(v, "API_ENTRY_CONFIRMED")

    def test_sp_mismatch_rejected(self):
        proof = full_proof()
        proof["sp_restored"] = False
        v, _ = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=self.stub_runs(3),
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=proof)
        self.assertEqual(v, "UNKNOWN")

    def test_mpr_mismatch_rejected(self):
        proof = full_proof()
        proof["mpr_consistent"] = False
        v, _ = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=self.stub_runs(3),
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=proof)
        self.assertEqual(v, "UNKNOWN")

    def test_arrival_unproven_rejected(self):
        proof = full_proof()
        proof["arrived_at_callee"] = False
        v, _ = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=self.stub_runs(3),
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=proof)
        self.assertEqual(v, "UNKNOWN")

    def test_order_mismatch_rejected(self):
        proof = full_proof()
        v, _ = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=self.stub_runs(3),
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=proof,
                              expected_slots=(0x33,))
        self.assertEqual(v, "UNKNOWN")

    def test_budget_overrun_is_not_success(self):
        # MAJOR-2: budget-exhaustion never yields REAL_SERVICE_RETURN.
        v, _ = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=[run("budget", confirmed=False),
                                    run("budget", confirmed=False, cycle="warm")],
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=full_proof())
        self.assertNotEqual(v, "REAL_SERVICE_RETURN")
        self.assertEqual(v, "NEXT_BLOCKER")

    def test_missing_trace_is_unknown(self):
        v, _ = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=[run("api_unimplemented", x=3, truncated=True),
                                    run("api_unimplemented", x=3, cycle="warm", truncated=True)],
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=full_proof())
        self.assertEqual(v, "UNKNOWN")

    def test_cold_warm_mismatch_rejected_for_d(self):
        v, _ = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=self.stub_runs(3),
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=False,
                              real_service_proof=full_proof())
        self.assertEqual(v, "UNKNOWN")

    def test_real_service_positive_with_full_proof(self):
        v, d = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=self.stub_runs(3),
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=full_proof(),
                              expected_slots=(0x02, 0x03))
        self.assertEqual(v, "REAL_SERVICE_RETURN")
        self.assertIn("real return", d)

    def test_derive_proof_sp_chain(self):
        # Derivation from measured records: SP chain + MPR + order.
        def call(slot, sp, mpr):
            return {"cycle": "cold", "slot": slot, "caller_pc": 0x3000,
                    "entry_pc": 0xE000 + slot * 3, "sp_before": sp,
                    "sp_after": (sp - 2) & 0xFF, "sp_delta": 2,
                    "caller_a": 0xAA, "caller_x": 0x11, "caller_y": 0x22,
                    "caller_p": 0x34, "mpr": mpr, "stack_check": "MATCH",
                    "return_matches_caller": True, "in_table": True}
        mpr = [0x80, 0xF8, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87]
        cold = [call(0x02, 0x80, mpr), call(0x03, 0x80, mpr)]
        warm = [call(0x02, 0x80, mpr), call(0x03, 0x80, mpr)]
        proof = M.derive_real_service_proof(cold, warm, [0x02, 0x03], (0x02, 0x03))
        self.assertTrue(all(proof[k] is True for k in M.REAL_SERVICE_PROOF_KEYS))
        bad = [call(0x02, 0x80, mpr), call(0x03, 0x7E, mpr)]
        proof2 = M.derive_real_service_proof(bad, bad, [0x02, 0x03], (0x02, 0x03))
        self.assertIs(proof2["sp_restored"], False)


class VerdictTests(unittest.TestCase):
    def test_pre_api_blocker(self):
        v, _ = M.classify_g5(variant_key="A", calls=[],
                              runs=[run("cd_halt_fail"), run("cd_halt_fail", cycle="warm")],
                              stub_addr=STUB, return_kind="NONE",
                              cold_warm_identical=True)
        self.assertEqual(v, "PRE_API_BLOCKER")

    def test_api_entry_confirmed(self):
        v, d = M.classify_g5(variant_key="A", calls=[(2, "JSR")],
                              runs=[run("api_unimplemented", x=2),
                                    run("api_unimplemented", x=2, cycle="warm")],
                              stub_addr=STUB, return_kind="NONE",
                              cold_warm_identical=True)
        self.assertEqual(v, "API_ENTRY_CONFIRMED")
        self.assertIn("$02", d)

    def test_simulated_next_api(self):
        v, d = M.classify_g5(variant_key="B", calls=[(2, "JSR"), (51, "JSR")],
                              runs=[run("api_unimplemented", x=51),
                                    run("api_unimplemented", x=51, cycle="warm")],
                              stub_addr=STUB, return_kind="FAKE_RTS",
                              cold_warm_identical=True)
        self.assertEqual(v, "SIMULATED_NEXT_API")
        self.assertIn("SIMULATED", d)

    def test_real_service_return(self):
        v, d = M.classify_g5(variant_key="D", calls=[(2, "JSR"), (3, "JSR")],
                              runs=[run("api_unimplemented", x=3),
                                    run("api_unimplemented", x=3, cycle="warm")],
                              stub_addr=STUB, return_kind="REAL_SERVICE",
                              cold_warm_identical=True,
                              real_service_proof=full_proof(),
                              expected_slots=(0x02, 0x03))
        self.assertEqual(v, "REAL_SERVICE_RETURN")

    def test_next_blocker_on_nonstub_halt(self):
        v, _ = M.classify_g5(variant_key="C", calls=[(2, "JSR"), (51, "JSR")],
                              runs=[run("cd_halt_ok", pc=0x3000, x=0),
                                    run("cd_halt_ok", pc=0x3000, x=0, cycle="warm")],
                              stub_addr=STUB, return_kind="FAKE_RTS",
                              cold_warm_identical=True)
        self.assertEqual(v, "NEXT_BLOCKER")

    def test_divergent_is_unknown(self):
        v, _ = M.classify_g5(variant_key="A", calls=[(2, "JSR")],
                              runs=[run("api_unimplemented", x=2),
                                    run("api_unimplemented", x=2, cycle="warm")],
                              stub_addr=STUB, return_kind="NONE",
                              cold_warm_identical=False)
        self.assertEqual(v, "UNKNOWN")

    def test_screen_change_alone_never_title(self):
        v, _ = M.classify_g5(variant_key="A", calls=[(2, "JSR")],
                              runs=[run("api_unimplemented", x=2),
                                    run("api_unimplemented", x=2, cycle="warm")],
                              stub_addr=STUB, return_kind="NONE",
                              cold_warm_identical=True,
                              frame_progress=True, screen_changed=True,
                              title_observed=False, title_basis="")
        self.assertEqual(v, "G5_CANDIDATE")

    def test_g5_observed_needs_basis_and_evidence(self):
        kw = dict(variant_key="C", calls=[(2, "JSR"), (51, "JSR"), (3, "JSR")],
                  runs=[run("api_unimplemented", x=3),
                        run("api_unimplemented", x=3, cycle="warm")],
                  stub_addr=STUB, return_kind="FAKE_RTS",
                  cold_warm_identical=True, frame_progress=True,
                  screen_changed=True)
        v1, _ = M.classify_g5(title_observed=True, title_basis="", **kw)
        self.assertNotEqual(v1, "G5_OBSERVED")
        # Basis alone (no structured observation) is still not G5_OBSERVED.
        v2, _ = M.classify_g5(title_observed=True,
                               title_basis="human-confirmed title frame + state note", **kw)
        self.assertNotEqual(v2, "G5_OBSERVED")
        self.assertEqual(v2, "UNKNOWN")
        # Full structured evidence confirms.
        v3, d3 = M.classify_g5(title_observed=True,
                               title_basis="human-confirmed title frame + state note",
                               title_evidence=M.build_title_evidence(
                                   source="human inspection",
                                   target="synthetic title marker",
                                   cold_warm=True,
                                   confirmation_method="human-confirmed frame + state note",
                                   fake_rts_relation="fabricated $02+$33 RTS; post-return conditional"),
                               **kw)
        self.assertEqual(v3, "G5_OBSERVED")
        self.assertIn("human-confirmed", d3)

    def test_g5_observed_refused_without_real_observation(self):
        # MEDIUM-2: observed=True with no evidence is UNKNOWN, never G5_OBSERVED.
        v, d = M.classify_g5(variant_key="A", calls=[(2, "JSR")],
                              runs=[run("api_unimplemented", x=2),
                                    run("api_unimplemented", x=2, cycle="warm")],
                              stub_addr=STUB, return_kind="NONE",
                              cold_warm_identical=True,
                              frame_progress=True, screen_changed=True,
                              title_observed=True,
                              title_basis="someone claims a title string")
        self.assertNotEqual(v, "G5_OBSERVED")
        self.assertEqual(v, "UNKNOWN")
        self.assertIn("without real observation", d)

    def test_cold_warm_mismatch_rejected_for_title(self):
        v, _ = M.classify_g5(variant_key="C", calls=[(2, "JSR"), (51, "JSR"), (3, "JSR")],
                              runs=[run("api_unimplemented", x=3),
                                    run("api_unimplemented", x=3, cycle="warm")],
                              stub_addr=STUB, return_kind="FAKE_RTS",
                              cold_warm_identical=False,
                              frame_progress=True, screen_changed=True,
                              title_observed=True,
                              title_basis="human-confirmed title frame",
                              title_evidence=full_title_evidence())
        self.assertNotEqual(v, "G5_OBSERVED")
        self.assertEqual(v, "UNKNOWN")

    def test_unverified_stub_is_unknown(self):
        v, _ = M.classify_g5(variant_key="A", calls=[(2, "JSR")],
                              runs=[run("api_unimplemented", x=2),
                                    run("api_unimplemented", x=2, cycle="warm")],
                              stub_addr=None, return_kind="NONE",
                              cold_warm_identical=True)
        self.assertEqual(v, "UNKNOWN")

    def test_budget_without_calls_is_unknown(self):
        v, _ = M.classify_g5(variant_key="A", calls=[],
                              runs=[run("budget", confirmed=False),
                                    run("budget", confirmed=False, cycle="warm")],
                              stub_addr=STUB, return_kind="NONE",
                              cold_warm_identical=True)
        self.assertEqual(v, "UNKNOWN")


class ProgressDiffTests(unittest.TestCase):
    def test_first_run(self):
        d = M.diff_progress({"api_count": 1}, None)
        self.assertEqual(d["verdict"], "FIRST_RUN")

    def test_more_same_regressed(self):
        self.assertEqual(M.diff_progress({"api_count": 2}, {"api_count": 1})["verdict"], "MORE_APIS")
        self.assertEqual(M.diff_progress({"api_count": 1}, {"api_count": 1})["verdict"], "SAME")
        self.assertEqual(M.diff_progress({"api_count": 1}, {"api_count": 2})["verdict"], "REGRESSED")


class SanitizeTests(unittest.TestCase):
    def test_clean_evidence_passes(self):
        ev = {"schema": M.SCHEMA, "variant": "A", "verdict": "PRE_API_BLOCKER",
              "api_count": 0, "pinned_geargrafx": M.PINNED_GEARGRAFX}
        self.assertEqual(M.sanitize_for_public(ev)["verdict"], "PRE_API_BLOCKER")

    def test_title_basis_stated_passes(self):
        # MAJOR-3: safe structured title fields must not trip the sanitizer.
        ev = {"schema": M.SCHEMA, "title_basis_stated": True,
              "title": {"title": "NOT_OBSERVED", "confirmed": False, "basis": ""},
              "media": "synthetic", "verdict": "API_ENTRY_CONFIRMED"}
        clean = M.sanitize_for_public(ev)
        self.assertIs(clean["title_basis_stated"], True)

    def test_private_keys_refused(self):
        for bad in ("cue_path", "media_hash", "rom_dir", "title_name", "trace_dump", "save_path"):
            with self.assertRaises(ValueError, msg=bad):
                M.sanitize_for_public({"schema": M.SCHEMA, bad: "x"})

    def test_nested_private_info_rejected(self):
        # MAJOR-3: recursion into ALL fields (nested private refused).
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA,
                                   "outer": {"cue_path": "./rom/game.cue"}})
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA,
                                   "results": [{"media_hash": "a" * 64}]})
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA,
                                   "title": {"title_name": "private game"}})

    def test_media_refs_refused(self):
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA, "note": "see ./rom/game.cue"})
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA, "note": "wrote /tmp/opencode/x.json"})

    def test_commercial_public_evidence_saves_cleanly(self):
        # MAJOR-3: post-commercial saving through the sanitizer.
        ev = {"schema": M.SCHEMA, "agent": "C", "head": "a" * 40,
              "mode": "debug", "title_basis_stated": False,
              "pinned_geargrafx": M.PINNED_GEARGRAFX,
              "pinned_cc65": M.PINNED_CC65,
              "variants_synthetic": {"A": {"verdict": "API_ENTRY_CONFIRMED",
                                           "api_count": 1, "last_slot": "$02"}},
              "commercial": {"status": "OBSERVED_STRUCTURAL_ONLY",
                             "results": {"A": {"verdict": "API_ENTRY_CONFIRMED",
                                               "api_count": 1}}}}
        clean = M.sanitize_for_public(ev)
        self.assertEqual(clean["commercial"]["status"], "OBSERVED_STRUCTURAL_ONLY")


class SafetyTests(unittest.TestCase):
    def test_safety_block(self):
        s = M.safety_block()
        self.assertEqual(s["reference_bios_jsr"], 0)
        self.assertEqual(s["abi_verified"], 0)
        self.assertIs(s["dev_callable"], False)
        self.assertIs(s["release_eligible"], False)
        self.assertIs(s["compatible_system_card_bios"], False)

    def test_skeleton_pins(self):
        e = M.evidence_skeleton(head="b" * 40, mode="debug")
        self.assertEqual(e["pinned_geargrafx_short"], "b49ae82a")
        self.assertEqual(e["pinned_cc65_short"], "71746c8")
        self.assertEqual(e["schema"], M.SCHEMA)


if __name__ == "__main__":
    unittest.main()
