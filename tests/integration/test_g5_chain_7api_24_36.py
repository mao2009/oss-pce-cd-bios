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


# ---- Strict 7-API wiring-mutant oracle (Issue #64 MEDIUM-1) ----
#
# The previous oracle credited a wiring-missing mutant as RED whenever the
# RUN reasons merely lacked "cd_halt_ok". That also credits budget
# exhaustion, pre-target stops, unexpected interrupts, malformed runs, and
# truncations. run_probe() already refuses probe-process failure, probe
# timeout, and RUN-count shortage (ValueError above this layer); the strict
# oracle below additionally separates the semantic cases that a completed
# probe record can still carry:
#   MUTANT_DETECTED - mutant ROM proven applied AND both cycles show the
#     exact expected stub trap (reason/pc/X/halt) with exact APICALL prefix
#     arrival (order/entry_pc/in_table/SP-delta/stack/MPR1, no post-target
#     calls). Only this label counts as RED.
#   MUTANT_SURVIVED - mutant ROM applied yet the chain still clean-halts
#     with the full 6-call prefix (detection failed; the test fails).
#   INCONCLUSIVE - well-formed run showing neither clean-halt nor the
#     expected trap (budget/interrupt/pre-target stop, reason/pc/X
#     mismatch, unconfirmed halt). Never RED, never a pass.
#   HARNESS_FAILURE - malformed run/record/mutant (counts, cycles, fields,
#     buffers, truncation, APICALL evidence, mutant proof). Never RED.
# APICALL stacked-return entry evidence is never confused with RTS
# observation here: arrival only requires entry-time bytes (MATCH), and
# end-to-end return is out of scope for the abnormal path by construction.

MUTANT_DETECTED = "MUTANT_DETECTED"
HARNESS_FAILURE = "HARNESS_FAILURE"
INCONCLUSIVE = "INCONCLUSIVE"
MUTANT_SURVIVED = "MUTANT_SURVIVED"

FULL_PREFIX_SLOTS = (0x33, 0x4A, 0x23, 0x25, 0x24, 0x36)
FULL_PREFIX_PCS = (0xE099, 0xE0DE, 0xE069, 0xE06F, 0xE06C, 0xE0A2)
PREFIX_24_SLOTS = FULL_PREFIX_SLOTS[:5]
PREFIX_24_PCS = FULL_PREFIX_PCS[:5]
PREFIX_36_SLOTS = FULL_PREFIX_SLOTS
PREFIX_36_PCS = FULL_PREFIX_PCS

_RUN_FIELDS = ("cycle", "reason", "pc", "halt_confirmed", "x",
               "buffer_hex", "apicall_truncated", "ret_truncated")
_APICALL_FIELDS = ("cycle", "slot", "entry_pc", "in_table", "sp_delta",
                   "stack_check", "mpr")


def prove_7api_wiring_mutant_applied(exp_bytes, mutant_bytes, normal_bytes, slot):
    """HARNESS_FAILURE unless the mutant ROM equals the experimental ROM
    except in exactly the target slot's 3-byte table window, which must now
    match the normal fallback stub. Guards against crediting an unapplied
    mutant (identical ROM), a size change, a wrong stub, an off-window edit,
    or an inapplicable (already-stub) table."""
    for name, blob in (("experimental", exp_bytes), ("mutant", mutant_bytes),
                       ("normal", normal_bytes)):
        if not isinstance(blob, (bytes, bytearray)):
            raise AssertionError(f"HARNESS_FAILURE: {name} ROM is not bytes")
        if len(blob) != 262144:
            raise AssertionError(f"HARNESS_FAILURE: {name} ROM size {len(blob)} != 262144")
    if slot not in (0x24, 0x36):
        raise AssertionError(f"HARNESS_FAILURE: slot {slot:#04x} is not $24/$36")
    at = 3 * slot
    want = bytes(normal_bytes[at:at + 3])
    if bytes(exp_bytes[at:at + 3]) == want:
        raise AssertionError(f"HARNESS_FAILURE: ${slot:02X} table already stub (mutant not applicable)")
    if bytes(mutant_bytes[at:at + 3]) != want:
        raise AssertionError("HARNESS_FAILURE: mutant window is not the normal fallback stub")
    if bytes(exp_bytes[:at]) != bytes(mutant_bytes[:at]) or \
            bytes(exp_bytes[at + 3:]) != bytes(mutant_bytes[at + 3:]):
        raise AssertionError("HARNESS_FAILURE: mutant changed bytes outside the target window")


