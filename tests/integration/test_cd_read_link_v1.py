"""Focused real-core tests for the EXPERIMENTAL CD_READ link V1 (Agent-B G5 P0).

Pinned Geargrafx b49ae82a + pinned cc65 71746c8. Synthetic discs only.
Asserts bootloader success (cold+warm) plus link LBA/state/RAM/$2800 for the
experimental contract. Genuine ABI stays UNKNOWN/BLOCKED. release_eligible=False.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MODE = "debug"
STEP_BUDGET = 12_000_000
BUFFER_BASE = 0x2800
RES_OFF = 0x3100 - BUFFER_BASE
EXPECTED_MPR = [0xFF, 0xF8, 1, 2, 31, 3, 4, 0]

PAYLOAD_A = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
MARK = $3F00
RETVEC = $3F10
.ifndef LINKADDR
.error "LINKADDR required"
.endif
.ifndef IDXHI
.error "IDXHI required"
.endif
.ifndef IDXMID
.error "IDXMID required"
.endif
.ifndef IDXLO
.error "IDXLO required"
.endif
payload_entry:
    cld
    lda #IDXHI
    sta $2400
    lda #IDXMID
    sta $2401
    lda #IDXLO
    sta $2402
    lda #$01
    sta $2403
    lda #$00
    sta $2404
    sta $2405
    sta $2406
    sta $2407
    lda #IDXLO
    sta $20F5
    lda #IDXMID
    sta $20F6
    lda #IDXHI
    sta $20F7
    lda #$00
    sta $20F2
    sta $20F3
    sta $20F4
    jsr LINKADDR
    sta RESBASE+1
    php
    pla
    sta RESBASE+4
    ldx #$09
@cp0:
    lda $2320,x
    sta RESBASE+22,x
    dex
    bpl @cp0
    ldx #$1F
@hd0:
    lda $2800,x
    sta RESBASE+52,x
    dex
    bpl @hd0
    lda #$01
    sta $20F2
    jsr LINKADDR
    sta RESBASE+2
    ldx #$09
@cp1:
    lda $2320,x
    sta RESBASE+32,x
    dex
    bpl @cp1
    ldx #$1F
@hd1:
    lda $2800,x
    sta RESBASE+84,x
    dex
    bpl @hd1
    lda #$05
    sta $20F2
    jsr LINKADDR
    sta RESBASE+3
    ldx #$09
@cp5:
    lda $2320,x
    sta RESBASE+42,x
    dex
    bpl @cp5
    ldx #$1F
@hd5:
    lda $2800,x
    sta RESBASE+116,x
    dex
    bpl @hd5
    tsx
    txa
    sta RESBASE+5
    ldx #$07
@cpst:
    lda $2400,x
    sta RESBASE+6,x
    dex
    bpl @cpst
    tma #$01
    sta RESBASE+14
    tma #$02
    sta RESBASE+15
    tma #$04
    sta RESBASE+16
    tma #$08
    sta RESBASE+17
    tma #$10
    sta RESBASE+18
    tma #$20
    sta RESBASE+19
    tma #$40
    sta RESBASE+20
    tma #$80
    sta RESBASE+21
    lda #$AA
    sta RESBASE
    lda #$A5
    sta MARK
    lda #$5A
    sta MARK+1
    tma #$02
    sta MARK+4
    tsx
    txa
    sta MARK+5
    lda #$3C
    sta MARK+6
payload_done:
    jmp (RETVEC)
"""

