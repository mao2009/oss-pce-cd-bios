"""Host tests for the C-3 static checks, CI-1 (MPR / stack contract). Synthetic source trees; no emulator."""
import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import c3_static_check as C  # noqa: E402

RESET = """.setcpu "huc6280"
.segment "BOOT"
reset_entry:
    sei
    lda #$FF
    tam #$01
    lda #$F8
    tam #$02
    ldx #$FF
    txs
@halt:
    bra @halt
"""
API = """.setcpu "huc6280"
.segment "OVERRIDES"
api_slot_00:
    php
    sei
    tma #$01
    pha
    lda #$FF
    tam #$01
    pla
    tam #$01
    plp
    rts
"""
BASE = {
    "schema": 1,
    "rule_groups": ["CI-1"],
    "production_allowlist": {"status": "UNDECIDED", "approved_by": None, "review_ref": None, "mpr1_stack_banks": None},
    "scope": {"bios": ["src/reset.s", "src/api.s"], "fixture": ["src/probe.s"],
              "fixture_reasons": {"src/probe.s": "diagnostic"}},
    "files": {
        "src/reset.s": {"role": "reset_init", "tam": [{"mask": "0x01", "value": "0xFF"},
                                                      {"mask": "0x02", "value": "0xF8"}]},
        "src/api.s": {"role": "api", "tam": [{"mask": "0x01", "value": "0xFF"},
                                             {"mask": "0x01", "value": "restore", "reason": "caller MPR0"}]},
    },
}


def run(sources, contract):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, text in sources.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        path = root / "contract.json"
        path.write_text(json.dumps(contract), encoding="utf-8")
        report, _ = C.run(path, root)
    return report


def verdicts(report, rule=None):
    return [f["verdict"] for f in report.findings if rule is None or f["rule"] == rule]


SOURCES = {"src/reset.s": RESET, "src/api.s": API, "src/probe.s": "    nop\n"}


class PositiveTests(unittest.TestCase):
    def test_declared_tree_has_no_fail_and_never_pass(self):
        report = run(SOURCES, BASE)
        self.assertFalse(report.failed(), report.findings)
        self.assertNotIn("PASS", verdicts(report))
        self.assertIn(C.BLOCKED, verdicts(report, "CI-1.05"))      # reset-time MPR1: allowlist UNDECIDED
        self.assertIn(C.BLOCKED, verdicts(report, "CI-1.06"))      # TXS: stack-pointer ABI UNKNOWN
        self.assertIn(C.BLOCKED, verdicts(report, "CI-1.07"))      # allowlist UNDECIDED
        self.assertIn(C.NOT_STATIC, verdicts(report, "CI-1.08"))   # self-modifying code not proven absent

    def test_repository_contract_has_no_fail(self):
        report, _ = C.run(C.CONTRACT, ROOT)
        self.assertFalse(report.failed(), [f for f in report.findings if f["verdict"] == C.FAIL])


