"""Synthetic real-core tests for the $2C EX_IRQON D2 limited-functional body (Issue #54).

Proves the CURRENT experimental wiring end to end on synthetic media with the
pinned core: PUBLIC IPL boot (2-base builder, commercial FIRST default,
cd_2c="experimental" unit image + full ExpF image) -> JSR $E084 (real table
entry, never direct-body JSR, never fake RTS) -> CR R05 bit3 set via the
shared $2502 shadow (other bits preserved) -> RTS resumption -> payload halt.
Companion coverage:

  stub default (cd_2c="stub") keeps $E084 on the linked fallback (byte-equal
  to the normal DEV ROM) and traps with X=$2C (T01 RED baseline);
  ok path (shadow seeded $2502=seed/$2503=$A5): CR low = seed|$08, shadow
  published, AR=5, C=0, A/X/Y/MPRs/SP preserved, $20F3/$20F7/IDR/IRR/I
  unchanged, canary + full-buffer cold/warm equality (falsifiable oracle:
  the expected CR value derives from the independent guest seed, never from
  the implementation output);
  reject path (shadow unseeded $2503=$00): no VDC write, no shadow write,
  C=1, everything else preserved;
  entry-C=0 and entry-C=1 classes on both paths (all four combinations);
  nonzero entry A/X/Y preservation incl. A!=P vectors (a clobber mutant
  goes RED: no fixed success value);
  VDC state on the real core (dedicated probe with GetState snapshots:
  ok CR low exact + high/other regs unchanged + AR=5; reject all unchanged;
  MPRs/IDR/IRR unchanged);
  work-RAM scope (256-byte canary + full-buffer cold/warm equality +
  per-instruction write history: writes ⊆ transient ∪ {$2502,$2503}, and
  the transient stack discipline intact);
  MPR1=$F8 precondition validated by the harness BEFORE JSR (T13: a $FF
  candidate refuses at refused_halt without executing JSR, never reaches
  $2C, and never remaps first);
  SP depth (SP=$FF and boundary SP=$06 GREEN and balanced guest-executed;
  SP=$00/$05 probe-argument refusal GREEN with no guest execution);
  synthetic chain $33->$4A->$23->$25->$24->$36->$35->$2C through the real
  table entries with per-call APICALL evidence (the $02 leg is the PUBLIC
  boot itself; the observed $2C entry state is SIMULATED-entry);
  $20F3/$20F7 mirror invariance (explicitly unimplemented: asserted
  UNCHANGED, never guessed);
  routing, 44-byte structural pin, single-byte mutants (wrong bit, destroy
  bits, no-op mask, missing VDC write, missing shadow publish, shadow
  bypass, CLC/SEC drops, MPR restore drop, stack smash via cavity), and
  buffer negatives.

NOT a compatible System Card BIOS. NOT genuine ABI evidence (genuine $2C
semantics stay UNKNOWN/BLOCKED; see docs/54-ex-irqon-contract.md,
docs/55-ex-irqoff-contract.md and docs/g5-display-functional-selection.md).
NOT DEV_CALLABLE. release_eligible=false. G5_OBSERVED=false. Synthetic
only; commercial media: NOT_RUN here (no ./rom probing in this file).
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

# Payload CODE ($3000): seeds the $2502/$2503 shadow (SEED_LO/SEED_EN),
# snapshots $20F3/$20F7 pre-call, then JSRs the real $E084 table entry twice
# with different regs and records everything. MPR0=$F8 (non-I/O, so any I/O
# touch is detectable), MPR1=$F8 (caller precondition, T13). A 256-byte
# canary at $2900 ($5A) proves the work-RAM scope on the real core (T05).
# res_base layout (48 bytes):
#   +0 ran marker ($AA)  +1..5 A/X/Y/P/SP (call 1)  +6..13 MPR0..7
#   +14 $2502 post  +15 $2503 post  +16 $20F3 post  +17 $20F7 post
#   +18 zp $10 post  +19..36 call-2 records (same shape, no marker)
#   +37 $20F3 pre  +38 $20F7 pre  +39 zp $10 pre
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
    lda #SEED_LO
    sta $2502
    .if SEED_EN
    lda #$A5
    .else
    lda #$00
    .endif
    sta $2503
    lda $20F3
    sta res_base+37
    lda $20F7
    sta res_base+38
    lda $10
    sta res_base+39
    lda #POPEN
    ldx #XONE
    ldy #YONE
    lda #POPEN
    pha
    lda #AONE
    plp
    jsr $E084
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
    lda $2502
    sta res_base+14
    lda $2503
    sta res_base+15
    lda $20F3
    sta res_base+16
    lda $20F7
    sta res_base+17
    lda $10
    sta res_base+18
    lda #PTWO
    ldx #XTWO
    ldy #YTWO
    lda #PTWO
    pha
    lda #ATWO
    plp
    jsr $E084
    sta res_base+19
    stx res_base+20
    sty res_base+21
    php
    pla
    sta res_base+22
    tsx
    txa
    sta res_base+23
    tma #$01
    sta res_base+24
    tma #$02
    sta res_base+25
    tma #$04
    sta res_base+26
    tma #$08
    sta res_base+27
    tma #$10
    sta res_base+28
    tma #$20
    sta res_base+29
    tma #$40
    sta res_base+30
    tma #$80
    sta res_base+31
    lda $2502
    sta res_base+32
    lda $2503
    sta res_base+33
    lda $20F3
    sta res_base+34
    lda $20F7
    sta res_base+35
    lda $10
    sta res_base+36
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 48
"""