def _7api_run_shape_ok(rec):
    """HARNESS_FAILURE unless the record is structurally complete: RUN is
    a cold/warm 2-list with all fields, valid 2048B+ hex buffers, no
    truncation, and a well-formed APICALL list."""
    if not isinstance(rec, dict):
        raise AssertionError("HARNESS_FAILURE: record is not an object")
    runs = rec.get("RUN")
    if not isinstance(runs, list) or len(runs) != 2:
        raise AssertionError("HARNESS_FAILURE: RUN is not a 2-list")
    cycles = sorted(r.get("cycle") for r in runs if isinstance(r, dict))
    if cycles != ["cold", "warm"]:
        raise AssertionError("HARNESS_FAILURE: RUN cycles are not cold/warm once each")
    for r in runs:
        for field in _RUN_FIELDS:
            if field not in r:
                raise AssertionError(f"HARNESS_FAILURE: RUN record missing {field}")
        if not isinstance(r["pc"], int) or not isinstance(r["buffer_hex"], str):
            raise AssertionError("HARNESS_FAILURE: RUN pc/buffer_hex mistyped")
        try:
            buf = bytes.fromhex(r["buffer_hex"])
        except (TypeError, ValueError):
            raise AssertionError("HARNESS_FAILURE: RUN buffer_hex is not valid hex")
        if len(buf) < 2048:
            raise AssertionError("HARNESS_FAILURE: RUN buffer shorter than 2048B")
        if r["apicall_truncated"] is not False or r["ret_truncated"] is not False:
            raise AssertionError("HARNESS_FAILURE: RUN trace truncated")
    calls = rec.get("APICALL")
    if not isinstance(calls, list):
        raise AssertionError("HARNESS_FAILURE: APICALL is not a list")
    for c in calls:
        if not isinstance(c, dict):
            raise AssertionError("HARNESS_FAILURE: APICALL entry is not an object")
        for field in _APICALL_FIELDS:
            if field not in c:
                raise AssertionError(f"HARNESS_FAILURE: APICALL entry missing {field}")
        if c.get("cycle") not in ("cold", "warm"):
            raise AssertionError("HARNESS_FAILURE: APICALL entry has bad cycle")
        if not isinstance(c.get("slot"), int) or not isinstance(c.get("entry_pc"), int):
            raise AssertionError("HARNESS_FAILURE: APICALL slot/entry_pc mistyped")
        if not isinstance(c.get("in_table"), bool) or not isinstance(c.get("sp_delta"), int):
            raise AssertionError("HARNESS_FAILURE: APICALL in_table/sp_delta mistyped")
        if not isinstance(c.get("mpr"), list) or len(c.get("mpr")) != 8:
            raise AssertionError("HARNESS_FAILURE: APICALL mpr is not an 8-list")


def _7api_prefix_ok(rec, exp_slots, exp_pcs):
    """Exact per-cycle APICALL prefix: order, entry_pc, in_table, SP delta,
    stack MATCH, MPR1, and no calls beyond the expected prefix."""
    for cycle in ("cold", "warm"):
        calls = [c for c in rec["APICALL"] if c.get("cycle") == cycle]
        if [c.get("slot") for c in calls] != list(exp_slots):
            return False
        if [c.get("entry_pc") for c in calls] != list(exp_pcs):
            return False
        for c in calls:
            if c.get("in_table") is not True or c.get("sp_delta") != 2:
                return False
            if c.get("stack_check") != "MATCH":
                return False
            if c.get("mpr")[1] != 0xF8:
                return False
    return True


