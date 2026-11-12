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

    def test_native_slot_entry_malformed_entry_blocked(self):
        # Review 5872703030 fail-open: valid slot + unparsable entry must be
        # BLOCKED, never a silent pass on the slot alone.
        with self.assertRaises(ValueError) as cm:
            M.derive_native_slots(
                {"cd_base_proto": {"slot": "$02", "entry": "garbage"}})
        self.assertIn("BLOCKED", str(cm.exception))

    def test_native_slot_entry_mismatch_blocked(self):
        # Slot $02 wired to the $33 table entry ($E099) is a mismatch.
        with self.assertRaises(ValueError) as cm:
            M.derive_native_slots(
                {"cd_base_proto": {"slot": "$02", "entry": "$E099"}})
        self.assertIn("BLOCKED", str(cm.exception))
        self.assertIn("mismatch", str(cm.exception).lower())

    def test_native_slot_entry_wrong_types_blocked(self):
        # Non-str/int entry types (list/dict/float/bool) never parse.
        for bad in (["$E006"], {"addr": "$E006"}, 3.5, True, b"$E006"):
            with self.assertRaises(ValueError, msg=f"entry={bad!r}") as cm:
                M.derive_native_slots(
                    {"cd_base_proto": {"slot": "$02", "entry": bad}})
            self.assertIn("BLOCKED", str(cm.exception))

    def test_native_slot_entry_out_of_range_blocked(self):
        # Parsable but outside the BIOS window / not slot-aligned.
        for bad in ("$1234", "$FFFF", "$E007", 0x1234, "$E0F1"):
            with self.assertRaises(ValueError, msg=f"entry={bad!r}") as cm:
                M.derive_native_slots(
                    {"cd_base_proto": {"slot": "$02", "entry": bad}})
            self.assertIn("BLOCKED", str(cm.exception))

    def test_native_bad_slot_boundaries_blocked(self):
        # Slots outside $00..$50 are refused even with a matching entry.
        for bad_slot in ("$51", "$FF", "0x51", 0x51, -1):
            with self.assertRaises(ValueError, msg=f"slot={bad_slot!r}") as cm:
                M.derive_native_slots(
                    {"cd_base_proto": {"slot": bad_slot, "entry": "$E006"}})
            self.assertIn("BLOCKED", str(cm.exception))
        # Entry past the last slot boundary ($E000+3*$50=$E0F0) is refused.
        with self.assertRaises(ValueError) as cm:
            M.derive_native_slots(
                {"cd_base_proto": {"slot": "$02", "entry": "$E0F3"}})
        self.assertIn("BLOCKED", str(cm.exception))

    def test_native_slot_entry_consistent_shapes_pass(self):
        # Normal shape keeps passing across notations (slot<->entry agree).
        for slot_key, entry in (("$02", "$E006"), ("02", "E006"),
                                ("0x02", "0xE006"), (2, 0xE006)):
            slots = M.derive_native_slots(
                {"cd_base_proto": {"slot": slot_key, "entry": entry}})
            self.assertEqual(slots, (0x02,))


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

    def test_media_filename_variants_refused(self):
        # PR #778 sanitize gap: bare/case-variant media filenames (no
        # trailing quote) and .chd must refuse (case-insensitive).
        for name in ("PrivateGame.bin", "PRIVATEGAME.BIN", "PrivateGame.chd",
                     "PrivateGame.cue", "PrivateGame.iso"):
            with self.assertRaises(ValueError, msg=name):
                M.sanitize_for_public({"schema": M.SCHEMA, "detail": name})
            with self.assertRaises(ValueError, msg="detail:" + name):
                M.sanitize_for_public({"schema": M.SCHEMA,
                                       "detail": f"secret {name} inside detail"})

    def test_any_absolute_path_refused(self):
        # PR #778 sanitize gap: rejection is not limited to /tmp/, /home/,
        # /Users/; any POSIX absolute path, Windows drive-absolute, or UNC.
        for p in ("/mnt/private/PrivateGame.bin", "/media/storage/game.cue",
                  "/private/x", r"C:\Games\x.bin", "c:/games/x.bin",
                  r"\\server\share\game.cue"):
            with self.assertRaises(ValueError, msg=p):
                M.sanitize_for_public({"schema": M.SCHEMA, "detail": p})
        # Smuggled inside nested evidence fields (recursion).
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA,
                                   "reason": {"reason": "saw /mnt/private/x.bin nested"}})
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA,
                                   "results": [{"detail": "see PrivateGame.chd here"}]})

    def test_sanitize_no_overblocking(self):
        # False-positive guards: plain words, mid-word slashes, and normal
        # structured evidence (PC/slot/status) must still pass.
        for note in ("this is binary data", "cold/warm both observed", "and/or note"):
            clean = M.sanitize_for_public({"schema": M.SCHEMA, "note": note})
            self.assertEqual(clean["note"], note)
        ev = {"schema": M.SCHEMA, "variant": "A", "verdict": "PRE_API_BLOCKER",
              "api_count": 0, "pc": 0xE245, "slot": "$02",
              "status": "OBSERVED_STRUCTURAL_ONLY"}
        self.assertEqual(M.sanitize_for_public(ev)["status"], "OBSERVED_STRUCTURAL_ONLY")

    def test_rejection_does_not_releak_value(self):
        # Fail-closed + no re-leak: the secret path must not appear in the
        # rejection message itself.
        secret = "/mnt/private/PrivateGame.bin"
        try:
            M.sanitize_for_public({"schema": M.SCHEMA, "detail": secret})
            self.fail("expected ValueError")
        except ValueError as e:
            self.assertNotIn("PrivateGame", str(e))
            self.assertNotIn(secret, str(e))

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


