"""Issue #793 integration: G5 $4A SIMULATED diagnostic (synthetic, pinned core).

Parent #635. Synthetic only, NOT_RUN commercial. NOT compatible, NOT ABI.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.environment import cc65_skip_reason, cc65_required, requires_cc65  # noqa: E402

MODE = "debug"
SEL = 0x0109
STEP_BUDGET = 20_000_000
BUFFER_BASE = 0x2800
RES_OFF = 0x3100 - BUFFER_BASE
ENTRY4A = 0xE0DE
ENTRY04 = 0xE00C

G5_4A_PAYLOAD_ASM = r"""
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done
RESBASE = $3100
E4A = $E0DE
E04 = $E00C
payload_entry:
    cld
    lda #$00
    sta RESBASE+1
    sta RESBASE+2
    sta RESBASE+3
    sta RESBASE+4
    sta RESBASE+5
    sta RESBASE+6
    sta RESBASE+7
    lda #$AA
    sta RESBASE
    ldx #$FF
    txs
    jsr E4A
    sta RESBASE+1
    php
    pla
    sta RESBASE+2
    tsx
    txa
    sta RESBASE+3
    lda #$4A
    sta RESBASE+4
    jsr E04
    sta RESBASE+5
    php
    pla
    sta RESBASE+6
    lda #$04
    sta RESBASE+7
payload_done:
    bra payload_done
"""


@requires_cc65
class G5FourASimRealCoreTests(unittest.TestCase):
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
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        from geargrafx_api import compile_probe
        from geargrafx_cdread import execute
        import geargrafx_ipl_loader as L
        cls.L = L
        cls.execute = execute
        cls.work = Path(tempfile.mkdtemp(prefix="pce-g54a-realcore-"))
        cls.addClassCleanup(shutil.rmtree, cls.work, ignore_errors=True)
        normal_path = build(cls.work / "normal", root=ROOT)
        out = cls.work / "g5rom"
        std_rom, diag_rom, syms, info = build_ipl_loader_g5_4a_simulated(
            normal_path, out, SEL, MODE, "t4a", ["-D", "IPL_PUBLIC"], 1)
        cls.std_rom = std_rom
        cls.diag_rom = diag_rom
        cls.syms = syms
        cls.info = info
        # Standard $4A table entry must be JMP (no fake RTS in control).
        std = std_rom.read_bytes()
        if std[ENTRY4A - 0xE000] != 0x4C:
            raise ValueError("BLOCKED: standard $4A entry is not JMP")
        diag = diag_rom.read_bytes()
        if diag[ENTRY4A - 0xE000] != 0x4C:
            raise ValueError("BLOCKED: diagnostic $4A entry moved")
        probe_dir = cls.work / "probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        cls.probe = compile_probe(probe_dir, "geargrafx_cdread_probe.cpp",
                                  "geargrafx-g54a-probe")
        # Synthetic PUBLIC disc with the $4A payload.
        from test_ipl_loader_g5_800 import build_custom_payload
        code, pay_syms = build_custom_payload(
            G5_4A_PAYLOAD_ASM, cls.work / "pay-4a", "g54a", [])
        case = {"name": "g5_4a", "n": 1, "iplblk": 2, "sta": 0x3000,
                "entry": 0x3000, "mprs": [0, 1, 2, 3, 4], "openmode": 0,
                "public": True}
        body = L.body_for(code, case["n"])
        cksum = sum(body) & 0xFFFF
        info_sec = L.info_sector(case["iplblk"], case["n"], case["sta"],
                                 case["entry"], case["mprs"], case["openmode"], cksum)
        info_sec[14] = 0
        info_sec[15] = 0
        disc = cls.work / "disc-4a"
        cue = L.make_disc(disc, L.case_disc(case, body, info_sec))
        cls.cue = cue
        cls.code = code
        cls.pay_syms = pay_syms
        cls.case = case
        cls.body = body
        # Control labels (standard ROM) + experiment labels (diagnostic ROM:
        # same halt symbols, patch moves nothing).
        cls.std_labels = cls.work / "std.labels"
        cls.std_labels.write_text(
            f"cd_halt_ok {pay_syms['payload_done']:04x}\n"
            f"cd_halt_fail {syms['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {syms['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {syms['api_unimplemented']:04x}\n",
            encoding="ascii")
        cls.diag_labels = cls.work / "diag.labels"
        cls.diag_labels.write_text(
            f"cd_halt_ok {pay_syms['payload_done']:04x}\n"
            f"cd_halt_fail {syms['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {syms['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {syms['api_unimplemented']:04x}\n",
            encoding="ascii")
        cls.std_records = execute(cls.probe, std_rom, cue, cls.std_labels, STEP_BUDGET)
        cls.diag_records = execute(cls.probe, diag_rom, cue, cls.diag_labels, STEP_BUDGET)
        cls.evidence = {"commercial": {"status": "NOT_RUN",
                                       "reason": "synthetic-only $4A test"}}

    def test_standard_halts_at_4a(self):
        for run in self.std_records["RUN"]:
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(run["reason"], "api_unimplemented")
                self.assertTrue(run["halt_confirmed"])
                self.assertEqual(run["x"], 0x4A)
                buf = bytes.fromhex(run["buffer_hex"])
                # $4A reached, no return, $04 never reached.
                self.assertEqual(buf[RES_OFF], 0xAA)
                self.assertEqual(buf[RES_OFF + 4], 0x00)  # order marker unwritten

    def test_diagnostic_returns_and_reaches_next_api(self):
        for run in self.diag_records["RUN"]:
            with self.subTest(cycle=run["cycle"]):
                self.assertEqual(run["reason"], "api_unimplemented")
                self.assertTrue(run["halt_confirmed"])
                # Post-$4A $04 stub halt proves SIMULATED return + next entry.
                self.assertEqual(run["x"], 0x04)
                buf = bytes.fromhex(run["buffer_hex"])
                self.assertEqual(buf[RES_OFF], 0xAA)
                self.assertEqual(buf[RES_OFF + 4], 0x4A)
                # Stores after the $04 JSR never execute ($04 halts).
                self.assertEqual(buf[RES_OFF + 5], 0x00)
                self.assertEqual(buf[RES_OFF + 7], 0x00)

    def test_cold_warm_repeatable(self):
        std_runs = [(r["reason"], r["x"]) for r in self.std_records["RUN"]]
        self.assertEqual(std_runs[0], std_runs[1])
        diag_runs = [(r["reason"], r["x"]) for r in self.diag_records["RUN"]]
        self.assertEqual(diag_runs[0], diag_runs[1])


if __name__ == "__main__":
    unittest.main()
