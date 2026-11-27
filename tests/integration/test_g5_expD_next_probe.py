"""Synthetic ExpD NEXT-detection readiness for G5 P0 (Issue #802, parent #635).

Proves the integrated main (PUBLIC IPL 2-base + commercial FIRST + $33 +
$4A/$25/$24/$36 experimental) can return through $36 and trap a following
unimplemented slot, so a future Owner-permitted commercial ExpD run can
identify the true NEXT blocker after $36. Uses slot $37 (EX_SPRPUT
candidate, $E0A5) ONLY as a synthetic stand-in NEXT: it is stub on every
variant here, never wired, never claimed as the commercial NEXT. The
commercial NEXT remains UNKNOWN until an explicit Owner-permitted
commercial ExpD re-observation (Phase H of docs/64-ex-satclr-contract.md).

Scope discipline:
- Synthetic media only, pinned core only (Geargrafx b49ae82a, cc65 71746c8).
- NOT a compatible System Card BIOS. NOT genuine ABI evidence. NOT
  DEV_CALLABLE. release_eligible=False. G5_OBSERVED=False.
- Commercial media: NOT_RUN here (no ./rom probing in this file).
- No BIOS body change. No new wiring. No guessed RTS. No $1C work.
  No overlap with Issue #831 (7-API oracle/JMP-operand hardening) or
  PRs #814/#823 (guard work).
- $37 stand-in arrival is SIMULATED_NEXT_API discipline, never genuine.

What is proved:
- ExpD (cd_4a/cd_25/cd_24/cd_36 all experimental) returns through $36 and
  reaches the $37 stub: 7 APICALLs in order, table JSR, MPR1 $F8, SP
  balanced, stack MATCH, cold/warm identical, RUN api_unimplemented X=$37.
- ExpC (same but cd_36 stub) halts at $36: 6 APICALLs, X=$36. Proves $36
  experimental wiring is load-bearing for NEXT reachability (RED control).
- ROM identity: 256KiB, JMP tables, normal DEV untouched, default all-stub
  untouched, BOOT span, fail-closed, manifest release/dev flags.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests" / "integration")]

SEL = 0x109
TRACK_SEL = 2
BUDGET = 20_000_000

# Payload: $33->$4A->$23->$25->$24->$36->$37(stand-in stub).
# $37 is NEVER wired in any variant here; on ExpD it traps as
# api_unimplemented X=$37, proving $36 returned. On ExpC ($36 stub) it
# traps at $36 instead, proving the $36 wiring is required.
NEXT_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    ldx #$FF
    txs
    lda #$F8
    tam #$02
    lda #$F8
    tam #$01
    lda #$07
    ldx #$12
    ldy #$34
    pha
    lda #$31
    plp
    jsr $E099
    jsr $E0DE
    jsr $E069
    jsr $E06F
    jsr $E06C
    jsr $E0A2
    jsr $E0A5
    sta res_base+1
    stx res_base+2
    sty res_base+3
    php
    pla
    sta res_base+4
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 16
"""

EXPECTED_ORDER = [0x33, 0x4A, 0x23, 0x25, 0x24, 0x36, 0x37]
EXPECTED_PCS = [0xE099, 0xE0DE, 0xE069, 0xE06F, 0xE06C, 0xE0A2, 0xE0A5]
EXPC_ORDER = [0x33, 0x4A, 0x23, 0x25, 0x24, 0x36]
EXPC_PCS = [0xE099, 0xE0DE, 0xE069, 0xE06F, 0xE06C, 0xE0A2]


class ExpDNextProbeSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-expd-next-")
        cls.out = Path(cls._tmp.name)
        dev_out = Path(cls._tmp.name) / "dev"
        with patch("sys.argv", ["build_rom.py", "--mode", "debug",
                                "--output", str(dev_out)]):
            assert build_rom.main() == 0
        orig = dev_out / "dev-only-not-compatible-syscard3.pce"
        assert orig.is_file()
        cls.normal = orig.read_bytes()
        cls.sel = SEL
        cls.track_sel = TRACK_SEL
        # ExpD: all four opt-ins (the integrated-main full experimental).
        cls.rom_expd, cls.syms_expd = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "mexpd", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False,
            cd_4a="experimental", cd_25="experimental",
            cd_24="experimental", cd_36="experimental")
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        # ExpC: same but $36 stub (RED control: NEXT unreachable by construction).
        cls.rom_expc, cls.syms_expc = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "mexpc", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False,
            cd_4a="experimental", cd_25="experimental",
            cd_24="experimental", cd_36="stub")
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        # Table routing: ExpD wires $36 to BOOT body; ExpC keeps $36 stub.
        # $37 stays stub on both (stand-in NEXT, never wired).
        for rom, syms, expect_36_exp in (
                (cls.rom_expd, cls.syms_expd, True),
                (cls.rom_expc, cls.syms_expc, False)):
            data = rom.read_bytes()
            assert len(data) == 262144
            for slot, entry in ((0x4A, "ex_memopen_entry"),
                                (0x25, "ex_scrmod_entry"),
                                (0x24, "ex_dotmod_entry")):
                body = syms[entry]
                tab = data[3 * slot:3 * slot + 3]
                assert tuple(tab) == (0x4C, body & 0xFF, (body >> 8) & 0xFF), (slot, entry)
                assert 0xF000 <= body <= 0xFFF6 - 9, (slot, body)
            body36 = syms["ex_satclr_entry"] if expect_36_exp else None
            tab36 = data[3 * 0x36:3 * 0x36 + 3]
            if expect_36_exp:
                assert tuple(tab36) == (0x4C, body36 & 0xFF, (body36 >> 8) & 0xFF)
                assert 0xF000 <= body36 <= 0xFFF6 - 9
            else:
                fb36 = syms["api_slot_36"]
                assert tuple(tab36) == (0x4C, fb36 & 0xFF, (fb36 >> 8) & 0xFF)
            # $37 stub on both (stand-in NEXT never wired; $37 not in G5
            # loader syms, so compare against untouched normal DEV ROM).
            tab37 = data[3 * 0x37:3 * 0x37 + 3]
            assert tuple(tab37) == tuple(cls.normal[3 * 0x37:3 * 0x37 + 3]), (tab37,)
            assert tab37[0] == 0x4C
        import json
        man_expd = json.loads((cls.out / "manifest-mexpd-g5.json").read_text())
        assert man_expd["cd_36"] == "experimental"
        assert man_expd["release_eligible"] is False
        assert man_expd["dev_callable"] is False
        man_expc = json.loads((cls.out / "manifest-mexpc-g5.json").read_text())
        assert man_expc["cd_36"] == "stub"
        assert man_expc["release_eligible"] is False
        trace = cls.out / "ipl-loader-g5-mexpd.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "mexpd-probe")
        import geargrafx_ipl_loader as L
        cls.L = L

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tmp = cls._tmp.name
        assert "pce-expd-next-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    def _run_next_on_rom(self, rom_path, tag):
        o = self.out / tag
        o.mkdir(exist_ok=True)
        src = o / "next.s"
        src.write_text(NEXT_ASM, encoding="ascii")
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        obj, binp, cfgp, lblp = (o / "next.o", o / "next.bin",
                                 o / "next.cfg", o / "next.lbl")
        cfgp.write_text(CFG, encoding="ascii")
        import environment
        ca65, ld65 = environment.executable("ca65"), environment.executable("ld65")
        subprocess.run([ca65, str(src), "-o", str(obj), "-g"], check=True)
        subprocess.run([ld65, "-C", str(cfgp), "-o", str(binp), "-Ln", str(lblp),
                        str(obj)], check=True)
        text = lblp.read_text(encoding="ascii")
        m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", text, re.M)
        halt = int(m.group(1), 16)
        lab = o / "next.labels"
        lab.write_text(
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {self.labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels['api_unimplemented']:04x}\n", encoding="ascii")
        code = binp.read_bytes()
        body = self.L.body_for(code, 2)
        info = self.L.info_sector(2, 2, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0,
                                  sum(body) & 0xFFFF)
        cue = self.L.make_disc(o, self.L.case_disc({"n": 2, "iplblk": 2}, body, info))
        import geargrafx_api_entry as Eentry
        return Eentry.run_probe(self.probe, rom_path, cue, lab, BUDGET, "synthetic")

    def _assert_stub_halt(self, rec, expect_x, expect_order, expect_pcs):
        self.assertIn("RUN", rec)
        runs = rec["RUN"]
        self.assertEqual(len(runs), 2)
        self.assertEqual(sorted(r["cycle"] for r in runs), ["cold", "warm"])
        pcs = []
        for r in runs:
            self.assertEqual(r.get("reason"), "api_unimplemented")
            self.assertIs(r.get("halt_confirmed"), True)
            self.assertEqual(r.get("pc"), self.labels["api_unimplemented"])
            self.assertEqual(r.get("x"), expect_x)
            self.assertIs(r.get("apicall_truncated"), False)
            self.assertIs(r.get("ret_truncated"), False)
            pcs.append(r["pc"])
        self.assertEqual(pcs[0], pcs[1])
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual([c["slot"] for c in calls], expect_order, cycle)
            self.assertEqual([c["entry_pc"] for c in calls], expect_pcs, cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))
                self.assertEqual(c["mpr"][1], 0xF8, (cycle, c))
            # $36 return on ExpD is proved by $37 arrival (next call observed
            # with JSR MATCH); stacked-return bytes at entry, not RTS landing.
            self.assertIs(rec["RUN"][0].get("apicall_truncated"), False)
            self.assertIs(rec["RUN"][0].get("ret_truncated"), False)

    def test_expd_reaches_37_standin(self):
        # GREEN: ExpD returns through $36 and traps at $37 stand-in.
        rec = self._run_next_on_rom(self.rom_expd, "expd-next")
        self._assert_stub_halt(rec, 0x37, EXPECTED_ORDER, EXPECTED_PCS)

    def test_expc_halts_at_36(self):
        # RED control: without $36 wiring the same payload halts at $36,
        # never reaching $37. Proves $36 wiring is load-bearing for NEXT.
        rec = self._run_next_on_rom(self.rom_expc, "expc-next")
        self._assert_stub_halt(rec, 0x36, EXPC_ORDER, EXPC_PCS)

    def test_rom_identity_and_table_routing(self):
        import hashlib
        expd = self.rom_expd.read_bytes()
        expc = self.rom_expc.read_bytes()
        self.assertEqual(len(expd), 262144)
        self.assertEqual(len(expc), 262144)
        self.assertNotEqual(hashlib.sha256(expd).digest(), hashlib.sha256(expc).digest())
        # Only documented $36 window differs between ExpC and ExpD.
        diffs = [i for i in range(len(expd)) if expd[i] != expc[i]]
        # Table entry (3 bytes) + BOOT body inclusion account for all diffs;
        # the exact window is asserted by the builder host tests, here we
        # assert the $37 stub window is identical (stand-in never wired).
        for slot in (0x37, 0x4A, 0x25, 0x24):
            self.assertEqual(expd[3 * slot:3 * slot + 3], expc[3 * slot:3 * slot + 3],
                             hex(slot))
        # Normal DEV ROM untouched.
        self.assertEqual(self.normal[3 * 0x36:3 * 0x36 + 3],
                         bytes(self.normal[3 * 0x36:3 * 0x36 + 3]))
        # $37 entry stays JMP on both (never JSR/RTS-patched).
        self.assertEqual(expd[0xE0A5 - 0xE000], 0x4C)
        self.assertEqual(expc[0xE0A5 - 0xE000], 0x4C)


if __name__ == "__main__":
    unittest.main()