class NegativeTests(unittest.TestCase):
    def assert_fail(self, rule, sources=None, contract=None):
        report = run(sources or SOURCES, contract or BASE)
        self.assertIn(C.FAIL, verdicts(report, rule), report.findings)

    def test_unclassified_file(self):
        self.assert_fail("CI-1.01", {**SOURCES, "src/new.s": "    nop\n"})

    def test_fixture_without_reason(self):
        c = copy.deepcopy(BASE)
        c["scope"]["fixture_reasons"] = {}
        self.assert_fail("CI-1.01", contract=c)

    def test_mpr1_changed_by_api(self):   # mutation: an API routine remaps the stack page
        src = {**SOURCES, "src/api.s": API.replace("tam #$01\n    pla", "tam #$02\n    pla")}
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["tam"][0]["mask"] = "0x02"
        self.assert_fail("CI-1.05", src, c)

    def test_undeclared_tam(self):
        self.assert_fail("CI-1.03", {**SOURCES, "src/api.s": API.replace("    plp\n", "    tam #$04\n    plp\n")})

    def test_non_immediate_tam(self):
        self.assert_fail("CI-1.04", {**SOURCES, "src/api.s": API.replace("lda #$FF\n    tam #$01",
                                                                           "lda #$FF\n    tam $10")})

    def test_declared_value_mismatch(self):
        self.assert_fail("CI-1.04", {**SOURCES, "src/api.s": API.replace("lda #$FF", "lda #$FE")})

    def test_restore_without_save(self):   # mutation: the TMA that saves the caller's MPR0 is removed
        self.assert_fail("CI-1.04", {**SOURCES, "src/api.s": API.replace("    tma #$01\n", "")})

    def test_dynamic_mpr1_at_reset(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["tam"][1] = {"mask": "0x02", "value": "restore", "reason": "x"}
        self.assert_fail("CI-1.04", {**SOURCES, "src/reset.s": RESET.replace("lda #$F8\n", "")}, c)

    def test_txs_in_api(self):
        self.assert_fail("CI-1.06", {**SOURCES, "src/api.s": API.replace("    rts", "    txs\n    rts")})

    def test_store_into_code(self):
        self.assert_fail("CI-1.08", {**SOURCES, "src/api.s": API.replace("    plp\n", "    sta api_slot_00\n    plp\n")})

    def test_unsupported_macro(self):
        self.assert_fail("CI-1.02", {**SOURCES, "src/api.s": ".macro M\n.endmacro\n" + API})

    def test_unapproved_allowlist_values(self):   # an allowlist must not be filled in without approval
        c = copy.deepcopy(BASE)
        c["production_allowlist"]["mpr1_stack_banks"] = ["0xF8"]
        self.assert_fail("CI-1.07", contract=c)

    def test_approved_without_reference(self):
        c = copy.deepcopy(BASE)
        c["production_allowlist"]["status"] = "APPROVED"
        self.assert_fail("CI-1.07", contract=c)

    def test_declared_group_without_module_is_not_skipped(self):
        c = copy.deepcopy(BASE)
        c["rule_groups"] = ["CI-1", "CI-9"]
        self.assert_fail("CI-1.00", contract=c)

    # --- #662 independent review ---
    def _site(self, report, rule):
        return [f["verdict"] for f in report.findings if f["rule"] == rule and f["where"].startswith("src/api.s:")]

    def assert_not_static_only(self, report):
        """The undecidable TAM site is NOT_STATIC and gets no STATIC_OK of its own."""
        sites = [f["where"] for f in report.findings if f["rule"] == "CI-1.04" and f["verdict"] == C.NOT_STATIC]
        self.assertEqual(len(sites), 1, report.findings)
        self.assertFalse([f for f in report.findings if f["where"] == sites[0] and f["verdict"] == C.OK])

    def test_conditional_lda_is_not_static_not_ok(self):   # MAJOR: .ifdef / .else picks the LDA value
        src = {**SOURCES, "src/api.s": API.replace("    lda #$FF\n", ".ifdef ALT\n    lda #$10\n.else\n    lda #$FF\n.endif\n")}
        self.assert_not_static_only(run(src, BASE))

    def test_data_byte_between_lda_and_tam(self):   # MAJOR: .byte $68 assembles to PLA
        src = {**SOURCES, "src/api.s": API.replace("    lda #$FF\n", "    lda #$FF\n    .byte $68\n")}
        self.assert_not_static_only(run(src, BASE))

    def test_undecidable_mpr1_change_in_api_is_fail(self):
        src = {**SOURCES, "src/api.s": API.replace("    lda #$FF\n    tam #$01", ".ifdef ALT\n    lda #$10\n.else\n"
                                                                             "    lda #$FF\n.endif\n    tam #$02")}
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["tam"][0]["mask"] = "0x02"
        self.assert_fail("CI-1.05", src, c)

    def test_conditional_constant_is_not_fixed(self):   # MEDIUM: .ifndef V / V = $FF / .endif (a -D option)
        src = {**SOURCES, "src/api.s": ".ifndef V\nV = $FF\n.endif\n" + API.replace("lda #$FF", "lda #V")}
        self.assert_not_static_only(run(src, BASE))

    def test_unconditional_constant_is_still_resolved(self):   # positive
        src = {**SOURCES, "src/api.s": "V = $FF\n" + API.replace("lda #$FF", "lda #V")}
        report = run(src, BASE)
        self.assertFalse(report.failed(), report.findings)
        self.assertIn(C.OK, self._site(report, "CI-1.03"))

    def test_tab_after_directive(self):   # MEDIUM: .incbin<TAB>"x.bin" must not bypass the unsupported check
        self.assert_fail("CI-1.02", {**SOURCES, "src/api.s": API + ".incbin\t\"x.bin\"\n"})

    def test_tab_after_segment_is_honoured(self):
        src = {**SOURCES, "src/api.s": API.replace(".segment \"OVERRIDES\"", ".segment\t\"OVERRIDES\"").replace(
            "    plp\n", "    sta api_slot_00\n    plp\n")}
        self.assert_fail("CI-1.08", src)

    def test_store_to_label_plus_offset(self):   # MEDIUM: sta label+1 (operand patching)
        self.assert_fail("CI-1.08", {**SOURCES, "src/api.s": API.replace("    plp\n", "    sta api_slot_00+1\n    plp\n")})

    def test_read_modify_write_on_code(self):   # MEDIUM: inc / smb on a code label
        self.assert_fail("CI-1.08", {**SOURCES, "src/api.s": API.replace("    plp\n", "    inc api_slot_00\n    plp\n")})
        self.assert_fail("CI-1.08", {**SOURCES, "src/api.s": API.replace("    plp\n", "    smb3 api_slot_00\n    plp\n")})

    def test_rmw_on_zero_page_is_not_flagged(self):   # positive
        src = {**SOURCES, "src/api.s": ".zeropage\nzp: .res 1\n" + API.replace("    plp\n", "    inc zp\n    plp\n")}
        self.assertNotIn(C.FAIL, verdicts(run(src, BASE), "CI-1.08"))

    def test_malformed_contract_hex_is_fail(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["tam"][0]["value"] = "0xZZ"
        self.assert_fail("CI-1.00", contract=c)

    def test_zero_page_store_is_not_a_code_store(self):
        src = {**SOURCES, "src/api.s": ".zeropage\nzp: .res 1\n" + API.replace("    plp\n", "    sta zp\n    plp\n")}
        self.assertNotIn(C.FAIL, verdicts(run(src, BASE), "CI-1.08"))


class WiringTests(unittest.TestCase):
    def test_make_check_runs_the_tool(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = re.search(r"^check:.*$", makefile, re.M)
        recipe = []
        for line in makefile[target.end():].splitlines()[1:]:
            if not line.startswith("\t"):
                break
            recipe.append(line.strip())
        self.assertIn("$(PYTHON) tools/c3_static_check.py", recipe)

    def test_contract_allowlist_is_undecided(self):
        contract = json.loads(C.CONTRACT.read_text(encoding="utf-8"))
        allow = contract["production_allowlist"]
        self.assertEqual(allow["status"], "UNDECIDED")
        self.assertTrue(all(v is None for k, v in allow.items() if k not in ("status", "note")))


if __name__ == "__main__":
    unittest.main()
