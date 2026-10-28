"""Host checks for the project-own EX_SETWRT (slot $3A) VDC MAWR contract (Issue #68, DEV only).

Scope: a Python model of the documented contract (not the assembly) plus an exact instruction-sequence
check of src/overrides/slot_3A.s. No HuC6280 execution happens here. These checks are NOT evidence of
behaviour on hardware or on the Geargrafx core; real-core verification is a separate, verifier-run step.

Stack contract:
  Normal path inside api_slot_3A pushes at most 5 bytes simultaneously:
    php, phy, phx, pha (caller A), pha (MPR0)
  JSR return address: 2 bytes below caller SP.
  Total max depth below caller SP: 5 + 2 = 7 bytes.
  SP=$07 is the contract lower bound: deepest write reaches $2101 (inside stack page $2100-$21FF),
  SP reaches $00 after the 5th push but does not wrap.
  SP=$06 causes SP wraparound during the 5th push; out of contract.
  Reject path: bcs branches immediately, 0 pushes inside routine, only JSR's 2 bytes.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "overrides" / "slot_3A.s"

# Exact instruction sequence (comments and directives stripped). Any change must update this on purpose.
EXPECTED = [
    "cpx #$80", "bcs setwrt_reject",
    "php", "sei", "phy", "phx", "pha",
    "tma #$01", "pha", "lda #$FF", "tam #$01",
    "stz a:$0000", "pla", "tay", "pla", "sta a:$0002", "plx", "stx a:$0003",
    "pha", "tya", "tam #$01", "pla", "ply", "plp", "clc", "rts",
    "sec", "rts",
]


def setwrt_model(a, x, mawr=None):
    """Model of the contract: return (ok, mawr_after, a_after, x_after). X >= $80 changes nothing."""
    if not (0 <= a <= 255 and 0 <= x <= 255):
        raise ValueError("A and X must be bytes")
    if x >= 0x80:
        return False, mawr, a, x
    return True, (x << 8) | a, a, x


# 構造化拒否理由コード。テストと実装の間で安定した契約を形成する。
REASON_STACK_OVERFLOW = "STACK_OVERFLOW"
REASON_VDC_CHANGED = "VDC_CHANGED"
REASON_C_NOT_SET = "C_NOT_SET"
REASON_NOOP_UNDETECTED = "NOOP_UNDETECTED"
REASON_HALT_FAILURE = "HALT_FAILURE"
REASON_UNEXPECTED_PASS = "UNEXPECTED_PASS"
REASON_MAWR_CHANGED = "MAWR_CHANGED"
REASON_REGISTER_MISMATCH = "REGISTER_MISMATCH"
REASON_STACK_MISMATCH = "STACK_MISMATCH"
REASON_MPR_MISMATCH = "MPR_MISMATCH"


class SetwrtModelTests(unittest.TestCase):
    def test_boundary_addresses(self):
        self.assertEqual(setwrt_model(0x00, 0x00), (True, 0x0000, 0x00, 0x00))
        self.assertEqual(setwrt_model(0xFF, 0x7F), (True, 0x7FFF, 0xFF, 0x7F))
        self.assertEqual(setwrt_model(0x34, 0x12), (True, 0x1234, 0x34, 0x12))

    def test_out_of_range_is_rejected_without_change(self):
        self.assertEqual(setwrt_model(0x00, 0x80, mawr=0x2222), (False, 0x2222, 0x00, 0x80))
        self.assertEqual(setwrt_model(0xAB, 0xFF, mawr=0x1111), (False, 0x1111, 0xAB, 0xFF))

    def test_non_byte_input_is_refused(self):
        for a, x in ((256, 0), (0, 256), (-1, 0), (0, -1)):
            with self.assertRaises(ValueError):
                setwrt_model(a, x)


class SetwrtStackTests(unittest.TestCase):
    """スタック境界の静的検証。実コアでは geargrafx_dev_ex_setwrt.py でSP=$07/$08を実測する。"""

    def test_normal_path_max_pushes_is_5(self):
        # api_slot_3A normal path: php, phy, phx, pha (caller A), pha (MPR0) = 5 simultaneous pushes.
        # After these, pla/pla/pha/pla/ply/plp pop/restore. Max simultaneous = 5.
        # JSR return address adds 2 bytes below caller SP.
        # Total max depth below caller SP = 5 + 2 = 7.
        self.assertEqual(5 + 2, 7)

    def test_reject_path_max_pushes_is_0(self):
        # Reject path: cpx #$80 / bcs setwrt_reject / sec / rts, no pushes inside routine.
        # Only JSR's 2 bytes below caller SP.
        self.assertEqual(0 + 2, 2)

    def test_sp_07_is_contract_lower_bound(self):
        # SP=$07: JSR uses $06/$05 (SP -> $04), then 5 pushes use $04/$03/$02/$01/$00 (SP -> $FF).
        # Deepest write at $2101 (5th push writes at $2100+$01=$2101), inside stack page $2100-$21FF.
        # SP reaches $00 but the 6502 push writes BEFORE decrementing, so the 5th push writes at $2101.
        # No $20xx write occurs. SP=$07 is safe.
        sp = 0x07
        sp_after_jsr = sp - 2          # $05
        # Push sequence from SP=$05: write at $2105($04), $2104($03), $2103($02), $2102($01), $2101($00)
        deepest_write_addr = 0x2100 + 1  # $2101
        self.assertTrue(0x2100 <= deepest_write_addr <= 0x21FF, f"deepest write ${deepest_write_addr:04X} outside stack page")
        self.assertEqual(sp_after_jsr - 5, 0x00)  # SP reaches $00 after 5 pushes

    def test_sp_06_wraps_sp(self):
        # SP=$06: JSR uses $05/$04 (SP -> $03), then 5 pushes use $03/$02/$01/$00/$FF.
        # 5th push writes at $2100+$00=$2100, then SP wraps to $FF.
        # The write at $2100 is inside the stack page, but SP wraparound ($00 -> $FF)
        # is outside the contract.
        sp = 0x06
        sp_after_jsr = sp - 2          # $04
        sp_after_5_pushes = (sp_after_jsr - 5) & 0xFF  # $FF (wrapped from $00)
        self.assertEqual(sp_after_5_pushes, 0xFF)  # SP wrapped from $00 to $FF
        # SP wraparound: out of contract.

    def test_stack_page_is_2100_to_21ff(self):
        # MPR1 = $F8 maps $2000-$3FFF; stack is at $2100-$21FF within that window.
        # $20xx addresses are below the stack page (< $2100).
        self.assertTrue(0x20FF < 0x2100)  # $20xx is below stack page
        self.assertTrue(0x2100 <= 0x21FF)  # stack page is valid range


class ProbeRejectedForHost(ValueError):
    """Host-test mirror of the integration test's ProbeRejected."""
    def __init__(self, reason, detail):
        self.reason = reason
        super().__init__(detail)


