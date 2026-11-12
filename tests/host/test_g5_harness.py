"""Host tests for the G5 boot-progress measurement harness (Agent C, parent #635).

Pure model tests only: no core, no cc65, no media, no builds. All public
tests remain reproducible without private media.
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

    def test_pins(self):
        self.assertTrue(M.PINNED_GEARGRAFX.startswith("b49ae82a"))
        self.assertTrue(M.PINNED_CC65.startswith("71746c8"))


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
                              cold_warm_identical=True)
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

    def test_g5_observed_needs_basis(self):
        kw = dict(variant_key="C", calls=[(2, "JSR"), (51, "JSR"), (3, "JSR")],
                  runs=[run("api_unimplemented", x=3),
                        run("api_unimplemented", x=3, cycle="warm")],
                  stub_addr=STUB, return_kind="FAKE_RTS",
                  cold_warm_identical=True, frame_progress=True,
                  screen_changed=True)
        v1, _ = M.classify_g5(title_observed=True, title_basis="", **kw)
        self.assertNotEqual(v1, "G5_OBSERVED")
        v2, d2 = M.classify_g5(title_observed=True,
                               title_basis="human-confirmed title frame + state note", **kw)
        self.assertEqual(v2, "G5_OBSERVED")
        self.assertIn("human-confirmed", d2)

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

    def test_private_keys_refused(self):
        for bad in ("cue_path", "media_hash", "rom_dir", "title_name", "trace_dump", "save_path"):
            with self.assertRaises(ValueError, msg=bad):
                M.sanitize_for_public({"schema": M.SCHEMA, bad: "x"})

    def test_media_refs_refused(self):
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA, "note": "see ./rom/game.cue"})
        with self.assertRaises(ValueError):
            M.sanitize_for_public({"schema": M.SCHEMA, "note": "wrote /tmp/opencode/x.json"})


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