PAYLOAD_B1 = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
MARK = $3F00
RETVEC = $3F10
.ifndef LINKADDR
.error "LINKADDR required"
.endif
.ifndef IDXHI
.error "IDXHI required"
.endif
.ifndef IDXMID
.error "IDXMID required"
.endif
.ifndef IDXLO
.error "IDXLO required"
.endif
payload_entry:
    cld
    lda #IDXHI
    sta $2400
    lda #IDXMID
    sta $2401
    lda #IDXLO
    sta $2402
    lda #$01
    sta $2403
    lda #$00
    sta $2404
    sta $2405
    sta $2406
    sta $2407
    lda #IDXLO
    sta $20F5
    lda #IDXMID
    sta $20F6
    lda #IDXHI
    sta $20F7
    lda #$00
    sta $20F2
    sta $20F3
    sta $20F4
    lda #$30
    sta $2404
    jsr LINKADDR
    sta RESBASE+1
    php
    pla
    sta RESBASE+7
    lda #$00
    sta $2404
    lda #$20
    sta $20F7
    jsr LINKADDR
    sta RESBASE+2
    lda #IDXHI
    sta $20F7
    lda #$00
    sta $20F2
    sta $20F3
    lda #$20
    sta $20F4
    jsr LINKADDR
    sta RESBASE+3
    tsx
    txa
    sta RESBASE+8
    ldx #$07
@cpst:
    lda $2400,x
    sta RESBASE+11,x
    dex
    bpl @cpst
    ldx #$09
@cplr:
    lda $2320,x
    sta RESBASE+19,x
    dex
    bpl @cplr
    tma #$01
    sta RESBASE+29
    tma #$02
    sta RESBASE+30
    tma #$04
    sta RESBASE+31
    tma #$08
    sta RESBASE+32
    tma #$10
    sta RESBASE+33
    tma #$20
    sta RESBASE+34
    tma #$40
    sta RESBASE+35
    tma #$80
    sta RESBASE+36
    lda #$AA
    sta RESBASE
    lda #$A5
    sta MARK
    lda #$5A
    sta MARK+1
    tma #$02
    sta MARK+4
    tsx
    txa
    sta MARK+5
    lda #$3C
    sta MARK+6
payload_done:
    jmp (RETVEC)
"""

PAYLOAD_B2 = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
MARK = $3F00
RETVEC = $3F10
.ifndef LINKADDR
.error "LINKADDR required"
.endif
.ifndef IDXHI
.error "IDXHI required"
.endif
.ifndef IDXMID
.error "IDXMID required"
.endif
.ifndef IDXLO
.error "IDXLO required"
.endif
payload_entry:
    cld
    lda #IDXHI
    sta $2400
    lda #IDXMID
    sta $2401
    lda #IDXLO
    sta $2402
    lda #$01
    sta $2403
    lda #$00
    sta $2404
    sta $2405
    sta $2406
    sta $2407
    lda #IDXLO
    sta $20F5
    lda #IDXMID
    sta $20F6
    lda #IDXHI
    sta $20F7
    lda #$00
    sta $20F2
    sta $20F3
    sta $20F4
    lda #IDXLO
    clc
    adc #$0A
    sta $20F5
    lda #IDXMID
    adc #$00
    sta $20F6
    lda #IDXHI
    adc #$00
    sta $20F7
    jsr LINKADDR
    sta RESBASE+1
    php
    pla
    sta RESBASE+7
    lda #IDXLO
    sta $20F5
    lda #IDXMID
    sta $20F6
    lda #IDXHI
    sta $20F7
    lda #$10
    sta $20F2
    jsr LINKADDR
    sta RESBASE+2
    lda #$00
    sta $2400
    lda #$FF
    sta $2401
    sta $2402
    lda #$01
    sta $20F2
    lda #$00
    sta $20F3
    sta $20F4
    jsr LINKADDR
    sta RESBASE+3
    tsx
    txa
    sta RESBASE+8
    ldx #$07
@cpst:
    lda $2400,x
    sta RESBASE+11,x
    dex
    bpl @cpst
    ldx #$09
@cplr:
    lda $2320,x
    sta RESBASE+19,x
    dex
    bpl @cplr
    tma #$01
    sta RESBASE+29
    tma #$02
    sta RESBASE+30
    tma #$04
    sta RESBASE+31
    tma #$08
    sta RESBASE+32
    tma #$10
    sta RESBASE+33
    tma #$20
    sta RESBASE+34
    tma #$40
    sta RESBASE+35
    tma #$80
    sta RESBASE+36
    lda #$AA
    sta RESBASE
    lda #$A5
    sta MARK
    lda #$5A
    sta MARK+1
    tma #$02
    sta MARK+4
    tsx
    txa
    sta MARK+5
    lda #$3C
    sta MARK+6
payload_done:
    jmp (RETVEC)
"""

