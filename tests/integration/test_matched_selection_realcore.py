"""Matched-selection A/B/C/D real-core comparison (Issue #789).

Same synthetic CUE/BIN, track, INDEX01 LBA, payload bytes, selection record,
cold/warm conditions, and execution budget for all four variants:

  A: normal PUBLIC loader (all stubs non-returning).
  B: $02 fake RTS (PR #757 pattern).
  C: $02+$33 fake RTS (PR #759 pattern).
  D: $02 CD_BASE proto + $03 CD_READ link + $33 EX_DSPOFF real processing
     (triple-link #788, EXPERIMENTAL contract only, NOT genuine ABI).

Unified synthetic disc (SEL=0x109=265, track 1) carries BOTH encodings of the
same unified payload (225B, $3000, 1..255B for D):
  frame 265 (INDEX01): encode_sector(265, code) for D XSEL boot;
  frames 267/266: body_for(code)/info_sector for A/B/C IPL boot.
Both boot flows hand off the same code bytes to $3000. Selection
(track/LBA/identity) is derived once from the exact mounted CUE and embedded
in D; A/B/C embed the same SEL at build time. Mismatch on any side is
BLOCKED, never a silent pass.

Unified payload order is $02->$33->$03 (SEQ23 order) with full D $03
experimental inputs (base/block/window/MPR1=$F8/work-RAM stack/IRQs masked):
  A halts at $02 (X=2, 1 API); B returns $02 (fake) then halts at $33 (X=51,
  2 APIs); C returns $02+$33 (fake) then halts at $03 (X=3, 3 APIs);
  D returns all three real (state/LINK_RES/$2800 transfer/shadow) then halts
  clean. Fake vs real is distinguished by callee-exclusive side effects
  (proto state $2400, LINK_RES+$2800 transfer, dspoff shadow), never by
  manifest declaration or entry arrival alone.

Pinned cc65 71746c8 + pinned Geargrafx b49ae82a. Synthetic only. Runs in
`make test-real-core` / `make integration-test`, never in the host stage.
Commercial media: opt-in only via tools/commercial_media (worktree fallback,
recursive, symlink/traversal BLOCKED, sanitized count-only); no CUE ->
NOT_RUN; no commercial boot path -> BLOCKED (never opened/read/executed).

NOT a compatible System Card BIOS. NOT an ABI claim. Genuine ABI stays
UNKNOWN/BLOCKED. release_eligible=False, DEV_CALLABLE 0, reference JSR 0/81,
ABI VERIFIED 0/81.
"""
import copy
import hashlib
import re
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
SEL = 0x109
TRACK = 1
STEP_BUDGET = 12_000_000
BUFFER_BASE = 0x2800
RES_OFF = 0x3100 - BUFFER_BASE

