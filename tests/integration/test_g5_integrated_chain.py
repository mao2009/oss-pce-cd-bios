"""Synthetic real-core tests for the integrated G5 chain (Issue #802, parent #635).

Proves the CURRENT main integrated configuration end to end on synthetic
media with the pinned core: PUBLIC IPL boot (2-base builder, commercial FIRST
default) -> $E006 (CD_BASE 2-base TRACK) -> $E009 (CD_READ commercial FIRST)
-> $E099 (EX_DSPOFF fragment) -> halt. All calls go through the real table
entries (no direct-body JSR, no fake RTS). Companion control coverage:

  control ROM (cd_read="link") diverges on the same payload ($03 refuses);
  bad _dh=1 refuses fail-closed with the transfer window untouched;
  corruption/tamper mutants prove the checks detect false success;
  unimplemented slot $04 still traps fail-closed;
  static ROM identity/table-routing proof incl. $4A ($E0DE) untouched.

NOT a compatible System Card BIOS. NOT genuine ABI evidence. NOT
DEV_CALLABLE. release_eligible=false. G5_OBSERVED=false. Synthetic only;
commercial media: NOT_RUN here (no ./rom probing in this file).
$4A genuine ABI stays UNKNOWN/BLOCKED and is never implemented here.
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

CHAIN_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    lda #$AA
    ldx #$00
can: sta $2800,x
    sta $2900,x
    sta $2A00,x
    sta $2B00,x
    sta $2C00,x
    sta $2D00,x
    sta $2E00,x
    sta $2F00,x
    dex
    bne can
    lda #$00
    ldx #$09
zc: sta $2330,x
    sta $2320,x
    dex
    bpl zc
    lda #$00
    ldx #$07
bz: sta $2400,x
    dex
    bpl bz
    lda #$FF
    sta $2404
    lda #TRACKNUM
    sta $20F8
    lda #$00
    sta $20F9
    sta $20FA
    sta $20FD
    sta $20FE
    sta $20FF
    lda #$80
    sta $20FB
    lda #$01
    sta $20FC
    ldx #$FF
    txs
    jsr $E006
    sta res_base+1
    php
    pla
    sta res_base+2
    lda #$02
    sta res_base+3
    ldx #$07
cpst: lda $2400,x
    sta res_base+4,x
    dex
    bpl cpst
    lda $2400
    sta res_base+12
    lda $2401
    sta res_base+13
    lda $2402
    sta res_base+14
    lda $2404
    sta res_base+15
    lda #$FF
    sta $20F2
    sta $20F3
    sta $20F4
    sta $20F5
    sta $20F6
    sta $20F7
    lda #$00
    sta $20FC
    sta $20FD
    sta $20FE
    sta $20FF
    sta $20F8
    lda #$08
    sta $20F9
    lda #$00
    sta $20FA
    lda #$28
    sta $20FB
    tma #$01
    sta res_base+20
    tma #$02
    sta res_base+21
    tma #$04
    sta res_base+22
    tma #$08
    sta res_base+23
    tma #$10
    sta res_base+24
    tma #$20
    sta res_base+25
    tma #$40
    sta res_base+26
    tma #$80
    sta res_base+27
    ldx #$12
    ldy #$34
    jsr $E009
    sta res_base+28
    php
    pla
    sta res_base+29
    stx res_base+32
    sty res_base+33
    tsx
    txa
    sta res_base+30
    lda #$03
    sta res_base+31
    tma #$01
    sta res_base+34
    tma #$02
    sta res_base+35
    tma #$04
    sta res_base+36
    tma #$08
    sta res_base+37
    tma #$10
    sta res_base+38
    tma #$20
    sta res_base+39
    tma #$40
    sta res_base+40
    tma #$80
    sta res_base+41
    ldx #$09
rc: lda $2330,x
    sta res_base+42,x
    lda $2320,x
    sta res_base+52,x
    dex
    bpl rc
    ldx #$07
hd: lda $2800,x
    sta res_base+62,x
    dex
    bpl hd
    lda #CRLOW
    sta $2502
    lda #$A5
    sta $2503
    sei
    lda #$12
    ldx #$34
    ldy #$56
    jsr $E099
    sta res_base+70
    php
    pla
    sta res_base+71
    tsx
    txa
    sta res_base+72
    lda #$33
    sta res_base+73
    lda $2502
    sta res_base+74
    lda $2503
    sta res_base+75
    tma #$01
    sta res_base+76
    tma #$02
    sta res_base+77
    tma #$04
    sta res_base+78
    tma #$08
    sta res_base+79
    tma #$10
    sta res_base+80
    tma #$20
    sta res_base+81
    tma #$40
    sta res_base+82
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 96
"""

