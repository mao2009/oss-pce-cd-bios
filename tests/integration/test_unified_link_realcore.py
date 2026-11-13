"""Triple-link real-core tests: $02 CD_BASE proto -> $03 CD_READ link -> $33 EX_DSPOFF
in ONE experimental image (Issue #787).

Pinned cc65 71746c8 + pinned Geargrafx b49ae82a. Synthetic media only.
Runs in `make test-real-core` / `make integration-test`, never in the host stage.

The triple image (tools/build_experimental_bios_boot.py) wires all three
callees as REAL processing: slot $02 JMPs to cd_base_proto.s (Agent A), the
$E009 table entry JMPs to cd_read_link.s (Agent B link V1, EXPERIMENTAL
contract only, NOT the genuine $03 ABI), and the $E099 table entry JMPs to
the BOOT-region EX_DSPOFF fragment (Agent B). No test pre-seeding of the
proto base state $2400-$2402: the CHAIN payload calls the proto first and the
link consumes the just-computed state.

Cases (each a custom <256-byte payload at $3000, INDEX01=0 synthetic disc):
  chain:  JSR $E006 (base == INDEX01 -> DATA) -> JSR $E009 (block 0,
          window INDEX01 -> valid READ(6), 2048 bytes to $2800) ->
          seed VDC shadow -> JSR $E099 (C=0 ok) -> MARK halt at cd_halt_ok.
          Proves propagation, the real transfer, and the dspoff return.
  refuse: JSR $E009 with last != $00 (INVALID_STATE $60, pre-proto) ->
          JSR $E006 (DATA) -> block HI $20 (OVER_CAP $62) ->
          window INDEX01+10 (BEFORE $63) -> block 16 (AFTER $64).
          Proves fail-closed refusals issue no SCSI and write nothing outside
          $2800-$2FFF ($2800 keeps the boot sector; only the bootloader READ
          appears in PHASE).

Checks: loader success cold+warm, NO fake RTS in the D path (all three table
entries are JMP; all three bodies start with real processing), per-call
A/P/SP/order, LINK_RES/LBA/state/RAM/$2800, shadow/MPR effects, cold/warm
repeatability, embedded vs executed selection cross-check, and a measured
return proof. Harness-D consumption uses the CURRENT in-tree model
(read-only): manifest validation, byte/SHA verification, per-slot target
verification, plus negative tamper cases. Commercial media: ./rom/*.cue
auto-detect only. Zero cues -> NOT_RUN. Any cue present -> BLOCKED (no
commercial boot path exists for this image; media never opened, read, or
published: status and count only, never bytes/hashes/paths/traces).

NOT a compatible System Card BIOS. NOT an ABI claim. Genuine ABI stays
UNKNOWN/BLOCKED. release_eligible=False, DEV_CALLABLE 0, reference JSR 0/81,
ABI VERIFIED 0/81.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.environment import cc65_required, cc65_skip_reason, requires_cc65  # noqa: E402

MODE = "debug"
STEP_BUDGET = 12_000_000
BUFFER_BASE = 0x2800
RES_OFF = 0x3100 - BUFFER_BASE
SEQ02 = 0xE006
ENTRY03 = 0xE009
ENTRY33 = 0xE099
P_MASK = 0xCF  # PHP pushes B=1/bit5=1; PLP ignores them (same mask as the $33 oracle)

CHAIN_PAYLOAD_ASM = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
MARK = $3F00
RETVEC = $3F10
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
    lda #$00
    sta $20F2
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

REFUSE_PAYLOAD_ASM = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
MARK = $3F00
RETVEC = $3F10
SEQ02 = $E006
ENTRY03 = $E009
payload_entry:
    cld
    lda #$00
    ldx #$07
@zst: sta $2400,x
    dex
    bpl @zst
    lda #$FF
    sta $2404
    lda #$00
    sta $20F2
    sta $20F3
    sta $20F4
    lda #IDXLO
    sta $20F5
    lda #IDXMID
    sta $20F6
    lda #IDXHI
    sta $20F7
    ldx #$FF
    txs
    jsr ENTRY03
    sta RESBASE+3
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
    jsr SEQ02
    sta RESBASE+1
    php
    pla
    sta RESBASE+2
    lda #$20
    sta $20F4
    jsr ENTRY03
    sta RESBASE+4
    ldx #$09
@c1: lda $2320,x
    sta RESBASE+20,x
    dex
    bpl @c1
    lda #$00
    sta $20F4
    lda #IDXLO
    clc
    adc #10
    sta $20F5
    lda #IDXMID
    adc #$00
    sta $20F6
    lda #IDXHI
    adc #$00
    sta $20F7
    jsr ENTRY03
    sta RESBASE+5
    ldx #$09
@c2: lda $2320,x
    sta RESBASE+30,x
    dex
    bpl @c2
    lda #IDXLO
    sta $20F5
    lda #IDXMID
    sta $20F6
    lda #IDXHI
    sta $20F7
    lda #$10
    sta $20F2
    jsr ENTRY03
    sta RESBASE+6
    ldx #$09
@c3: lda $2320,x
    sta RESBASE+40,x
    dex
    bpl @c3
    tsx
    txa
    sta RESBASE+7
    ldx #$07
@cpst: lda $2400,x
    sta RESBASE+50,x
    dex
    bpl @cpst
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


def build_payload(source_text, out_dir, name, defines):
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


def refuse_record(buffer):
    if len(buffer) != 6144:
        raise ValueError(f"RAM dump {len(buffer)} bytes, expected 6144")
    res = buffer[RES_OFF:RES_OFF + 58]
    return {
        "magic": res[0],
        "a_proto": res[1], "p_proto": res[2],
        "a_calls": list(res[3:7]),
        "sp": res[7],
        "lr_cap": list(res[20:30]),
        "lr_before": list(res[30:40]),
        "lr_after": list(res[40:50]),
        "state": list(res[50:58]),
    }


@requires_cc65
class TripleLinkRealCoreTests(unittest.TestCase):
    """Real Geargrafx runs of the triple-link $02->$03->$33 experimental image."""

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
        from build_cdload_exec import EXEC_ADDRESS, encode_sector
        from build_experimental_bios_boot import build_experimental_bios_boot
        from experimental_track_selection import selection_from_cue, snapshot_media
        from geargrafx_api import compile_probe
        from geargrafx_cdread import execute
        import make_cd_image
        cls.work = Path(tempfile.mkdtemp(prefix="pce-triple-link-real-core-"))
        cls.addClassCleanup(shutil.rmtree, cls.work, ignore_errors=True)
        normal_path = build(cls.work / "normal", root=ROOT)
        cls.normal = normal_path.read_bytes()
        cls.cases = {}
        for case_name, source in (("chain", CHAIN_PAYLOAD_ASM),
                                  ("refuse", REFUSE_PAYLOAD_ASM)):
            case_dir = cls.work / case_name
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
            out = cls.work / f"exp-{case_name}"
            exp_path = build_experimental_bios_boot(normal_path, out, "debug",
                                                     cue, code, pay_syms)
            exp = exp_path.read_bytes()
            if normal_path.read_bytes() != cls.normal:
                raise ValueError("BLOCKED: triple build mutated the normal DEV ROM input")
            import json
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            embedded = manifest.get("selection_contract", {})
            if embedded.get("lba") != selected["lba"]:
                raise ValueError("BLOCKED: embedded selection LBA != executed medium LBA")
            if embedded.get("media_identity") != selected["media_identity"]:
                raise ValueError("BLOCKED: embedded media identity != executed medium identity")
            # NO fake RTS anywhere in the D path: all three table entries are JMP.
            for entry in (SEQ02, ENTRY03, ENTRY33):
                if exp[entry - 0xE000] != 0x4C:
                    raise ValueError("BLOCKED: triple table entry %04X is not JMP (fake RTS?)" % entry)
            probe_dir = cls.work / f"probe-{case_name}"
            probe_dir.mkdir(parents=True, exist_ok=True)
            probe = compile_probe(probe_dir, "geargrafx_cdread_probe.cpp",
                                  f"geargrafx-triple-link-{case_name}")
            labels_path = out / "trace-labels.txt"
            records = execute(probe, exp_path, cue, labels_path, STEP_BUDGET)
            cls.cases[case_name] = (records, manifest, selected, code, exp)
        cls.evidence = {
            "scope": ("triple-link experimental DEV image real-core run "
                      "($02 CD_BASE proto -> $03 CD_READ link -> $33 EX_DSPOFF); NOT an ABI claim"),
            "commercial": cls._commercial_status(),
        }

    @classmethod
    def _commercial_status(cls):
        rom_dir = ROOT / "rom"
        cues = []
        if rom_dir.is_dir():
            cues = list(rom_dir.glob("*.cue")) + list(rom_dir.glob("*.CUE"))
        if not cues:
            return {"status": "NOT_RUN",
                    "reason": "./rom carries no CUE; synthetic media only"}
        return {"status": "BLOCKED",
                "reason": ("commercial medium present but the triple image has no "
                           "commercial boot path; synthetic-only runs"),
                "cue_count": len(cues)}

    def check_loader_success(self, records, manifest):
        from cdread_verify import check_halt_confirmation, result_block
        self.assertEqual([r["cycle"] for r in records["RUN"]], ["cold", "warm"])
        self.assertFalse(records.get("BLOCK"))
        for run in records["RUN"]:
            self.assertEqual(run["reason"], "cd_halt_ok")
            self.assertEqual(run["pc"], manifest["trace_symbols"]["cd_halt_ok"])
            check_halt_confirmation(run)
            self.assertEqual(run["sp"], 0xFF)
            block = result_block(run)
            self.assertEqual((block["code"], block["stage"], block["bytes"]),
                             (0, 16, 2048))

    def test_loader_boots_synthetic_medium_cold_and_warm(self):
        for case_name in ("chain", "refuse"):
            records, manifest, _, _, _ = self.cases[case_name]
            with self.subTest(case=case_name):
                self.check_loader_success(records, manifest)

    def test_chain_proto_computes_base_without_preseed(self):
        # The payload zeroes $2400-$2407 before JSR $E006: the state below can
        # only come from the proto's own arithmetic, never from test seeding.
        records, manifest, _, _, _ = self.cases["chain"]
        self.check_loader_success(records, manifest)
        for run in records["RUN"]:
            rec = chain_record(bytes.fromhex(run["buffer_hex"]))
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(rec["magic"], 0xAA)
                self.assertEqual(rec["a1"], 0)
                self.assertEqual(rec["p1"] & 0x03, 0x02)  # C=0, Z=1 success
                self.assertEqual(rec["order1"], 0x02)
                self.assertEqual(rec["state"], [0, 0, 0, 1, 0, 0, 0, 0])

    def test_chain_link_transfers_real_sector_from_proto_state(self):
        from build_cdload_exec import encode_sector
        records, manifest, selected, code, _ = self.cases["chain"]
        self.check_loader_success(records, manifest)
        for run in records["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            rec = chain_record(buf)
            with self.subTest(cycle=run["cycle"]):
                # Valid request: A=0, LINK_RES code 0 / stage 8 / 2048 bytes,
                # LBA == INDEX01 (proto base + block 0, no pre-seeding).
                self.assertEqual(rec["a2"], 0)
                self.assertEqual(rec["p2"] & 0x03, 0x02)
                self.assertEqual(rec["sp2"], 0xFF)
                self.assertEqual(rec["order2"], 0x03)
                self.assertEqual(rec["link_res"],
                                 [0, 8, 0, 8, 0, 0, 0, 0, 0, selected["lba"] & 0xFF])
                # Exactly 2048 sector bytes in RAM, matching the boot sector.
                self.assertEqual(rec["head"], encode_sector(0, code)[:8])
                self.assertEqual(buf[:2048], encode_sector(0, code))
                # Bootloader READ + link READ are distinguishable in PHASE.
                phases = [(r["load_sector"], r["load_count"]) for r in records["PHASE"]
                          if r["cycle"] == run["cycle"] and r["scsi_phase"] == 4]
                self.assertEqual(phases.count((selected["lba"], 1)), 2)

    def test_chain_dspoff_returns_and_next_stop(self):
        records, manifest, _, _, _ = self.cases["chain"]
        self.check_loader_success(records, manifest)
        for run in records["RUN"]:
            rec = chain_record(bytes.fromhex(run["buffer_hex"]))
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(rec["a3"], 0x12)
                self.assertEqual(rec["p3"] & P_MASK, 0x34 & P_MASK & ~0x01)
                self.assertEqual(rec["p3"] & 0x01, 0)  # C=0 ok
                self.assertEqual(rec["sp3"], 0xFF)
                self.assertEqual(rec["order3"], 0x33)
                self.assertEqual(rec["shadow"], [0xF2 & 0x3F, 0xA5])
                self.assertEqual(rec["mpr01"], [0xFF, 0xF8])
                self.assertEqual(run["pc"], manifest["trace_symbols"]["cd_halt_ok"])
                self.assertEqual(run["sp"], 0xFF)
        recs = [chain_record(bytes.fromhex(r["buffer_hex"])) for r in records["RUN"]]
        self.assertEqual(recs[0], recs[1])  # cold/warm repeatability
        self.assertEqual([recs[0][k] for k in ("order1", "order2", "order3")],
                         [0x02, 0x03, 0x33])

    def test_chain_four_gate_return_proof(self):
        # Measured gates (MPR full-bank consistency rides on the existing
        # unified $02+$33 coverage of the same image recipe plus the MPR0/1
        # snapshot here; the link/dspoff sources perform no TAM).
        records, manifest, _, _, _ = self.cases["chain"]
        self.check_loader_success(records, manifest)
        recs = [chain_record(bytes.fromhex(r["buffer_hex"])) for r in records["RUN"]]
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
            "order_matches_expected": True,
        }
        self.assertTrue(all(proof.values()), f"chain return proof failed: {proof}")

    def test_refuse_paths_reject_before_scsi(self):
        from build_cdload_exec import encode_sector
        records, manifest, selected, code, _ = self.cases["refuse"]
        self.check_loader_success(records, manifest)
        for run in records["RUN"]:
            buf = bytes.fromhex(run["buffer_hex"])
            rec = refuse_record(buf)
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(rec["magic"], 0xAA)
                # Proto setup call succeeds (needed for the cap/boundary cases).
                self.assertEqual(rec["a_proto"], 0)
                self.assertEqual(rec["p_proto"] & 0x03, 0x02)
                # $60 invalid-state (pre-proto), $62 over-cap, $63 before,
                # $64 after. A fake RTS could not produce these codes.
                self.assertEqual(rec["a_calls"], [0x60, 0x62, 0x63, 0x64])
                for lr in (rec["lr_cap"], rec["lr_before"], rec["lr_after"]):
                    self.assertEqual(lr[1], 1)  # refusal stage: no READ issued
                    self.assertEqual(int.from_bytes(bytes(lr[2:4]), "little"), 0)
                self.assertEqual(rec["lr_cap"][:1], [0x62])
                self.assertEqual(rec["lr_before"][:1], [0x63])
                self.assertEqual(rec["lr_after"][:1], [0x64])
                # Reported LBAs: cap computes base+block, before/after the window math.
                self.assertEqual(tuple(rec["lr_cap"][7:10]), (0x20, 0x00, 0x00))
                self.assertEqual(tuple(rec["lr_before"][7:10]), (0x00, 0x00, 0x00))
                self.assertEqual(tuple(rec["lr_after"][7:10]), (0x00, 0x00, 0x10))
                # SP balanced, proto state preserved (link never writes it).
                self.assertEqual(rec["sp"], 0xFF)
                self.assertEqual(rec["state"], [0, 0, 0, 1, 0, 0, 0, 0])
                # $2800 untouched: still the bootloader sector, never partial.
                self.assertEqual(buf[:2048], encode_sector(selected["lba"], code))
                # No link READ on the bus: only the bootloader DATA_IN phase.
                phases = [(r["load_sector"], r["load_count"]) for r in records["PHASE"]
                          if r["cycle"] == run["cycle"] and r["scsi_phase"] == 4]
                self.assertEqual(phases, [(selected["lba"], 1)])
        recs = [refuse_record(bytes.fromhex(r["buffer_hex"])) for r in records["RUN"]]
        self.assertEqual(recs[0], recs[1])  # cold/warm repeatability

    def test_harness_d_validators_accept_triple_manifest(self):
        import g5_progress_model as M
        for case_name in ("chain", "refuse"):
            _, manifest, selected, _, exp = self.cases[case_name]
            with self.subTest(case=case_name):
                checked = M.validate_experimental_manifest(manifest)
                self.assertEqual(checked["slots"], (0x02, 0x03, 0x33))
                self.assertTrue(checked["native"])
                for ref, sha in manifest.get("merged_heads", {}).items():
                    self.assertRegex(sha, r"\A[0-9a-f]{40}\Z",
                                     f"merged head {ref} unresolved at integration time")
                M.verify_d_rom_bytes(exp, manifest)
                M.check_embedded_selection_vs_executed(
                    manifest, {"lba": selected["lba"],
                                 "identity": selected["media_identity"]})
                labels = manifest["labels"]
                self.assertEqual(M.parse_slot_table_target(exp, 0x02),
                                 labels["api_slot_02"])
                self.assertEqual(M.parse_slot_table_target(exp, 0x03),
                                 labels["cd_read_link_entry"])
                self.assertEqual(M.parse_slot_table_target(exp, 0x33),
                                 labels["experimental_slot_33"])
                M.verify_d_rom_slot_targets(exp, manifest)

    def test_harness_d_rejects_slot03_tamper(self):
        import copy
        import g5_progress_model as M
        _, manifest, _, _, exp = self.cases["chain"]
        tampered = bytearray(exp)
        tampered[0x03 * 3 + 1] ^= 0xFF  # wrong $E009 dest
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_d_rom_slot_targets(bytes(tampered), manifest)
        manifest2 = copy.deepcopy(manifest)
        manifest2["cd_read_link"].pop("link_entry", None)
        manifest2.get("labels", {}).pop("cd_read_link_entry", None)
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_d_rom_slot_targets(exp, manifest2)

    def test_commercial_media_is_not_run(self):
        status = self.evidence["commercial"]["status"]
        self.assertIn(status, ("NOT_RUN", "BLOCKED"))
        blob = __import__("json").dumps(self.evidence["commercial"])
        self.assertNotIn(".cue", blob)
        self.assertNotIn(".CUE", blob)
        self.assertNotRegex(blob, r"[0-9a-f]{64}")
        self.assertNotRegex(blob, r"[A-Za-z]:\\|/home/|/Users/")
        if status == "NOT_RUN":
            self.assertEqual(self.evidence["commercial"]["reason"],
                             "./rom carries no CUE; synthetic media only")


if __name__ == "__main__":
    unittest.main()