class RealRomSlotTargetTests(unittest.TestCase):
    def test_parse_slot_table_target_jmp(self):
        data = bytearray(M.ROM_BYTES)
        # slot 2 entry at offset 6: JMP $F000
        data[6:9] = bytes((0x4C, 0x00, 0xF0))
        target = M.parse_slot_table_target(bytes(data), 0x02)
        self.assertEqual(target, 0xF000)

    def test_parse_slot_table_target_rejects_non_jmp(self):
        data = bytearray(M.ROM_BYTES)
        data[6] = 0x60  # RTS
        with self.assertRaises(ValueError):
            M.parse_slot_table_target(bytes(data), 0x02)

    def test_verify_d_rom_bytes_requires_sha(self):
        data = bytes(M.ROM_BYTES)
        man = {"rom_name": "exp.pce", "rom_bytes": M.ROM_BYTES}
        with self.assertRaises(ValueError):
            M.verify_d_rom_bytes(data, man)

    def test_verify_d_rom_bytes_hash_mismatch(self):
        data = bytes(M.ROM_BYTES)
        man = {"rom_name": "exp.pce", "rom_bytes": M.ROM_BYTES,
               "rom_sha256": "0" * 64}
        with self.assertRaises(ValueError):
            M.verify_d_rom_bytes(data, man)

    def test_verify_slot_targets_rejects_fake_rts(self):
        # Fix1 two-stage: table -> api_slot_02 stub whose body is a bare RTS.
        data = bytearray(M.ROM_BYTES)
        data[6:9] = bytes((0x4C, 0x00, 0xE2))  # $E006 -> api_slot_02 $E200
        data[0xE200 - 0xE000] = 0x60  # stub is RTS (fake)
        man = {"rom_name": "exp.pce", "rom_bytes": M.ROM_BYTES,
               "rom_sha256": __import__("hashlib").sha256(bytes(data)).hexdigest(),
               "cd_base_proto": {"slot": "$02", "entry": "$E006", "proto_entry": "$F000"},
               "branch": "b", "head": "a" * 40, "pinned_cc65": M.PINNED_CC65,
               "pinned_geargrafx": M.PINNED_GEARGRAFX, "release_eligible": False,
               "dev_callable": False,
               "labels": {"api_slot_02": 0xE200, "api_unimplemented": 0xE245},
               "selection_contract": {"lba": 1, "media_identity": "x"}}
        with self.assertRaises(ValueError):
            M.verify_d_rom_slot_targets(bytes(data), man)

    def test_verify_slot_targets_accepts_real_body(self):
        # Fix1 two-stage positive (unified #784 shape): table -> api_slot_02
        # stub holding exactly JMP proto_entry; NOP; NOP.
        data = bytearray(M.ROM_BYTES)
        data[6:9] = bytes((0x4C, 0x00, 0xE2))  # $E006 -> api_slot_02 $E200
        data[0xE200 - 0xE000:0xE200 - 0xE000 + 5] = bytes((0x4C, 0x00, 0xF0, 0xEA, 0xEA))
        data[0xF000 - 0xE000] = 0x08  # PHP (real-ish proto body)
        import hashlib
        man = {"rom_name": "exp.pce", "rom_bytes": M.ROM_BYTES,
               "rom_sha256": hashlib.sha256(bytes(data)).hexdigest(),
               "cd_base_proto": {"slot": "$02", "entry": "$E006", "proto_entry": "$F000"},
               "branch": "b", "head": "a" * 40, "pinned_cc65": M.PINNED_CC65,
               "pinned_geargrafx": M.PINNED_GEARGRAFX, "release_eligible": False,
               "dev_callable": False,
               "labels": {"api_slot_02": 0xE200, "api_unimplemented": 0xE245},
               "selection_contract": {"lba": 1, "media_identity": "x"}}
        M.verify_d_rom_slot_targets(bytes(data), man)  # no raise


