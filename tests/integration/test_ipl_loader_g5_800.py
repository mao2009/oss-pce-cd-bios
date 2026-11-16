"""Issue #800 integration: PUBLIC IPL loader + experimental $02/$03/$33 (synthetic).

Parent #635. Synthetic only, no ./rom probing (NOT_RUN fixed record).
NOT a compatible System Card BIOS, NOT an ABI claim, release_eligible=False.

The G5 IPL loader ROM (tools/build_ipl_loader_g5.py) boots a PUBLIC
multi-sector IPL (INFO at INDEX01+1, BODY at INDEX01+IPLBLK) and wires
$02/$03/$33 to real experimental bodies. Payloads are test-owned, assembled
with the pinned ca65:

  chain:   JSR $E006 (base == INDEX01 265 -> DATA) -> JSR $E009 (block 2,
           window INDEX01 -> valid READ(6) LBA 267, 2048B to $2800) ->
           seed VDC shadow -> JSR $E099 (C=0 ok) -> halt. Proves arrival,
           real side effects (state/LINK_RES/shadow/transfer), return
           position, SP/MPR, order, cold/warm repeatability.
  coexist: JSR $E006 sanity -> JSR $E063 ($21 JOYSNS) -> JSR $E069 ($23
           SCRSIZ, A=$11) -> MA_MUL16S $E0ED (2*3) -> halt. Proves normal
           DEV 18 APIs work on the PUBLIC path in the same image.
  unimpl:  JSR $E00C ($04 stub) traps with X=$04 at api_unimplemented.
  badinfo: chain disc with corrupted INFO signature -> cd_halt_fail(20),
           payload never runs, load area untouched.

Every success case re-checks RESET->IPL boot (INFO+ BODY READs, result block,
code at $3000). No fake RTS in the wired path (all three table entries JMP).
$4A stays a stub (SIMULATED control separate). Commercial: NOT_RUN.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.environment import cc65_required, cc65_skip_reason, requires_cc65  # noqa: E402

MODE = "debug"
SEL = 0x0109  # synthetic INDEX 01 (265), same as geargrafx_ipl_loader
STEP_BUDGET = 20_000_000
BUFFER_BASE = 0x2800
RES_OFF = 0x3100 - BUFFER_BASE
SEQ02 = 0xE006
ENTRY03 = 0xE009
ENTRY21 = 0xE063
ENTRY23 = 0xE069
ENTRY33 = 0xE099
ENTRY4F = 0xE0ED
ENTRY_UNIMPL = 0xE00C  # slot $04
P_MASK = 0xCF

G5_CHAIN_PUBLIC_ASM = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
SEQ02 = $E006
ENTRY03 = $E009
ENTRY33 = $E099
payload_entry:
    cld
    lda #$00
    ldx #$07
@zst: sta $2400,x
    dex
    bpl @zst
    lda #$FF
    sta $2404
    lda #SELTRACK
    sta $20F8
    lda #$00
    sta $20F9
    sta $20FA
    sta $20FF
    lda #$80
    sta $20FB
    lda #IDXLO
    sta $20FC
    lda #IDXMID
    sta $20FD
    lda #IDXHI
    sta $20FE
    ldx #$FF
    txs
    jsr SEQ02
    sta RESBASE+1
    php
    pla
    sta RESBASE+2
    lda #$02
    sta RESBASE+3
    ldx #$07
@cpst: lda $2400,x
    sta RESBASE+4,x
    dex
    bpl @cpst
    lda #$02
    sta $20F2
    lda #$00
    sta $20F3
    sta $20F4
    lda #IDXLO
    sta $20F5
    lda #IDXMID
    sta $20F6
    lda #IDXHI
    sta $20F7
    jsr ENTRY03
    sta RESBASE+12
    php
    pla
    sta RESBASE+13
    tsx
    txa
    sta RESBASE+14
    lda #$03
    sta RESBASE+15
    ldx #$09
@cplr: lda $2320,x
    sta RESBASE+16,x
    dex
    bpl @cplr
    ldx #$07
@hd: lda $2800,x
    sta RESBASE+26,x
    dex
    bpl @hd
    lda #CRLOW
    sta $2502
    lda #$A5
    sta $2503
    sei
    lda #$12
    ldx #$34
    ldy #$56
    jsr ENTRY33
    sta RESBASE+34
    php
    pla
    sta RESBASE+35
    tsx
    txa
    sta RESBASE+36
    lda #$33
    sta RESBASE+37
    lda $2502
    sta RESBASE+38
    lda $2503
    sta RESBASE+39
    tma #$01
    sta RESBASE+40
    tma #$02
    sta RESBASE+41
    lda #$AA
    sta RESBASE
payload_done:
    bra payload_done
"""

