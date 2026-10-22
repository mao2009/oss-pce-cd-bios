"""describe_image / reproduce_static_triage: identity, TOCTOU snapshot, strict SHA, projection compare (Issues #476).

Synthetic bytes and a fake auditor only. Real-reference-ROM runs are NOT_RUN here (the ROM is private, absent from CI).
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import reproduce_static_triage  # noqa: E402
from reproduce_static_triage import ROOT, compare_projection, describe_image, main  # noqa: E402


class DescribeImage(unittest.TestCase):
    def test_headerless_and_headered_share_payload_facts(self):
        body = bytes(range(256)) * 1024
        a, b = describe_image(body), describe_image(b"\0" * 512 + body)
        self.assertEqual((a["header_bytes"], b["header_bytes"]), (0, 512))
        self.assertEqual(b["header_kind"], "headered")
        self.assertEqual(a["sha256_payload"], b["sha256_payload"])
        self.assertEqual(a["crc32_payload"], b["crc32_payload"])
        self.assertNotEqual(a["sha256_whole_file"], b["sha256_whole_file"])

    def test_odd_size_unsupported(self):
        self.assertEqual(describe_image(b"x" * 100)["header_kind"], "UNSUPPORTED_SIZE")

    def test_exact_size_gate(self):
        for n in (0, 8192, 524288):
            self.assertEqual(describe_image(b"x" * n)["header_kind"], "UNSUPPORTED_SIZE")

    def test_missing_image_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(main(["--rom", str(Path(t) / "none")]), 2)


def callee(**extra):
    base = {"instructions_decoded": 5, "states_visited": 10, "non_returning_states": 2, "unresolved": []}
    base.update(extra)
    return base


def projection(*callees):
    return {"schema": "s", "apis": [{"api_id": f"slot-{i:02d}", "callee": c} for i, c in enumerate(callees)]}


class Harness(unittest.TestCase):
    """main() with a fake auditor."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.rom = self.dir / "rom.bin"
        self.data = bytes(range(256)) * 1024
        self.rom.write_bytes(self.data)
        self.seen = {}

    def run_main(self, fresh, committed, args=None, before=None, crash=None):
        com = self.dir / "committed.json"
        com.write_text(json.dumps(committed), encoding="utf-8")

        def auditor(cmd, **kw):
            snap = Path(cmd[cmd.index("--rom") + 1])
            self.seen["snap"] = snap
            if before:
                before()
            if crash:
                raise crash
            self.seen["content"] = snap.read_bytes()
            self.seen["mode"] = snap.stat().st_mode & 0o777
            Path(cmd[cmd.index("--output") + 1]).write_text(json.dumps(fresh), encoding="utf-8")
            return mock.Mock(returncode=0, stderr="")

        with mock.patch.object(reproduce_static_triage, "COMMITTED", com), \
                mock.patch("reproduce_static_triage.subprocess.run", side_effect=auditor) as run:
            rc = main(["--rom", str(self.rom)] + (args or []))
        self.seen["run"] = run
        return rc

    def test_exact_match_unverified_identity(self):
        pr = projection(callee(), callee())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.run_main(pr, pr), 0)
        self.assertIn("UNVERIFIED", out.getvalue())
        self.assertNotIn("UNBASELINED", out.getvalue())

    def test_headered_and_headerless_accepted(self):
        pr = projection(callee())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.run_main(pr, pr), 0)
            self.rom.write_bytes(b"\0" * 512 + self.data)
            self.assertEqual(self.run_main(pr, pr), 0)

    def test_size_mismatch_blocked_before_auditor(self):
        self.rom.write_bytes(b"x" * 1000)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_main(projection(), projection()), 2)
        self.seen["run"].assert_not_called()

    def test_sha_mismatch_empty_malformed_fail_before_auditor(self):
        for v in ("0" * 64, "", "   ", "ab" * 31, "ab" * 33, "g" * 64, "0x" + "a" * 62):
            with self.subTest(v=v), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.run_main(projection(), projection(), ["--expect-sha256", v]), 1)
                self.seen["run"].assert_not_called()

    def test_sha_match_case_and_whitespace_ok_but_not_canonical(self):
        good = hashlib.sha256(self.data).hexdigest()
        pr = projection(callee())
        for v in (good, good.upper(), " " + good + "\n"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.run_main(pr, pr, ["--expect-sha256", v]), 0)
            self.assertNotIn("UNVERIFIED", out.getvalue())
            self.assertIn("not canonical", out.getvalue())

    def test_original_modified_after_check_auditor_reads_snapshot(self):
        good = hashlib.sha256(self.data).hexdigest()
        pr = projection(callee())
        with contextlib.redirect_stdout(io.StringIO()):
            rc = self.run_main(pr, pr, ["--expect-sha256", good],
                               before=lambda: self.rom.write_bytes(b"tampered" * 32768))
        self.assertEqual(rc, 0)
        self.assertEqual(self.seen["content"], self.data)
        self.assertNotEqual(self.seen["snap"], self.rom)
        self.assertEqual(hashlib.sha256(self.seen["content"]).hexdigest(), good)

    def test_snapshot_private_and_deleted(self):
        pr = projection(callee())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.run_main(pr, pr), 0)
        snap = self.seen["snap"]
        self.assertFalse(snap.exists())
        self.assertFalse(snap.parent.exists())
        self.assertNotIn(str(ROOT), str(snap))
        if os.name == "posix":
            self.assertEqual(self.seen["mode"], 0o600)

    def test_snapshot_deleted_on_mismatch_and_crash(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_main(projection(callee()), projection(callee(instructions_decoded=6))), 1)
            self.assertFalse(self.seen["snap"].parent.exists())
            with self.assertRaises(RuntimeError):  # auditor crash
                self.run_main(projection(), projection(), crash=RuntimeError("boom"))
            self.assertFalse(self.seen["snap"].parent.exists())

    def test_auditor_nonzero_exit_fails(self):
        com = self.dir / "c.json"
        com.write_text("{}")
        with mock.patch.object(reproduce_static_triage, "COMMITTED", com), \
                mock.patch("reproduce_static_triage.subprocess.run",
                           return_value=mock.Mock(returncode=7, stderr="x")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--rom", str(self.rom)]), 1)

    def test_only_new_metric_added_is_unbaselined_not_verified(self):
        old = projection(callee(), callee())
        new = projection(callee(states_without_guaranteed_return=4), callee(states_without_guaranteed_return=10))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(self.run_main(new, old), 3)
        self.assertIn("UNBASELINED: states_without_guaranteed_return", err.getvalue())
        self.assertNotIn("PASS", out.getvalue())
        self.assertEqual(compare_projection(new, old), (True, "", ["states_without_guaranteed_return"]))


    def test_snapshot_not_deleted_is_loud_and_nonzero(self):
        pr = projection(callee())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),                 mock.patch("reproduce_static_triage.shutil.rmtree", side_effect=PermissionError("locked")):
            rc = self.run_main(pr, pr)
        self.assertEqual(rc, 1)
        self.assertIn("SNAPSHOT NOT DELETED: " + str(self.seen["snap"].parent), err.getvalue())
        shutil.rmtree(self.seen["snap"].parent)

    def test_size_checked_before_read(self):
        self.rom.write_bytes(b"x" * 1000)
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read")),                 contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--rom", str(self.rom)]), 2)

    def test_auditor_stderr_truncated(self):
        com = self.dir / "c.json"
        com.write_text("{}")
        err = io.StringIO()
        with mock.patch.object(reproduce_static_triage, "COMMITTED", com),                 mock.patch("reproduce_static_triage.subprocess.run",
                           return_value=mock.Mock(returncode=7, stderr="z" * 5000)),                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(main(["--rom", str(self.rom)]), 1)
        self.assertLess(len(err.getvalue()), 700)