class MediaIdentityTests(unittest.TestCase):
    def test_mismatch_rejected(self):
        man = {"selection_contract": {"lba": 5, "media_identity": "A"}}
        with self.assertRaises(ValueError):
            M.check_embedded_selection_vs_executed(man, {"lba": 5, "identity": "B"})

    def test_unmeasured_executed_rejected(self):
        man = {"selection_contract": {"lba": 5, "media_identity": "A"}}
        with self.assertRaises(ValueError):
            M.check_embedded_selection_vs_executed(man, {"lba": 5, "identity": None})

    def test_unmeasured_manifest_rejected_when_executed_has_identity(self):
        man = {"selection_contract": {"lba": 5}}
        with self.assertRaises(ValueError):
            M.check_embedded_selection_vs_executed(man, {"lba": 5, "identity": "A"})

    def test_matching_identity_passes(self):
        man = {"selection_contract": {"lba": 5, "media_identity": "A"}}
        M.check_embedded_selection_vs_executed(man, {"lba": 5, "identity": "A"})

    def test_different_medium_same_lba_rejected(self):
        man = {"selection_contract": {"lba": 265, "media_identity": {"cue_sha256": "aaa"}}}
        with self.assertRaises(ValueError):
            M.check_embedded_selection_vs_executed(man, {"lba": 265, "identity": {"cue_sha256": "bbb"}})


def _unified_784_manifest(rom_sha, **overrides):
    """Minimal unified (#784) native manifest for host tests (structural only)."""
    man = {
        "rom_name": "experimental-dev-only-not-compatible-syscard-sized.pce",
        "rom_sha256": rom_sha, "rom_bytes": M.ROM_BYTES,
        "pinned_cc65": M.PINNED_CC65, "pinned_geargrafx": M.PINNED_GEARGRAFX,
        "release_eligible": False, "dev_callable": False,
        "branch": "agent-a/unified-bios-g5", "head": "c" * 40,
        "labels": {"api_slot_02": 0xE200, "api_unimplemented": 0xE245,
                   "cd_base_proto_entry": 0xF000, "experimental_slot_33": 0xF100,
                   "slot_02_table_entry": 0xE006, "slot_33_table_entry": 0xE099},
        "symbols": {"api_unimplemented": 0xE245},
        "selection_contract": {"lba": 7, "media_identity": "executed-id"},
        "cd_base_proto": {"slot": "$02", "entry": "$E006", "proto_entry": "$F000",
                          "patch": {"target": "api_slot_02", "length_bytes": 5}},
        "experimental_slot_33": {"table_entry": "0xE099", "body": "$F100"},
    }
    man.update(overrides)
    return man