def classify_7api_mutant_run(rec, api_unimplemented_pc, exp_slots, exp_pcs, target_slot):
    """Classify a completed wiring-mutant probe record. Never raises:
    every malformed input maps to HARNESS_FAILURE."""
    try:
        _7api_run_shape_ok(rec)
    except AssertionError:
        return HARNESS_FAILURE
    except Exception:
        return HARNESS_FAILURE
    try:
        runs = {r["cycle"]: r for r in rec["RUN"]}
        reasons = (runs["cold"]["reason"], runs["warm"]["reason"])
        if all(x == "cd_halt_ok" for x in reasons):
            if _7api_prefix_ok(rec, FULL_PREFIX_SLOTS, FULL_PREFIX_PCS):
                return MUTANT_SURVIVED
            return HARNESS_FAILURE
        for cycle in ("cold", "warm"):
            run = runs[cycle]
            if run["reason"] != "api_unimplemented":
                return INCONCLUSIVE
            if run["halt_confirmed"] is not True:
                return INCONCLUSIVE
            if run["pc"] != api_unimplemented_pc:
                return INCONCLUSIVE
            if run["x"] != target_slot:
                return INCONCLUSIVE
        if runs["cold"]["reason"] != runs["warm"]["reason"] or \
                runs["cold"]["pc"] != runs["warm"]["pc"] or \
                runs["cold"]["x"] != runs["warm"]["x"]:
            return INCONCLUSIVE
        if not _7api_prefix_ok(rec, exp_slots, exp_pcs):
            return HARNESS_FAILURE
    except AssertionError:
        return HARNESS_FAILURE
    except Exception:
        return HARNESS_FAILURE
    return MUTANT_DETECTED


def old_validator_passes(rec):
    """The pre-fix oracle: pass iff no RUN reason is cd_halt_ok. Kept only
    to prove (in negative tests) that it mis-credits every non-halt
    anomaly that the strict oracle refuses."""
    try:
        reasons = {r.get("reason") for r in rec.get("RUN", [])}
    except Exception:
        return True
    return "cd_halt_ok" not in reasons


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

    def _prove_7api_wiring_mutant(self, slot):
        exp = self.rom.read_bytes()
        normal = self.normal
        at = 3 * slot
        want = bytes(normal[at:at + 3])
        if bytes(exp[at:at + 3]) == want:
            self.fail(f"HARNESS_FAILURE: ${slot:02X} table already stub (mutant not applicable)")
        mutant = bytearray(exp)
        mutant[at:at + 3] = want
        prove_7api_wiring_mutant_applied(exp, bytes(mutant), normal, slot)
        return bytes(mutant)

    def test_mutant_24_wiring_missing_red(self):
        # Strict oracle: MUTANT_DETECTED requires the proven mutant ROM plus
        # both cycles showing the exact $24 stub trap with the exact
        # $33->$4A->$23->$25->$24 APICALL prefix (entry evidence only, never
        # confused with RTS observation).
        mut_path = self.out / "mut24.pce"
        mut_path.write_bytes(self._prove_7api_wiring_mutant(0x24))
        rec = self._run_chain_on_rom(mut_path, "mut24")
        label = classify_7api_mutant_run(rec, self.labels["api_unimplemented"],
                                         PREFIX_24_SLOTS, PREFIX_24_PCS, 0x24)
        self.assertEqual(label, MUTANT_DETECTED,
                         f"$24 wiring-missing classified as {label} (must be MUTANT_DETECTED)")

    def test_mutant_36_wiring_missing_red(self):
        # $36 is the last call: slot-list equality alone cannot RED, so the
        # oracle requires the stub-trap shape (api_unimplemented/X/pc both
        # cycles) with the exact 6-call prefix.
        mut_path = self.out / "mut36.pce"
        mut_path.write_bytes(self._prove_7api_wiring_mutant(0x36))
        rec = self._run_chain_on_rom(mut_path, "mut36")
        label = classify_7api_mutant_run(rec, self.labels["api_unimplemented"],
                                         PREFIX_36_SLOTS, PREFIX_36_PCS, 0x36)
        self.assertEqual(label, MUTANT_DETECTED,
                         f"$36 wiring-missing classified as {label} (must be MUTANT_DETECTED)")


