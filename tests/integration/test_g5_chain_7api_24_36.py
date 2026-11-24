"""Synthetic real-core 7-API chain for integrated $24+$36 (Issues #46 + #64).

Proves the merged main configuration end to end on synthetic media with the
pinned core: PUBLIC IPL boot ($02 CD_BASE 2-base leg) loads this payload,
which then calls $33 -> $4A -> $23 -> $25 -> $24 -> $36 through the real
table entries. Each APICALL records per-call entry evidence only (stacked
return bytes via return_matches_caller + stack_check MATCH, relative SP
change, MPR1 at entry); it does not observe the RTS destination or the full
MPR set. End-to-end resumption and preservation are verified by the final
record below plus halt. The observed $24/$36 entry states are
SIMULATED-entry (conditional on fabricated predecessors), never genuine
ABI input evidence.

Scope discipline (review threads):
- WRAM value-change (canary + res records) is distinguished from real write
  events: the writes-probe history is the stronger no-write guarantee, the
  static 9-byte pin is stronger than same-value-store invisibility.
- VDC snapshot invariance is distinguished from a no-write guarantee.
- APICALL entry evidence (stacked-return bytes at entry) is distinguished
  from RTS return observation (APIRETURN not collected).
- T=1 excluded (verified T=0 domain only); MPR1!=$F8 BLOCKED/out of contract
  (harness validates BEFORE JSR); SP<$06 out of contract (guest NOT_RUN).

NOT a compatible System Card BIOS. NOT genuine ABI evidence. NOT
DEV_CALLABLE. release_eligible=False. G5_OBSERVED=False. Synthetic only;
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

CHAIN7_ASM = r"""
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

B1_BODY = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x60))


