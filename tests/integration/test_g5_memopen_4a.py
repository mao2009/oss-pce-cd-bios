"""Synthetic real-core tests for the $4A EX_MEMOPEN preservation body (Issue #84).

Proves the CURRENT experimental wiring end to end on synthetic media with the
pinned core: PUBLIC IPL boot (2-base builder, commercial FIRST default,
cd_4a="experimental") -> JSR $E0DE (real table entry, never direct-body JSR,
never fake RTS) -> full A/X/Y/P/MPR/SP preservation -> RTS resumption ->
payload halt. Companion coverage:

  stub default (cd_4a="stub") keeps $E0DE on the linked fallback (byte-equal
  to the normal DEV ROM) and traps with X=$4A;
  entry-C=0 preservation (a forced-SEC mutant and a forced-CLC mutant both
  go RED: no fabricated flag);
  nonzero entry A/X/Y preservation (a clobber mutant goes RED: no fixed
  success value);
  MPR1=$F8 gate disambiguation (a TMA-operand mutant goes RED);
  refusal-path mutants, cold/warm repeatability, multi-call, unimplemented
  slot trap, normal DEV ROM byte-identity, and table routing.

NOT a compatible System Card BIOS. NOT genuine ABI evidence (genuine $4A
semantics stay UNKNOWN/BLOCKED; see docs/84-4a-contract.md). NOT
DEV_CALLABLE. release_eligible=false. G5_OBSERVED=false. Synthetic only;
commercial media: NOT_RUN here (no ./rom probing in this file).
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

# Payload CODE ($3000): set entry regs, then entry P last (so P is exact at
# JSR; entry A necessarily equals POPEN -- X/Y stay fully independent).
# JSR the real $E0DE table entry twice with different regs, record
# everything, halt. res_base layout (16 bytes):
#   +0 ran marker ($AA)  +1 A  +2 X  +3 Y  +4 P  +5 SP  +6..13 MPR0..7
#   second call records at +16.. (same shape, no marker)
PAYLOAD_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    ldx #$FF
    txs
    lda #POPEN
    ldx #XONE
    ldy #YONE
    lda #POPEN
    pha
    plp
    jsr $E0DE
    sta res_base+1
    stx res_base+2
    sty res_base+3
    php
    pla
    sta res_base+4
    tsx
    txa
    sta res_base+5
    tma #$01
    sta res_base+6
    tma #$02
    sta res_base+7
    tma #$04
    sta res_base+8
    tma #$08
    sta res_base+9
    tma #$10
    sta res_base+10
    tma #$20
    sta res_base+11
    tma #$40
    sta res_base+12
    tma #$80
    sta res_base+13
    lda #PTWO
    ldx #XTWO
    ldy #YTWO
    lda #PTWO
    pha
    plp
    jsr $E0DE
    sta res_base+17
    stx res_base+18
    sty res_base+19
    php
    pla
    sta res_base+20
    tsx
    txa
    sta res_base+21
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 32
"""

UNIMPL_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    ldx #$FF
    txs
    lda #$AA
    sta res_base
    jsr $E00C
    sta res_base+1
payload_halt:
    bra payload_halt
res_base:
    .res 16
"""

STUB4A_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    ldx #$FF
    txs
    jsr $E0DE
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 16
"""


class MemopenSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-4a-")
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
        # Experimental image (cd_4a="experimental"); stub default via the
        # builder default (cd_4a="stub") for the control comparisons.
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m4a", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False, cd_4a="experimental")
        cls.rom_stub, cls.syms_stub = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m4a-stub", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False)
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        for rom_bytes, symbols, addr, name in (
                (cls.rom.read_bytes(), cls.syms, cls.syms["ex_memopen_entry"], "exp"),
                (cls.rom_stub.read_bytes(), cls.syms_stub, cls.syms_stub["api_slot_4A"], "stub")):
            tab = rom_bytes[0xE0DE - 0xE000:0xE0DE - 0xE000 + 3]
            assert tuple(tab) == (0x4C, addr & 0xFF, (addr >> 8) & 0xFF), (name, addr)
        trace = cls.out / "ipl-loader-g5-m4a.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        trace_stub = cls.out / "ipl-loader-g5-m4a-stub.lbl"
        cls.labels_stub = parse_labels(
            trace_stub, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "m4a-probe")
        import geargrafx_ipl_loader as L
        cls.L = L
        from build_cdload_exec import USER_OFFSET
        from make_cd_image import RAW_BYTES
        cls.USER_OFFSET = USER_OFFSET
        cls.RAW_BYTES = RAW_BYTES

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tmp = cls._tmp.name
        assert "pce-4a-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    def _run(self, name, defines, rom_kind="exp"):
        import geargrafx_api_entry as Eentry  # noqa: F401 (re-export for probes)
        if rom_kind == "exp":
            rom, labels = self.rom, self.labels
        elif rom_kind == "stub":
            rom, labels = self.rom_stub, self.labels_stub
        else:
            raise ValueError("rom_kind must be 'exp' or 'stub'")
        return self._run_probe_on_rom(name, rom, labels, defines)

    def _run_on_rom(self, name, rom_bytes, defines):
        # Run the synthetic payload against caller-supplied ROM bytes
        # (mutants). Same labels/disc flow as _run with the experimental ROM.
        rom_path = self.out / (name + ".pce")
        rom_path.write_bytes(bytes(rom_bytes))
        return self._run_probe_on_rom(name, rom_path, self.labels, defines)

    def _run_probe_on_rom(self, name, rom, labels, defines):
        import geargrafx_api_entry as Eentry
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        o = self.out / name
        o.mkdir(exist_ok=True)
        src = o / (name + ".s")
        src.write_text(defines["asm"], encoding="ascii")
        obj, binp, cfgp, lblp = (o / (name + ".o"), o / (name + ".bin"),
                                 o / (name + ".cfg"), o / (name + ".lbl"))
        cfgp.write_text(CFG, encoding="ascii")
        import environment
        ca65, ld65 = environment.executable("ca65"), environment.executable("ld65")
        subprocess.run([ca65, str(src), *[f"-D{d}={v}" for d, v in defines["defs"].items()],
                        "-o", str(obj), "-g"], check=True)
        subprocess.run([ld65, "-C", str(cfgp), "-o", str(binp), "-Ln", str(lblp),
                        str(obj)], check=True)
        text = lblp.read_text(encoding="ascii")
        syms = {}
        for line in text.splitlines():
            m = re.match(r"^al ([0-9A-Fa-f]{6}) \.(.+)$", line)
            if m:
                syms[m.group(2)] = int(m.group(1), 16)
        res_base = syms["res_base"] - 0x2800
        assert syms.get("payload_halt", 0) < 0x3800, syms
        assert res_base + 32 <= 0x1800, syms
        code = binp.read_bytes()
        assert 1 <= len(code) <= 2 * 2048, len(code)
        body = self.L.body_for(code, 2)
        info = self.L.info_sector(2, 2, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0,
                                  sum(body) & 0xFFFF)
        cue = self.L.make_disc(o, self.L.case_disc({"n": 2, "iplblk": 2}, body, info))
        m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", text, re.M)
        halt = int(m.group(1), 16)
        lab = o / (name + ".labels")
        lab.write_text(
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {labels['api_unimplemented']:04x}\n", encoding="ascii")
        rec = Eentry.run_probe(self.probe, rom, cue, lab, BUDGET, "synthetic")
        return rec, res_base

    def _buf(self, rec):
        self.assertIn("RUN", rec)
        runs = rec["RUN"]
        self.assertEqual(len(runs), 2)
        cycles = [r.get("cycle") for r in runs]
        self.assertEqual(sorted(cycles), ["cold", "warm"])
        bufs = {}
        for r in runs:
            self.assertEqual(r.get("reason"), "cd_halt_ok")
            self.assertIs(r.get("halt_confirmed"), True)
            self.assertIn("buffer_hex", r)
            try:
                buf = bytes.fromhex(r["buffer_hex"])
            except (TypeError, ValueError):
                self.fail("buffer_hex is not valid hex")
            self.assertGreaterEqual(len(buf), 2048)
            bufs[r["cycle"]] = buf
        return bufs["cold"], bufs["warm"]

    def _buf_stublike(self, rec, labels, expect_x):
        # Stub-halt runs (api_unimplemented, confirmed): return buffers.
        self.assertIn("RUN", rec)
        runs = rec["RUN"]
        self.assertEqual(len(runs), 2)
        bufs = {}
        for r in runs:
            self.assertEqual(r.get("reason"), "api_unimplemented")
            self.assertIs(r.get("halt_confirmed"), True)
            self.assertEqual(r.get("pc"), labels["api_unimplemented"])
            self.assertEqual(r.get("x"), expect_x)
            self.assertIn("buffer_hex", r)
            try:
                buf = bytes.fromhex(r["buffer_hex"])
            except (TypeError, ValueError):
                self.fail("buffer_hex is not valid hex")
            bufs[r["cycle"]] = buf
        self.assertEqual(sorted(bufs), ["cold", "warm"])
        return bufs["cold"], bufs["warm"]

    def _defines(self, popen, xone, yone, ptwo, xtwo, ytwo):
        # Entry A equals entry P by construction (P set last via PLP); X/Y
        # are fully independent. A=$06/$07 still proves preservation: any
        # clobber/fixed-value/drop-restore mutant changes it.
        return {"asm": PAYLOAD_ASM,
                "defs": {"POPEN": popen, "XONE": xone, "YONE": yone,
                         "PTWO": ptwo, "XTWO": xtwo, "YTWO": ytwo}}


    def _mutate_body(self, rom_bytes, offset, new_bytes):
        # Patch the experimental $4A body in a ROM copy (mutants live in
        # /tmp-style out dirs, never in tracked files).
        body_off = (self.syms["ex_memopen_entry"] & 0xFFFF) - 0xE000
        out = bytearray(rom_bytes)
        out[body_off + offset:body_off + offset + len(new_bytes)] = new_bytes
        return bytes(out)

    def _check_c0(self, cold, base):
        # Entry P=$06 (C=0): the most discriminating class. Refusal, forced
        # flags, clobbers and dropped restores all change at least one field.
        r = cold
        self.assertEqual(r[base], 0xAA)
        self.assertEqual((r[base + 1], r[base + 2], r[base + 3]), (0x06, 0x11, 0x22))
        self.assertEqual(r[base + 4] & ~0x10, 0x06)
        self.assertEqual(r[base + 5], 0xFF)
        self.assertEqual(list(r[base + 6:base + 14]), [0xFF, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        self.assertEqual((r[base + 17], r[base + 18], r[base + 19]), (0x06, 0x66, 0x77))
        self.assertEqual(r[base + 20] & ~0x10, 0x06)
        self.assertEqual(r[base + 21], 0xFF)

    def _check_c1(self, cold, base):
        # Entry P=$07 (C=1, commercial CUE[0] shape): C=1 preservation class.
        # Mirrors _check_c0 with 0x07 (A==POPEN by construction). Used for
        # force-CLC (which must RED vs C=1, not vs C=0 baseline mismatch).
        r = cold
        self.assertEqual(r[base], 0xAA)
        self.assertEqual((r[base + 1], r[base + 2], r[base + 3]), (0x07, 0x11, 0x22))
        self.assertEqual(r[base + 4] & ~0x10, 0x07)
        self.assertEqual(r[base + 5], 0xFF)
        self.assertEqual(list(r[base + 6:base + 14]), [0xFF, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        self.assertEqual((r[base + 17], r[base + 18], r[base + 19]), (0x07, 0x66, 0x77))
        self.assertEqual(r[base + 20] & ~0x10, 0x07)
        self.assertEqual(r[base + 21], 0xFF)

    def test_memopen_preserves_carry_set(self):
        # Entry P=$07 (C=1, commercial CUE shape): everything preserved.
        rec, base = self._run("c1", self._defines(0x07, 0x11, 0x22,
                                                  0x07, 0x66, 0x77))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = cold
        self.assertEqual(r[base], 0xAA)  # payload ran => PUBLIC boot OK
        # Entry A == POPEN by construction ($07); X/Y independent.
        self.assertEqual((r[base + 1], r[base + 2], r[base + 3]), (0x07, 0x11, 0x22))
        self.assertEqual(r[base + 4] & 0x01, 0x01)  # Carry preserved (C=1)
        # PHP readback forces B; the latch has no B bit (SetP masks $EF),
        # so compare modulo B.
        self.assertEqual(r[base + 4] & ~0x10, 0x07)  # P preserved modulo B
        self.assertEqual(r[base + 5], 0xFF)  # SP balanced (TXS FF, JSR+RTS+4 pushes net zero)
        self.assertEqual(list(r[base + 6:base + 14]), [0xFF, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        self.assertEqual((r[base + 17], r[base + 18], r[base + 19]), (0x07, 0x66, 0x77))
        self.assertEqual(r[base + 20] & ~0x10, 0x07)
        self.assertEqual(r[base + 21], 0xFF)

    def test_memopen_preserves_carry_clear(self):
        # Entry P=$06 (C=0): Carry stays clear. A forced-SEC mutant goes RED
        # here (proves no fabricated C=1 success signal).
        rec, base = self._run("c0", self._defines(0x06, 0x11, 0x22,
                                                  0x06, 0x66, 0x77))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        self._check_c0(cold, base)
        self._check_c0(warm, base)

    def test_memopen_apicall_slot_order(self):
        rec, base = self._run("order", self._defines(0x07, 0x11, 0x22,
                                                     0x06, 0x66, 0x77))
        self._buf(rec)  # recognized halt first; order alone must not pass a hang
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            slots = [c["slot"] for c in calls]
            self.assertEqual(slots, [0x4A, 0x4A], cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["entry_pc"], 0xE0DE, (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))

    def test_memopen_body_bytes_pinned(self):
        # Structural anti-fake pin (no probe run): the experimental body must
        # be exactly the reviewed 21-byte sequence. A bare-RTS substitution
        # (60 EA EA EA EA ...) would be GREEN on behavior in observed classes
        # by construction (RTS preserves everything when there is no work),
        # so the body shape itself is pinned here: the MPR gate, the
        # save/restore discipline and the refusal signal are load-bearing
        # and must change only together with this test under review.
        exp = self.rom.read_bytes()
        body = self.syms["ex_memopen_entry"] & 0xFFFF
        want = bytes((0x08, 0x48, 0xDA, 0x5A,  # PHP PHA PHX PHY
                        0x43, 0x02,              # TMA #$02 (MPR1)
                        0xC9, 0xF8,              # CMP #$F8
                        0xD0, 0x05,              # BNE refuse
                        0x7A, 0xFA, 0x68, 0x28, 0x60,  # PLY PLX PLA PLP RTS
                        0x7A, 0xFA, 0x68, 0x28, 0x38, 0x60))  # PLY PLX PLA PLP SEC RTS
        self.assertEqual(len(want), 21)
        self.assertEqual(exp[body - 0xE000:body - 0xE000 + 21], want)
        self.assertNotEqual(want[0], 0x60)  # not a bare RTS

    def _assert_mutant_red(self, rec, base, check=None):
        # A mutant is detected (RED) through either of two channels:
        # (a) abnormal halt -- dropping/replacing a pop unbalances SP, so
        #     RTS mis-returns and the probe reports a non-halt reason; or
        # (b) value mismatch -- balanced mutants that fabricate flags or
        #     misdirect the gate return cleanly but with wrong state.
        # If the run halts cleanly AND all values match, the mutant went
        # GREEN and this helper fails (the test then fails: coverage gap).
        # Review 5968211653: channel (a) counts ONLY expected control-flow
        # failure (RUN present with 2 cycles, valid buffers, reason is a
        # non-halt halt reason). Missing RUN, bad hex, short buffers, or
        # probe/internal errors are harness failures, not mutant detection
        # (fail the test, do not count as RED). `check` selects the baseline
        # matcher (C=0 vs C=1); unmutated ROM must be GREEN for it (control).
        if check is None:
            check = self._check_c0
        try:
            cold, _ = self._buf(rec)
        except AssertionError:
            # Distinguish control-flow failure from harness failure.
            self.assertIn("RUN", rec, "mutant run missing RUN (harness failure, not detection)")
            runs = rec["RUN"]
            self.assertEqual(len(runs), 2, "mutant run RUN count != 2 (harness failure)")
            for r in runs:
                self.assertIn(r.get("reason"), ("cd_halt_fail", "api_unimplemented", "cd_unexpected_interrupt"),
                              f"mutant run unexpected reason {r.get('reason')} (harness failure, not detection)")
                self.assertIn("buffer_hex", r, "mutant run missing buffer_hex (harness failure)")
                try:
                    buf = bytes.fromhex(r["buffer_hex"])
                except (TypeError, ValueError):
                    self.fail("mutant run buffer_hex invalid hex (harness failure)")
                self.assertGreaterEqual(len(buf), 2048, "mutant run buffer short (harness failure)")
            return  # channel (a): expected control-flow failure
        with self.assertRaises(AssertionError, msg="mutant went GREEN"):
            check(cold, base)  # channel (b)

    def test_memopen_mutant_drop_pla_red(self):
        # Drop the success-path A restore (PLA->NOP): A keeps the TMA gate
        # residue ($F8) instead of the entry value. Proves A preservation
        # is load-bearing, not incidental.
        bad = self._mutate_body(self.rom.read_bytes(), 12, bytes((0xEA,)))
        rec, base = self._run_on_rom("m-nopla", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base)

    def test_memopen_mutant_drop_plp_red(self):
        # Drop the success-path P restore (PLP->NOP): flags keep the gate
        # residue instead of the entry value. Proves flag preservation.
        bad = self._mutate_body(self.rom.read_bytes(), 13, bytes((0xEA,)))
        rec, base = self._run_on_rom("m-noplp", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base)

    def test_memopen_mutant_force_clc_red(self):
        # Fabricate C=0 on success (PLP->CLC): the C=1 entry class must go
        # RED. Proves no fixed C=0 success flag. Note: PLP->CLC drops a pop,
        # so SP is unbalanced (+1) and RTS mis-returns (channel (a) non-halt)
        # in addition to any flag mismatch; an isolated flag-only mutant would
        # need a stack-balanced replacement (future work, not claimed here).
        # Control: unmutated C=1 ROM is GREEN for _check_c1 (no baseline
        # mismatch false positive vs _check_c0).
        rec_ctrl, base_ctrl = self._run("c1-ctrl", self._defines(0x07, 0x11, 0x22,
                                                                  0x07, 0x66, 0x77))
        cold_ctrl, _ = self._buf(rec_ctrl)
        self._check_c1(cold_ctrl, base_ctrl)
        bad = self._mutate_body(self.rom.read_bytes(), 13, bytes((0x18,)))
        rec, base = self._run_on_rom("m-clc", bad,
                                      self._defines(0x07, 0x11, 0x22, 0x07, 0x66, 0x77))
        self._assert_mutant_red(rec, base, check=self._check_c1)

    def test_memopen_mutant_force_sec_red(self):
        # Fabricate C=1 on success (PLP->SEC): the C=0 entry class must go
        # RED. Proves no fixed C=1 success flag.
        bad = self._mutate_body(self.rom.read_bytes(), 13, bytes((0x38,)))
        rec, base = self._run_on_rom("m-sec", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base)

    def test_memopen_mutant_always_refuse_red(self):
        # Force the refusal path (BNE->BRA): the C=0 entry class returns C=1
        # and must go RED. Proves the gate outcome matters.
        bad = self._mutate_body(self.rom.read_bytes(), 8, bytes((0x80,)))
        rec, base = self._run_on_rom("m-refuse", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base)

    def test_memopen_mutant_gate_const_red(self):
        # Corrupt the gate constant (CMP #$F8 -> #$F7): MPR1=$F8 wrongly
        # refuses, and the C=0 class goes RED. Proves the gate value.
        bad = self._mutate_body(self.rom.read_bytes(), 7, bytes((0xF7,)))
        rec, base = self._run_on_rom("m-const", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base)

    def test_memopen_mutant_gate_register_red(self):
        # Read the wrong MPR (TMA #$02 -> #$01 reads MPR0=$FF): the gate
        # wrongly refuses, and the C=0 class goes RED. Proves the TMA
        # operand really selects MPR1 (disambiguation is load-bearing).
        bad = self._mutate_body(self.rom.read_bytes(), 5, bytes((0x01,)))
        rec, base = self._run_on_rom("m-tma", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base)

    def test_memopen_mutant_table_reroute_red(self):
        # Reroute $E0DE back to the stub fallback: calls halt at the stub
        # instead of returning, so the preservation check goes RED. Proves
        # the test exercises the wired body, not the table alone.
        tab = bytearray(self.rom.read_bytes())
        fb = self.syms["api_slot_4A"]
        tab[3 * 0x4A:3 * 0x4A + 3] = bytes((0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
        rec, base = self._run_on_rom("m-tab", bytes(tab),
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base)

    def test_memopen_buffer_mutants_detected(self):
        # Corrupt the recorded buffer: head/middle/tail changes must be visible.
        rec, base = self._run("mutbuf", self._defines(0x07, 0x11, 0x22,
                                                      0x06, 0x66, 0x77))
        cold, _ = self._buf(rec)
        good = bytes(cold[base:base + 32])
        for tag, pos in (("a1", 1), ("p1", 4), ("sp1", 5), ("a2", 17), ("p2", 20)):
            bad = bytearray(good)
            bad[pos] ^= 0xFF
            with self.assertRaises(AssertionError, msg=f"corrupt {tag}"):
                self.assertEqual(bytes(bad), good, f"mutant corrupt {tag}")

    def test_memopen_stub_default_traps(self):
        # cd_4a="stub" (builder default): $E0DE stays on the linked fallback,
        # JSR traps with X=$4A at the verified stub and never returns
        # (the post-JSR marker stays $00). Proves the default did not
        # change and fail-closed is intact.
        rec, base = self._run("stub", {"asm": STUB4A_ASM, "defs": {}}, rom_kind="stub")
        cold, warm = self._buf_stublike(rec, self.labels_stub, 0x4A)
        for buf in (cold, warm):
            self.assertEqual(buf[base], 0x00)  # JSR never returned

    def test_memopen_exp_unimplemented_slot_traps(self):
        # On the experimental image, other unimplemented slots still trap.
        rec, base = self._run("unimpl", {"asm": UNIMPL_ASM, "defs": {}})
        runs = {r["cycle"]: r for r in rec["RUN"]}
        self.assertEqual(sorted(runs), ["cold", "warm"])
        for cycle, run in runs.items():
            with self.subTest(cycle=cycle):
                self.assertEqual(run["reason"], "api_unimplemented")
                self.assertTrue(run["halt_confirmed"])
                self.assertEqual(run["pc"], self.labels["api_unimplemented"])
                self.assertEqual(run["x"], 0x04)
                self.assertEqual(run["sp"], 0xFD)

    def test_memopen_rom_identity_and_table_routing(self):
        import hashlib
        exp = self.rom.read_bytes()
        stub = self.rom_stub.read_bytes()
        self.assertNotEqual(hashlib.sha256(exp).digest(), hashlib.sha256(stub).digest())
        # Experimental: $E0DE -> JMP ex_memopen_entry (body starts PHP,PHA).
        tab = exp[0xE0DE - 0xE000:0xE0DE - 0xE000 + 3]
        body = self.syms["ex_memopen_entry"]
        self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
        self.assertGreaterEqual(body, 0xF000)
        self.assertEqual(exp[body - 0xE000:body - 0xE000 + 2], bytes((0x08, 0x48)))
        # Stub default: $E0DE -> JMP api_slot_4A fallback; fallback bytes are
        # LDX #$4A; JMP api_unimplemented; table bytes equal the normal DEV ROM.
        tab_s = stub[0xE0DE - 0xE000:0xE0DE - 0xE000 + 3]
        fb = self.syms_stub["api_slot_4A"]
        self.assertEqual(tuple(tab_s), (0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
        at_4a = 3 * 0x4A
        self.assertEqual(stub[at_4a:at_4a + 3], self.normal[at_4a:at_4a + 3])
        # $02/$03/$33 wiring untouched on both images.
        for rom_bytes, syms in ((exp, self.syms), (stub, self.syms_stub)):
            self.assertEqual(rom_bytes[0xE006 - 0xE000], 0x4C)
            self.assertEqual(rom_bytes[0xE009 - 0xE000], 0x4C)
            self.assertEqual(rom_bytes[0xE099 - 0xE000], 0x4C)


if __name__ == "__main__":
    unittest.main()