STUB2C_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    ldx #$FF
    txs
    jsr $E084
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 16
"""

# T13 harness precondition probe: MPR1 candidate validated BEFORE any JSR.
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
    jsr $E084
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

# Synthetic G5 chain leg: seed the shared $2502 shadow, then call
# $33 -> $4A -> $23 -> $25 -> $24 -> $36 -> $35 -> $2C through the real table
# entries (the $02 leg is the PUBLIC boot that loads this payload). The $33
# DSPOFF body shares the $2502 shadow family: seeded here, it takes its ok
# path (CR low = $00 & $3F = $00, no visible change); the $2C body then takes
# its ok path (CR low = $00 | $08 = $08). End-to-end records prove resumption
# and preservation; APICALL entries prove routing.
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
    lda #$00
    sta $2502
    lda #$A5
    sta $2503
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
    jsr $E0A2
    jsr $E09F
    jsr $E084
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

# D2-functional body bytes (src/ex_irqon.s, 44 bytes). Pinned here: any
# opcode/operand drift changes the mask, the shadow discipline, or the
# window, so the shape itself is load-bearing (a bare-RTS substitution would
# be behavior-GREEN on preservation oracles by construction).
D2F_BODY = bytes((
    0x08, 0x48, 0xAD, 0x03, 0x25, 0xC9, 0xA5, 0xF0, 0x04, 0x68, 0x28, 0x38,
    0x60, 0x78, 0x43, 0x01, 0x48, 0xA9, 0xFF, 0x53, 0x01, 0xA9, 0x05, 0x8D,
    0x00, 0x00, 0xAD, 0x02, 0x25, 0x09, 0x08, 0x8D, 0x02, 0x25, 0x8D, 0x02,
    0x00, 0x68, 0x53, 0x01, 0x68, 0x28, 0x18, 0x60,
))

# Single-byte body mutants (stack-balanced, clean RTS; each must RED exactly
# the oracle named in its test, never by hang or crash):
MUT_WRONGBIT = (30, bytes((0x04,)))    # ORA #$08 -> ORA #$04 (wrong bit)
MUT_DESTROY = (29, bytes((0x29,)))     # ORA -> AND #$08 (destroys other bits)
MUT_NOOP = (29, bytes((0xEA, 0xEA)))   # ORA #$08 -> NOP NOP (no bit set)
MUT_NOVDC = (34, bytes((0xEA, 0xEA, 0xEA)))     # STA $0002 -> NOPs (no VDC write)
MUT_NOPUBLISH = (31, bytes((0xEA, 0xEA, 0xEA)))  # STA $2502 -> NOPs (no publish)
MUT_BYPASS = (7, bytes((0x80,)))        # BEQ -> BRA (unseeded takes ok path)
MUT_CLC_DROP = (42, bytes((0xEA,)))    # CLC -> NOP (ok exits C=entry)
MUT_SEC_DROP = (11, bytes((0xEA,)))    # SEC -> NOP (reject exits C=entry)
MUT_MPR = (38, bytes((0xEA, 0xEA)))    # TAM #$01 -> NOPs (MPR0 stays $FF)


class Irqon2cSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-2c-")
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
        # $2C-only experimental unit image.
        cls.rom2c, cls.syms2c = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m2c", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False,
            cd_2c="experimental")
        # Full ExpF image (all six experimentals) for the chain leg.
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m2cfull", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False,
            cd_4a="experimental", cd_25="experimental",
            cd_24="experimental", cd_36="experimental",
            cd_35="experimental", cd_2c="experimental")
        cls.rom_stub, cls.syms_stub = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m2c-stub", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False)
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        for rom_bytes, symbols, addr, name in (
                (cls.rom2c.read_bytes(), cls.syms2c, cls.syms2c["ex_irqon_entry"], "2c"),
                (cls.rom.read_bytes(), cls.syms, cls.syms["ex_irqon_entry"], "full")):
            tab = rom_bytes[0xE084 - 0xE000:0xE084 - 0xE000 + 3]
            assert tuple(tab) == (0x4C, addr & 0xFF, (addr >> 8) & 0xFF), (name, addr)
            assert 0xF000 <= addr <= 0xFFF6 - 44, (name, addr)
            assert rom_bytes[addr - 0xE000:addr - 0xE000 + 44] == D2F_BODY, (name,)
        tab = cls.rom_stub.read_bytes()[0xE084 - 0xE000:0xE084 - 0xE000 + 3]
        fb = cls.syms_stub["api_slot_2C"]
        assert tuple(tab) == (0x4C, fb & 0xFF, (fb >> 8) & 0xFF)
        trace = cls.out / "ipl-loader-g5-m2c.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        trace_stub = cls.out / "ipl-loader-g5-m2c-stub.lbl"
        cls.labels_stub = parse_labels(
            trace_stub, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        trace_full = cls.out / "ipl-loader-g5-m2cfull.lbl"
        cls.labels_full = parse_labels(
            trace_full, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "m2c-probe")
        cls.vdc_probe = compile_probe(cls.out, "geargrafx_irqon2c_probe.cpp", "m2c-vdc-probe")
        cls.writes_probe = compile_probe(cls.out, "geargrafx_irqon2c_writes_probe.cpp",
                                         "m2c-writes-probe")
        import geargrafx_ipl_loader as L
        cls.L = L

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tmp = cls._tmp.name
        assert "pce-2c-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    # ---- return-probe harness (shared shape with the $35 suite) ----

    def _run(self, name, defines, rom_kind="exp"):
        if rom_kind == "exp":
            rom, labels = self.rom2c, self.labels
        elif rom_kind == "full":
            rom, labels = self.rom, self.labels_full
        elif rom_kind == "stub":
            rom, labels = self.rom_stub, self.labels_stub
        else:
            raise ValueError("rom_kind must be 'exp', 'full' or 'stub'")
        return self._run_probe_on_rom(name, rom, labels, defines)

    def _run_on_rom(self, name, rom_bytes, defines):
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
        assert res_base + 48 <= 0x1800, syms
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
            lab_lines += f"cd_halt_fail {int(refused.group(1), 16):04x}\n"
        lab.write_text(lab_lines, encoding="ascii")
        import geargrafx_api_entry as Eentry
        rec = Eentry.run_probe(self.probe, rom, cue, lab, BUDGET, "synthetic")
        return rec, res_base

    def _buf(self, rec):
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
                 seed_lo=0x00, seed_en=1, spinit=0xFF):
        if aone is None:
            aone = popen
        if atwo is None:
            atwo = ptwo
        return {"asm": PAYLOAD_ASM,
                "defs": {"POPEN": popen, "AONE": aone, "XONE": xone, "YONE": yone,
                         "PTWO": ptwo, "ATWO": atwo, "XTWO": xtwo, "YTWO": ytwo,
                         "SEED_LO": seed_lo, "SEED_EN": seed_en,
                         "SPINIT": spinit}}

    def _mutate_body(self, rom_bytes, offset, new_bytes):
        body_off = (self.syms2c["ex_irqon_entry"] & 0xFFFF) - 0xE000
        out = bytearray(rom_bytes)
        out[body_off + offset:body_off + offset + len(new_bytes)] = new_bytes
        return bytes(out)

    # ---- result checkers ----

    def _check_canary(self, buf):
        self.assertEqual(buf[0x100:0x200], bytes((0x5A,) * 256))

    def _p_expect(self, pin, cout):
        # Expected P-out image: entry P with T masked out and C forced to the
        # D2 convention (ok C=0 / reject C=1). B-bit behavior follows the
        # PHP/PLP round trip exactly as in the sibling B1 suites (compared
        # modulo T=$10 only).
        return (pin & ~0x11) | (cout & 0x01)

    def _check_ok_call(self, r, base, off, pin, ain, xin, yin, seed_lo):
        # ok path (seeded): A/X/Y exact, P per convention C=0 (T masked),
        # SP balanced, MPRs exact, shadow published seed|$08, mirrors + zp
        # unchanged.
        self.assertEqual((r[base + off], r[base + off + 1], r[base + off + 2]),
                         (ain, xin, yin))
        self.assertEqual(r[base + off + 3] & ~0x10, self._p_expect(pin, 0))
        self.assertEqual(r[base + off + 3] & 0x01, 0x00)
        self.assertEqual(r[base + off + 13], seed_lo | 0x08,
                         f"shadow not published (seed {seed_lo:#04x})")

    def _check_reject_call(self, r, base, off, pin, ain, xin, yin, seed_lo):
        # reject path (unseeded): A/X/Y exact, P per convention C=1,
        # shadow untouched ($2502==seed, $2503==$00), mirrors + zp unchanged.
        self.assertEqual((r[base + off], r[base + off + 1], r[base + off + 2]),
                         (ain, xin, yin))
        self.assertEqual(r[base + off + 3] & ~0x10, self._p_expect(pin, 1))
        self.assertEqual(r[base + off + 3] & 0x01, 0x01)
        self.assertEqual(r[base + off + 13], seed_lo,
                         "reject path wrote the shadow image")
        self.assertEqual(r[base + off + 14], 0x00,
                         "reject path wrote the shadow marker")

    def _check_common(self, buf, base, spinit, seed_en):
        # Shared post-call discipline (both paths, both calls): SP balanced,
        # MPRs exact (MPR0=$F8 proves the window restored; any I/O touch
        # without restore would surface here), $20F3/$20F7 equal pre/post
        # (mirrors explicitly unimplemented), marker set, zp intact.
        self.assertEqual(buf[base], 0xAA)
        self.assertEqual(buf[base + 5], spinit)
        self.assertEqual(buf[base + 23], spinit)
        self.assertEqual(list(buf[base + 6:base + 14]),
                         [0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        self.assertEqual(list(buf[base + 24:base + 32]),
                         [0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        # Mirrors: pre (37/38) equals post (16/17, 34/35). The D2 body owns
        # no mirror; any $20F3/$20F7 write is a defect, not a feature.
        self.assertEqual(buf[base + 16], buf[base + 37])
        self.assertEqual(buf[base + 17], buf[base + 38])
        self.assertEqual(buf[base + 34], buf[base + 37])
        self.assertEqual(buf[base + 35], buf[base + 38])
        self.assertEqual(buf[base + 15], 0xA5 if seed_en else 0x00)
        self.assertEqual(buf[base + 33], 0xA5 if seed_en else 0x00)
        self.assertEqual(buf[base + 18], 0xA5)
        self.assertEqual(buf[base + 36], 0xA5)
        self.assertEqual(buf[base + 39], 0xA5)

    # ---- T02/T03: ok path (seeded) ----

    def test_2c_ok_bit_set_carry_clear(self):
        # T03 ok: shadow $00/$A5, entry P=$06 (C=0) both calls. CR image
        # becomes $08, C=0 out, everything else preserved.
        rec, base = self._run("okc0", self._defines(0x06, 0x11, 0x22,
                                                      0x06, 0x66, 0x77,
                                                      seed_lo=0x00, seed_en=1))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for buf in (cold, warm):
            self.assertEqual(buf[base], 0xAA)
            self._check_ok_call(buf, base, 1, 0x06, 0x06, 0x11, 0x22, 0x00)
            self._check_ok_call(buf, base, 19, 0x06, 0x06, 0x66, 0x77, 0x00)
            self._check_common(buf, base, 0xFF, 1)
            self._check_canary(buf)

    def test_2c_ok_bit_set_carry_set(self):
        # T03 ok: entry P=$07 (C=1) both calls. Bit set, C forced to 0 out
        # (no Carry leak from entry into the result).
        rec, base = self._run("okc1", self._defines(0x07, 0x11, 0x22,
                                                      0x07, 0x66, 0x77,
                                                      seed_lo=0x00, seed_en=1))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for buf in (cold, warm):
            self._check_ok_call(buf, base, 1, 0x07, 0x07, 0x11, 0x22, 0x00)
            self._check_ok_call(buf, base, 19, 0x07, 0x07, 0x66, 0x77, 0x00)
            self._check_common(buf, base, 0xFF, 1)
            self._check_canary(buf)

    def test_2c_ok_preserves_other_bits(self):
        # T03 ok: shadow $F0 (other bits set) stays set, only bit3 added.
        # A!=P vectors prove A and P are independent (no conflation).
        rec, base = self._run("okbits", self._defines(0x07, 0x11, 0x22,
                                                        0x06, 0x66, 0x77,
                                                        aone=0xB0, atwo=0x35,
                                                        seed_lo=0xF0, seed_en=1))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for buf in (cold, warm):
            self._check_ok_call(buf, base, 1, 0x07, 0xB0, 0x11, 0x22, 0xF0)
            self._check_ok_call(buf, base, 19, 0x06, 0x35, 0x66, 0x77, 0xF0)
            self.assertEqual(buf[base + 14], 0xF8)
            self.assertEqual(buf[base + 32], 0xF8)
            self._check_common(buf, base, 0xFF, 1)
            self._check_canary(buf)

    def test_2c_ok_boundary_values(self):
        # T11: byte boundaries. Shadow $FF (all bits) is idempotent;
        # A=$00/$FF, X=$00/$80 preserved exactly.
        rec, base = self._run("okbnd", self._defines(0x04, 0x00, 0xA5,
                                                        0x85, 0x80, 0x5A,
                                                        aone=0x00, atwo=0xFF,
                                                        seed_lo=0xFF, seed_en=1))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for buf in (cold, warm):
            self._check_ok_call(buf, base, 1, 0x04, 0x00, 0x00, 0xA5, 0xFF)
            self._check_ok_call(buf, base, 19, 0x85, 0xFF, 0x80, 0x5A, 0xFF)
            self.assertEqual(buf[base + 14], 0xFF)
            self._check_common(buf, base, 0xFF, 1)
            self._check_canary(buf)

    def test_2c_ok_idempotent_seed(self):
        # Seed already has bit3 ($08): publish is idempotent, CR unchanged
        # in value (still exactly one set operation, proven by the VDC
        # probe asserting R[5] == $08).
        rec, base = self._run("okidem", self._defines(0x06, 0x11, 0x22,
                                                        0x06, 0x66, 0x77,
                                                        seed_lo=0x08, seed_en=1))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for buf in (cold, warm):
            self._check_ok_call(buf, base, 1, 0x06, 0x06, 0x11, 0x22, 0x08)
            self._check_ok_call(buf, base, 19, 0x06, 0x06, 0x66, 0x77, 0x08)
            self._check_common(buf, base, 0xFF, 1)
            self._check_canary(buf)

    # ---- T03: reject path (unseeded) ----

    def test_2c_reject_unseeded(self):
        # Reject: $2503=$00. No VDC/shadow write, C=1 out on both C-in
        # classes, everything else preserved.
        rec, base = self._run("rej", self._defines(0x06, 0x11, 0x22,
                                                     0x07, 0x66, 0x77,
                                                     seed_lo=0x00, seed_en=0))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for buf in (cold, warm):
            self.assertEqual(buf[base], 0xAA)
            self._check_reject_call(buf, base, 1, 0x06, 0x06, 0x11, 0x22, 0x00)
            self._check_reject_call(buf, base, 19, 0x07, 0x07, 0x66, 0x77, 0x00)
            self._check_common(buf, base, 0xFF, 0)
            self._check_canary(buf)

    def test_2c_reject_seed_value_ignored(self):
        # Reject ignores the $2502 value ($F0 left untouched, not published).
        rec, base = self._run("rejf0", self._defines(0x07, 0x11, 0x22,
                                                       0x06, 0x66, 0x77,
                                                       seed_lo=0xF0, seed_en=0))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for buf in (cold, warm):
            self._check_reject_call(buf, base, 1, 0x07, 0x07, 0x11, 0x22, 0xF0)
            self._check_reject_call(buf, base, 19, 0x06, 0x06, 0x66, 0x77, 0xF0)
            self._check_common(buf, base, 0xFF, 0)
            self._check_canary(buf)

    # ---- T01/T02: stub baseline + return mechanics ----

    def test_2c_stub_default_traps(self):
        # T01 RED baseline (G2): the stub default never returns. The
        # post-JSR marker stays $00 and the halt is api_unimplemented
        # with X=$2C on both cycles. Any returning $2C on this image is
        # a wiring defect.
        rec, base = self._run("stub", {"asm": STUB2C_ASM, "defs": {}},
                              rom_kind="stub")
        cold, warm = self._buf_stublike(rec, self.labels_stub, 0x2C)
        self.assertEqual(cold, warm)
        self.assertEqual(cold[base], 0x00)
        self.assertEqual(warm[base], 0x00)

    def test_2c_apicall_slot_order(self):
        # T02 mechanics: JSR through the real $E084 table entry, RTS lands
        # at caller+3, SP delta 2, MATCH, balanced, MPR1=$F8 at entry.
        rec, base = self._run("order", self._defines(0x07, 0x11, 0x22,
                                                       0x06, 0x66, 0x77,
                                                       seed_lo=0x00, seed_en=1))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual([c["slot"] for c in calls], [0x2C, 0x2C], cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["entry_pc"], 0xE084, (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))
                self.assertEqual(c["mpr"][1], 0xF8, (cycle, c))
        self._check_ok_call(cold, base, 1, 0x07, 0x07, 0x11, 0x22, 0x00)
        self._check_ok_call(cold, base, 19, 0x06, 0x06, 0x66, 0x77, 0x00)
        self._check_common(cold, base, 0xFF, 1)
        self._check_canary(cold)
        self._check_canary(warm)

    def test_2c_body_bytes_pinned(self):
        # 44-byte structural pin: the exact reviewed D2F bytes must be
        # linked in both the unit and the full ExpF images.
        self.assertEqual(len(D2F_BODY), 44)
        for romb, syms, tag in ((self.rom2c.read_bytes(), self.syms2c, "unit"),
                                 (self.rom.read_bytes(), self.syms, "full")):
            body = syms["ex_irqon_entry"] & 0xFFFF
            self.assertEqual(romb[body - 0xE000:body - 0xE000 + 44], D2F_BODY, tag)
        self.assertNotEqual(D2F_BODY[0], 0x60)

    def test_2c_checker_distinguishes(self):
        # The checkers themselves must fire: corrupt each asserted field in
        # turn and hand the buffer to the REAL checker.
        rec, base = self._run("chk", self._defines(0x07, 0x11, 0x22,
                                                     0x06, 0x66, 0x77,
                                                     aone=0xB0, atwo=0x35,
                                                     seed_lo=0xF0, seed_en=1))
        cold, warm = self._buf(rec)
        buf = bytes(cold)
        n = len(buf)
        self.assertGreaterEqual(n, base + 48)
        cases = [(1, 0x07, "a1:=p1"), (14, 0xF0, "shadow1:=seed"),
                 (16, buf[base + 37] ^ 0xFF, "mir1"), (18, 0x00, "zp1"),
                 (19, 0x06, "a2:=p2"), (32, 0xF0, "shadow2:=seed")]
        for pos, val, tag in cases:
            with self.subTest(tag=tag):
                bad = bytearray(buf)
                bad[base + pos] = val
                with self.assertRaises(AssertionError, msg=f"checker blind ({tag})"):
                    if pos in (1, 19):
                        self._check_ok_call(bytes(bad), base, pos,
                                            0x07 if pos == 1 else 0x06,
                                            0xB0 if pos == 1 else 0x35,
                                            0x11 if pos == 1 else 0x66,
                                            0x22 if pos == 1 else 0x77, 0xF0)
                    elif pos in (14, 32):
                        self._check_ok_call(bytes(bad), base, 1 if pos == 14 else 19,
                                            0x07 if pos == 14 else 0x06,
                                            0xB0 if pos == 14 else 0x35,
                                            0x11 if pos == 14 else 0x66,
                                            0x22 if pos == 14 else 0x77, 0xF0)
                    else:
                        self._check_common(bytes(bad), base, 0xFF, 1)

    # ---- T12/T13: SP + MPR1 ----

    def test_2c_sp_boundary_minimum(self):
        # T12: SP=$FF, boundary SP=$06, and minimum SP=$05 are GREEN and
        # balanced (caller minimum $05: JSR 2 + 3 transient = 5 bytes
        # $2105..$2101). Below-minimum is out of contract and never run
        # on the real core (writes-probe contract refusal covers $00-$04
        # without guest execution).
        for sp, tag in ((0xFF, "spff"), (0x06, "sp06"), (0x05, "sp05")):
            with self.subTest(sp=hex(sp)):
                rec, base = self._run(tag, self._defines(0x06, 0x11, 0x22,
                                                         0x06, 0x66, 0x77,
                                                         seed_lo=0x00, seed_en=1,
                                                         spinit=sp))
                cold, warm = self._buf(rec)
                self.assertEqual(cold, warm)
                for buf in (cold, warm):
                    self._check_ok_call(buf, base, 1, 0x06, 0x06, 0x11, 0x22, 0x00)
                    self._check_ok_call(buf, base, 19, 0x06, 0x06, 0x66, 0x77, 0x00)
                    self._check_common(buf, base, sp, 1)
                    self._check_canary(buf)

    def test_mpr1_precondition_refused_before_jsr(self):
        # T13: MPR1=$FF candidate refuses at refused_halt with zero $2C
        # APICALL in both cycles (check-then-act: no JSR, no remap).
        rec, base = self._run("mpr1", {"asm": MPR1_ASM,
                                        "defs": {"BREAK_MPR1": 1}})
        self.assertIn("RUN", rec)
        for r in rec["RUN"]:
            self.assertEqual(r.get("reason"), "cd_halt_fail", r)
            self.assertIs(r.get("halt_confirmed"), True)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec.get("APICALL", []) if c["cycle"] == cycle]
            self.assertEqual(calls, [], cycle)

    def test_mpr1_precondition_positive_control(self):
        # T13 control: MPR1=$F8 runs normally with proven $2C arrival.
        rec, base = self._run("mpr1ok", {"asm": MPR1_ASM,
                                           "defs": {"BREAK_MPR1": 0}})
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual([c["slot"] for c in calls], [0x2C], cycle)
            self.assertEqual(calls[0]["mpr"][1], 0xF8, cycle)
        self.assertEqual(cold[base], 0xAA)

    # ---- VDC-state probe (dedicated GetState snapshots) ----

    def _run_vdc(self, name, entry_ar, areg, xreg, pseed, seed_lo, seed_en):
        import tempfile
        o = self.out / ("vdc-" + name)
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.vdc_probe), str(self.rom2c.resolve()),
                            str(cue.resolve()), "4096", str(entry_ar),
                            str(areg), str(xreg), str(pseed),
                            str(seed_lo), str(seed_en)],
                           capture_output=True, text=True, timeout=120, check=True)
        import json
        for line in r.stdout.splitlines():
            if line.startswith("RESULT "):
                return json.loads(line[7:])
        raise ValueError("no RESULT line\n" + r.stderr)

    def _check_vdc_common(self, res, entry_ar, areg, xreg, pseed, cout):
        # P is per the D2 convention (C forced to cout, T masked), not
        # preserved: entry C must NOT leak into the result.
        self.assertEqual(res["pc"], res["halt"])
        self.assertEqual(res["a"], areg)
        self.assertEqual(res["p"] & ~0x10, (pseed & ~0x11) | cout)
        self.assertEqual(res["p"] & 0x01, cout)
        self.assertEqual(res["x"], xreg)
        self.assertEqual(res["y"], 0xA5)
        self.assertEqual(res["sp"], 0xFF)
        self.assertEqual(res["mpr_store"], res["mpr_after"])
        self.assertEqual(res["mpr_after"][0], 0xF8)
        self.assertEqual(res["mpr_after"][1], 0xF8)
        self.assertEqual(res["mpr_after"][2:], res["mpr_before"][2:])
        self.assertEqual(res["idr_before"], res["idr_after"])
        self.assertEqual(res["irr_before"], res["irr_after"])

    def test_vdc_ok_zero_seed(self):
        # ok: shadow $00 -> CR low $08, AR=5, high/other regs unchanged.
        res = self._run_vdc("ok0", 0, 0x3C, 0x12, 0x07, 0x00, 1)
        self._check_vdc_common(res, 0, 0x3C, 0x12, 0x07, 0)
        self.assertEqual(res["vdc_ar_after"], 5)
        before, after = res["vdc_r_before"], res["vdc_r_after"]
        self.assertEqual(after[5] & 0xFF, 0x08)
        self.assertEqual(after[5] & 0xFF00, before[5] & 0xFF00)
        for i in list(range(5)) + list(range(6, 20)):
            self.assertEqual(after[i], before[i], f"VDC R{i} changed")

    def test_vdc_ok_nonzero_seed(self):
        # ok: shadow $F0 -> CR low $F8 (other bits kept), AR=5.
        res = self._run_vdc("okf0", 9, 0x00, 0x80, 0x06, 0xF0, 1)
        self._check_vdc_common(res, 9, 0x00, 0x80, 0x06, 0)
        self.assertEqual(res["vdc_ar_after"], 5)
        before, after = res["vdc_r_before"], res["vdc_r_after"]
        self.assertEqual(after[5] & 0xFF, 0xF8)
        self.assertEqual(after[5] & 0xFF00, before[5] & 0xFF00)
        for i in list(range(5)) + list(range(6, 20)):
            self.assertEqual(after[i], before[i], f"VDC R{i} changed")

    def test_vdc_ok_idempotent_seed(self):
        # ok: shadow $08 (bit already set) -> CR low stays $08.
        res = self._run_vdc("ok08", 5, 0x5A, 0x34, 0x06, 0x08, 1)
        self._check_vdc_common(res, 5, 0x5A, 0x34, 0x06, 0)
        before, after = res["vdc_r_before"], res["vdc_r_after"]
        self.assertEqual(after[5] & 0xFF, 0x08)
        for i in list(range(5)) + list(range(6, 20)):
            self.assertEqual(after[i], before[i], f"VDC R{i} changed")

    def test_vdc_reject_unseeded(self):
        # reject: no VDC write at all (all 20 regs equal), AR unchanged
        # (no ST0 executed), C=1 out.
        res = self._run_vdc("rej", 2, 0x3C, 0x12, 0x07, 0x00, 0)
        self._check_vdc_common(res, 2, 0x3C, 0x12, 0x07, 1)
        self.assertEqual(res["vdc_ar_after"], 2)
        self.assertEqual(res["vdc_r_before"], res["vdc_r_after"])

    def test_vdc_mutant_wrongbit_red(self):
        # M_WRONGBIT (ORA #$04): seeded $00 must RED (CR=$04, not $08).
        off, new = MUT_WRONGBIT
        rom = self._mutate_body(self.rom2c.read_bytes(), off, new)
        o = self.out / "vdc-wrongbit"
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        p = self.out / "vdc-wrongbit.pce"
        p.write_bytes(bytes(rom))
        r = subprocess.run([str(self.vdc_probe), str(p.resolve()),
                            str(cue.resolve()), "4096", "0", "60", "18", "7",
                            "0", "1"],
                           capture_output=True, text=True, timeout=120, check=True)
        import json
        for line in r.stdout.splitlines():
            if line.startswith("RESULT "):
                res = json.loads(line[7:])
                self.assertNotEqual(res["vdc_r_after"][5] & 0xFF, 0x08,
                                    "wrong-bit mutant not detected")
                self.assertEqual(res["vdc_r_after"][5] & 0xFF, 0x04)
                return
        self.fail("no RESULT line")

    def test_vdc_mutant_novdc_red(self):
        # M_NOVDC (STA $0002 dropped): seeded $00 must RED (CR unchanged).
        off, new = MUT_NOVDC
        rom = self._mutate_body(self.rom2c.read_bytes(), off, new)
        p = self.out / "vdc-novdc.pce"
        p.write_bytes(bytes(rom))
        o = self.out / "vdc-novdc"
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.vdc_probe), str(p.resolve()),
                            str(cue.resolve()), "4096", "0", "60", "18", "7",
                            "0", "1"],
                           capture_output=True, text=True, timeout=120, check=True)
        import json
        for line in r.stdout.splitlines():
            if line.startswith("RESULT "):
                res = json.loads(line[7:])
                self.assertEqual(res["vdc_r_before"], res["vdc_r_after"],
                                 "missing-VDC-write mutant wrote VDC")
                return
        self.fail("no RESULT line")

    def test_vdc_mutant_bypass_red(self):
        # M_BYPASS (BEQ->BRA): unseeded run takes the ok path (CR set,
        # C=0). RED against the reject oracle (CR unchanged + C=1).
        off, new = MUT_BYPASS
        rom = self._mutate_body(self.rom2c.read_bytes(), off, new)
        p = self.out / "vdc-bypass.pce"
        p.write_bytes(bytes(rom))
        o = self.out / "vdc-bypass"
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.vdc_probe), str(p.resolve()),
                            str(cue.resolve()), "4096", "2", "60", "18", "7",
                            "0", "0"],
                           capture_output=True, text=True, timeout=120, check=True)
        import json
        for line in r.stdout.splitlines():
            if line.startswith("RESULT "):
                res = json.loads(line[7:])
                self.assertNotEqual(res["vdc_r_before"], res["vdc_r_after"],
                                    "shadow-bypass mutant did not write VDC")
                self.assertEqual(res["p"] & 0x01, 0x00,
                                 "bypass mutant did not force ok")
                return
        self.fail("no RESULT line")

    def test_vdc_probe_rejects_bad_seed_en(self):
        # New argv (seed_lo/seed_en) fail closed: seed_en=2 exits 2 with
        # no RESULT line.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-2c-argv-") as td:
            o = Path(td)
            (o / "d.bin").write_bytes(bytes(2352 * 32))
            cue = o / "d.cue"
            cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
            r = subprocess.run([str(self.vdc_probe), str(self.rom2c.resolve()),
                                str(cue.resolve()), "4096", "0", "60", "18",
                                "7", "0", "2"],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 2)
            self.assertNotIn("RESULT ", r.stdout)

    # ---- WRAM write-tracer probe ----

    def _run_writes(self, name, spinit, seed_lo, seed_en, rom_bytes=None):
        import tempfile
        rom = self.rom2c if rom_bytes is None else self._tmp_rom(name, rom_bytes)
        o = self.out / ("writes-" + name)
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(self.writes_probe), str(rom.resolve()),
                            str(cue.resolve()), "4096", str(spinit),
                            str(seed_lo), str(seed_en)],
                           capture_output=True, text=True, timeout=120, check=True)
        import json
        recs = [json.loads(line[7:]) for line in r.stdout.splitlines()
                if line.startswith("RESULT ")]
        self.assertEqual(len(recs), 2, "write tracer must report cold+warm")
        cycles = sorted(x["cycle"] for x in recs)
        self.assertEqual(cycles, ["cold", "warm"])
        return recs

    def _tmp_rom(self, name, rom_bytes):
        p = self.out / ("writes-" + name + ".pce")
        p.write_bytes(bytes(rom_bytes))
        return p

    def _written_set(self, res):
        out = set()
        for s, e in res["written"]:
            out.update(range(s, e + 1))
        return out

    def _allowed_2c(self, spinit, seed_en):
        # T05 scope for D2: JSR (2 bytes) + body (3 transient pushes) below
        # SP, plus the guest-established shadow bytes $2502/$2503 (WRAM
        # offsets 0x502/0x503). The guest seed writes and the body publish
        # share these two addresses, so the set assertion proves containment
        # (no out-of-scope write); exact publish is proven by the return
        # probe ($2502 assertions) and the VDC probe (CR change), never by
        # this probe alone. Same-value-store invisibility (see the B1
        # suites) applies: seed writes already matching boot residue are
        # invisible by construction, so membership (not exact equality) of
        # the seed addresses is asserted.
        # ok path: JSR (2) + PHP/PHA/PHA (3) = 5 transient bytes.
        # reject path: JSR (2) + PHP/PHA (2) = 4 transient bytes (no MPR0
        # push: the window is never entered).
        depth = 4 if seed_en else 3
        transient = set(range(0x100 + spinit - depth, 0x100 + spinit + 1))
        return transient | {0x502, 0x503}, transient

    def _check_writes_contained(self, recs, spinit, seed_en):
        allowed, transient = self._allowed_2c(spinit, seed_en)
        seen = []
        for res in recs:
            self.assertTrue(res["halted"], f"tracer did not halt ({res['cycle']})")
            self.assertFalse(res["truncated"], f"history truncated ({res['cycle']})")
            seen.append(self._written_set(res))
        self.assertEqual(seen[0], seen[1], "cold/warm write sets differ")
        self.assertLessEqual(seen[0], allowed,
                             f"out-of-scope writes: {sorted(seen[0] - allowed)}")
        self.assertTrue(transient <= seen[0],
                        f"transient stack writes missing: {sorted(transient - seen[0])}")

    def test_writes_ok_sp_ff(self):
        self._check_writes_contained(self._run_writes("okff", 0xFF, 0x00, 1), 0xFF, 1)

    def test_writes_ok_sp_05(self):
        self._check_writes_contained(self._run_writes("ok05", 0x05, 0x00, 1), 0x05, 1)

    def test_writes_reject_sp_ff(self):
        self._check_writes_contained(self._run_writes("rejff", 0xFF, 0xF0, 0), 0xFF, 0)

    def test_writes_rejects_contract_sp(self):
        # SP=$00/$04 probe-argument refusal: exit 2, no RESULT, no guest.
        # SP=$05 is the caller minimum (GREEN above); below is refused.
        for sp in (0x00, 0x04):
            with self.subTest(sp=hex(sp)):
                import tempfile
                with tempfile.TemporaryDirectory(prefix="pce-2c-wsp-") as td:
                    o = Path(td)
                    (o / "d.bin").write_bytes(bytes(2352 * 32))
                    cue = o / "d.cue"
                    cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
                    r = subprocess.run([str(self.writes_probe),
                                        str(self.rom2c.resolve()),
                                        str(cue.resolve()), "4096", str(sp),
                                        "0", "1"],
                                       capture_output=True, text=True, timeout=120)
                    self.assertEqual(r.returncode, 2, f"SP={sp:#04x} not refused")
                    self.assertNotIn("RESULT ", r.stdout)

    def test_writes_mutant_store_red(self):
        # Out-of-scope store mutant (balanced B1-style STA $2200 in a BOOT
        # cavity, vector repointed): halted cleanly, surplus exactly $2200
        # (WRAM offset 0x200). Legacy oracles stay GREEN is not required
        # here (the $2200 store is inside the res_base region of the
        # return probe, a different guest); the tracer RED is exact.
        mutant = bytes(D2F_BODY[:42]) + bytes((0x8D, 0x00, 0x22, 0x18, 0x60))
        out = bytearray(self.rom2c.read_bytes())
        cav = self._cavity_off(bytes(out), len(mutant))
        cav_addr = 0xE000 + cav
        out[cav:cav + len(mutant)] = mutant
        out[3 * 0x2C:3 * 0x2C + 3] = bytes((0x4C, cav_addr & 0xFF,
                                            (cav_addr >> 8) & 0xFF))
        recs = self._run_writes("mutstore", 0xFF, 0x00, 1, rom_bytes=bytes(out))
        allowed, _ = self._allowed_2c(0xFF, 1)
        seen = []
        for res in recs:
            self.assertTrue(res["halted"])
            self.assertFalse(res["truncated"])
            seen.append(self._written_set(res))
        self.assertEqual(seen[0], seen[1])
        surplus = seen[0] - allowed
        self.assertEqual(surplus, {0x200}, f"unexpected surplus {sorted(surplus)}")

    def test_writes_mutant_restore_red(self):
        # Write-then-restore mutant ($2200 save/$AA/restore inside the ok
        # path): final $2200 value unchanged, write history non-empty.
        # Proves history-vs-final distinction (same guarantee as the B1
        # suites, re-scoped to the D2 body).
        prefix = bytes(D2F_BODY[:31])  # through LDA $2502/ORA (A=img)
        mutant = (prefix
                  + bytes((0x48, 0xAD, 0x00, 0x22, 0x48, 0xA9, 0xAA,
                           0x8D, 0x00, 0x22, 0x68, 0x8D, 0x00, 0x22, 0x68))
                  + bytes(D2F_BODY[31:]))  # STA $2502 .. RTS
        out = bytearray(self.rom2c.read_bytes())
        cav = self._cavity_off(bytes(out), len(mutant))
        cav_addr = 0xE000 + cav
        out[cav:cav + len(mutant)] = mutant
        out[3 * 0x2C:3 * 0x2C + 3] = bytes((0x4C, cav_addr & 0xFF,
                                            (cav_addr >> 8) & 0xFF))
        recs = self._run_writes("mutrestore", 0xFF, 0x00, 1, rom_bytes=bytes(out))
        allowed, _ = self._allowed_2c(0xFF, 1)
        seen = []
        for res in recs:
            self.assertTrue(res["halted"])
            self.assertFalse(res["truncated"])
            seen.append(self._written_set(res))
        self.assertEqual(seen[0], seen[1])
        surplus = seen[0] - allowed
        self.assertIn(0x200, surplus, "restore mutant left no history")

    def _cavity_off(self, rom_bytes, size):
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

    # ---- single-byte body mutants (G3) ----

    def _run_mutant_returns(self, name, off_new, seed_lo, seed_en, entry_c):
        # Helper: apply one in-body mutant ROM patch, run the standard ok
        # or reject payload once, return (cold, warm, base).
        off, new = off_new
        rom = self._mutate_body(self.rom2c.read_bytes(), off, new)
        rec, base = self._run_on_rom(name, rom, self._defines(
            0x06 | entry_c, 0x11, 0x22, 0x06 | entry_c, 0x66, 0x77,
            seed_lo=seed_lo, seed_en=seed_en))
        return self._buf(rec) + (base,)

    def test_mutant_wrongbit_red(self):
        # M_WRONGBIT: CR gets $04 instead of $08. The ok oracle (shadow
        # == seed|$08) must RED on the seeded class.
        cold, warm, base = self._run_mutant_returns(
            "mwrongbit", MUT_WRONGBIT, 0x00, 1, 0)
        with self.assertRaises(AssertionError):
            self._check_ok_call(cold, base, 1, 0x06, 0x06, 0x11, 0x22, 0x00)

    def test_mutant_destroy_red(self):
        # M_DESTROY (AND #$08): seeded $F0 -> $00, other bits destroyed.
        cold, warm, base = self._run_mutant_returns(
            "mdestroy", MUT_DESTROY, 0xF0, 1, 0)
        with self.assertRaises(AssertionError):
            self._check_ok_call(cold, base, 1, 0x06, 0x06, 0x11, 0x22, 0xF0)

    def test_mutant_noop_red(self):
        # M_NOOP (mask op -> NOPs): seeded $00 -> no bit set.
        cold, warm, base = self._run_mutant_returns(
            "mnoop", MUT_NOOP, 0x00, 1, 0)
        with self.assertRaises(AssertionError):
            self._check_ok_call(cold, base, 1, 0x06, 0x06, 0x11, 0x22, 0x00)

    def test_mutant_nopublish_red(self):
        # M_NOPUBLISH (STA $2502 dropped): CR still set (A holds the
        # image), but the shadow is stale. Return-probe shadow oracle REDs;
        # the VDC probe alone would stay GREEN (separation documented).
        cold, warm, base = self._run_mutant_returns(
            "mnopub", MUT_NOPUBLISH, 0x00, 1, 0)
        with self.assertRaises(AssertionError):
            self._check_ok_call(cold, base, 1, 0x06, 0x06, 0x11, 0x22, 0x00)

    def test_mutant_bypass_red(self):
        # M_BYPASS (BEQ->BRA): unseeded run performs the ok path (C=0,
        # shadow published). The reject oracle must RED.
        cold, warm, base = self._run_mutant_returns(
            "mbypass", MUT_BYPASS, 0x00, 0, 0)
        with self.assertRaises(AssertionError):
            self._check_reject_call(cold, base, 1, 0x06, 0x06, 0x11, 0x22, 0x00)

    def test_mutant_clc_drop_red(self):
        # M_CLC_DROP: ok path exits C=entry. Entry C=1 class must RED
        # (exit C=1 vs C=0); entry C=0 class stays GREEN (masks the mutant).
        cold, warm, base = self._run_mutant_returns(
            "mclc", MUT_CLC_DROP, 0x00, 1, 1)
        with self.assertRaises(AssertionError):
            self._check_ok_call(cold, base, 1, 0x07, 0x07, 0x11, 0x22, 0x00)
        rec, base2 = self._run_on_rom("mclc0", self._mutate_body(
            self.rom2c.read_bytes(), *MUT_CLC_DROP), self._defines(
            0x06, 0x11, 0x22, 0x06, 0x66, 0x77, seed_lo=0x00, seed_en=1))
        c0, w0 = self._buf(rec)
        self._check_ok_call(c0, base2, 1, 0x06, 0x06, 0x11, 0x22, 0x00)

    def test_mutant_sec_drop_red(self):
        # M_SEC_DROP: reject exits C=entry. Entry C=0 class must RED.
        cold, warm, base = self._run_mutant_returns(
            "msec", MUT_SEC_DROP, 0x00, 0, 0)
        with self.assertRaises(AssertionError):
            self._check_reject_call(cold, base, 1, 0x06, 0x06, 0x11, 0x22, 0x00)

    def test_mutant_mpr_red(self):
        # M_MPR (MPR0 restore dropped): MPR0 stays $FF. The MPR oracle REDs.
        cold, warm, base = self._run_mutant_returns(
            "mmpr", MUT_MPR, 0x00, 1, 0)
        with self.assertRaises(AssertionError):
            self._check_common(cold, base, 0xFF, 1)

    # ---- synthetic ExpF chain leg ----

    def test_chain_33_4a_23_25_24_36_35_2c(self):
        # Synthetic G5 chain leg on the full ExpF image: the PUBLIC boot
        # ($02 leg) loads this payload, which seeds the shared shadow then
        # calls $33 -> $4A -> $23 -> $25 -> $24 -> $36 -> $35 -> $2C through
        # the real table entries. Per-call entry evidence + end-to-end
        # preservation + final shadow state prove routing and resumption.
        # The observed $2C entry state is SIMULATED-entry, never genuine.
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
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {self.labels_full['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels_full['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels_full['api_unimplemented']:04x}\n", encoding="ascii")
        import geargrafx_api_entry as Eentry
        rec = Eentry.run_probe(self.probe, self.rom, cue, lab, BUDGET, "synthetic")
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual([c["slot"] for c in calls],
                             [0x33, 0x4A, 0x23, 0x25, 0x24, 0x36, 0x35, 0x2C], cycle)
            self.assertEqual([c["entry_pc"] for c in calls],
                             [0xE099, 0xE0DE, 0xE069, 0xE06F, 0xE06C, 0xE0A2,
                              0xE09F, 0xE084], cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))
                self.assertEqual(c["mpr"][1], 0xF8, (cycle, c))
        self.assertEqual(cold[base], 0xAA)
        self.assertEqual((cold[base + 1], cold[base + 2], cold[base + 3]),
                         (0x31, 0x12, 0x34))
        # Last chain call is the $2C ok path: entry P=$07 (C=1) exits C=0.
        self.assertEqual(cold[base + 4] & ~0x10, 0x06)
        self.assertEqual(cold[base + 4] & 0x01, 0x00)
        self.assertEqual(cold[base + 5], 0xFF)
        self.assertEqual(list(cold[base + 6:base + 14]),
                         [0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        self.assertEqual(cold[base + 14], 0xA5)
        self.assertEqual(cold[base + 15], 0xA5)
        self._check_canary(cold)
        self._check_canary(warm)
        for cycle in ("cold", "warm"):
            call2c = [c for c in rec["APICALL"]
                      if c["cycle"] == cycle and c["slot"] == 0x2C][0]
            self.assertEqual((call2c["caller_a"], call2c["caller_x"],
                              call2c["caller_y"]), (0x31, 0x12, 0x34), cycle)
            # Caller P at the $2C call is $06 (C=0): the payload entered the
            # chain with C=1, the $33 DSPOFF ok path cleared it (family C=0
            # convention), and every downstream B1 scaffold preserved C=0.
            # SIMULATED-entry fact under fabricated predecessors, not genuine.
            self.assertEqual(call2c["caller_p"] & ~0x10, 0x06, cycle)
            self.assertEqual(call2c["mpr"][1], 0xF8, cycle)


if __name__ == "__main__":
    unittest.main()
