"""Host tests for the C-3 static checks, CI-3 (IRQ / MMIO contract). Synthetic source trees; no emulator."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import c3_static_check as C  # noqa: E402

WINDOW = """.setcpu "huc6280"
.segment "OVERRIDES"
api_vdc:
    php
    sei
    tma #$01
    pha
    lda #$FF
    tam #$01
    stz a:$0000
    pla
    tam #$01
    plp
    rts
"""
DEVICE = """.setcpu "huc6280"
.segment "CODE"
CD_STATUS = $1800
ptr = $04
dev_poll:
    lda CD_STATUS
    sta $00
    sta (ptr),y
    rts
"""
RESET = """.setcpu "huc6280"
.segment "BOOT"
reset_entry:
    sei
    lda #$FF
    tam #$01
    lda #$F8
    tam #$02
    jsr dev_poll
@halt:
    bra @halt
irq_halt:
    bra irq_halt

.segment "VECTORS"
    .word irq_halt
    .word irq_halt
    .word irq_halt
    .word irq_halt
    .word reset_entry
"""
SOURCES = {"src/api.s": WINDOW, "src/dev.s": DEVICE, "src/reset.s": RESET}
BASE = {
    "schema": 1,
    "rule_groups": ["CI-1", "CI-2", "CI-3"],
    "production_allowlist": {"status": "UNDECIDED", "approved_by": None, "review_ref": None},
    "regions": {},
    "scope": {"bios": sorted(SOURCES), "fixture": [], "fixture_reasons": {}},
    "files": {
        "src/api.s": {"role": "api", "transfers": [], "tam": [
            {"mask": "0x01", "value": "0xFF"}, {"mask": "0x01", "value": "restore", "reason": "caller MPR0"}],
            "mmio": {"device": "VDC", "addresses": ["0x0000"], "mapping": "window"}},
        "src/dev.s": {"role": "internal", "tam": [], "transfers": [],
                      "mmio": {"device": "CD", "addresses": ["0x1800"], "mapping": "precondition", "entry": "dev_poll",
                               "callers": ["src/reset.s"]},
                      "indirect": {"count": 1, "reason": "result through ptr"}},
        "src/reset.s": {"role": "reset_init", "transfers": [], "tam": [
            {"mask": "0x01", "value": "0xFF"}, {"mask": "0x02", "value": "0xF8"}],
            "vectors": {"irq_handlers": ["irq_halt"], "reset": "reset_entry"}},
    },
}


def run(sources=None, contract=None):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, text in (sources or SOURCES).items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        path = root / "contract.json"
        path.write_text(json.dumps(contract or BASE), encoding="utf-8")
        report, _ = C.run(path, root)
    return report


def by_rule(report, rule):
    return [f["verdict"] for f in report.findings if f["rule"] == rule]


class PositiveTests(unittest.TestCase):
    def test_declared_tree(self):
        report = run()
        self.assertFalse(report.failed(), [f for f in report.findings if f["verdict"] == C.FAIL])
        self.assertIn(C.OK, by_rule(report, "CI-3.02"))         # SEI-masked window, restored
        self.assertIn(C.BLOCKED, by_rule(report, "CI-3.02"))    # device list: production allowlist UNDECIDED
        self.assertIn(C.OK, by_rule(report, "CI-3.03"))         # caller maps MPR0 = $FF before the JSR
        self.assertIn(C.NOT_STATIC, by_rule(report, "CI-3.04"))  # indirect access: run-time address
        self.assertIn(C.OK, by_rule(report, "CI-3.05"))         # handler without mapping / I/O changes
        self.assertIn(C.NOT_STATIC, by_rule(report, "CI-3.06"))  # DMA / IRQ timing / hardware

    def test_zero_page_is_not_mmio(self):   # "sta $00" is zero page ($2000), not the hardware page
        report = run()
        self.assertFalse(any("$0000" in f["message"] and f["where"].startswith("src/dev.s")
                             for f in report.findings if f["verdict"] == C.FAIL))

    def test_repository_contract_has_no_fail(self):
        report, _ = C.run(C.CONTRACT, ROOT)
        self.assertFalse(report.failed(), [f for f in report.findings if f["verdict"] == C.FAIL])


class NegativeTests(unittest.TestCase):
    def assert_fail(self, rule, sources=None, contract=None):
        report = run(sources, contract)
        self.assertIn(C.FAIL, by_rule(report, rule), report.findings)

    def test_window_without_sei(self):   # mutation: the IRQ mask before the MPR0 window is removed
        self.assert_fail("CI-3.02", {**SOURCES, "src/api.s": WINDOW.replace("    sei\n", "")})

    def test_window_never_restored(self):
        src = {**SOURCES, "src/api.s": WINDOW.replace("    pla\n    tam #$01\n", "    pla\n")}
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["tam"] = c["files"]["src/api.s"]["tam"][:1]
        self.assert_fail("CI-3.02", src, c)

    def test_undeclared_hardware_access(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/api.s": WINDOW.replace("stz a:$0000", "stz a:$0000\n    "
                                                                                          "sta a:$0400")})

    def test_undeclared_mmio_in_file_without_declaration(self):
        c = copy.deepcopy(BASE)
        del c["files"]["src/dev.s"]["mmio"]
        self.assert_fail("CI-3.02", contract=c)

    def test_stale_declared_address(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["mmio"]["addresses"].append("0x0002")
        self.assert_fail("CI-3.02", contract=c)

    def test_caller_does_not_map_mpr0(self):   # mutation: the reset entry no longer maps the hardware page
        src = {**SOURCES, "src/reset.s": RESET.replace("    lda #$FF\n    tam #$01\n", "")}
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["tam"] = c["files"]["src/reset.s"]["tam"][1:]
        self.assert_fail("CI-3.03", src, c)

    def test_caller_calls_before_mapping(self):
        src = {**SOURCES, "src/reset.s": RESET.replace("    sei\n", "    sei\n    jsr dev_poll\n").replace(
            "    jsr dev_poll\n@halt", "@halt")}
        self.assert_fail("CI-3.03", src)

    def test_undeclared_indirect(self):
        c = copy.deepcopy(BASE)
        del c["files"]["src/dev.s"]["indirect"]
        self.assert_fail("CI-3.04", contract=c)

    def test_undeclared_st0(self):
        self.assert_fail("CI-3.01", {**SOURCES, "src/api.s": WINDOW.replace("    plp\n", "    st0 #$00\n    plp\n")})

    def test_vector_to_undeclared_handler(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["vectors"]["irq_handlers"] = []
        self.assert_fail("CI-3.05", contract=c)

    def test_handler_with_tam(self):   # mutation: the IRQ handler remaps a page
        src = {**SOURCES, "src/reset.s": RESET.replace("irq_halt:\n    bra irq_halt",
                                                       "irq_halt:\n    tam #$04\n    bra irq_halt")}
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["tam"].append({"mask": "0x04", "value": "restore", "reason": "x"})
        self.assert_fail("CI-3.05", src, c)

    # --- #661 review: false-OK cases, each must now FAIL ---
    def test_unresolved_imported_operand(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": DEVICE.replace("    rts", "    sta IOP\n    rts")})

    def test_tst_memory_operand_is_seen(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": DEVICE.replace("    rts", "    tst #$01, a:$1804\n    rts")})

    def test_abs_y_on_lda_is_not_zero_page(self):   # lda $00,y assembles absolute,Y: $0000+Y
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": DEVICE.replace("    rts", "    lda $00,y\n    rts")})

    def test_indexed_page0_access(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": DEVICE.replace("lda CD_STATUS", "lda CD_STATUS,x")})

    def test_window_file_without_window(self):
        src = {**SOURCES, "src/api.s": WINDOW.replace("    lda #$FF\n    tam #$01\n", "    pla\n    pha\n")}
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["tam"] = c["files"]["src/api.s"]["tam"][1:]
        self.assert_fail("CI-3.02", src, c)

    def test_cli_between_sei_and_window(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/api.s": WINDOW.replace("    tma #$01\n", "    cli\n    tma #$01\n")})

    def test_branch_inside_window(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/api.s": WINDOW.replace("    stz a:$0000\n",
                                                                           "    bcs @out\n    stz a:$0000\n@out:\n")})

    def test_access_after_restore(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/api.s": WINDOW.replace("    stz a:$0000\n", "").replace(
            "    plp\n", "    stz a:$0000\n    plp\n")})

    def test_restore_that_maps_ff_again(self):
        src = {**SOURCES, "src/api.s": WINDOW.replace("    pla\n    tam #$01\n", "    lda #$FF\n    tam #$01\n")}
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["tam"][1] = {"mask": "0x01", "value": "0xFF"}
        self.assert_fail("CI-3.02", src, c)

    def test_undeclared_precondition_caller(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/dev.s"]["mmio"]["callers"] = []
        self.assert_fail("CI-3.03", contract=c)

    def test_caller_restores_before_call(self):
        src = {**SOURCES, "src/reset.s": RESET.replace("    jsr dev_poll\n", "    tma #$01\n    tam #$01\n    jsr dev_poll\n")}
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["tam"].append({"mask": "0x01", "value": "restore", "reason": "x"})
        self.assert_fail("CI-3.03", src, c)

    def test_stale_declared_caller(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/dev.s"]["mmio"]["callers"].append("src/api.s")
        self.assert_fail("CI-3.03", contract=c)

    def test_precondition_entry_must_exist(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/dev.s"]["mmio"]["entry"] = "nowhere"
        self.assert_fail("CI-3.03", contract=c)

    def test_handler_calls_out(self):
        self.assert_fail("CI-3.05", {**SOURCES, "src/reset.s": RESET.replace("irq_halt:\n    bra irq_halt",
                                                                             "irq_halt:\n    jsr dev_poll\n    bra irq_halt")})

    def test_handler_indirect_operand(self):
        self.assert_fail("CI-3.05", {**SOURCES, "src/reset.s": RESET.replace("irq_halt:\n    bra irq_halt",
                                                                             "irq_halt:\n    sta ($10)\n    bra irq_halt")})

    def test_handler_falls_through(self):
        self.assert_fail("CI-3.05", {**SOURCES, "src/reset.s": RESET.replace("irq_halt:\n    bra irq_halt",
                                                                             "irq_halt:\n    nop")})

    def test_vectors_other_than_word(self):
        self.assert_fail("CI-3.05", {**SOURCES, "src/reset.s": RESET.replace("    .word irq_halt\n    .word irq_halt\n",
                                                                             "    .addr irq_halt, irq_halt\n")})

    def test_hardware_page_through_another_mpr(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": DEVICE.replace("    rts", "    lda #$FF\n    tam #$04\n    rts")},
                         {**BASE, "files": {**BASE["files"], "src/dev.s": {**BASE["files"]["src/dev.s"], "tam": [
                             {"mask": "0x04", "value": "0xFF"}]}}})

    def test_pages_2_to_6_are_not_static(self):
        report = run({**SOURCES, "src/dev.s": DEVICE.replace("    rts", "    sta $4800\n    rts")})
        self.assertIn(C.NOT_STATIC, by_rule(report, "CI-3.02"))

    def test_duplicate_constant_is_unsupported(self):
        report = run({**SOURCES, "src/dev.s": DEVICE.replace("ptr = $04", "ptr = $04\nptr = $06")})
        self.assertIn(C.FAIL, by_rule(report, "CI-1.02"))

    # --- #661 re-review (N1-N4, LOW) ---
    def test_sei_in_another_routine(self):   # N1: SEI in a previous routine does not mask this window
        src = {**SOURCES, "src/api.s": WINDOW.replace("api_vdc:\n    php\n    sei\n",
                                                      "other:\n    sei\n    rts\napi_vdc:\n    php\n")}
        self.assert_fail("CI-3.02", src)

    def test_sei_skipped_by_a_branch(self):   # N1: a label between SEI and the window = another entry path
        self.assert_fail("CI-3.02", {**SOURCES, "src/api.s": WINDOW.replace("    sei\n", "    bcs @x\n    sei\n@x:\n")})

    def test_label_between_lda_and_tam(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/api.s": WINDOW.replace("    lda #$FF\n    tam #$01\n",
                                                                           "    lda #$FF\n@e:\n    tam #$01\n")})

    def test_caller_tam_in_another_routine(self):   # N2
        src = {**SOURCES, "src/reset.s": RESET.replace("    jsr dev_poll\n", "other:\n    jsr dev_poll\n")}
        self.assert_fail("CI-3.03", src)

    def test_pass_label_needs_reason_and_no_target(self):
        src = {**SOURCES, "src/reset.s": RESET.replace("    jsr dev_poll\n", "ready:\n    jsr dev_poll\n")}
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["mmio_pass_labels"] = {"ready": "observation point"}
        report = run(src, c)
        self.assertNotIn(C.FAIL, by_rule(report, "CI-3.03"), report.findings)
        self.assertIn(C.NOT_STATIC, by_rule(report, "CI-3.03"))
        c["files"]["src/reset.s"]["mmio_pass_labels"] = {"ready": ""}            # no reason
        self.assert_fail("CI-3.03", src, c)
        c["files"]["src/reset.s"]["mmio_pass_labels"] = {"ready": "observation point"}
        targeted = {**src, "src/api.s": WINDOW.replace("    rts", "    jmp ready")}  # something jumps to it
        self.assert_fail("CI-3.03", targeted, c)

    def test_zero_page_label_with_absolute_y(self):   # N3: lda buf,y assembles absolute,Y -> $00xx
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": ".zeropage\nbuf: .res 4\n" + DEVICE.replace(
            "    rts", "    lda buf,y\n    rts")})

    def test_zero_page_label_forced_absolute(self):
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": ".zeropage\nbuf: .res 4\n" + DEVICE.replace(
            "    rts", "    lda a:buf\n    rts")})

    def test_rom_label_with_large_offset(self):   # N4
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": DEVICE.replace("    rts", "    lda tbl-$D000\n    rts\ntbl:\n"
                                                                            "    nop")})

    def test_non_bare_call_operands(self):
        self.assert_fail("CI-3.03", {**SOURCES, "src/reset.s": RESET.replace("jsr dev_poll", "jsr dev_poll+0")})
        self.assert_fail("CI-3.03", {**SOURCES, "src/api.s": WINDOW.replace("    rts", "    jmp ($0010)")})

    def test_brk_in_handler(self):
        self.assert_fail("CI-3.05", {**SOURCES, "src/reset.s": RESET.replace("irq_halt:\n    bra irq_halt",
                                                                             "irq_halt:\n    brk\n    bra irq_halt")})

    def test_window_closed_by_an_immediate(self):
        src = {**SOURCES, "src/api.s": WINDOW.replace("    pla\n    tam #$01\n", "    lda #$00\n    tam #$01\n")}
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["tam"][1] = {"mask": "0x01", "value": "0x00"}
        self.assert_fail("CI-3.02", src, c)

    def test_pass_label_targeted_by_bbr(self):
        src = {**SOURCES, "src/reset.s": RESET.replace("    jsr dev_poll\n", "ready:\n    jsr dev_poll\n"),
               "src/api.s": WINDOW.replace("    rts", "    bbr0 $10, ready\n    rts")}
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["mmio_pass_labels"] = {"ready": "observation point"}
        self.assert_fail("CI-3.03", src, c)

    def test_other_mpr0_tam_inside_window(self):
        src = {**SOURCES, "src/api.s": WINDOW.replace("    stz a:$0000\n", "    stz a:$0000\n    tam #$03\n")}
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["tam"].insert(1, {"mask": "0x03", "value": "restore", "reason": "x"})
        self.assert_fail("CI-3.02", src, c)

    # --- #667 final review (M1-M3, D1-D2) ---
    def test_conditional_sei(self):   # M1: a build without X has no IRQ mask
        self.assert_fail("CI-3.02", {**SOURCES, "src/api.s": WINDOW.replace("    sei\n", ".ifdef X\n    sei\n.endif\n")})

    def test_conditional_mpr0_setup_before_unconditional_call(self):   # M1
        src = {**SOURCES, "src/reset.s": RESET.replace("    lda #$FF\n    tam #$01\n",
                                                       ".ifdef X\n    lda #$FF\n    tam #$01\n.endif\n")}
        self.assert_fail("CI-3.03", src)

    def test_conditional_setup_before_differently_conditional_call(self):   # R1: .ifdef A set-up, .ifdef B call
        src = {**SOURCES, "src/reset.s": RESET.replace("    lda #$FF\n    tam #$01\n",
                                                       ".ifdef A\n    lda #$FF\n    tam #$01\n.endif\n").replace(
            "    jsr dev_poll\n", ".ifdef B\n    jsr dev_poll\n.endif\n")}
        self.assert_fail("CI-3.03", src)

    def test_unconditional_setup_before_conditional_call_is_accepted(self):   # the reset_entry.s pattern
        src = {**SOURCES, "src/reset.s": RESET.replace("    jsr dev_poll\n", ".ifdef B\n    jsr dev_poll\n.endif\n")}
        self.assertNotIn(C.FAIL, by_rule(run(src), "CI-3.03"))

    def test_multi_mpr_tam_remaps_mpr0_before_call(self):   # M2: TAM #$05 includes MPR0
        src = {**SOURCES, "src/reset.s": RESET.replace("    jsr dev_poll\n", "    lda #$00\n    tam #$05\n    jsr dev_poll\n")}
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["tam"].append({"mask": "0x05", "value": "0x00"})
        self.assert_fail("CI-3.03", src, c)

    def test_handler_branches_out(self):   # M3
        self.assert_fail("CI-3.05", {**SOURCES, "src/reset.s": RESET.replace(
            "irq_halt:\n    bra irq_halt", "irq_halt:\n    beq reset_entry\n    bra irq_halt")})

    def test_caller_by_branch_is_discovered(self):   # D1
        src = {**SOURCES, "src/api.s": WINDOW.replace("    rts", "    clc\n    bcc dev_poll")}
        self.assert_fail("CI-3.03", src)

    def test_reset_init_without_vectors_is_not_static(self):   # D2
        src = {**SOURCES, "src/reset.s": RESET.split(".segment \"VECTORS\"")[0]}
        c = copy.deepcopy(BASE)
        del c["files"]["src/reset.s"]["vectors"]
        report = run(src, c)
        self.assertIn(C.NOT_STATIC, by_rule(report, "CI-3.05"))

    # --- merge of #662 / #663 into #667: build-dependent addresses ---
    def test_build_dependent_address_is_not_static_not_fail(self):   # .ifndef default, overridden by -D
        src = {**SOURCES, "src/dev.s": DEVICE.replace("ptr = $04", "ptr = $04\n.ifndef R0\nR0 = $2300\n.endif\nRES = R0")
               .replace("    rts", "    sta RES+1\n    rts")}
        report = run(src)
        self.assertNotIn(C.FAIL, by_rule(report, "CI-3.02"), report.findings)
        self.assertTrue(any(f["verdict"] == C.NOT_STATIC and "build-dependent" in f["message"]
                            for f in report.findings if f["rule"] == "CI-3.02"))

    def test_unknown_symbol_is_still_fail(self):   # an import or unknown name is not build-dependent
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": DEVICE.replace("    rts", "    sta RES+1\n    rts")})

    def test_reset_vector_not_declared(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["vectors"]["reset"] = "other"
        self.assert_fail("CI-3.05", contract=c)


if __name__ == "__main__":
    unittest.main()