PAYLOAD_C = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
MARK = $3F00
RETVEC = $3F10
.ifndef LINKADDR
.error "LINKADDR required"
.endif
payload_entry:
    cld
    lda #$00
    sta $2400
    sta $2401
    lda #$96
    sta $2402
    lda #$01
    sta $2403
    lda #$00
    sta $2404
    sta $2405
    sta $2406
    sta $2407
    lda #$96
    sta $20F5
    lda #$00
    sta $20F6
    sta $20F7
    lda #$00
    sta $20F2
    sta $20F3
    sta $20F4
    jsr LINKADDR
    sta RESBASE+1
    php
    pla
    sta RESBASE+8
    ldx #$09
@cplr:
    lda $2320,x
    sta RESBASE+11,x
    dex
    bpl @cplr
    tsx
    txa
    sta RESBASE+10
    tma #$01
    sta RESBASE+27
    tma #$02
    sta RESBASE+28
    tma #$04
    sta RESBASE+29
    tma #$08
    sta RESBASE+30
    tma #$10
    sta RESBASE+31
    tma #$20
    sta RESBASE+32
    tma #$40
    sta RESBASE+33
    tma #$80
    sta RESBASE+34
    lda #$AA
    sta RESBASE
    lda #$A5
    sta MARK
    lda #$5A
    sta MARK+1
    tma #$02
    sta MARK+4
    tsx
    txa
    sta MARK+5
    lda #$3C
    sta MARK+6
payload_done:
    jmp (RETVEC)
