"""Static guardrails for the synthetic RUN-input guest; no Geargrafx core runs here."""
from pathlib import Path
import unittest


class RunInputGuestFixtureTests(unittest.TestCase):
    def test_guest_uses_controller_io_and_only_its_named_result_block(self):
        source = (Path(__file__).resolve().parents[1] / "fixtures/run_input_guest.s").read_text()
        self.assertIn("sta $1000", source)
        self.assertIn("lda $1000", source)
        self.assertIn("$2200-$220F fixture-owned WRAM result block", source)
        self.assertNotIn("$180", source)

    def test_probe_uses_public_press_release_and_reports_result_only(self):
        source = (Path(__file__).resolve().parents[1] / "integration/geargrafx_run_input_guest.cpp").read_text()
        self.assertIn("core.KeyPressed(GG_CONTROLLER_1, GG_KEY_RUN)", source)
        self.assertIn("core.KeyReleased(GG_CONTROLLER_1, GG_KEY_RUN)", source)
        self.assertIn('"RESULT {', source)
        self.assertNotIn("RunInstruction(", source)


if __name__ == "__main__":
    unittest.main()