class Chain7MutantOracleUnitTests(unittest.TestCase):
    """RED->GREEN proof for the strict oracle: every synthetic anomaly that
    the old reason-only check mis-credits must not yield MUTANT_DETECTED,
    while the genuine stub-trap record must. No fixtures, no real core."""

    PC = 0xE123  # stand-in verified api_unimplemented halt pc

    def _run(self, cycle, reason, x, pc=None, halt=True, buf=True, trunc=False,
             ret_trunc=False):
        return {"cycle": cycle, "reason": reason,
                "pc": self.PC if pc is None else pc,
                "halt_confirmed": halt, "x": x,
                "buffer_hex": ("ab" * 2048) if buf else "zz",
                "apicall_truncated": trunc, "ret_truncated": ret_trunc}

    def _call(self, cycle, slot, pc, sp_delta=2, stack="MATCH", mpr1=0xF8):
        mpr = [0xF8, mpr1, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00]
        return {"cycle": cycle, "slot": slot, "entry_pc": pc,
                "in_table": True, "sp_delta": sp_delta,
                "stack_check": stack, "return_matches_caller": True,
                "mpr": mpr}

    def _good_rec(self, target=0x24):
        slots, pcs = (PREFIX_24_SLOTS, PREFIX_24_PCS) \
            if target == 0x24 else (PREFIX_36_SLOTS, PREFIX_36_PCS)
        rec = {"RUN": [self._run("cold", "api_unimplemented", target),
                        self._run("warm", "api_unimplemented", target)],
               "APICALL": []}
        for cycle in ("cold", "warm"):
            for slot, pc in zip(slots, pcs):
                rec["APICALL"].append(self._call(cycle, slot, pc))
        return rec

    def _classify24(self, rec):
        return classify_7api_mutant_run(rec, self.PC, PREFIX_24_SLOTS,
                                       PREFIX_24_PCS, 0x24)

    def _check_negative(self, rec, expected):
        # The strict oracle must not credit the anomaly ...
        self.assertEqual(self._classify24(rec), expected)
        # ... while the old reason-only oracle mis-credits it (proving the
        # fix matters). The survival case is the one the old check got right.
        self.assertTrue(old_validator_passes(rec))

    def test_oracle_positive_control_24(self):
        self.assertEqual(self._classify24(self._good_rec(0x24)), MUTANT_DETECTED)
        self.assertTrue(old_validator_passes(self._good_rec(0x24)))

    def test_oracle_positive_control_36(self):
        rec = self._good_rec(0x36)
        label = classify_7api_mutant_run(rec, self.PC, PREFIX_36_SLOTS,
                                         PREFIX_36_PCS, 0x36)
        self.assertEqual(label, MUTANT_DETECTED)
        self.assertTrue(old_validator_passes(rec))

    def test_oracle_rejects_budget_exhaustion(self):
        rec = self._good_rec()
        for r in rec["RUN"]:
            r["reason"] = "budget"
            r["halt_confirmed"] = False
        self._check_negative(rec, INCONCLUSIVE)

    def test_oracle_rejects_unexpected_interrupt(self):
        rec = self._good_rec()
        for r in rec["RUN"]:
            r["reason"] = "cd_unexpected_interrupt"
        self._check_negative(rec, INCONCLUSIVE)

    def test_oracle_rejects_pre_target_stop(self):
        rec = self._good_rec()
        for r in rec["RUN"]:
            r["reason"] = "cd_halt_fail"
        rec["APICALL"] = [c for c in rec["APICALL"]
                          if c["slot"] in (0x33, 0x4A, 0x23)]
        self._check_negative(rec, INCONCLUSIVE)

    def test_oracle_rejects_cold_only(self):
        rec = self._good_rec()
        rec["RUN"] = [rec["RUN"][0]]
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_warm_only(self):
        rec = self._good_rec()
        rec["RUN"] = [rec["RUN"][1]]
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_cold_duplicated(self):
        rec = self._good_rec()
        rec["RUN"] = [rec["RUN"][0], dict(rec["RUN"][0])]
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_reason_mismatch(self):
        rec = self._good_rec()
        rec["RUN"][1]["reason"] = "cd_halt_fail"
        self._check_negative(rec, INCONCLUSIVE)

    def test_oracle_rejects_unconfirmed_halt(self):
        rec = self._good_rec()
        for r in rec["RUN"]:
            r["halt_confirmed"] = False
        self._check_negative(rec, INCONCLUSIVE)

    def test_oracle_rejects_pc_mismatch(self):
        rec = self._good_rec()
        for r in rec["RUN"]:
            r["pc"] = 0x1000
        self._check_negative(rec, INCONCLUSIVE)

    def test_oracle_rejects_x_mismatch(self):
        rec = self._good_rec()
        for r in rec["RUN"]:
            r["x"] = 0x00
        self._check_negative(rec, INCONCLUSIVE)

    def test_oracle_rejects_missing_apicall(self):
        rec = self._good_rec()
        del rec["APICALL"]
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_order_violation(self):
        rec = self._good_rec()
        cold = [c for c in rec["APICALL"] if c["cycle"] == "cold"]
        cold[0], cold[1] = cold[1], cold[0]
        rec["APICALL"] = cold + [c for c in rec["APICALL"]
                                if c["cycle"] == "warm"]
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_entry_pc_mismatch(self):
        rec = self._good_rec()
        for c in rec["APICALL"]:
            if c["slot"] == 0x25:
                c["entry_pc"] = 0xE070
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_sp_delta_mismatch(self):
        rec = self._good_rec()
        for c in rec["APICALL"]:
            if c["cycle"] == "warm" and c["slot"] == 0x4A:
                c["sp_delta"] = 3
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_stack_check_mismatch(self):
        rec = self._good_rec()
        rec["APICALL"][0]["stack_check"] = "MISMATCH"
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_mpr1_mismatch(self):
        rec = self._good_rec()
        for c in rec["APICALL"]:
            if c["slot"] == 0x24:
                c["mpr"][1] = 0x00
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_truncation(self):
        rec = self._good_rec()
        rec["RUN"][0]["apicall_truncated"] = True
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_rejects_garbage_record(self):
        self.assertEqual(self._classify24({}), HARNESS_FAILURE)
        self.assertTrue(old_validator_passes({}))
        rec = self._good_rec()
        rec["RUN"] = "cold,warm"
        self._check_negative(rec, HARNESS_FAILURE)

    def test_oracle_survival_is_distinct(self):
        # A clean-halt with the full 6-call prefix is MUTANT_SURVIVED, not
        # detection: the one anomaly the old check also withholds (it fails
        # its own assertNotIn), proving the labels discriminate.
        rec = {"RUN": [self._run("cold", "cd_halt_ok", 0x31),
                        self._run("warm", "cd_halt_ok", 0x31)],
               "APICALL": []}
        for cycle in ("cold", "warm"):
            for slot, pc in zip(FULL_PREFIX_SLOTS, FULL_PREFIX_PCS):
                rec["APICALL"].append(self._call(cycle, slot, pc))
        self.assertEqual(self._classify24(rec), MUTANT_SURVIVED)
        self.assertFalse(old_validator_passes(rec))

    def _rom_fixture(self, slot):
        exp = bytearray(b"\xAA" * 262144)
        normal = bytearray(b"\xBB" * 262144)
        exp[3 * slot:3 * slot + 3] = b"\x4C\x00\x01"
        normal[3 * slot:3 * slot + 3] = b"\x4C\x34\x12"
        return bytes(exp), bytes(normal)

    def test_oracle_proves_applied_mutant(self):
        exp, normal = self._rom_fixture(0x24)
        mutant = bytearray(exp)
        mutant[3 * 0x24:3 * 0x24 + 3] = normal[3 * 0x24:3 * 0x24 + 3]
        prove_7api_wiring_mutant_applied(exp, bytes(mutant), normal, 0x24)

    def test_oracle_rejects_unapplied_mutant(self):
        exp, normal = self._rom_fixture(0x36)
        with self.assertRaisesRegex(AssertionError, "HARNESS_FAILURE"):
            prove_7api_wiring_mutant_applied(exp, bytes(exp), normal, 0x36)

    def test_oracle_rejects_off_window_change(self):
        exp, normal = self._rom_fixture(0x24)
        mutant = bytearray(exp)
        mutant[3 * 0x24:3 * 0x24 + 3] = normal[3 * 0x24:3 * 0x24 + 3]
        mutant[0x2000] ^= 0xFF
        with self.assertRaisesRegex(AssertionError, "HARNESS_FAILURE"):
            prove_7api_wiring_mutant_applied(exp, bytes(mutant), normal, 0x24)

    def test_oracle_rejects_wrong_stub_bytes(self):
        exp, normal = self._rom_fixture(0x36)
        mutant = bytearray(exp)
        mutant[3 * 0x36:3 * 0x36 + 3] = b"\x4C\x99\x99"
        with self.assertRaisesRegex(AssertionError, "HARNESS_FAILURE"):
            prove_7api_wiring_mutant_applied(exp, bytes(mutant), normal, 0x36)

    def test_oracle_rejects_size_change(self):
        exp, normal = self._rom_fixture(0x24)
        with self.assertRaisesRegex(AssertionError, "HARNESS_FAILURE"):
            prove_7api_wiring_mutant_applied(exp, exp[:-1], normal, 0x24)
