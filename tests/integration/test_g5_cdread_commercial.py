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
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        # A-4/C-6: self-contained DEV ROM built from current sources into
        # the private temp dir; never depend on a repo build/ tree.
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-cdread-comm-")
        cls.out = Path(cls._tmp.name)
        dev_out = Path(cls._tmp.name) / "dev"
        with patch("sys.argv", ["build_rom.py", "--mode", "debug",
                                  "--output", str(dev_out)]):
            assert build_rom.main() == 0
        orig = dev_out / "dev-only-not-compatible-syscard3.pce"
        assert orig.is_file()
        # C-6: isolated private fixture; rebuild both ROMs from current sources
        # every fixture (never reuse a stale ROM). Only this directory is
        # removed in tearDownClass.
        cls.sel = SEL
        cls.track_sel = TRACK_SEL
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "vct", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False, cd_read="commercial")
        cls.rom_ctrl, cls.syms_ctrl = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "vct-ctrl", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False, cd_read="link")
        # ROM identity: distinct builds; each $E009 table entry must point at
        # its own link body (verified again per-test where it matters).
        assert cls.rom.read_bytes() != cls.rom_ctrl.read_bytes()
        for _rom, _syms, _entry in (
                (cls.rom, cls.syms, "cd_read_commercial_entry"),
                (cls.rom_ctrl, cls.syms_ctrl, "cd_read_link_entry")):
            data = _rom.read_bytes()
            tab = data[0xE009 - 0xE000:0xE009 - 0xE000 + 3]
            assert tuple(tab) == (0x4C, _syms[_entry] & 0xFF,
                                  (_syms[_entry] >> 8) & 0xFF), (_rom.name, _entry)
        trace = cls.out / "ipl-loader-g5-vct.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        trace_ctrl = cls.out / "ipl-loader-g5-vct-ctrl.lbl"
        cls.labels_ctrl = parse_labels(
            trace_ctrl, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp", "vct-probe")
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
        assert "pce-cdread-comm-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    def _run(self, name, setup_lines, via_slot03=False, slot03_block=0,
             poke_base=None, skip_base_call=False, rom_kind="exp",
             via_table=False, tamper_mpr3=False, mock_magic=0xA5,
             mock_valid=0x01, base_cl=1, xsel_lba=None, xsel_magic=None):
        """Assemble payload CODE ($3000) + commercial BOOT ($3800); run cold+warm.

        rom_kind selects the Experimental (commercial $03) or Control (link V1
        $03) ROM; via_table=True issues the call through the $E009 table entry
        instead of a direct JSR, proving table routing on real hardware.
        The payload defines its own trusted XSEL selection record
        (experimental_selection; default INDEX01 = sel, magic "XSEL"/1/1)
        so the synthetic fixture exercises the identical boundary path as the
        ROM link (glue XSEL) with no test-only fallback. xsel_lba overrides
        the XSEL INDEX01; xsel_magic overrides the 4 magic bytes (None = valid).
        skip_base_call installs an explicit initialized mock base state
        (ST_LAST=0, ST_MAGIC, ST_VALID) instead of running $E006."""
        import geargrafx_api_entry as Eentry
        if rom_kind == "exp":
            rom, labels = self.rom, self.labels
        elif rom_kind == "ctrl":
            rom, labels = self.rom_ctrl, self.labels_ctrl
        else:
            raise ValueError("rom_kind must be 'exp' or 'ctrl'")
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
                         f" lda #${mock_magic:02X}\n sta $240B\n"
                         f" lda #${mock_valid:02X}\n sta $240C\n"
                         " sta $2403\n tsx\n stx $F1\n")
            base_pull = ""
        else:
            base_call = (
                " lda #$00\n ldx #$07\nbz: sta $2400,x\n dex\n bpl bz\n"
                " lda #$FF\n sta $2404\n"
                f" lda #${self.track_sel:02X}\n sta $20F8\n"
                " lda #$00\n sta $20F9\n sta $20FA\n sta $20FD\n sta $20FE\n sta $20FF\n"
                " lda #$80\n sta $20FB\n"
                f" lda #${base_cl:02X}\n sta $20FC\n"
                " jsr $E006\n tsx\n stx $F1\n pha\n php\n"
                " ; base A/P stashed on stack; stored after the transfer\n"
                " ; ($2F80+ lies inside the [$2800,$3000) transfer window)\n")
            base_pull = (" pla\n sta res_base+59\n pla\n sta res_base+58\n"
                         " lda $2400\n sta res_base+60\n lda $2401\n sta res_base+61\n"
                         " lda $2402\n sta res_base+62\n lda $2404\n sta res_base+63\n")
        if via_slot03:
            blo = slot03_block & 0xFF
            bmi = (slot03_block >> 8) & 0xFF
            bhi = (slot03_block >> 16) & 0xFF
            call = (f" lda #${blo:02X}\n sta $20F2\n lda #${bmi:02X}\n sta $20F3\n"
                    f" lda #${bhi:02X}\n sta $20F4\n"
                    f" lda #${wlo:02X}\n sta $20F5\n lda #${wmid:02X}\n sta $20F6\n"
                    f" lda #${whi:02X}\n sta $20F7\n jsr $E009\n")
        elif via_table:
            call = "\n".join(setup_lines) + "\n jsr $E009\n"
            setup_lines = []
        else:
            call = " jsr cd_read_commercial_entry\n"
        asm = (
            ".setcpu \"huc6280\"\n.import cd_read_commercial_entry\n.export experimental_selection\n.segment \"CODE\"\n"
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
            " sta res_base+0\n php\n pla\n sta res_base+1\n stx res_base+94\n sty res_base+95\n"
            + (" lda #$42\n tam #$08\n" if tamper_mpr3 else "") +
            " tma #$01\n sta res_base+80\n tma #$02\n sta res_base+81\n tma #$04\n sta res_base+82\n"
            " tma #$08\n sta res_base+83\n tma #$10\n sta res_base+84\n tma #$20\n sta res_base+85\n"
            " tma #$40\n sta res_base+86\n tma #$80\n sta res_base+87\n"
            " tma #$01\n sta res_base+12\n tma #$02\n sta res_base+13\n tma #$04\n sta res_base+14\n"
            " tma #$08\n sta res_base+15\n tma #$10\n sta res_base+16\n tma #$20\n sta res_base+17\n"
            " tma #$40\n sta res_base+18\n tma #$80\n sta res_base+19\n"
            # pull before-snapshot in exact reverse push order: M7..M0
            " pla\n sta res_base+19\n pla\n sta res_base+18\n pla\n sta res_base+17\n pla\n sta res_base+16\n"
            " pla\n sta res_base+15\n pla\n sta res_base+14\n pla\n sta res_base+13\n pla\n sta res_base+12\n"
            + base_pull +
            # post-call legacy-input snapshot (proves save/restore net-zero).
            " lda $20F2\n sta res_base+88\n lda $20F3\n sta res_base+89\n lda $20F4\n sta res_base+90\n"
            # SP baseline (pre-stash) vs final SP (all pulls done): must match
            " lda $F1\n sta res_base+2\n tsx\n stx res_base+3\n"
            # ST_MAGIC/ST_VALID post-call snapshot (P2 state-preservation)
            " lda $240B\n sta res_base+96\n lda $240C\n sta res_base+97\n"
            " ldx #$09\nrc: lda $2330,x\n sta res_base+20,x\n lda $2320,x\n sta res_base+30,x\n dex\n bpl rc\n"
            " ldx #$0F\nrb: lda $2800,x\n sta res_base+40,x\n dex\n bpl rb\n"
            " ldx #$07\nrs: lda $2400,x\n sta res_base+64,x\n"
            " lda $20F8,x\n sta res_base+72,x\n dex\n bpl rs\n"
            "payload_halt:\n bra payload_halt\n"
            "experimental_selection:\n"
            f" .byte {xsel_magic if xsel_magic is not None else '\"XSEL\"'}\n"
            f" .byte $01, $01, $01, ${self.track_sel:02X}\n"
            f" .byte ${((xsel_lba if xsel_lba is not None else self.sel) >> 16) & 0xFF:02X},"
            f" ${((xsel_lba if xsel_lba is not None else self.sel) >> 8) & 0xFF:02X},"
            f" ${(xsel_lba if xsel_lba is not None else self.sel) & 0xFF:02X}\n"
            "res_base:\n .res 100\n")
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
        assert syms.get("res_base", 0) < 0x3800, syms
        self._res_base = syms["res_base"] - 0x2800
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
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {labels['api_unimplemented']:04x}\n",
            encoding="ascii")
        self._last_cue, self._last_lab = cue, lab
        rec = Eentry.run_probe(self.probe, rom, cue, lab, BUDGET, "synthetic")
        return rec

    def _buf(self, rec):
        runs = rec["RUN"]
        self.assertEqual(len(runs), 2)
        by_cycle = {r["cycle"]: bytes.fromhex(r["buffer_hex"]) for r in runs}
        self.assertEqual(sorted(by_cycle), ["cold", "warm"])
        return by_cycle["cold"], by_cycle["warm"]

    def _res(self, buf):
        base = self._res_base  # res_base block offset in dump (was 0x780)
        return {
            "a": buf[base], "p": buf[base + 1],
            "sp0": buf[base + 2], "sp1": buf[base + 3],
            "mpr_pre": list(buf[base + 12:base + 20]),
            "mpr_post": list(buf[base + 0x50:base + 0x58]),  # $2FD0-$2FD7 (post-call snapshot)
            "sentinel": list(buf[base + 0x58:base + 0x5B]),  # $2FD8-$2FDA post-call sentinels
            "cres": list(buf[base + 20:base + 30]),
            "lres": list(buf[base + 30:base + 40]),
            "buf16": list(buf[base + 40:base + 56]),
            "base_a": buf[base + 58], "base_p": buf[base + 59],
            "rb0": tuple(buf[base + 60:base + 63]), "stlast": buf[base + 63],
            "x_after": buf[base + 94], "y_after": buf[base + 95],
            "magic": buf[base + 96], "valid": buf[base + 97],
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
        # MPR preserved across the call: pre-call snapshot vs post-call
        # snapshot in disjoint areas (no shared-window aliasing possible).
        self.assertEqual(r["mpr_pre"], [255, 248, 128, 129, 130, 131, 132, 0])
        self.assertEqual(r["mpr_post"], [255, 248, 128, 129, 130, 131, 132, 0])
        self.assertEqual(r["mpr_pre"], r["mpr_post"])
        self.assertEqual((r["x_after"], r["y_after"]), (0x12, 0x34))  # X/Y
        exp = self._expected_frame("c01", 0x109)
        got = list(cold[0x000:0x800])[:2048]
        # Full 2048B memcmp: the transfer window [$2800,$3000) is pristine
        # (all result recording moved to the linker res_base block).
        self.assertEqual(list(got[0:2048]), list(exp[0:2048]))

    def test_c02_valid_block1_distinct(self):
        rec = self._run("c02", self._comm_regs(1))
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 10))
        exp = self._expected_frame("c02", 0x10A)
        got2 = list(cold[0:2048])
        self.assertEqual(list(got2[0:2048]), list(exp[0:2048]))
        exp0 = self._expected_frame("c02", 0x109)
        self.assertNotEqual(exp, exp0)  # distinct sectors

    def test_c03_win16_accepted_vs_control_after(self):
        rec = self._run("c03", self._comm_regs(16))
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)  # experimental accepts win+16
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 0x19))
        self.assertEqual(r["mpr_pre"], r["mpr_post"])
        exp = self._expected_frame("c03", 0x119)
        got3 = list(cold[0:2048])
        self.assertEqual(list(got3[0:2048]), list(exp[0:2048]))

    def test_c03ctrl_control_reports_after(self):
        # Control ROM (link V1 $03, $20F2 block): win+16 is AFTER ($64),
        # no transfer, canary intact. Experimental ROM must NOT behave so.
        rec2 = self._run("c03ctrl", [], via_slot03=True, slot03_block=16,
                         poke_base=0x109, skip_base_call=True,
                         rom_kind="ctrl")
        cold2, warm2 = self._buf(rec2)
        self.assertEqual(cold2, warm2)
        r2 = self._res(cold2)
        self.assertEqual(r2["a"], 0x64)
        self.assertEqual(tuple(r2["cres"][7:10]), (0, 0, 0))
        self.assertEqual(list(cold2[0:16]), [0xAA] * 16)
        self.assertEqual(list(cold2[0x7FF:0x800]), [0xAA])  # canary intact

    def test_c04_before_pregap_guard(self):
        rec = self._run("c04", self._comm_regs(5), poke_base=0, skip_base_call=True)
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x63)
        self.assertEqual((r["cres"][2], r["cres"][3]), (0, 0))
        self.assertEqual(list(r["cres"]), [0] * 10)  # no CRES writes on refusal
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
        self.assertEqual(list(r["cres"]), [0] * 10)  # no CRES writes on refusal
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)

    def test_c06_mode_refuse(self):
        for name, mode in (("c06a", 1), ("c06b", 0xFE)):
            rec = self._run(name, self._comm_regs(0, mode=mode))
            cold, _ = self._buf(rec)
            r = self._res(cold)
            self.assertEqual(r["a"], 0x65, name)
            self.assertEqual(list(r["cres"]), [0] * 10, name)  # no CRES writes on refusal
            self.assertEqual(list(cold[0:16]), [0xAA] * 16, name)

    def test_c07_size_refuse(self):
        rec = self._run("c07", self._comm_regs(0, size_lo=0, size_hi=4))
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x66)
        self.assertEqual(list(r["cres"]), [0] * 10)  # no CRES writes on refusal
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)

    def test_c08_dest_refuse(self):
        for name, dlo, dhi in (("c08a", 0, 0x20), ("c08b", 0, 0x30),
                               ("c08c", 1, 0x28)):
            rec = self._run(name, self._comm_regs(0, dst_lo=dlo, dst_hi=dhi))
            cold, _ = self._buf(rec)
            r = self._res(cold)
            self.assertEqual(r["a"], 0x67, name)
            self.assertEqual(list(r["cres"]), [0] * 10, name)  # no CRES writes on refusal
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
        self.assertEqual(list(got9[0:2048]), list(exp[0:2048]))

    def test_c10_cold_warm_and_2base_link(self):
        rec = self._run("c10", self._comm_regs(2))
        cold, warm = self._buf(rec)
        self.assertEqual(self._res(cold)["a"], self._res(warm)["a"])
        self.assertEqual(self._res(cold)["cres"], self._res(warm)["cres"])
        self.assertEqual(self._res(cold)["mpr_pre"], self._res(warm)["mpr_pre"])
        self.assertEqual(self._res(cold)["mpr_post"], self._res(warm)["mpr_post"])
        self.assertEqual(self._res(cold)["mpr_pre"], self._res(cold)["mpr_post"])
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
        self.assertEqual(list(r["cres"]), [0] * 10)  # no CRES writes on refusal
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

    def test_c13_rom_identity_separation(self):
        # Control vs Experimental are distinct ROMs; each $E009 table entry
        # points at its own link body (verified from each ROM's own bytes).
        import hashlib
        exp = self.rom.read_bytes()
        ctrl = self.rom_ctrl.read_bytes()
        self.assertNotEqual(hashlib.sha256(exp).digest(),
                            hashlib.sha256(ctrl).digest())
        for rom, syms, entry in (
                (exp, self.syms, "cd_read_commercial_entry"),
                (ctrl, self.syms_ctrl, "cd_read_link_entry")):
            tab = rom[0xE009 - 0xE000:0xE009 - 0xE000 + 3]
            self.assertEqual(tuple(tab),
                             (0x4C, syms[entry] & 0xFF,
                              (syms[entry] >> 8) & 0xFF))

    def test_c14_slot03_table_routes_to_commercial(self):
        # JSR $E009 on the experimental ROM reaches the commercial entry:
        # transfer succeeds, returns $00, MPRs preserved, canary intact.
        rec = self._run("c14", self._comm_regs(0), via_table=True)
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 9))
        self.assertEqual(r["mpr_pre"], r["mpr_post"])
        exp = self._expected_frame("c14", 0x109)
        got = list(cold[0:2048])
        self.assertEqual(list(got[0:2048]), list(exp[0:2048]))

    def test_c15_mpr_tamper_detected(self):
        # Mutant: tamper MPR3 after the call but before the post-call snapshot
        # must show up as pre != post (proves the comparison is real, not
        # vacuous). Normal runs assert pre == post (see c01/c10).
        rec = self._run("c15", self._comm_regs(0), tamper_mpr3=True)
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)  # call itself still succeeds
        self.assertNotEqual(r["mpr_pre"], r["mpr_post"])
        self.assertEqual(r["mpr_pre"],
                         [255, 248, 128, 129, 130, 131, 132, 0])

    def test_c16_refused_call_preserves_everything(self):
        # Refused call ($65) writes nothing observable: inputs intact
        # (incl. legacy $20F2-$20F4 sentinels), CRES zeros, canary intact.
        lines = self._comm_regs(0, mode=1) + [
            " lda #$11\n sta $20F2", " lda #$22\n sta $20F3",
            " lda #$33\n sta $20F4",
        ]
        rec = self._run("c16", lines)
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x65)
        self.assertEqual(list(r["cres"]), [0] * 10)
        self.assertEqual(r["mpr_pre"], r["mpr_post"])
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)
        self.assertEqual(r["sentinel"], [0x11, 0x22, 0x33])
        self.assertEqual(r["in8"], [0, 8, 0, 0x28, 0, 0, 0, 1])

    # --- P1: trusted XSEL INDEX01 (untrusted $20F5-$20F7 ignored) ---
    def test_c17_win_mutation_ignored(self):
        # $20F5-$20F7 garbage (even HI>=$20, which the old code refused as
        # $61) must not change the boundary: XSEL INDEX01 rules. RED on old
        # code ($61), GREEN on trusted-XSEL code ($00 + correct LBA).
        lines = self._comm_regs(0) + [
            " lda #$FF\n sta $20F5", " lda #$FF\n sta $20F6",
            " lda #$FF\n sta $20F7",
        ]
        rec = self._run("c17", lines)
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 9))
        self.assertEqual(r["mpr_pre"], r["mpr_post"])

    def test_c18_bad_xsel_magic_refused(self):
        # Corrupted XSEL magic: no trusted bound exists -> $61, no CRES,
        # canary intact, no transfer. NOT a BEFORE ($63): the bound itself
        # is unusable.
        rec = self._run("c18", self._comm_regs(0),
                        xsel_magic='"$00,$00,$00,$00"')
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x61)
        self.assertEqual(list(r["cres"]), [0] * 10)
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)
        self.assertEqual(cold[0x7FF], 0xAA)
        self.assertEqual(r["mpr_pre"], r["mpr_post"])

    def test_c19_custom_xsel_boundary(self):
        # XSEL INDEX01 = 0x10A drives the bound (not hardcoded 0x109):
        # LBA 0x109 < 0x10A -> $63; LBA 0x10A -> success (frame exists).
        rec = self._run("c19a", self._comm_regs(0), xsel_lba=0x10A)
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x63)
        self.assertEqual(list(r["cres"]), [0] * 10)
        rec = self._run("c19b", self._comm_regs(1), xsel_lba=0x10A)
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 10))

    # --- P2: CD_BASE initialization gate (ST_LAST + MAGIC + VALID bit0) ---
    def test_c20_uninit_state_last_zero(self):
        # Cleared RAM, ST_LAST=0, but no seed (magic/valid zero) -> $60.
        rec = self._run("c20", self._comm_regs(0), poke_base=0x109,
                        skip_base_call=True, mock_magic=0x00, mock_valid=0x00)
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x60)
        self.assertEqual(list(r["cres"]), [0] * 10)
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)
        self.assertEqual(r["mpr_pre"], r["mpr_post"])
        self.assertEqual((r["magic"], r["valid"]), (0x00, 0x00))

    def test_c21_magic_mismatch(self):
        rec = self._run("c21", self._comm_regs(0), poke_base=0x109,
                        skip_base_call=True, mock_magic=0x5A, mock_valid=0x01)
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0x60)
        self.assertEqual(list(r["cres"]), [0] * 10)
        self.assertEqual(list(cold[0:16]), [0xAA] * 16)
        self.assertEqual((r["magic"], r["valid"]), (0x5A, 0x01))

    def test_c22_valid_bit_missing(self):
        # VALID=0 and VALID=$02 (bit1 only, no recbase0 bit) both refuse;
        # VALID=$03 (both bits) must still succeed (no over-constraint).
        for nm, valid, want in (("c22a", 0x00, 0x60), ("c22b", 0x02, 0x60)):
            rec = self._run(nm, self._comm_regs(0), poke_base=0x109,
                            skip_base_call=True, mock_valid=valid)
            cold, _ = self._buf(rec)
            r = self._res(cold)
            self.assertEqual(r["a"], want, nm)
            self.assertEqual(list(r["cres"]), [0] * 10, nm)
            self.assertEqual((r["magic"], r["valid"]), (0xA5, valid), nm)
        rec = self._run("c22c", self._comm_regs(0), poke_base=0x109,
                        skip_base_call=True, mock_valid=0x03)
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 9))

    def test_c23_second_update_keeps_recbase0(self):
        # $E006 with _cl=2 (SECOND): recbase0 stays seeded INDEX01 and valid;
        # a following $03 block 0 reads recbase0 -> success, LBA 0x109.
        rec = self._run("c23", self._comm_regs(0), base_cl=2)
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)
        self.assertEqual(r["rb0"], (0, 1, 9))
        self.assertEqual(tuple(r["cres"][7:10]), (0, 1, 9))
        self.assertEqual(r["stlast"], 0)

    def test_c24_full_memcmp_detects_corruption(self):
        # P2: the full 2048B transfer window is compared (no exclusions).
        # Corrupting head/middle/old-excluded-range/tail must each be detected
        # (proves the old $2F80-$2FDF exclusion is gone, not just moved).
        rec = self._run("c24", self._comm_regs(0))
        cold, _ = self._buf(rec)
        r = self._res(cold)
        self.assertEqual(r["a"], 0)
        exp = self._expected_frame("c24", 0x109)
        got = list(cold[0:2048])
        self.assertEqual(got, exp)
        for pos, tag in ((0, "head"), (1000, "middle"),
                         (1950, "old-excluded"), (2047, "tail")):
            bad = list(exp)
            bad[pos] ^= 0xFF
            self.assertNotEqual(got, bad, tag)

    def test_c25_dh_classifier_local_bytes(self):
        # P2: probe classifies slot-3 _dh=0 as LOCAL_BYTES (was ZERO,
        # contradicting the implemented FIRST/LOCAL-bytes mode) and
        # unverified _dh=1 as OTHER (was LOCAL, implying a confirmed mode
        # the routine actually refuses $65).
        rec = self._run("c25a", self._comm_regs(0), via_table=True)
        calls = [c for c in rec["APICALL"]
                 if c["cycle"] == "cold" and c["slot"] == 3]
        self.assertTrue(calls)
        self.assertEqual(calls[0]["addr_mode"], "LOCAL_BYTES")
        rec = self._run("c25b", self._comm_regs(0, mode=1), via_table=True)
        calls = [c for c in rec["APICALL"]
                 if c["cycle"] == "cold" and c["slot"] == 3]
        self.assertTrue(calls)
        self.assertEqual(calls[0]["addr_mode"], "OTHER")