# Unified payload: $02->$33->$03 with D $03 experimental inputs.
# Base = INDEX01 (265 = $0109: LO=$09, MID=$01, HI=$00) for DATA;
# block = 3 -> LBA 268 (sector_payload data, not IPL info/body);
# window = INDEX01. MPR1=$F8/stack/SEI inherited from bootloader (plus
# explicit TXS/SEI here, no TAM/CLI). 225B, EXEC $3000, 1..255B for D.
UNIFIED_PAYLOAD_ASM = r"""
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
    lda #$01
    sta $20F8
    lda #$00
    sta $20F9
    sta $20FA
    sta $20FF
    lda #$80
    sta $20FB
    lda #$09
    sta $20FC
    lda #$01
    sta $20FD
    lda #$00
    sta $20FE
    ldx #$FF
    txs
    sei
    jsr SEQ02
    sta $3101
    php
    pla
    sta $3102
    lda #$02
    sta $3103
    ldx #$07
@cpst: lda $2400,x
    sta $3104,x
    dex
    bpl @cpst
    lda #$42
    sta $2502
    lda #$A5
    sta $2503
    jsr ENTRY33
    sta $3110
    php
    pla
    sta $3111
    lda #$33
    sta $3112
    lda $2502
    sta $3113
    lda $2503
    sta $3114
    lda #$03
    sta $20F2
    lda #$00
    sta $20F3
    sta $20F4
    lda #$09
    sta $20F5
    lda #$01
    sta $20F6
    lda #$00
    sta $20F7
    jsr ENTRY03
    sta $3120
    php
    pla
    sta $3121
    tsx
    txa
    sta $3122
    lda #$03
    sta $3123
    ldx #$09
@cplr: lda $2320,x
    sta $3124,x
    dex
    bpl @cplr
    ldx #$07
@hd: lda $2800,x
    sta $312E,x
    dex
    bpl @hd
    lda #$AA
    sta $3100
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


def build_unified_payload(out_dir, name="unified"):
    from environment import executable
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = out_dir / f"{name}.s"
    src.write_text(UNIFIED_PAYLOAD_ASM, encoding="ascii")
    obj = out_dir / f"{name}.o"
    bin_path = out_dir / f"{name}.bin"
    lbl = out_dir / f"{name}.lbl"
    cfg = out_dir / f"{name}.cfg"
    cfg.write_text("MEMORY { RAM: start = $3000, size = $0800, file = %O; }\n"
                   "SEGMENTS { PAYLOAD: load = RAM, type = ro, start = $3000; }\n",
                   encoding="ascii")
    subprocess.run([executable("ca65"), str(src), "-o", str(obj), "-g"], check=True)
    subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(bin_path),
                    "-Ln", str(lbl), str(obj)], check=True)
    code = bin_path.read_bytes()
    if not 1 <= len(code) <= 255:
        raise ValueError(f"unified payload {len(code)} bytes outside 1..255")
    text = lbl.read_text(encoding="utf-8")
    found = {}
    for address, label in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", text, re.M):
        if label in ("payload_entry", "payload_done"):
            found.setdefault(label, int(address, 16))
    if set(found) != {"payload_entry", "payload_done"}:
        raise ValueError("unified payload labels missing")
    from build_cdload_exec import EXEC_ADDRESS
    if found["payload_entry"] != EXEC_ADDRESS:
        raise ValueError(f"unified payload_entry != ${EXEC_ADDRESS:04X}")
    return code, found


def build_unified_disc(work_dir, code):
    """Same CUE for A/B/C IPL boot and D XSEL boot (pure build, no core)."""
    import geargrafx_ipl_loader as L
    from make_cd_image import BIN_NAME
    from build_cdload_exec import encode_sector, USER_OFFSET, SECTOR_BYTES
    from make_cd_image import RAW_BYTES
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    body = L.body_for(code, 1)
    info = L.info_sector(2, 1, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0, sum(body) & 0xFFFF)
    frames = L.case_disc({"n": 1, "iplblk": 2}, body, info)
    cue = L.make_disc(work_dir, frames)
    sector = encode_sector(SEL, code)
    bin_path = work_dir / BIN_NAME
    raw = bytearray(bin_path.read_bytes())
    at = SEL * RAW_BYTES + USER_OFFSET
    raw[at:at + SECTOR_BYTES] = sector
    bin_path.write_bytes(raw)
    return cue


def unified_record(buffer):
    if len(buffer) != 6144:
        raise ValueError(f"RAM dump {len(buffer)} bytes, expected 6144")
    res = buffer[RES_OFF:RES_OFF + 60]
    return {
        "magic": res[0],
        "a1": res[1], "p1": res[2], "order1": res[3],
        "state": list(res[4:12]),
        "a33": res[0x10], "p33": res[0x11], "order33": res[0x12],
        "shadow": list(res[0x13:0x15]),
        "a3": res[0x20], "p3": res[0x21], "sp3": res[0x22], "order3": res[0x23],
        "link_res": list(res[0x24:0x2E]),
        "head": bytes(res[0x2E:0x36]),
    }


@requires_cc65
class MatchedSelectionRealCoreTests(unittest.TestCase):
    """A/B/C/D on the same synthetic medium, cold/warm, same budget."""

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
        from build_cdload_exec import encode_sector
        from build_dev_ipl_loader_bios import build_ipl_loader_bios, parse_labels as parse_ipl
        from build_slot02_return_loader import build_slot02_return_bios
        from build_experimental_bios_boot import build_experimental_bios_boot
        from experimental_track_selection import selection_from_cue, snapshot_media
        from geargrafx_api import compile_probe
        from geargrafx_cdread import execute
        from make_cd_image import sector_payload
        cls.work = Path(tempfile.mkdtemp(prefix="pce-matched-selection-"))
        cls.addClassCleanup(shutil.rmtree, cls.work, ignore_errors=True)
        normal_path = build(cls.work / "normal", root=ROOT)
        cls.normal = normal_path.read_bytes()
        code, pay_syms = build_unified_payload(cls.work / "pay-unified")
        disc_dir = cls.work / "unified-disc"
        cue = build_unified_disc(disc_dir, code)
        cue = snapshot_media(cue, cls.work / "unified-mounted")
        selected = selection_from_cue(cue, cue)
        if selected["lba"] != SEL or selected["track"] != TRACK:
            raise ValueError(f"unified selection mismatch: {selected}")
        # D ROM (experimental XSEL boot, same cue/code).
        out_d = cls.work / "exp-unified"
        exp_path = build_experimental_bios_boot(normal_path, out_d, "debug",
                                                 cue, code, pay_syms)
        exp = exp_path.read_bytes()
        if normal_path.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: unified build mutated the normal DEV ROM input")
        import json
        manifest = json.loads((out_d / "manifest.json").read_text(encoding="utf-8"))
        embedded = manifest.get("selection_contract", {})
        if embedded.get("lba") != selected["lba"]:
            raise ValueError("BLOCKED: D embedded selection LBA != executed medium LBA")
        if embedded.get("media_identity") != selected["media_identity"]:
            raise ValueError("BLOCKED: D embedded media identity != executed medium identity")
        # A/B/C ROMs (IPL boot, same SEL, same disc).
        defines = ["-D", "IPL_PUBLIC"]
        out_abc = cls.work / "abc-unified"
        rom_a, lab_a_full = build_ipl_loader_bios(normal_path, out_abc, SEL, "debug",
                                                   "g5a-unified", defines, private_input=False)
        _, exp_b, _ = build_slot02_return_bios(normal_path, out_abc, SEL, "debug",
                                                "g5b-unified", defines, private_input=False,
                                                slots=(0x02,), issue=756)
        _, exp_c, _ = build_slot02_return_bios(normal_path, out_abc, SEL, "debug",
                                                "g5c-unified", defines, private_input=False,
                                                slots=(0x02, 0x33), issue=758)
        # Mechanically verify matched inputs (same CUE/BIN/track/LBA/payload/selection).
        import g5_progress_model as M
        abc_sels = [{"variant": "A", "lba": SEL, "track": TRACK},
                    {"variant": "B", "lba": SEL, "track": TRACK},
                    {"variant": "C", "lba": SEL, "track": TRACK}]
        M.verify_matched_selection(d_manifest=manifest, abc_sels=abc_sels,
                                   unified_code=code,
                                   executed={"lba": selected["lba"], "track": selected["track"],
                                             "identity": selected["media_identity"]})
        # On-disc payload in BOTH encodings equals the same code bytes.
        from cue_analysis import analyze, user_sector
        from make_cd_image import RAW_BYTES
        from build_cdload_exec import USER_OFFSET
        import geargrafx_ipl_loader as L
        if user_sector(cue, analyze(cue), SEL) != encode_sector(SEL, code):
            raise ValueError("BLOCKED: unified INDEX01 sector != encode_sector(SEL, code)")
        raw = (cue.parent / "oss-pce-cd-test.bin").read_bytes()
        if raw[(SEL + 2) * RAW_BYTES + USER_OFFSET:(SEL + 2) * RAW_BYTES + USER_OFFSET + 2048] != L.body_for(code, 1)[:2048]:
            raise ValueError("BLOCKED: unified IPL body != body_for(code)")
        # Probe (single cdread probe for all four, same budget).
        probe_dir = cls.work / "probe-unified"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = compile_probe(probe_dir, "geargrafx_cdread_probe.cpp",
                              "geargrafx-matched-unified")
        # Labels: D uses trace-labels (clean halt via RETVEC); A/B/C use ROM
        # stub labels + payload_done as unreached cd_halt_ok.
        d_labels = out_d / "trace-labels.txt"
        pay_done = pay_syms["payload_done"]
        cls.cases = {}
        roms = {"A": (rom_a, lab_a_full), "B": (exp_b, None), "C": (exp_c, None), "D": (exp_path, None)}
        # Resolve B/C full labels (base files carry halt labels).
        lab_b = parse_ipl(out_abc / "ipl-loader-bios-g5b-unified-base.lbl",
                          ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        lab_c = parse_ipl(out_abc / "ipl-loader-bios-g5c-unified-base.lbl",
                          ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        label_map = {"A": lab_a_full, "B": lab_b, "C": lab_c}
        for variant in ("A", "B", "C"):
            rom, _ = roms[variant]
            lab = label_map[variant]
            lp = cls.work / f"labels-{variant.lower()}.execute"
            lp.write_text(f"cd_halt_ok {pay_done:04x}\n"
                          f"cd_halt_fail {lab['cd_halt_fail']:04x}\n"
                          f"cd_unexpected_interrupt {lab['cd_unexpected_interrupt']:04x}\n"
                          f"api_unimplemented {lab['api_unimplemented']:04x}\n",
                          encoding="ascii")
            records = execute(probe, rom, cue, lp, STEP_BUDGET)
            cls.cases[variant] = (records, lab)
        records_d = execute(probe, exp_path, cue, d_labels, STEP_BUDGET)
        cls.cases["D"] = (records_d, manifest)
        cls.probe = probe
        cls.cue = cue
        cls.selected = selected
        cls.code = code
        cls.manifest_d = manifest
        cls.exp_d = exp
        cls.evidence = {
            "scope": ("matched-selection A/B/C/D on one synthetic medium "
                      "(SEL=0x109 track=1, unified $02->$33->$03 payload); NOT an ABI claim"),
            "commercial": cls._commercial_status(),
        }

    @classmethod
    def _commercial_status(cls):
        try:
            import commercial_media as CM
        except ImportError as e:
            return {"status": "BLOCKED", "reason": f"BLOCKED: commercial detector unavailable ({e})"}
        try:
            rom_dir = CM.resolve_rom_dir(ROOT)
        except ValueError as e:
            return {"status": "BLOCKED", "reason": str(e)[:200]}
        try:
            cues = CM.detect_cue_files(rom_dir)
        except ValueError as e:
            msg = str(e)
            if msg.startswith("NOT_RUN"):
                return {"status": "NOT_RUN",
                        "reason": "./rom carries no CUE; synthetic media only"}
            return {"status": "BLOCKED", "reason": msg[:200]}
        if not cues:
            return {"status": "NOT_RUN",
                    "reason": "./rom carries no CUE; synthetic media only"}
        return {"status": "BLOCKED",
                "reason": ("commercial medium present but the matched-selection image has no "
                           "commercial boot path; synthetic-only runs"),
                "cue_count": len(cues)}

    def test_selection_is_matched(self):
        import g5_progress_model as M
        abc_sels = [{"variant": "A", "lba": SEL, "track": TRACK},
                    {"variant": "B", "lba": SEL, "track": TRACK},
                    {"variant": "C", "lba": SEL, "track": TRACK}]
        M.verify_matched_selection(d_manifest=self.manifest_d, abc_sels=abc_sels,
                                   unified_code=self.code,
                                   executed={"lba": self.selected["lba"],
                                             "track": self.selected["track"],
                                             "identity": self.selected["media_identity"]})
        # Payload on disc in both encodings equals the same code.
        from cue_analysis import analyze, user_sector
        from build_cdload_exec import encode_sector, USER_OFFSET
        from make_cd_image import RAW_BYTES
        import geargrafx_ipl_loader as L
        self.assertEqual(user_sector(self.cue, analyze(self.cue), SEL),
                         encode_sector(SEL, self.code))
        raw = (self.cue.parent / "oss-pce-cd-test.bin").read_bytes()
        self.assertEqual(raw[(SEL + 2) * RAW_BYTES + USER_OFFSET:(SEL + 2) * RAW_BYTES + USER_OFFSET + 2048],
                         L.body_for(self.code, 1)[:2048])

    def test_variants_reach_expected_apis(self):
        # A: 1 API ($02 stub X=2). B: 2 APIs ($02 fake, $33 stub X=51).
        # C: 3 APIs ($02+$33 fake, $03 stub X=3). D: 3 real + clean halt.
        expected = {"A": ("api_unimplemented", 0x02), "B": ("api_unimplemented", 0x33),
                    "C": ("api_unimplemented", 0x03), "D": ("cd_halt_ok", None)}
        for variant, (reason, x) in expected.items():
            records, _ = self.cases[variant]
            with self.subTest(variant=variant):
                self.assertEqual([r["cycle"] for r in records["RUN"]], ["cold", "warm"])
                for run in records["RUN"]:
                    self.assertEqual(run["reason"], reason)
                    self.assertTrue(run["halt_confirmed"])
                    if x is not None:
                        self.assertEqual(run["x"], x)

    def test_d_transfers_2048b(self):
        from make_cd_image import sector_payload
        from build_cdload_exec import encode_sector
        records, _ = self.cases["D"]
        expected = sector_payload(SEL + 3)
        boot = encode_sector(SEL, self.code)
        self.assertNotEqual(boot, expected)
        for run in records["RUN"]:
            with self.subTest(cycle=run["cycle"]):
                buf = bytes.fromhex(run["buffer_hex"])
                rec = unified_record(buf)
                self.assertEqual(rec["magic"], 0xAA)
                self.assertEqual(rec["order1"], 0x02)
                self.assertEqual(rec["order33"], 0x33)
                self.assertEqual(rec["order3"], 0x03)
                self.assertEqual(rec["state"], [0, 1, 9, 1, 0, 0, 0, 0])
                self.assertEqual(rec["link_res"], [0, 8, 0, 8, 0, 0, 0, 0, 1, 12])
                self.assertEqual(rec["head"], expected[:8])
                self.assertNotEqual(rec["head"], boot[:8])
                self.assertEqual(buf[:2048], expected)
        for cycle in ("cold", "warm"):
            phases = sorted((r["load_sector"], r["load_count"]) for r in records["PHASE"]
                            if r["cycle"] == cycle and r["scsi_phase"] == 4)
            self.assertEqual(phases, [(SEL, 1), (SEL + 3, 1)])

    def test_mutant_rts_proves_transfer(self):
        from geargrafx_cdread import execute
        from build_cdload_exec import encode_sector
        from make_cd_image import sector_payload
        _, manifest = self.cases["D"]
        link_off = manifest["labels"]["cd_read_link_entry"] - 0xE000
        mutant = bytearray(self.exp_d)
        self.assertEqual(bytes(mutant[link_off:link_off + 2]), bytes((0x08, 0x48)))
        mutant[link_off] = 0x60
        mutant_path = self.work / "mutant-unified-link-rts.pce"
        mutant_path.write_bytes(bytes(mutant))
        records = execute(self.probe, mutant_path, self.cue,
                          self.work / "exp-unified" / "trace-labels.txt", STEP_BUDGET)
        for run in records["RUN"]:
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(run["reason"], "cd_halt_ok")
                buf = bytes.fromhex(run["buffer_hex"])
                self.assertEqual(buf[:2048], encode_sector(SEL, self.code))
        for cycle in ("cold", "warm"):
            phases = [(r["load_sector"], r["load_count"]) for r in records["PHASE"]
                      if r["cycle"] == cycle and r["scsi_phase"] == 4]
            self.assertEqual(phases, [(SEL, 1)])
        expected = sector_payload(SEL + 3)
        boot = encode_sector(SEL, self.code)
        with self.assertRaises(AssertionError):
            for run in records["RUN"]:
                buf = bytes.fromhex(run["buffer_hex"])
                rec = unified_record(buf)
                self.assertEqual(buf[:2048], expected)
                self.assertEqual(rec["head"], expected[:8])

    def test_next_stopper(self):
        # Comparison table inputs: last return position, stop cause, next API.
        stops = {}
        for variant in ("A", "B", "C", "D"):
            records, _ = self.cases[variant]
            with self.subTest(variant=variant):
                self.assertEqual([r["cycle"] for r in records["RUN"]], ["cold", "warm"])
                recs = [unified_record(bytes.fromhex(r["buffer_hex"])) if variant == "D"
                        else None for r in records["RUN"]]
                for run in records["RUN"]:
                    if variant == "D":
                        self.assertEqual(run["pc"], self.manifest_d["trace_symbols"]["cd_halt_ok"])
                    else:
                        self.assertEqual(run["reason"], "api_unimplemented")
                stops[variant] = [r["reason"] for r in records["RUN"]]
        self.assertEqual(stops["A"], ["api_unimplemented", "api_unimplemented"])
        self.assertEqual(stops["D"], ["cd_halt_ok", "cd_halt_ok"])
        # Cold/warm identical for all (same inputs, same budget).
        for variant in ("A", "B", "C", "D"):
            records, _ = self.cases[variant]
            bufs = [r["buffer_hex"] for r in records["RUN"]]
            # A/B/C halt early (buffers differ from D clean, but cold==warm per variant).
            self.assertEqual(bufs[0], bufs[1])

    def test_commercial_is_not_run(self):
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