BADMODE_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    lda #$AA
    ldx #$00
can: sta $2800,x
    sta $2900,x
    sta $2A00,x
    sta $2B00,x
    sta $2C00,x
    sta $2D00,x
    sta $2E00,x
    sta $2F00,x
    dex
    bne can
    lda #$00
    ldx #$09
zc: sta $2330,x
    sta $2320,x
    dex
    bpl zc
    lda #$00
    ldx #$07
bz: sta $2400,x
    dex
    bpl bz
    lda #$FF
    sta $2404
    lda #TRACKNUM
    sta $20F8
    lda #$00
    sta $20F9
    sta $20FA
    sta $20FD
    sta $20FE
    sta $20FF
    lda #$80
    sta $20FB
    lda #$01
    sta $20FC
    ldx #$FF
    txs
    jsr $E006
    lda #$00
    sta $20FC
    sta $20FD
    sta $20FE
    sta $20F8
    lda #$01
    sta $20FF
    lda #$08
    sta $20F9
    lda #$00
    sta $20FA
    lda #$28
    sta $20FB
    jsr $E009
    sta res_base+28
    php
    pla
    sta res_base+29
    ldx #$09
rc: lda $2330,x
    sta res_base+42,x
    dex
    bpl rc
    ldx #$07
hd: lda $2800,x
    sta res_base+62,x
    dex
    bpl hd
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 96
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