class Chain7Api2436Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-7api-")
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
        # Full experimental image: all four opt-ins.
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "m7api", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False,
            cd_4a="experimental", cd_25="experimental",
            cd_24="experimental", cd_36="experimental")
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        # Table routing: all four experimental slots wired to BOOT bodies.
        data = cls.rom.read_bytes()
        for slot, entry in ((0x4A, "ex_memopen_entry"),
                            (0x25, "ex_scrmod_entry"),
                            (0x24, "ex_dotmod_entry"),
                            (0x36, "ex_satclr_entry")):
            body = cls.syms[entry]
            tab = data[3 * slot:3 * slot + 3]
            assert tuple(tab) == (0x4C, body & 0xFF, (body >> 8) & 0xFF), (slot, entry)
            assert 0xF000 <= body <= 0xFFF6 - 9, (slot, body)
        # Manifest records all four.
        import json
        man = json.loads((cls.out / "manifest-m7api-g5.json").read_text())
        assert man["cd_4a"] == "experimental"
        assert man["cd_25"] == "experimental"
        assert man["cd_24"] == "experimental"
        assert man["cd_36"] == "experimental"
        assert set(["$02", "$03", "$33", "$4A", "$25", "$24", "$36"]) <= set(man["experimental_slots"])
        assert man["release_eligible"] is False
        assert man["dev_callable"] is False
        assert len(data) == 262144
        trace = cls.out / "ipl-loader-g5-m7api.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "m7api-probe")
        import geargrafx_ipl_loader as L
        cls.L = L

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tmp = cls._tmp.name
        assert "pce-7api-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

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

    def _check_canary(self, buf):
        self.assertEqual(buf[0x100:0x200], bytes((0x5A,) * 256))

    def test_chain_02_33_4a_23_25_24_36(self):
        # $02 PUBLIC boot loads the payload (boot success is the $02 leg;
        # the probe APICALL list covers the six payload calls below).
        # Order: $33->$4A->$23->$25->$24->$36 through the real table.
        import json
        o = self.out / "chain7"
        o.mkdir(exist_ok=True)
        src = o / "chain7.s"
        src.write_text(CHAIN7_ASM, encoding="ascii")
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        obj, binp, cfgp, lblp = (o / "chain7.o", o / "chain7.bin",
                                 o / "chain7.cfg", o / "chain7.lbl")
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
        lab = o / "chain7.labels"
        lab.write_text(
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {self.labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels['api_unimplemented']:04x}\n", encoding="ascii")
        import geargrafx_api_entry as Eentry
        rec = Eentry.run_probe(self.probe, self.rom, cue, lab, BUDGET, "synthetic")
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        # Per-call entry evidence, in order (six payload calls; $02 boot
        # success is proved by the payload running at all + halt).
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual([c["slot"] for c in calls],
                             [0x33, 0x4A, 0x23, 0x25, 0x24, 0x36], cycle)
            self.assertEqual([c["entry_pc"] for c in calls],
                             [0xE099, 0xE0DE, 0xE069, 0xE06F, 0xE06C, 0xE0A2], cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))
                self.assertEqual(c["mpr"][1], 0xF8, (cycle, c))
            # NOTE: return_matches_caller + MATCH observe stacked return
            # bytes at entry, not the RTS destination (APIRETURN not
            # collected). End-to-end return is proved below + halt.
            self.assertEqual(len(calls), 6, cycle)
            self.assertIs(rec["RUN"][0].get("apicall_truncated"), False)
            self.assertIs(rec["RUN"][0].get("ret_truncated"), False)
        # End-to-end preservation across all six calls.
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
        self._check_canary(cold)
        self._check_canary(warm)
        # SIMULATED-entry: $24/$36 reached with payload-set state intact.
        for cycle in ("cold", "warm"):
            call24 = [c for c in rec["APICALL"]
                      if c["cycle"] == cycle and c["slot"] == 0x24][0]
            call36 = [c for c in rec["APICALL"]
                      if c["cycle"] == cycle and c["slot"] == 0x36][0]
            for call in (call24, call36):
                self.assertEqual((call["caller_a"], call["caller_x"], call["caller_y"]),
                                 (0x31, 0x12, 0x34), (cycle, call["slot"]))
                self.assertEqual(call["caller_p"] & ~0x10, 0x07, (cycle, call["slot"]))
                self.assertEqual(call["mpr"][1], 0xF8, (cycle, call["slot"]))
        # 9-byte body pins for both $24 and $36 in this ROM.
        data = self.rom.read_bytes()
        for entry in ("ex_dotmod_entry", "ex_satclr_entry"):
            off = (self.syms[entry] & 0xFFFF) - 0xE000
            self.assertEqual(data[off:off + 9], B1_BODY, entry)

    def test_24_36_body_pins(self):
        data = self.rom.read_bytes()
        for entry in ("ex_dotmod_entry", "ex_satclr_entry"):
            off = (self.syms[entry] & 0xFFFF) - 0xE000
            self.assertEqual(data[off:off + 9], B1_BODY, entry)
            self.assertTrue(0xF000 <= (self.syms[entry] & 0xFFFF) <= 0xFFF6 - 9, entry)

    def _run_chain_on_rom(self, rom_path, tag):
        import re, subprocess
        o = self.out / tag
        o.mkdir(exist_ok=True)
        src = o / "c7.s"
        src.write_text(CHAIN7_ASM, encoding="ascii")
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        obj, binp, cfgp, lblp = (o / "c7.o", o / "c7.bin", o / "c7.cfg", o / "c7.lbl")
        cfgp.write_text(CFG, encoding="ascii")
        import environment
        ca65, ld65 = environment.executable("ca65"), environment.executable("ld65")
        subprocess.run([ca65, str(src), "-o", str(obj), "-g"], check=True)
        subprocess.run([ld65, "-C", str(cfgp), "-o", str(binp), "-Ln", str(lblp), str(obj)], check=True)
        text = lblp.read_text(encoding="ascii")
        m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", text, re.M)
        halt = int(m.group(1), 16)
        lab = o / "c7.labels"
        lab.write_text(
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {self.labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels['api_unimplemented']:04x}\n", encoding="ascii")
        import geargrafx_api_entry as Eentry
        code2 = binp.read_bytes()
        body2 = self.L.body_for(code2, 2)
        info2 = self.L.info_sector(2, 2, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0,
                                   sum(body2) & 0xFFFF)
        cue2 = self.L.make_disc(o, self.L.case_disc({"n": 2, "iplblk": 2}, body2, info2))
        return Eentry.run_probe(self.probe, rom_path, cue2, lab, BUDGET, "synthetic")

    def test_mutant_24_wiring_missing_red(self):
        # Integration mutant: restore $E06C to the normal stub; the 7-API
        # chain must NOT clean-halt (RED: routing broken, stub traps).
        normal = self.normal
        exp = bytearray(self.rom.read_bytes())
        exp[3 * 0x24:3 * 0x24 + 3] = normal[3 * 0x24:3 * 0x24 + 3]
        mut_path = self.out / "mut24.pce"
        mut_path.write_bytes(bytes(exp))
        rec = self._run_chain_on_rom(mut_path, "mut24")
        reasons = sorted(set(r.get("reason") for r in rec.get("RUN", [])))
        self.assertNotIn("cd_halt_ok", reasons,
                         f"mutant $24 wiring missing still clean-halts (must RED): {reasons}")

    def test_mutant_36_wiring_missing_red(self):
        # $36 is the last call: the stub still records an APICALL entry, so
        # slot-list equality alone cannot RED. Detection is via the stub
        # trap (api_unimplemented, no clean-halt resumption).
        normal = self.normal
        exp = bytearray(self.rom.read_bytes())
        exp[3 * 0x36:3 * 0x36 + 3] = normal[3 * 0x36:3 * 0x36 + 3]
        mut_path = self.out / "mut36.pce"
        mut_path.write_bytes(bytes(exp))
        rec = self._run_chain_on_rom(mut_path, "mut36")
        reasons = sorted(set(r.get("reason") for r in rec.get("RUN", [])))
        self.assertNotIn("cd_halt_ok", reasons,
                         f"mutant $36 wiring missing still clean-halts (must RED): {reasons}")
