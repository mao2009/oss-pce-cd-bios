"""Unified real-core tests for the experimental $02+$33 BIOS (G5 Agent A+B union).

Pinned cc65 71746c8 + pinned Geargrafx b49ae82a. Synthetic media only.
Runs in `make test-real-core` / `make integration-test`, never in the host stage.

The union image (tools/build_experimental_bios_boot.py) wires BOTH callees as
REAL processing: slot $02 JMPs to cd_base_proto.s, the $E099 table entry JMPs
to the BOOT-region EX_DSPOFF fragment. A test-generated SEQ payload (assembled
at test time with the pinned ca65; no new repo fixtures) runs at $3000 after
the experimental loader boots synthetic media, then:

  JSR $E006 (proto, base == INDEX01) -> A=0, state updated, returns;
  seed VDC shadow + program CR -> JSR $E099 (dspoff) -> C=0, shadow updated,
  returns; order markers prove 02-then-33; JMP (RETVEC) halts at cd_halt_ok.

Checks: real-callee arrival + real return for BOTH slots, NO fake RTS anywhere
in the D path (both table entries are JMP; both bodies start with real
processing), SP/MPR/register integrity, VDC shadow side effects, API call
order, cold/warm repeatability, next real stop position (halt PC), embedded
vs executed selection cross-check (mismatch -> failure), and a test-local
5-gate REAL_SERVICE_RETURN proof mirroring harness-D semantics
(arrived/post-return/SP/MPR/order). The authoritative VDC CR write for $33
stays covered by tests/integration/test_ex_dspoff_real_core.py (debugger
oracle); this file asserts the shadow/VDC-programming side effects visible in
the cd-flow buffer.

Harness-D consumption: when the pinned harness ref is materializable, the
pure validators from tools/g5_progress_model.py at that ref
(validate_experimental_manifest, verify_d_rom_bytes,
check_embedded_selection_vs_executed, derive_native_slots) run read-only
against this build's manifest/ROM/medium. Otherwise that subtest SKIPs with
its reason; it never substitutes a pass.

Commercial media: ./rom/*.cue auto-detect only. Zero cues -> NOT_RUN. Any cue
present -> BLOCKED (no commercial boot path exists; media never opened, read,
or published: status and count only, never bytes/hashes/paths/traces).

NOT a compatible System Card BIOS. NOT an ABI claim. Genuine ABI stays
UNKNOWN/BLOCKED. release_eligible=False, DEV_CALLABLE 0, reference JSR 0/81,
ABI VERIFIED 0/81.
"""
import importlib.util
import json
import shutil
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
ENTRY33 = 0xE099
HARNESS_REF = "15ae4872"  # pinned PR #778 head; read-only D-consumption only
P_MASK = 0xCF  # PHP pushes B=1/bit5=1; PLP ignores them (same mask as the $33 oracle)

SEQ_PAYLOAD_ASM = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
RETVEC = $3F10
SEQ02 = $E006
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
    tsx
    txa
    sta RESBASE+3
    lda #$02
    sta RESBASE+4
    ldx #$07
@cpst: lda $2400,x
    sta RESBASE+8,x
    dex
    bpl @cpst
    lda #CRLOW
    sta $2502
    lda #$A5
    sta $2503
    sei
    tma #$01
    sta RESBASE+35
    lda #$FF
    tam #$01
    lda #$05
    sta a:$0000
    lda #CRLOW
    sta a:$0002
    lda #CRHIGH
    sta a:$0003
    lda RESBASE+35
    tam #$01
    lda #$34
    pha
    plp
    lda #$12
    ldx #$34
    ldy #$56
    jsr ENTRY33
    sta RESBASE+5
    php
    pla
    sta RESBASE+6
    tsx
    txa
    sta RESBASE+7
    lda #$33
    sta RESBASE+16
    lda $2502
    sta RESBASE+17
    lda $2503
    sta RESBASE+18
    tma #$01
    sta RESBASE+19
    tma #$02
    sta RESBASE+20
    tma #$04
    sta RESBASE+21
    tma #$08
    sta RESBASE+22
    tma #$10
    sta RESBASE+23
    tma #$20
    sta RESBASE+24
    tma #$40
    sta RESBASE+25
    tma #$80
    sta RESBASE+26
    ldx #$07
@cpin: lda $20F8,x
    sta RESBASE+27,x
    dex
    bpl @cpin
    lda #$AA
    sta RESBASE
    lda #$A5
    sta $3F00
    lda #$5A
    sta $3F01
