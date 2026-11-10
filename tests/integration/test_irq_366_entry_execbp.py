"""Issue #366 acceptance: EXECUTE-breakpoint stop at the IRQ handler entry.

Real-core test (pinned Geargrafx debugger build, synthetic media only).
Runs in the required integration gate via `make integration-test` discovery.
For each of IRQ2/IRQ1/TIQ: the armed probe stops at the resolved handler with
zero handler instructions and page-1-only stack writes (judge PASS); every RED
variant is positively verified (acceptance at the handler, expected breakpoint
registration, one executed ST0 with VDC AR $00->$07, substantive armed
rejection); every classifier/judge falsification is rejected; libretro stays
BLOCKED. No reference BIOS, no commercial media, no ABI change.
BLOCKED_ABI stays, release_eligible=false.
"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import geargrafx_irq_entry_execbp as driver  # noqa: E402
from geargrafx_reference_guard import synthetic_media  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402


class IrqEntryExecbpAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-irq366-execbp-")
        cls.addClassCleanup(cls._tmp.cleanup)
        tmpdir = Path(cls._tmp.name)
        cls.bios, cls.cue = synthetic_media(tmpdir)
        cls.probe = compile_probe(tmpdir, "geargrafx_irq_entry_execbp.cpp",
                                  "irq-entry-execbp")

    def test_armed_stop_at_handler_with_zero_instructions(self):
        for irq in driver.VECTORS:
            with self.subTest(vector=irq):
                driver.check(driver.run(self.probe, self.bios, self.cue, irq + "-armed"), irq)

    def test_red_variants_show_execution_side_effects(self):
        # verify_red succeeds ONLY on the measured RED observations (acceptance
        # at the handler, expected breakpoint registration, one executed ST0
        # with VDC AR $00->$07 and PC handler->handler+2, substantive armed
        # rejection). A normally-stopped or evidence-dropped RED FAILs here.
        for irq in driver.VECTORS:
            for red in driver.RED_VARIANTS:
                with self.subTest(case=f"{irq}-{red}"):
                    driver.verify_red(driver.run(self.probe, self.bios, self.cue,
                                                 irq + "-" + red), irq, red)

    def test_mutations_and_libretro_blocked(self):
        for irq in driver.VECTORS:
            with self.subTest(vector=irq):
                driver.mutation(driver.run(self.probe, self.bios, self.cue, irq + "-armed"), irq)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
