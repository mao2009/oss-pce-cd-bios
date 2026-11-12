"""Pinned real-core execution of the experimental slot $33 body (Issue #61; G5 Agent B).

The experimental System-Card-sized DEV image redirects its $E099 table entry to a
BOOT-region copy of the project-own DEV EX_DSPOFF fragment (single source:
src/ex_dspoff.s). The guest below is placed in the $E800-$EFFF probe-overlay
window with the reset vector redirected at it (the BOOT region holds the $33
body, so the reset-overwrite pattern cannot be used) and JSRs the REAL $E099
table entry -- not a stub, not an immediate-RTS fabrication.

Each run checks the project-own contract (src/ex_dspoff.s) through the shared
geargrafx_dev_cr oracle: VDC CR low gains cleared BB+SB via the work-RAM shadow
($2502/$2503, magic $A5), CR high untouched, A/X/Y/P preserved (C=0 ok, C=1 on
uninitialized-shadow reject), VDC AR left at 5, stack balanced, MPRs intact, RAM
sentinel untouched. The authoritative VDC oracle is the probe's independent
vdc_cr observation (GetState()->R[5]).

NOT the reference System Card EX_DSPOFF ABI (remains UNVERIFIED). NOT game
compatibility. NOT G5 success by fake RTS. Commercial media is never consumed
(NOT_RUN without ./rom). Runs in `make integration-test`, never in the host stage.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.environment import cc65_required, cc65_skip_reason, requires_cc65  # noqa: E402
import geargrafx_api as api  # noqa: E402
import geargrafx_dev_bcd as dev  # noqa: E402
import geargrafx_dev_cr as cr  # noqa: E402

BLOCKED_EXIT = 3
SLOT = 0x33
ENTRY = 0xE000 + 3 * SLOT  # the real $E099 table entry (redirected in this image)
ENTRY02 = 0xE000 + 3 * 0x02
GUEST_ADDR = 0xE800  # probe-overlay window (padding in the built image)
SET_MASK, CLEAR_MASK = 0x00, 0xC0
REGS = (0x12, 0x34, 0x56, 0x34, 0xFF)
REGS_SP05 = (0xA5, 0x5A, 0x3C, 0xB4, 0x05)
REGS_SP07 = (0xA5, 0x5A, 0x3C, 0xB4, 0x07)
# A mutant is detected only if a contract check names the mismatch (a crash is not a detection).
MISMATCH_KEYS = ("A/P/X/Y observed", "stack not balanced", "RAM sentinel",
                 "did not reach its halt loop", "MPR changed", "MPR0 not restored",
                 "CR $", "VDC CR", "VDC AR", "shadow ", "out-of-contract RAM write",
                 "reject path changed", "reserved shadow")
MUTANTS = [
    # AND #$3F is the display-off mask; each single-byte change must be caught on 0xF2.
    ("and_ff_keeps_display", bytes([0x29, 0x3F]), bytes([0x29, 0xFF])),
    ("and_7f_keeps_bb", bytes([0x29, 0x3F]), bytes([0x29, 0x7F])),
    ("and_00_clears_kept_bits", bytes([0x29, 0x3F]), bytes([0x29, 0x00])),
    ("high_byte_rewritten", bytes([0x8D, 0x02, 0x00]), bytes([0x8D, 0x03, 0x00])),
    ("stack_smash", bytes([0x8D, 0x02, 0x25]), bytes([0x8D, 0x00, 0x21])),
]
REJECT_MUTANTS = [
    ("reject_bypass", bytes([0xC9, 0xA5]), bytes([0xC9, 0x00])),
]


def guest_two_call(reset, entry02, entry33, cr_low, cr_high, a, x, y, p, sp):
    """Seed + program CR, JSR slot $02 (caller-supplied scaffolding), JSR slot $33, record, halt."""
    code = bytearray([0x78, 0xD8, 0xA9, 0xF8, 0x53, 0x02, 0xA2, sp, 0x9A])
    code += bytes([0xA2, 0x00, 0x8A, 0x49, 0xA5,
                   0x9D, cr.SENTINEL & 0xFF, cr.SENTINEL >> 8, 0xE8, 0xE0, cr.SENTINEL_LEN, 0xD0, 0xF5])
    code += bytes([0x43, 0x01, 0x8D, cr.STORE_MPR0 & 0xFF, cr.STORE_MPR0 >> 8])
    code += bytes([0xA9, cr_low, 0x8D, 0x02, 0x25, 0xA9, cr.SHADOW_VALID, 0x8D, 0x03, 0x25])
    code += cr.window_open()
    code += bytes([0xA9, 0x05, 0x8D, 0x00, 0x00, 0xA9, cr_low, 0x8D, 0x02, 0x00,
                   0xA9, cr_high, 0x8D, 0x03, 0x00])
    code += cr.window_close(cr.STORE_MPR0)
    code += bytes([0xA9, p, 0x48, 0x28, 0xA2, x, 0xA0, y, 0xA9, a,
                   0x20, entry02 & 0xFF, entry02 >> 8,
                   0x20, entry33 & 0xFF, entry33 >> 8,
                   0x08, 0x8D, cr.STORE & 0xFF, cr.STORE >> 8, 0x68,
                   0x8D, (cr.STORE + 1) & 0xFF, (cr.STORE + 1) >> 8,
                   0x8E, (cr.STORE + 2) & 0xFF, (cr.STORE + 2) >> 8,
                   0x8C, (cr.STORE + 3) & 0xFF, (cr.STORE + 3) >> 8,
                   0xBA, 0x8E, (cr.STORE + 4) & 0xFF, (cr.STORE + 4) >> 8])
    halt = reset + len(code)
    code += bytes([0x80, 0xFE])
    if reset + len(code) > 0xFFF6:
        raise ValueError("BLOCKED: guest code does not fit before the vectors")
    return bytes(code), halt


@requires_cc65
class ExperimentalSlot33RealCpuTests(unittest.TestCase):
    """Real Geargrafx runs of project-own EX_DSPOFF through the $E099 entry."""

    @classmethod
    def setUpClass(cls):
        reason = cc65_skip_reason()
        if reason is not None:
            if cc65_required():
                raise AssertionError(f"REQUIRED tool missing: {reason}")
            raise unittest.SkipTest(f"SKIP cc65-dependent: {reason}")
        from build_rom import build
        from build_experimental_bios_boot import (ROM_NAME, build_experimental_bios_boot,
                                                  build_experimental_payload)
        cls.work = Path(tempfile.mkdtemp(prefix="pce-dspoff-real-core-"))
        cls.addClassCleanup(shutil.rmtree, cls.work, ignore_errors=True)
        normal_path = build(cls.work / "normal", root=ROOT)
        cls.normal = normal_path.read_bytes()
        import make_cd_image
        from build_cdload_exec import USER_OFFSET, encode_sector
        from experimental_track_selection import selection_from_cue, snapshot_media
        single_dir = cls.work / "single"
        make_cd_image.build_image(single_dir)
        cue = single_dir / make_cd_image.CUE_NAME
        payload, _ = build_experimental_payload(cls.work / "payload", "debug")
        raw = bytearray((cue.parent / make_cd_image.BIN_NAME).read_bytes())
        raw[USER_OFFSET:USER_OFFSET + 2048] = encode_sector(0, payload)
        (cue.parent / make_cd_image.BIN_NAME).write_bytes(bytes(raw))
        cue = snapshot_media(cue, cls.work / "mounted")
        out = cls.work / "exp"
        cls.exp_path = build_experimental_bios_boot(normal_path, out, "debug", cue, payload)
        cls.exp = cls.exp_path.read_bytes()
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        cls.body = manifest["symbols"]["experimental_slot_33"]
        cls.stub33 = manifest["experimental_slot_33"]["fallback_stub"]
        # Pinned probe (instruction-debugger objects at the pinned Geargrafx revision).
        probe_dir = cls.work / "probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        cls.probe = api.compile_probe(probe_dir)
        cls.cue = cls.work / "blank.cue"
        cls.cue.write_text('FILE "blank.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        (cls.work / "blank.bin").write_bytes(bytes(2352 * 4))
        cls.evidence = {"scope": "experimental DEV ROM real-core run of project-own EX_DSPOFF, "
                                 "NOT System Card ABI compatibility",
                        "rom_sha256": hashlib.sha256(cls.exp).hexdigest(),
                        "experimental_slot_33_body": f"{cls.body:#06x}",
                        "runs": [], "negative_cases": [],
                        "commercial": {"status": "NOT_RUN",
                                       "reason": "./rom not consumed; synthetic media only"}}
        cls.addClassCleanup(cls._write_evidence)

    @classmethod
    def _write_evidence(cls):
        (cls.work / "geargrafx-dspoff-real-cpu-evidence.json").write_text(
            json.dumps(cls.evidence, indent=2) + "\n")
        print(f"evidence: {len(cls.evidence['runs'])} runs, "
              f"{len(cls.evidence['negative_cases'])} mutants rejected")

    def _variant(self, code, label, patch02_rts=False, base=None):
        base = self.exp if base is None else base
        if base[0x800:0x800 + len(code)] != b"\xff" * len(code):
            raise ValueError(f"{label}: overlay window not free for the guest")
        variant = bytearray(base)
        variant[0x800:0x800 + len(code)] = code
        variant[0x1FFE:0x2000] = GUEST_ADDR.to_bytes(2, "little")
        if patch02_rts:
            # SIMULATED scaffolding ONLY to reach $33 after a prior API call: the
            # $02 fallback stub becomes RTS+NOPs in this variant. Never a success claim.
            at = 3 * 0x02
            if variant[at] != 0x4C or bytes(variant[at + 1:at + 3]) != base[at + 1:at + 3]:
                raise ValueError("slot $02 table entry is not the linked JMP; refusing scaffolding")
            stub = variant[at + 1] | (variant[at + 2] << 8)
            off = stub - 0xE000
            if bytes(variant[off:off + 2]) != bytes((0xA2, 0x02)):
                raise ValueError("slot $02 stub lacks the LDX #$02 marker; refusing scaffolding")
            variant[off:off + 5] = bytes((0x60, 0xEA, 0xEA, 0xEA, 0xEA))
        rom = self.work / f"{label}.pce"
        rom.write_bytes(bytes(variant))
        return rom

    def _run(self, label, cr_low, cr_high, regs, seed_shadow=True):
        a, x, y, p, sp = regs
        code, halt = cr.guest(GUEST_ADDR, ENTRY, cr_low, cr_high, a, x, y, p, sp,
                              seed_shadow=seed_shadow)
        rows = dev.run_probe(self.probe, self._variant(code, label), self.cue, count=cr.RUN_STEPS)
        result = cr.check(label, rows, halt, a, x, y, p, sp, cr_low, cr_high,
                          SET_MASK, CLEAR_MASK, seed_shadow=seed_shadow)
        cold = next(r for r in rows if r["phase"] == "cold")
        warm = next(r for r in rows if r["phase"] == "warm")
        if warm["pc"] != halt or cold["pc"] != halt:
            raise ValueError(f"{label}: cold/warm repeatability "
                             f"(cold ${cold['pc']:04X}, warm ${warm['pc']:04X}, want ${halt:04X})")
        if warm["vdc_cr"] != cold["vdc_cr"]:
            raise ValueError(f"{label}: warm VDC CR differs from cold")
        result["warm_repeatable"] = True
        self.evidence["runs"].append({"case": label, **result})
        return result

    def test_ok_path_clears_bb_sb_and_keeps_other_bits(self):
        self._run("ok-f2", 0xF2, 0x00, REGS)

    def test_ok_path_cr_boundaries(self):
        for label, lo, hi in (("ok-00", 0x00, 0x00), ("ok-ff", 0xFF, 0x00),
                              ("ok-c0", 0xC0, 0x00), ("ok-high-kept", 0x40, 0x1F)):
            with self.subTest(case=label):
                self._run(label, lo, hi, REGS)

    def test_ok_path_sp_boundaries(self):
        self._run("ok-sp05-min", 0x40, 0x00, REGS_SP05)
        self._run("ok-sp07", 0x40, 0x00, REGS_SP07)

    def test_reject_path_unseeded_leaves_hardware_alone(self):
        self._run("reject-uninit", 0x00, 0x00, REGS, seed_shadow=False)

    def test_mutants_are_rejected(self):
        body_off = self.body - 0xE000
        (mname, mlo, mhi), mregs = ("all-set", 0xF2, 0x00), REGS
        for name, find, replace in MUTANTS:
            with self.subTest(mutant=name):
                at = self.exp.find(find, body_off, body_off + 0x60)
                if at < 0:
                    raise ValueError(f"mutant {name}: pattern {find.hex()} not in slot $33 body")
                mutant = bytearray(self.exp)
                mutant[at:at + len(replace)] = replace
                code, halt = cr.guest(GUEST_ADDR, ENTRY, mlo, mhi, *mregs)
                rows = dev.run_probe(self.probe, self._variant(code, f"mut-{name}", base=bytes(mutant)),
                                     self.cue, count=cr.RUN_STEPS)
                with self.assertRaises(ValueError) as caught:
                    cr.check(f"mut-{name}", rows, halt, *mregs, mlo, mhi,
                             SET_MASK, CLEAR_MASK, seed_shadow=True)
                if not any(key in str(caught.exception) for key in MISMATCH_KEYS):
                    raise ValueError(f"mutant {name}: wrong rejection cause: {caught.exception}")
                print(f"MUTANT-REJECTED {name}: {caught.exception}", flush=True)
                self.evidence["negative_cases"].append({"case": name,
                                                        "rejected_by": str(caught.exception)})

    def test_reject_bypass_mutant_is_rejected(self):
        body_off = self.body - 0xE000
        for name, find, replace in REJECT_MUTANTS:
            with self.subTest(mutant=name):
                at = self.exp.find(find, body_off, body_off + 0x60)
                if at < 0:
                    raise ValueError(f"mutant {name}: pattern {find.hex()} not in slot $33 body")
                mutant = bytearray(self.exp)
                mutant[at:at + len(replace)] = replace
                a, x, y, p, sp = REGS
                code, halt = cr.guest(GUEST_ADDR, ENTRY, 0x00, 0x00, a, x, y, p, sp,
                                      seed_shadow=False)
                rows = dev.run_probe(self.probe, self._variant(code, f"mut-{name}", base=bytes(mutant)),
                                     self.cue, count=cr.RUN_STEPS)
                with self.assertRaises(ValueError) as caught:
                    cr.check(f"mut-{name}", rows, halt, a, x, y, p, sp, 0x00, 0x00,
                             SET_MASK, CLEAR_MASK, seed_shadow=False)
                if not any(key in str(caught.exception) for key in MISMATCH_KEYS):
                    raise ValueError(f"mutant {name}: wrong rejection cause: {caught.exception}")
                print(f"MUTANT-REJECTED {name}: {caught.exception}", flush=True)
                self.evidence["negative_cases"].append({"case": name,
                                                        "rejected_by": str(caught.exception)})

    def test_two_call_flow_reaches_slot_33_after_slot_02_scaffolding(self):
        # SIMULATED $02 scaffolding (fake RTS in this variant only) so the guest can
        # observe whether boot-flow order reaches $33 and returns: the $33 processing
        # below is REAL (redirected table entry, dev_cr oracle).
        a, x, y, p, sp = REGS
        code, halt = guest_two_call(GUEST_ADDR, ENTRY02, ENTRY, 0xF2, 0x00, a, x, y, p, sp)
        rows = dev.run_probe(self.probe, self._variant(code, "two-call", patch02_rts=True),
                             self.cue, count=cr.RUN_STEPS)
        result = cr.check("two-call", rows, halt, a, x, y, p, sp, 0xF2, 0x00,
                          SET_MASK, CLEAR_MASK, seed_shadow=True)
        self.evidence["runs"].append({"case": "two-call-slot02-scaffold-then-real-33", **result})

    def test_wired_table_points_at_boot_body(self):
        table = bytes(self.exp[3 * SLOT:3 * SLOT + 3])
        self.assertEqual(table, bytes((0x4C, self.body & 0xFF, self.body >> 8)))
        normal_target = self.normal[3 * SLOT + 1] | (self.normal[3 * SLOT + 2] << 8)
        self.assertEqual(normal_target, int(self.stub33, 16))

    def test_commercial_media_is_not_run(self):
        self.assertEqual(self.evidence["commercial"]["status"], "NOT_RUN")
        self.assertTrue(str(self.cue.resolve()).startswith(str(self.work.resolve())))


if __name__ == "__main__":
    unittest.main()