G5_COEXIST_PUBLIC_ASM = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
SEQ02 = $E006
ENTRY21 = $E063
ENTRY23 = $E069
ENTRY4F = $E0ED
payload_entry:
    cld
    lda #$00
    ldx #$07
@zst: sta $2400,x
    dex
    bpl @zst
    lda #$FF
    sta $2404
    lda #SELTRACK
    sta $20F8
    lda #$00
    sta $20F9
    sta $20FA
    sta $20FF
    lda #$80
    sta $20FB
    lda #IDXLO
    sta $20FC
    lda #IDXMID
    sta $20FD
    lda #IDXHI
    sta $20FE
    ldx #$FF
    txs
    jsr SEQ02
    sta RESBASE+1
    php
    pla
    sta RESBASE+2
    lda #$02
    sta RESBASE+3
    ldx #$07
@cpst: lda $2400,x
    sta RESBASE+4,x
    dex
    bpl @cpst
    jsr ENTRY21
    sta RESBASE+12
    php
    pla
    sta RESBASE+13
    lda #$21
    sta RESBASE+14
    lda #$11
    jsr ENTRY23
    sta RESBASE+15
    php
    pla
    sta RESBASE+16
    lda #$23
    sta RESBASE+17
    lda #$02
    pha
    lda #$00
    pha
    lda #$03
    pha
    lda #$00
    pha
    jsr ENTRY4F
    pla
    sta RESBASE+18
    pla
    sta RESBASE+19
    pla
    sta RESBASE+20
    pla
    sta RESBASE+21
    php
    pla
    sta RESBASE+22
    lda #$4F
    sta RESBASE+23
    tma #$02
    sta RESBASE+24
    tsx
    txa
    sta RESBASE+25
    lda #$AA
    sta RESBASE
payload_done:
    bra payload_done
"""

G5_UNIMPL_PUBLIC_ASM = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
ENTRY_UNIMPL = $E00C
payload_entry:
    cld
    lda #$00
    sta RESBASE+1
    lda #$AA
    sta RESBASE
    ldx #$FF
    txs
    jsr ENTRY_UNIMPL
    lda #$BB
    sta RESBASE+1
payload_done:
    bra payload_done
"""


def build_custom_payload(source_text, out_dir, name, defines):
    import subprocess
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
    subprocess.run([executable("ca65"), str(src), *defines, "-o", str(obj), "-g"], check=True)
    subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(bin_path),
                    "-Ln", str(lbl), str(obj)], check=True)
    code = bin_path.read_bytes()
    if not 1 <= len(code) <= 512:
        raise ValueError(f"{name} payload {len(code)} bytes outside 1..512")
    import re
    found = {}
    for address, label in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$",
                                     lbl.read_text(encoding="utf-8"), re.M):
        if label in ("payload_entry", "payload_done"):
            found.setdefault(label, int(address, 16))
    if set(found) != {"payload_entry", "payload_done"}:
        raise ValueError(f"{name} payload labels missing")
    if found["payload_entry"] != 0x3000:
        raise ValueError(f"{name} payload_entry != $3000")
    return code, found


def chain_record(buffer):
    if len(buffer) != 6144:
        raise ValueError(f"RAM dump {len(buffer)} bytes, expected 6144")
    res = buffer[RES_OFF:RES_OFF + 42]
    return {
        "magic": res[0],
        "a1": res[1], "p1": res[2], "order1": res[3],
        "state": list(res[4:12]),
        "a2": res[12], "p2": res[13], "sp2": res[14], "order2": res[15],
        "link_res": list(res[16:26]),
        "head": bytes(res[26:34]),
        "a3": res[34], "p3": res[35], "sp3": res[36], "order3": res[37],
        "shadow": list(res[38:40]),
        "mpr01": list(res[40:42]),
    }


def coexist_record(buffer):
    if len(buffer) != 6144:
        raise ValueError(f"RAM dump {len(buffer)} bytes, expected 6144")
    res = buffer[RES_OFF:RES_OFF + 26]
    return {
        "magic": res[0],
        "a1": res[1], "p1": res[2], "order1": res[3],
        "state": list(res[4:12]),
        "a21": res[12], "p21": res[13], "order21": res[14],
        "a23": res[15], "p23": res[16], "order23": res[17],
        "product": list(res[18:22]),
        "p4f": res[22], "order4f": res[23],
        "mpr1": res[24], "sp": res[25],
    }


