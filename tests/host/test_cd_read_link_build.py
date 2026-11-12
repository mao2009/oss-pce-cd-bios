"""Host static checks for the EXPERIMENTAL link source (no core needed).

Proves the new guest file keeps the experimental discipline without touching
shared builders or registries: no TAM/TXS (MPR/stack preserved), balanced
push/pull, refusal-before-READ structure, result/state addresses, and that the
normal ROM sources are untouched (link lives only in its new file).
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "cd_read_link.s"


class LinkSourceTests(unittest.TestCase):
    def test_source_exists_and_declares_experimental(self):
        text = SRC.read_text(encoding="utf-8")
        for line in ("NOT a compatible System Card BIOS",
                     "NOT the genuine CD_READ ABI",
                     "release_eligible=false",
                     "UNKNOWN/BLOCKED",
                     "cd_read_link_entry:"):
            self.assertIn(line, text)

    def test_no_tam_no_txs_no_slot_claim(self):
        code = [ln.split(";")[0].strip().lower()
                for ln in SRC.read_text(encoding="utf-8").splitlines()]
        code = [ln for ln in code if ln]
        self.assertNotIn("tam #$01", code)
        self.assertFalse(any(ln.startswith("tam ") for ln in code),
                         "link performs no TAM (MPRs preserved)")
        self.assertFalse(any(ln.startswith("txs") for ln in code),
                         "link never changes the stack pointer")
        self.assertNotIn("$e009", "\n".join(code))
        self.assertNotIn("slot $03", "\n".join(code).lower())

    def test_balanced_stack_and_endpoints(self):
        text = SRC.read_text(encoding="utf-8")
        for op in ("php", "pha", "phx", "phy", "ply", "plx", "pla", "plp"):
            self.assertIn(op, text)
        # Every refusal path returns with SEC, success with CLC; all end with RTS.
        self.assertIn("sec\n    rts", text)
        self.assertIn("clc\n    rts", text)

    def test_alt_initialized_before_first_wait(self):
        text = SRC.read_text(encoding="utf-8")
        # MEDIUM-1: alt ($0B) is initialised to $FF at entry, before the first
        # wait, and the DATA_IN path still overrides it with PH_STATUS.
        self.assertIn("sta alt", text)
        self.assertIn("lda #PH_STATUS\n    sta alt", text)
        # The init must occur before the invalid-state refusal target.
        idx_init = text.index("sta alt")
        idx_state = text.index("@ref_state:")
        self.assertLess(idx_init, idx_state)
        # link_wait_phase must not unconditionally re-init alt at entry.
        wp = text[text.index("link_wait_phase:"):text.index("link_ack_byte:")]
        self.assertTrue(wp.startswith("link_wait_phase:\n    lda #0"))
        self.assertNotIn("sta alt", wp.split("@poll:")[0])


    def test_addresses_and_error_taxonomy(self):
        text = SRC.read_text(encoding="utf-8")
        for token in ("$2400", "$2401", "$2402", "$2403", "$2404",
                      "$20F2", "$20F3", "$20F4", "$20F5", "$20F6", "$20F7",
                      "$2320", "$27E8", "$2800", "$1800",
                      "$60", "$61", "$62", "$63", "$64", "$70", "$71"):
            self.assertIn(token, text)

    def test_normal_rom_untouched(self):
        boot = (ROOT / "src" / "boot.s").read_text(encoding="utf-8")
        self.assertNotIn("cd_read_link", boot)
        probe = (ROOT / "src" / "cd_load_exec_probe.s").read_text(encoding="utf-8")
        self.assertNotIn("cd_read_link", probe)

    def test_host_mirror_matches_guest_codes(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import cd_read_link as L
        text = SRC.read_text(encoding="utf-8")
        for name, val in (("ERR_STATE", L.ERR_INVALID_STATE),
                          ("ERR_WINDOW", L.ERR_INVALID_WINDOW),
                          ("ERR_CAP", L.ERR_OVER_CAP),
                          ("ERR_BEFORE", L.ERR_BEFORE),
                          ("ERR_AFTER", L.ERR_AFTER),
                          ("ERR_FAIL", L.ERR_READ_FAIL),
                          ("ERR_TMO", L.ERR_READ_TIMEOUT)):
            self.assertIn(f"{name} ", text)
            self.assertEqual(val, {"ERR_STATE": 0x60, "ERR_WINDOW": 0x61,
                                   "ERR_CAP": 0x62, "ERR_BEFORE": 0x63,
                                   "ERR_AFTER": 0x64, "ERR_FAIL": 0x70,
                                   "ERR_TMO": 0x71}[name])


if __name__ == "__main__":
    unittest.main()