def _unified_784_rom(api_slot_02=0xE200, proto=0xF000, body33=0xF100):
    """Unified (#784) ROM bytes: table $E006->api_slot_02->proto; $E099->body33."""
    data = bytearray(M.ROM_BYTES)
    data[0x02 * 3:0x02 * 3 + 3] = bytes((0x4C, api_slot_02 & 0xFF, (api_slot_02 >> 8) & 0xFF))
    data[api_slot_02 - 0xE000:api_slot_02 - 0xE000 + 5] = bytes(
        (0x4C, proto & 0xFF, (proto >> 8) & 0xFF, 0xEA, 0xEA))
    data[proto - 0xE000] = 0x08  # PHP: real proto body start
    data[0x33 * 3:0x33 * 3 + 3] = bytes((0x4C, body33 & 0xFF, (body33 >> 8) & 0xFF))
    data[body33 - 0xE000:body33 - 0xE000 + 2] = bytes((0x08, 0x48))  # PHP/PHA
    return bytes(data)


class NativeSlot02TwoStageTests(unittest.TestCase):
    """Fix1: native $02 two-stage verification (review 5872009452)."""

    def test_unified_784_positive(self):
        import hashlib
        rom = _unified_784_rom()
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        M.verify_d_rom_bytes(rom, man)
        M.verify_d_rom_slot_targets(rom, man)  # no raise: both slots verified

    def test_wrong_table_dest_rejected(self):
        import hashlib
        rom = bytearray(_unified_784_rom())
        rom[6:9] = bytes((0x4C, 0x00, 0xE3))  # $E006 -> $E300, not api_slot_02
        rom = bytes(rom)
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("api_slot_02", str(cm.exception))

    def test_wrong_stub_second_hop_rejected(self):
        import hashlib
        rom = bytearray(_unified_784_rom())
        # Stub holds JMP $F001 instead of JMP $F000+NOPs.
        rom[0xE200 - 0xE000:0xE200 - 0xE000 + 5] = bytes((0x4C, 0x01, 0xF0, 0xEA, 0xEA))
        rom = bytes(rom)
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("proto_entry", str(cm.exception))

    def test_stub_fake_rts_rejected(self):
        import hashlib
        rom = bytearray(_unified_784_rom())
        rom[0xE200 - 0xE000:0xE200 - 0xE000 + 5] = bytes((0x60, 0xEA, 0xEA, 0xEA, 0xEA))
        rom = bytes(rom)
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("fake RTS", str(cm.exception))

    def test_stub_nop_rts_rejected(self):
        import hashlib
        rom = bytearray(_unified_784_rom())
        rom[0xE200 - 0xE000:0xE200 - 0xE000 + 5] = bytes((0xEA, 0x60, 0xEA, 0xEA, 0xEA))
        rom = bytes(rom)
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError):
            M.verify_d_rom_slot_targets(rom, man)

    def test_proto_body_rts_rejected(self):
        import hashlib
        rom = bytearray(_unified_784_rom())
        rom[0xF000 - 0xE000] = 0x60  # proto body is RTS: not real processing
        rom = bytes(rom)
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("fake RTS", str(cm.exception))

    def test_missing_api_slot_02_blocked(self):
        import hashlib
        rom = _unified_784_rom()
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        del man["labels"]["api_slot_02"]
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("api_slot_02", str(cm.exception))

    def test_missing_proto_entry_blocked(self):
        import hashlib
        rom = _unified_784_rom()
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        del man["cd_base_proto"]["proto_entry"]
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("proto_entry", str(cm.exception))

    def test_slot33_wrong_redirect_rejected(self):
        import hashlib
        rom = bytearray(_unified_784_rom())
        rom[0x33 * 3:0x33 * 3 + 3] = bytes((0x4C, 0x00, 0xE2))  # -> $E200
        rom = bytes(rom)
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("$33", str(cm.exception))

    def test_slot33_missing_body_blocked(self):
        import hashlib
        rom = _unified_784_rom()
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        del man["experimental_slot_33"]["body"]
        del man["labels"]["experimental_slot_33"]
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("$33", str(cm.exception))

    def test_manifest_self_claim_alone_never_passes(self):
        # Declared wiring + matching SHA but single-stage-patched ROM bytes
        # (table straight to proto, stub untouched fallback) must NOT verify.
        import hashlib
        rom = bytearray(M.ROM_BYTES)
        rom[6:9] = bytes((0x4C, 0x00, 0xF0))  # 1-stage straight to proto
        rom[0xF000 - 0xE000] = 0x08
        rom = bytes(rom)
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError):
            M.verify_d_rom_slot_targets(rom, man)

    def test_out_of_window_proto_entry_blocked(self):
        import hashlib
        rom = _unified_784_rom()
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        man["cd_base_proto"]["proto_entry"] = "$1234"  # RAM, not BIOS window
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("BLOCKED", str(cm.exception))


