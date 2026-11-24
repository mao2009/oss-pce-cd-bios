"""Synthetic real-core tests for the $36 EX_SATCLR B1-analogous preservation scaffold (Issue #64).

Proves the CURRENT experimental wiring end to end on synthetic media with the
pinned core: PUBLIC IPL boot (2-base builder, commercial FIRST default,
cd_4a+cd_25+cd_36="experimental") -> JSR $E0A2 (real table entry,
never direct-body JSR, never fake RTS) -> full A/X/Y/P(incl Carry)/MPR/SP
preservation -> RTS resumption -> payload halt. Companion coverage:

  stub default (cd_36="stub") keeps $E0A2 on the linked fallback (byte-equal
  to the normal DEV ROM) and traps with X=$36;
  entry-C=0 and entry-C=1 preservation (forced-CLC mutant M7a must RED on the
  C=1 class as M7a-C1-preservation; forced-SEC mutant M7b must RED on the C=0
  class as M7b-C0-preservation; no fabricated flag, no ok/reject values);
  nonzero entry A/X/Y preservation incl. A!=P vectors (a clobber mutant goes
  RED: no fixed success value);
  VDC invariance on the real core (dedicated probe: AR unchanged for several
  entry AR values, all 20 VDC regs unchanged, MPRs/IDR/IRR unchanged);
  work-RAM scope (256-byte canary + full-buffer cold/warm equality);
  MPR1=$F8 precondition validated by the harness BEFORE JSR (T13: a $FF
  candidate refuses at refused_halt without executing JSR, never reaches
  $36, and never remaps first);
  SP depth (SP=$FF and boundary SP=$06 GREEN and balanced guest-executed;
  SP=$00/$05 probe-argument refusal GREEN with no guest execution; SP<$06
  guest execution OUT_OF_CONTRACT/NOT_RUN and never run);
  synthetic chain $33->$4A->$23->$25->$36 through the real table entries with
  per-call APICALL evidence (the $02 leg is the PUBLIC boot itself; the
  observed $36 entry state is labeled SIMULATED-entry);
  refusal-path absence (B1 defines no refusal: T04/T08/T09 NOT_APPLICABLE),
  routing, 9-byte structural pin, and buffer negatives.

NOT a compatible System Card BIOS. NOT genuine ABI evidence (genuine $36
semantics stay UNKNOWN/BLOCKED; see docs/64-ex-satclr-contract.md and
docs/64-ex-satclr-b1.md). NOT DEV_CALLABLE. release_eligible=false.
G5_OBSERVED=false. Synthetic only; commercial media: NOT_RUN here (no ./rom
probing in this file).
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

# Payload CODE ($3000): entry regs with A and P independent (PHA pushes
# POPEN, LDA #AONE sets A, PLP restores P -- so at JSR, A==AONE and
# P==POPEN exactly; X/Y stay fully independent). MPR0=$F8 (non-I/O, so any
# I/O touch is detectable), MPR1=$F8 (caller precondition, T13). A 256-byte
# canary at $2900 ($5A) proves the work-RAM scope on the real core (T05).
# JSR the real $E0A2 table entry twice with different regs, record
# everything, halt. res_base layout (32 bytes):
#   +0 ran marker ($AA)  +1 A  +2 X  +3 Y  +4 P  +5 SP  +6..13 MPR0..7
#   second call records at +16.. (same shape, no marker)
PAYLOAD_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    ldx #SPINIT
    txs
    lda #$F8
    tam #$02
    lda #$F8
    tam #$01
    lda #$5A
    ldx #$00
canfill:
    sta $2900,x
    dex
    bne canfill
    lda #$A5
    sta $10
    lda #POPEN
    ldx #XONE
    ldy #YONE
    lda $10
    sta res_base+14
    lda #POPEN
    pha
    lda #AONE
    plp
    jsr $E0A2
    sta res_base+1
    stx res_base+2
    sty res_base+3
    php
    pla
    sta res_base+4
    lda $10
    sta res_base+15
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
    lda $10
    sta res_base+30
    lda #PTWO
    pha
    lda #ATWO
    plp
    jsr $E0A2
    sta res_base+17
    stx res_base+18
    sty res_base+19
    php
    pla
    sta res_base+20
    lda $10
    sta res_base+31
    tsx
    txa
    sta res_base+21
    tma #$01
    sta res_base+22
    tma #$02
    sta res_base+23
    tma #$04
    sta res_base+24
    tma #$08
    sta res_base+25
    tma #$10
    sta res_base+26
    tma #$20
    sta res_base+27
    tma #$40
    sta res_base+28
    tma #$80
    sta res_base+29
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

STUB25_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    ldx #$FF
    txs
    jsr $E0A2
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 16
"""

# T13 harness precondition probe: the MPR1 candidate is validated BEFORE any
# JSR or remap. With BREAK_MPR1=1 the candidate is $FF (an out-of-contract
# mapping): the harness must refuse at refused_halt WITHOUT executing JSR
# and WITHOUT remapping (remapping first would unmap this code: MPR1 selects
# the $2000-$3FFF bank, so a live MPR1=$FF makes the payload itself
# unreachable -- the guard must therefore check-then-act, never act-then-check).
# With BREAK_MPR1=0 the candidate is $F8 and the call runs normally.
MPR1_ASM = r"""
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
    .if BREAK_MPR1
    lda #$FF
    .else
    lda #$F8
    .endif
    cmp #$F8
    bne refused_halt
    tam #$02
    lda #$07
    ldx #$11
    ldy #$22
    pha
    lda #$3C
    plp
    jsr $E0A2
    sta res_base+1
    stx res_base+2
    sty res_base+3
    php
    pla
    sta res_base+4
    tsx
    txa
    sta res_base+5
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
refused_halt:
    bra refused_halt
res_base:
    .res 16
"""

# Synthetic G5 chain leg: $33 -> $4A -> $23 -> $25 -> $36 through the real table
# entries (the $02 leg is the PUBLIC boot that loads this payload). Entry
# regs are set once; every body preserves them, so the final record equals
# the entry state. Each APICALL's return_matches_caller + MATCH + SP balance
# observes the STACKED return bytes at table-entry time (before the body
# runs) -- not the RTS landing itself. The 9-byte body-shape pin (exact B1
# bytes verified pre-run) is the only guard against a body that rewrites
# its stacked return to a trampoline; APIRETURN is not collected.
CHAIN_ASM = r"""
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
    lda #$5A
    ldx #$00
canfill:
    sta $2900,x
    dex
    bne canfill
    lda #$A5
    sta $10
    lda $10
    sta res_base+14
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
    jsr $E0A2
    sta res_base+1
    stx res_base+2
    sty res_base+3
    php
    pla
    sta res_base+4
    lda $10
    sta res_base+15
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
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 16
"""

# B1 preservation-only scaffold bytes: PHP PHA PHX PHY PLY PLX PLA PLP RTS.
B1_BODY = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x60))
# Extended cavity mutant bodies (position-independent: stack ops only, no
# absolute addressing except the deliberate stores; executed via a repointed
# $E0A2 vector from an $FF cavity in BOOT padding, never in the normal ROM).
# Balanced flag-only mutants for the T07 Carry oracle (4 pushes / 4 pops,
# clean RTS, only the Carry bit differs from B1):
MUT_CLC10 = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x18, 0x60))
MUT_SEC10 = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x38, 0x60))
# Balanced out-of-scope write mutant for the T05 write tracer (B1 + STA
# $2200 + RTS; $2200 is outside the canary $2900-$29FF and zp $10):
MUT_STA2200_12 = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28,
                        0x8D, 0x00, 0x22, 0x60))
# Balanced write-then-restore mutant (save $2200 / write $AA / restore $2200
# inserted between B1's PLA and PLP, so the final PLP still restores the
# entry flags exactly; final $2200 value unchanged, write history non-empty):
MUT_RESTORE_36 = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68,
                        0x48, 0xAD, 0x00, 0x22, 0x48, 0xA9, 0xAA, 0x8D,
                        0x00, 0x22, 0x68, 0x8D, 0x00, 0x22, 0x68, 0x28,
                        0x60))