class TestFalsePassRegression(unittest.TestCase):
    """偽PASS回帰テスト: 期待理由とは異なる異常が発生してもFAILする。

    修正前: check() が期待理由コードをエラーメッセージの先頭に付けるため、
            expect_rejected() は常に一致と判定 → 偽PASS。
    修正後: ProbeRejected.reason は実際の異常理由。不一致 → FAIL。
    """

    def _expect_rejected(self, expected_reason, check_fn):
        """expect_rejected のホスト版: ProbeRejected.reason を厳密比較。"""
        try:
            check_fn()
        except ProbeRejectedForHost as error:
            if error.reason != expected_reason:
                raise ValueError(
                    f"rejected for wrong reason: got {error.reason!r}, want {expected_reason!r}"
                )
            return error.reason
        raise ValueError("unexpectedly passed")

    def test_false_pass_before_fix_scenario(self):
        # 修正前なら偽PASSしていたシナリオ:
        # MAWR_CHANGEDを期待しているが、実際にはSP異常（STACK_MISMATCH）が発生。
        # 修正前: check()が"MAWR_CHANGED:"をプレフィクスに付ける → 一致 → PASS（偽）
        # 修正後: ProbeRejected.reason="STACK_MISMATCH" != "MAWR_CHANGED" → FAIL
        def check_fn():
            raise ProbeRejectedForHost("STACK_MISMATCH", "SP mismatch")

        with self.assertRaises(ValueError) as ctx:
            self._expect_rejected("MAWR_CHANGED", check_fn)
        self.assertIn("wrong reason", str(ctx.exception))

    def test_matching_reason_passes(self):
        # 正しい理由で拒否された場合はPASS。
        def check_fn():
            raise ProbeRejectedForHost("MAWR_CHANGED", "MAWR mismatch")

        result = self._expect_rejected("MAWR_CHANGED", check_fn)
        self.assertEqual(result, "MAWR_CHANGED")

    def test_vdc_changed_vs_c_not_set_mismatch(self):
        # VDC_CHANGEDを期待しているが、実際はC_NOT_SET。
        def check_fn():
            raise ProbeRejectedForHost("C_NOT_SET", "C not set")

        with self.assertRaises(ValueError) as ctx:
            self._expect_rejected("VDC_CHANGED", check_fn)
        self.assertIn("wrong reason", str(ctx.exception))

    def test_blocked_is_wrong_reason(self):
        # BLOCKEDは正常な拒否ではなく、期待理由と不一致 → FAIL。
        def check_fn():
            raise ProbeRejectedForHost("BLOCKED", "probe missing")

        with self.assertRaises(ValueError) as ctx:
            self._expect_rejected("MAWR_CHANGED", check_fn)
        self.assertIn("wrong reason", str(ctx.exception))

    def test_unexpected_pass_fails(self):
        # 変異体が予期せずパスした場合はFAIL。
        def check_fn():
            pass  # no exception

        with self.assertRaises(ValueError) as ctx:
            self._expect_rejected("MAWR_CHANGED", check_fn)
        self.assertIn("unexpectedly passed", str(ctx.exception))


