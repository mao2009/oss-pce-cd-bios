"""Synthetic real-core tests for the $24 EX_DOTMOD B1 preservation scaffold (Issue #46).

Proves the CURRENT experimental wiring end to end on synthetic media with the
pinned core: PUBLIC IPL boot (2-base builder, commercial FIRST default,
cd_4a+cd_25+cd_24="experimental") -> JSR $E06C (real table entry,
never direct-body JSR, never fake RTS) -> full A/X/Y/P(incl Carry)/MPR/SP
preservation -> RTS resumption -> payload halt. Companion coverage:

  stub default (cd_24="stub") keeps $E06C on the linked fallback (byte-equal
  to the normal DEV ROM) and traps with X=$24;
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
  $24, and never remaps first);
  SP depth (SP=$FF and boundary SP=$06 GREEN and balanced; below-minimum is
  out of contract and never run);
  synthetic chain $33->$4A->$23->$25->$24 through the real table entries with
  per-call APICALL evidence (the $02 leg is the PUBLIC boot itself; the
  observed $24 entry state is labeled SIMULATED-entry);
  refusal-path absence (B1 defines no refusal: T04/T08/T09 NOT_APPLICABLE),
  routing, 9-byte structural pin, and buffer negatives.

NOT a compatible System Card BIOS. NOT genuine ABI evidence (genuine $24
semantics stay UNKNOWN/BLOCKED; see docs/46-ex-dotmod-contract.md and
docs/46-ex-dotmod-b1.md). NOT DEV_CALLABLE. release_eligible=false.
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
# JSR the real $E06C table entry twice with different regs, record
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
    jsr $E06C
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
    jsr $E06C
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
    jsr $E06C
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
    jsr $E06C
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

# Synthetic G5 chain leg: $33 -> $4A -> $23 -> $25 -> $24 through the real table
# entries (the $02 leg is the PUBLIC boot that loads this payload). Entry
# regs are set once; every body preserves them, so the final record equals
# the entry state. Each APICALL's return_matches_caller + MATCH + SP balance
# observes the STACKED return bytes at table-entry time (before the body
# runs) -- not the RTS landing itself. The $24 9-byte body-shape pin (exact
# B1 bytes verified pre-run) excludes the stacked-return-rewriting trampoline
# class for the $24 call only -- it constrains no companion body ($33/$4A/
# $23/$25 keep their own contracts/pins, none established here); APIRETURN
# is not collected.
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
    jsr $E06C
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
# $E06C vector from an $FF cavity in BOOT padding, never in the normal ROM).
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
MUT_RESTORE_24 = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68,
                        0x48, 0xAD, 0x00, 0x22, 0x48, 0xA9, 0xAA, 0x8D,
                        0x00, 0x22, 0x68, 0x8D, 0x00, 0x22, 0x68, 0x28,
                        0x60))


class Dotmod24Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-24-")
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
        # Experimental image (cd_4a+cd_25+cd_24 experimental); stub default via
        # the builder defaults for the control comparisons.
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m24", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False,
            cd_4a="experimental", cd_25="experimental",
            cd_24="experimental")
        cls.rom_stub, cls.syms_stub = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m24-stub", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False)
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        for rom_bytes, symbols, addr, name in (
                (cls.rom.read_bytes(), cls.syms, cls.syms["ex_dotmod_entry"], "exp"),
                (cls.rom_stub.read_bytes(), cls.syms_stub, cls.syms_stub["api_slot_24"], "stub")):
            tab = rom_bytes[0xE06C - 0xE000:0xE06C - 0xE000 + 3]
            assert tuple(tab) == (0x4C, addr & 0xFF, (addr >> 8) & 0xFF), (name, addr)
        trace = cls.out / "ipl-loader-g5-m24.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        trace_stub = cls.out / "ipl-loader-g5-m24-stub.lbl"
        cls.labels_stub = parse_labels(
            trace_stub, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "m25-probe")
        cls.vdc_probe = compile_probe(cls.out, "geargrafx_dotmod24_probe.cpp", "m25-vdc-probe")
        cls.writes_probe = compile_probe(cls.out, "geargrafx_dotmod24_writes_probe.cpp",
                                         "m25-writes-probe")
        import geargrafx_ipl_loader as L
        cls.L = L

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tmp = cls._tmp.name
        assert "pce-24-" in tmp and tmp.startswith(tempfile.gettempdir())
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
            # The appended line OVERWRITES the loader cd_halt_fail mapping in
            # the probe's label table (later duplicate wins), so a loader-side
            # failure halt no longer matches any halt name and surfaces as a
            # budget/unconfirmed run -- never as this refusal. The refusal is
            # therefore identified by (reason cd_halt_fail + halt_confirmed +
            # pc == refused_halt symbol), which loader failures cannot mimic
            # (different pc, unconfirmed). The strict validator
            # (_check_mpr1_refusal_strict) enforces exactly that triple.
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
        # minimum for the 4-push body + 2 JSR bytes; below $06 is out of
        # contract and never run on the real core; the writes probe refuses
        # SP < $06 pre-execution). Verified P domain is T=0 (bit $20 clear);
        # T=1 entry is OUT OF CONTRACT (pinned-core T-consumption) and no
        # test passes T=1 seeds; PHP/PLP save is distinct from T-consumption
        # and no T-bit code is added.
        if aone is None:
            aone = popen
        if atwo is None:
            atwo = ptwo
        return {"asm": PAYLOAD_ASM,
                "defs": {"POPEN": popen, "AONE": aone, "XONE": xone, "YONE": yone,
                         "PTWO": ptwo, "ATWO": atwo, "XTWO": xtwo, "YTWO": ytwo,
                         "SPINIT": spinit}}

    def _mutate_body(self, rom_bytes, offset, new_bytes):
        # Patch the experimental $24 body in a ROM copy (mutants live in
        # /tmp-style out dirs, never in tracked files).
        body_off = (self.syms["ex_dotmod_entry"] & 0xFFFF) - 0xE000
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
        # Extended mutant body in a BOOT $FF cavity with the $E06C vector
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
        out[3 * 0x24:3 * 0x24 + 3] = bytes((0x4C, cav_addr & 0xFF,
                                            (cav_addr >> 8) & 0xFF))
        return bytes(out), cav

    def _assert_ext_mutant_applied(self, orig_bytes, mutant_bytes_rom, cavity_off,
                                   mutant_bytes):
        # Prove the extended mutant changed ONLY the $E06C vector plus a
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
        self.assertEqual(bytes(mutant_bytes_rom[3 * 0x24:3 * 0x24 + 3]),
                         bytes((0x4C, cav_addr & 0xFF, (cav_addr >> 8) & 0xFF)),
                         "$E06C vector does not target the cavity (harness failure)")
        body_off = (self.syms["ex_dotmod_entry"] & 0xFFFF) - 0xE000
        self.assertEqual(bytes(mutant_bytes_rom[body_off:body_off + 9]), B1_BODY,
                         "normal B1 body disturbed (harness failure)")
        expect = bytearray(orig_bytes)
        expect[cavity_off:cavity_off + size] = mutant_bytes
        expect[3 * 0x24:3 * 0x24 + 3] = bytes((0x4C, cav_addr & 0xFF,
                                               (cav_addr >> 8) & 0xFF))
        self.assertEqual(bytes(mutant_bytes_rom), bytes(expect),
                         "mutant changed bytes outside vector+cavity (harness failure)")

    def _assert_green(self, rec, base, check, spinit=0xFF, mutant_tag=""):
        # Full GREEN proof for a clean-halt run: halt shape, both-cycle
        # value match (incl. zp/canary via the checker), SP balance, and
        # proven $24 arrival. Used for passing-seed mutant runs (T07) and
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

    def test_dotmod_preserves_carry_set(self):
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

    def test_dotmod_preserves_carry_clear(self):
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

    def test_dotmod_preserves_ap_independent(self):
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
        self._check_sp(cold, base, 0xFF)
        self._check_sp(warm, base, 0xFF)
        self._check_canary(cold, base)
        self._check_canary(warm, base)

    def test_sp_only_corruption_detected(self):
        # SP-balance is load-bearing and invisible to _check_expected: with
        # every other field intact, corrupting only the recorded SP bytes
        # (base+5/+21) must still be caught -- by _check_sp, while the value
        # matcher stays GREEN. Proves the SP assertion is not redundant.
        # MINOR-1: boundary/A!=P vectors now carry _check_sp for the same
        # reason; this negative pins the detector itself.
        rec, base = self._run("sponly", self._defines(0x06, 0x11, 0x22,
                                                         0x06, 0x66, 0x77))
        cold, warm = self._buf(rec)
        self._check_c0(cold, base)  # control: values GREEN
        for buf, cycle in ((cold, "cold"), (warm, "warm")):
            for pos in (5, 21):
                with self.subTest(cycle=cycle, pos=pos):
                    bad = bytearray(buf)
                    bad[base + pos] ^= 0xFF
                    self._check_c0(bytes(bad), base)  # value matcher blind to SP
                    with self.assertRaises(AssertionError,
                                            msg=f"SP-only corruption missed ({cycle}+{pos})"):
                        self._check_sp(bytes(bad), base, 0xFF)

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

    def test_dotmod_apicall_slot_order(self):
        # T02: return mechanics. JSR through the real $E06C table entry
        # (3-byte JSR), RTS lands at caller+3, SP delta 2, MATCH, balanced.
        rec, base = self._run("order", self._defines(0x07, 0x11, 0x22,
                                                     0x06, 0x66, 0x77))
        cold, warm = self._buf(rec)  # recognized halt first; order alone must not pass a hang
        self.assertEqual(cold, warm)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            slots = [c["slot"] for c in calls]
            self.assertEqual(slots, [0x24, 0x24], cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["entry_pc"], 0xE06C, (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))
        # NOTE: the probe parser only collects the APICALL tag (APIRETURN
        # lines are not collected; the parser retains APICALL only).
        # return_matches_caller + stack_check ==
        # MATCH + SP balance observe the stacked return bytes at entry time,
        # before the body runs -- they are ENTRY evidence and do not observe
        # the RTS destination. Per-call return resumption is NOT claimed from
        # APICALL alone; return is proved end-to-end by the post-return
        # records + verified halt below, never per-call.
        # A $24 body that rewrites its stacked return to a trampoline landing
        # back at caller+3 would still MATCH; that class is excluded only by
        # the $24 9-byte structural pin (exact B1 bytes verified pre-run), not
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

    def test_dotmod_body_bytes_pinned(self):
        # Structural anti-fake pin (no probe run): the experimental body must
        # be exactly the reviewed 9-byte sequence. A bare-RTS substitution
        # (60 EA ...) would be GREEN on behavior in observed classes by
        # construction (RTS preserves everything when there is no work), so
        # the body shape itself is pinned here: the save/restore discipline
        # is load-bearing and must change only together with this test.
        exp = self.rom.read_bytes()
        body = self.syms["ex_dotmod_entry"] & 0xFFFF
        self.assertEqual(len(B1_BODY), 9)
        self.assertEqual(exp[body - 0xE000:body - 0xE000 + 9], B1_BODY)
        self.assertNotEqual(B1_BODY[0], 0x60)  # not a bare RTS

    def test_dotmod_boundary_values(self):
        # T11: byte-boundary preservation probe (B1 defines no reject code;
        # every value must be preserved exactly, never rejected). P domain
        # excludes T=1: the pinned core consumes the transfer flag every
        # clock (huc6280_inline.h Clock(): m_transfer_flag snapshot +
        # ClearFlag), so entry T=1 cannot round-trip on ANY implementation
        # (platform fact, not B1 behavior; documented in docs/46-ex-dotmod-b1.md).
        rec, base = self._run("bnd", self._defines(0x04, 0x00, 0xA5,
                                                     0x85, 0x80, 0x5A,
                                                     aone=0x00, atwo=0xFF))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        self._check_expected(cold, base, 0x04, 0x00, 0x00, 0xA5,
                             0x85, 0xFF, 0x80, 0x5A)
        self._check_expected(warm, base, 0x04, 0x00, 0x00, 0xA5,
                             0x85, 0xFF, 0x80, 0x5A)
        self._check_sp(cold, base, 0xFF)
        self._check_sp(warm, base, 0xFF)
        self._check_canary(cold, base)
        self._check_canary(warm, base)

    def test_dotmod_sp_boundary_minimum(self):
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
        # Below-minimum SP is OUT OF CONTRACT (T12): the harness never runs
        # it on the real core; the minimum is pinned here and in
        # docs/46-ex-dotmod-b1.md. SP<$06 would consume the caller's live
        # stack bytes and must never be reported GREEN.

    def _mpr1_refused_lbl_pc(self, name):
        # Refused-halt PC from the assembled payload .lbl (independent of the
        # probe labels file, whose cd_halt_fail entry is aliased). The strict
        # refusal validator compares the observed RUN pc against this symbol.
        text = (self.out / name / (name + ".lbl")).read_text(encoding="ascii")
        m = re.search(r"^al ([0-9A-Fa-f]{6}) \.refused_halt$", text, re.M)
        self.assertIsNotNone(m, "refused_halt symbol missing (harness failure)")
        return int(m.group(1), 16)

    def _check_apicall_record_strict(self, c, tag=""):
        # Strict per-record APICALL validation (MEDIUM-2). Every record the
        # parser yields is checked for presence, exact type (bool excluded
        # where ints are required) and range/consistency -- including {} or
        # records with missing/unknown cycle, missing slot/entry_pc, or a
        # slot/entry_pc contradiction. Filters downstream must never see an
        # unvalidated record: a malformed record passing a .get() filter is
        # a false negative, not an absence.
        self.assertIsInstance(c, dict,
                              f"refusal {tag} APICALL record not an object")
        for field in ("cycle", "step", "caller_pc", "entry_pc",
                      "in_table", "slot", "sp_before", "sp_after",
                      "sp_delta", "caller_a", "caller_x", "caller_y",
                      "caller_p", "stack_check", "return_matches_caller",
                      "mpr", "idr", "irr"):
            self.assertIn(field, c,
                          f"refusal {tag} APICALL record missing {field}")
        self.assertIn(c.get("cycle"), ("cold", "warm"),
                      f"refusal {tag} APICALL bad cycle {c.get('cycle')!r}")
        self.assertTrue(type(c.get("slot")) is int and 0 <= c["slot"] <= 0x51,
                        f"refusal {tag} APICALL bad slot {c.get('slot')!r}")
        self.assertTrue(type(c.get("entry_pc")) is int
                        and 0xE000 <= c["entry_pc"] <= 0xE0F2,
                        f"refusal {tag} APICALL bad entry_pc {c.get('entry_pc')!r}")
        self.assertIsInstance(c.get("in_table"), bool,
                              f"refusal {tag} APICALL in_table not bool")
        if c.get("in_table"):
            self.assertEqual((c["entry_pc"] - 0xE000) % 3, 0,
                             f"refusal {tag} APICALL entry_pc off-table")
            self.assertEqual(c["slot"], (c["entry_pc"] - 0xE000) // 3,
                             f"refusal {tag} APICALL slot/entry_pc mismatch")
        for field in ("sp_before", "sp_after", "sp_delta"):
            self.assertTrue(type(c.get(field)) is int
                            and 0 <= c[field] <= 0xFF,
                            f"refusal {tag} APICALL {field} bad")
        self.assertEqual((c["sp_before"] - c["sp_after"]) & 0xFF,
                         c["sp_delta"],
                         f"refusal {tag} APICALL sp_delta inconsistent")
        self.assertIn(c.get("stack_check"),
                      ("MATCH", "MISMATCH", "UNKNOWN"),
                      f"refusal {tag} APICALL bad stack_check")
        self.assertIn(c.get("return_matches_caller"), (True, False, None),
                      f"refusal {tag} APICALL bad return_matches_caller")
        for field in ("caller_a", "caller_x", "caller_y", "caller_p",
                      "idr", "irr"):
            self.assertTrue(type(c.get(field)) is int
                            and 0 <= c[field] <= 0xFF,
                            f"refusal {tag} APICALL {field} bad")
        for field in ("step", "caller_pc"):
            self.assertTrue(type(c.get(field)) is int and c[field] >= 0,
                            f"refusal {tag} APICALL {field} bad")
        mpr = c.get("mpr")
        self.assertIsInstance(mpr, list,
                              f"refusal {tag} APICALL mpr not a list")
        self.assertEqual(len(mpr), 8,
                         f"refusal {tag} APICALL mpr len != 8")
        for i, v in enumerate(mpr):
            self.assertTrue(type(v) is int and 0 <= v <= 0xFF,
                            f"refusal {tag} APICALL mpr[{i}] bad")

    def _check_mpr1_refusal_strict(self, rec, refused_pc, tag=""):
        # Strict T13 pre-JSR refusal validator. A refusal counts ONLY when
        # ALL hold on the raw RUN list (never a per-cycle dict, which would
        # silently collapse duplicate cycles): RUN is a 2-list with cold and
        # warm exactly once each; every run carries the required fields;
        # reason is cd_halt_fail; halt_confirmed is exactly True (strict
        # boolean, not truthy); apicall_truncated and ret_truncated are
        # exactly False (a truncated trace cannot prove no JSR); pc is an
        # int equal to the refused_halt symbol (loader failures halt
        # elsewhere and surface as budget/unconfirmed, never as this pc);
        # buffers are valid hex dumps. APICALL must be a list with zero
        # slot-$24 records in each cycle and zero $E06C entries overall.
        # Anything else is harness failure/INCONCLUSIVE, never a refusal.
        # APICALL completeness (MEDIUM-2): the probe emits an independent
        # per-cycle observed count (apicalls_observed, synthetic RUN field;
        # commercial serialization unchanged). The parser-derived per-cycle
        # count must match it with apicall_truncated False, so a dropped
        # emission stream cannot masquerade as zero events. Counts are never
        # derived from the parsed array itself.
        self.assertIn("RUN", rec, f"refusal {tag} missing RUN (harness failure)")
        runs = rec["RUN"]
        self.assertIsInstance(runs, list, f"refusal {tag} RUN not a list")
        self.assertEqual(len(runs), 2,
                         f"refusal {tag} RUN count != 2 (duplicate/missing/extra)")
        cycles = [r.get("cycle") for r in runs]
        self.assertEqual(sorted(cycles), ["cold", "warm"],
                         f"refusal {tag} cycles must be cold+warm once each, got {cycles}")
        self.assertIn("APICALL", rec,
                      f"refusal {tag} missing APICALL (harness failure)")
        calls = rec["APICALL"]
        self.assertIsInstance(calls, list,
                              f"refusal {tag} APICALL not a list (harness failure)")
        # Strict per-record validation BEFORE any filter: malformed records
        # ({} / missing keys / contradictions) must raise here, never pass
        # a .get() filter silently.
        for c in calls:
            self._check_apicall_record_strict(c, tag=tag)
        for r in runs:
            for field in ("cycle", "reason", "pc", "halt_confirmed",
                          "buffer_hex", "apicall_truncated", "ret_truncated",
                          "apicalls_observed"):
                self.assertIn(field, r,
                              f"refusal {tag} run missing {field} (harness failure)")
            self.assertEqual(r.get("reason"), "cd_halt_fail",
                             f"refusal {tag} unexpected reason {r.get('reason')}")
            self.assertIs(r.get("halt_confirmed"), True,
                          f"refusal {tag} halt not confirmed (harness failure)")
            self.assertIs(r.get("apicall_truncated"), False,
                          f"refusal {tag} APICALL truncated (trace gap, not proof)")
            self.assertIs(r.get("ret_truncated"), False,
                          f"refusal {tag} APIRETURN truncated (trace gap, not proof)")
            self.assertIsInstance(r.get("pc"), int,
                                  f"refusal {tag} pc not int (harness failure)")
            self.assertEqual(r.get("pc"), refused_pc,
                             f"refusal {tag} pc {r.get('pc'):04x} != refused_halt "
                             f"{refused_pc:04x} (loader failure/budget, not refusal)")
            try:
                buf = bytes.fromhex(r["buffer_hex"])
            except (TypeError, ValueError):
                self.fail(f"refusal {tag} buffer_hex invalid hex (harness failure)")
            self.assertGreaterEqual(len(buf), 2048,
                                     f"refusal {tag} buffer short (harness failure)")
            # Observed-vs-parsed independence: the count comes from the probe
            # (entry-watch hits), not from len(parsed). Both cycles observed
            # zero here -- genuine zero, not a missing stream.
            self.assertTrue(type(r["apicalls_observed"]) is int,
                            f"refusal {tag} apicalls_observed bad type")
            parsed_here = [c for c in calls
                           if c.get("cycle") == r.get("cycle")]
            self.assertEqual(len(parsed_here), r["apicalls_observed"],
                             f"refusal {tag} parsed/observed APICALL mismatch "
                             f"({r.get('cycle')}: parsed {len(parsed_here)} vs "
                             f"observed {r['apicalls_observed']})")
            self.assertEqual(r["apicalls_observed"], 0,
                             f"refusal {tag} observed APICALL (must be zero)")
        for cycle in ("cold", "warm"):
            bad = [c for c in calls
                   if c.get("cycle") == cycle and c.get("slot") == 0x24]
            self.assertEqual(bad, [],
                             f"MPR1!=$F8 reached $24 in {cycle} (must refuse BEFORE JSR)")
        creep = [c for c in calls if c.get("entry_pc") == 0xE06C]
        self.assertEqual(creep, [],
                         f"refusal {tag} reached $E06C (must refuse BEFORE JSR)")

    def test_mpr1_precondition_refused_before_jsr(self):
        # T13: the harness validates MPR1 BEFORE JSR $E06C. With MPR1=$FF
        # the harness refuses at refused_halt WITHOUT executing JSR: no
        # $24 APICALL exists in either cycle. Rationale: JSR itself writes
        # 2 return bytes through the MPR1 mapping before any body gate runs,
        # so a body-internal gate cannot make the call side-effect-free.
        # Strictness (MEDIUM-1): the RUN list is validated raw (never a
        # per-cycle dict, which collapses duplicates); truncations are
        # rejected (a gapped trace cannot prove no JSR); the halt pc must
        # equal the refused_halt symbol (loader failures halt elsewhere).
        rec, base = self._run("mpr1ref", {"asm": MPR1_ASM,
                                            "defs": {"BREAK_MPR1": 1}})
        refused_pc = self._mpr1_refused_lbl_pc("mpr1ref")
        self._check_mpr1_refusal_strict(rec, refused_pc, tag="mpr1ref")

    def _fake_refusal_run(self, cycle, pc, reason="cd_halt_fail", halt=True,
                          a_trunc=False, r_trunc=False, buf=None, observed=0):
        # Fabricated T13 refusal RUN record (no probe run). Defaults form a
        # valid refusal (zero observed APICALL, matching empty trace); each
        # negative flips exactly one property.
        if buf is None:
            buf = self._fake_buf_hex()
        return {"cycle": cycle, "reason": reason,
                "halt_confirmed": halt, "pc": pc,
                "buffer_hex": buf,
                "apicall_truncated": a_trunc, "ret_truncated": r_trunc,
                "apicalls_observed": observed}

    def _fake_strict_arrival(self, cycle, slot=0x24, entry_pc=0xE06C,
                             in_table=True):
        # Complete APICALL record as the real probe emits it (all structural
        # fields present). Negatives below mutate exactly one property.
        return {"cycle": cycle, "step": 100,
                "caller_pc": 0x3100, "entry_pc": entry_pc,
                "in_table": in_table, "slot": slot,
                "sp_before": 0xFF, "sp_after": 0xFD, "sp_delta": 2,
                "caller_a": 0x06, "caller_x": 0x11, "caller_y": 0x22,
                "caller_p": 0x06, "stack_check": "MATCH",
                "return_matches_caller": True,
                "mpr": [0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00],
                "idr": 0, "irr": 0}

    def _fake_refusal_rec(self, runs, apicalls=None):
        if apicalls is None:
            apicalls = []
        return {"RUN": runs, "APICALL": apicalls}

    def test_mpr1_refusal_rejects_cold_duplicate(self):
        # Dict-collapse guard: 3 RUN rows (cold, cold, warm) with valid
        # fields must NOT validate -- the old per-cycle-dict oracle collapsed
        # this to {cold, warm} and passed it. The strict list validator
        # rejects on the count.
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc),
            self._fake_refusal_run("cold", pc),
            self._fake_refusal_run("warm", pc),
        ])
        with self.assertRaises(AssertionError, msg="cold duplicate accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="dup-control")

    def test_mpr1_refusal_rejects_run_short(self):
        pc = 0x3100
        rec = self._fake_refusal_rec([self._fake_refusal_run("cold", pc)])
        with self.assertRaises(AssertionError, msg="short RUN accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="short-control")

    def test_mpr1_refusal_rejects_run_extra(self):
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc),
            self._fake_refusal_run("warm", pc),
            self._fake_refusal_run("warm", pc),
        ])
        with self.assertRaises(AssertionError, msg="extra RUN accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="extra-control")

    def test_mpr1_refusal_rejects_apicall_truncated(self):
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc, a_trunc=True),
            self._fake_refusal_run("warm", pc, a_trunc=True),
        ])
        with self.assertRaises(AssertionError, msg="truncated APICALL accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="atrunc-control")

    def test_mpr1_refusal_rejects_ret_truncated(self):
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc, r_trunc=True),
            self._fake_refusal_run("warm", pc, r_trunc=True),
        ])
        with self.assertRaises(AssertionError, msg="truncated APIRETURN accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="rtrunc-control")

    def test_mpr1_refusal_rejects_wrong_pc(self):
        # A halt at any other pc (e.g. the loader's own fail halt) is not
        # this refusal, even with the refusal reason string attached.
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc + 1),
            self._fake_refusal_run("warm", pc + 1),
        ])
        with self.assertRaises(AssertionError, msg="wrong-pc halt accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="pc-control")

    def test_mpr1_refusal_rejects_halt_unconfirmed(self):
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc, halt=False),
            self._fake_refusal_run("warm", pc, halt=False),
        ])
        with self.assertRaises(AssertionError, msg="unconfirmed halt accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="halt-control")

    def test_mpr1_refusal_rejects_missing_field(self):
        pc = 0x3100
        run = self._fake_refusal_run("cold", pc)
        del run["pc"]
        rec = self._fake_refusal_rec([run, self._fake_refusal_run("warm", pc)])
        with self.assertRaises(AssertionError, msg="missing field accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="field-control")
        with self.assertRaises(AssertionError, msg="missing APICALL accepted"):
            self._check_mpr1_refusal_strict({"RUN": [
                self._fake_refusal_run("cold", pc),
                self._fake_refusal_run("warm", pc)]}, pc, tag="noapicall-control")

    def test_mpr1_refusal_rejects_slot24_arrival(self):
        # A $24 APICALL in either cycle disproves pre-JSR refusal, even when
        # the RUN rows look like a clean refusal. Full records here so the
        # schema passes and the arrival filter itself must fire.
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc, observed=1),
            self._fake_refusal_run("warm", pc, observed=1),
        ], apicalls=[self._fake_strict_arrival("cold"),
                      self._fake_strict_arrival("warm")])
        with self.assertRaises(AssertionError, msg="$24 arrival accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="arrival-control")

    def test_mpr1_refusal_rejects_empty_record(self):
        # {} passes every .get() filter silently -- the strict per-record
        # schema must reject it instead.
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc),
            self._fake_refusal_run("warm", pc),
        ], apicalls=[{}])
        with self.assertRaises(AssertionError, msg="empty record accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="empty-control")

    def test_mpr1_refusal_rejects_bad_cycle(self):
        # Missing cycle and unknown cycle records must not validate.
        pc = 0x3100
        for mutate, label in ((lambda r: r.pop("cycle"), "missing-cycle"),
                              (lambda r: r.update(cycle="hot"), "unknown-cycle")):
            bad = self._fake_strict_arrival("cold")
            mutate(bad)
            rec = self._fake_refusal_rec([
                self._fake_refusal_run("cold", pc),
                self._fake_refusal_run("warm", pc),
            ], apicalls=[bad])
            with self.assertRaises(AssertionError, msg=f"{label} accepted"):
                self._check_mpr1_refusal_strict(rec, pc, tag=f"{label}-control")

    def test_mpr1_refusal_rejects_missing_slot_entry(self):
        pc = 0x3100
        for field in ("slot", "entry_pc"):
            bad = self._fake_strict_arrival("cold")
            del bad[field]
            rec = self._fake_refusal_rec([
                self._fake_refusal_run("cold", pc),
                self._fake_refusal_run("warm", pc),
            ], apicalls=[bad])
            with self.assertRaises(AssertionError, msg=f"missing {field} accepted"):
                self._check_mpr1_refusal_strict(rec, pc, tag=f"{field}-control")

    def test_mpr1_refusal_rejects_slot_entry_mismatch(self):
        # Slot says $24 but the entry address belongs to $02: contradictory
        # evidence must raise, never validate.
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc),
            self._fake_refusal_run("warm", pc),
        ], apicalls=[self._fake_strict_arrival("cold", slot=0x24,
                                                entry_pc=0xE00C)])
        with self.assertRaises(AssertionError, msg="slot/entry mismatch accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="mismatch-control")

    def test_mpr1_refusal_rejects_missing_count(self):
        # The observed-count field itself missing: parsed-vs-observed
        # independence is broken, so the refusal cannot validate.
        pc = 0x3100
        runs = [self._fake_refusal_run("cold", pc),
                self._fake_refusal_run("warm", pc)]
        for r in runs:
            del r["apicalls_observed"]
        rec = self._fake_refusal_rec(runs)
        with self.assertRaises(AssertionError, msg="missing count accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="count-missing-control")

    def test_mpr1_refusal_rejects_count_mismatch(self):
        # Probe observed one event but the trace carries none: dropped
        # emission stream, not zero events.
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc, observed=1),
            self._fake_refusal_run("warm", pc, observed=1),
        ])
        with self.assertRaises(AssertionError, msg="count mismatch accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="mismatch-count-control")

    def test_mpr1_refusal_rejects_bad_count_type(self):
        # Non-integer count (string/bool/float) is not a measurement.
        pc = 0x3100
        for bad_count in ("0", True, 0.0):
            runs = [self._fake_refusal_run("cold", pc),
                    self._fake_refusal_run("warm", pc)]
            for r in runs:
                r["apicalls_observed"] = bad_count
            rec = self._fake_refusal_rec(runs)
            with self.assertRaises(AssertionError,
                                   msg=f"bad count type accepted ({bad_count!r})"):
                self._check_mpr1_refusal_strict(rec, pc, tag="count-type-control")

    def _run_fake_probe(self, name, records):
        # Canned probe stdout through the REAL Eentry.run_probe parser
        # (tag partition, JSON decode, 2-RUN gate). The fake probe ignores
        # its argv and exits 0 after printing the given record dicts as
        # "TAG {json}" lines. `records` is a list of (tag, dict) pairs.
        import json
        lines = [f"{tag} {json.dumps(body, separators=(',', ':'))}\n"
                 for tag, body in records]
        script = self.out / (name + ".py")
        script.write_text("#!/usr/bin/env python3\nimport sys\n"
                          f"sys.stdout.write({''.join(lines)!r})\n",
                         encoding="ascii")
        script.chmod(0o755)
        import geargrafx_api_entry as Eentry
        return Eentry.run_probe(script, self.out / "dummy.rom",
                                self.out / "dummy.cue",
                                self.out / "dummy.labels", BUDGET,
                                "synthetic")

    def _fake_probe_run(self, cycle, pc, observed, reason="cd_halt_fail",
                        halt=True, a_trunc=False, r_trunc=False):
        return {"cycle": cycle, "reason": reason,
                "halt_confirmed": halt, "pc": pc,
                "buffer_hex": "ab" * 2048,
                "apicall_truncated": a_trunc, "ret_truncated": r_trunc,
                "apicalls_observed": observed}

    def test_mpr1_parser_zero_events_genuine(self):
        # Genuine zero through the real parser: 2 RUNs observed=0 and no
        # APICALL lines validate as a refusal (a missing stream could not
        # produce the observed==0 + parsed==0 agreement).
        pc = 0x3100
        rec = self._run_fake_probe("fp-zero", [
            ("RUN", self._fake_probe_run("cold", pc, 0)),
            ("RUN", self._fake_probe_run("warm", pc, 0)),
        ])
        self._check_mpr1_refusal_strict(rec, pc, tag="parser-zero")

    def test_mpr1_parser_dropped_event(self):
        # Warm emission line dropped: RUN says observed=1/1 but only the
        # cold line parses. The per-cycle count cross-check must fire.
        pc = 0x3100
        rec = self._run_fake_probe("fp-drop", [
            ("RUN", self._fake_probe_run("cold", pc, 1)),
            ("RUN", self._fake_probe_run("warm", pc, 1)),
            ("APICALL", self._fake_strict_arrival("cold")),
        ])
        with self.assertRaises(AssertionError, msg="dropped event accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="parser-drop")

    def test_mpr1_parser_cold_line_missing(self):
        # Cold line missing while cold observed=1: same asymmetry, cold side.
        pc = 0x3100
        rec = self._run_fake_probe("fp-coldmiss", [
            ("RUN", self._fake_probe_run("cold", pc, 1)),
            ("RUN", self._fake_probe_run("warm", pc, 0)),
            ("APICALL", self._fake_strict_arrival("warm", slot=0x02,
                                                    entry_pc=0xE006)),
        ])
        with self.assertRaises(AssertionError, msg="cold line gap accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="parser-coldmiss")

    def test_mpr1_parser_missing_count(self):
        # RUN rows without the observed-count field: independence broken.
        pc = 0x3100
        cold = self._fake_probe_run("cold", pc, 0)
        warm = self._fake_probe_run("warm", pc, 0)
        del cold["apicalls_observed"]
        del warm["apicalls_observed"]
        rec = self._run_fake_probe("fp-nocount", [("RUN", cold),
                                                     ("RUN", warm)])
        with self.assertRaises(AssertionError, msg="parser missing count accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="parser-nocount")

    def test_mpr1_parser_count_mismatch(self):
        # Observed=2 but a single line emitted: truncated/gapped stream.
        pc = 0x3100
        rec = self._run_fake_probe("fp-mismatch", [
            ("RUN", self._fake_probe_run("cold", pc, 2)),
            ("RUN", self._fake_probe_run("warm", pc, 2)),
            ("APICALL", self._fake_strict_arrival("cold")),
            ("APICALL", self._fake_strict_arrival("warm")),
        ])
        with self.assertRaises(AssertionError, msg="parser count mismatch accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="parser-mismatch")

    def test_mpr1_parser_bad_count_type(self):
        # String count straight from the parser: not a measurement.
        pc = 0x3100
        cold = self._fake_probe_run("cold", pc, 0)
        warm = self._fake_probe_run("warm", pc, 0)
        cold["apicalls_observed"] = "0"
        rec = self._run_fake_probe("fp-badcount", [("RUN", cold),
                                                      ("RUN", warm)])
        with self.assertRaises(AssertionError, msg="parser bad count accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="parser-badcount")

    def test_mpr1_parser_bad_json(self):
        # Malformed APICALL line: the parser itself must refuse the output.
        pc = 0x3100
        script = self.out / "fp-badjson.py"
        script.write_text("#!/usr/bin/env python3\nimport sys\n"
                          "sys.stdout.write('RUN {\"cycle\":\"cold\"}\\n')\n"
                          "sys.stdout.write('APICALL {oops\\n')\n",
                          encoding="ascii")
        script.chmod(0o755)
        import geargrafx_api_entry as Eentry
        with self.assertRaises(ValueError, msg="malformed JSON accepted"):
            Eentry.run_probe(script, self.out / "dummy.rom",
                             self.out / "dummy.cue",
                             self.out / "dummy.labels", BUDGET,
                             "synthetic")

    def test_mpr1_parser_empty_record(self):
        # "APICALL {}" through the real parser: the strict per-record
        # schema must reject it (missing cycle/slot/entry_pc).
        pc = 0x3100
        rec = self._run_fake_probe("fp-empty", [
            ("RUN", self._fake_probe_run("cold", pc, 1)),
            ("RUN", self._fake_probe_run("warm", pc, 0)),
            ("APICALL", {}),
        ])
        with self.assertRaises(AssertionError, msg="parser empty record accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="parser-empty")

    def test_mpr1_refusal_rejects_loader_budget(self):
        # Loader failure / step-budget exhaustion (reason budget,
        # unconfirmed) is never a pre-JSR refusal, even at the refused pc.
        pc = 0x3100
        rec = self._fake_refusal_rec([
            self._fake_refusal_run("cold", pc, reason="budget", halt=False),
            self._fake_refusal_run("warm", pc, reason="budget", halt=False),
        ])
        with self.assertRaises(AssertionError, msg="loader budget accepted"):
            self._check_mpr1_refusal_strict(rec, pc, tag="budget-control")

    def test_mpr1_precondition_positive_control(self):
        # T13 positive control: with MPR1=$F8 the same harness shape runs
        # JSR normally (GREEN preservation + canary). The $24 call shows
        # proven arrival (in_table, sp_delta 2, MATCH, return_matches_caller)
        # and balanced SP on both cycles -- the actual call-and-return path
        # the refusal case never takes.
        rec, base = self._run("mpr1ok", {"asm": MPR1_ASM,
                                           "defs": {"BREAK_MPR1": 0}})
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        self.assertEqual(cold[base], 0xAA)
        self.assertEqual((cold[base + 1], cold[base + 2], cold[base + 3]),
                         (0x3C, 0x11, 0x22))
        self.assertEqual(cold[base + 4] & ~0x10, 0x07)
        self.assertEqual(cold[base + 5], 0xFF)
        self.assertEqual(warm[base + 5], 0xFF)
        # Observed-vs-parsed independence on the positive path too: the RUN
        # rows carry the probe's own per-cycle entry count; it must equal
        # the parsed APICALL count for that cycle.
        runs_by_cycle = {r["cycle"]: r for r in rec["RUN"]}
        self.assertEqual(sorted(runs_by_cycle), ["cold", "warm"])
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"]
                       if c["cycle"] == cycle and c["slot"] == 0x24]
            self.assertEqual(len(calls), 1, cycle)
            self.assertEqual(calls[0]["entry_pc"], 0xE06C, cycle)
            self.assertEqual(calls[0].get("stack_check"), "MATCH", cycle)
            self.assertTrue(calls[0].get("return_matches_caller"), cycle)
            run = runs_by_cycle[cycle]
            self.assertIn("apicalls_observed", run, cycle)
            self.assertTrue(type(run["apicalls_observed"]) is int, cycle)
            parsed_cycle = [c for c in rec["APICALL"]
                            if c.get("cycle") == cycle]
            self.assertEqual(len(parsed_cycle), run["apicalls_observed"],
                             f"{cycle}: parsed/observed APICALL mismatch")
            for c in calls:
                self._check_apicall_record_strict(c, tag="mpr1ok")

    def _run_vdc(self, name, entry_ar, areg, xreg, pseed):
        # Dedicated VDC-invariance probe: guest sets AR, calls $E06C, halts.
        # C++ snapshots VDC R[0..19] + AR + MPRs + IDR/IRR per cycle (cold then
        # warm, ResetMedia between) with explicit cycle tags and a 16
        # start-and-end halt confirmation. Returns the two RESULT records;
        # budget exhaustion or an unconfirmed halt reports halted=false and
        # never counts as success (the checker requires halted true).
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
        recs = [json.loads(line[7:]) for line in r.stdout.splitlines()
                if line.startswith("RESULT ")]
        self._assert_vdc_pair(recs)
        return recs

    def _assert_vdc_pair(self, recs):
        # Cold+warm pair requirement: exactly 2 RESULT records, cold and
        # warm once each. A single cycle -- or a duplicated one -- is never
        # cold+warm evidence (harness failure, never GREEN). The halt address
        # must agree across the two independent executions: a self-consistent
        # but fabricated halt pc in one record cannot match the other.
        self.assertIsInstance(recs, list, "VDC records not a list")
        self.assertEqual(len(recs), 2, "VDC probe must report cold+warm")
        cycles = sorted(x["cycle"] for x in recs)
        self.assertEqual(cycles, ["cold", "warm"],
                         "VDC probe cycles must be cold+warm once each")
        by_cycle = {r["cycle"]: r for r in recs}
        self.assertEqual(by_cycle["cold"].get("halt"),
                         by_cycle["warm"].get("halt"),
                         "VDC cold/warm halt disagree (harness failure)")

    @staticmethod
    def _strict_int(v, lo, hi):
        # Strict integer: exact int type (bool excluded) within [lo, hi].
        # JSON truthiness or float coercion must never smuggle values in.
        return type(v) is int and lo <= v <= hi

    def _assert_vdc_schema(self, res, tag=""):
        # Fail-closed VDC record schema (MEDIUM-1). Every field the checker
        # reads is validated for presence, exact type (bool excluded) and
        # range BEFORE any value comparison: truthy impostors ("false", 1),
        # empty/truncated arrays ([] == [] would compare equal), short/long
        # arrays, mistyped elements and out-of-range registers must all
        # raise here instead of flowing into a vacuous GREEN.
        for field in ("cycle", "pc", "halt", "halted", "steps",
                      "a", "p", "x", "y", "sp",
                      "mpr_store", "mpr_before", "mpr_after",
                      "vdc_ar_before", "vdc_ar_after",
                      "vdc_r_before", "vdc_r_after",
                      "idr_before", "idr_after", "irr_before", "irr_after"):
            self.assertIn(field, res,
                          f"VDC record missing {field} ({tag}): harness failure")
        self.assertIn(res.get("cycle"), ("cold", "warm"),
                      f"VDC record bad cycle ({tag}): harness failure")
        # Exact boolean True: "false"/1/1.0 must not count as confirmed.
        self.assertIs(res.get("halted"), True,
                      f"VDC probe did not halt ({res.get('cycle')}): harness failure")
        # VDC register files: exactly 20 strict u16 each. [] == [] or two
        # equally truncated arrays must not compare equal here.
        for field in ("vdc_r_before", "vdc_r_after"):
            arr = res[field]
            self.assertIsInstance(arr, list,
                                  f"VDC {field} not a list ({tag})")
            self.assertEqual(len(arr), 20,
                             f"VDC {field} len {len(arr)} != 20 ({tag})")
            for i, v in enumerate(arr):
                self.assertTrue(self._strict_int(v, 0, 0xFFFF),
                                f"VDC {field}[{i}] bad type/range ({tag})")
        # MPR files: exactly 8 strict u8 each (store/before/after).
        for field in ("mpr_store", "mpr_before", "mpr_after"):
            arr = res[field]
            self.assertIsInstance(arr, list,
                                  f"MPR {field} not a list ({tag})")
            self.assertEqual(len(arr), 8,
                             f"MPR {field} len {len(arr)} != 8 ({tag})")
            for i, v in enumerate(arr):
                self.assertTrue(self._strict_int(v, 0, 0xFF),
                                f"MPR {field}[{i}] bad type/range ({tag})")
        # Scalars: strict u8 regs, AR 5-bit, IRQ bytes, int PC/halt/steps.
        # AR is the 5-bit VDC address register (0-31).
        for field, lo, hi in (("a", 0, 0xFF), ("p", 0, 0xFF),
                              ("x", 0, 0xFF), ("y", 0, 0xFF),
                              ("sp", 0, 0xFF),
                              ("vdc_ar_before", 0, 31),
                              ("vdc_ar_after", 0, 31),
                              ("idr_before", 0, 0xFF),
                              ("idr_after", 0, 0xFF),
                              ("irr_before", 0, 0xFF),
                              ("irr_after", 0, 0xFF)):
            self.assertTrue(self._strict_int(res[field], lo, hi),
                            f"VDC {field} bad type/range ({tag})")
        for field in ("pc", "halt"):
            self.assertTrue(self._strict_int(res[field], 0xE000, 0xFFFF),
                            f"VDC {field} outside HuCARD patch range ({tag})")
        # Step bounds: halted requires 16 start-and-end confirmations after
        # the arrival step, so a confirmed run is never shorter than 16.
        self.assertTrue(self._strict_int(res["steps"], 16, 65536),
                        f"VDC steps inconsistent with 16-confirmation ({tag})")

    def _check_vdc(self, res, entry_ar, areg, xreg, pseed):
        # T03/T05 VDC scope, single cycle: schema first (fail-closed types,
        # lengths, ranges, exact halted True), then halt/PC consistency
        # (pc == halt within the HuCARD patch range; halt agreement
        # cold-vs-warm is enforced by the pair validator), then AR unchanged
        # at the guest-set value, all 20 VDC registers unchanged against the
        # SAME-cycle baseline, regs/MPRs/IRQ state preserved. Baselines are
        # per-cycle: a cold/warm baseline difference is a platform reset
        # fact and is never compared across cycles here.
        tag = res.get("cycle")
        self._assert_vdc_schema(res, tag=tag)
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
        # No VDC write of any kind: AR still the guest-set value, every
        # register identical to the pre-run snapshot.
        self.assertEqual(res["vdc_ar_after"], entry_ar)
        self.assertEqual(res["vdc_r_before"], res["vdc_r_after"])
        # No IRQ side effects.
        self.assertEqual(res["idr_before"], res["idr_after"])
        self.assertEqual(res["irr_before"], res["irr_after"])

    def test_vdc_ar_unchanged_cr_family(self):
        for res in self._run_vdc("ar5", 5, 0x3C, 0x12, 0x07):
            self._check_vdc(res, 5, 0x3C, 0x12, 0x07)

    def test_vdc_ar_unchanged_mwr_family(self):
        for res in self._run_vdc("ar9", 9, 0x00, 0x80, 0x06):
            self._check_vdc(res, 9, 0x00, 0x80, 0x06)

    def test_vdc_ar_unchanged_high_select(self):
        for res in self._run_vdc("ar18", 18, 0xFF, 0x00, 0x07):
            self._check_vdc(res, 18, 0xFF, 0x00, 0x07)

    def test_vdc_ar_unchanged_zero_select(self):
        for res in self._run_vdc("ar0", 0, 0x5A, 0x34, 0x06):
            self._check_vdc(res, 0, 0x5A, 0x34, 0x06)

    def test_vdc_rejects_warm_only_violation(self):
        # Warm-only regression guard: a VDC change on warm alone must RED
        # while the cold record still passes -- per-cycle comparison, never
        # a cross-cycle baseline diff. Fabricated warm corruption (R0 +1)
        # must raise; the untouched cold record must pass.
        recs = self._run_vdc("ar5-neg", 5, 0x3C, 0x12, 0x07)
        by_cycle = {r["cycle"]: r for r in recs}
        self._check_vdc(by_cycle["cold"], 5, 0x3C, 0x12, 0x07)  # control passes
        import copy
        bad_warm = copy.deepcopy(by_cycle["warm"])
        bad_warm["vdc_r_after"] = list(bad_warm["vdc_r_after"])
        bad_warm["vdc_r_after"][0] = (bad_warm["vdc_r_after"][0] + 1) & 0xFFFF
        with self.assertRaises(AssertionError, msg="warm VDC violation missed"):
            self._check_vdc(bad_warm, 5, 0x3C, 0x12, 0x07)

    def test_vdc_rejects_missing_cycle(self):
        # Cold-only evidence is not cold+warm evidence: a single-cycle record
        # set -- or a duplicated cycle -- must be rejected by the pair
        # validator, never GREEN.
        recs = self._run_vdc("ar9-neg", 9, 0x00, 0x80, 0x06)
        with self.assertRaises(AssertionError, msg="cold-only accepted"):
            self._assert_vdc_pair([r for r in recs if r["cycle"] == "cold"])
        with self.assertRaises(AssertionError, msg="duplicate cycle accepted"):
            self._assert_vdc_pair([recs[0], dict(recs[0])])

    def test_vdc_rejects_unconfirmed_halt(self):
        # Budget exhaustion / unconfirmed halt is harness failure, never
        # VDC GREEN: halted=false must raise even when registers match.
        recs = self._run_vdc("ar0-neg", 0, 0x5A, 0x34, 0x06)
        import copy
        bad = copy.deepcopy(recs[0])
        bad["halted"] = False
        with self.assertRaises(AssertionError, msg="unconfirmed VDC halt accepted"):
            self._check_vdc(bad, 0, 0x5A, 0x34, 0x06)

    def _vdc_mutant(self, name, entry_ar, areg, xreg, pseed, mutate):
        # Real VDC record pair with one fabricated field mutation applied to
        # the cold record (warm stays genuine). Returns (good_cold, bad).
        # Every MEDIUM-1 negative below exercises the actual _check_vdc.
        import copy
        recs = self._run_vdc(name, entry_ar, areg, xreg, pseed)
        by_cycle = {r["cycle"]: r for r in recs}
        self._check_vdc(by_cycle["cold"], entry_ar, areg, xreg, pseed)
        self._check_vdc(by_cycle["warm"], entry_ar, areg, xreg, pseed)
        bad = copy.deepcopy(by_cycle["cold"])
        mutate(bad)
        return by_cycle["cold"], bad

    def test_vdc_rejects_halted_string(self):
        # Truthy impostor: JSON string "false" must not count as confirmed.
        _, bad = self._vdc_mutant("ar5-negstr", 5, 0x3C, 0x12, 0x07,
                                   lambda r: r.update(halted="false"))
        with self.assertRaises(AssertionError, msg="halted string accepted"):
            self._check_vdc(bad, 5, 0x3C, 0x12, 0x07)

    def test_vdc_rejects_halted_int(self):
        # Integer 1 is not the boolean True.
        _, bad = self._vdc_mutant("ar5-negint", 5, 0x3C, 0x12, 0x07,
                                   lambda r: r.update(halted=1))
        with self.assertRaises(AssertionError, msg="halted int accepted"):
            self._check_vdc(bad, 5, 0x3C, 0x12, 0x07)

    def test_vdc_rejects_missing_field(self):
        _, bad = self._vdc_mutant("ar5-negmiss", 5, 0x3C, 0x12, 0x07,
                                   lambda r: r.pop("vdc_r_after"))
        with self.assertRaises(AssertionError, msg="missing field accepted"):
            self._check_vdc(bad, 5, 0x3C, 0x12, 0x07)

    def test_vdc_rejects_empty_arrays(self):
        # [] == [] would compare equal without the exact-length gate.
        def wipe(r):
            r["vdc_r_before"] = []
            r["vdc_r_after"] = []
        _, bad = self._vdc_mutant("ar5-negempty", 5, 0x3C, 0x12, 0x07, wipe)
        with self.assertRaises(AssertionError, msg="empty VDC arrays accepted"):
            self._check_vdc(bad, 5, 0x3C, 0x12, 0x07)

    def test_vdc_rejects_short_long_arrays(self):
        # Equally truncated (19) or over-long (21) arrays must not compare.
        _, bad_short = self._vdc_mutant(
            "ar5-negshort", 5, 0x3C, 0x12, 0x07,
            lambda r: (r.__setitem__("vdc_r_before", r["vdc_r_before"][:19]),
                        r.__setitem__("vdc_r_after", r["vdc_r_after"][:19])))
        with self.assertRaises(AssertionError, msg="short VDC arrays accepted"):
            self._check_vdc(bad_short, 5, 0x3C, 0x12, 0x07)
        _, bad_long = self._vdc_mutant(
            "ar5-neglong", 5, 0x3C, 0x12, 0x07,
            lambda r: (r.__setitem__("vdc_r_before", r["vdc_r_before"] + [0]),
                        r.__setitem__("vdc_r_after", r["vdc_r_after"] + [0])))
        with self.assertRaises(AssertionError, msg="long VDC arrays accepted"):
            self._check_vdc(bad_long, 5, 0x3C, 0x12, 0x07)

    def test_vdc_rejects_mpr_empty_short_long(self):
        # Matching short MPR arrays (e.g. 2-element) must not compare equal.
        def empty(r):
            r["mpr_before"] = []
            r["mpr_after"] = []
            r["mpr_store"] = []
        _, bad = self._vdc_mutant("ar5-negmpre", 5, 0x3C, 0x12, 0x07, empty)
        with self.assertRaises(AssertionError, msg="empty MPR arrays accepted"):
            self._check_vdc(bad, 5, 0x3C, 0x12, 0x07)

        def short(r):
            r["mpr_before"] = r["mpr_before"][:7]
            r["mpr_after"] = r["mpr_after"][:7]
            r["mpr_store"] = r["mpr_store"][:7]
        _, bad = self._vdc_mutant("ar5-negmprs", 5, 0x3C, 0x12, 0x07, short)
        with self.assertRaises(AssertionError, msg="short MPR arrays accepted"):
            self._check_vdc(bad, 5, 0x3C, 0x12, 0x07)

        def long(r):
            r["mpr_before"] = r["mpr_before"] + [0xF8]
            r["mpr_after"] = r["mpr_after"] + [0xF8]
            r["mpr_store"] = r["mpr_store"] + [0xF8]
        _, bad = self._vdc_mutant("ar5-negmprl", 5, 0x3C, 0x12, 0x07, long)
        with self.assertRaises(AssertionError, msg="long MPR arrays accepted"):
            self._check_vdc(bad, 5, 0x3C, 0x12, 0x07)

    def test_vdc_rejects_bad_element_type_range(self):
        # String element, None element, bool element (bool is an int
        # subclass, must be excluded), and out-of-range values.
        _, bad_str = self._vdc_mutant(
            "ar5-negtype", 5, 0x3C, 0x12, 0x07,
            lambda r: r["vdc_r_after"].__setitem__(0, "0"))
        with self.assertRaises(AssertionError, msg="string element accepted"):
            self._check_vdc(bad_str, 5, 0x3C, 0x12, 0x07)
        _, bad_bool = self._vdc_mutant(
            "ar5-negbool", 5, 0x3C, 0x12, 0x07,
            lambda r: r["mpr_after"].__setitem__(0, True))
        with self.assertRaises(AssertionError, msg="bool element accepted"):
            self._check_vdc(bad_bool, 5, 0x3C, 0x12, 0x07)
        _, bad_range = self._vdc_mutant(
            "ar5-negrange", 5, 0x3C, 0x12, 0x07,
            lambda r: r["vdc_r_after"].__setitem__(0, 0x10000))
        with self.assertRaises(AssertionError, msg="out-of-range VDC accepted"):
            self._check_vdc(bad_range, 5, 0x3C, 0x12, 0x07)
        _, bad_mrange = self._vdc_mutant(
            "ar5-negmrange", 5, 0x3C, 0x12, 0x07,
            lambda r: r["mpr_after"].__setitem__(0, 0x100))
        with self.assertRaises(AssertionError, msg="out-of-range MPR accepted"):
            self._check_vdc(bad_mrange, 5, 0x3C, 0x12, 0x07)

    def test_vdc_rejects_pc_mismatch(self):
        # pc != halt with halted True fabricated: inconsistent halt record.
        _, bad = self._vdc_mutant("ar5-negpc", 5, 0x3C, 0x12, 0x07,
                                   lambda r: r.update(pc=r["halt"] + 1))
        with self.assertRaises(AssertionError, msg="pc mismatch accepted"):
            self._check_vdc(bad, 5, 0x3C, 0x12, 0x07)

    def test_vdc_rejects_short_steps(self):
        # Fewer than 16 steps cannot hold 16 start-and-end confirmations.
        _, bad = self._vdc_mutant("ar5-negsteps", 5, 0x3C, 0x12, 0x07,
                                   lambda r: r.update(steps=5))
        with self.assertRaises(AssertionError, msg="short steps accepted"):
            self._check_vdc(bad, 5, 0x3C, 0x12, 0x07)

    def _run_writes(self, name, spinit, rom_bytes=None):
        # WRAM value-change tracer run: HuCARD-style guest (SEI, MPR0/1=$F8,
        # SP=spinit, JSR $E06C, halt) with per-instruction WRAM changed-offset
        # history recorded by the probe, cold then warm. In-contract SP is
        # >= $06; SP < $06 is refused pre-execution by the probe (see
        # test_writes_sp_refused_*). The tracer records changed offsets at
        # instruction boundaries, NOT write events (same-value stores are
        # invisible); dynamic results are changed offsets only, never a full
        # write-event proof. Returns the two RESULT records.
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
        # T05 scope: caller-side JSR (2 bytes) + B1 body callee pushes
        # (4 bytes: P/A/X/Y) below SP, i.e. WRAM offsets for
        # $21(spinit-5)..$21(spinit) (6 transient stack bytes total). Nothing
        # else may show a value change on any cycle for B1. SP is
        # restored/balanced; stacked bytes are NOT scrubbed (SP restore and
        # stack-memory restore are distinct).
        return set(range(0x100 + spinit - 5, 0x100 + spinit + 1))

    def _check_writes(self, recs, spinit):
        # Strict B1 value-change history proof, both cycles: halted (16
        # start-and-end confirmations; unconfirmed/budget/timeout never counts
        # as success), complete (not truncated), identical changed-offset sets
        # cold==warm, and exactly the transient stack bytes -- a bare-RTS
        # mutant (2 JSR bytes only) or any extra value change fails here.
        # Same-value stores are invisible by design (changed offsets only);
        # full dynamic write-event detection is NOT claimed -- the static
        # 9-byte pin (no store opcode beyond the 4 pushes) is the stronger
        # guarantee, separated from this dynamic observation.
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
        # Mutant RED through the value-change tracer only: halted cleanly on
        # both cycles (16 start-and-end confirmations; abnormal
        # halt/timeout/unconfirmed never counts as side-effect detection),
        # identical changed-offset sets, and the out-of-scope surplus is
        # exactly `extra` (non-empty). Changed offsets only: a same-value
        # store would NOT surplus here by design. The caller additionally
        # proves the legacy oracles stay GREEN on the same ROM where the
        # property demands it.
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
        # T05: B1 value-change history is exactly the 6 transient stack bytes
        # (caller-side JSR 2 + callee 4: $21FA-$21FF) and nothing else, cold
        # and warm. Changed offsets only (same-value invisible by design).
        self._check_writes(self._run_writes("b1ff", 0xFF), 0xFF)

    def test_writes_b1_sp_06(self):
        # T05 boundary: SP=$06 minimum shows exactly $2101-$2106 changed.
        # $06 is the in-contract minimum (JSR 2 + 4 pushes use $2101-$2106).
        self._check_writes(self._run_writes("b106", 0x06), 0x06)

    def test_writes_sp_refused_00(self):
        # MINOR-1 negative: SP=$00 (below $06) is refused pre-execution.
        # The probe must exit 2 with spinit refusal and NO RESULT lines --
        # a contract refusal, never an execution failure or budget timeout.
        import subprocess
        import tempfile
        o = self.out / "writes-ref00"
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.writes_probe), str(self.rom.resolve()),
                            str(cue.resolve()), "4096", "0"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 2, "SP=$00 must be refused pre-execution (exit 2)")
        self.assertNotIn("RESULT", r.stdout, "refused SP must emit no RESULT")
        self.assertIn("spinit", r.stderr, "refusal must name spinit")

    def test_writes_sp_refused_05(self):
        # MINOR-1 negative: SP=$05 (below $06) is refused pre-execution,
        # same contract as $00. $06 remains the minimum GREEN boundary.
        import subprocess
        o = self.out / "writes-ref05"
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.writes_probe), str(self.rom.resolve()),
                            str(cue.resolve()), "4096", "5"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 2, "SP=$05 must be refused pre-execution (exit 2)")
        self.assertNotIn("RESULT", r.stdout, "refused SP must emit no RESULT")
        self.assertIn("spinit", r.stderr, "refusal must name spinit")

    def test_writes_sp_probes_boundary_contract(self):
        # MINOR-1 scope check: the sibling VDC probe uses a fixed SP=$FF
        # guest (no spinit argument), so it has no SP-boundary issue. Only
        # the writes tracer takes a caller SP and only it enforces $06.
        import inspect
        src = Path(__file__).with_name("geargrafx_dotmod24_probe.cpp").read_text()
        self.assertNotIn("spinit", src, "VDC probe must not take spinit")
        wsrc = Path(__file__).with_name("geargrafx_dotmod24_writes_probe.cpp").read_text()
        self.assertIn("spinit < 0x06", wsrc, "writes probe must enforce $06 minimum")
        self.assertIn("pc_before == halt", wsrc, "writes probe must count start-and-end halt")

    def test_writes_unconfirmed_halt_rejected(self):
        # MINOR-2 negative control: an unconfirmed halt (halted=false, e.g.
        # only 15 post-arrival confirmations) must NOT count as success.
        # _check_writes requires halted true, so a fabricated unconfirmed
        # record raises (harness failure), never GREEN and never RED.
        wrecs = self._run_writes("b1ff-h", 0xFF)
        self.assertTrue(wrecs[0]["halted"], "control must halt")
        bad = [dict(wrecs[0]), dict(wrecs[1])]
        bad[0]["halted"] = False
        bad[1]["halted"] = False
        with self.assertRaises(AssertionError, msg="unconfirmed halt accepted"):
            self._check_writes(bad, 0xFF)

    def _run_writes_steps(self, name, spinit, steps, rom_bytes=None):
        # Probe run with an explicit step budget and NO halted assertion:
        # returns the two RESULT records for dynamic boundary calibration.
        # Used only to establish arrival via pc (not via halted) and to prove
        # the 15-vs-16 confirmation boundary on the real core.
        rom = self.rom if rom_bytes is None else self._tmp_rom(name, rom_bytes)
        o = self.out / ("writes-" + name)
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.writes_probe), str(rom.resolve()),
                            str(cue.resolve()), str(steps), str(spinit)],
                           capture_output=True, text=True, timeout=120, check=True)
        import json
        recs = [json.loads(line[7:]) for line in r.stdout.splitlines()
                if line.startswith("RESULT ")]
        self.assertEqual(len(recs), 2, "write tracer must report cold+warm")
        cycles = sorted(x["cycle"] for x in recs)
        self.assertEqual(cycles, ["cold", "warm"])
        return recs

    def test_writes_halt_boundary_15_vs_16_dynamic(self):
        # MINOR-2 dynamic boundary on the real core: independently establish
        # the arrival step A via pc (first steps budget with final pc==halt,
        # never via the halted flag), then prove A+15 leaves halted=false
        # and A+16 leaves halted=true on BOTH cycles. Budget/timeout is never
        # success: halted=false always fails _check_writes as harness failure.
        import json
        ctrl = self._run_writes("b1ff-dyn", 0xFF)
        self.assertTrue(ctrl[0]["halted"], "control must halt")
        halt = ctrl[0]["halt"]
        self.assertEqual(ctrl[1]["halt"], halt, "cold/warm halt differ")
        run_steps = ctrl[0]["steps"]
        self.assertGreaterEqual(run_steps, 17, "control run too short")
        # Binary-search the arrival step A: first steps budget with final
        # pc==halt (monotonic: the halt is a BRA -2 self-loop, so pc sticks).
        lo, hi = 1, run_steps
        # hi must already end at halt (control proves it for 4096; verify for
        # run_steps too via the same probe shape before searching).
        hi_recs = self._run_writes_steps("dyn-hi", 0xFF, hi)
        self.assertEqual(hi_recs[0]["pc"], halt, "hi must end at halt")
        self.assertEqual(hi_recs[1]["pc"], halt, "hi must end at halt (warm)")
        while lo < hi:
            mid = (lo + hi) // 2
            recs = self._run_writes_steps(f"dyn-bs{mid}", 0xFF, mid)
            # Arrival is identical cold+warm (deterministic guest); require both.
            if recs[0]["pc"] == halt and recs[1]["pc"] == halt:
                hi = mid
            else:
                # Before arrival neither cycle may report halt pc.
                self.assertNotEqual(recs[0]["pc"], halt, "cold/warm arrival split")
                self.assertNotEqual(recs[1]["pc"], halt, "cold/warm arrival split")
                lo = mid + 1
        arrival = lo
        # Cross-check: arrival+16 must equal the control run_steps (arrival
        # step itself never counts; 16 start-and-end confirmations follow).
        self.assertEqual(arrival + 16, run_steps,
                         "arrival+16 must match control run_steps")
        # A+15: at halt but UNCONFIRMED on both cycles (never success).
        pre = self._run_writes_steps("dyn-pre15", 0xFF, arrival + 15)
        for res in pre:
            self.assertEqual(res["pc"], halt, "pre must end at halt")
            self.assertFalse(res["halted"],
                             f"arrival+15 must be unconfirmed ({res['cycle']})")
        with self.assertRaises(AssertionError, msg="15-post-arrival accepted"):
            self._check_writes(pre, 0xFF)
        # A+16: CONFIRMED on both cycles.
        post = self._run_writes_steps("dyn-post16", 0xFF, arrival + 16)
        for res in post:
            self.assertEqual(res["pc"], halt, "post must end at halt")
            self.assertTrue(res["halted"],
                            f"arrival+16 must be confirmed ({res['cycle']})")
        self._check_writes(post, 0xFF)

    def test_writes_mutant_sta2200_red(self):
        # T05 value-change write (outside canary $2900-$29FF and zp $10):
        # the legacy oracles stay GREEN (proving their blindness here)
        # while the value-change tracer flags changed offset $2200 on both
        # cycles. This mutant CHANGES the value, so it is visible; a
        # same-value STZ to an already-matching byte would stay invisible by
        # design (changed offsets only, never a write-event proof).
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
        # per-instruction value-change history still records changed offset
        # $2200 -> RED. This proves history-vs-final, NOT same-value
        # detection: a same-value store would leave no diff and stay
        # invisible by design.
        orig = self.rom.read_bytes()
        bad, cav = self._mutate_body_extended(orig, MUT_RESTORE_24)
        self._assert_ext_mutant_applied(orig, bad, cav, MUT_RESTORE_24)
        rec, base = self._run_on_rom("m-restore", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_green(rec, base, self._check_c0,
                           mutant_tag="restore-old-oracle-green")
        wrecs = self._run_writes("restore", 0xFF, rom_bytes=bad)
        self._check_writes_extra(wrecs, 0xFF, {0x200})

    def test_chain_33_4a_23_25_24(self):
        # Synthetic G5 chain leg: the PUBLIC boot ($02 leg) loads this
        # payload, which then calls $33 -> $4A -> $23 -> $25 -> $24 through the real
        # table entries. Each APICALL (return_matches_caller + MATCH) proves
        # per-call ENTRY (stacked-return bytes at entry time, MPR and SP at
        # entry); per-call RTS return resumption (caller+3) is NOT proved by
        # APICALL alone (APIRETURN is not collected and the parser retains
        # APICALL only) -- return is proved end-to-end by the final records +
        # verified halt, never per-call. The observed
        # $24 entry state is SIMULATED-entry (conditional on the fabricated
        # $24 return's predecessors), never genuine $24 input evidence.
        # Trampoline exclusion is scoped: the $24 9-byte pin excludes a
        # stacked-return-rewriting trampoline for the $24 call only; the
        # companion legs ($33/$4A/$23/$25) are constrained by no pin here and
        # their individual returns are not proved per-call.
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
            self.assertEqual([c["slot"] for c in calls], [0x33, 0x4A, 0x23, 0x25, 0x24], cycle)
            self.assertEqual([c["entry_pc"] for c in calls],
                             [0xE099, 0xE0DE, 0xE069, 0xE06F, 0xE06C], cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))
                self.assertEqual(c["mpr"][1], 0xF8, (cycle, c))  # MPR1=$F8 at every entry
            # NOTE: APIRETURN lines are not collected by the probe parser
            # (the parser retains APICALL only). return_matches_caller +
            # stack_check == MATCH are ENTRY evidence: they observe the
            # stacked return bytes at entry time, not the RTS destination.
            # Per-call return resumption is NOT claimed from APICALL alone;
            # a stacked-return-rewriting trampoline back to caller+3 for the
            # $24 call is excluded only by the $24 9-byte body-shape pin
            # (companions unconstrained here), while the
            # end-to-end records below exclude byte-skipping bodies and prove
            # return end-to-end (final records + halt), never per-call.
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
        # SIMULATED-entry observation: the $24 call was reached with the
        # payload-set state intact through $33/$4A/$23 (MPRs/SP/regs). This
        # records the scaffold's entry conditions, not genuine $24 inputs.
        for cycle in ("cold", "warm"):
            call25 = [c for c in rec["APICALL"]
                        if c["cycle"] == cycle and c["slot"] == 0x24][0]
            self.assertEqual((call25["caller_a"], call25["caller_x"], call25["caller_y"]),
                             (0x31, 0x12, 0x34), cycle)
            self.assertEqual(call25["caller_p"] & ~0x10, 0x07, cycle)
            self.assertEqual(call25["mpr"][1], 0xF8, cycle)

    def test_dotmod_stub_default_traps(self):
        # T01: cd_24="stub" (builder default): $E06C stays on the linked
        # fallback, JSR traps with X=$24 at the verified stub and never
        # returns (the post-JSR marker stays $00). Proves the default did
        # not change and fail-closed is intact.
        rec, base = self._run("stub", {"asm": STUB25_ASM, "defs": {}}, rom_kind="stub")
        cold, warm = self._buf_stublike(rec, self.labels_stub, 0x24)
        for buf in (cold, warm):
            self.assertEqual(buf[base], 0x00)  # JSR never returned

    def test_dotmod_exp_unimplemented_slot_traps(self):
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

    def test_dotmod_rom_identity_and_table_routing(self):
        import hashlib
        exp = self.rom.read_bytes()
        stub = self.rom_stub.read_bytes()
        self.assertNotEqual(hashlib.sha256(exp).digest(), hashlib.sha256(stub).digest())
        # Experimental: $E06C -> JMP ex_dotmod_entry (body starts PHP,PHA).
        tab = exp[0xE06C - 0xE000:0xE06C - 0xE000 + 3]
        body = self.syms["ex_dotmod_entry"]
        self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
        self.assertGreaterEqual(body, 0xF000)
        self.assertEqual(exp[body - 0xE000:body - 0xE000 + 9], B1_BODY)
        # Stub default: $E06C -> JMP api_slot_24 fallback; table bytes equal
        # the normal DEV ROM.
        tab_s = stub[0xE06C - 0xE000:0xE06C - 0xE000 + 3]
        fb = self.syms_stub["api_slot_24"]
        self.assertEqual(tuple(tab_s), (0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
        at_25 = 3 * 0x24
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
        # Every cycle needs >=1 APICALL showing a genuine JSR into slot $24
        # (table entry $E06C, in_table, SP delta 2, internal stack MATCH).
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
                        and c.get("slot") == 0x24
                        and c.get("entry_pc") == 0xE06C
                        and c.get("in_table") is True
                        and c.get("sp_delta") == 2
                        and c.get("stack_check") == "MATCH"]
            self.assertGreaterEqual(
                len(arrivals), 1,
                f"mutant {mutant_tag} $24 not reached in {cycle} "
                "(harness failure, not detection)")

    def _assert_body_mutant_applied(self, orig_bytes, mutant_bytes, offset, new_bytes):
        # Prove the mutant changed ONLY the intended 9-byte body range:
        # ROM length unchanged, offset inside the body, replacement differs
        # from the original target bytes, target reads back as new_bytes,
        # and every byte outside the target range is identical.
        body_off = (self.syms["ex_dotmod_entry"] & 0xFFFF) - 0xE000
        self.assertEqual(bytes(orig_bytes[body_off:body_off + 2]),
                         bytes((0x08, 0x48)),
                         "body offset does not anchor at reviewed prologue (harness failure)")
        body_addr = self.syms["ex_dotmod_entry"] & 0xFFFF
        self.assertEqual(bytes(orig_bytes[0xE06C - 0xE000:0xE06C - 0xE000 + 3]),
                         bytes((0x4C, body_addr & 0xFF, (body_addr >> 8) & 0xFF)),
                         "$E06C vector does not target ex_dotmod_entry (harness failure)")
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
        # Channel (b) additionally requires the per-cycle $24 arrival proof:
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

    def test_dotmod_mutant_drop_pla_red(self):
        # Drop the A restore (PLA->NOP): unbalanced SP (pop dropped), so
        # RTS mis-returns (control-flow mutant, channel (a)). Proves A
        # preservation is load-bearing, not incidental.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 6, bytes((0xEA,)))
        self._assert_body_mutant_applied(orig, bad, 6, bytes((0xEA,)))
        rec, base = self._run_on_rom("m-nopla", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, mutant_tag="drop-pla")

    def test_dotmod_mutant_drop_plp_red(self):
        # Drop the P restore (PLP->NOP): unbalanced SP. Proves flag
        # preservation (incl. Carry) is load-bearing.
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 7, bytes((0xEA,)))
        self._assert_body_mutant_applied(orig, bad, 7, bytes((0xEA,)))
        rec, base = self._run_on_rom("m-noplp", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, mutant_tag="drop-plp")

    def test_dotmod_mutant_force_clc_unbalanced_red(self):
        # Control-flow mutant (kept, NOT the T07 oracle): PLP->CLC drops a
        # pop, so SP is unbalanced and RTS mis-returns (channel (a)). The
        # C=1 seed pairing is retained; the discriminating T07 proof is the
        # balanced mutant below (test_dotmod_mutant_force_clc_balanced_red).
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

    def test_dotmod_mutant_force_sec_unbalanced_red(self):
        # Control-flow mutant (kept, NOT the T07 oracle): PLP->SEC drops a
        # pop (unbalanced, channel (a)); the discriminating proof is the
        # balanced mutant below (test_dotmod_mutant_force_sec_balanced_red).
        orig = self.rom.read_bytes()
        bad = self._mutate_body(orig, 7, bytes((0x38,)))
        self._assert_body_mutant_applied(orig, bad, 7, bytes((0x38,)))
        rec, base = self._run_on_rom("m-sec-unbal", bad,
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base,
                                mutant_tag="M7b-C0-control-flow (unbalanced)")

    def _run_balanced(self, name, mutant_bytes, defines):
        # Extended cavity mutant run: $E06C vector repointed to the cavity
        # body (scope-verified); the normal 9-byte B1 body stays intact.
        orig = self.rom.read_bytes()
        bad, cav = self._mutate_body_extended(orig, mutant_bytes)
        self._assert_ext_mutant_applied(orig, bad, cav, mutant_bytes)
        return self._run_on_rom(name, bad, defines)

    def test_dotmod_mutant_force_clc_balanced_red(self):
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
        # $07 with bit0 off; B is masked by the checker). SP balance is
        # verified on both cycles via _check_sp so a stacked-SP corruption
        # cannot be mistaken for Carry-specific detection (CodeRabbit: SP
        # check applies to balanced RED as well as GREEN, cold+warm).
        cold, warm = self._buf(rec)
        self._check_sp(cold, base, 0xFF)
        self._check_sp(warm, base, 0xFF)
        for buf in (cold, warm):
            self.assertEqual(buf[base + 4] & 0x01, 0x00)
            self.assertEqual(buf[base + 20] & 0x01, 0x00)
            self._check_expected(buf, base, 0x06, 0x07, 0x11, 0x22,
                                 0x06, 0x07, 0x66, 0x77)

    def test_dotmod_mutant_force_clc_balanced_green_c0(self):
        # T07 passing seed: the same CLC cavity body at C=0 must stay GREEN
        # (full preservation proof: no violation to detect).
        rec, base = self._run_balanced("m-clc-bal0", MUT_CLC10,
                                        self._defines(0x06, 0x11, 0x22,
                                                     0x06, 0x66, 0x77))
        self._assert_green(rec, base, self._check_c0,
                           mutant_tag="M7a-C0-green (balanced CLC)")

    def test_dotmod_mutant_force_sec_balanced_red(self):
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
        # SP balance is verified on both cycles via _check_sp so a stacked-SP
        # corruption cannot be mistaken for Carry-specific detection.
        cold, warm = self._buf(rec)
        self._check_sp(cold, base, 0xFF)
        self._check_sp(warm, base, 0xFF)
        for buf in (cold, warm):
            self.assertEqual(buf[base + 4] & 0x01, 0x01)
            self.assertEqual(buf[base + 20] & 0x01, 0x01)
            self._check_expected(buf, base, 0x07, 0x06, 0x11, 0x22,
                                 0x07, 0x06, 0x66, 0x77)

    def test_dotmod_mutant_force_sec_balanced_green_c1(self):
        # T07 passing seed: the same SEC cavity body at C=1 must stay GREEN.
        rec, base = self._run_balanced("m-sec-bal1", MUT_SEC10,
                                        self._defines(0x07, 0x11, 0x22,
                                                     0x07, 0x66, 0x77))
        self._assert_green(rec, base, self._check_c1,
                           mutant_tag="M7b-C1-green (balanced SEC)")

    def test_dotmod_mutant_save_pha_to_php_red(self):
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

    def test_dotmod_mutant_stack_smash_red(self):
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

    def test_dotmod_mutant_table_reroute_red(self):
        # T06: reroute $E06C back to the stub fallback: calls halt at the
        # stub (api_unimplemented, confirmed, X=$24) instead of returning.
        # Proves the test exercises the wired body, not the table alone.
        orig = self.rom.read_bytes()
        tab = bytearray(orig)
        fb = self.syms["api_slot_24"]
        want_tab = bytes((0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
        tab[3 * 0x24:3 * 0x24 + 3] = want_tab
        self.assertNotEqual(bytes(tab), orig, "table reroute not applied")
        self.assertEqual(bytes(tab[3 * 0x24:3 * 0x24 + 3]), want_tab)
        self.assertEqual(bytes(tab[:3 * 0x24]), orig[:3 * 0x24],
                         "table mutant changed bytes before the vector (harness failure)")
        self.assertEqual(bytes(tab[3 * 0x24 + 3:]), orig[3 * 0x24 + 3:],
                         "table mutant changed bytes outside the vector (harness failure)")
        rec, base = self._run_on_rom("m-tab", bytes(tab),
                                      self._defines(0x06, 0x11, 0x22, 0x06, 0x66, 0x77))
        self._assert_mutant_red(rec, base, allowed_abnormal=("api_unimplemented",),
                                expect_x=0x24, mutant_tag="table-reroute")

    def test_dotmod_buffer_mutants_detected(self):
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

    def _fake_arrival_call(self, cycle, slot=0x24, entry_pc=0xE06C,
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
        return (self.syms["ex_dotmod_entry"] & 0xFFFF) - 0xE000

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
