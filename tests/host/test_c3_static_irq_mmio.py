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

    def test_build_dependent_mixed_with_unknown_name_is_fail(self):   # B1
        src = {**SOURCES, "src/dev.s": DEVICE.replace("ptr = $04", "ptr = $04\n.ifndef R0\nR0 = $2300\n.endif")
               .replace("    rts", "    sta R0+IMP\n    rts")}
        self.assert_fail("CI-3.02", src)

    def test_window_summary_not_ok_with_build_dependent_access(self):   # B2
        src = {**SOURCES, "src/api.s": ".ifndef HW\nHW = $0002\n.endif\n" + WINDOW.replace(
            "    plp\n", "    sta a:HW\n    plp\n")}
        report = run(src)
        self.assertFalse([f for f in report.findings if f["rule"] == "CI-3.02" and f["verdict"] == C.OK
                          and f["where"] == "src/api.s"], report.findings)

    def test_unknown_symbol_is_still_fail(self):   # an import or unknown name is not build-dependent
        self.assert_fail("CI-3.02", {**SOURCES, "src/dev.s": DEVICE.replace("    rts", "    sta RES+1\n    rts")})

    def test_reset_vector_not_declared(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/reset.s"]["vectors"]["reset"] = "other"
        self.assert_fail("CI-3.05", contract=c)


SHARED_HEALTHY = """.setcpu "huc6280"
.segment "CODE"
.ifdef VAR_A
head_a:
    php
    sei
    tma #$01
    pha
    lda #$FF
    tam #$01
    lda #$05
    sta a:$0000
    bra shared_tail
.endif
.ifdef VAR_B
head_b:
    php
    sei
    tma #$01
    pha
    lda #$FF
    tam #$01
    lda #$05
    sta a:$0000
    bra shared_tail
.endif
shared_tail:
    pla
    tam #$01
    plp
    rts
"""
SHARED_BASE = {
    "schema": 1,
    "rule_groups": ["CI-1", "CI-2", "CI-3"],
    "production_allowlist": {"status": "UNDECIDED", "approved_by": None, "review_ref": None},
    "regions": {},
    "scope": {"bios": ["src/shared.s"], "fixture": [], "fixture_reasons": {}},
    "files": {
        "src/shared.s": {"role": "api", "transfers": [], "tam": [
            {"mask": "0x01", "value": "0xFF"},
            {"mask": "0x01", "value": "restore", "reason": "caller MPR0"}],
            "mmio": {"device": "VDC", "addresses": ["0x0000"], "mapping": "window",
                     "shared_tail": "shared_tail", "variants": ["VAR_A", "VAR_B"]}},
    },
}


def run_shared(text, contract=None):
    return run({"src/shared.s": text}, copy.deepcopy(contract or SHARED_BASE))


class SharedTailTests(unittest.TestCase):
    """Shared-tail conditional-unit windows (#843 Wave2): the tail span is scanned head-inclusive and any
    unresolvable conditional structure inside a claimed span is a FAIL."""
    def assert_shared_fail(self, text):
        report = run_shared(text)
        self.assertIn(C.FAIL, by_rule(report, "CI-3.02"), report.findings)

    def test_healthy_shared_tail_accepted(self):   # unconditional tail, uniform variant sections, no .else
        report = run_shared(SHARED_HEALTHY)
        self.assertNotIn(C.FAIL, by_rule(report, "CI-3.02"), report.findings)
        self.assertIn(C.OK, by_rule(report, "CI-3.02"))

    def test_tail_head_cli_breaks_window(self):   # off-by-one: the first tail instruction must count
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",
                                                         "shared_tail:\n    cli\n    pla\n"))

    def test_tail_head_plp_breaks_window(self):
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",
                                                         "shared_tail:\n    plp\n    pla\n"))

    def test_tail_head_branch_breaks_window(self):
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",
                                                         "shared_tail:\n    bra shared_tail\n    pla\n"))

    def test_tail_head_txs_breaks_window(self):
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",
                                                         "shared_tail:\n    txs\n    pla\n"))

    def test_conditional_tail_head_breaker_is_unresolvable(self):   # conditional CLI at the tail head
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",
                                                         "shared_tail:\n.ifdef VAR_A\n    cli\n.endif\n"
                                                         "    pla\n"))

    def test_else_inside_variant_span_is_unresolvable(self):   # .else mid-span, uniform ifs otherwise
        self.assert_shared_fail(SHARED_HEALTHY.replace("    sta a:$0000\n    bra shared_tail\n.endif\n"
                                                         ".ifdef VAR_B",
                                                         "    sta a:$0000\n.else\n    nop\n    bra shared_tail\n"
                                                         ".endif\n.ifdef VAR_B"))

    def test_nested_ifdef_inside_variant_span_is_unresolvable(self):   # span instruction outside VAR_A
        self.assert_shared_fail(SHARED_HEALTHY.replace("head_a:\n    php\n    sei\n",
                                                         "head_a:\n    php\n    sei\n.ifdef EXTRA\n    nop\n"
                                                         ".endif\n"))

    def test_nested_conditional_sei_is_omitted(self):   # the only SEI sits under an extra .ifdef: no same-section SEI
        self.assert_shared_fail(SHARED_HEALTHY.replace("head_a:\n    php\n    sei\n",
                                                         "head_a:\n    php\n.ifdef EXTRA\n    sei\n.endif\n"))

    def test_ifndef_opener_cannot_claim_variant(self):   # polarity: a `.ifndef VAR_A` block is the VAR_A-absent
        self.assert_shared_fail(SHARED_HEALTHY.replace(".ifdef VAR_A\nhead_a:", ".ifndef VAR_A\nhead_a:"))  # path

    def test_if_comparison_opener_cannot_claim_variant(self):  # #875: VAR_A-absent paths
        self.assert_shared_fail(SHARED_HEALTHY.replace(".ifdef VAR_A\nhead_a:", ".if VAR_A = 0\nhead_a:"))
        self.assert_shared_fail(SHARED_HEALTHY.replace(".ifdef VAR_A\nhead_a:", ".if VAR_A <> 1\nhead_a:"))
        self.assert_shared_fail(SHARED_HEALTHY.replace(".ifdef VAR_A\nhead_a:",
                                                         ".if !DEFINED(VAR_A)\nhead_a:"))

    def test_endif_reopen_breaks_span_identity(self):   # same operand, new block: SEI and set-up no longer share it
        self.assert_shared_fail(SHARED_HEALTHY.replace("head_a:\n    php\n    sei\n",
                                                         "head_a:\n    php\n    sei\n.endif\n.ifdef VAR_A\n"))

    def test_if_ifdef_reopen_mismatch(self):   # same operand text, different kinds/blocks: still a reopen
        self.assert_shared_fail(SHARED_HEALTHY.replace(".ifdef VAR_A\nhead_a:", ".if VAR_A\nhead_a:").replace(
            "head_a:\n    php\n    sei\n", "head_a:\n    php\n    sei\n.endif\n.ifdef VAR_A\n"))

    def test_pre_sei_else_branch_cannot_claim_variant(self):   # window in the .else (VAR_A-absent) branch
        self.assert_shared_fail(SHARED_HEALTHY.replace(".ifdef VAR_A\nhead_a:\n    php\n    sei\n",
                                                         ".ifdef VAR_A\nhead_a:\n    php\n.else\n    sei\n"))

    def test_pre_sei_elseif_branch_cannot_claim_variant(self):   # window past .elseif: same absent-branch rule
        self.assert_shared_fail(SHARED_HEALTHY.replace(".ifdef VAR_A\nhead_a:\n    php\n    sei\n",
                                                         ".ifdef VAR_A\nhead_a:\n    php\n.elseif VAR_A\n"
                                                         "    sei\n"))

    def test_conditional_tail_label_is_unresolvable(self):   # the label exists only in VAR_A builds
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",
                                                         ".ifdef VAR_A\nshared_tail:\n.endif\n    pla\n"))

    def test_outside_span_conditional_parcel_still_accepted(self):   # unrelated parcel after the tail: healthy
        report = run_shared(SHARED_HEALTHY + ".ifdef EXTRA\nextra_pin:\n    nop\n    rts\n.endif\n")
        self.assertNotIn(C.FAIL, by_rule(report, "CI-3.02"), report.findings)
        self.assertIn(C.OK, by_rule(report, "CI-3.02"))

    # --- tail/variant range hardening (Wave2 follow-up): label-anchored scans ---
    # Boundary table (10 boundaries; T0 = first tail instruction line, TJ = restore TAM line,
    # H = variant-head label line, S = SEI line, B = closing BRA line):
    #   T1  directive at tail_no (`shared_tail: .byte ...`) .................... FAIL (inspected)
    #   T2  same-line-label directive in (tail_no, T0) (`pad: .byte`) .......... FAIL (inspected)
    #   T3  stray label in (tail_no, T0) (`stray:` between tail and pla) ....... FAIL (inspected)
    #   T4  directive at T0 (same line as the first tail instruction) .......... out-of-scope: the
    #       reader models one statement per physical line, so no line can be both a directive and an
    #       instruction sharing that number.
    #   T5  interior directive in (T0, TJ) (`.byte` mid-tail) .................. FAIL (inspected pin;
    #       already FAIL before this change, kept against regressions)
    #   T6  directive at TJ (same line as the restoring TAM) ................... out-of-scope: same
    #       one-statement-per-line reason as T4.
    #   T7  directive after TJ (post-restore parcel) ........................... out-of-scope: outside
    #       the span by construction; covered by test_outside_span_conditional_parcel_still_accepted.
    #   V1  directive in [H, S) (`.byte` between the head label and SEI) ...... FAIL (inspected)
    #   V2  outer scaffolding (`.ifdef V` opener before H, `.endif` after B) ... ACCEPT (inspected via
    #       the healthy fixture and the outside-span parcel: exempt by range, not by directive kind).
    #   N1  negated/comparison opener (`.if VAR_A = 0` claiming VAR_A) ......... out-of-scope here:
    #       #875 residual, intentionally unchanged by this task; starter-polarity (.ifndef / .else /
    #       .elseif, tested above) still FAILs, so no guarantee is weakened.
    def test_tail_label_byte_is_rejected(self):   # T1: directive on the tail-label line itself
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",
                                                         "shared_tail: .byte $00\n    pla\n"))

    def test_same_line_label_byte_in_tail_gap_is_rejected(self):   # T2: `pad: .byte` hides between a
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",  # label-only tail
                                                         "shared_tail:\npad: .byte $00\n    pla\n"))  # and pla

    def test_stray_label_between_tail_label_and_head_is_rejected(self):   # T3: other label in the gap
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",
                                                         "shared_tail:\nstray:\n    pla\n"))

    def test_interior_tail_byte_is_rejected(self):   # T5 pin: `.byte` between pla and the restore
        self.assert_shared_fail(SHARED_HEALTHY.replace("shared_tail:\n    pla\n",
                                                         "shared_tail:\n    pla\n    .byte $00\n"))

    def test_byte_between_head_and_sei_is_rejected(self):   # V1: smuggled bytes below the old SEI bound
        self.assert_shared_fail(SHARED_HEALTHY.replace("head_a:\n    php\n    sei\n",
                                                         "head_a:\n    php\n    .byte $00\n    sei\n"))


if __name__ == "__main__":
    unittest.main()