"""


def build_payload(source_text, out_dir, name, link_addr, idx_lo, idx_mid, idx_hi):
    from environment import executable
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = out_dir / f"{name}.s"
    src.write_text(source_text, encoding="ascii")
    obj = out_dir / f"{name}.o"
    bin_path = out_dir / f"{name}.bin"
    lbl = out_dir / f"{name}.lbl"
    cfg = out_dir / f"{name}.cfg"
    cfg.write_text("MEMORY { RAM: start = $3000, size = $0800, file = %O; }\n"
                   "SEGMENTS { PAYLOAD: load = RAM, type = ro, start = $3000; }\n",
                   encoding="ascii")
    defines = [f"-DLINKADDR=${link_addr:04X}", f"-DIDXLO={idx_lo}",
               f"-DIDXMID={idx_mid}", f"-DIDXHI={idx_hi}"]
    subprocess.run([executable("ca65"), str(src), *defines, "-o", str(obj), "-g"], check=True)
    subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(bin_path),
                    "-Ln", str(lbl), str(obj)], check=True)
    code = bin_path.read_bytes()
    if not 1 <= len(code) <= 255:
        raise ValueError(f"{name} payload {len(code)} bytes outside 1..255")
    text = lbl.read_text(encoding="utf-8")
    import re
    found = {}
    for address, label in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", text, re.M):
        if label in ("payload_entry", "payload_done"):
            found.setdefault(label, int(address, 16))
    if set(found) != {"payload_entry", "payload_done"}:
        raise ValueError(f"{name} payload labels missing")
    from build_cdload_exec import EXEC_ADDRESS
    if found["payload_entry"] != EXEC_ADDRESS:
        raise ValueError(f"{name} payload_entry != ${EXEC_ADDRESS:04X}")
    return code, found


def patch_index01_sector(cue, index01, code):
    from make_cd_image import RAW_BYTES
    from build_cdload_exec import USER_OFFSET, SECTOR_BYTES, encode_sector
    bin_name = cue.name.replace(".cue", ".bin")
    raw = bytearray((cue.parent / bin_name).read_bytes())
    sector = encode_sector(index01, code)
    at = 0 * RAW_BYTES + USER_OFFSET
    raw[at:at + SECTOR_BYTES] = sector
    (cue.parent / bin_name).write_bytes(bytes(raw))


def link_results(buffer):
    if len(buffer) != 6144:
        raise ValueError(f"RAM dump {len(buffer)} bytes, expected 6144")
    res = buffer[RES_OFF:RES_OFF + 35]
    return {
        "magic": res[0],
        "a_vals": list(res[1:8]),
        "p": res[8],
        "sp": res[10],
        "state": list(res[11:19]),
        "mpr": list(res[27:35]),
        "raw": bytes(res),
    }


class LinkRealCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux"):
            raise unittest.SkipTest("real-core needs Linux-pinned Geargrafx")
        try:
            from environment import executable
            executable("ca65")
        except Exception as error:
            raise unittest.SkipTest(f"cc65 unavailable: {error}")
        cls.tmp = tempfile.TemporaryDirectory(prefix="pce-cd-read-link-")
        root = Path(cls.tmp.name)
        (root / "probe").mkdir(parents=True, exist_ok=True)
        from geargrafx_api import compile_probe
        cls.probe = compile_probe(root / "probe", "geargrafx_cdread_probe.cpp",
                                  "geargrafx-cd-read-link")
        from build_rom import verify_dev_rom
        cls.base = ROOT / "build" / MODE / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
        if not cls.base.exists():
            subprocess.run([sys.executable, str(ROOT / "tools" / "build_rom.py"),
                            "--mode", MODE], check=True)
        cls.cases = {}
        import cd_base_probe
        from experimental_track_selection import selection_from_cue, snapshot_media, verify_media_identity
        from cd_read_link_image import build_link_image
        from build_cdload_exec import encode_sector
        from make_cd_image import sector_payload
        for fixture, build_fn, idx in (("idx150", cd_base_probe.build_fixture, 150),
                                       ("idx0", cd_base_probe.build_zero_fixture, 0)):
            for payload_name, source in (("a", PAYLOAD_A), ("b1", PAYLOAD_B1), ("b2", PAYLOAD_B2)):
                case_dir = root / f"{fixture}-{payload_name}"
                case_dir.mkdir()
                if fixture == "idx150":
                    cd_base_probe.build_fixture(case_dir)
                    cue = case_dir / cd_base_probe.CUE_NAME
                else:
                    cd_base_probe.build_zero_fixture(case_dir)
                    cue = case_dir / cd_base_probe.ZERO_CUE_NAME
                idx_lo, idx_mid, idx_hi = idx & 0xFF, (idx >> 8) & 0xFF, (idx >> 16) & 0xFF
                # First assemble a dummy payload to learn the link address:
                # build the ROM with a placeholder, read link addr, rebuild payload.
                # Simpler: build ROM first with a throwaway payload, then rebuild.
                from environment import executable as exe
                # Temporary minimal payload to get link address.
                tmp_pay_dir = case_dir / "tmp-pay"
                tmp_pay_dir.mkdir()
                # Minimal 1-byte payload (RTS-like? must be valid for encode check?
                # encode_sector requires 1..255 bytes; use single NOP $EA.)
                dummy_code = bytes((0xEA,))
                dummy_syms = {"payload_entry": 0x3000, "payload_done": 0x3001}
                # Patch dummy to learn addresses (media check needs encode match).
                patch_index01_sector(cue, idx, dummy_code)
                cue_m = snapshot_media(cue, root / f"{fixture}-{payload_name}-mounted0")
                selected = selection_from_cue(cue_m, cue_m)
                out0 = case_dir / "rom0"
                build_link_image(cls.base, out0, MODE, cue_m, dummy_code, dummy_syms)
                import json
                manifest0 = json.loads((out0 / "manifest.json").read_text())
                link_addr = manifest0["symbols"]["cd_read_link_entry"]
                # Real payload with the link address.
                code, pay_syms = build_payload(source, case_dir / "pay", payload_name,
                                               link_addr, idx_lo, idx_mid, idx_hi)
                # Repatch the *original* cue dir? Rebuild fixture to reset BIN,
                # then patch with the real payload.
                import shutil
                # Rebuild fixture BIN cleanly.
                if fixture == "idx150":
                    cd_base_probe.build_fixture(case_dir)
                    cue = case_dir / cd_base_probe.CUE_NAME
                else:
                    cd_base_probe.build_zero_fixture(case_dir)
                    cue = case_dir / cd_base_probe.ZERO_CUE_NAME
                patch_index01_sector(cue, idx, code)
                cue = snapshot_media(cue, root / f"{fixture}-{payload_name}-mounted")
                selected = selection_from_cue(cue, cue)
                if selected["lba"] != idx or selected["track"] != 1:
                    raise ValueError(f"selection mismatch: {selected}")
                out = case_dir / "rom"
                build_link_image(cls.base, out, MODE, cue, code, pay_syms)
                manifest = json.loads((out / "manifest.json").read_text())
                if manifest["symbols"]["cd_read_link_entry"] != link_addr:
                    raise ValueError("link address moved between dummy and real build")
                labels = out / "trace-labels.txt"
                verify_media_identity(selected["media_identity"], cue)
                from geargrafx_cdread import execute
                records = execute(cls.probe,
                                  out / "experimental-dev-only-not-compatible-syscard-sized.pce",
                                  cue, labels, STEP_BUDGET)
                cls.cases[(fixture, payload_name)] = (records, manifest, selected, code)

        # Payload C: idx0 disc, window 150 (off-disc READ_FAIL).
        case_dir = root / "idx0-c"
        case_dir.mkdir()
        cd_base_probe.build_fixture(case_dir) if False else cd_base_probe.build_zero_fixture(case_dir)
        cue = case_dir / cd_base_probe.ZERO_CUE_NAME
        dummy_code = bytes((0xEA,))
        dummy_syms = {"payload_entry": 0x3000, "payload_done": 0x3001}
        patch_index01_sector(cue, 0, dummy_code)
        cue_m = snapshot_media(cue, root / "idx0-c-mounted0")
        out0 = case_dir / "rom0"
        build_link_image(cls.base, out0, MODE, cue_m, dummy_code, dummy_syms)
        import json
        manifest0 = json.loads((out0 / "manifest.json").read_text())
        link_addr = manifest0["symbols"]["cd_read_link_entry"]
        code, pay_syms = build_payload(PAYLOAD_C, case_dir / "pay", "c", link_addr, 0, 0, 0)
        cd_base_probe.build_zero_fixture(case_dir)
        cue = case_dir / cd_base_probe.ZERO_CUE_NAME
        patch_index01_sector(cue, 0, code)
        cue = snapshot_media(cue, root / "idx0-c-mounted")
        selected = selection_from_cue(cue, cue)
        out = case_dir / "rom"
        build_link_image(cls.base, out, MODE, cue, code, pay_syms)
        manifest = json.loads((out / "manifest.json").read_text())
        labels = out / "trace-labels.txt"
        verify_media_identity(selected["media_identity"], cue)
        from geargrafx_cdread import execute
        records = execute(cls.probe,
                          out / "experimental-dev-only-not-compatible-syscard-sized.pce",
                          cue, labels, STEP_BUDGET)
        cls.cases[("idx0", "c")] = (records, manifest, selected, code)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check_loader_success(self, records, manifest, selected, code):
        from build_cdload_exec import EXEC_ADDRESS, EXPECTED_MPR, encode_sector, SECTOR_BYTES
        from geargrafx_cdread import execute  # noqa
        from cdread_verify import check_halt_confirmation, result_block
        self.assertEqual([r["cycle"] for r in records["RUN"]], ["cold", "warm"])
        self.assertFalse(records.get("BLOCK"))
        for run in records["RUN"]:
            self.assertEqual(run["reason"], "cd_halt_ok")
            self.assertEqual(run["pc"], manifest["trace_symbols"]["cd_halt_ok"])
            check_halt_confirmation(run)
            self.assertEqual(run["sp"], 0xFF)
            block = result_block(run)
            # Link clobbers ZP ptr, so the final RES byte count reflects the
            # link's last transfer (2048 on success, 0 on refusal/fail), not
            # the bootloader's. Only code/stage prove the bootloader handoff.
            self.assertEqual((block["code"], block["stage"]), (0, 16))

    def test_a_idx150_valid_transfers(self):
        from build_cdload_exec import encode_sector, SECTOR_BYTES
        from make_cd_image import sector_payload
        records, manifest, selected, code = self.cases[("idx150", "a")]
        self.check_loader_success(records, manifest, selected, code)
        for run in records["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            res = buf[RES_OFF:RES_OFF + 200]
            self.assertEqual(res[0], 0xAA)
            self.assertEqual((res[1], res[2], res[3]), (0, 0, 0))
            self.assertEqual(res[4], 0x16)
            self.assertEqual(res[5], 0xFF)
            self.assertEqual(list(buf[RES_OFF + 6:RES_OFF + 14]), [0, 0, 150, 1, 0, 0, 0, 0])
            self.assertEqual(list(buf[RES_OFF + 14:RES_OFF + 22]), EXPECTED_MPR)
            # LINK_RES snapshots: LBA 150/151/155, success, 2048 bytes.
            for off, lba in ((22, 150), (32, 151), (42, 155)):
                lr = buf[RES_OFF + off:RES_OFF + off + 10]
                self.assertEqual(lr[0], 0, lba)
                self.assertEqual(lr[1], 8, lba)
                self.assertEqual(int.from_bytes(lr[2:4], "little"), 2048, lba)
                self.assertEqual((lr[7], lr[8], lr[9]), (0, 0, lba & 0xFF), lba)
            # $2800 heads saved per call.
            self.assertEqual(buf[RES_OFF + 52:RES_OFF + 84], encode_sector(150, code)[:32])
            self.assertEqual(buf[RES_OFF + 84:RES_OFF + 116], sector_payload(151)[:32])
            self.assertEqual(buf[RES_OFF + 116:RES_OFF + 148], sector_payload(155)[:32])
            # Final $2800 = last sector (155).
            self.assertEqual(buf[:SECTOR_BYTES], sector_payload(155))
            # Bootloader vs link READs distinguishable in PHASE.
            phases = [(r["load_sector"], r["load_count"]) for r in records["PHASE"]
                      if r["cycle"] == run["cycle"] and r["scsi_phase"] == 4]
            self.assertIn((150, 1), phases)
            self.assertIn((151, 1), phases)
            self.assertIn((155, 1), phases)

    def test_a_idx0_valid_transfers(self):
        from build_cdload_exec import encode_sector
        from make_cd_image import sector_payload
        records, manifest, selected, code = self.cases[("idx0", "a")]
        self.check_loader_success(records, manifest, selected, code)
        for run in records["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            res = buf[RES_OFF:RES_OFF + 200]
            self.assertEqual(res[0], 0xAA)
            self.assertEqual((res[1], res[2], res[3]), (0, 0, 0))
            self.assertEqual(list(buf[RES_OFF + 6:RES_OFF + 14]), [0, 0, 0, 1, 0, 0, 0, 0])
            for off, lba in ((22, 0), (32, 1), (42, 5)):
                lr = buf[RES_OFF + off:RES_OFF + off + 10]
                self.assertEqual(lr[0], 0, lba)
                self.assertEqual(int.from_bytes(lr[2:4], "little"), 2048, lba)
            self.assertEqual(buf[RES_OFF + 52:RES_OFF + 84], encode_sector(0, code)[:32])
            self.assertEqual(buf[RES_OFF + 84:RES_OFF + 116], sector_payload(1)[:32])
            self.assertEqual(buf[:2048], sector_payload(5))

    def test_b1_refusals_no_scsi(self):
        for fixture in ("idx150", "idx0"):
            records, manifest, selected, code = self.cases[(fixture, "b1")]
            self.check_loader_success(records, manifest, selected, code)
            for run in records["RUN"]:
                buf = bytes.fromhex(run["buffer_hex"])
                res = buf[RES_OFF:RES_OFF + 40]
                self.assertEqual(res[0], 0xAA, fixture)
                self.assertEqual(res[1], 0x60, fixture)
                self.assertEqual(res[2], 0x61, fixture)
                self.assertEqual(res[3], 0x62, fixture)
                self.assertEqual(res[7], 0x15, fixture)
                self.assertEqual(res[8], 0xFF, fixture)
                from build_cdload_exec import encode_sector
                self.assertEqual(buf[:2048], encode_sector(selected["lba"], code), fixture)
                phases = [(r["load_sector"], r["load_count"]) for r in records["PHASE"]
                          if r["cycle"] == run["cycle"] and r["scsi_phase"] == 4]
                self.assertEqual(phases, [(selected["lba"], 1)], fixture)

    def test_b2_refusals_no_scsi_carry(self):
        for fixture in ("idx150", "idx0"):
            records, manifest, selected, code = self.cases[(fixture, "b2")]
            self.check_loader_success(records, manifest, selected, code)
            for run in records["RUN"]:
                buf = bytes.fromhex(run["buffer_hex"])
                res = buf[RES_OFF:RES_OFF + 40]
                self.assertEqual(res[0], 0xAA, fixture)
                self.assertEqual(res[1], 0x63, fixture)
                self.assertEqual(res[2], 0x64, fixture)
                self.assertEqual(res[3], 0x64, fixture)
                self.assertEqual(res[7], 0x15, fixture)
                self.assertEqual(res[8], 0xFF, fixture)
                lr = buf[RES_OFF + 19:RES_OFF + 29]
                self.assertEqual((lr[7], lr[8], lr[9]), (1, 0, 0), fixture)
                from build_cdload_exec import encode_sector
                self.assertEqual(buf[:2048], encode_sector(selected["lba"], code), fixture)
                phases = [(r["load_sector"], r["load_count"]) for r in records["PHASE"]
                          if r["cycle"] == run["cycle"] and r["scsi_phase"] == 4]
                self.assertEqual(phases, [(selected["lba"], 1)], fixture)

    def test_c_offdisc_read_fail(self):
        from build_cdload_exec import encode_sector
        records, manifest, selected, code = self.cases[("idx0", "c")]
        self.check_loader_success(records, manifest, selected, code)
        for run in records["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            res = buf[RES_OFF:RES_OFF + 35]
            self.assertEqual(res[0], 0xAA)
            self.assertEqual(res[1], 0x70)
            self.assertEqual(res[8], 0x15)
            lr = buf[RES_OFF + 11:RES_OFF + 21]
            self.assertEqual(lr[0], 0x70)
            self.assertEqual((lr[7], lr[8], lr[9]), (0, 0, 0x96))
            # Off-disc READ never reaches DATA_IN: bootloader DATA_IN present,
            # link DATA_IN absent, but LINK_RES proves the link attempted LBA 150.
            # LOAD_SECTOR only updates on DATA_IN, so no (150,1) phase exists.
            data_phases = [(r["load_sector"], r["load_count"]) for r in records["PHASE"]
                           if r["cycle"] == run["cycle"] and r["scsi_phase"] == 4]
            self.assertIn((0, 1), data_phases)  # bootloader DATA_IN
            self.assertNotIn((150, 1), data_phases)  # off-disc: no DATA_IN
            # $2800 untouched (0 bytes transferred, still bootloader sector).
            self.assertEqual(buf[:2048], encode_sector(selected["lba"], code))


if __name__ == "__main__":
    unittest.main()