payload_done:
    jmp (RETVEC)
"""


def build_seq_payload(out_dir, idx_lo, idx_mid, idx_hi, track, cr_low, cr_high):
    """Assemble the test-owned SEQ payload with the pinned ca65 (no repo fixture)."""
    from environment import executable
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = out_dir / "unified_seq_payload.s"
    src.write_text(SEQ_PAYLOAD_ASM, encoding="ascii")
    cfg = out_dir / "seq.cfg"
    cfg.write_text("MEMORY { RAM: start = $3000, size = $0800, file = %O; }\n"
                   "SEGMENTS { PAYLOAD: load = RAM, type = ro, start = $3000; }\n",
                   encoding="ascii")
    obj, bin_path, lbl = out_dir / "seq.o", out_dir / "seq.bin", out_dir / "seq.lbl"
    defines = [f"-DSELTRACK={track}", f"-DIDXLO={idx_lo}", f"-DIDXMID={idx_mid}",
               f"-DIDXHI={idx_hi}", f"-DCRLOW={cr_low}", f"-DCRHIGH={cr_high}"]
    subprocess.run([executable("ca65"), str(src), *defines, "-o", str(obj), "-g"], check=True)
    subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(bin_path),
                    "-Ln", str(lbl), str(obj)], check=True)
    code = bin_path.read_bytes()
    if not 1 <= len(code) <= 255:
        raise ValueError(f"SEQ payload {len(code)} bytes outside 1..255")
    import re
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$",
                                    lbl.read_text(encoding="utf-8"), re.M):
        if name in ("payload_entry", "payload_done"):
            found.setdefault(name, int(address, 16))
    if set(found) != {"payload_entry", "payload_done"}:
        raise ValueError("SEQ payload labels missing")
    from build_cdload_exec import EXEC_ADDRESS
    if found["payload_entry"] != EXEC_ADDRESS:
        raise ValueError(f"SEQ payload_entry != ${EXEC_ADDRESS:04X}")
    return code, found


def seq_record(buffer):
    if len(buffer) != 6144:
        raise ValueError(f"RAM dump {len(buffer)} bytes, expected 6144")
    res = buffer[RES_OFF:RES_OFF + 36]
    return {
        "magic": res[0],
        "a1": res[1], "p1": res[2], "sp1": res[3], "order1": res[4],
        "a2": res[5], "p2": res[6], "sp2": res[7],
        "state": list(res[8:16]),
        "order2": res[16],
        "shadow": list(res[17:19]),
        "mpr": list(res[19:27]),
        "inputs": list(res[27:35]),
        "caller_mpr0": res[35],
    }


@requires_cc65
class UnifiedRealCoreTests(unittest.TestCase):
    """Real Geargrafx runs of the unified $02+$33 experimental image."""

    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux"):
            raise unittest.SkipTest("real-core needs Linux-pinned Geargrafx")
        reason = cc65_skip_reason()
        if reason is not None:
            if cc65_required():
                raise AssertionError(f"REQUIRED tool missing: {reason}")
            raise unittest.SkipTest(f"SKIP cc65-dependent: {reason}")
        from build_rom import build
        from build_experimental_bios_boot import build_experimental_bios_boot
        from build_cdload_exec import EXEC_ADDRESS, EXPECTED_MPR, encode_sector
        from experimental_track_selection import selection_from_cue, snapshot_media
        from geargrafx_api import compile_probe
        from geargrafx_cdread import execute
        import make_cd_image
        cls.EXPECTED_MPR = list(EXPECTED_MPR)
        cls.work = Path(tempfile.mkdtemp(prefix="pce-unified-real-core-"))
        cls.addClassCleanup(shutil.rmtree, cls.work, ignore_errors=True)
        normal_path = build(cls.work / "normal", root=ROOT)
        cls.normal = normal_path.read_bytes()
        single_dir = cls.work / "single"
        make_cd_image.build_image(single_dir)
        cue = single_dir / make_cd_image.CUE_NAME
        from make_cd_image import RAW_BYTES
        from build_cdload_exec import USER_OFFSET
        code, pay_syms = build_seq_payload(cls.work / "seq", 0, 0, 0, 1, 0xF2, 0x00)
        cls.payload_code = code
        raw = bytearray((cue.parent / make_cd_image.BIN_NAME).read_bytes())
        raw[0 * RAW_BYTES + USER_OFFSET:0 * RAW_BYTES + USER_OFFSET + 2048] = \
            encode_sector(0, code)
        (cue.parent / make_cd_image.BIN_NAME).write_bytes(bytes(raw))
        cue = snapshot_media(cue, cls.work / "mounted")
        cls.cue = cue
        cls.selected = selection_from_cue(cue, cue)
        if cls.selected["lba"] != 0 or cls.selected["track"] != 1:
            raise ValueError(f"selection mismatch: {cls.selected}")
        out = cls.work / "exp"
        cls.exp_path = build_experimental_bios_boot(normal_path, out, "debug",
                                                     cue, code, pay_syms)
        cls.exp = cls.exp_path.read_bytes()
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        cls.manifest = manifest
        cls.labels_path = out / "trace-labels.txt"
        # Embedded vs executed selection cross-check: mismatch -> BLOCKED-style failure.
        embedded = manifest.get("selection_contract", {})
        if embedded.get("lba") != cls.selected["lba"]:
            raise ValueError("BLOCKED: embedded selection LBA != executed medium LBA")
        if embedded.get("media_identity") != cls.selected["media_identity"]:
            raise ValueError("BLOCKED: embedded media identity != executed medium identity")
        # NO fake RTS anywhere in the D path: both table entries are JMP.
        if cls.exp[SEQ02 - 0xE000] != 0x4C or cls.exp[ENTRY33 - 0xE000] != 0x4C:
            raise ValueError("BLOCKED: unified table entries are not JMP (fake RTS?)")
        probe_dir = cls.work / "probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        cls.probe = compile_probe(probe_dir, "geargrafx_cdread_probe.cpp",
                                  "geargrafx-unified-real-core")
        cls.records = execute(cls.probe, cls.exp_path, cue, cls.labels_path, STEP_BUDGET)
        cls.evidence = {
            "scope": ("unified experimental DEV image real-core run "
                      "($02 CD_BASE proto + $33 EX_DSPOFF fragment); NOT an ABI claim"),
            "rom_sha256": manifest["rom_sha256"],
            "slots": ["$02", "$33"],
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
                "reason": "commercial medium present but the union image has no "
                          "commercial boot path; synthetic-only runs",
                "cue_count": len(cues)}

    def check_loader_success(self):
        from cdread_verify import check_halt_confirmation, result_block
        from build_cdload_exec import SECTOR_BYTES, encode_sector
        manifest = self.manifest
        self.assertEqual([r["cycle"] for r in self.records["RUN"]], ["cold", "warm"])
        self.assertFalse(self.records.get("BLOCK"))
        for run in self.records["RUN"]:
            self.assertEqual(run["reason"], "cd_halt_ok")
            self.assertEqual(run["pc"], manifest["trace_symbols"]["cd_halt_ok"])
            check_halt_confirmation(run)
            self.assertEqual(run["sp"], 0xFF)
            block = result_block(run)
            self.assertEqual((block["code"], block["stage"], block["bytes"]),
                             (0, 16, SECTOR_BYTES))
        # First sector intact (results live at $3100, outside $2800-$2FFF).
        for run in self.records["RUN"]:
            buffer = bytes.fromhex(run["buffer_hex"])
            self.assertEqual(buffer[:SECTOR_BYTES], encode_sector(0, self.payload_code))

    def seq_records(self):
        self.check_loader_success()
        recs = [seq_record(bytes.fromhex(run["buffer_hex"])) for run in self.records["RUN"]]
        for rec in recs:
            self.assertEqual(rec["magic"], 0xAA)
        self.assertEqual(recs[0], recs[1])  # cold/warm repeatability
        return recs[0]

    def test_loader_boots_synthetic_medium_cold_and_warm(self):
        self.check_loader_success()

    def test_slot02_real_arrival_and_return(self):
        rec = self.seq_records()
        # Real arrival: only the proto body writes base/generation/last/offset.
        self.assertEqual(rec["a1"], 0)
        self.assertEqual(rec["p1"] & 0x03, 0x02)  # C=0, Z=1 success; other bits are caller P
        self.assertEqual(rec["sp1"], 0xFF)
        self.assertEqual(rec["state"], [0, 0, 0, 1, 0, 0, 0, 0])
        self.assertEqual(rec["inputs"], [1, 0, 0, 0x80, 0, 0, 0, 0])
        # A fake RTS could not produce the state update above.

    def test_slot33_real_arrival_and_return(self):
        rec = self.seq_records()
        # Real arrival: only the fragment writes the shadow update + C=0 ok path.
        self.assertEqual(rec["a2"], 0x12)
        self.assertEqual(rec["p2"] & P_MASK, 0x34 & P_MASK & ~0x01)
        self.assertEqual(rec["p2"] & 0x01, 0)  # C=0 ok (C=1 would be the reject path)
        self.assertEqual(rec["sp2"], 0xFF)
        self.assertEqual(rec["shadow"], [0xF2 & 0x3F, 0xA5])
        self.assertEqual(rec["mpr"], self.EXPECTED_MPR)
        # A fake RTS could not program the shadow or preserve A/X/Y/P like this.
        self.assertEqual(self.manifest["experimental_slot_33"]["table_entry"], "0xE099")

    def test_api_call_order_and_next_stop(self):
        rec = self.seq_records()
        self.assertEqual([rec["order1"], rec["order2"]], [0x02, 0x33])
        for run in self.records["RUN"]:
            self.assertEqual(run["pc"], self.manifest["trace_symbols"]["cd_halt_ok"])
            self.assertEqual(run["sp"], 0xFF)

    def test_five_gate_real_service_proof(self):
        rec = self.seq_records()
        proof = {
            "arrived_at_callee": (rec["state"] == [0, 0, 0, 1, 0, 0, 0, 0]
                                  and rec["shadow"] == [0xF2 & 0x3F, 0xA5]),
            "post_return_position_observed": (
                [rec["order1"], rec["order2"]] == [0x02, 0x33]
                and all(run["pc"] == self.manifest["trace_symbols"]["cd_halt_ok"]
                        for run in self.records["RUN"])),
            "sp_restored": (rec["sp1"] == 0xFF and rec["sp2"] == 0xFF
                             and all(run["sp"] == 0xFF for run in self.records["RUN"])),
            "mpr_consistent": (rec["mpr"] == self.EXPECTED_MPR),
            "order_matches_expected": ([rec["order1"], rec["order2"]] == [0x02, 0x33]),
        }
        self.assertTrue(all(proof.values()), f"5-gate proof failed: {proof}")
        self.assertEqual(self._classify(proof), "REAL_SERVICE_RETURN")

    @staticmethod
    def _classify(proof):
        # Test-local mirror of harness-D semantics: all five gates True (bool)
        # -> REAL_SERVICE_RETURN. Unmeasured/missing/False -> not classified.
        # This names the same verdict; it is NOT a harness-D run by itself.
        if isinstance(proof, dict) and all(proof.get(k) is True for k in
                                           ("arrived_at_callee",
                                            "post_return_position_observed",
                                            "sp_restored", "mpr_consistent",
                                            "order_matches_expected")):
            return "REAL_SERVICE_RETURN"
        return "UNKNOWN"

    def test_harness_d_validators_accept_manifest(self):
        model_path = self._materialize_harness_model()
        if model_path is None:
            raise unittest.SkipTest("harness ref not materializable; pure-D validation unmeasured")
        spec = importlib.util.spec_from_file_location("g5_progress_model_pinned", model_path)
        model = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(model)
        checked = model.validate_experimental_manifest(self.manifest)
        self.assertEqual(tuple(checked["slots"]), (0x02, 0x33))
        self.assertTrue(checked["native"])
        self.assertEqual(model.derive_native_slots(self.manifest), (0x02, 0x33))
        model.verify_d_rom_bytes(self.exp, self.manifest)
        model.check_embedded_selection_vs_executed(
            self.manifest, {"lba": self.selected["lba"], "identity": None})

    def _materialize_harness_model(self):
        dest_dir = self.work / "harness-ref"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "g5_progress_model.py"
        if not dest.exists():
            try:
                out = subprocess.run(
                    ["git", "-C", str(ROOT), "show",
                     f"{HARNESS_REF}:tools/g5_progress_model.py"],
                    capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                return None
            if out.returncode != 0:
                return None
            dest.write_bytes(out.stdout)
        return dest

    def test_commercial_media_is_not_run(self):
        status = self.evidence["commercial"]["status"]
        self.assertIn(status, ("NOT_RUN", "BLOCKED"))
        blob = json.dumps(self.evidence["commercial"])
        # Sanitized: no filenames, hashes, images, traces, or absolute paths.
        self.assertNotIn(".cue", blob)
        self.assertNotIn(".CUE", blob)
        self.assertNotRegex(blob, r"[0-9a-f]{64}")
        self.assertNotRegex(blob, r"[A-Za-z]:\\|/home/|/Users/")
        if status == "NOT_RUN":
            self.assertEqual(self.evidence["commercial"]["reason"],
                             "./rom carries no CUE; synthetic media only")


if __name__ == "__main__":
    unittest.main()
