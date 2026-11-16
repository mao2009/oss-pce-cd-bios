"""G5 DEV profile real-core tests (Issue #798): one profile ROM boots every scenario disc.

Pinned cc65 71746c8 + pinned Geargrafx b49ae82a. Synthetic media only.
Runs in `make test-real-core` / `make integration-test`, never in the host stage.

The profile ROM is built through tools/build_dev_g5.py (thin wrapper over
tools/build_experimental_bios_boot.py: normal 18-override DEV base untouched,
$02 two-stage JMP to cd_base_proto, $E009 JMP to the cd_read_link body, $E099
JMP to the EX_DSPOFF fragment, RESET -> synthetic selection -> READ(6) INDEX01
-> PCEX IPL -> EXEC $3000 -> MARK return). Payloads are test-owned and
assembled at test time with the pinned ca65 (no repo fixtures):

  chain:    JSR $E006 (proto, base == INDEX01 -> DATA) -> JSR $E009 (block 1,
            window INDEX01 -> valid READ(6) of the sector AFTER the boot
            sector, 2048 bytes to $2800) -> seed VDC shadow -> JSR $E099
            (C=0 ok) -> MARK halt. Mirrors the triple-link CHAIN (imported,
            not duplicated).
  coexist:  JSR $E006 (sanity) -> JSR $E063 ($21 EX_JOYSNS) -> JSR $E069
            ($23 EX_SCRSIZ, A=$11) -> MA_MUL16S $E0ED (2*3) -> MARK halt.
            Proves the normal DEV 18 APIs work in the SAME image.
  refuse:   $03 INVALID_STATE/OVER_CAP/BEFORE/AFTER refusals with no SCSI.
            Mirrors the triple-link REFUSE (imported, not duplicated).
  unimpl:   JSR $E00C (slot $04 stub) traps with X=$04 at api_unimplemented.
  badmagic: chain disc with a corrupted sector magic -> cd_halt_fail(20),
            EXEC untouched.
  badsel:   chain ROM with a corrupted selection-track byte -> dev_selection_halt
            before any READ(6).

Every success case re-checks the RESET->IPL boot path (first-arrival label
order, payload at $3000, exactly one boot READ, MARK) via the shared
geargrafx_experimental_track_contract judges (imported, not duplicated).

NOT a compatible System Card BIOS. NOT an ABI claim. Genuine ABI stays
UNKNOWN/BLOCKED. release_eligible=False, DEV_CALLABLE 0, reference JSR 0/81,
ABI VERIFIED 0/81. Commercial media: ./rom/*.cue auto-detect only; zero cues
-> NOT_RUN. Any cue present -> BLOCKED (no commercial boot path exists; media
never opened, read, or published: status and count only, never bytes/hashes/
paths/traces).
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

# Reuse, never duplicate: chain/refuse payloads, builders, and boot-path judges.
from test_unified_link_realcore import (CHAIN_PAYLOAD_ASM, REFUSE_PAYLOAD_ASM,  # noqa: E402
                                        build_payload, chain_record,
                                        patch_index01_sector, refuse_record)
from geargrafx_experimental_track_contract import (BOOT_ORDER, NOT_AFTER_REJECT,  # noqa: E402
                                                   assert_invalid, assert_success,
                                                   corrupt_selection)

MODE = "debug"
STEP_BUDGET = 12_000_000
BUFFER_BASE = 0x2800
RES_OFF = 0x3100 - BUFFER_BASE
SEQ02 = 0xE006
ENTRY03 = 0xE009
ENTRY21 = 0xE063
ENTRY23 = 0xE069
ENTRY33 = 0xE099
ENTRY4F = 0xE0ED
ENTRY_UNIMPL = 0xE00C  # slot $04: no override, fail-closed diagnostic stub
P_MASK = 0xCF  # PHP pushes B=1/bit5=1; PLP ignores them (same mask as the $33 oracle)

G5_COEXIST_ASM = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
MARK = $3F00
RETVEC = $3F10
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

G5_UNIMPL_ASM = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
MARK = $3F00
RETVEC = $3F10
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
class DevG5ProfileRealCoreTests(unittest.TestCase):
    """Real Geargrafx runs of the G5 DEV profile image (Issue #798)."""

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
        from build_cdload_exec import EXPECTED_MPR, encode_sector
        from build_dev_g5 import G5_ROM_NAME, build_g5_dev_rom
        from build_experimental_bios_boot import build_experimental_bios_boot
        from build_rom import build
        from experimental_track_selection import (selection_from_cue,
                                                  snapshot_media,
                                                  verify_media_identity)
        from geargrafx_api import compile_probe
        from geargrafx_cdread import execute
        import make_cd_image
        cls.EXPECTED_MPR = list(EXPECTED_MPR)
        cls.work = Path(tempfile.mkdtemp(prefix="pce-dev-g5-profile-"))
        cls.addClassCleanup(shutil.rmtree, cls.work, ignore_errors=True)
        normal_path = build(cls.work / "normal", root=ROOT)
        cls.normal = normal_path.read_bytes()
        probe_dir = cls.work / "probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        cls.probe = compile_probe(probe_dir, "geargrafx_cdread_probe.cpp",
                                  "geargrafx-dev-g5-profile")
        cls.cases = {}
        payload_sources = {"chain": CHAIN_PAYLOAD_ASM, "coexist": G5_COEXIST_ASM,
                           "refuse": REFUSE_PAYLOAD_ASM, "unimpl": G5_UNIMPL_ASM}
        for case_name, source in payload_sources.items():
            case_dir = cls.work / f"disc-{case_name}"
            make_cd_image.build_image(case_dir)
            cue = case_dir / make_cd_image.CUE_NAME
            code, pay_syms = build_payload(
                source, cls.work / f"pay-{case_name}", case_name,
                ["-DSELTRACK=1", "-DIDXLO=0", "-DIDXMID=0", "-DIDXHI=0", "-DCRLOW=242"])
            patch_index01_sector(cue, 0, code)
            cue = snapshot_media(cue, cls.work / f"{case_name}-mounted")
            selected = selection_from_cue(cue, cue)
            if selected["lba"] != 0 or selected["track"] != 1:
                raise ValueError(f"selection mismatch: {selected}")
            out = cls.work / f"g5-{case_name}"
            g5_path = build_g5_dev_rom(normal_path, out, "debug", cue, code, pay_syms)
            g5 = g5_path.read_bytes()
            if normal_path.read_bytes() != cls.normal:
                raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
            verify_media_identity(selected["media_identity"], cue)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            embedded = manifest.get("selection_contract", {})
            if embedded.get("lba") != selected["lba"]:
                raise ValueError("BLOCKED: embedded selection LBA != executed medium LBA")
            if embedded.get("media_identity") != selected["media_identity"]:
                raise ValueError("BLOCKED: embedded media identity != executed medium identity")
            if manifest.get("g5_profile", {}).get("profile") != "g5-experimental":
                raise ValueError("BLOCKED: G5 profile section missing from the manifest")
            # NO fake RTS anywhere in the wired path: every wired entry is JMP.
            if g5[SEQ02 - 0xE000] != 0x4C or g5[ENTRY03 - 0xE000] != 0x4C \
                    or g5[ENTRY33 - 0xE000] != 0x4C:
                raise ValueError("BLOCKED: G5 table entry is not JMP (fake RTS?)")
            records = execute(cls.probe, g5_path, cue, out / "trace-labels.txt", STEP_BUDGET)
            cls.cases[case_name] = {"records": records, "manifest": manifest,
                                    "selected": selected, "code": code, "syms": pay_syms,
                                    "rom": g5, "path": g5_path, "cue": cue,
                                    "labels": out / "trace-labels.txt"}
        # badmagic: chain disc with a corrupted sector magic (guest-negative).
        bad = cls.cases["chain"]
        bad_dir = cls.work / "disc-badmagic"
        shutil.copytree(cls.work / "disc-chain", bad_dir)
        from make_cd_image import BIN_NAME, RAW_BYTES
        from build_cdload_exec import USER_OFFSET, SECTOR_BYTES
        raw = bytearray((bad_dir / BIN_NAME).read_bytes())
        raw[0 * RAW_BYTES + USER_OFFSET] = ord("X")  # "PCEX" -> "XCEX"
        (bad_dir / BIN_NAME).write_bytes(bytes(raw))
        bad_cue = bad_dir / make_cd_image.CUE_NAME
        bad_records = execute(cls.probe, bad["path"], bad_cue, bad["labels"], STEP_BUDGET)
        cls.cases["badmagic"] = {"records": bad_records, "manifest": bad["manifest"],
                                 "selected": bad["selected"], "code": bad["code"],
                                 "rom": bad["rom"], "path": bad["path"],
                                 "cue": bad_cue, "labels": bad["labels"]}
        # badsel: chain ROM with a corrupted selection-track byte (guest-negative).
        sel_out = cls.work / "g5-badsel"
        sel_out.mkdir(parents=True, exist_ok=True)
        sel_path = sel_out / G5_ROM_NAME
        sel_path.write_bytes(bad["rom"])
        corrupt_selection(sel_path, bad["manifest"]["trace_symbols"], 7, 0)
        sel_records = execute(cls.probe, sel_path, bad["cue"], bad["labels"], STEP_BUDGET)
        cls.cases["badsel"] = {"records": sel_records, "manifest": bad["manifest"],
                               "selected": bad["selected"], "code": bad["code"],
                               "rom": sel_path.read_bytes(), "path": sel_path,
                               "cue": bad["cue"], "labels": bad["labels"]}
        # The profile wrapper must be byte-neutral: the same inputs through the
        # underlying experimental builder give identical ROM bytes.
        direct_out = cls.work / "direct"
        direct_path = build_experimental_bios_boot(normal_path, direct_out, "debug",
                                                   cls.cases["chain"]["cue"],
                                                   cls.cases["chain"]["code"],
                                                   cls.cases["chain"]["syms"])
        if direct_path.read_bytes() != cls.cases["chain"]["rom"]:
            raise ValueError("BLOCKED: G5 wrapper changed ROM bytes vs the experimental builder")
        cls.evidence = {
            "scope": ("G5 DEV profile real-core run (RESET->IPL->$02/$03/$33 + "
                      "normal DEV $21/$23/$4F coexistence + negatives); NOT an ABI claim"),
            "rom_sha256": bad["manifest"]["rom_sha256"],
            "commercial": cls._commercial_status(),
        }

    @classmethod
    def _commercial_status(cls):
        try:
            import commercial_media as CM
        except ImportError as error:
            return {"status": "BLOCKED", "reason": f"BLOCKED: commercial detector unavailable ({error})"}
        try:
            rom_dir = CM.resolve_rom_dir(ROOT)
        except ValueError as error:
            return {"status": "BLOCKED", "reason": str(error)[:200]}
        try:
            cues = CM.detect_cue_files(rom_dir)
        except ValueError as error:
            message = str(error)
            if message.startswith("NOT_RUN"):
                return {"status": "NOT_RUN",
                        "reason": "./rom carries no CUE; synthetic media only"}
            return {"status": "BLOCKED", "reason": message[:200]}
        if not cues:
            return {"status": "NOT_RUN",
                    "reason": "./rom carries no CUE; synthetic media only"}
        return {"status": "BLOCKED",
                "reason": ("commercial medium present but the G5 image has no "
                           "commercial boot path; synthetic-only runs"),
                "cue_count": len(cues)}

    def check_boot_path(self, case_name):
        """Scenario 1: RESET executes the IPL (never an instant stop loop)."""
        case = self.cases[case_name]
        if case_name == "chain":
            # The chain payload overwrites $2800 with the link transfer, so the
            # shared assert_success (pristine boot sector) does not apply;
            # the boot path itself is identical and checked directly.
            self.check_chain_boot_path(case)
            return
        assert_success(case["records"], case["manifest"]["trace_symbols"],
                       case["code"], case["selected"]["lba"])

    def check_chain_boot_path(self, case):
        """RESET->IPL boot path for the chain payload (its $03 call reuses $2800)."""
        from build_cdload_exec import EXEC_ADDRESS, SECTOR_BYTES
        from cdread_verify import (PHASE_DATA_IN, check_halt_confirmation,
                                     result_block)
        from geargrafx_experimental_track_contract import (
            MARK_OFF, MARKER, assert_boot_path, require_cycles)
        records, manifest, code = case["records"], case["manifest"], case["code"]
        symbols = manifest["trace_symbols"]
        require_cycles(records)
        for run in records["RUN"]:
            cycle = run["cycle"]
            if run["reason"] != "cd_halt_ok" or run["pc"] != symbols["cd_halt_ok"]:
                raise ValueError(f"{cycle}: chain did not complete load/handoff: "
                                 f"{run['reason']} at {run['pc']:#x}, {result_block(run)}")
            check_halt_confirmation(run)
            if run["sp"] != 0xFF:
                raise ValueError(f"{cycle}: stack pointer {run['sp']:#x} at halt")
            block = result_block(run)
            if (block["code"], block["stage"], block["bytes"],
                    block["scsi_status"], block["scsi_message"]) != (0, 16, SECTOR_BYTES, 0, 0):
                raise ValueError(f"{cycle}: chain result block is not a clean success: {block}")
            buffer = bytes.fromhex(run["buffer_hex"])
            if len(buffer) != 6144:
                raise ValueError(f"{cycle}: RAM dump has {len(buffer)} bytes")
            if buffer[EXEC_ADDRESS - BUFFER_BASE:EXEC_ADDRESS - BUFFER_BASE + len(code)] != code:
                raise ValueError(f"{cycle}: code in RAM at {EXEC_ADDRESS:#x} differs")
            mark = buffer[MARK_OFF:MARK_OFF + 7]
            if (mark[0], mark[1], mark[4], mark[5], mark[6]) != MARKER:
                raise ValueError(f"{cycle}: chain handoff marker differs")
            assert_boot_path(records, symbols, cycle)
            reads = [(p["load_sector"], p["load_count"]) for p in records["PHASE"]
                     if p["cycle"] == cycle and p["scsi_phase"] == PHASE_DATA_IN]
            if (case["selected"]["lba"], 1) not in reads:
                raise ValueError(f"{cycle}: boot READ(6) missing from {reads}")

    def test_reset_boots_ipl_cold_and_warm(self):
        for case_name in ("chain", "coexist", "refuse"):
            with self.subTest(case=case_name):
                self.check_boot_path(case_name)

    def test_ipl_calls_proto_link_dspoff(self):
        """Scenarios 2-4: $02 state, $03 READ(6) transfer, $33 return, 4 gates."""
        from build_cdload_exec import encode_sector
        from make_cd_image import sector_payload
        case = self.cases["chain"]
        manifest, selected, code = case["manifest"], case["selected"], case["code"]
        records = case["records"]
        self.check_boot_path("chain")
        expected = sector_payload(selected["lba"] + 1)
        boot = encode_sector(0, code)
        self.assertNotEqual(boot, expected)
        want_lba = (selected["lba"] + 1) & 0xFF
        for run in records["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            rec = chain_record(buf)
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(rec["magic"], 0xAA)
                self.assertEqual(rec["a1"], 0)
                self.assertEqual(rec["p1"] & 0x03, 0x02)
                self.assertEqual(rec["order1"], 0x02)
                self.assertEqual(rec["state"], [0, 0, 0, 1, 0, 0, 0, 0])
                self.assertEqual(rec["a2"], 0)
                self.assertEqual(rec["p2"] & 0x03, 0x02)
                self.assertEqual(rec["sp2"], 0xFF)
                self.assertEqual(rec["order2"], 0x03)
                self.assertEqual(rec["link_res"],
                                 [0, 8, 0, 8, 0, 0, 0, 0, 0, want_lba])
                self.assertEqual(rec["head"], expected[:8])
                self.assertNotEqual(rec["head"], boot[:8])
                self.assertEqual(buf[:2048], expected)
                self.assertEqual(rec["a3"], 0x12)
                self.assertEqual(rec["p3"] & P_MASK, 0x34 & P_MASK & ~0x01)
                self.assertEqual(rec["p3"] & 0x01, 0)
                self.assertEqual(rec["sp3"], 0xFF)
                self.assertEqual(rec["order3"], 0x33)
                self.assertEqual(rec["shadow"], [0xF2 & 0x3F, 0xA5])
                self.assertEqual(rec["mpr01"], [0xFF, 0xF8])
        for cycle in ("cold", "warm"):
            phases = sorted((r["load_sector"], r["load_count"]) for r in records["PHASE"]
                            if r["cycle"] == cycle and r["scsi_phase"] == 4)
            self.assertEqual(phases, [(selected["lba"], 1), (selected["lba"] + 1, 1)])
        recs = [chain_record(bytes.fromhex(r["buffer_hex"])) for r in records["RUN"]]
        self.assertEqual(recs[0], recs[1])
        proof = {
            "arrived_at_callee": all(
                r["state"] == [0, 0, 0, 1, 0, 0, 0, 0]
                and r["link_res"][:4] == [0, 8, 0, 8]
                and r["shadow"] == [0xF2 & 0x3F, 0xA5] for r in recs),
            "post_return_position_observed": all(
                [r["order1"], r["order2"], r["order3"]] == [0x02, 0x03, 0x33]
                for r in recs) and all(
                r["pc"] == manifest["trace_symbols"]["cd_halt_ok"] for r in records["RUN"]),
            "sp_restored": all(r["sp2"] == 0xFF and r["sp3"] == 0xFF for r in recs)
            and all(r["sp"] == 0xFF for r in records["RUN"]),
            "mpr_consistent": all(r["mpr01"] == [0xFF, 0xF8] for r in recs),
            "order_matches_expected": all(
                [r["order1"], r["order2"], r["order3"]] == [0x02, 0x03, 0x33]
                for r in recs),
        }
        self.assertTrue(all(proof.values()), f"G5 chain return proof failed: {proof}")

    def test_coexistence_with_normal_dev_apis(self):
        """Scenario 5: $21/$23/$4F return in the same G5 image (with $02 sanity)."""
        case = self.cases["coexist"]
        manifest = case["manifest"]
        self.check_boot_path("coexist")
        for run in case["records"]["RUN"]:
            rec = coexist_record(bytes.fromhex(run["buffer_hex"]))
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(rec["magic"], 0xAA)
                self.assertEqual(rec["a1"], 0)
                self.assertEqual(rec["p1"] & 0x03, 0x02)
                self.assertEqual(rec["order1"], 0x02)
                self.assertEqual(rec["state"], [0, 0, 0, 1, 0, 0, 0, 0])
                self.assertEqual(rec["order21"], 0x21)
                self.assertEqual(rec["sp"], 0xFF)
                self.assertEqual(rec["a23"], 0x11)  # A preserved through $23
                self.assertEqual(rec["order23"], 0x23)
                self.assertEqual(rec["product"], [0, 0, 0, 6])  # 2 * 3 signed
                self.assertEqual(rec["order4f"], 0x4F)
                self.assertEqual(rec["mpr1"], 0xF8)
                self.assertEqual(run["pc"], manifest["trace_symbols"]["cd_halt_ok"])
                self.assertEqual(run["sp"], 0xFF)
        recs = [coexist_record(bytes.fromhex(r["buffer_hex"])) for r in case["records"]["RUN"]]
        self.assertEqual(recs[0], recs[1])  # cold/warm repeatability

    def test_read_refusals_fail_closed(self):
        """Scenario 6 (CD_READ): INVALID_STATE/OVER_CAP/BEFORE/AFTER, no SCSI."""
        from build_cdload_exec import encode_sector
        case = self.cases["refuse"]
        manifest, selected, code = case["manifest"], case["selected"], case["code"]
        self.check_boot_path("refuse")
        for run in case["records"]["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            rec = refuse_record(buf)
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(rec["magic"], 0xAA)
                self.assertEqual(rec["a_proto"], 0)
                self.assertEqual(rec["p_proto"] & 0x03, 0x02)
                self.assertEqual(rec["a_calls"], [0x60, 0x62, 0x63, 0x64])
                for lr in (rec["lr_cap"], rec["lr_before"], rec["lr_after"]):
                    self.assertEqual(lr[1], 1)
                    self.assertEqual(int.from_bytes(bytes(lr[2:4]), "little"), 0)
                self.assertEqual(rec["sp"], 0xFF)
                self.assertEqual(rec["state"], [0, 0, 0, 1, 0, 0, 0, 0])
                self.assertEqual(buf[:2048], encode_sector(selected["lba"], code))
                phases = [(r["load_sector"], r["load_count"]) for r in case["records"]["PHASE"]
                          if r["cycle"] == run["cycle"] and r["scsi_phase"] == 4]
                self.assertEqual(phases, [(selected["lba"], 1)])
        recs = [refuse_record(bytes.fromhex(r["buffer_hex"])) for r in case["records"]["RUN"]]
        self.assertEqual(recs[0], recs[1])

    def test_unimplemented_slot_traps_fail_closed(self):
        """Scenario 6 (unimplemented API): the stub traps with X=slot, never returns."""
        case = self.cases["unimpl"]
        manifest = case["manifest"]
        labels = manifest["labels"]
        for run in case["records"]["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            with self.subTest(cycle=run["cycle"]):
                # IPL ran (arrival marker) but the call never returned.
                self.assertEqual(buf[RES_OFF], 0xAA)
                self.assertEqual(buf[RES_OFF + 1], 0x00)
                self.assertEqual(run["reason"], "api_unimplemented")
                self.assertTrue(run["halt_confirmed"])
                self.assertEqual(run["pc"], labels["api_unimplemented"])
                self.assertEqual(run["x"], 0x04)
                self.assertEqual(run["sp"], 0xFD)  # JSR pushed, stub never returned

    def test_bad_ipl_format_rejected(self):
        """Scenario 6 (IPL format): bad magic halts before any EXEC copy."""
        from build_cdload_exec import SECTOR_BYTES
        case = self.cases["badmagic"]
        manifest = case["manifest"]
        self.assertEqual([r["cycle"] for r in case["records"]["RUN"]], ["cold", "warm"])
        for run in case["records"]["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(run["reason"], "cd_halt_fail")
                self.assertEqual(run["pc"], manifest["trace_symbols"]["cd_halt_fail"])
                from cdread_verify import check_halt_confirmation, result_block
                check_halt_confirmation(run)
                block = result_block(run)
                self.assertEqual((block["code"], block["stage"]), (20, 11))
                # Nothing copied: the loader sentinel page ($3000-$30FF) intact.
                self.assertEqual(
                    buf[0x3000 - BUFFER_BASE:0x3000 - BUFFER_BASE + 0x100],
                    bytes([0x5A]) * 0x100)
                # The sector was still read exactly once (format checked post-transfer).
                phases = [(r["load_sector"], r["load_count"]) for r in case["records"]["PHASE"]
                          if r["cycle"] == run["cycle"] and r["scsi_phase"] == 4]
                self.assertEqual(phases, [(case["selected"]["lba"], 1)])

    def test_bad_selection_halts_before_any_read(self):
        """Scenario 6 (selection): a corrupted record halts RESET before SCSI."""
        case = self.cases["badsel"]
        assert_invalid(case["records"], case["manifest"]["trace_symbols"])

    def test_one_image_boots_every_disc(self):
        """All success-case ROMs are byte-identical: single G5 image, many IPLs."""
        roms = {name: self.cases[name]["rom"] for name in ("chain", "coexist", "refuse", "unimpl")}
        first = roms["chain"]
        for name, rom in roms.items():
            with self.subTest(case=name):
                self.assertEqual(rom, first)
        self.assertEqual(self.cases["chain"]["manifest"]["rom_sha256"],
                         __import__("hashlib").sha256(first).hexdigest())

    def test_g5_manifest_gates(self):
        case = self.cases["chain"]
        manifest = case["manifest"]
        self.assertEqual(manifest["rom_name"], "dev-g5-experimental-not-compatible.pce")
        self.assertFalse(manifest["release_eligible"])
        self.assertFalse(manifest["dev_callable"])
        self.assertEqual(manifest["system_card_abi"], "NO_CLAIM")
        profile = manifest["g5_profile"]
        self.assertEqual(profile["profile"], "g5-experimental")
        self.assertEqual(profile["normal_dev_rom_sha256"],
                         __import__("hashlib").sha256(self.normal).hexdigest())
        self.assertEqual(profile["experimental_slots"], ["$02", "$03", "$33"])
        self.assertEqual(profile["reference_jsr"], "0/81")
        self.assertEqual(profile["abi_verified"], "0/81")
        self.assertFalse(profile["release_eligible"])

    def test_harness_d_validators_accept_g5_manifest(self):
        import g5_progress_model as M
        case = self.cases["chain"]
        manifest, selected, exp = case["manifest"], case["selected"], case["rom"]
        checked = M.validate_experimental_manifest(manifest)
        self.assertEqual(tuple(checked["slots"]), (0x02, 0x03, 0x33))
        self.assertTrue(checked["native"])
        for ref, sha in manifest.get("merged_heads", {}).items():
            self.assertRegex(sha, r"\A[0-9a-f]{40}\Z",
                             f"merged head {ref} unresolved at integration time")
        M.verify_d_rom_bytes(exp, manifest)
        M.check_embedded_selection_vs_executed(
            manifest, {"lba": selected["lba"], "identity": selected["media_identity"]})
        labels = manifest["labels"]
        self.assertEqual(M.parse_slot_table_target(exp, 0x02), labels["api_slot_02"])
        self.assertEqual(M.parse_slot_table_target(exp, 0x03), labels["cd_read_link_entry"])
        self.assertEqual(M.parse_slot_table_target(exp, 0x33), labels["experimental_slot_33"])
        M.verify_d_rom_slot_targets(exp, manifest)

    def test_harness_d_rejects_g5_tamper(self):
        import g5_progress_model as M
        case = self.cases["chain"]
        manifest, exp = case["manifest"], case["rom"]
        tampered = bytearray(exp)
        tampered[0x03 * 3 + 1] ^= 0xFF  # wrong $E009 dest
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_d_rom_slot_targets(bytes(tampered), manifest)
        manifest2 = copy.deepcopy(manifest)
        manifest2["cd_read_link"].pop("link_entry", None)
        manifest2.get("labels", {}).pop("cd_read_link_entry", None)
        manifest2.get("symbols", {}).pop("cd_read_link_entry", None)
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_d_rom_slot_targets(exp, manifest2)

    def test_commercial_media_is_not_run(self):
        status = self.evidence["commercial"]["status"]
        self.assertIn(status, ("NOT_RUN", "BLOCKED"))
        blob = json.dumps(self.evidence["commercial"])
        self.assertNotIn(".cue", blob)
        self.assertNotIn(".CUE", blob)
        self.assertNotRegex(blob, r"[0-9a-f]{64}")
        self.assertNotRegex(blob, r"[A-Za-z]:\\|/home/|/Users/")
        if status == "NOT_RUN":
            self.assertEqual(self.evidence["commercial"]["reason"],
                             "./rom carries no CUE; synthetic media only")


if __name__ == "__main__":
    unittest.main()
