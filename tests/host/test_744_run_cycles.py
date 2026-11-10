"""Issue #744 MEDIUM-2: cold/warm cycle multiset (host-only).

require_cold_warm_cycles() (tests/integration/geargrafx_ipl_loader.py) is the
shared gate used by judge() (all 63 cases) and the norange mutant kill: exactly
one "cold" and one "warm" RUN record, in any order. A bare count of 2 would
accept cold+cold, so the multiset -- not the length -- is pinned here.

No emulator, no ROM, no core: pure observation-dict negatives.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from geargrafx_ipl_loader import require_cold_warm_cycles  # noqa: E402


def recs(*cycles):
    return {"RUN": [{"cycle": c} for c in cycles]}


class ColdWarmCycleTest(unittest.TestCase):
    def test_cold_warm_passes(self):
        self.assertEqual(len(require_cold_warm_cycles(recs("cold", "warm"), "t")), 2)

    def test_warm_cold_order_passes(self):
        self.assertEqual(len(require_cold_warm_cycles(recs("warm", "cold"), "t")), 2)

    def test_duplicate_cycles_rejected(self):
        for cycles in (("cold", "cold"), ("warm", "warm")):
            with self.subTest(cycles=cycles):
                with self.assertRaises(ValueError):
                    require_cold_warm_cycles(recs(*cycles), "t")

    def test_single_run_rejected(self):
        for cycles in (("cold",), ("warm",)):
            with self.subTest(cycles=cycles):
                with self.assertRaises(ValueError):
                    require_cold_warm_cycles(recs(*cycles), "t")

    def test_three_runs_rejected(self):
        with self.assertRaises(ValueError):
            require_cold_warm_cycles(recs("cold", "warm", "cold"), "t")

    def test_unknown_cycle_rejected(self):
        for cycles in (("cold", "unknown"), ("warm", "hot"), ("cold", "COLD")):
            with self.subTest(cycles=cycles):
                with self.assertRaises(ValueError):
                    require_cold_warm_cycles(recs(*cycles), "t")

    def test_missing_cycle_rejected(self):
        with self.assertRaises(ValueError):
            require_cold_warm_cycles({"RUN": [{}, {"cycle": "warm"}]}, "t")

    def test_non_string_cycle_rejected(self):
        for bad in (None, 0, 1, True, ["cold"], {"cycle": "cold"}):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    require_cold_warm_cycles({"RUN": [{"cycle": bad}, {"cycle": "warm"}]}, "t")

    def test_missing_run_key_rejected(self):
        for records in ({}, {"RUN": None}, {"RUN": "cold"}, {"RUN": [{"cycle": "cold"}]},
                         "RUN", None, []):
            with self.subTest(records=repr(records)):
                with self.assertRaises(ValueError):
                    require_cold_warm_cycles(records, "t")

    def test_malformed_record_rejected(self):
        with self.assertRaises(ValueError):
            require_cold_warm_cycles({"RUN": ["cold", {"cycle": "warm"}]}, "t")


if __name__ == "__main__":
    unittest.main()