@requires_cc65
class IplLoaderG5RealCoreTests(unittest.TestCase):
    """PUBLIC loader + experimental $02/$03/$33 on pinned Geargrafx (Issue #800)."""

    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux"):
            raise unittest.SkipTest("real-core needs Linux-pinned Geargrafx")
        reason = cc65_skip_reason()
        if reason is not None:
            if cc65_required():
                raise AssertionError(f"REQUIRED tool missing: {reason}")
            raise unittest.SkipTest(f"SKIP cc65-dependent: {reason}")
        import shutil
        import tempfile
        from build_rom import build
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom
        from geargrafx_api import compile_probe
        from geargrafx_cdread import execute
        import geargrafx_ipl_loader as L
        cls.L = L
        cls.execute = execute
        cls.work = Path(tempfile.mkdtemp(prefix="pce-800-g5-realcore-"))
        cls.addClassCleanup(shutil.rmtree, cls.work, ignore_errors=True)
        normal_path = build(cls.work / "normal", root=ROOT)
        cls.normal = normal_path.read_bytes()
        out = cls.work / "g5rom"
        rom_path, syms = build_ipl_loader_g5_rom(
            ROOT / "build" / MODE / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
            if (ROOT / "build" / MODE / "bios-dev" / "dev-only-not-compatible-syscard3.pce").is_file()
            else normal_path,
            out, SEL, MODE, "t800", ["-D", "IPL_PUBLIC"], track=1)
        # If the checked-in build tree was stale, fall back to the fresh normal
        # ROM built above (byte-identical by verify_dev_rom, never a mismatch).
        cls.rom_path = rom_path
        cls.rom = rom_path.read_bytes()
        cls.syms = syms
        if normal_path.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        # NO fake RTS: all three wired entries are JMP.
        for entry in (SEQ02, ENTRY03, ENTRY33):
            if cls.rom[entry - 0xE000] != 0x4C:
                raise ValueError(f"BLOCKED: table entry ${entry:04X} is not JMP (fake RTS?)")
        probe_dir = cls.work / "probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        cls.probe = compile_probe(probe_dir, "geargrafx_cdread_probe.cpp",
                                  "geargrafx-800-g5-probe")
        cls.cases = {}
        payload_sources = {
            "chain": (G5_CHAIN_PUBLIC_ASM,
                      ["-DSELTRACK=1", "-DIDXLO=9", "-DIDXMID=1", "-DIDXHI=0", "-DCRLOW=242"]),
            "coexist": (G5_COEXIST_PUBLIC_ASM,
                        ["-DSELTRACK=1", "-DIDXLO=9", "-DIDXMID=1", "-DIDXHI=0"]),
            "unimpl": (G5_UNIMPL_PUBLIC_ASM, []),
        }
        for name, (source, defines) in payload_sources.items():
            code, pay_syms = build_custom_payload(
                source, cls.work / f"pay-{name}", name, defines)
            # PUBLIC disc: N=5, IPLBLK=2, STA=$3000, ENTRY=$3000, MPR raw 0..4.
            case = {"name": f"g5_{name}", "n": 5, "iplblk": 2, "sta": 0x3000,
                    "entry": 0x3000, "mprs": [0, 1, 2, 3, 4], "openmode": 0,
                    "public": True}
            body = L.body_for(code, case["n"])
            cksum = sum(body) & 0xFFFF
            info = L.info_sector(case["iplblk"], case["n"], case["sta"],
                                 case["entry"], case["mprs"], case["openmode"], cksum)
            # PUBLIC: bytes 14..15 ignored (zero them for determinism).
            info[14] = 0
            info[15] = 0
            disc = cls.work / f"disc-{name}"
            cue = L.make_disc(disc, L.case_disc(case, body, info))
            labels = cls.work / f"{name}.labels"
            labels.write_text(
                f"cd_halt_ok {pay_syms['payload_done']:04x}\n"
                f"cd_halt_fail {syms['cd_halt_fail']:04x}\n"
                f"cd_unexpected_interrupt {syms['cd_unexpected_interrupt']:04x}\n"
                f"api_unimplemented {syms['api_unimplemented']:04x}\n",
                encoding="ascii")
            records = execute(cls.probe, rom_path, cue, labels, STEP_BUDGET)
            cls.cases[name] = {"records": records, "code": code, "body": body,
                               "pay_syms": pay_syms, "cue": cue, "labels": labels,
                               "case": case}
        # badinfo: chain code but corrupted INFO signature -> loader halt.
        bad = cls.cases["chain"]
        case = dict(bad["case"])
        case["name"] = "g5_badinfo"
        body = bad["body"]
        cksum = sum(body) & 0xFFFF
        info = L.info_sector(case["iplblk"], case["n"], case["sta"],
                             case["entry"], case["mprs"], case["openmode"], cksum)
        info[14] = 0
        info[15] = 0
        info[32] = ord("X")  # "PC Engine..." -> broken signature
        disc = cls.work / "disc-badinfo"
        cue = L.make_disc(disc, L.case_disc(case, body, info))
        labels = cls.work / "badinfo.labels"
        labels.write_text(
            f"cd_halt_ok {bad['pay_syms']['payload_done']:04x}\n"
            f"cd_halt_fail {syms['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {syms['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {syms['api_unimplemented']:04x}\n",
            encoding="ascii")
        records = execute(cls.probe, rom_path, cue, labels, STEP_BUDGET)
        cls.cases["badinfo"] = {"records": records, "code": bad["code"],
                                "body": body, "cue": cue, "labels": labels,
                                "case": case}
        cls.evidence = {
            "scope": ("PUBLIC IPL loader + $02/$03/$33 experimental wiring; "
                      "synthetic only, NOT an ABI claim"),
            "rom_sha256": syms and __import__("hashlib").sha256(cls.rom).hexdigest(),
            "commercial": {"status": "NOT_RUN",
                           "reason": "Commercial media not requested; synthetic-only test"},
        }

    def check_boot_path(self, name):
        case = self.cases[name]
        records, body, code = case["records"], case["body"], case["code"]
        self.assertEqual([r["cycle"] for r in records["RUN"]], ["cold", "warm"])
        for run in records["RUN"]:
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(run["reason"], "cd_halt_ok")
                self.assertTrue(run["halt_confirmed"])
                buf = bytes.fromhex(run["buffer_hex"])
                self.assertEqual(len(buf), 6144)
                # Payload code prefix at $3000 is intact (link writes $2800,
                # payload results overwrite $3100, so the full BODY image is
                # NOT compared here; the transfer is proven via $2800 below).
                self.assertEqual(
                    buf[0x3000 - BUFFER_BASE:0x3000 - BUFFER_BASE + len(code)],
                    code)
                # Payload ran (magic $AA at $3100).
                self.assertEqual(buf[RES_OFF], 0xAA)
                # Boot READs: INFO (SEL+1=266) + BODY (SEL+IPLBLK .. =267..271).
                # The link case re-reads 267; PHASE must contain the boot set.
                phases = sorted((p["load_sector"], p["load_count"])
                                for p in records["PHASE"]
                                if p["cycle"] == run["cycle"] and p["scsi_phase"] == 4)
                for want in (266, 267, 268, 269, 270, 271):
                    self.assertIn((want, 1), phases,
                                  f"{run['cycle']}: boot READ LBA {want} missing from {phases}")

    def test_reset_boots_public_ipl_cold_and_warm(self):
        for name in ("chain", "coexist"):
            with self.subTest(case=name):
                self.check_boot_path(name)

    def test_chain_calls_proto_link_dspoff(self):
        from make_cd_image import sector_payload
        case = self.cases["chain"]
        self.check_boot_path("chain")
        # Link reads LBA 267 (SEL 265 + block 2): first BODY sector.
        expected_body0 = case["body"][:2048]
        # Boot READs: INFO 266 + BODY 267..271. Link READ: 267 again.
        # $2800 must equal the BODY sector (proves real READ(6) transfer,
        # not stale boot data: INFO 266 differs from BODY 267).
        for run in case["records"]["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            rec = chain_record(buf)
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(rec["magic"], 0xAA)
                self.assertEqual(rec["a1"], 0)
                self.assertEqual(rec["p1"] & 0x03, 0x02)
                self.assertEqual(rec["order1"], 0x02)
                self.assertEqual(rec["state"], [0, 1, 9, 1, 0, 0, 0, 0])
                self.assertEqual(rec["a2"], 0)
                self.assertEqual(rec["p2"] & 0x03, 0x02)
                self.assertEqual(rec["sp2"], 0xFF)
                self.assertEqual(rec["order2"], 0x03)
                self.assertEqual(rec["link_res"],
                                 [0, 8, 0, 8, 0, 0, 0, 0, 1, 11])
                self.assertEqual(buf[:2048], expected_body0)
                self.assertEqual(rec["head"], expected_body0[:8])
                self.assertEqual(rec["a3"], 0x12)
                self.assertEqual(rec["p3"] & 0x01, 0)
                self.assertEqual(rec["sp3"], 0xFF)
                self.assertEqual(rec["order3"], 0x33)
                self.assertEqual(rec["shadow"], [0xF2 & 0x3F, 0xA5])
                self.assertEqual(rec["mpr01"], [0xFF, 0xF8])
        recs = [chain_record(bytes.fromhex(r["buffer_hex"]))
                for r in case["records"]["RUN"]]
        self.assertEqual(recs[0], recs[1])
        proof = {
            "arrived_at_callee": all(
                r["state"] == [0, 1, 9, 1, 0, 0, 0, 0]
                and r["link_res"][:4] == [0, 8, 0, 8]
                and r["shadow"] == [0xF2 & 0x3F, 0xA5] for r in recs),
            "post_return_position_observed": all(
                [r["order1"], r["order2"], r["order3"]] == [0x02, 0x03, 0x33]
                for r in recs),
            "sp_restored": all(r["sp2"] == 0xFF and r["sp3"] == 0xFF for r in recs),
            "mpr_consistent": all(r["mpr01"] == [0xFF, 0xF8] for r in recs),
            "order_matches_expected": all(
                [r["order1"], r["order2"], r["order3"]] == [0x02, 0x03, 0x33]
                for r in recs),
        }
        self.assertTrue(all(proof.values()), f"chain return proof failed: {proof}")

    def test_coexistence_with_normal_dev_apis(self):
        case = self.cases["coexist"]
        self.check_boot_path("coexist")
        for run in case["records"]["RUN"]:
            rec = coexist_record(bytes.fromhex(run["buffer_hex"]))
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(rec["magic"], 0xAA)
                self.assertEqual(rec["a1"], 0)
                self.assertEqual(rec["p1"] & 0x03, 0x02)
                self.assertEqual(rec["order1"], 0x02)
                self.assertEqual(rec["state"], [0, 1, 9, 1, 0, 0, 0, 0])
                self.assertEqual(rec["order21"], 0x21)
                self.assertEqual(rec["a23"], 0x11)
                self.assertEqual(rec["order23"], 0x23)
                self.assertEqual(rec["product"], [0, 0, 0, 6])
                self.assertEqual(rec["order4f"], 0x4F)
                self.assertEqual(rec["mpr1"], 0xF8)
                self.assertEqual(rec["sp"], 0xFF)
        recs = [coexist_record(bytes.fromhex(r["buffer_hex"]))
                for r in case["records"]["RUN"]]
        self.assertEqual(recs[0], recs[1])

    def test_unimplemented_slot_traps_fail_closed(self):
        case = self.cases["unimpl"]
        for run in case["records"]["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(buf[RES_OFF], 0xAA)
                self.assertEqual(buf[RES_OFF + 1], 0x00)
                self.assertEqual(run["reason"], "api_unimplemented")
                self.assertTrue(run["halt_confirmed"])
                self.assertEqual(run["pc"], self.syms["api_unimplemented"])
                self.assertEqual(run["x"], 0x04)
                self.assertEqual(run["sp"], 0xFD)

    def test_bad_ipl_format_rejected(self):
        case = self.cases["badinfo"]
        self.assertEqual([r["cycle"] for r in case["records"]["RUN"]], ["cold", "warm"])
        for run in case["records"]["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(run["reason"], "cd_halt_fail")
                self.assertEqual(run["pc"], self.syms["cd_halt_fail"])
                # Result block code 20 (signature mismatch), nothing copied.
                self.assertEqual(buf[0x2F00 - BUFFER_BASE], 20)
                self.assertEqual(buf[0x2F00 - BUFFER_BASE + 2], 0)
                # Payload never ran (magic absent).
                self.assertNotEqual(buf[RES_OFF], 0xAA)

    def test_commercial_media_is_not_run(self):
        self.assertEqual(self.evidence["commercial"],
                         {"status": "NOT_RUN",
                          "reason": "Commercial media not requested; synthetic-only test"})
        blob = json.dumps(self.evidence["commercial"])
        self.assertNotIn(".cue", blob)
        self.assertNotIn(".CUE", blob)
        self.assertNotRegex(blob, r"[0-9a-f]{64}")
        self.assertNotRegex(blob, r"[A-Za-z]:\\|/home/|/Users/")


if __name__ == "__main__":
    unittest.main()
