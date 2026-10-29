"""Unit tests for tools/geargrafx_slots.py (Issue #103). Slot logic only; no emulator is run."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import geargrafx_slots as gs  # noqa: E402


class SlotAllocator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_slots_distinct_then_blocked(self):
        a = gs.acquire(self.state, "workerA")
        b = gs.acquire(self.state, "workerB")
        self.assertNotEqual(a["slot"], b["slot"])
        self.assertNotEqual(a["profile"], b["profile"])
        self.assertNotEqual(a["port"], b["port"])
        self.assertEqual({a["port"], b["port"]}, {gs.BASE_PORT, gs.BASE_PORT + 1})
        with self.assertRaises(gs.SlotError):
            gs.acquire(self.state, "workerC")

    def test_release_frees_slot_and_only_owner_may_release(self):
        a = gs.acquire(self.state, "workerA")
        gs.acquire(self.state, "workerB")
        with self.assertRaises(gs.SlotError):
            gs.release(self.state, a["slot"], "workerB")
        gs.release(self.state, a["slot"], "workerA")
        c = gs.acquire(self.state, "workerC")
        self.assertEqual(c["slot"], a["slot"])

    def test_release_unheld_slot_fails(self):
        with self.assertRaises(gs.SlotError):
            gs.release(self.state, 0, "workerA")

    def test_force_release_by_operator(self):
        a = gs.acquire(self.state, "workerA")
        gs.release(self.state, a["slot"], "operator", force=True)
        self.assertFalse(Path(a["lock"]).exists())

    def test_lock_records_owner(self):
        a = gs.acquire(self.state, "workerA")
        self.assertEqual(json.loads(Path(a["lock"]).read_text(encoding="utf-8"))["owner"], "workerA")

    def test_state_dir_inside_repo_is_refused(self):
        with self.assertRaises(gs.SlotError):
            gs.acquire(gs.REPO_ROOT / "tmp-should-not-exist", "workerA")
        self.assertFalse((gs.REPO_ROOT / "tmp-should-not-exist").exists())

    def test_empty_lock_recovered_only_by_force(self):
        self.state.mkdir(parents=True)
        lock = self.state / "slot-0.lock"
        lock.write_text("", encoding="utf-8")  # crash between O_EXCL create and json write
        with self.assertRaises(gs.SlotError):
            gs.release(self.state, 0, "workerA")
        self.assertTrue(lock.exists())
        gs.release(self.state, 0, "operator", force=True)
        self.assertFalse(lock.exists())

    def test_empty_owner_refused(self):
        with self.assertRaises(gs.SlotError):
            gs.acquire(self.state, "")

    def test_cli_blocked_exit_code(self):
        self.assertEqual(gs.main(["acquire", "--state-dir", str(self.state), "--owner", "a"]), 0)
        self.assertEqual(gs.main(["acquire", "--state-dir", str(self.state), "--owner", "b"]), 0)
        self.assertEqual(gs.main(["acquire", "--state-dir", str(self.state), "--owner", "c"]), 3)


if __name__ == "__main__":
    unittest.main()