class MissingTargetBlockedTests(unittest.TestCase):
    """Fix3: declared slot without callee target is BLOCKED, never skipped."""

    def test_legacy_slot_without_target_blocked(self):
        import hashlib
        data = bytearray(M.ROM_BYTES)
        data[6:9] = bytes((0x4C, 0x00, 0xF0))
        data[0xF000 - 0xE000] = 0x08
        rom = bytes(data)
        man = {"schema": "exp/v1", "rom_name": "exp.pce", "branch": "b",
               "head": "d" * 40,
               "slot_wiring": {"02": {"kind": "real", "description": "wiring w/o target"}},
               "pinned_cc65": M.PINNED_CC65, "pinned_geargrafx": M.PINNED_GEARGRAFX,
               "release_eligible": False, "dev_callable": False,
               "rom_sha256": hashlib.sha256(rom).hexdigest(),
               "labels": {"api_unimplemented": 0xE245},
               "selection_contract": {"lba": 1, "media_identity": "x"}}
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("no callee target", str(cm.exception))

    def test_nop_rts_body_detected(self):
        # _slot_body_is_fake_rts: NOP then RTS is fabrication, not real.
        data = bytearray(M.ROM_BYTES)
        data[6:9] = bytes((0x4C, 0x00, 0xF0))
        data[0xF000 - 0xE000:0xF000 - 0xE000 + 2] = bytes((0xEA, 0x60))
        self.assertTrue(M._slot_body_is_fake_rts(bytes(data), 0x02, 0xF000))
        self.assertTrue(M._slot_body_is_fake_rts(bytes(data), 0x33, 0xF000))
        # A real JMP+NOP pad body is not fake.
        data2 = bytearray(M.ROM_BYTES)
        data2[0xF000 - 0xE000:0xF000 - 0xE000 + 3] = bytes((0x4C, 0x10, 0xF1))
        self.assertFalse(M._slot_body_is_fake_rts(bytes(data2), 0x02, 0xF000))

    def test_native_missing_proto_not_skipped(self):
        import hashlib
        rom = _unified_784_rom()
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        man["cd_base_proto"] = {"slot": "$02", "entry": "$E006"}  # no proto_entry
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("BLOCKED", str(cm.exception))


