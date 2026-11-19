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
        # Strict clean-halt validator (review 5968681873): cold/warm each 1,
        # all required fields present, halt confirmed, same PC both cycles,
        # valid 2048B+ buffers, no truncated traces. Any deviation is a
        # harness failure (AssertionError), never a silent pass.
        self.assertIn("RUN", rec)
        runs = rec["RUN"]
        self.assertIsInstance(runs, list)
        self.assertEqual(len(runs), 2)
        cycles = [r.get("cycle") for r in runs]
        self.assertEqual(sorted(cycles), ["cold", "warm"])
        bufs = {}
        pcs = []
        for r in runs:
            self.assertIn("cycle", r)
            self.assertIn("reason", r)
            self.assertIn("pc", r)
            self.assertIn("halt_confirmed", r)
            self.assertIn("buffer_hex", r)
            self.assertIn("apicall_truncated", r)
            self.assertIn("ret_truncated", r)
            self.assertEqual(r.get("reason"), "cd_halt_ok")
            self.assertIs(r.get("halt_confirmed"), True)
            self.assertIsInstance(r.get("pc"), int)
            self.assertIs(r.get("apicall_truncated"), False)
            self.assertIs(r.get("ret_truncated"), False)
            try:
                buf = bytes.fromhex(r["buffer_hex"])
            except (TypeError, ValueError):
                self.fail("buffer_hex is not valid hex")
            self.assertGreaterEqual(len(buf), 2048)
            bufs[r["cycle"]] = buf
            pcs.append(r["pc"])
        self.assertEqual(pcs[0], pcs[1], "clean-halt cold/warm pc differ")
        return bufs["cold"], bufs["warm"]

    def _buf_stublike(self, rec, labels, expect_x):
        # Stub-halt runs (api_unimplemented, confirmed): return buffers.
        self.assertIn("RUN", rec)
        runs = rec["RUN"]
        self.assertIsInstance(runs, list)
        self.assertEqual(len(runs), 2)
        cycles = [r.get("cycle") for r in runs]
        self.assertEqual(sorted(cycles), ["cold", "warm"])
        bufs = {}
        for r in runs:
            self.assertIn("apicall_truncated", r)
            self.assertIn("ret_truncated", r)
            self.assertEqual(r.get("reason"), "api_unimplemented")
            self.assertIs(r.get("halt_confirmed"), True)
            self.assertEqual(r.get("pc"), labels["api_unimplemented"])
            self.assertEqual(r.get("x"), expect_x)
            self.assertIs(r.get("apicall_truncated"), False)
            self.assertIs(r.get("ret_truncated"), False)
            self.assertIn("buffer_hex", r)
            try:
                buf = bytes.fromhex(r["buffer_hex"])
            except (TypeError, ValueError):
                self.fail("buffer_hex is not valid hex")
            self.assertGreaterEqual(len(buf), 2048)
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

    def _validate_abnormal_runs(self, rec, allowed_reasons, expect_x=None,
                               mutant_tag=""):
        # Strict abnormal-halt validator (review 5968681873 MEDIUM).
        # Counts as mutant RED only when ALL hold:
        #  RUN is a 2-list with cold/warm each exactly once (no dup/missing/
        #  excess); every run carries cycle/reason/pc/halt_confirmed/
        #  buffer_hex/apicall_truncated/ret_truncated; reason is in the
        #  per-mutant allowed set (subset of the 3 confirmed halt reasons,
        #  never cd_halt_ok/budget/unknown); halt_confirmed is exactly True
        #  (budget/unconfirmed is harness failure, never RED); pc equals
        #  labels[reason] (halt PC must match the named halt); buffer_hex is
        #  valid hex with >=2048 bytes; no truncated traces; cold/warm share
        #  the same reason and pc (deterministic failure mode); optional
        #  expect_x pins the halt X (table-reroute stub halt X=$4A).
        # Anything else raises AssertionError (harness failure), never RED.
        self.assertIn("RUN", rec, "mutant run missing RUN (harness failure, not detection)")
        runs = rec["RUN"]
        self.assertIsInstance(runs, list, "mutant RUN not a list (harness failure)")
        self.assertEqual(len(runs), 2, "mutant run RUN count != 2 (harness failure)")
        cycles = [r.get("cycle") for r in runs]
        self.assertEqual(sorted(cycles), ["cold", "warm"],
                         f"mutant run cycles must be cold/warm each once, got {cycles} (harness failure)")
        reasons = []
        pcs = []
        for r in runs:
            for field in ("cycle", "reason", "pc", "halt_confirmed",
                          "buffer_hex", "apicall_truncated", "ret_truncated"):
                self.assertIn(field, r, f"mutant run missing {field} (harness failure)")
            self.assertIn(r.get("cycle"), ("cold", "warm"),
                          f"mutant run bad cycle {r.get('cycle')} (harness failure)")
            self.assertIn(r.get("reason"), tuple(allowed_reasons),
                          f"mutant run unexpected reason {r.get('reason')} (harness failure, not detection)")
            self.assertIs(r.get("halt_confirmed"), True,
                          f"mutant run halt not confirmed for {r.get('reason')} (harness failure, not detection)")
            self.assertIsInstance(r.get("pc"), int, "mutant run pc not int (harness failure)")
            self.assertIn(r.get("reason"), self.labels,
                          f"mutant run reason {r.get('reason')} has no verified halt PC (harness failure)")
            self.assertEqual(r.get("pc"), self.labels[r.get("reason")],
                              f"mutant run pc {r.get('pc'):04x} != {r.get('reason')} halt "
                              f"{self.labels[r.get('reason')]:04x} (harness failure, not detection)")
            self.assertIs(r.get("apicall_truncated"), False,
                          "mutant run APICALL truncated (harness failure, not detection)")
            self.assertIs(r.get("ret_truncated"), False,
                          "mutant run RET truncated (harness failure, not detection)")
            try:
                buf = bytes.fromhex(r["buffer_hex"])
            except (TypeError, ValueError):
                self.fail("mutant run buffer_hex invalid hex (harness failure)")
            self.assertGreaterEqual(len(buf), 2048, "mutant run buffer short (harness failure)")
            if expect_x is not None:
                self.assertEqual(r.get("x"), expect_x,
                                 f"mutant run x {r.get('x')!r} != {expect_x:#04x} (harness failure)")
            reasons.append(r.get("reason"))
            pcs.append(r.get("pc"))
        self.assertEqual(reasons[0], reasons[1],
                         f"mutant run cold/warm reasons differ {reasons} (harness failure, not detection)")
        self.assertEqual(pcs[0], pcs[1],
                         "mutant run cold/warm pc differ (harness failure, not detection)")
        # MEDIUM-1: the mutated $4A must actually have been reached in each
        # cycle. RUN evidence alone cannot prove this (an early hang elsewhere
        # yields the same RUN shape).
        self._assert_apicall_arrival(rec, mutant_tag=mutant_tag)

    def _assert_apicall_arrival(self, rec, mutant_tag=""):
        # MEDIUM-1 arrival proof: every cycle needs >=1 APICALL showing a
        # genuine JSR into slot $4A (table entry $E0DE, in_table, SP delta 2,
        # internal stack MATCH). Return success is deliberately NOT required:
        # control-flow mutants mis-return by design, so arrival and return
        # are validated separately (a missing/failed return never invalidates
        # a proven arrival, and a proven arrival never implies a return).
        # Missing APICALL, no $4A call, or broken call evidence in any cycle
        # is HARNESS_FAILURE/INCONCLUSIVE, never MUTANT RED.
        # The probe emits these APICALL fields (geargrafx_api_return_probe.cpp);
        # no arrival shape is guessed beyond what the probe observes.
        self.assertIn("APICALL", rec,
                      f"mutant {mutant_tag} run missing APICALL (harness failure, not detection)")
        calls = rec["APICALL"]
        self.assertIsInstance(calls, list,
                              f"mutant {mutant_tag} APICALL not a list (harness failure)")
        for cycle in ("cold", "warm"):
            arrivals = [c for c in calls
                        if c.get("cycle") == cycle
                        and c.get("slot") == 0x4A
                        and c.get("entry_pc") == 0xE0DE
                        and c.get("in_table") is True
                        and c.get("sp_delta") == 2
                        and c.get("stack_check") == "MATCH"]
            self.assertGreaterEqual(
                len(arrivals), 1,
                f"mutant {mutant_tag} $4A not reached in {cycle} "
                "(harness failure, not detection)")

    def _assert_body_mutant_applied(self, orig_bytes, mutant_bytes, offset, new_bytes):
        # Prove the mutant bytes actually landed at the intended 21-byte body
        # offset (review 5968681873 C): correct offset, correct replacement,
        # ROM length unchanged, bytes really differ. An unpatched copy fails
        # here (harness/mutant-helper failure), never counts as RED.
        body_off = (self.syms["ex_memopen_entry"] & 0xFFFF) - 0xE000
        self.assertEqual(len(orig_bytes), len(mutant_bytes),
                         "mutant ROM length changed (harness failure)")
        self.assertGreaterEqual(offset, 0)
        self.assertLessEqual(offset + len(new_bytes), 21,
                             f"mutant offset {offset}+{len(new_bytes)} outside 21-byte body")
        self.assertEqual(bytes(mutant_bytes[body_off + offset:body_off + offset + len(new_bytes)]),
                         bytes(new_bytes),
                         f"mutant bytes not at body offset {offset}")
        self.assertNotEqual(bytes(orig_bytes), bytes(mutant_bytes),
                            "mutant not applied (bytes identical)")

    def _assert_mutant_red(self, rec, base, check=None, allowed_abnormal=None,
                           expect_x=None, mutant_tag=""):
        # A mutant is detected (RED) through either of two channels:
        # (a) abnormal halt -- dropping/replacing a pop unbalances SP, so
        #     RTS mis-returns and the probe reports a non-halt confirmed halt; or
        # (b) value mismatch -- balanced gate/refusal mutants return cleanly
        #     (cd_halt_ok, confirmed, same PC) but with wrong preserved state.
        # If the run halts cleanly AND all values match, the mutant went
        # GREEN and this helper fails (the test then fails: coverage gap).
        # Review 5968681873 (MEDIUM): channel (a) counts ONLY a strictly
        # validated control-flow failure (cold/warm each once, required
        # fields present, valid 2048B+ buffers, reason in the per-mutant
        # allowed set, halt_confirmed exactly True, pc == labels[reason],
        # same reason/pc both cycles, no truncation, optional X pin).
        # Missing RUN, bad hex, short buffers, budget/unconfirmed halts,
        # pc mismatches, cycle dupes/splits, truncations, or probe/internal
        # errors are harness failures, not mutant detection (fail the test,
        # do not count as RED). `check` selects the baseline matcher (C=0
        # vs C=1); unmutated ROM must be GREEN for it (control). Value-class
        # mutants (gate/refusal) forbid abnormal halts: pass
        # allowed_abnormal=set() so an abnormal path fails instead of
        # counting as RED. Control-flow mutants allow the named halt set;
        # table-reroute additionally pins reason api_unimplemented + X=$4A.
        # PLP->CLC/SEC replacements unbalance SP (pop dropped), so they are
        # control-flow mutants, NOT isolated flag-only mutants (no claim of
        # stack-balanced flag fabrication).
        if check is None:
            check = self._check_c0
        if allowed_abnormal is None:
            allowed_abnormal = ("cd_halt_fail", "api_unimplemented",
                                "cd_unexpected_interrupt")
        try:
            cold, warm = self._buf(rec)
        except AssertionError as buf_err:
            if len(tuple(allowed_abnormal)) == 0:
                self.fail(f"value mutant {mutant_tag} took abnormal path "
                          f"(expected clean-halt value mismatch): {buf_err}")
            # Strict control-flow failure validation (raises on harness failure).
            self._validate_abnormal_runs(rec, allowed_abnormal, expect_x,
                                        mutant_tag=mutant_tag)
            return  # channel (a): expected control-flow failure
        if expect_x is not None:
            self.fail(f"control-flow mutant {mutant_tag} returned cleanly "
                      "(expected abnormal halt)")
        # MEDIUM-2 channel (b): the value mismatch must hold on BOTH cycles
        # with identical buffers. A cold-only anomaly, a warm-only anomaly,
        # or divergent cold/warm anomalies are INCONCLUSIVE (single-cycle
        # glitch or nondeterminism), never MUTANT RED. Checking an exception
        # on one cycle alone is not a detection.
        self.assertEqual(cold, warm,
                         f"value mutant {mutant_tag} cold/warm buffers differ "
                         "(INCONCLUSIVE, not detection)")
        with self.assertRaises(AssertionError,
                               msg=f"mutant went GREEN on cold {mutant_tag}"):
            check(cold, base)  # channel (b), cold side
        with self.assertRaises(AssertionError,
                               msg=f"mutant went GREEN on warm {mutant_tag}"):
            check(warm, base)  # channel (b), warm side

    def test_memopen_mutant_drop_pla_red(self):
        # Drop the success-path A restore (PLA->NOP): unbalanced SP (pop
        # dropped), so RTS mis-returns (control-flow mutant, channel (a)
        # expected; a clean return with wrong A/SP also counts via (b)).
        # Proves A preservation is load-bearing, not incidental.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 12, bytes((0xEA,)))
        self._assert_body_mutant_applied(orig, bad, 12, bytes((0xEA,)))
        rec, base = self._run_on_rom("m-nopla", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, mutant_tag="drop-pla")

    def test_memopen_mutant_drop_plp_red(self):
        # Drop the success-path P restore (PLP->NOP): unbalanced SP, same
        # class as drop-PLA. Proves flag preservation.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 13, bytes((0xEA,)))
        self._assert_body_mutant_applied(orig, bad, 13, bytes((0xEA,)))
        rec, base = self._run_on_rom("m-noplp", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, mutant_tag="drop-plp")

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
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 13, bytes((0x18,)))
        self._assert_body_mutant_applied(orig, bad, 13, bytes((0x18,)))
        rec, base = self._run_on_rom("m-clc", bad,
                                      self._defines(0x07, 0x11, 0x22, 0x07, 0x66, 0x77))
        self._assert_mutant_red(rec, base, check=self._check_c1, mutant_tag="force-clc")

    def test_memopen_mutant_force_sec_red(self):
        # Fabricate C=1 on success (PLP->SEC): unbalanced SP (PLP pop
        # dropped, like PLP->CLC), so this is a control-flow mutant, NOT an
        # isolated flag-only mutant; no stack-balanced flag fabrication is
        # claimed. The C=0 entry class must go RED.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 13, bytes((0x38,)))
        self._assert_body_mutant_applied(orig, bad, 13, bytes((0x38,)))
        rec, base = self._run_on_rom("m-sec", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, mutant_tag="force-sec")

    def test_memopen_mutant_always_refuse_red(self):
        # Force the refusal path (BNE->BRA): balanced refusal return
        # (PLY/PLX/PLA/PLP/SEC/RTS), so clean halt with C=1 is expected
        # (value mutant, channel (b)); an abnormal halt here is unexpected
        # and fails instead of counting as RED. Proves the gate outcome matters.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 8, bytes((0x80,)))
        self._assert_body_mutant_applied(orig, bad, 8, bytes((0x80,)))
        rec, base = self._run_on_rom("m-refuse", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, allowed_abnormal=set(),
                                mutant_tag="always-refuse")

    def test_memopen_mutant_gate_const_red(self):
        # Corrupt the gate constant (CMP #$F8 -> #$F7): MPR1=$F8 wrongly
        # refuses via the balanced refusal path (value mutant, channel (b)).
        # Proves the gate value.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 7, bytes((0xF7,)))
        self._assert_body_mutant_applied(orig, bad, 7, bytes((0xF7,)))
        rec, base = self._run_on_rom("m-const", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, allowed_abnormal=set(),
                                mutant_tag="gate-const")

    def test_memopen_mutant_gate_register_red(self):
        # Read the wrong MPR (TMA #$02 -> #$01 reads MPR0=$FF): the gate
        # wrongly refuses via the balanced path (value mutant, channel (b)).
        # Proves the TMA operand really selects MPR1.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 5, bytes((0x01,)))
        self._assert_body_mutant_applied(orig, bad, 5, bytes((0x01,)))
        rec, base = self._run_on_rom("m-tma", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, allowed_abnormal=set(),
                                mutant_tag="gate-register")

    def test_memopen_mutant_table_reroute_red(self):
        # Reroute $E0DE back to the stub fallback: calls halt at the stub
        # (api_unimplemented, confirmed, X=$4A) instead of returning.
        # Control-flow mutant pinned to that halt (channel (a) only).
        # Proves the test exercises the wired body, not the table alone.
        orig = self.rom.read_bytes()
        tab = bytearray(orig)
        fb = self.syms["api_slot_4A"]
        want_tab = bytes((0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
        tab[3 * 0x4A:3 * 0x4A + 3] = want_tab
        self.assertNotEqual(bytes(tab), orig, "table reroute not applied")
        self.assertEqual(bytes(tab[3 * 0x4A:3 * 0x4A + 3]), want_tab)
        rec, base = self._run_on_rom("m-tab", bytes(tab),
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, allowed_abnormal=("api_unimplemented",),
                                expect_x=0x4A, mutant_tag="table-reroute")

    def test_memopen_buffer_mutants_detected(self):
        # MINOR-1: the preservation checker itself must fire. Every asserted
        # field (marker/A/X/Y/P/SP/MPR x8, both calls) is corrupted in turn
        # and handed to the REAL checker (_check_c0) on cold AND warm buffers;
        # the checker must raise each time. Corrupting bytes the checker never
        # reads (record gaps, far tail) must NOT raise: a silent self-evident
        # `assertEqual(bad, good)` can no longer stand in for the detector.
        rec, base = self._run("mutbuf", self._defines(0x06, 0x11, 0x22,
                                                      0x06, 0x66, 0x77))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        # Control: genuine buffers pass the checker on both cycles.
        self._check_c0(cold, base)
        self._check_c0(warm, base)
        # Every asserted position must trip the checker when corrupted.
        targets = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13,
                   17, 18, 19, 20, 21]
        for buf, cycle in ((cold, "cold"), (warm, "warm")):
            for pos in targets:
                with self.subTest(cycle=cycle, pos=pos):
                    bad = bytearray(buf)
                    bad[base + pos] ^= 0xFF
                    with self.assertRaises(AssertionError,
                                            msg=f"checker blind at +{pos} ({cycle})"):
                        self._check_c0(bytes(bad), base)
        # Bytes the checker never reads must NOT trip it (no false detection).
        n = len(cold)
        self.assertGreaterEqual(n, base + 32)
        unrelated = [base + 14, base + 15, base + 16, base + 22,
                     base + 30, base + 31, n - 2, n - 1]
        for buf, cycle in ((cold, "cold"), (warm, "warm")):
            for idx in unrelated:
                with self.subTest(cycle=cycle, idx=idx):
                    bad = bytearray(buf)
                    bad[idx] ^= 0xFF
                    self._check_c0(bytes(bad), base)  # must not raise

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

    # --- Oracle negative controls (review 5968681873 MEDIUM).
    # Fabricated RUN records (no probe run) that must NEVER count as
    # mutant RED. Each asserts the oracle raises instead of returning.
    # Before the strict-oracle fix the lax cases (cold duplicate,
    # halt_unconfirmed, pc mismatch, truncated, cross-cycle reason split)
    # are falsely accepted as RED, so these tests are RED pre-fix and
    # GREEN post-fix. The remaining cases already fail-closed pre-fix
    # and pin that behavior.
    def _fake_buf_hex(self):
        return "ab" * 2048  # 2048 bytes valid hex

    def _fake_abnormal_run(self, cycle, reason, halt=True, pc=None,
                           buf=None, trunc=False):
        if pc is None:
            pc = self.labels[reason]
        if buf is None:
            buf = self._fake_buf_hex()
        return {"cycle": cycle, "reason": reason,
                "halt_confirmed": halt, "pc": pc,
                "buffer_hex": buf,
                "apicall_truncated": trunc, "ret_truncated": trunc}

    def _fake_abnormal_rec(self, runs):
        return {"RUN": runs}

    def _fake_arrival_call(self, cycle, slot=0x4A, entry_pc=0xE0DE,
                           in_table=True, sp_delta=2, stack_check="MATCH",
                           ret_match=True):
        # Realistic $4A arrival evidence as emitted by
        # geargrafx_api_return_probe.cpp (APICALL line). Only the checked
        # subset (cycle/slot/entry_pc/in_table/sp_delta/stack_check) gates
        # the oracle; the rest is faithful context.
        return {"cycle": cycle, "step": 100,
                "caller_pc": 0x3100, "entry_pc": entry_pc,
                "in_table": in_table, "slot": slot,
                "sp_before": 0xFF, "sp_after": 0xFD, "sp_delta": sp_delta,
                "caller_a": 0x06, "caller_x": 0x11, "caller_y": 0x22,
                "caller_p": 0x06, "stack_check": stack_check,
                "return_matches_caller": ret_match}

    def _fake_valid_abnormal_rec(self, ret_match=True):
        # Strictly-valid channel-(a) record: confirmed halt + per-cycle $4A
        # arrival. Must be accepted as RED (no false harness failure).
        return {"RUN": [self._fake_abnormal_run("cold", "cd_halt_fail"),
                        self._fake_abnormal_run("warm", "cd_halt_fail")],
                "APICALL": [self._fake_arrival_call("cold", ret_match=ret_match),
                              self._fake_arrival_call("cold", ret_match=ret_match),
                              self._fake_arrival_call("warm", ret_match=ret_match),
                              self._fake_arrival_call("warm", ret_match=ret_match)]}

    def _fake_good_buf(self, base=512, popen=0x06):
        good = bytearray(2048)
        good[base] = 0xAA
        good[base + 1:base + 4] = bytes((popen, 0x11, 0x22))
        good[base + 4] = popen
        good[base + 5] = 0xFF
        good[base + 6:base + 14] = bytes((0xFF, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00))
        good[base + 17:base + 20] = bytes((popen, 0x66, 0x77))
        good[base + 20] = popen
        good[base + 21] = 0xFF
        return good

    def _fake_clean_rec(self, cold_hex, warm_hex, pc=0x3000):
        def run(cycle, hx):
            return {"cycle": cycle, "reason": "cd_halt_ok",
                    "halt_confirmed": True, "pc": pc, "buffer_hex": hx,
                    "apicall_truncated": False, "ret_truncated": False}
        return {"RUN": [run("cold", cold_hex), run("warm", warm_hex)]}

    def test_oracle_rejects_cold_duplicate(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented"),
            self._fake_abnormal_run("cold", "api_unimplemented"),
        ])
        with self.assertRaises(AssertionError, msg="cold duplicate accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_run_count_short(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented"),
        ])
        with self.assertRaises(AssertionError, msg="short RUN accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_run_count_excess(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented"),
            self._fake_abnormal_run("warm", "api_unimplemented"),
            self._fake_abnormal_run("warm", "cd_halt_fail"),
        ])
        with self.assertRaises(AssertionError, msg="excess RUN accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_bad_reason(self):
        rec = self._fake_abnormal_rec([
            {"cycle": "cold", "reason": "bogus",
             "halt_confirmed": True, "pc": 0,
             "buffer_hex": self._fake_buf_hex(),
             "apicall_truncated": False, "ret_truncated": False},
            self._fake_abnormal_run("warm", "api_unimplemented"),
        ])
        with self.assertRaises(AssertionError, msg="bad reason accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_budget_reason(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented",
                                      halt=False, pc=0),
            self._fake_abnormal_run("warm", "api_unimplemented",
                                      halt=False, pc=0),
        ])
        # budget-style: halt False with mismatched reason/pc is harness failure
        rec["RUN"][0]["reason"] = "budget"
        rec["RUN"][1]["reason"] = "budget"
        with self.assertRaises(AssertionError, msg="budget accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_halt_unconfirmed(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented", halt=False),
            self._fake_abnormal_run("warm", "api_unimplemented", halt=False),
        ])
        with self.assertRaises(AssertionError, msg="unconfirmed halt accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_pc_mismatch(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented", pc=0xEEEE),
            self._fake_abnormal_run("warm", "api_unimplemented", pc=0xEEEE),
        ])
        with self.assertRaises(AssertionError, msg="pc mismatch accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_cross_cycle_reason_split(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented"),
            self._fake_abnormal_run("warm", "cd_halt_fail"),
        ])
        with self.assertRaises(AssertionError, msg="reason split accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_truncated(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented", trunc=True),
            self._fake_abnormal_run("warm", "api_unimplemented", trunc=True),
        ])
        with self.assertRaises(AssertionError, msg="truncated trace accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_missing_buffer(self):
        r = self._fake_abnormal_run("cold", "api_unimplemented")
        del r["buffer_hex"]
        rec = self._fake_abnormal_rec([
            r, self._fake_abnormal_run("warm", "api_unimplemented"),
        ])
        with self.assertRaises(AssertionError, msg="missing buffer accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_bad_hex(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented", buf="zz"),
            self._fake_abnormal_run("warm", "api_unimplemented"),
        ])
        with self.assertRaises(AssertionError, msg="bad hex accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_short_buffer(self):
        rec = self._fake_abnormal_rec([
            self._fake_abnormal_run("cold", "api_unimplemented", buf="ab" * 100),
            self._fake_abnormal_run("warm", "api_unimplemented"),
        ])
        with self.assertRaises(AssertionError, msg="short buffer accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_missing_run(self):
        with self.assertRaises(AssertionError, msg="missing RUN accepted as RED"):
            self._assert_mutant_red({}, 0)

    def test_oracle_rejects_unapplied_mutant(self):
        # Mutation helper must actually change ROM bytes at the right offset;
        # an unpatched copy is not a mutant and must not count as applied.
        orig = self.rom.read_bytes()
        same = bytes(orig)
        with self.assertRaises(AssertionError, msg="unapplied mutant counted"):
            self._assert_body_mutant_applied(orig, same, 12, bytes((0xEA,)))

    def test_oracle_rejects_clean_rom_as_mutant(self):
        # A clean halt with fully matching values is GREEN, never RED:
        # the detector must raise (mutant went GREEN), not return.
        good = bytearray(2048)
        base = 512
        good[base] = 0xAA
        good[base + 1:base + 4] = bytes((0x06, 0x11, 0x22))
        good[base + 4] = 0x06
        good[base + 5] = 0xFF
        good[base + 6:base + 14] = bytes((0xFF, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00))
        good[base + 17:base + 20] = bytes((0x06, 0x66, 0x77))
        good[base + 20] = 0x06
        good[base + 21] = 0xFF
        hx = bytes(good).hex()
        rec = {"RUN": [
            {"cycle": "cold", "reason": "cd_halt_ok",
             "halt_confirmed": True, "pc": 0x3000,
             "buffer_hex": hx,
             "apicall_truncated": False, "ret_truncated": False},
            {"cycle": "warm", "reason": "cd_halt_ok",
             "halt_confirmed": True, "pc": 0x3000,
             "buffer_hex": hx,
             "apicall_truncated": False, "ret_truncated": False},
        ]}
        with self.assertRaises(AssertionError, msg="clean ROM counted as mutant RED"):
            self._assert_mutant_red(rec, base)

    # --- MEDIUM-1 negatives: $4A arrival is required for channel (a) RED.
    # Pre-fix the oracle never consults APICALL, so every no-evidence record
    # below is falsely accepted as RED (these tests FAIL pre-fix, PASS post-fix).
    def test_oracle_rejects_empty_apicall(self):
        rec = self._fake_valid_abnormal_rec()
        rec["APICALL"] = []
        with self.assertRaises(AssertionError, msg="APICALL=[] accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_missing_apicall(self):
        rec = self._fake_valid_abnormal_rec()
        del rec["APICALL"]
        with self.assertRaises(AssertionError, msg="missing APICALL accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_wrong_slot_arrival(self):
        # Calls exist but never reach slot $4A (e.g. only $02 traffic).
        rec = self._fake_valid_abnormal_rec()
        rec["APICALL"] = [self._fake_arrival_call("cold", slot=0x02),
                          self._fake_arrival_call("warm", slot=0x02)]
        with self.assertRaises(AssertionError, msg="wrong-slot arrival accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_wrong_entry_arrival(self):
        rec = self._fake_valid_abnormal_rec()
        rec["APICALL"] = [self._fake_arrival_call("cold", entry_pc=0xE00C),
                          self._fake_arrival_call("warm", entry_pc=0xE00C)]
        with self.assertRaises(AssertionError, msg="wrong-entry arrival accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_bad_stack_evidence(self):
        # in_table / sp_delta / stack_check each break the JSR-arrival proof.
        for kw in (dict(in_table=False), dict(sp_delta=0),
                   dict(stack_check="MISMATCH")):
            with self.subTest(kw=kw):
                rec = self._fake_valid_abnormal_rec()
                rec["APICALL"] = [self._fake_arrival_call("cold", **kw),
                                  self._fake_arrival_call("warm", **kw)]
                with self.assertRaises(AssertionError,
                                        msg=f"bad stack evidence {kw} accepted as RED"):
                    self._assert_mutant_red(rec, 0)

    def test_oracle_rejects_partial_arrival(self):
        # $4A reached on cold only; warm has no arrival (INCONCLUSIVE).
        rec = self._fake_valid_abnormal_rec()
        rec["APICALL"] = [c for c in rec["APICALL"] if c["cycle"] == "cold"]
        with self.assertRaises(AssertionError, msg="partial arrival accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_accepts_valid_abnormal_arrival(self):
        # Positive control: strictly-valid abnormal record stays RED.
        self._assert_mutant_red(self._fake_valid_abnormal_rec(), 0,
                                mutant_tag="arrival-control")

    def test_oracle_accepts_misreturn_arrival(self):
        # Positive control: arrival proven but return failed
        # (return_matches_caller=False, the control-flow mutant signature)
        # must still count as RED. Return success is NOT required.
        self._assert_mutant_red(self._fake_valid_abnormal_rec(ret_match=False), 0,
                                mutant_tag="misreturn-control")

    # --- MEDIUM-2 negatives: value mismatch must hold on BOTH cycles.
    # Pre-fix only cold is checked, so cold-only and divergent anomalies are
    # falsely accepted as RED (FAIL pre-fix, PASS post-fix).
    def test_oracle_rejects_cold_only_anomaly(self):
        base = 512
        cold = self._fake_good_buf(base)
        cold[base + 1] ^= 0xFF  # corrupt A1 on cold only
        warm = self._fake_good_buf(base)
        rec = self._fake_clean_rec(bytes(cold).hex(), bytes(warm).hex())
        with self.assertRaises(AssertionError, msg="cold-only anomaly accepted as RED"):
            self._assert_mutant_red(rec, base)

    def test_oracle_rejects_warm_only_anomaly(self):
        base = 512
        cold = self._fake_good_buf(base)
        warm = self._fake_good_buf(base)
        warm[base + 1] ^= 0xFF  # corrupt A1 on warm only
        rec = self._fake_clean_rec(bytes(cold).hex(), bytes(warm).hex())
        with self.assertRaises(AssertionError, msg="warm-only anomaly accepted as RED"):
            self._assert_mutant_red(rec, base)

    def test_oracle_rejects_divergent_anomalies(self):
        base = 512
        cold = self._fake_good_buf(base)
        cold[base + 1] ^= 0xFF  # cold: wrong A1
        warm = self._fake_good_buf(base)
        warm[base + 4] ^= 0xFF  # warm: wrong P1 (different anomaly)
        rec = self._fake_clean_rec(bytes(cold).hex(), bytes(warm).hex())
        with self.assertRaises(AssertionError, msg="divergent anomalies accepted as RED"):
            self._assert_mutant_red(rec, base)

    def test_oracle_accepts_both_cycle_value_mismatch(self):
        # Positive control: same value corruption on both cycles stays RED.
        base = 512
        bad = self._fake_good_buf(base)
        bad[base + 1] ^= 0xFF
        hx = bytes(bad).hex()
        rec = self._fake_clean_rec(hx, hx)
        self._assert_mutant_red(rec, base, mutant_tag="both-cycle-control")


if __name__ == "__main__":
    unittest.main()