class CompareProjection(unittest.TestCase):
    def test_type_strict_equality(self):
        for committed, fresh in ((True, 1), (1, True), (4.0, 4), (4, 4.0), (0, False)):
            with self.subTest(c=committed, f=fresh):
                self.assertFalse(compare_projection(projection(callee(flag=fresh)),
                                                    projection(callee(flag=committed)))[0])
        self.assertTrue(compare_projection(projection(callee(flag=4.0)), projection(callee(flag=4.0)))[0])

    def test_non_int_floor_fails_closed(self):
        old = projection(callee())
        for bad in ("2", None, 2.0):
            new = projection(callee(non_returning_states=bad, states_without_guaranteed_return=4))
            self.assertFalse(compare_projection(new, old)[0])
        new = projection(callee(states_without_guaranteed_return=4))
        new["apis"][0]["callee"]["states_visited"] = "10"
        self.assertFalse(compare_projection(new, old)[0])

    def test_new_metric_present_in_both_is_compared_strictly(self):
        a = projection(callee(states_without_guaranteed_return=4))
        b = projection(callee(states_without_guaranteed_return=5))
        self.assertFalse(compare_projection(a, b)[0])
        self.assertEqual(compare_projection(a, a), (True, "", []))

    def test_existing_metric_changed_fails_even_with_new_metric(self):
        old = projection(callee())
        new = projection(callee(instructions_decoded=6, states_without_guaranteed_return=4))
        self.assertFalse(compare_projection(new, old)[0])

    def test_unknown_field_fails(self):
        old = projection(callee())
        self.assertFalse(compare_projection(projection(callee(surprise=1)), old)[0])
        top = projection(callee())
        top["extra"] = 1
        self.assertFalse(compare_projection(top, old)[0])

    def test_new_metric_invalid_fails(self):
        old = projection(callee())
        for v in (-1, 11, 1, True, "4", 4.0, None):  # 1 < non_returning_states (2)
            with self.subTest(v=v):
                new = projection(callee(states_without_guaranteed_return=v))
                self.assertFalse(compare_projection(new, old)[0])

    def test_removed_field_and_structure_fail(self):
        old = projection(callee(states_without_guaranteed_return=4))
        self.assertFalse(compare_projection(projection(callee()), old)[0])
        self.assertFalse(compare_projection(projection(callee(), callee()), projection(callee()))[0])

    def test_new_metric_wrong_scope_fails(self):
        new = projection(callee())
        new["apis"][0]["states_without_guaranteed_return"] = 3
        self.assertFalse(compare_projection(new, projection(callee()))[0])


if __name__ == "__main__":
    unittest.main()