class G5IntegratedChainSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-g5-chain-")
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
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "vch", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False, cd_read="commercial")
        cls.rom_ctrl, cls.syms_ctrl = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "vch-ctrl", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False, cd_read="link")
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        for rom_bytes, syms, entry, name in (
                (cls.rom.read_bytes(), cls.syms, "cd_read_commercial_entry", "exp"),
                (cls.rom_ctrl.read_bytes(), cls.syms_ctrl, "cd_read_link_entry", "ctrl")):
            tab = rom_bytes[0xE009 - 0xE000:0xE009 - 0xE000 + 3]
            assert tuple(tab) == (0x4C, syms[entry] & 0xFF,
                                  (syms[entry] >> 8) & 0xFF), (name, entry)
        trace = cls.out / "ipl-loader-g5-vch.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        trace_ctrl = cls.out / "ipl-loader-g5-vch-ctrl.lbl"
        cls.labels_ctrl = parse_labels(
            trace_ctrl, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "vch-probe")
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
        assert "pce-g5-chain-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    def _run(self, name, source, defines, rom_kind="exp"):
        import geargrafx_api_entry as Eentry
        if rom_kind == "exp":
            rom, labels = self.rom, self.labels
        elif rom_kind == "ctrl":
            rom, labels = self.rom_ctrl, self.labels_ctrl
        else:
            raise ValueError("rom_kind must be 'exp' or 'ctrl'")
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        o = self.out / name
        o.mkdir(exist_ok=True)
        src = o / (name + ".s")
        src.write_text(source, encoding="ascii")
        obj, binp, cfgp, lblp = (o / (name + ".o"), o / (name + ".bin"),
                                 o / (name + ".cfg"), o / (name + ".lbl"))
        cfgp.write_text(CFG, encoding="ascii")
        import environment
        ca65, ld65 = environment.executable("ca65"), environment.executable("ld65")
        subprocess.run([ca65, str(src), *defines, "-o", str(obj), "-g"], check=True)
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
        assert res_base + 96 <= 0x1800, syms
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
        self._last_cue, self._last_lab = cue, lab
        self._last_bin = o / "oss-pce-cd-test.bin"
        rec = Eentry.run_probe(self.probe, rom, cue, lab, BUDGET, "synthetic")
        return rec, res_base

    def _buf(self, rec):
        runs = rec["RUN"]
        self.assertEqual(len(runs), 2)
        by_cycle = {r["cycle"]: bytes.fromhex(r["buffer_hex"]) for r in runs}
        self.assertEqual(sorted(by_cycle), ["cold", "warm"])
        return by_cycle["cold"], by_cycle["warm"]

    def _expected_frame(self, lba):
        raw = self._last_bin.read_bytes()
        at = lba * self.RAW_BYTES + self.USER_OFFSET
        return list(raw[at:at + 2048])

    def _check_full(self, got, exp, tag):
        self.assertEqual(list(got)[:2048], list(exp)[:2048], tag)

    def _chain_defines(self):
        return ["-DTRACKNUM=2", "-DCRLOW=242"]

    def test_chain_2base_commercial_dspoff(self):
        rec, base = self._run("chain", CHAIN_ASM, self._chain_defines())
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = cold
        self.assertEqual(r[base], 0xAA)  # payload ran => PUBLIC boot OK
        self.assertEqual([r[base + 3], r[base + 31], r[base + 73]], [0x02, 0x03, 0x33])
        # $02 2-base TRACK: real return, recbase0 == INDEX01.
        self.assertEqual(r[base + 1], 0)
        self.assertEqual(r[base + 2] & 0x03, 0x02)
        self.assertEqual(list(r[base + 4:base + 12]), [0, 1, 9, 1, 0, 0, 0, 0])
        self.assertEqual(tuple(r[base + 12:base + 15]), (0, 1, 9))
        self.assertEqual(r[base + 15], 0)
        # $03 commercial FIRST: success, stage 8, 2048 bytes, LBA == sel.
        self.assertEqual(r[base + 28], 0)
        self.assertEqual(r[base + 29] & 0x03, 0x02)
        self.assertEqual(r[base + 30], 0xFF)
        self.assertEqual((r[base + 32], r[base + 33]), (0x12, 0x34))
        self.assertEqual(list(r[base + 20:base + 28]),
                         [0xFF, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        self.assertEqual(list(r[base + 20:base + 28]), list(r[base + 34:base + 42]))
        self.assertEqual(list(r[base + 42:base + 52]), [0, 8, 0, 8, 0, 0, 0, 0, 1, 9])
        self.assertEqual(list(r[base + 52:base + 62]), [0] * 10)
        exp = self._expected_frame(SEL)
        self._check_full(r[0:2048], exp, "chain full-2048")
        self.assertEqual(list(r[base + 62:base + 70]), list(exp[:8]))
        # $33 DSPOFF fragment: A=$12, shadow masked, SP/MPR intact.
        self.assertEqual(r[base + 70], 0x12)
        self.assertEqual(r[base + 72], 0xFF)
        self.assertEqual(tuple(r[base + 74:base + 76]), (0xF2 & 0x3F, 0xA5))
        self.assertEqual(list(r[base + 76:base + 78]), [0xFF, 0xF8])
        self.assertEqual(list(r[base + 78:base + 83]), [0x80, 0x81, 0x82, 0x83, 0x84])

    def test_chain_apicall_slot_order(self):
        rec, base = self._run("order", CHAIN_ASM, self._chain_defines())
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            slots = [c["slot"] for c in calls]
            self.assertEqual(slots, [2, 3, 0x33], cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))

    def test_chain_control_rom_refuses(self):
        rec, base = self._run("ctrl", CHAIN_ASM, self._chain_defines(), rom_kind="ctrl")
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = cold
        self.assertEqual(r[base], 0xAA)
        self.assertEqual(r[base + 1], 0)  # $02 2-base still returns on control ROM
        self.assertNotEqual(r[base + 28], 0)  # link V1 refuses the $FFFFFF block
        self.assertEqual(list(r[base + 42:base + 52]), [0] * 10)  # no CRES writes
        self.assertEqual(list(r[0:16]), [0xAA] * 16)  # transfer window untouched
        self.assertEqual([r[base + 3], r[base + 31], r[base + 73]], [0x02, 0x03, 0x33])
        # Review regression: the control probe must use the control ROM's own
        # halt labels (loader CODE shifts with the BOOT body size; exp and ctrl
        # cd_halt_fail/cd_unexpected_interrupt differ). With the wrong labels
        # a control failure path would run to budget unrecognized.
        lab_text = Path(self._last_lab).read_text(encoding="ascii")
        self.assertIn(f"cd_halt_fail {self.labels_ctrl['cd_halt_fail']:04x}", lab_text)
        self.assertIn(f"cd_unexpected_interrupt {self.labels_ctrl['cd_unexpected_interrupt']:04x}",
                        lab_text)
        runs = {r["cycle"]: r for r in rec["RUN"]}
        for cycle, run in runs.items():
            with self.subTest(cycle=cycle):
                self.assertEqual(run["reason"], "cd_halt_ok")
                self.assertTrue(run["halt_confirmed"])

    def test_chain_bad_mode_fail_closed(self):
        rec, base = self._run("badmode", BADMODE_ASM, ["-DTRACKNUM=2"])
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = cold
        self.assertEqual(r[base], 0xAA)
        self.assertEqual(r[base + 28], 0x65)  # _dh=1 refused
        self.assertEqual(list(r[base + 42:base + 52]), [0] * 10)  # no CRES writes
        self.assertEqual(list(r[0:16]), [0xAA] * 16)  # canary intact

    def test_chain_mutants_detected(self):
        rec, base = self._run("mutant", CHAIN_ASM, self._chain_defines())
        cold, _ = self._buf(rec)
        exp = self._expected_frame(SEL)
        self._check_full(cold[0:2048], exp, "mutant baseline full-2048")
        for tag, pos in (("head", 0), ("middle", 1000), ("tail", 2047)):
            bad = bytearray(cold[0:2048])
            bad[pos] ^= 0xFF
            with self.assertRaises(AssertionError, msg=f"corrupt {tag}"):
                self._check_full(bad, exp, f"mutant corrupt {tag}")
        self.assertEqual(list(cold[base + 20:base + 28]),
                         list(cold[base + 34:base + 42]))  # MPR pre == post
        tampered_pre = bytearray(cold[base + 20:base + 28])
        tampered_pre[3] ^= 0xFF
        self.assertNotEqual(list(tampered_pre), list(cold[base + 34:base + 42]))

    def test_chain_unimplemented_slot_traps(self):
        rec, base = self._run("unimpl", UNIMPL_ASM, [])
        runs = {r["cycle"]: r for r in rec["RUN"]}
        self.assertEqual(sorted(runs), ["cold", "warm"])
        for cycle, run in runs.items():
            with self.subTest(cycle=cycle):
                self.assertEqual(run["reason"], "api_unimplemented")
                self.assertTrue(run["halt_confirmed"])
                self.assertEqual(run["pc"], self.labels["api_unimplemented"])
                self.assertEqual(run["x"], 0x04)
                self.assertEqual(run["sp"], 0xFD)

    def test_chain_rom_identity_and_table_routing(self):
        import hashlib
        exp = self.rom.read_bytes()
        ctrl = self.rom_ctrl.read_bytes()
        self.assertNotEqual(hashlib.sha256(exp).digest(), hashlib.sha256(ctrl).digest())
        for rom_bytes, syms, entry, name in (
                (exp, self.syms, "cd_read_commercial_entry", "exp"),
                (ctrl, self.syms_ctrl, "cd_read_link_entry", "ctrl")):
            tab = rom_bytes[0xE009 - 0xE000:0xE009 - 0xE000 + 3]
            self.assertEqual(tuple(tab), (0x4C, syms[entry] & 0xFF,
                                          (syms[entry] >> 8) & 0xFF), name)
        for rom_bytes, name in ((exp, "exp"), (ctrl, "ctrl")):
            tab02 = rom_bytes[0xE006 - 0xE000:0xE006 - 0xE000 + 3]
            self.assertEqual(tab02[0], 0x4C, name)  # $02 is JMP, never fake RTS
            tab33 = rom_bytes[0xE099 - 0xE000:0xE099 - 0xE000 + 3]
            self.assertEqual(tab33[0], 0x4C, name)  # $33 is JMP, never fake RTS
        # $4A ($E0DE) stays the untouched stub on both G5 ROMs (SIMULATED
        # control lives in a separate builder; never mixed in here).
        at_4a = 3 * 0x4A
        self.assertEqual(exp[at_4a:at_4a + 3], self.normal[at_4a:at_4a + 3])
        self.assertEqual(ctrl[at_4a:at_4a + 3], self.normal[at_4a:at_4a + 3])


if __name__ == "__main__":
    unittest.main()