class CommercialDFailClosedTests(unittest.TestCase):
    """Fix2: TOCTOU second-verify failure stays BLOCKED, never AttributeError."""

    def test_blocked_d_result_shape(self):
        import g5_harness as H
        r = H.blocked_d_result("BLOCKED: boom", (0x02, 0x33))
        self.assertEqual(r["verdict"], "BLOCKED")
        self.assertEqual(r["manifest_slots"], ["$02", "$33"])
        self.assertEqual(r["stop_cold"]["cause"], "BLOCKED")

    def test_gate_stops_after_manifest_cleared(self):
        import g5_harness as H
        c_results = {"D": H.blocked_d_result("BLOCKED: ROM verify")}
        # The fixed harness sets d_manifest=None here; the gate must stop the
        # rest of the D path so d_manifest.get() is never reached.
        self.assertFalse(H.commercial_d_should_continue(None, c_results))
        self.assertFalse(H.commercial_d_should_continue(None, {}))
        with self.assertRaises(AttributeError):
            None.get("labels")  # documents the old crash shape (guarded now)

    def test_gate_allows_fresh_manifest(self):
        import g5_harness as H
        self.assertTrue(H.commercial_d_should_continue({"labels": {}}, {}))
        self.assertFalse(H.commercial_d_should_continue({"labels": {}}, {"D": {}}))
        self.assertFalse(H.commercial_d_should_continue("not-a-dict", {}))

    def test_toctou_rom_changed_after_first_verify(self):
        # First verify passes; the ROM then changes/vanishes before the
        # commercial re-verify. The harness records BLOCKED and the gate stops
        # the path -- no AttributeError, no measured verdict.
        import hashlib
        import g5_harness as H
        rom = _unified_784_rom()
        man = _unified_784_manifest(hashlib.sha256(rom).hexdigest())
        M.verify_d_rom_bytes(rom, man)
        M.verify_d_rom_slot_targets(rom, man)  # first verify OK
        mutated = bytearray(rom)
        mutated[6:9] = bytes((0x4C, 0x00, 0xE3))  # table hijacked after verify
        mutated = bytes(mutated)
        c_results = {}
        d_manifest = man
        try:
            M.verify_d_rom_bytes(mutated, d_manifest)
            M.verify_d_rom_slot_targets(mutated, d_manifest)
        except (OSError, ValueError) as e:
            c_results["D"] = H.blocked_d_result(str(e), (0x02, 0x33))
            d_manifest = None
        self.assertIn("D", c_results)
        self.assertEqual(c_results["D"]["verdict"], "BLOCKED")
        self.assertFalse(H.commercial_d_should_continue(d_manifest, c_results))
        # Guarded access pattern (the fix): never dereference cleared manifest.
        d_lab = None
        if H.commercial_d_should_continue(d_manifest, c_results):
            d_lab = d_manifest.get("labels")
        self.assertIsNone(d_lab)


class MediaIdentityStrictTests(unittest.TestCase):
    """Fix4: identity of the actually-executed medium required on both sides."""

    def test_both_none_blocked(self):
        man = {"selection_contract": {"lba": 5}}
        with self.assertRaises(ValueError) as cm:
            M.check_embedded_selection_vs_executed(man, {"lba": 5, "identity": None})
        self.assertIn("BLOCKED", str(cm.exception))

    def test_manifest_without_identity_blocked_even_when_executed_none(self):
        man = {"selection_contract": {"lba": 5}}
        with self.assertRaises(ValueError):
            M.check_embedded_selection_vs_executed(man, {"lba": 5, "identity": None})

    def test_executed_without_identity_blocked(self):
        man = {"selection_contract": {"lba": 5, "media_identity": "A"}}
        with self.assertRaises(ValueError):
            M.check_embedded_selection_vs_executed(man, {"lba": 5, "identity": None})

    def test_fetch_failure_identity_none_never_bypasses(self):
        # Identity fetch failure (None) must BLOCKED even when the LBA matches.
        man = {"selection_contract": {"lba": 5, "media_identity": "A"}}
        with self.assertRaises(ValueError) as cm:
            M.check_embedded_selection_vs_executed(man, {"lba": 5, "identity": None})
        self.assertIn("unmeasured", str(cm.exception))

    def test_lba_coincidence_alone_blocked(self):
        man = {"selection_contract": {"lba": 5}}
        with self.assertRaises(ValueError):
            M.check_embedded_selection_vs_executed(man, {"lba": 5, "identity": None})

    def test_matching_measured_identity_passes(self):
        man = {"selection_contract": {"lba": 5, "media_identity": {"cue": "s.cue"}}}
        M.check_embedded_selection_vs_executed(
            man, {"lba": 5, "identity": {"cue": "s.cue"}})  # no raise