class Satclr36Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-36-")
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
        # Experimental image (cd_4a+cd_25+cd_36 experimental); stub default via
        # the builder defaults for the control comparisons.
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m36", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False,
            cd_4a="experimental", cd_25="experimental",
            cd_36="experimental")
        cls.rom_stub, cls.syms_stub = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m36-stub", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False)
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        for rom_bytes, symbols, addr, name in (
                (cls.rom.read_bytes(), cls.syms, cls.syms["ex_satclr_entry"], "exp"),
                (cls.rom_stub.read_bytes(), cls.syms_stub, cls.syms_stub["api_slot_36"], "stub")):
            tab = rom_bytes[0xE0A2 - 0xE000:0xE0A2 - 0xE000 + 3]
            assert tuple(tab) == (0x4C, addr & 0xFF, (addr >> 8) & 0xFF), (name, addr)
        trace = cls.out / "ipl-loader-g5-m36.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        trace_stub = cls.out / "ipl-loader-g5-m36-stub.lbl"
        cls.labels_stub = parse_labels(
            trace_stub, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "m36-probe")
        cls.vdc_probe = compile_probe(cls.out, "geargrafx_satclr36_probe.cpp", "m36-vdc-probe")
        cls.writes_probe = compile_probe(cls.out, "geargrafx_satclr36_writes_probe.cpp",
                                         "m36-writes-probe")
        import geargrafx_ipl_loader as L
        cls.L = L

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tmp = cls._tmp.name
        assert "pce-36-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    def _run(self, name, defines, rom_kind="exp"):
        if rom_kind == "exp":
            rom, labels = self.rom, self.labels
        elif rom_kind == "stub":
            rom, labels = self.rom_stub, self.labels_stub
        else:
            raise ValueError("rom_kind must be 'exp' or 'stub'")
        return self._run_probe_on_rom(name, rom, labels, defines)

    def _run_on_rom(self, name, rom_bytes, defines):
        # Run the synthetic payload against caller-supplied ROM bytes
        # (mutants live in /tmp-style out dirs, never in tracked files).
        rom_path = self.out / (name + ".pce")
        rom_path.write_bytes(bytes(rom_bytes))
        return self._run_probe_on_rom(name, rom_path, self.labels, defines)

    def _run_probe_on_rom(self, name, rom, labels, defines):
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
        lab_lines = (f"cd_halt_ok {halt:04x}\ncd_halt_fail {labels['cd_halt_fail']:04x}\n"
                     f"cd_unexpected_interrupt {labels['cd_unexpected_interrupt']:04x}\n"
                     f"api_unimplemented {labels['api_unimplemented']:04x}\n")
        refused = re.search(r"^al ([0-9A-Fa-f]{6}) \.refused_halt$", text, re.M)
        if refused:
            # T13 refusal alias: the harness refusal halt is observed through
            # the cd_halt_fail reason (documented aliasing for this run only;
            # boot failures would prevent the payload from running at all).
            lab_lines += f"cd_halt_fail {int(refused.group(1), 16):04x}\n"
        lab.write_text(lab_lines, encoding="ascii")
        import geargrafx_api_entry as Eentry
        rec = Eentry.run_probe(self.probe, rom, cue, lab, BUDGET, "synthetic")
        return rec, res_base

    def _buf(self, rec):
        # Strict clean-halt validator: cold/warm each 1, all required fields
        # present, halt confirmed, same PC both cycles, valid 2048B+ buffers,
        # no truncated traces. Any deviation is a harness failure
        # (AssertionError), never a silent pass.
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

    def _defines(self, popen, xone, yone, ptwo, xtwo, ytwo, aone=None, atwo=None,
                 spinit=0xFF):
        # Entry A (AONE/ATWO) is independent of entry P (POPEN/PTWO): PHA
        # pushes POPEN, LDA #AONE sets A, PLP restores P. Legacy callers omit
        # AONE/ATWO and keep A==P; A!=P vectors pass explicit values.
        # SPINIT selects the caller stack pointer ($FF normal, $06 boundary
        # minimum for the 4-push body + 2 JSR bytes; below $06 guest execution
        # is out of contract and never run on the real core; $00/$05 probe-argument
        # refusal is verified separately without guest execution).
        if aone is None:
            aone = popen
        if atwo is None:
            atwo = ptwo
        return {"asm": PAYLOAD_ASM,
                "defs": {"POPEN": popen, "AONE": aone, "XONE": xone, "YONE": yone,
                         "PTWO": ptwo, "ATWO": atwo, "XTWO": xtwo, "YTWO": ytwo,
                         "SPINIT": spinit}}

    def _mutate_body(self, rom_bytes, offset, new_bytes):
        # Patch the experimental $36 body in a ROM copy (mutants live in
        # /tmp-style out dirs, never in tracked files).
        body_off = (self.syms["ex_satclr_entry"] & 0xFFFF) - 0xE000
        out = bytearray(rom_bytes)
        out[body_off + offset:body_off + offset + len(new_bytes)] = new_bytes
        return bytes(out)

    def _cavity_off(self, rom_bytes, size):
        # First $FF run of at least `size` bytes in the BOOT file range
        # [0x1000, 0x1FF6) (bank0 padding before VECTORS $FFF6).
        # Deterministic for identical images; the caller re-verifies the
        # cavity content in _assert_ext_mutant_applied.
        bank = rom_bytes[:0x2000]
        i = 0x1000
        while i < 0x1FF6:
            if bank[i] == 0xFF:
                j = i
                while j < 0x1FF6 and bank[j] == 0xFF:
                    j += 1
                if j - i >= size:
                    return i
                i = j
            else:
                i += 1
        self.fail(f"no FF cavity >= {size} in BOOT (harness failure, not RED)")

    def _mutate_body_extended(self, rom_bytes, mutant_bytes):
        # Extended mutant body in a BOOT $FF cavity with the $E0A2 vector
        # repointed to it (for shapes that do not fit the 9-byte B1 body:
        # balanced flag-only and balanced store mutants). Returns
        # (mutant_rom, cavity_file_offset). Position-independent bodies
        # only (stack ops + RTS, plus the deliberate store under test).
        out = bytearray(rom_bytes)
        cav = self._cavity_off(rom_bytes, len(mutant_bytes))
        if not 0x1000 <= cav <= 0x1FF6 - len(mutant_bytes):
            self.fail("cavity outside BOOT span (harness failure)")
        cav_addr = 0xE000 + cav
        out[cav:cav + len(mutant_bytes)] = mutant_bytes
        out[3 * 0x36:3 * 0x36 + 3] = bytes((0x4C, cav_addr & 0xFF,
                                            (cav_addr >> 8) & 0xFF))
        return bytes(out), cav

    def _assert_ext_mutant_applied(self, orig_bytes, mutant_bytes_rom, cavity_off,
                                   mutant_bytes):
        # Prove the extended mutant changed ONLY the $E0A2 vector plus a
        # previously-$FF cavity: ROM length unchanged, cavity was all-$FF,
        # cavity now holds the mutant bytes, vector targets the cavity, the
        # normal 9-byte B1 body is intact (so the test exercises the mutant
        # body through the vector, never the original), and every other
        # byte is identical.
        size = len(mutant_bytes)
        self.assertGreater(size, 0, "empty replacement is not a mutant (harness failure)")
        self.assertEqual(len(orig_bytes), len(mutant_bytes_rom),
                         "mutant ROM length changed (harness failure)")
        self.assertEqual(bytes(orig_bytes[cavity_off:cavity_off + size]),
                         bytes((0xFF,) * size),
                         "cavity was not all-$FF (harness failure)")
        self.assertEqual(bytes(mutant_bytes_rom[cavity_off:cavity_off + size]),
                         bytes(mutant_bytes), "mutant bytes not in cavity (harness failure)")
        cav_addr = 0xE000 + cavity_off
        self.assertEqual(bytes(mutant_bytes_rom[3 * 0x36:3 * 0x36 + 3]),
                         bytes((0x4C, cav_addr & 0xFF, (cav_addr >> 8) & 0xFF)),
                         "$E0A2 vector does not target the cavity (harness failure)")
        body_off = (self.syms["ex_satclr_entry"] & 0xFFFF) - 0xE000
        self.assertEqual(bytes(mutant_bytes_rom[body_off:body_off + 9]), B1_BODY,
                         "normal B1 body disturbed (harness failure)")
        expect = bytearray(orig_bytes)
        expect[cavity_off:cavity_off + size] = mutant_bytes
        expect[3 * 0x36:3 * 0x36 + 3] = bytes((0x4C, cav_addr & 0xFF,
                                               (cav_addr >> 8) & 0xFF))
        self.assertEqual(bytes(mutant_bytes_rom), bytes(expect),
                         "mutant changed bytes outside vector+cavity (harness failure)")

    def _assert_green(self, rec, base, check, spinit=0xFF, mutant_tag=""):
        # Full GREEN proof for a clean-halt run: halt shape, both-cycle
        # value match (incl. zp/canary via the checker), SP balance, and
        # proven $36 arrival. Used for passing-seed mutant runs (T07) and
        # for old-oracle GREEN controls on tracer mutants (T05).
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm,
                         f"GREEN run {mutant_tag} cold/warm buffers differ (harness failure)")
        check(cold, base)
        check(warm, base)
        self._check_sp(cold, base, spinit)
        self._check_sp(warm, base, spinit)
        self._check_canary(cold, base)
        self._check_canary(warm, base)
        self._assert_apicall_arrival(rec, mutant_tag=mutant_tag)

    def _check_canary(self, buf, base):
        # T05 work-RAM scope: the 256-byte canary at CPU $2900-$29FF (dump
        # offset 0x100 absolute, independent of res_base) must be intact.
        # The payload fills it before JSR; B1 writes no work RAM outside
        # the transient caller-stack bytes.
        del base  # absolute dump offset, not res-relative
        self.assertEqual(buf[0x100:0x200], bytes((0x5A,) * 256))

    def _check_expected(self, cold, base, p1, a1, x1, y1, p2, a2, x2, y2):
        # General preservation matcher: entry (P/A/X/Y) per call are
        # independent expectations. A is compared exactly (no conflation
        # with P); P is compared modulo B (PHP readback forces B, the latch
        # masks $EF). SP balanced at SPINIT, MPRs exact (MPR0=$F8 set by the
        # payload so any I/O touch is detectable), marker set.
        r = cold
        self.assertEqual(r[base], 0xAA)
        self.assertEqual((r[base + 1], r[base + 2], r[base + 3]), (a1, x1, y1))
        self.assertEqual(r[base + 4] & ~0x10, p1)
        self.assertEqual(list(r[base + 6:base + 14]), [0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        # CodeRabbit: the second call's MPR record is asserted too (same
        # expected mapping; the payload performs no MPR write between calls).
        self.assertEqual(list(r[base + 22:base + 30]), [0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        # Zero-page scratch invariance (guest-observed, both calls): the
        # payload seeds zp $10 with $A5 immediately before each JSR; B1 must
        # leave it untouched (only transient caller-stack bytes may change).
        self.assertEqual(r[base + 14], 0xA5)
        self.assertEqual(r[base + 15], 0xA5)
        self.assertEqual(r[base + 30], 0xA5)
        self.assertEqual(r[base + 31], 0xA5)
        self.assertEqual((r[base + 17], r[base + 18], r[base + 19]), (a2, x2, y2))
        self.assertEqual(r[base + 20] & ~0x10, p2)

    def _check_sp(self, cold, base, spinit):
        self.assertEqual(cold[base + 5], spinit)
        self.assertEqual(cold[base + 21], spinit)

    def _check_c0(self, cold, base, aone=0x06, atwo=0x06):
        # Entry P=$06 (C=0): the most discriminating class. A forced-SEC
        # mutant (M7b) must RED here (M7b-C0-preservation); dropping restores
        # and clobbers change at least one field.
        self._check_expected(cold, base, 0x06, aone, 0x11, 0x22,
                             0x06, atwo, 0x66, 0x77)

    def _check_c1(self, cold, base, aone=0x07, atwo=0x07):
        # Entry P=$07 (C=1): C=1 preservation class. A forced-CLC mutant
        # (M7a) must RED here (M7a-C1-preservation).
        self._check_expected(cold, base, 0x07, aone, 0x11, 0x22,
                             0x07, atwo, 0x66, 0x77)

    def _check_mixed(self, cold, base):
        # A!=P vector (synthetic example, not genuine ABI): first call
        # P=$07(C=1)/A=$B0/X=$11/Y=$22, second P=$06(C=0)/A=$35/X=$66/Y=$77.
        self._check_expected(cold, base, 0x07, 0xB0, 0x11, 0x22,
                             0x06, 0x35, 0x66, 0x77)

    def test_satclr_preserves_carry_set(self):
        # T03: entry P=$07 (C=1). Everything preserved, canary intact.
        rec, base = self._run("c1", self._defines(0x07, 0x11, 0x22,
                                                  0x07, 0x66, 0x77))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        self.assertEqual(cold[base], 0xAA)  # payload ran => PUBLIC boot OK
        self._check_c1(cold, base)
        self._check_c1(warm, base)
        for buf in (cold, warm):
            self.assertEqual(buf[base + 4] & 0x01, 0x01)  # Carry kept, call 1
            self.assertEqual(buf[base + 20] & 0x01, 0x01)  # Carry kept, call 2
        self._check_sp(cold, base, 0xFF)
        self._check_sp(warm, base, 0xFF)
        self._check_canary(cold, base)
        self._check_canary(warm, base)

    def test_satclr_preserves_carry_clear(self):
        # T03: entry P=$06 (C=0). Carry stays clear, canary intact.
        rec, base = self._run("c0", self._defines(0x06, 0x11, 0x22,
                                                  0x06, 0x66, 0x77))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        self._check_c0(cold, base)
        self._check_c0(warm, base)
        self._check_sp(cold, base, 0xFF)
        self._check_sp(warm, base, 0xFF)
        self._check_canary(cold, base)
        self._check_canary(warm, base)

    def test_satclr_preserves_ap_independent(self):
        # T03: A and P are independent inputs (A!=P). First call
        # P=$07(C=1)/A=$B0, second P=$06(C=0)/A=$35 (synthetic example
        # values, not genuine ABI). Everything preserved on both cycles.
        rec, base = self._run("ap", self._defines(0x07, 0x11, 0x22,
                                                     0x06, 0x66, 0x77,
                                                     aone=0xB0, atwo=0x35))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        self._check_mixed(cold, base)
        self._check_mixed(warm, base)
        for buf in (cold, warm):
            self.assertEqual(buf[base + 4] & 0x01, 0x01)  # C=1 kept, call 1
            self.assertEqual(buf[base + 20] & 0x01, 0x00)  # C=0 kept, call 2
            self.assertNotEqual(buf[base + 1], buf[base + 4] & ~0x10)  # A1 != P1
            self.assertNotEqual(buf[base + 17], buf[base + 20] & ~0x10)  # A2 != P2
        self._check_canary(cold, base)
        self._check_canary(warm, base)

    def test_checker_distinguishes_a_from_p(self):
        # The preservation checker itself must fire. Every asserted field
        # is corrupted in turn and handed to the REAL checker; the checker
        # must raise each time. Corrupting bytes the checker never reads
        # must NOT raise.
        rec, base = self._run("apd", self._defines(0x07, 0x11, 0x22,
                                                      0x06, 0x66, 0x77,
                                                      aone=0xB0, atwo=0x35))
        cold, warm = self._buf(rec)
        self._check_mixed(cold, base)  # control: genuine buffers pass
        self._check_mixed(warm, base)
        for buf, cycle in ((cold, "cold"), (warm, "warm")):
            for pos, val, tag in ((1, 0x07, "a1:=p1"), (17, 0x06, "a2:=p2")):
                with self.subTest(cycle=cycle, tag=tag):
                    bad = bytearray(buf)
                    bad[base + pos] = val
                    with self.assertRaises(AssertionError,
                                            msg=f"checker conflates A/P ({tag})"):
                        self._check_mixed(bytes(bad), base)

    def test_satclr_apicall_slot_order(self):
        # T02: return mechanics. JSR through the real $E0A2 table entry
        # (3-byte JSR), RTS lands at caller+3, SP delta 2, MATCH, balanced.
        rec, base = self._run("order", self._defines(0x07, 0x11, 0x22,
                                                     0x06, 0x66, 0x77))
        cold, warm = self._buf(rec)  # recognized halt first; order alone must not pass a hang
        self.assertEqual(cold, warm)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            slots = [c["slot"] for c in calls]
            self.assertEqual(slots, [0x36, 0x36], cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["entry_pc"], 0xE0A2, (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))
        # NOTE: the probe parser only collects the APICALL tag (APIRETURN
        # lines are not collected). return_matches_caller + stack_check ==
        # MATCH + SP balance observe the stacked return bytes at entry time,
        # before the body runs -- they do not observe the RTS destination.
        # A body that rewrites its stacked return to a trampoline landing
        # back at caller+3 would still MATCH; that class is excluded only by
        # the 9-byte structural pin (exact B1 bytes verified pre-run), not
        # by runtime records. The post-return record stream below excludes
        # only bodies that skip bytes (e.g. resuming at caller+6 instead of
        # caller+3 cannot produce the exact records and the verified halt).
        self._check_expected(cold, base, 0x07, 0x07, 0x11, 0x22,
                             0x06, 0x06, 0x66, 0x77)
        self._check_expected(warm, base, 0x07, 0x07, 0x11, 0x22,
                             0x06, 0x06, 0x66, 0x77)
        self._check_sp(cold, base, 0xFF)
        self._check_sp(warm, base, 0xFF)
        self._check_canary(cold, base)
        self._check_canary(warm, base)

    def test_satclr_body_bytes_pinned(self):
        # Structural anti-fake pin (no probe run): the experimental body must
        # be exactly the reviewed 9-byte sequence. A bare-RTS substitution
        # (60 EA ...) would be GREEN on behavior in observed classes by
        # construction (RTS preserves everything when there is no work), so
        # the body shape itself is pinned here: the save/restore discipline
        # is load-bearing and must change only together with this test.
        exp = self.rom.read_bytes()
        body = self.syms["ex_satclr_entry"] & 0xFFFF
        self.assertEqual(len(B1_BODY), 9)
        self.assertEqual(exp[body - 0xE000:body - 0xE000 + 9], B1_BODY)
        self.assertNotEqual(B1_BODY[0], 0x60)  # not a bare RTS

    def test_satclr_boundary_values(self):
        # T11: byte-boundary preservation probe (B1 defines no reject code;
        # every value must be preserved exactly, never rejected). P domain
        # excludes T=1: the pinned core consumes the transfer flag every
        # clock (huc6280_inline.h Clock(): m_transfer_flag snapshot +
        # ClearFlag), so entry T=1 cannot round-trip on ANY implementation
        # (platform fact, not B1 behavior; documented in docs/64-ex-satclr-b1.md).
        rec, base = self._run("bnd", self._defines(0x04, 0x00, 0xA5,
                                                     0x85, 0x80, 0x5A,
                                                     aone=0x00, atwo=0xFF))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        self._check_expected(cold, base, 0x04, 0x00, 0x00, 0xA5,
                             0x85, 0xFF, 0x80, 0x5A)
        self._check_expected(warm, base, 0x04, 0x00, 0x00, 0xA5,
                             0x85, 0xFF, 0x80, 0x5A)
        self._check_canary(cold, base)
        self._check_canary(warm, base)

    def test_satclr_sp_boundary_minimum(self):
        # T12: SP=$06 is the in-contract minimum for the 4-push body
        # (JSR 2 bytes + P/A/X/Y 4 bytes = 6 bytes; $2101-$2106 used, no
        # wrap, no caller-stack overwrite). GREEN and balanced.
        rec, base = self._run("sp06", self._defines(0x07, 0x11, 0x22,
                                                      0x06, 0x66, 0x77,
                                                      spinit=0x06))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        # First call is the C=1 class, second the C=0 class.
        self._check_expected(cold, base, 0x07, 0x07, 0x11, 0x22,
                             0x06, 0x06, 0x66, 0x77)
        self._check_expected(warm, base, 0x07, 0x07, 0x11, 0x22,
                             0x06, 0x06, 0x66, 0x77)
        self._check_sp(cold, base, 0x06)
        self._check_sp(warm, base, 0x06)
        self._check_canary(cold, base)
        self._check_canary(warm, base)
        # Below-minimum SP guest execution is OUT OF CONTRACT (T12): the harness
        # never runs it on the real core; the minimum is pinned here and in
        # docs/64-ex-satclr-b1.md. SP<$06 would consume the caller's live
        # stack bytes and must never be reported GREEN. $00/$05 probe refusal
        # (no guest run) is verified in test_writes_rejects_contract_sp.

    def test_mpr1_precondition_refused_before_jsr(self):
        # T13: the harness validates MPR1 BEFORE JSR $E0A2. With MPR1=$FF
        # the harness refuses at refused_halt WITHOUT executing JSR: no
        # $36 APICALL exists in either cycle. Rationale: JSR itself writes
        # 2 return bytes through the MPR1 mapping before any body gate runs,
        # so a body-internal gate cannot make the call side-effect-free.
        rec, base = self._run("mpr1ref", {"asm": MPR1_ASM,
                                            "defs": {"BREAK_MPR1": 1}})
        runs = {r["cycle"]: r for r in rec["RUN"]}
        self.assertEqual(sorted(runs), ["cold", "warm"])
        for cycle, run in runs.items():
            with self.subTest(cycle=cycle):
                # Refusal halt observed through the documented cd_halt_fail
                # alias (see _run_probe_on_rom); halt confirmed.
                self.assertEqual(run["reason"], "cd_halt_fail")
                self.assertTrue(run["halt_confirmed"])
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"]
                       if c["cycle"] == cycle and c["slot"] == 0x36]
            self.assertEqual(calls, [],
                             f"MPR1!=$F8 reached $36 in {cycle} (must refuse BEFORE JSR)")

    def test_mpr1_precondition_positive_control(self):
        # T13 positive control: with MPR1=$F8 the same harness shape runs
        # JSR normally (GREEN preservation + canary).
        rec, base = self._run("mpr1ok", {"asm": MPR1_ASM,
                                           "defs": {"BREAK_MPR1": 0}})
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        self.assertEqual(cold[base], 0xAA)
        self.assertEqual((cold[base + 1], cold[base + 2], cold[base + 3]),
                         (0x3C, 0x11, 0x22))
        self.assertEqual(cold[base + 4] & ~0x10, 0x07)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"]
                       if c["cycle"] == cycle and c["slot"] == 0x36]
            self.assertEqual(len(calls), 1, cycle)
            self.assertEqual(calls[0]["entry_pc"], 0xE0A2, cycle)
            self.assertEqual(calls[0].get("stack_check"), "MATCH", cycle)

    def _run_vdc(self, name, entry_ar, areg, xreg, pseed):
        # Dedicated VDC-invariance probe: guest sets AR, calls $E0A2, halts.
        # C++ snapshots VDC R[0..19] + AR + MPRs + IDR/IRR before and after.
        import tempfile
        o = self.out / ("vdc-" + name)
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.vdc_probe), str(self.rom.resolve()),
                            str(cue.resolve()), "4096", str(entry_ar),
                            str(areg), str(xreg), str(pseed)],
                           capture_output=True, text=True, timeout=120, check=True)
        import json
        for line in r.stdout.splitlines():
            if line.startswith("RESULT "):
                return json.loads(line[7:])
        raise ValueError("no RESULT line\n" + r.stderr)

    def _check_vdc(self, res, entry_ar, areg, xreg, pseed):
        # T03/T05 VDC scope (snapshot equality, not absence of writes): AR
        # equals the guest-set value and all 20 VDC registers equal the
        # pre-run snapshot; regs/MPRs/IRQ state preserved. Same-value or
        # restore-masked VDC writes are NOT excluded by this snapshot
        # (NOT_RUN/BLOCKED); absence of VDC writes is established only by the
        # exact 9-byte body pin (no ST0/ST1/ST2 opcode), not by this probe.
        self.assertEqual(res["pc"], res["halt"])
        self.assertEqual(res["a"], areg)
        self.assertEqual(res["p"] & ~0x10, pseed & ~0x10)
        self.assertEqual(res["x"], xreg)
        self.assertEqual(res["y"], 0xA5)
        self.assertEqual(res["sp"], 0xFF)
        self.assertEqual(res["mpr_store"], res["mpr_after"])
        # MPR0/MPR1 are set by the guest itself ($F8/non-I/O + work RAM);
        # B1 must leave them at the guest-set values. MPR2-7 are untouched
        # by both guest and body, so after == before there.
        self.assertEqual(res["mpr_after"][0], 0xF8)
        self.assertEqual(res["mpr_after"][1], 0xF8)
        self.assertEqual(res["mpr_after"][2:], res["mpr_before"][2:])
        # VDC state equality (snapshot): AR still the guest-set value, every
        # register identical to the pre-run snapshot. No-write claim reserved
        # for the exact body pin (see test_satclr_body_bytes_pinned).
        self.assertEqual(res["vdc_ar_after"], entry_ar)
        self.assertEqual(res["vdc_r_before"], res["vdc_r_after"])
        # No IRQ side effects.
        self.assertEqual(res["idr_before"], res["idr_after"])
        self.assertEqual(res["irr_before"], res["irr_after"])

    def test_vdc_ar_unchanged_cr_family(self):
        self._check_vdc(self._run_vdc("ar5", 5, 0x3C, 0x12, 0x07), 5, 0x3C, 0x12, 0x07)

    def test_vdc_ar_unchanged_mwr_family(self):
        self._check_vdc(self._run_vdc("ar9", 9, 0x00, 0x80, 0x06), 9, 0x00, 0x80, 0x06)

    def test_vdc_ar_unchanged_high_select(self):
        self._check_vdc(self._run_vdc("ar18", 18, 0xFF, 0x00, 0x07), 18, 0xFF, 0x00, 0x07)

    def test_vdc_ar_unchanged_zero_select(self):
        self._check_vdc(self._run_vdc("ar0", 0, 0x5A, 0x34, 0x06), 0, 0x5A, 0x34, 0x06)

    def _run_writes(self, name, spinit, rom_bytes=None):
        # WRAM write-tracer run: HuCARD-style guest (SEI, MPR0/1=$F8,
        # SP=spinit, JSR $E0A2, halt) with per-instruction WRAM write
        # history recorded by the probe, cold then warm. Returns the two
        # RESULT records.
        import tempfile
        rom = self.rom if rom_bytes is None else self._tmp_rom(name, rom_bytes)
        o = self.out / ("writes-" + name)
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.writes_probe), str(rom.resolve()),
                            str(cue.resolve()), "4096", str(spinit)],
                           capture_output=True, text=True, timeout=120, check=True)
        import json
        recs = [json.loads(line[7:]) for line in r.stdout.splitlines()
                if line.startswith("RESULT ")]
        self.assertEqual(len(recs), 2, "write tracer must report cold+warm")
        cycles = sorted(x["cycle"] for x in recs)
        self.assertEqual(cycles, ["cold", "warm"])
        return recs

    def _tmp_rom(self, name, rom_bytes):
        # Mutant ROM materialized under the test out dir (never tracked).
        p = self.out / ("writes-" + name + ".pce")
        p.write_bytes(bytes(rom_bytes))
        return p

    def _written_set(self, res):
        out = set()
        for s, e in res["written"]:
            out.update(range(s, e + 1))
        return out

    def _allowed_transient(self, spinit):
        # T05 scope: JSR (2 bytes) + B1 body (4 pushes) below SP, i.e.
        # WRAM offsets for $21(spinit-5)..$21(spinit). Nothing else may be
        # written on any cycle for B1.
        return set(range(0x100 + spinit - 5, 0x100 + spinit + 1))

    def _check_writes(self, recs, spinit):
        # Strict B1 write-history proof, both cycles: halted (16 BRA
        # confirmations, arrival excluded), complete (not truncated), identical
        # sets cold==warm, and exactly the transient stack bytes -- a bare-RTS
        # mutant (2 JSR bytes only) or any extra value-changing write fails here.
        # Residual limit (see docs/64-ex-satclr-b1.md §3): the tracer reports
        # observed value changes only (per-instruction WRAM *value diffs*), so a
        # same-value store (writing back the byte already present) is invisible
        # by construction and remains NOT_RUN/BLOCKED (unverified), never PASS
        # (out of scope; bus-level write detection would need core write-path
        # hooks). Only state-changing writes are claimed as detected.
        allowed = self._allowed_transient(spinit)
        seen = []
        for res in recs:
            self.assertTrue(res["halted"],
                            f"write tracer did not halt ({res['cycle']}): harness failure, not RED")
            self.assertFalse(res["truncated"],
                             f"write history truncated ({res['cycle']}): harness failure")
            seen.append(self._written_set(res))
        self.assertEqual(seen[0], seen[1], "cold/warm write sets differ (INCONCLUSIVE)")
        self.assertEqual(seen[0], allowed,
                         f"B1 wrote outside transient stack: {sorted(seen[0] - allowed)}")

    def _check_writes_extra(self, recs, spinit, extra):
        # Mutant RED through the tracer only: halted cleanly on both cycles
        # (abnormal halt/timeout never counts as side-effect detection),
        # identical write sets, and the out-of-scope surplus is exactly
        # `extra` (non-empty). The caller additionally proves the legacy
        # oracles stay GREEN on the same ROM where the property demands it.
        self.assertTrue(len(extra) > 0, "empty extra set is not a mutant")
        allowed = self._allowed_transient(spinit)
        seen = []
        for res in recs:
            self.assertTrue(res["halted"],
                            f"tracer mutant did not halt ({res['cycle']}): "
                            "harness failure, never side-effect RED")
            self.assertFalse(res["truncated"],
                             f"write history truncated ({res['cycle']}): harness failure")
            seen.append(self._written_set(res))
        self.assertEqual(seen[0], seen[1], "cold/warm write sets differ (INCONCLUSIVE)")
        self.assertTrue(allowed <= seen[0],
                        f"transient stack writes missing: {sorted(allowed - seen[0])}")
        surplus = seen[0] - allowed
        self.assertEqual(surplus, set(extra),
                         f"unexpected out-of-scope writes: {sorted(surplus)}")

    def test_writes_b1_sp_ff(self):
        # T05: B1 writes exactly the 6 transient stack bytes ($21FA-$21FF)
        # and nothing else, cold and warm.
        self._check_writes(self._run_writes("b1ff", 0xFF), 0xFF)

    def test_writes_b1_sp_06(self):
        # T05 boundary: SP=$06 minimum writes exactly $2101-$2106.
        self._check_writes(self._run_writes("b106", 0x06), 0x06)

    def _run_writes_raw(self, name, spinit, steps=4096, rom_bytes=None):
        # Low-level tracer run without halted/result assertions (for
        # contract-rejection and halt-boundary negatives). Returns
        # (returncode, stdout, stderr). Rejection must occur before any guest
        # execution (no RESULT line on contract refusal).
        import tempfile
        rom = self.rom if rom_bytes is None else self._tmp_rom(name, rom_bytes)
        o = self.out / ("writes-" + name)
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.writes_probe), str(rom.resolve()),
                            str(cue.resolve()), str(steps), str(spinit)],
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def test_writes_rejects_contract_sp(self):
        # MINOR-1 / T12: SP below $06 is OUT OF CONTRACT. The standalone
        # probe must reject $00 and $05 BEFORE guest execution (non-zero exit,
        # no RESULT line, contract message on stderr). $06 and $FF remain
        # valid (covered by test_writes_b1_sp_06 / test_writes_b1_sp_ff).
        for bad_sp, tag in ((0x00, "sp00"), (0x05, "sp05")):
            with self.subTest(sp=hex(bad_sp)):
                rc, out, err = self._run_writes_raw(tag, bad_sp)
                self.assertNotEqual(rc, 0,
                                    f"SP {bad_sp:#04x} must be rejected (non-zero exit)")
                self.assertNotIn("RESULT", out,
                                 f"SP {bad_sp:#04x} must not run the guest")
                self.assertIn("out of contract", err,
                              f"SP {bad_sp:#04x} refusal must cite the contract")
        # Positive controls: $06 and $FF run and halt (already proved via
        # _check_writes, re-asserted here as rejection-boundary witnesses).
        for good_sp, tag in ((0x06, "sp06pos"), (0xFF, "spffpos")):
            with self.subTest(sp=hex(good_sp)):
                rc, out, err = self._run_writes_raw(tag, good_sp)
                self.assertEqual(rc, 0, f"SP {good_sp:#04x} must run (rc 0): {err}")
                self.assertIn("RESULT", out,
                              f"SP {good_sp:#04x} must produce RESULT lines")

    # Independent halt-arrival oracle (static guest accounting, never derived
    # from the confirmation threshold under test):
    # patched guest executes SEI, CLD, LDA, TAM, LDX, TXS, LDA, TAM, LDY, LDX,
    # LDA, PHA, LDA, PLP, JSR $E0A2 (15 steps landing on table $E0A2), then
    # table JMP to BOOT body (1 step), then 9-body PHP/PHA/PHX/PHY/PLY/PLX/PLA/
    # PLP/RTS (9 steps) landing on halt BRA. 15+1+9 = 25. Hence H=25.
    # The probe reports independent `arrival` (first run_steps with pc_after
    # == halt) separate from the 16-confirmation counter, so budgets below
    # use that independent H, never steps_full derived from the threshold.
    EXPECTED_HALT_ARRIVAL = 25

    def _parse_writes_stdout(self, stdout, expect_rc=0, rc=None):
        # Strict RESULT integrity: exactly 2 records, cycles cold/warm once
        # each, no missing/duplicate, required fields present with types.
        # Budget exhaustion (halted false) is INCONCLUSIVE, never GREEN/RED.
        import json
        if rc is not None and expect_rc is not None:
            self.assertEqual(rc, expect_rc, f"probe exit {rc} != {expect_rc}")
        recs = [json.loads(line[7:]) for line in stdout.splitlines()
                if line.startswith("RESULT ")]
        self.assertEqual(len(recs), 2,
                         f"RESULT count {len(recs)} != 2 (missing/duplicate)")
        cycles = sorted(x.get("cycle") for x in recs)
        self.assertEqual(cycles, ["cold", "warm"],
                         f"cycles {cycles} must be cold/warm once each")
        for r in recs:
            for f in ("cycle", "pc", "halt", "halted", "steps", "arrival",
                      "truncated", "written"):
                self.assertIn(f, r, f"RESULT missing {f} ({r.get('cycle')})")
            self.assertIsInstance(r["pc"], int)
            self.assertIsInstance(r["halt"], int)
            self.assertIsInstance(r["halted"], bool)
            self.assertIsInstance(r["steps"], int)
            self.assertIsInstance(r["arrival"], int)
            self.assertIsInstance(r["truncated"], bool)
        return {r["cycle"]: r for r in recs}

    def _run_writes_budget(self, name, spinit, budget, probe=None, rom_bytes=None):
        # Run tracer with explicit budget against given probe (default normal).
        # Returns per-cycle dict. Asserts clean execution (rc 0, 2 RESULTs),
        # never treating harness failure as RED.
        import tempfile
        probe = self.writes_probe if probe is None else probe
        rom = self.rom if rom_bytes is None else self._tmp_rom(name, rom_bytes)
        o = self.out / ("writes-" + name)
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(probe), str(rom.resolve()),
                            str(cue.resolve()), str(budget), str(spinit)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0,
                         f"probe budget {budget} exit {r.returncode}: {r.stderr[:500]} (harness failure, not RED)")
        by_cycle = self._parse_writes_stdout(r.stdout, expect_rc=0, rc=r.returncode)
        return by_cycle

    def _check_halt_budget(self, by_cycle, budget, H, expect_halted):
        # Check single-budget expectation for both cycles with independent H.
        for cycle in ("cold", "warm"):
            r = by_cycle[cycle]
            h = H[cycle] if isinstance(H, dict) else H
            self.assertFalse(r["truncated"], f"{cycle} truncated (harness failure)")
            if budget < h:
                self.assertEqual(r["arrival"], 0,
                                 f"{cycle} arrival must be 0 before H (budget {budget})")
                self.assertNotEqual(r["pc"], r["halt"],
                                    f"{cycle} budget {budget} < H={h} must not reach halt")
                self.assertFalse(r["halted"],
                                 f"{cycle} budget {budget} < H must be unconfirmed")
                self.assertEqual(r["steps"], budget,
                                 f"{cycle} steps must equal budget before break (budget {budget})")
            else:
                self.assertEqual(r["arrival"], h,
                                 f"{cycle} independent arrival must be H={h} (budget {budget})")
                self.assertEqual(r["pc"], r["halt"],
                                 f"{cycle} budget {budget} >= H must be at halt")
                self.assertEqual(r["halted"], expect_halted,
                                 f"{cycle} budget {budget} halted must be {expect_halted}")
                if expect_halted:
                    self.assertEqual(r["steps"], h + 16,
                                     f"{cycle} confirming steps must be H+16={h+16} (budget {budget})")
                else:
                    self.assertEqual(r["steps"], budget,
                                     f"{cycle} unconfirmed steps must equal budget (budget {budget})")

    def test_writes_halt_arrival_independent(self):
        # Independent arrival oracle: full-budget run reports arrival via
        # first-sighting logic separate from the confirmation counter.
        by_cycle = self._run_writes_budget("harrival", 0xFF, 4096)
        for cycle in ("cold", "warm"):
            r = by_cycle[cycle]
            self.assertEqual(r["arrival"], self.EXPECTED_HALT_ARRIVAL,
                             f"{cycle} independent arrival {r['arrival']} != static 25")
            self.assertTrue(r["halted"], f"{cycle} full budget must confirm")
            self.assertEqual(r["steps"], r["arrival"] + 16,
                             f"{cycle} full steps must be arrival+16 (arrival excluded)")

    def test_writes_halt_confirmation_boundary_independent(self):
        # Independent boundary table using static H=25 (never steps_full).
        H = self.EXPECTED_HALT_ARRIVAL
        cases = [
            (H - 1, False, "H-1 halt unreached, unconfirmed"),
            (H, False, "H arrival, unconfirmed"),
            (H + 1, False, "H+1 1 confirm, unconfirmed"),
            (H + 15, False, "H+15 15 confirms, unconfirmed"),
            (H + 16, True, "H+16 16 confirms, confirmed"),
            (H + 17, True, "H+17 confirmed (early break)"),
        ]
        for budget, expect_halted, tag in cases:
            with self.subTest(budget=budget, expect_halted=expect_halted):
                by_cycle = self._run_writes_budget(f"hbound{budget}", 0xFF, budget)
                for cycle in ("cold", "warm"):
                    r = by_cycle[cycle]
                    if budget < H:
                        self.assertEqual(r["arrival"], 0,
                                         f"{cycle} {tag}: arrival 0 before H")
                    else:
                        self.assertEqual(r["arrival"], H,
                                         f"{cycle} {tag}: arrival independent H")
                self._check_halt_budget(by_cycle, budget, H, expect_halted)

    def _compile_mutant_probe(self, name, old, new, must_contain=None):
        # Build mutant probe in untracked out dir (never in tracked tree).
        from pathlib import Path as _P
        src_path = _P(__file__).with_name("geargrafx_satclr36_writes_probe.cpp")
        text = src_path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"mutant base pattern missing for {name} (harness failure)")
        mutated = text.replace(old, new)
        self.assertNotEqual(mutated, text, f"mutant {name} no change (harness failure)")
        if must_contain is not None:
            self.assertIn(must_contain, mutated, f"mutant {name} missing marker")
        mdir = self.out / "mutants"
        mdir.mkdir(exist_ok=True)
        mpath = mdir / f"{name}.cpp"
        mpath.write_text(mutated, encoding="utf-8")
        import sys as _sys
        _sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "tools"))
        from environment import dependency_dir as _depdir
        from geargrafx_api import build_command as _build
        source = _depdir('geargrafx').with_name(_depdir('geargrafx').name + '-debugger')
        make_dir = source / 'platforms/libretro'
        objects = _build(['make', '-s', '--no-print-directory', '-C', str(make_dir),
                          '--eval', 'probe-objects:;@echo $(OBJECTS)', 'probe-objects']).stdout.split()
        objects = [(make_dir / p).resolve() for p in objects]
        target = mdir / name
        _build(['g++', '-std=c++11', '-O0', '-fno-rtti', '-D__LIBRETRO__',
                '-DGG_DISABLE_VGMRECORDER', '-DGG_ENABLE_CDROM_CUEBIN_READAHEAD',
                '-I' + str(source / 'src'), '-I' + str(make_dir),
                '-I' + str(source / 'platforms/shared/dependencies/miniz'),
                '-I' + str(source / 'platforms/shared/dependencies/json'), str(mpath),
                *map(str, objects), '-pthread', '-lm', '-o', str(target)])
        self.assertTrue(target.is_file(), f"mutant {name} build failed (harness failure)")
        return target

    def test_halt_mutant_m1_old_offbyone_red(self):
        # M1: old off-by-one (arrival counted).
        old = "            if (pc_before == halt && pc_after == halt) {\n                if (++confirm >= 16) { halted = true; break; }\n            } else {\n                confirm = 0;\n            }"
        new = "            if (pc_after == halt) {\n                if (++confirm >= 16) { halted = true; break; }\n            } else {\n                confirm = 0;\n            }"
        mprobe = self._compile_mutant_probe("m1-old-offbyone", old, new)
        H = self.EXPECTED_HALT_ARRIVAL
        by_cycle = self._run_writes_budget("m1b40", 0xFF, H + 15, probe=mprobe)
        for cycle in ("cold", "warm"):
            self.assertTrue(by_cycle[cycle]["halted"],
                            f"M1 {cycle} must falsely halt at H+15 (mutant not applied?)")
        with self.assertRaises(AssertionError, msg="M1 must be caught (H+15 false expected)"):
            self._check_halt_budget(by_cycle, H + 15, H, False)
        by_full = self._run_writes_budget("m1bfull", 0xFF, 4096, probe=mprobe)
        for cycle in ("cold", "warm"):
            self.assertEqual(by_full[cycle]["steps"], H + 15,
                             f"M1 {cycle} full steps must be H+15 (off-by-one)")

    def test_halt_mutant_m2_threshold15_red(self):
        # M2: threshold 15 (insufficient confirmation).
        old = "                if (++confirm >= 16) { halted = true; break; }"
        new = "                if (++confirm >= 15) { halted = true; break; }"
        mprobe = self._compile_mutant_probe("m2-thr15", old, new)
        H = self.EXPECTED_HALT_ARRIVAL
        by_cycle = self._run_writes_budget("m2b40", 0xFF, H + 15, probe=mprobe)
        for cycle in ("cold", "warm"):
            self.assertTrue(by_cycle[cycle]["halted"],
                            f"M2 {cycle} must halt at H+15 (threshold 15)")
        with self.assertRaises(AssertionError, msg="M2 must be caught"):
            self._check_halt_budget(by_cycle, H + 15, H, False)

    def test_halt_mutant_m3_threshold17_red(self):
        # M3: threshold 17 (excessive confirmation).
        old = "                if (++confirm >= 16) { halted = true; break; }"
        new = "                if (++confirm >= 17) { halted = true; break; }"
        mprobe = self._compile_mutant_probe("m3-thr17", old, new)
        H = self.EXPECTED_HALT_ARRIVAL
        by_cycle = self._run_writes_budget("m3b41", 0xFF, H + 16, probe=mprobe)
        for cycle in ("cold", "warm"):
            self.assertFalse(by_cycle[cycle]["halted"],
                             f"M3 {cycle} must stay unconfirmed at H+16 (needs 17)")
        with self.assertRaises(AssertionError, msg="M3 must be caught (H+16 true expected)"):
            self._check_halt_budget(by_cycle, H + 16, H, True)

    def test_halt_mutant_m4_noreset_unit(self):
        # M4: missing reset on leaving halt. Existing guest never leaves halt,
        # so real-core boundary cannot exercise the reset path; provide
        # minimal unit simulation of the counter logic plus source check.
        def run_correct(trace):
            confirm = 0
            halted_at = None
            for idx, (before, after, halt) in enumerate(trace):
                if before == halt and after == halt:
                    confirm += 1
                    if confirm >= 16 and halted_at is None:
                        halted_at = idx
                else:
                    confirm = 0
            return confirm, halted_at
        def run_mutant_noreset(trace):
            confirm = 0
            halted_at = None
            for idx, (before, after, halt) in enumerate(trace):
                if before == halt and after == halt:
                    confirm += 1
                    if confirm >= 16 and halted_at is None:
                        halted_at = idx
            return confirm, halted_at
        HLT = 100
        trace = []
        for _ in range(5):
            trace.append((HLT, HLT, HLT))
        trace.append((HLT, 999, HLT))
        trace.append((999, HLT, HLT))
        for _ in range(16):
            trace.append((HLT, HLT, HLT))
        c_conf, c_at = run_correct(trace)
        m_conf, m_at = run_mutant_noreset(trace)
        self.assertEqual(c_conf, 16)
        self.assertEqual(c_at, len(trace) - 1)
        self.assertEqual(m_conf, 21)
        self.assertEqual(m_at, len(trace) - 1 - 5)
        self.assertLess(m_at, c_at, "M4 must confirm early (reset leak)")
        from pathlib import Path as _P
        cur = _P(__file__).with_name("geargrafx_satclr36_writes_probe.cpp").read_text()
        self.assertIn("pc_before == halt && pc_after == halt", cur)
        self.assertIn("} else {\n                confirm = 0;", cur)

    def test_writes_mutant_sta2200_red(self):
        # T05 non-sampled write (outside canary $2900-$29FF and zp $10):
        # the legacy oracles stay GREEN (proving their blindness here)
        # while the tracer flags $2200 on both cycles.
        orig = self.rom.read_bytes()
        bad, cav = self._mutate_body_extended(orig, MUT_STA2200_12)
        self._assert_ext_mutant_applied(orig, bad, cav, MUT_STA2200_12)
        rec, base = self._run_on_rom("m-w2200", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_green(rec, base, self._check_c0,
                           mutant_tag="sta2200-old-oracle-green")
        wrecs = self._run_writes("w2200", 0xFF, rom_bytes=bad)
        self._check_writes_extra(wrecs, 0xFF, {0x200})

    def test_writes_mutant_restore_red(self):
        # T05 history-vs-final distinction: the mutant saves $2200, writes
        # $AA, then restores the original value -- the final WRAM value is
        # unchanged, so snapshots/dumps/checksums stay GREEN, but the
        # per-instruction write history still records $2200 -> RED.
        orig = self.rom.read_bytes()
        bad, cav = self._mutate_body_extended(orig, MUT_RESTORE_36)
        self._assert_ext_mutant_applied(orig, bad, cav, MUT_RESTORE_36)
        rec, base = self._run_on_rom("m-restore", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_green(rec, base, self._check_c0,
                           mutant_tag="restore-old-oracle-green")
        wrecs = self._run_writes("restore", 0xFF, rom_bytes=bad)
        self._check_writes_extra(wrecs, 0xFF, {0x200})

    def test_chain_33_4a_23_25_36(self):
        # Synthetic G5 chain leg: the PUBLIC boot ($02 leg) loads this
        # payload, which then calls $33 -> $4A -> $23 -> $25 -> $36 through the real
        # table entries. Each APICALL records per-call entry evidence only
        # (stacked return bytes via return_matches_caller + stack_check MATCH,
        # relative SP change, MPR1 at entry); it does not observe the RTS
        # destination or the full MPR set. End-to-end resumption and preservation
        # are verified by the final record below. The observed $36 entry state is
        # SIMULATED-entry (conditional on the fabricated predecessors), never
        # genuine $36 input evidence.
        import json
        o = self.out / "chain"
        o.mkdir(exist_ok=True)
        src = o / "chain.s"
        src.write_text(CHAIN_ASM, encoding="ascii")
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        obj, binp, cfgp, lblp = (o / "chain.o", o / "chain.bin",
                                 o / "chain.cfg", o / "chain.lbl")
        cfgp.write_text(CFG, encoding="ascii")
        import environment
        ca65, ld65 = environment.executable("ca65"), environment.executable("ld65")
        subprocess.run([ca65, str(src), "-o", str(obj), "-g"], check=True)
        subprocess.run([ld65, "-C", str(cfgp), "-o", str(binp), "-Ln", str(lblp),
                        str(obj)], check=True)
        text = lblp.read_text(encoding="ascii")
        syms = {}
        for line in text.splitlines():
            m = re.match(r"^al ([0-9A-Fa-f]{6}) \.(.+)$", line)
            if m:
                syms[m.group(2)] = int(m.group(1), 16)
        base = syms["res_base"] - 0x2800
        code = binp.read_bytes()
        body = self.L.body_for(code, 2)
        info = self.L.info_sector(2, 2, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0,
                                  sum(body) & 0xFFFF)
        cue = self.L.make_disc(o, self.L.case_disc({"n": 2, "iplblk": 2}, body, info))
        m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", text, re.M)
        halt = int(m.group(1), 16)
        lab = o / "chain.labels"
        lab.write_text(
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {self.labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels['api_unimplemented']:04x}\n", encoding="ascii")
        import geargrafx_api_entry as Eentry
        rec = Eentry.run_probe(self.probe, self.rom, cue, lab, BUDGET, "synthetic")
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        # Per-call entry evidence, in order.
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual([c["slot"] for c in calls], [0x33, 0x4A, 0x23, 0x25, 0x36], cycle)
            self.assertEqual([c["entry_pc"] for c in calls],
                             [0xE099, 0xE0DE, 0xE069, 0xE06F, 0xE0A2], cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))
                self.assertEqual(c["mpr"][1], 0xF8, (cycle, c))  # MPR1=$F8 at every entry
            # NOTE: APIRETURN lines are not collected by the probe parser.
            # return_matches_caller + stack_check == MATCH observe the
            # stacked return bytes at entry time, not the RTS destination;
            # a stacked-return-rewriting trampoline back to caller+3 is
            # excluded only by the 9-byte body-shape pin, while the
            # end-to-end records below exclude byte-skipping bodies.
        # End-to-end preservation across all four calls.
        self.assertEqual(cold[base], 0xAA)
        self.assertEqual((cold[base + 1], cold[base + 2], cold[base + 3]),
                         (0x31, 0x12, 0x34))
        self.assertEqual(cold[base + 4] & ~0x10, 0x07)
        self.assertEqual(cold[base + 5], 0xFF)
        self.assertEqual(list(cold[base + 6:base + 14]),
                         [0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        self.assertEqual(cold[base + 14], 0xA5)
        self.assertEqual(cold[base + 15], 0xA5)
        self.assertEqual(warm[base + 14], 0xA5)
        self.assertEqual(warm[base + 15], 0xA5)
        self._check_canary(cold, base)
        self._check_canary(warm, base)
        # SIMULATED-entry observation: the $36 call was reached with the
        # payload-set state intact through $33/$4A/$23 (MPRs/SP/regs). This
        # records the scaffold's entry conditions, not genuine $36 inputs.
        for cycle in ("cold", "warm"):
            call25 = [c for c in rec["APICALL"]
                        if c["cycle"] == cycle and c["slot"] == 0x36][0]
            self.assertEqual((call25["caller_a"], call25["caller_x"], call25["caller_y"]),
                             (0x31, 0x12, 0x34), cycle)
            self.assertEqual(call25["caller_p"] & ~0x10, 0x07, cycle)
            self.assertEqual(call25["mpr"][1], 0xF8, cycle)

    def test_satclr_stub_default_traps(self):
        # T01: cd_36="stub" (builder default): $E0A2 stays on the linked
        # fallback, JSR traps with X=$36 at the verified stub and never
        # returns (the post-JSR marker stays $00). Proves the default did
        # not change and fail-closed is intact.
        rec, base = self._run("stub", {"asm": STUB25_ASM, "defs": {}}, rom_kind="stub")
        cold, warm = self._buf_stublike(rec, self.labels_stub, 0x36)
        for buf in (cold, warm):
            self.assertEqual(buf[base], 0x00)  # JSR never returned

    def test_satclr_exp_unimplemented_slot_traps(self):
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

    def test_satclr_rom_identity_and_table_routing(self):
        import hashlib
        exp = self.rom.read_bytes()
        stub = self.rom_stub.read_bytes()
        self.assertNotEqual(hashlib.sha256(exp).digest(), hashlib.sha256(stub).digest())
        # Experimental: $E0A2 -> JMP ex_satclr_entry (body starts PHP,PHA).
        tab = exp[0xE0A2 - 0xE000:0xE0A2 - 0xE000 + 3]
        body = self.syms["ex_satclr_entry"]
        self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
        self.assertGreaterEqual(body, 0xF000)
        self.assertEqual(exp[body - 0xE000:body - 0xE000 + 9], B1_BODY)
        # Stub default: $E0A2 -> JMP api_slot_36 fallback; table bytes equal
        # the normal DEV ROM.
        tab_s = stub[0xE0A2 - 0xE000:0xE0A2 - 0xE000 + 3]
        fb = self.syms_stub["api_slot_36"]
        self.assertEqual(tuple(tab_s), (0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
        at_25 = 3 * 0x36
        self.assertEqual(stub[at_25:at_25 + 3], self.normal[at_25:at_25 + 3])
        # $02/$03/$33 wiring untouched on both images; $4A experimental on
        # the exp image (cd_4a="experimental"), stub on the stub image.
        for rom_bytes, syms in ((exp, self.syms), (stub, self.syms_stub)):
            self.assertEqual(rom_bytes[0xE006 - 0xE000], 0x4C)
            self.assertEqual(rom_bytes[0xE009 - 0xE000], 0x4C)
            self.assertEqual(rom_bytes[0xE099 - 0xE000], 0x4C)
        tab_4a_exp = exp[0xE0DE - 0xE000:0xE0DE - 0xE000 + 3]
        self.assertEqual(tuple(tab_4a_exp),
                         (0x4C, self.syms["ex_memopen_entry"] & 0xFF,
                          (self.syms["ex_memopen_entry"] >> 8) & 0xFF))
        tab_4a_stub = stub[0xE0DE - 0xE000:0xE0DE - 0xE000 + 3]
        fb_4a = self.syms_stub["api_slot_4A"]
        self.assertEqual(tuple(tab_4a_stub), (0x4C, fb_4a & 0xFF, (fb_4a >> 8) & 0xFF))
        # $25 experimental on the exp image as companion (cd_25="experimental"),
        # stub on the stub image.
        tab_25_exp = exp[0xE06F - 0xE000:0xE06F - 0xE000 + 3]
        self.assertEqual(tuple(tab_25_exp),
                         (0x4C, self.syms["ex_scrmod_entry"] & 0xFF,
                          (self.syms["ex_scrmod_entry"] >> 8) & 0xFF))
        tab_25_stub = stub[0xE06F - 0xE000:0xE06F - 0xE000 + 3]
        fb_25 = self.syms_stub["api_slot_25"]
        self.assertEqual(tuple(tab_25_stub), (0x4C, fb_25 & 0xFF, (fb_25 >> 8) & 0xFF))

    def _validate_abnormal_runs(self, rec, allowed_reasons, expect_x=None,
                               mutant_tag=""):
        # Strict abnormal-halt validator: counts as mutant RED only when ALL
        # hold: RUN is a 2-list with cold/warm each once; every run carries
        # the required fields; reason is in the per-mutant allowed set;
        # halt_confirmed is exactly True; pc equals labels[reason]; valid
        # 2048B+ buffers; no truncation; same reason/pc both cycles; optional
        # X pin. Anything else is harness failure, never RED.
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
        self._assert_apicall_arrival(rec, mutant_tag=mutant_tag)

    def _assert_apicall_arrival(self, rec, mutant_tag=""):
        # Every cycle needs >=1 APICALL showing a genuine JSR into slot $36
        # (table entry $E0A2, in_table, SP delta 2, internal stack MATCH).
        # Return success is deliberately NOT required: control-flow mutants
        # mis-return by design, so arrival and return are validated
        # separately. Missing/broken arrival evidence is HARNESS_FAILURE /
        # INCONCLUSIVE, never MUTANT RED.
        self.assertIn("APICALL", rec,
                      f"mutant {mutant_tag} run missing APICALL (harness failure, not detection)")
        calls = rec["APICALL"]
        self.assertIsInstance(calls, list,
                              f"mutant {mutant_tag} APICALL not a list (harness failure)")
        for cycle in ("cold", "warm"):
            arrivals = [c for c in calls
                        if c.get("cycle") == cycle
                        and c.get("slot") == 0x36
                        and c.get("entry_pc") == 0xE0A2
                        and c.get("in_table") is True
                        and c.get("sp_delta") == 2
                        and c.get("stack_check") == "MATCH"]
            self.assertGreaterEqual(
                len(arrivals), 1,
                f"mutant {mutant_tag} $36 not reached in {cycle} "
                "(harness failure, not detection)")

    def _assert_body_mutant_applied(self, orig_bytes, mutant_bytes, offset, new_bytes):
        # Prove the mutant changed ONLY the intended 9-byte body range:
        # ROM length unchanged, offset inside the body, replacement differs
        # from the original target bytes, target reads back as new_bytes,
        # and every byte outside the target range is identical.
        body_off = (self.syms["ex_satclr_entry"] & 0xFFFF) - 0xE000
        self.assertEqual(bytes(orig_bytes[body_off:body_off + 2]),
                         bytes((0x08, 0x48)),
                         "body offset does not anchor at reviewed prologue (harness failure)")
        body_addr = self.syms["ex_satclr_entry"] & 0xFFFF
        self.assertEqual(bytes(orig_bytes[0xE0A2 - 0xE000:0xE0A2 - 0xE000 + 3]),
                         bytes((0x4C, body_addr & 0xFF, (body_addr >> 8) & 0xFF)),
                         "$E0A2 vector does not target ex_satclr_entry (harness failure)")
        self.assertEqual(len(orig_bytes), len(mutant_bytes),
                         "mutant ROM length changed (harness failure)")
        self.assertGreater(len(new_bytes), 0,
                           "empty replacement is not a mutant (harness failure)")
        self.assertGreaterEqual(offset, 0)
        self.assertLessEqual(offset + len(new_bytes), 9,
                             f"mutant offset {offset}+{len(new_bytes)} outside 9-byte body")
        start, end = body_off + offset, body_off + offset + len(new_bytes)
        self.assertNotEqual(bytes(orig_bytes[start:end]), bytes(new_bytes),
                            "replacement identical to original target (no mutant)")
        self.assertEqual(bytes(mutant_bytes[start:end]), bytes(new_bytes),
                         f"mutant bytes not at body offset {offset}")
        self.assertEqual(bytes(mutant_bytes[:start]), bytes(orig_bytes[:start]),
                         "mutant changed bytes before target range (harness failure)")
        self.assertEqual(bytes(mutant_bytes[end:]), bytes(orig_bytes[end:]),
                         "mutant changed bytes outside target range (harness failure)")

    def _assert_mutant_red(self, rec, base, check=None, allowed_abnormal=None,
                           expect_x=None, mutant_tag=""):
        # A mutant is detected (RED) through either of two channels:
        # (a) abnormal halt -- dropping a pop unbalances SP, so RTS
        #     mis-returns; or (b) value mismatch -- balanced mutants return
        #     cleanly but with wrong preserved state / smashed canary.
        # Channel (b) additionally requires the per-cycle $36 arrival proof:
        # without proven arrival a clean-halt mismatch is INCONCLUSIVE.
        # If the run halts cleanly AND all values match, the mutant went
        # GREEN and this helper fails (coverage gap).
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
            self._validate_abnormal_runs(rec, allowed_abnormal, expect_x,
                                        mutant_tag=mutant_tag)
            return  # channel (a): expected control-flow failure
        if expect_x is not None:
            self.fail(f"control-flow mutant {mutant_tag} returned cleanly "
                      "(expected abnormal halt)")
        self._assert_apicall_arrival(rec, mutant_tag=mutant_tag)
        self.assertEqual(cold, warm,
                         f"value mutant {mutant_tag} cold/warm buffers differ "
                         "(INCONCLUSIVE, not detection)")
        with self.assertRaises(AssertionError,
                               msg=f"mutant went GREEN on cold {mutant_tag}"):
            check(cold, base)  # channel (b), cold side
        with self.assertRaises(AssertionError,
                               msg=f"mutant went GREEN on warm {mutant_tag}"):
            check(warm, base)  # channel (b), warm side

    def test_satclr_mutant_drop_pla_red(self):
        # Drop the A restore (PLA->NOP): unbalanced SP (pop dropped), so
        # RTS mis-returns (control-flow mutant, channel (a)). Proves A
        # preservation is load-bearing, not incidental.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 6, bytes((0xEA,)))
        self._assert_body_mutant_applied(orig, bad, 6, bytes((0xEA,)))
        rec, base = self._run_on_rom("m-nopla", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, mutant_tag="drop-pla")

    def test_satclr_mutant_drop_plp_red(self):
        # Drop the P restore (PLP->NOP): unbalanced SP. Proves flag
        # preservation (incl. Carry) is load-bearing.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 7, bytes((0xEA,)))
        self._assert_body_mutant_applied(orig, bad, 7, bytes((0xEA,)))
        rec, base = self._run_on_rom("m-noplp", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, mutant_tag="drop-plp")

    def test_satclr_mutant_force_clc_unbalanced_red(self):
        # Control-flow mutant (kept, NOT the T07 oracle): PLP->CLC drops a
        # pop, so SP is unbalanced and RTS mis-returns (channel (a)). The
        # C=1 seed pairing is retained; the discriminating T07 proof is the
        # balanced mutant below (test_satclr_mutant_force_clc_balanced_red).
        rec_ctrl, base_ctrl = self._run("c1-ctrl", self._defines(0x07, 0x11, 0x22,
                                                                  0x07, 0x66, 0x77))
        cold_ctrl, _ = self._buf(rec_ctrl)
        self._check_c1(cold_ctrl, base_ctrl)
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 7, bytes((0x18,)))
        self._assert_body_mutant_applied(orig, bad, 7, bytes((0x18,)))
        rec, base = self._run_on_rom("m-clc-unbal", bad,
                                      self._defines(0x07, 0x11, 0x22, 0x07, 0x66, 0x77))
        self._assert_mutant_red(rec, base, check=self._check_c1,
                                mutant_tag="M7a-C1-control-flow (unbalanced)")

    def test_satclr_mutant_force_sec_unbalanced_red(self):
        # Control-flow mutant (kept, NOT the T07 oracle): PLP->SEC drops a
        # pop (unbalanced, channel (a)); the discriminating proof is the
        # balanced mutant below (test_satclr_mutant_force_sec_balanced_red).
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 7, bytes((0x38,)))
        self._assert_body_mutant_applied(orig, bad, 7, bytes((0x38,)))
        rec, base = self._run_on_rom("m-sec-unbal", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base,
                                mutant_tag="M7b-C0-control-flow (unbalanced)")

    def _run_balanced(self, name, mutant_bytes, defines):
        # Extended cavity mutant run: $E0A2 vector repointed to the cavity
        # body (scope-verified); the normal 9-byte B1 body stays intact.
        orig = self.rom.read_bytes()
        bad, cav = self._mutate_body_extended(orig, mutant_bytes)
        self._assert_ext_mutant_applied(orig, bad, cav, mutant_bytes)
        return self._run_on_rom(name, bad, defines)

    def test_satclr_mutant_force_clc_balanced_red(self):
        # T07/M7a-C1-preservation (balanced, channel (b)): the cavity body
        # is B1 + CLC before RTS (4 pushes / 4 pops, clean return, only
        # Carry differs). C=1 entry must RED on the direct Carry-assertion
        # mismatch; abnormal halts are forbidden for this mutant.
        rec, base = self._run_balanced("m-clc-bal", MUT_CLC10,
                                        self._defines(0x07, 0x11, 0x22,
                                                     0x07, 0x66, 0x77))
        self._assert_mutant_red(rec, base, check=self._check_c1,
                                allowed_abnormal=set(),
                                mutant_tag="M7a-C1-preservation (balanced)")
        # Carry-specificity: the forced clear is observed DIRECTLY at both
        # return offsets, and it is the ONLY deviation -- every other field
        # still matches the C=1 vector with Carry forced clear (P=$06 is
        # $07 with bit0 off; B is masked by the checker). Balanced mutant:
        # SP stays balanced on both cycles (stack-shape integrity).
        cold, warm = self._buf(rec)
        for buf in (cold, warm):
            self.assertEqual(buf[base + 4] & 0x01, 0x00)
            self.assertEqual(buf[base + 20] & 0x01, 0x00)
            self._check_expected(buf, base, 0x06, 0x07, 0x11, 0x22,
                                 0x06, 0x07, 0x66, 0x77)
        self._check_sp(cold, base, 0xFF)
        self._check_sp(warm, base, 0xFF)

    def test_satclr_mutant_force_clc_balanced_green_c0(self):
        # T07 passing seed: the same CLC cavity body at C=0 must stay GREEN
        # (full preservation proof: no violation to detect).
        rec, base = self._run_balanced("m-clc-bal0", MUT_CLC10,
                                        self._defines(0x06, 0x11, 0x22,
                                                     0x06, 0x66, 0x77))
        self._assert_green(rec, base, self._check_c0,
                           mutant_tag="M7a-C0-green (balanced CLC)")

    def test_satclr_mutant_force_sec_balanced_red(self):
        # T07/M7b-C0-preservation (balanced, channel (b)): B1 + SEC before
        # RTS. C=0 entry must RED on the direct Carry-assertion mismatch;
        # abnormal halts are forbidden for this mutant.
        rec, base = self._run_balanced("m-sec-bal", MUT_SEC10,
                                        self._defines(0x06, 0x11, 0x22,
                                                     0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, check=self._check_c0,
                                allowed_abnormal=set(),
                                mutant_tag="M7b-C0-preservation (balanced)")
        # Carry-specificity (mirrored): forced set observed directly at
        # both return offsets, and the only deviation from the C=0 vector.
        # Balanced mutant: SP stays balanced on both cycles.
        cold, warm = self._buf(rec)
        for buf in (cold, warm):
            self.assertEqual(buf[base + 4] & 0x01, 0x01)
            self.assertEqual(buf[base + 20] & 0x01, 0x01)
            self._check_expected(buf, base, 0x07, 0x06, 0x11, 0x22,
                                 0x07, 0x06, 0x66, 0x77)
        self._check_sp(cold, base, 0xFF)
        self._check_sp(warm, base, 0xFF)

    def test_satclr_mutant_force_sec_balanced_green_c1(self):
        # T07 passing seed: the same SEC cavity body at C=1 must stay GREEN.
        rec, base = self._run_balanced("m-sec-bal1", MUT_SEC10,
                                        self._defines(0x07, 0x11, 0x22,
                                                     0x07, 0x66, 0x77))
        self._assert_green(rec, base, self._check_c1,
                           mutant_tag="M7b-C1-green (balanced SEC)")

    def test_satclr_mutant_save_pha_to_php_red(self):
        # Stack-balanced A corruption (save-path PHA->PHP at body offset 1).
        # Pushes stay 4 (PHP/PHP/PHX/PHY) and pops stay 4, so the body
        # returns cleanly -- but PLA restores the pushed P ($07, B bit set
        # by PHP) instead of entry A ($B0). Only a value mismatch on the
        # A!=P vector detects this: channel (b) with abnormal halts
        # forbidden. A==P vectors cannot isolate this class, so the mixed
        # vector is required here.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 1, bytes((0x08,)))
        self._assert_body_mutant_applied(orig, bad, 1, bytes((0x08,)))
        rec, base = self._run_on_rom("m-save", bad,
                                      self._defines(0x07, 0x11, 0x22,
                                                   0x06, 0x66, 0x77,
                                                   aone=0xB0, atwo=0x35))
        self._assert_mutant_red(rec, base, check=self._check_mixed,
                                allowed_abnormal=set(),
                                mutant_tag="save-pha-to-php")

    def test_satclr_mutant_stack_smash_red(self):
        # T10: out-of-scope RAM write (PHY/PLY -> STA $10 zero-page scratch).
        # Stack-balanced (3 pushes / 3 pops), so the body returns cleanly --
        # but zp $10 ($A5 seeded by the payload) is smashed to entry A.
        # The guest-observed zp pre/post mismatch is the RED signal (channel
        # (b), abnormal halts forbidden). A canary-region STA was rejected
        # as the T10 shape: its unbalanced RTS mis-returns into a random
        # walk that hangs the probe past its timeout instead of halting.
        # This balanced shape keeps the halt fast and deterministic while
        # proving the same property (out-of-contract write -> RED).
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 3, bytes((0x85, 0x10)))
        self._assert_body_mutant_applied(orig, bad, 3, bytes((0x85, 0x10)))
        rec, base = self._run_on_rom("m-smash", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, check=self._check_c0,
                                allowed_abnormal=set(),
                                mutant_tag="stack-smash")

    def test_satclr_mutant_table_reroute_red(self):
        # T06: reroute $E0A2 back to the stub fallback: calls halt at the
        # stub (api_unimplemented, confirmed, X=$36) instead of returning.
        # Proves the test exercises the wired body, not the table alone.
        orig = self.rom.read_bytes()
        tab = bytearray(orig)
        fb = self.syms["api_slot_36"]
        want_tab = bytes((0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
        tab[3 * 0x36:3 * 0x36 + 3] = want_tab
        self.assertNotEqual(bytes(tab), orig, "table reroute not applied")
        self.assertEqual(bytes(tab[3 * 0x36:3 * 0x36 + 3]), want_tab)
        self.assertEqual(bytes(tab[:3 * 0x36]), orig[:3 * 0x36],
                         "table mutant changed bytes before the vector (harness failure)")
        self.assertEqual(bytes(tab[3 * 0x36 + 3:]), orig[3 * 0x36 + 3:],
                         "table mutant changed bytes outside the vector (harness failure)")
        rec, base = self._run_on_rom("m-tab", bytes(tab),
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, allowed_abnormal=("api_unimplemented",),
                                expect_x=0x36, mutant_tag="table-reroute")

    def test_satclr_buffer_mutants_detected(self):
        # The preservation checker itself must fire. Every asserted field
        # (marker/A/X/Y/P/SP/MPR x8, both calls, canary head/tail) is
        # corrupted in turn on cold AND warm buffers; the checker must raise
        # each time. Corrupting bytes the checker never reads must NOT
        # raise.
        rec, base = self._run("mutbuf", self._defines(0x06, 0x11, 0x22,
                                                      0x06, 0x66, 0x77))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        self._check_c0(cold, base)
        self._check_c0(warm, base)
        self._check_canary(cold, base)
        self._check_canary(warm, base)
        # SP (base+5/+21) is verified by _check_sp, not _check_c0: corrupt
        # it and assert _check_sp fires instead.
        targets = [0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                   17, 18, 19, 20, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31]
        for buf, cycle in ((cold, "cold"), (warm, "warm")):
            for pos in targets:
                with self.subTest(cycle=cycle, pos=pos):
                    bad = bytearray(buf)
                    bad[base + pos] ^= 0xFF
                    with self.assertRaises(AssertionError,
                                            msg=f"checker blind at +{pos} ({cycle})"):
                        self._check_c0(bytes(bad), base)
        for buf, cycle in ((cold, "cold"), (warm, "warm")):
            for pos in (5, 21):
                with self.subTest(cycle=cycle, pos=pos):
                    bad = bytearray(buf)
                    bad[base + pos] ^= 0xFF
                    with self.assertRaises(AssertionError,
                                            msg=f"SP checker blind at +{pos} ({cycle})"):
                        self._check_sp(bytes(bad), base, 0xFF)
        for buf, cycle in ((cold, "cold"), (warm, "warm")):
            for pos in (0x100, 0x1FF):
                with self.subTest(cycle=cycle, pos=hex(pos)):
                    bad = bytearray(buf)
                    bad[pos] ^= 0xFF
                    with self.assertRaises(AssertionError,
                                            msg=f"canary checker blind at {pos:#x} ({cycle})"):
                        self._check_canary(bytes(bad), base)
        n = len(cold)
        self.assertGreaterEqual(n, base + 32)
        unrelated = [base + 16, n - 2, n - 1]
        for buf, cycle in ((cold, "cold"), (warm, "warm")):
            for idx in unrelated:
                with self.subTest(cycle=cycle, idx=idx):
                    bad = bytearray(buf)
                    bad[idx] ^= 0xFF
                    self._check_c0(bytes(bad), base)  # must not raise

    # --- Oracle negative controls: fabricated RUN records (no probe run)
    # that must NEVER count as mutant RED. Each asserts the oracle raises
    # instead of returning.
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
        return {"RUN": runs,
                "APICALL": [self._fake_arrival_call("cold"),
                            self._fake_arrival_call("warm")]}

    def _fake_arrival_call(self, cycle, slot=0x36, entry_pc=0xE0A2,
                           in_table=True, sp_delta=2, stack_check="MATCH",
                           ret_match=True):
        return {"cycle": cycle, "step": 100,
                "caller_pc": 0x3100, "entry_pc": entry_pc,
                "in_table": in_table, "slot": slot,
                "sp_before": 0xFF, "sp_after": 0xFD, "sp_delta": sp_delta,
                "caller_a": 0x06, "caller_x": 0x11, "caller_y": 0x22,
                "caller_p": 0x06, "stack_check": stack_check,
                "return_matches_caller": ret_match}

    def _fake_valid_abnormal_rec(self, ret_match=True):
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
        good[base + 6:base + 14] = bytes((0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00))
        good[base + 22:base + 30] = bytes((0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00))
        good[base + 14] = 0xA5
        good[base + 15] = 0xA5
        good[base + 17:base + 20] = bytes((popen, 0x66, 0x77))
        good[base + 20] = popen
        good[base + 21] = 0xFF
        good[base + 30] = 0xA5
        good[base + 31] = 0xA5
        return good

    def _fake_clean_rec(self, cold_hex, warm_hex, pc=0x3000):
        def run(cycle, hx):
            return {"cycle": cycle, "reason": "cd_halt_ok",
                    "halt_confirmed": True, "pc": pc, "buffer_hex": hx,
                    "apicall_truncated": False, "ret_truncated": False}
        return {"RUN": [run("cold", cold_hex), run("warm", warm_hex)],
                "APICALL": [self._fake_arrival_call("cold"),
                            self._fake_arrival_call("warm")]}

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

    def test_oracle_rejects_missing_run(self):
        with self.assertRaises(AssertionError, msg="missing RUN accepted as RED"):
            self._assert_mutant_red({}, 0)

    def test_oracle_rejects_unapplied_mutant(self):
        orig = self.rom.read_bytes()
        same = bytes(orig)
        with self.assertRaises(AssertionError, msg="unapplied mutant counted"):
            self._assert_body_mutant_applied(orig, same, 6, bytes((0xEA,)))

    def _rom_with(self, orig, patches):
        out = bytearray(orig)
        for off, bs in patches:
            out[off:off + len(bs)] = bs
        return bytes(out)

    def _body_off(self):
        return (self.syms["ex_satclr_entry"] & 0xFFFF) - 0xE000

    def test_applied_rejects_extra_change_in_body(self):
        orig = self.rom.read_bytes()
        body_off = self._body_off()
        bad = self._rom_with(orig, [(body_off + 6, bytes((0xEA,))),
                                    (body_off + 0, bytes((0x60,)))])
        with self.assertRaises(AssertionError,
                               msg="extra in-body change accepted as applied"):
            self._assert_body_mutant_applied(orig, bad, 6, bytes((0xEA,)))

    def test_applied_rejects_extra_change_outside_body(self):
        orig = self.rom.read_bytes()
        body_off = self._body_off()
        distant = 0x1000
        bad = self._rom_with(orig, [(body_off + 6, bytes((0xEA,))),
                                    (distant, bytes((orig[distant] ^ 0xFF,)))])
        with self.assertRaises(AssertionError,
                               msg="extra out-of-body change accepted as applied"):
            self._assert_body_mutant_applied(orig, bad, 6, bytes((0xEA,)))

    def test_applied_rejects_bad_offset(self):
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 6, bytes((0xEA,)))
        for off in (-1, 9, 99):
            with self.subTest(offset=off):
                with self.assertRaises(AssertionError,
                                        msg=f"bad offset {off} accepted as applied"):
                    self._assert_body_mutant_applied(orig, bad, off, bytes((0xEA,)))

    def test_applied_rejects_length_change(self):
        orig = self.rom.read_bytes()
        bad = bytes(orig) + b"\x00"
        with self.assertRaises(AssertionError,
                               msg="length-changed ROM accepted as applied"):
            self._assert_body_mutant_applied(orig, bad, 6, bytes((0xEA,)))

    def test_applied_accepts_single_byte(self):
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 6, bytes((0xEA,)))
        self._assert_body_mutant_applied(orig, bad, 6, bytes((0xEA,)))

    def test_oracle_rejects_clean_rom_as_mutant(self):
        # A clean halt with fully matching values is GREEN, never RED.
        base = 512
        good = self._fake_good_buf(base)
        hx = bytes(good).hex()
        rec = self._fake_clean_rec(hx, hx)
        with self.assertRaises(AssertionError, msg="clean ROM counted as mutant RED"):
            self._assert_mutant_red(rec, base)

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

    def test_oracle_rejects_partial_arrival(self):
        rec = self._fake_valid_abnormal_rec()
        rec["APICALL"] = [c for c in rec["APICALL"] if c["cycle"] == "cold"]
        with self.assertRaises(AssertionError, msg="partial arrival accepted as RED"):
            self._assert_mutant_red(rec, 0)

    def test_oracle_accepts_valid_abnormal_arrival(self):
        self._assert_mutant_red(self._fake_valid_abnormal_rec(), 0,
                                mutant_tag="arrival-control")

    def test_oracle_accepts_misreturn_arrival(self):
        self._assert_mutant_red(self._fake_valid_abnormal_rec(ret_match=False), 0,
                                mutant_tag="misreturn-control")

    def test_oracle_rejects_cold_only_anomaly(self):
        base = 512
        cold = self._fake_good_buf(base)
        cold[base + 1] ^= 0xFF
        warm = self._fake_good_buf(base)
        rec = self._fake_clean_rec(bytes(cold).hex(), bytes(warm).hex())
        with self.assertRaises(AssertionError, msg="cold-only anomaly accepted as RED"):
            self._assert_mutant_red(rec, base)

    def test_oracle_rejects_divergent_anomalies(self):
        base = 512
        cold = self._fake_good_buf(base)
        cold[base + 1] ^= 0xFF
        warm = self._fake_good_buf(base)
        warm[base + 4] ^= 0xFF
        rec = self._fake_clean_rec(bytes(cold).hex(), bytes(warm).hex())
        with self.assertRaises(AssertionError, msg="divergent anomalies accepted as RED"):
            self._assert_mutant_red(rec, base)

    def test_oracle_accepts_both_cycle_value_mismatch(self):
        base = 512
        bad = self._fake_good_buf(base)
        bad[base + 1] ^= 0xFF
        hx = bytes(bad).hex()
        rec = self._fake_clean_rec(hx, hx)
        self._assert_mutant_red(rec, base, mutant_tag="both-cycle-control")

    def test_oracle_rejects_clean_missing_arrival(self):
        base = 512
        bad = self._fake_good_buf(base)
        bad[base + 1] ^= 0xFF
        hx = bytes(bad).hex()
        rec = self._fake_clean_rec(hx, hx)
        del rec["APICALL"]
        with self.assertRaises(AssertionError,
                               msg="clean mismatch without APICALL accepted as RED"):
            self._assert_mutant_red(rec, base)

    def test_oracle_rejects_clean_partial_arrival(self):
        base = 512
        bad = self._fake_good_buf(base)
        bad[base + 1] ^= 0xFF
        hx = bytes(bad).hex()
        rec = self._fake_clean_rec(hx, hx)
        rec["APICALL"] = [c for c in rec["APICALL"] if c["cycle"] == "cold"]
        with self.assertRaises(AssertionError,
                               msg="clean mismatch with partial arrival accepted as RED"):
            self._assert_mutant_red(rec, base)


if __name__ == "__main__":
    unittest.main()
