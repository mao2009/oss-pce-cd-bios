"""Unit tests for tools/geargrafx_slots.py (Issue #103). Slot logic only; no emulator is run."""
import json
import os
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
        held = gs.acquire(self.state, "workerA")
        lock = Path(held["lock"])
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


    def test_force_does_not_delete_unmarked_unrelated_lock(self):
        self.state.mkdir(parents=True)
        unrelated = self.state / "slot-0.lock"
        unrelated.write_text("other-worker", encoding="utf-8")
        with self.assertRaisesRegex(gs.SlotError, "marker"):
            gs.release(self.state, 0, "operator", force=True)
        self.assertEqual(unrelated.read_text(), "other-worker")
        with self.assertRaisesRegex(gs.SlotError, "not empty"):
            gs.acquire(self.state, "workerA")

    def test_home_project_root_and_parent_are_never_slot_dirs(self):
        for forbidden in (Path.home(), Path("/"), gs.REPO_ROOT, gs.REPO_ROOT.parent):
            with self.subTest(path=str(forbidden)):
                with self.assertRaises(gs.SlotError):
                    gs.acquire(forbidden, "workerA")

    def test_relative_and_symlinked_state_dirs_are_rejected(self):
        with self.assertRaisesRegex(gs.SlotError, "absolute"):
            gs.acquire("relative-slots", "workerA")
        gs.acquire(self.state, "workerA")
        linked = Path(self.tmp.name) / "alias"
        linked.symlink_to(self.state, target_is_directory=True)
        with self.assertRaisesRegex(gs.SlotError, "canonical"):
            gs.release(linked, 0, "operator", force=True)
        self.assertTrue((self.state / "slot-0.lock").exists())

    def test_slot_out_of_range_never_unlinks_any_lock(self):
        held = gs.acquire(self.state, "workerA")
        for invalid in (-1, gs.MAX_SLOTS, 100, True, "0"):
            with self.subTest(slot=invalid):
                with self.assertRaisesRegex(gs.SlotError, "range"):
                    gs.release(self.state, invalid, "operator", force=True)
        self.assertTrue(Path(held["lock"]).exists())

    def test_force_refuses_symlink_lock_without_touching_target(self):
        gs.acquire(self.state, "workerA")
        lock = self.state / "slot-0.lock"
        lock.unlink()  # isolated fixture directory only
        target = Path(self.tmp.name) / "unrelated"
        target.write_text("do not delete", encoding="utf-8")
        lock.symlink_to(target)
        with self.assertRaises(gs.SlotError):
            gs.release(self.state, 0, "operator", force=True)
        self.assertEqual(target.read_text(), "do not delete")

    def test_force_refuses_hardlinked_lock_without_touching_other_path(self):
        held = gs.acquire(self.state, "workerA")
        lock = Path(held["lock"])
        other = Path(self.tmp.name) / "unrelated-hardlink"
        os.link(lock, other)
        with self.assertRaisesRegex(gs.SlotError, "single-link"):
            gs.release(self.state, 0, "operator", force=True)
        self.assertTrue(lock.exists())
        self.assertTrue(other.exists())

    def test_releasing_missing_state_dir_does_not_create_it(self):
        with self.assertRaises(gs.SlotError):
            gs.release(self.state, 0, "operator", force=True)
        self.assertFalse(self.state.exists())

    def test_invalid_marker_refuses_force_release(self):
        held = gs.acquire(self.state, "workerA")
        (self.state / gs.STATE_MARKER).write_text("wrong-project", encoding="utf-8")
        with self.assertRaisesRegex(gs.SlotError, "marker"):
            gs.release(self.state, held["slot"], "operator", force=True)
        self.assertTrue(Path(held["lock"]).exists())


if __name__ == "__main__":
    unittest.main()