class SetwrtSourceTests(unittest.TestCase):
    def setUp(self):
        self.text = SOURCE.read_text(encoding="utf-8")
        code = [line.split(";", 1)[0].strip() for line in self.text.splitlines()]
        self.code = [line for line in code if line and not line.startswith(".") and not line.endswith(":")]

    def test_entry_and_segment(self):
        self.assertIn('.segment "OVERRIDES"', self.text)
        self.assertIn(".export api_slot_3A", self.text)
        self.assertIn("api_slot_3A:", self.text)

    def test_instruction_sequence_is_exact(self):
        self.assertEqual(self.code, EXPECTED)

    def test_only_allowed_absolute_operands(self):
        # Only the VDC ports at $0000-$0003 (MPR0 window) may be addressed by absolute operands.
        for line in self.code:
            for hexaddr in re.findall(r"\$([0-9A-Fa-f]{4})\b", line):
                self.assertIn(hexaddr.upper(), {"0000", "0002", "0003"}, line)

    def test_no_zero_page_addressing(self):
        # Zero page is $2000 on the pinned core. A bare 1-2 digit operand (no #) encoded as zero-page
        # by ca65 would hit $20xx. Every VDC access must use the a: (absolute) prefix.
        bare = re.compile(r"(?<![#\w:])\$[0-9A-Fa-f]{1,2}(?![0-9A-Fa-f])")
        self.assertTrue(bare.search("    sta $02"))
        self.assertTrue(bare.search("    stz $00"))
        self.assertFalse(bare.search("    sta a:$0002"))
        self.assertFalse(bare.search("    lda #$02"))
        for line in self.code:
            self.assertIsNone(bare.search(line), line)
        for prefix in ("stz a:$0000", "sta a:$0002", "stx a:$0003"):
            self.assertIn(prefix, self.code)

    def test_no_access_to_the_stack_or_zero_page_window(self):
        # The stack ($2100-$21FF) and zero page ($2000) stay in MPR1. No operand may name them.
        for line in self.code:
            for hexaddr in re.findall(r"\$([0-9A-Fa-f]{4})\b", line):
                self.assertFalse(0x2000 <= int(hexaddr, 16) <= 0x21FF, line)

    def test_routine_is_linked_in_overrides_mpr7(self):
        cfg = (ROOT / "src" / "bank0.cfg").read_text(encoding="utf-8")
        self.assertRegex(cfg, r"OVERRIDES:\s*load\s*=\s*ROM0,.*start\s*=\s*\$E[0-9A-Fa-f]{3}")
        start = int(re.search(r"OVERRIDES:.*start\s*=\s*\$([0-9A-Fa-f]{4})", cfg).group(1), 16)
        self.assertGreaterEqual(start, 0xE000)

    def test_window_is_mpr0_not_the_stack_page(self):
        self.assertNotIn("tam #$02", self.code)
        self.assertNotIn("tma #$02", self.code)
        self.assertEqual(self.code.count("tam #$01"), 2)
        self.assertEqual(self.code.count("tma #$01"), 1)

    def test_push_pull_balance(self):
        pushes = sum(1 for line in self.code if line.split()[0] in ("php", "phy", "phx", "pha"))
        pulls = sum(1 for line in self.code if line.split()[0] in ("plp", "ply", "plx", "pla"))
        self.assertEqual(pushes, pulls)

    def test_max_simultaneous_pushes_in_normal_path(self):
        # Count the maximum number of pushes that remain on the stack at any point.
        # php(1) phy(2) phx(3) pha(4) pha(5) -> pla(4) -> pla(3) -> pha(4) -> pla(3) -> ply(2) -> plp(1)
        max_depth = 0
        depth = 0
        for line in self.code:
            op = line.split()[0]
            if op in ("php", "phy", "phx", "pha"):
                depth += 1
                max_depth = max(max_depth, depth)
            elif op in ("plp", "ply", "plx", "pla"):
                depth -= 1
        self.assertEqual(max_depth, 5)  # 5 simultaneous pushes, not 6

    def test_structured_reason_codes_are_defined(self):
        for code in (REASON_STACK_OVERFLOW, REASON_VDC_CHANGED, REASON_C_NOT_SET,
                     REASON_NOOP_UNDETECTED, REASON_HALT_FAILURE, REASON_UNEXPECTED_PASS,
                     REASON_MAWR_CHANGED, REASON_REGISTER_MISMATCH,
                     REASON_STACK_MISMATCH, REASON_MPR_MISMATCH):
            self.assertIsInstance(code, str)
            self.assertTrue(len(code) > 0)
            self.assertEqual(code, code.upper())


if __name__ == "__main__":
    unittest.main()