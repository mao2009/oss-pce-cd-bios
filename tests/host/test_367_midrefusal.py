"""Host checks for the #367 per-byte mid-transfer refusal driver (synthetic only).

No emulator, BIOS, ROM or Geargrafx core is read. Verifies the case set (30 real-core cases),
the per-op cross expectations are internally consistent, the classifiers accept well-formed
synthetic rows and reject falsified ones via ValueError (the purpose assertion), and the driver
contains no Python `assert` safety checks (they would vanish under `python -O`).
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_block_midrefusal as M  # noqa: E402

MPR = [0xF8, 0xF8, 0xFF, 0xF8, 0xF8, 0xF8, 0xF8, 0xF8]


def synthetic_cross_row(op, *, armed=True):
    dests, refusals, bsrc, bdst, w3ffe, w3fff, ar = M.CROSS[op]
    row = {
        "exit": 0, "mode": f"{op}-cross-{'armed' if armed else 'unguarded'}", "status": "ok",
        "armed": armed, "op": op, "block_op": M.OPS[op][0], "opcode": M.OPS[op][1],
        "length": 3, "src": 0x1E80, "stop_pc": 7687, "expected_end_pc": 7687,
        "before": {"mpr": list(MPR)}, "after": {"mpr": list(MPR)},
        "sp": [4, 4], "p": [4, 4], "a_after": 0x12, "x_after": 0x34, "y_after": 0x07,
        "stack_wram": [0x34, 0x12, 0x07], "vdc_sr": [M.SR_PRESET, M.SR_PRESET],
        "vdc_r0": [0, 0], "vdc_r7": [0, 0], "vdc_read_buffer": [0xBEEF, 0xBEEF],
        "wram_3ffe": w3ffe, "wram_3fff": w3fff,
    }
    if armed:
        row.update({"guard_calls": 3, "guard_refusals": refusals, "guard_dests": list(dests),
                    "byte_src": list(bsrc), "byte_dst": list(bdst), "vdc_ar": [0, 0]})
    else:
        all_src, all_dst = M.CROSS_ALL[op]
        row.update({"guard_calls": 0, "guard_refusals": 0, "guard_dests": [],
                    "byte_src": list(all_src), "byte_dst": list(all_dst), "vdc_ar": [0, ar]})
    return row


def synthetic_srcread_row(op, *, forbidden, armed=True):
    mpr = [0xFF, 0xF8, 0xFF, 0xF8, 0xF8, 0xF8, 0xF8, 0xF8] if forbidden else list(MPR)
    row = {
        "exit": 0, "mode": f"{op}-{'srcreadff' if forbidden else 'srcread'}-{'armed' if armed else 'unguarded'}",
        "status": "ok", "armed": armed, "op": op, "block_op": M.OPS[op][0], "opcode": M.OPS[op][1],
        "length": 1, "src": 0x4104, "stop_pc": 15879, "expected_end_pc": 15879,
        "before": {"mpr": list(mpr)}, "after": {"mpr": list(mpr)},
        "sp": [4, 4], "p": [4, 4], "a_after": 0x12, "x_after": 0x34, "y_after": 0x07,
        "stack_wram": [0x34, 0x12, 0x07], "vdc_r0": [0, 0], "vdc_r7": [0, 0],
        "vdc_read_buffer": [0xBEEF, 0xBEEF],
    }
    if forbidden:
        row.update({"guard_calls": 1 if armed else 0, "guard_refusals": 1 if armed else 0,
                    "byte_src": [] if armed else [0x4104], "byte_dst": [] if armed else [0x0000],
                    "vdc_sr": [M.SR_PRESET, M.SR_PRESET] if armed else [M.SR_PRESET, 0],
                    "vdc_ar": [0, 0] if armed else [0, M.SR_PRESET]})
    else:
        row.update({"guard_calls": 1 if armed else 0, "guard_refusals": 0,
                    "byte_src": [0x4104], "byte_dst": [0x3E90],
                    "vdc_sr": [M.SR_PRESET, 0], "vdc_ar": [0, 0], "wram_1e90": M.SR_PRESET})
    return row


class CaseSetTest(unittest.TestCase):
    def test_30_cases(self):
        cases = list(M.cases())
        self.assertEqual(len(cases), 30)
        self.assertEqual(len(set(cases)), 30)
        for op in M.OPS:
            for variant in ("cross", "srcread", "srcreadff"):
                for role in ("armed", "unguarded"):
                    self.assertIn(f"{op}-{variant}-{role}", cases)

    def test_cross_sequences_are_distinct_per_op(self):
        # The five ops must not share one destination sequence; each opcode has its own walk.
        seqs = {op: tuple(M.CROSS[op][0]) for op in M.OPS}
        self.assertEqual(seqs["tii"], seqs["tai"])
        self.assertNotEqual(seqs["tii"], seqs["tdd"])
        self.assertNotEqual(seqs["tia"], seqs["tin"])
        self.assertNotEqual(tuple(M.CROSS_ALL["tai"][0]), tuple(M.CROSS_ALL["tii"][0]))

    def test_tia_cross_has_post_refusal_allowed_byte(self):
        dests, refusals, bsrc, bdst, *_ = M.CROSS["tia"]
        self.assertEqual((dests, refusals), ([0x3FFF, 0x4000, 0x3FFF], 1))
        self.assertEqual(bdst, [0x3FFF, 0x3FFF])  # byte 3 lands after byte 2 was refused

    def test_tdd_cross_refuses_first_then_lands(self):
        dests, refusals, bsrc, bdst, *_ = M.CROSS["tdd"]
        self.assertEqual(dests[0], 0x4000)
        self.assertEqual(bdst, [0x3FFF, 0x3FFE])


class ClassifierTest(unittest.TestCase):
    def test_cross_armed_accepts_synthetic_rows(self):
        for op in M.OPS:
            M.check_cross_armed(synthetic_cross_row(op), f"{op}-cross-armed")

    def test_cross_unguarded_accepts_synthetic_rows(self):
        for op in M.OPS:
            M.check_cross_unguarded(synthetic_cross_row(op, armed=False), f"{op}-cross-unguarded")

    def test_srcread_accepts_synthetic_rows(self):
        for op in M.OPS:
            M.check_srcread_ok(synthetic_srcread_row(op, forbidden=False), f"{op}-srcread-armed")
            M.check_srcread_unguarded(synthetic_srcread_row(op, forbidden=False, armed=False),
                                      f"{op}-srcread-unguarded")
            M.check_srcreadff_armed(synthetic_srcread_row(op, forbidden=True), f"{op}-srcreadff-armed")
            M.check_srcreadff_unguarded(synthetic_srcread_row(op, forbidden=True, armed=False),
                                        f"{op}-srcreadff-unguarded")

    def test_cross_armed_rejects_falsified_rows(self):
        row = synthetic_cross_row("tii")
        for field, value in (("guard_refusals", 0), ("byte_dst", [0x3FFE]),
                             ("wram_3fff", 0), ("vdc_ar", [0, 1]), ("a_after", 0)):
            broken = copy.deepcopy(row)
            broken[field] = value
            with self.assertRaises(ValueError, msg=f"{field} must be rejected"):
                M.check_cross_armed(broken, "tii-cross-armed")

    def test_srcread_rejects_status_not_cleared(self):
        row = synthetic_srcread_row("tin", forbidden=False)
        row["vdc_sr"] = [M.SR_PRESET, M.SR_PRESET]
        with self.assertRaises(ValueError):
            M.check_srcread_ok(row, "tin-srcread-armed")

    def test_srcreadff_rejects_status_cleared_despite_refusal(self):
        row = synthetic_srcread_row("tai", forbidden=True)
        row["vdc_sr"] = [M.SR_PRESET, 0]
        with self.assertRaises(ValueError):
            M.check_srcreadff_armed(row, "tai-srcreadff-armed")

    def test_mutation_suite_detects_every_mutation(self):
        # The driver's own mutation_test must pass on every armed synthetic row: each falsified
        # field is detected by the purpose assertion (ValueError), none is silently accepted.
        for op in M.OPS:
            M.mutation_test(synthetic_cross_row(op), f"{op}-cross-armed")
            M.mutation_test(synthetic_srcread_row(op, forbidden=False), f"{op}-srcread-armed")
            M.mutation_test(synthetic_srcread_row(op, forbidden=True), f"{op}-srcreadff-armed")

    def test_mutation_suite_detects_every_unguarded_mutation(self):
        # The RED (unguarded) classifiers get the same proof: every falsified field is rejected.
        for op in M.OPS:
            M.mutation_test_unguarded(synthetic_cross_row(op, armed=False), f"{op}-cross-unguarded")
            M.mutation_test_unguarded(synthetic_srcread_row(op, forbidden=False, armed=False),
                                      f"{op}-srcread-unguarded")
            M.mutation_test_unguarded(synthetic_srcread_row(op, forbidden=True, armed=False),
                                      f"{op}-srcreadff-unguarded")

    def test_no_assert_in_driver(self):
        src = Path(M.__file__).read_text(encoding="utf-8")
        self.assertNotIn("\n    assert ", src)
        self.assertNotIn("\nassert ", src)

    def test_fail_closed_under_python_O(self):
        import subprocess
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'tests' / 'integration')!r})\n"
            "import geargrafx_block_midrefusal as M\n"
            "row = {'exit': 0, 'mode': 'tii-cross-armed', 'status': 'ok', 'armed': True, 'stop_pc': 0, 'expected_end_pc': 7687}\n"
            "try:\n"
            "    M.check_cross_armed(row, 'tii-cross-armed')\n"
            "except ValueError:\n"
            "    sys.exit(3)\n"
            "sys.exit(0)\n"
        )
        proc = subprocess.run([sys.executable, "-O", "-c", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 3, proc.stderr)


if __name__ == "__main__":
    unittest.main()