class PublicEvidenceLeakFixTests(unittest.TestCase):
    """Task 1 (PR #778): public-evidence leak closures (Problem A + B)."""

    SECRET64 = "9f3a" * 16  # 64 hex, mixed digits/letters (case-insensitive shape)

    def test_d_fixed_identifier_passes_and_real_name_absent(self):
        # Problem A: D public evidence uses the fixed structural identifier;
        # the real experimental filename (e.g. PrivateGame.pce) never appears.
        import g5_harness as H
        import inspect
        import json as _json
        ev = {"schema": M.SCHEMA, "variant": "D", "rom_name": H.D_PUBLIC_ROM_ID,
              "verdict": "UNKNOWN", "media": "synthetic"}
        clean = M.sanitize_for_public(ev)
        self.assertEqual(clean["rom_name"], "experimental-D")
        self.assertNotIn("PrivateGame", _json.dumps(clean))
        self.assertEqual(H.D_PUBLIC_ROM_ID, "experimental-D")
        src = inspect.getsource(H.main) + inspect.getsource(H)
        self.assertNotIn('summary["rom_name"] = Path(rom_d).name', src)
        self.assertIn("D_PUBLIC_ROM_ID", inspect.getsource(H.main))

    def test_64hex_in_detail_rejected(self):
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA, "detail": f"saw {self.SECRET64} here"})

    def test_64hex_in_reason_note_rejected(self):
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA, "reason": self.SECRET64})
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA, "note": f"note {self.SECRET64.upper()}"})
        # Mixed-case variant.
        mixed = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(self.SECRET64))
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA, "detail": mixed})

    def test_nested_64hex_rejected(self):
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA,
                                   "outer": {"detail": self.SECRET64}})
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA,
                                   "results": [{"note": f"x {self.SECRET64} y"}]})
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA,
                                   "reason": {"reason": f"saw {self.SECRET64} nested"}})

    def test_64hex_rejection_does_not_releak_secret(self):
        try:
            M.sanitize_for_public({"schema": M.SCHEMA, "detail": self.SECRET64})
            self.fail("expected ValueError")
        except ValueError as e:
            self.assertNotIn(self.SECRET64, str(e))
            self.assertNotIn(self.SECRET64[:16], str(e))
        upper = self.SECRET64.upper()
        try:
            M.sanitize_for_public({"schema": M.SCHEMA, "note": upper})
            self.fail("expected ValueError")
        except ValueError as e:
            self.assertNotIn(upper, str(e))

    def test_allowlisted_revision_keys_still_pass(self):
        ev = {"schema": M.SCHEMA, "head": "b" * 40,
              "pinned_geargrafx": M.PINNED_GEARGRAFX,
              "pinned_cc65": M.PINNED_CC65,
              "variant": "A", "verdict": "PRE_API_BLOCKER", "media": "synthetic",
              "rom_name": "experimental-D"}
        clean = M.sanitize_for_public(ev)
        self.assertEqual(clean["head"], "b" * 40)
        # Allowlisted key nearby never whitelists sibling free text.
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA, "head": "b" * 40,
                                   "detail": self.SECRET64})

    def test_normal_values_not_overrejected(self):
        for note in ("cold/warm both observed", "this is binary data", "and/or note"):
            clean = M.sanitize_for_public({"schema": M.SCHEMA, "note": note})
            self.assertEqual(clean["note"], note)
        ev = {"schema": M.SCHEMA, "variant": "A", "verdict": "PRE_API_BLOCKER",
              "api_count": 0, "pc": 0xE245, "slot": "$02",
              "status": "OBSERVED_STRUCTURAL_ONLY", "media": "synthetic",
              "head": "c" * 40, "pinned_cc65_short": M.PINNED_CC65_SHORT}
        clean = M.sanitize_for_public(ev)
        self.assertEqual(clean["status"], "OBSERVED_STRUCTURAL_ONLY")
        # Short git SHA (7 chars) in free text still passes.
        clean2 = M.sanitize_for_public({"schema": M.SCHEMA, "note": "pin 71746c8 ok"})
        self.assertIn("71746c8", clean2["note"])
