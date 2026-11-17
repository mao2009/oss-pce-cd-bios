"""Synthetic real-core tests for CD_READ commercial first-mode (G5, pinned core).

Control (CD_READ_LINK_V1 at $E009, $20F2 block, 16-sector window) vs experimental
(cd_read_commercial_entry in-payload at $3800, $20FC-$20FE block, no window end,
_dh=0/size=2048/dest=$2800 guards) on the SAME 2-base ROM/disc/sel. NOT genuine
ABI evidence. NOT compatible. G5_OBSERVED=false. Commercial media: BLOCKED here
(no ./rom/ in this worktree; Phase B reported SKIP/BLOCKED, never PASS).
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
BUDGET = 10_000_000


def _win_le(sel):
    return sel & 0xFF, (sel >> 8) & 0xFF, (sel >> 16) & 0xFF


def _blk_be(block):
    return (block >> 16) & 0xFF, (block >> 8) & 0xFF, block & 0xFF


class CdReadCommercialSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        orig = ROOT / "build" / "debug" / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
        cls.out = Path("/tmp/pce-cdread-comm-synth")
        cls.out.mkdir(parents=True, exist_ok=True)
        cls.sel = SEL
        cls.track_sel = TRACK_SEL
        rom_name = "dev-ipl-loader-g5-not-compatible-vct.pce"
        cls.rom = cls.out / rom_name
        if not cls.rom.is_file():
            rom, _ = build_ipl_loader_g5_rom(
                orig, cls.out, cls.sel, "debug", "vct", ["-D", "IPL_PUBLIC"],
                track=cls.track_sel, private_input=False)
            cls.rom = rom
        trace = cls.out / "ipl-loader-g5-vct.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "vct-probe")
        import geargrafx_ipl_loader as L
        cls.L = L
        from build_cdload_exec import USER_OFFSET
        from make_cd_image import RAW_BYTES
        cls.USER_OFFSET = USER_OFFSET
        cls.RAW_BYTES = RAW_BYTES

    def _run(self, name, setup_lines, via_slot03=False, slot03_block=0,
             poke_base=None, skip_base_call=False):
        """Assemble payload CODE ($3000) + commercial BOOT ($3800); run cold+warm."""
        import geargrafx_api_entry as Eentry
        comm_src = ROOT / "src" / "cd_read_commercial.s"
        comm_text = comm_src.read_text(encoding="ascii")
        # The commercial body is segment-agnostic (absolute work-RAM/I-O only);
        # link its BOOT segment into payload RAM at $3800 for this test.
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n"
               "    BOOT: load = RAM, type = ro, start = $3800;\n}\n")
        wlo, wmid, whi = _win_le(self.sel)
        if skip_base_call:
            base_call = ""
            assert poke_base is not None
            phi, pmi, plo = _blk_be(poke_base)
            base_call = (f" lda #${plo:02X}\n sta $2402\n lda #${pmi:02X}\n sta $2401\n"
                         f" lda #${phi:02X}\n sta $2400\n lda #$00\n sta $2404\n"
                         " sta $2403\n tsx\n stx $F1\n")
            base_pull = ""
        else:
            base_call = (
                " lda #$00\n ldx #$07\nbz: sta $2400,x\n dex\n bpl bz\n"
                " lda #$FF\n sta $2404\n"
                f" lda #${self.track_sel:02X}\n sta $20F8\n"
                " lda #$00\n sta $20F9\n sta $20FA\n sta $20FD\n sta $20FE\n sta $20FF\n"
                " lda #$80\n sta $20FB\n lda #$01\n sta $20FC\n"
                " jsr $E006\n tsx\n stx $F1\n pha\n php\n"
                " ; base A/P stashed on stack; stored after the transfer\n"
                " ; ($2F80+ lies inside the [$2800,$3000) transfer window)\n")
            base_pull = (" pla\n sta $2FBB\n pla\n sta $2FBA\n"
                         " lda $2400\n sta $2FBC\n lda $2401\n sta $2FBD\n"
                         " lda $2402\n sta $2FBE\n lda $2404\n sta $2FBF\n")
        if via_slot03:
            blo = slot03_block & 0xFF
            bmi = (slot03_block >> 8) & 0xFF
            bhi = (slot03_block >> 16) & 0xFF
            call = (f" lda #${blo:02X}\n sta $20F2\n lda #${bmi:02X}\n sta $20F3\n"
                    f" lda #${bhi:02X}\n sta $20F4\n"
                    f" lda #${wlo:02X}\n sta $20F5\n lda #${wmid:02X}\n sta $20F6\n"
                    f" lda #${whi:02X}\n sta $20F7\n jsr $E009\n")
        else:
            call = " jsr cd_read_commercial_entry\n"
        asm = (
            ".setcpu \"huc6280\"\n.import cd_read_commercial_entry\n.segment \"CODE\"\n"
            "payload_entry:\n"
            # canary $2800-$2FFF = $AA
            " lda #$AA\n ldx #$00\ncan: sta $2800,x\n sta $2900,x\n sta $2A00,x\n sta $2B00,x\n"
            " sta $2C00,x\n sta $2D00,x\n sta $2E00,x\n sta $2F00,x\n"
            " dex\n bne can\n"
            # clear CRES + LINK_RES areas
            " lda #$00\n ldx #$09\nzc: sta $2330,x\n sta $2320,x\n dex\n bpl zc\n"
            + base_call +
            "\n".join(setup_lines) +
            "\n"
            # SP baseline already in ZP $F1 (captured before any stash push;
            # commercial never touches $F1; the window cannot hold a snapshot).
            " tma #$01\n pha\n tma #$02\n pha\n tma #$04\n pha\n tma #$08\n pha\n"
            " tma #$10\n pha\n tma #$20\n pha\n tma #$40\n pha\n tma #$80\n pha\n"
            " ldx #$12\n ldy #$34\n"  # X/Y canaries (routine must preserve)
            + call +
            " sta $2F80\n php\n pla\n sta $2F81\n stx $2FDE\n sty $2FDF\n"
            " tma #$01\n sta $2F8C\n tma #$02\n sta $2F8D\n tma #$04\n sta $2F8E\n"
            " tma #$08\n sta $2F8F\n tma #$10\n sta $2F90\n tma #$20\n sta $2F91\n"
            " tma #$40\n sta $2F92\n tma #$80\n sta $2F93\n"
            # pull before-snapshot in exact reverse push order: M7..M0
            " pla\n sta $2F93\n pla\n sta $2F92\n pla\n sta $2F91\n pla\n sta $2F90\n"
            " pla\n sta $2F8F\n pla\n sta $2F8E\n pla\n sta $2F8D\n pla\n sta $2F8C\n"
            + base_pull +
            # SP baseline (pre-stash) vs final SP (all pulls done): must match
            " lda $F1\n sta $2F82\n tsx\n stx $2F83\n"
            " ldx #$09\nrc: lda $2330,x\n sta $2F94,x\n lda $2320,x\n sta $2F9E,x\n dex\n bpl rc\n"
            " ldx #$0F\nrb: lda $2800,x\n sta $2FA8,x\n dex\n bpl rb\n"
            " ldx #$07\nrs: lda $2400,x\n sta $2FC0,x\n"
            " lda $20F8,x\n sta $2FC8,x\n dex\n bpl rs\n"
            "payload_halt:\n bra payload_halt\n")
        o = self.out / name
        o.mkdir(exist_ok=True)
        src = o / (name + ".s")
        src.write_text(asm, encoding="ascii")
        ccom = o / "ccom.s"
        ccom.write_text(comm_text, encoding="ascii")
        obj, cobj, binp, cfgp, lblp = (o / (name + ".o"), o / "ccom.o",
                                       o / (name + ".bin"), o / (name + ".cfg"),
                                       o / (name + ".lbl"))
        cfgp.write_text(CFG, encoding="ascii")
        import environment
        ca65, ld65 = environment.executable("ca65"), environment.executable("ld65")
        subprocess.run([ca65, str(src), "-o", str(obj), "-g"], check=True)
        subprocess.run([ca65, str(ccom), "-o", str(cobj), "-g"], check=True)
        subprocess.run([ld65, "-C", str(cfgp), "-o", str(binp), "-Ln", str(lblp),
                        str(obj), str(cobj)], check=True)
        import re as _re
        syms = {}
        for line in lblp.read_text(encoding="ascii").splitlines():
            m = _re.match(r"^al ([0-9A-Fa-f]{6}) \.(.+)$", line)
            if m:
                syms[m.group(2)] = int(m.group(1), 16)
        assert syms.get("cd_read_commercial_entry") == 0x3800, syms
        assert syms.get("payload_halt", 0) < 0x3800, syms
        code = binp.read_bytes()
        assert len(code) <= 2 * 2048, len(code)
        body = self.L.body_for(code, 2)
        info = self.L.info_sector(2, 2, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0,
                                 sum(body) & 0xFFFF)
        cue = self.L.make_disc(self.out / name,
                               self.L.case_disc({"n": 2, "iplblk": 2}, body, info))
        t = (_re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", lblp.read_text(),
                        _re.M))
        halt = int(t.group(1), 16)
        lab = o / (name + ".labels")
        lab.write_text(
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {self.labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels['api_unimplemented']:04x}\n",
            encoding="ascii")
        rec = Eentry.run_probe(self.probe, self.rom, cue, lab, BUDGET, "synthetic")
        return rec

    def _buf(self, rec):
        runs = rec["RUN"]
        self.assertEqual(len(runs), 2)
        by_cycle = {r["cycle"]: bytes.fromhex(r["buffer_hex"]) for r in runs}
        self.assertEqual(sorted(by_cycle), ["cold", "warm"])
        return by_cycle["cold"], by_cycle["warm"]

    def _res(self, buf):
        base = 0x780  # $2F80 - $2800 (dump starts at $2800)
        return {
            "a": buf[base], "p": buf[base + 1],
            "sp0": buf[base + 2], "sp1": buf[base + 3],
            "mpr0": list(buf[base + 4:base + 12]),
            "mpr1": list(buf[base + 12:base + 20]),
            "cres": list(buf[base + 20:base + 30]),
            "lres": list(buf[base + 30:base + 40]),
            "buf16": list(buf[base + 40:base + 56]),
            "base_a": buf[base + 58], "base_p": buf[base + 59],
            "rb0": tuple(buf[base + 60:base + 63]), "stlast": buf[base + 63],
            "x_after": buf[base + 94], "y_after": buf[base + 95],
            "state8": list(buf[base + 64:base + 72]),
            "in8": list(buf[base + 72:base + 80]),
        }

    def _comm_regs(self, block, mode=0, size_lo=0, size_hi=8,
                   dst_lo=0, dst_hi=0x28):
        bhi, bmi, blo = _blk_be(block)
        wlo, wmid, whi = _win_le(self.sel)
        return [
            f" lda #${wlo:02X}\n sta $20F5", f" lda #${wmid:02X}\n sta $20F6",
            f" lda #${whi:02X}\n sta $20F7",
            f" lda #${bhi:02X}\n sta $20FC", f" lda #${bmi:02X}\n sta $20FD",
            f" lda #${blo:02X}\n sta $20FE", f" lda #${mode:02X}\n sta $20FF",
            f" lda #${size_lo:02X}\n sta $20F8",
            f" lda #${size_hi:02X}\n sta $20F9",
            f" lda #${dst_lo:02X}\n sta $20FA",
            f" lda #${dst_hi:02X}\n sta $20FB",
        ]

    def _expected_frame(self, run_dir_name, lba):
        from pathlib import Path as _P
        binp = self.out / run_dir_name / "oss-pce-cd-test.bin"
        raw = binp.read_bytes()
        at = lba * self.RAW_BYTES + self.USER_OFFSET
        return list(raw[at:at + 2048])

    def test_c01_valid_block0_memcmp(self):
        rec = self._run("c01", self._comm_regs(0))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)  # cold==warm (observable 14)
        r = self._res(cold)
        self.assertEqual(r["base_a"], 0)  # 2-base link ok (observable: 2-base)
        self.assertEqual(r["rb0"], (0, 1, 9))  # recbase0 == INDEX01
        self.assertEqual(r["a"], 0)  # commercial success
        self.assertEqual(r["cres"][0], 0)  # CRES code
        self.assertEqual(r["cres"][1], 8)  # stage
        self.assertEqual((r["cres"][2], r["cres"][3]), (0, 8))  # 2048 bytes
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 9))  # LBA 0x109
        self.assertEqual(r["lres"], [0] * 10)  # LINK_RES untouched (area separation)
        self.assertEqual(r["sp0"], r["sp1"])  # SP balanced
        self.assertEqual(r["mpr1"], [255, 248, 128, 129, 130, 131, 132, 0])  # MPR preserved (post-call; pre-call area lies inside the transfer window)
        self.assertEqual((r["x_after"], r["y_after"]), (0x12, 0x34))  # X/Y
        exp = self._expected_frame("c01", 0x109)
        got = list(cold[0x000:0x800])[:2048]
        # $2F80..$2FDF (offsets 1920..2015) hold post-transfer result stores;
        # verify transfer data outside that window.
        self.assertEqual(got[0:1920], exp[0:1920])
        self.assertEqual(got[2016:2048], exp[2016:2048])

    def test_c02_valid_block1_distinct(self):
        rec = self._run("c02", self._comm_regs(1))
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 10))
        exp = self._expected_frame("c02", 0x10A)
        got2 = list(cold[0:2048])
        self.assertEqual(got2[0:1920], exp[0:1920])
        self.assertEqual(got2[2016:2048], exp[2016:2048])
        exp0 = self._expected_frame("c02", 0x109)
        self.assertNotEqual(exp, exp0)  # distinct sectors

    def test_c03_win16_accepted_vs_control_after(self):
        rec = self._run("c03", self._comm_regs(16))
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)  # experimental accepts win+16
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 0x19))
        exp = self._expected_frame("c03", 0x119)
        got3 = list(cold[0:2048])
        self.assertEqual(got3[0:1920], exp[0:1920])
        self.assertEqual(got3[2016:2048], exp[2016:2048])
        # control: link V1 at $03 with $20F2=16 reports $64 AFTER, no transfer
        rec2 = self._run("c03ctrl", [], via_slot03=True, slot03_block=16,
                         poke_base=0x109, skip_base_call=True)
        cold2, _ = self._buf(rec2)
        self.assertEqual(cold2[0x780], 0x64)
        self.assertEqual(list(cold2[0:16]), [0xAA] * 16)  # canary intact

    def test_c04_before_pregap_guard(self):
        rec = self._run("c04", self._comm_regs(5), poke_base=0, skip_base_call=True)
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x63)
        self.assertEqual((r["cres"][2], r["cres"][3]), (0, 0))
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)
        self.assertEqual(cold[0x7FF], 0xAA)  # canary tail intact

    def test_c05_21bit_drive_judged_and_cap(self):
        # LBA $1FFFFF (last 21-bit address): valid input here; the drive
        # reports past-end via STATUS (no fixture frame there) -> $70/$71.
        rec = self._run("c05a", self._comm_regs(0), poke_base=0x1FFFFF,
                        skip_base_call=True)
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertIn(r["a"], (0x70, 0x71))
        self.assertTrue(r["cres"][1] in (5, 8))
        # LBA $200000: OVER_CAP pre-drive, no $1800, canary intact.
        rec = self._run("c05b", self._comm_regs(1), poke_base=0x1FFFFF,
                        skip_base_call=True)
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x62)
        self.assertEqual((r["cres"][2], r["cres"][3]), (0, 0))
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)

    def test_c06_mode_refuse(self):
        for name, mode in (("c06a", 1), ("c06b", 0xFE)):
            rec = self._run(name, self._comm_regs(0, mode=mode))
            cold, _ = self._buf(rec)
            r = self._res(cold)
            self.assertEqual(r["a"], 0x65, name)
            self.assertEqual(list(cold[0:16]), [0xAA] * 16, name)

    def test_c07_size_refuse(self):
        rec = self._run("c07", self._comm_regs(0, size_lo=0, size_hi=4))
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x66)
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)

    def test_c08_dest_refuse(self):
        for name, dlo, dhi in (("c08a", 0, 0x20), ("c08b", 0, 0x30),
                               ("c08c", 1, 0x28)):
            rec = self._run(name, self._comm_regs(0, dst_lo=dlo, dst_hi=dhi))
            cold, _ = self._buf(rec)
            r = self._res(cold)
            self.assertEqual(r["a"], 0x67, name)
            self.assertEqual(list(cold[0:16]), [0xAA] * 16, name)

    def test_c09_20f2_independence(self):
        lines = self._comm_regs(0) + [
            " lda #$FF\n sta $20F2", " lda #$FF\n sta $20F3",
            " lda #$FF\n sta $20F4",
        ]
        rec = self._run("c09", lines)
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)  # $20F2 garbage changes nothing
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 9))
        exp = self._expected_frame("c09", 0x109)
        got9 = list(cold[0:2048])
        self.assertEqual(got9[0:1920], exp[0:1920])
        self.assertEqual(got9[2016:2048], exp[2016:2048])

    def test_c10_cold_warm_and_2base_link(self):
        rec = self._run("c10", self._comm_regs(2))
        cold, warm = self._buf(rec)
        self.assertEqual(self._res(cold)["a"], self._res(warm)["a"])
        self.assertEqual(self._res(cold)["cres"], self._res(warm)["cres"])
        self.assertEqual(self._res(cold)["mpr0"], self._res(warm)["mpr0"])
        r = self._res(cold)
        self.assertEqual(r["base_a"], 0)
        self.assertEqual(r["rb0"], (0, 1, 9))
        self.assertEqual(r["stlast"], 0)

    def test_c11_invalid_state(self):
        lines = self._comm_regs(0) + [" lda #$FF\n sta $2404"]
        rec = self._run("c11", lines, poke_base=0x109, skip_base_call=True)
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x60)
        self.assertEqual(tuple(r["cres"][7:10]), (0, 0, 0))
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)

    def test_c12_no_oob_and_payload_intact(self):
        rec = self._run("c12", self._comm_regs(0))
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)
        # base state + input bytes preserved (post-call copies at $2FC0/$2FC8).
        # c12 ran the $E006 TRACK call first: state = recbase0 + ST_LAST=0.
        self.assertEqual(r["state8"], [0, 1, 9, 1, 0, 0, 0, 0])
        self.assertEqual(r["in8"], [0, 8, 0, 0x28, 0, 0, 0, 0])
        # payload CODE at $3000 intact (halt reached, no self-overwrite):
        self.assertNotEqual(list(cold[0x800:0x810]), [0xAA] * 16)
