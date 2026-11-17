"""Synthetic real-core tests for CD_BASE 2-base variant (G5, pinned core).

Covers: recbase0/1/BOTH/NONE, LBA/MSF/Track, BCD boundary, nonexistent track,
INDEX01 boundary, 21-bit range, cold/warm, MPR/SP, fail-closed invalids.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests" / "integration")]


class CdBase2BaseSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        orig = ROOT / "build" / "debug" / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
        cls.out = Path("/tmp/pce-2base-synth-v2")
        cls.out.mkdir(parents=True, exist_ok=True)
        cls.sel = 0x109
        cls.track_sel = 2
        rom_name = "dev-ipl-loader-g5-not-compatible-v2t.pce"
        cls.rom = cls.out / rom_name
        if not cls.rom.is_file():
            rom, _ = build_ipl_loader_g5_rom(
                orig, cls.out, cls.sel, "debug", "v2t", ["-D", "IPL_PUBLIC"],
                track=cls.track_sel, private_input=False)
            cls.rom = rom
        trace = cls.out / "ipl-loader-g5-v2t.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "v2t-probe")
        import geargrafx_ipl_loader as L
        cls.L = L

    def _run(self, name, regs):
        import geargrafx_api_entry as Eentry
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        sets = "\n ".join(f"lda #${v:02X}\n sta ${a:04X}" for a, v in sorted(regs.items()))
        asm = (".setcpu \"huc6280\"\n.segment \"CODE\"\nduck: nop\n" if False else
               ".setcpu \"huc6280\"\n.segment \"CODE\"\n"
               "payload_entry:\n"
               " lda #$00\n ldx #$07\nlz: sta $2400,x\n dex\n bpl lz\n"
               " lda #$FF\n sta $2404\n lda #$00\n sta $2408\n sta $2409\n sta $240A\n"
               f" {sets}\n jsr $E006\n"
               " sta $2F80\n php\n pla\n sta $2F81\n"
               " lda $2400\n sta $2F82\n lda $2401\n sta $2F83\n lda $2402\n sta $2F84\n"
               " lda $2408\n sta $2F85\n lda $2409\n sta $2F86\n lda $240A\n sta $2F87\n"
               " lda $2404\n sta $2F88\n"
               "payload_halt:\n bra payload_halt\n")
        o = self.out / name
        o.mkdir(exist_ok=True)
        src = o / (name + ".s")
        src.write_text(asm, encoding="ascii")
        obj, binp, cfgp, lblp = o / (name + ".o"), o / (name + ".bin"), o / (name + ".cfg"), o / (name + ".lbl")
        cfgp.write_text(CFG, encoding="ascii")
        subprocess.run([__import__("environment").executable("ca65"), str(src), "-o", str(obj), "-g"], check=True)
        subprocess.run([__import__("environment").executable("ld65"), "-C", str(cfgp), "-o", str(binp), "-Ln", str(lblp), str(obj)], check=True)
        code = binp.read_bytes()
        body = self.L.body_for(code, 1)
        info = self.L.info_sector(2, 1, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0, sum(body) & 0xFFFF)
        cue = self.L.make_disc(self.out / name, self.L.case_disc({"n": 1, "iplblk": 2}, body, info))
        t = (o / (name + ".lbl")).read_text()
        m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", t, re.M)
        halt = int(m.group(1), 16)
        lab = o / (name + ".labels")
        lab.write_text(
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {self.labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels['api_unimplemented']:04x}\n", encoding="ascii")
        rec = Eentry.run_probe(self.probe, self.rom, cue, lab, 2_000_000, "synthetic")
        return rec

    def _buf_vals(self, rec):
        run = [r for r in rec["RUN"] if r["cycle"] == "cold"][0]
        buf = bytes.fromhex(run["buffer_hex"])
        base = 0x780
        return list(buf[base:base + 9])

    def test_recbase0_track_first(self):
        rec = self._run("v2t01", {0x20F8: 0x02, 0x20F9: 0, 0x20FA: 0, 0x20FB: 0x80,
                                   0x20FC: 1, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        v = self._buf_vals(rec)
        self.assertEqual(v[0], 0x00)  # A
        self.assertEqual((v[1] & 1), 0)  # C=0
        self.assertEqual((v[2], v[3], v[4]), (0x00, 0x01, 0x09))  # recbase0=INDEX01
        self.assertEqual((v[5], v[6], v[7]), (0x00, 0x00, 0x00))  # recbase1 unchanged
        self.assertEqual(v[8], 0x00)  # ST_LAST

    def test_recbase1_lba_second(self):
        rec = self._run("v2t02", {0x20F8: 0x00, 0x20F9: 0x01, 0x20FA: 0x09, 0x20FB: 0x00,
                                   0x20FC: 2, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        v = self._buf_vals(rec)
        self.assertEqual(v[0], 0x00)
        self.assertEqual((v[2], v[3], v[4]), (0x00, 0x00, 0x00))
        self.assertEqual((v[5], v[6], v[7]), (0x00, 0x01, 0x09))
        self.assertEqual(v[8], 0x00)

    def test_both_lba(self):
        rec = self._run("v2t03", {0x20F8: 0x00, 0x20F9: 0x01, 0x20FA: 0x09, 0x20FB: 0x00,
                                   0x20FC: 0, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        v = self._buf_vals(rec)
        self.assertEqual(v[0], 0x00)
        self.assertEqual((v[2], v[3], v[4]), (0x00, 0x01, 0x09))
        self.assertEqual((v[5], v[6], v[7]), (0x00, 0x01, 0x09))

    def test_none_track(self):
        rec = self._run("v2t04", {0x20F8: 0x02, 0x20F9: 0, 0x20FA: 0, 0x20FB: 0x80,
                                   0x20FC: 3, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        v = self._buf_vals(rec)
        self.assertEqual(v[0], 0x00)
        self.assertEqual((v[2], v[3], v[4]), (0x00, 0x00, 0x00))
        self.assertEqual((v[5], v[6], v[7]), (0x00, 0x00, 0x00))

    def test_msf_000200(self):
        rec = self._run("v2t06", {0x20F8: 0x00, 0x20F9: 0x02, 0x20FA: 0x00, 0x20FB: 0x40,
                                   0x20FC: 1, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        v = self._buf_vals(rec)
        self.assertEqual(v[0], 0x00)
        self.assertEqual((v[2], v[3], v[4]), (0x00, 0x00, 0x00))  # LBA 0

    def test_bad_track_bcd01(self):
        rec = self._run("v2t08", {0x20F8: 0x01, 0x20F9: 0, 0x20FA: 0, 0x20FB: 0x80,
                                   0x20FC: 1, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        v = self._buf_vals(rec)
        self.assertEqual(v[0], 0x15)
        self.assertEqual(v[8], 0x15)

    def test_bad_track_99(self):
        rec = self._run("v2t09", {0x20F8: 0x99, 0x20F9: 0, 0x20FA: 0, 0x20FB: 0x80,
                                   0x20FC: 1, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        v = self._buf_vals(rec)
        self.assertEqual(v[0], 0x15)

    def test_range_hi20(self):
        rec = self._run("v2t11", {0x20F8: 0x20, 0x20F9: 0x00, 0x20FA: 0x00, 0x20FB: 0x00,
                                   0x20FC: 1, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        v = self._buf_vals(rec)
        self.assertEqual(v[0], 0x1A)

    def test_bad_mode(self):
        rec = self._run("v2t14", {0x20F8: 0x02, 0x20F9: 0, 0x20FA: 0, 0x20FB: 0x80,
                                   0x20FC: 4, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        v = self._buf_vals(rec)
        self.assertEqual(v[0], 0x18)

    def test_cold_warm_identical(self):
        rec = self._run("v2t01w", {0x20F8: 0x02, 0x20F9: 0, 0x20FA: 0, 0x20FB: 0x80,
                                    0x20FC: 1, 0x20FD: 0, 0x20FE: 0, 0x20FF: 0})
        colds = [c for c in rec["APICALL"] if c["cycle"] == "cold" and c["slot"] == 2]
        warms = [c for c in rec["APICALL"] if c["cycle"] == "warm" and c["slot"] == 2]
        self.assertEqual(len(colds), 1)
        self.assertEqual(len(warms), 1)
        for k in ("slot", "sp_before", "sp_after", "caller_a"):
            self.assertEqual(colds[0][k], warms[0][k])


    def test_sequential_independence(self):
        # FIRST LBA 0x109 then SECOND LBA 0x200: recbase0 stays 0x109, recbase1=0x200
        import geargrafx_api_entry as Eentry
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        asm = (".setcpu \"huc6280\"\n.segment \"CODE\"\n"
               "payload_entry:\n"
               " lda #$00\n ldx #$07\nlz: sta $2400,x\n dex\n bpl lz\n"
               " lda #$FF\n sta $2404\n lda #$00\n sta $2408\n sta $2409\n sta $240A\n"
               " lda #$00\n sta $20F8\n lda #$01\n sta $20F9\n lda #$09\n sta $20FA\n"
               " lda #$00\n sta $20FB\n lda #$01\n sta $20FC\n lda #$00\n sta $20FD\n sta $20FE\n sta $20FF\n"
               " jsr $E006\n"
               " lda #$00\n sta $20F8\n lda #$02\n sta $20F9\n lda #$00\n sta $20FA\n"
               " lda #$00\n sta $20FB\n lda #$02\n sta $20FC\n"
               " jsr $E006\n"
               " lda $2400\n sta $2F80\n lda $2401\n sta $2F81\n lda $2402\n sta $2F82\n"
               " lda $2408\n sta $2F83\n lda $2409\n sta $2F84\n lda $240A\n sta $2F85\n"
               "payload_halt:\n bra payload_halt\n")
        import subprocess
        o = self.out / "v2seq"
        o.mkdir(exist_ok=True)
        src = o / "v2seq.s"
        src.write_text(asm, encoding="ascii")
        obj, binp, cfgp, lblp = o / "v2seq.o", o / "v2seq.bin", o / "v2seq.cfg", o / "v2seq.lbl"
        cfgp.write_text(CFG, encoding="ascii")
        ex = __import__("environment").executable
        subprocess.run([ex("ca65"), str(src), "-o", str(obj), "-g"], check=True)
        subprocess.run([ex("ld65"), "-C", str(cfgp), "-o", str(binp), "-Ln", str(lblp), str(obj)], check=True)
        code = binp.read_bytes()
        body = self.L.body_for(code, 1)
        info = self.L.info_sector(2, 1, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0, sum(body) & 0xFFFF)
        cue = self.L.make_disc(self.out / "v2seq", self.L.case_disc({"n": 1, "iplblk": 2}, body, info))
        t = (o / "v2seq.lbl").read_text()
        m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", t, re.M)
        halt = int(m.group(1), 16)
        lab = o / "v2seq.labels"
        lab.write_text(
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {self.labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels['api_unimplemented']:04x}\n", encoding="ascii")
        rec = Eentry.run_probe(self.probe, self.rom, cue, lab, 2_000_000, "synthetic")
        run = [r for r in rec["RUN"] if r["cycle"] == "cold"][0]
        buf = bytes.fromhex(run["buffer_hex"])
        base = 0x780
        vals = list(buf[base:base + 6])
        self.assertEqual((vals[0], vals[1], vals[2]), (0x00, 0x01, 0x09))
        self.assertEqual((vals[3], vals[4], vals[5]), (0x00, 0x02, 0x00))

if __name__ == "__main__":
    unittest.main()
