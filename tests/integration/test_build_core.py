"""The built Geargrafx core must exist. It needs `make setup` (Geargrafx fetched and built), so it is
an integration check and not part of the host stage (Issue #333). Moved from tests/host/test_build.py."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.environment import core_path


class BuildCoreTests(unittest.TestCase):
    def test_core_present(self):
        self.assertTrue(core_path().is_file())


if __name__ == "__main__":
    unittest.main()
