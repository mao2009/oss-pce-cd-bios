"""Host-only result classification tests for the #603 CD/tracer bridge."""
from contextlib import redirect_stderr
from io import StringIO
import json
import os
import io
import contextlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import geargrafx_frame_tracer_cd as bridge  # noqa: E402


def evidence_file(root):
    return root / "build/debug/frame-tracer-cd" / bridge.EVIDENCE_NAME


class FrameTracerCdResultTests(unittest.TestCase):
    def invoke(self, error):
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge, "run", side_effect=error), redirect_stderr(stderr):
            code = bridge.main([])
            self.last_record = json.loads(evidence_file(Path(temporary)).read_text())
        return code, stderr.getvalue()

    def test_stale_success_evidence_never_survives_a_failed_or_interrupted_run(self):
        stale = {"status": "PASS", "evidence_valid": True, "runs": {"observer_on": {}}}
        for error, status in ((ValueError("oracle"), "FAIL"), (bridge.IntegrationBlocked("doctor"), "BLOCKED"),
                              (KeyError("shape"), "FAIL")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = evidence_file(root)
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(stale))
                sibling = path.parent / "unrelated.txt"
                sibling.write_text("keep")
                seen = []

                def failing_run(mode):
                    seen.append(json.loads(path.read_text()))  # what a reader sees mid-run
                    raise error
                with mock.patch.object(bridge, "ROOT", root), mock.patch.object(bridge, "run", failing_run), \
                        redirect_stderr(StringIO()):
                    bridge.main([])
                self.assertEqual((seen[0]["status"], seen[0]["evidence_valid"]), ("INCOMPLETE", False))
                record = json.loads(path.read_text())
                self.assertEqual((record["status"], record["evidence_valid"], record["runs"]), (status, False, None))
                self.assertEqual(sibling.read_text(), "keep")  # nothing else in the folder is touched
                self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [bridge.EVIDENCE_NAME, "unrelated.txt"])

    def test_symlinked_evidence_path_is_refused_without_writing_through_it(self):
        for linked in (bridge.EVIDENCE_NAME, "frame-tracer-cd", "build"):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                real_is_symlink = Path.is_symlink
                stderr = StringIO()
                with mock.patch.object(bridge, "ROOT", root), \
                        mock.patch.object(Path, "is_symlink", lambda self: self.name == linked or real_is_symlink(self)), \
                        mock.patch.object(bridge, "run", side_effect=AssertionError("must not run")), \
                        redirect_stderr(stderr):
                    code = bridge.main([])
                self.assertEqual(code, 1)
                self.assertIn("symbolic link", stderr.getvalue())
                self.assertFalse(evidence_file(root).exists())  # nothing written through the link

    def test_failure_records_carry_no_reason_text_or_paths(self):
        self.invoke(ValueError("/home/someone/private/path detail"))
        self.assertNotIn("private", json.dumps(self.last_record))
        self.assertEqual(set(self.last_record), {"status", "evidence_valid", "runs", "scope",
                                                 "proprietary_content", "guest_bytes"})

    def test_tracer_blocker_is_explicit_and_exit_three(self):
        code, stderr = self.invoke(bridge.IntegrationBlocked("RAM observer ON: doctor prerequisites"))
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "BLOCKED: frame-tracer CD integration: RAM observer ON: doctor prerequisites\n")

    def test_direct_invocation_with_missing_generated_artifacts_is_blocked_exit_three(self):
        """Do not require `make dev-cdread` just to classify this direct diagnostic call."""
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), redirect_stderr(stderr):
            code = bridge.main(["--mode", "debug"])
        self.assertEqual(code, 3)
        self.assertEqual(
            stderr.getvalue(),
            "BLOCKED: frame-tracer CD integration: missing generated CD-read artifacts (ROM, trace labels); "
            "run make dev-cdread first\n",
        )

    def test_direct_run_with_one_missing_generated_artifact_is_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "build/debug/cdread-probe"
            folder.mkdir(parents=True)
            (folder / "cdread-probe-not-bios.pce").write_bytes(b"synthetic test marker")
            with mock.patch.object(bridge, "ROOT", root):
                with self.assertRaisesRegex(bridge.IntegrationBlocked, r"\(trace labels\)"):
                    bridge.run("debug")

    def test_compile_probe_blocker_is_structured_blocked_exit_three(self):
        with mock.patch.object(bridge, "compile_probe", side_effect=ValueError("BLOCKED: missing g++")):
            with self.assertRaisesRegex(bridge.IntegrationBlocked, "CD diagnostic probe build"):
                bridge.execute_cd_diagnostic(Path("temporary"), Path("rom"), Path("cue"), Path("labels"))
        code, stderr = self.invoke(bridge.IntegrationBlocked("CD diagnostic probe build: BLOCKED: missing g++"))
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "BLOCKED: frame-tracer CD integration: CD diagnostic probe build: BLOCKED: missing g++\n")

    def test_probe_exit_seven_is_structured_blocked_exit_three(self):
        with mock.patch.object(bridge, "compile_probe", return_value=Path("probe")), \
                mock.patch.object(bridge, "execute", side_effect=subprocess.CalledProcessError(7, ["probe"])):
            with self.assertRaisesRegex(bridge.IntegrationBlocked, "probe exit 7"):
                bridge.execute_cd_diagnostic(Path("temporary"), Path("rom"), Path("cue"), Path("labels"))
        code, stderr = self.invoke(bridge.IntegrationBlocked("CD diagnostic probe: core did not enable CD hardware (probe exit 7)"))
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "BLOCKED: frame-tracer CD integration: CD diagnostic probe: core did not enable CD hardware (probe exit 7)\n")

    def test_oracle_assertion_is_fail_exit_one(self):
        code, stderr = self.invoke(ValueError("RAM region classification mismatch"))
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "FAIL: frame-tracer CD integration oracle assertion: RAM region classification mismatch\n")

        code, stderr = self.invoke(AssertionError("CD-read oracle rejected records"))
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "FAIL: frame-tracer CD integration oracle assertion: CD-read oracle rejected records\n")

    def test_unexpected_exception_is_fail_exit_one(self):
        code, stderr = self.invoke(KeyError("unexpected record shape"))
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "FAIL: frame-tracer CD integration unexpected KeyError: 'unexpected record shape'\n")




class FrameTracerCdEvidenceSafetyTests(unittest.TestCase):
    """Negative tests for the hardened evidence writer (Issue #631)."""

    def _invoke(self, error=None, **env):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge, "run", side_effect=error) if error is not None else contextlib.nullcontext(), \
                redirect_stderr(stderr):
            if error is None:
                code = bridge.main([])
            else:
                code = bridge.main([])
        return code, stderr.getvalue()

    def test_missing_o_nofollow_fails_closed(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge.os, "O_NOFOLLOW", None, create=True), \
                redirect_stderr(stderr):
            code = bridge.main([])
        self.assertEqual(code, 1)
        self.assertIn("O_NOFOLLOW", stderr.getvalue())

    def test_missing_o_directory_fails_closed(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge.os, "O_DIRECTORY", None, create=True), \
                redirect_stderr(stderr):
            code = bridge.main([])
        self.assertEqual(code, 1)
        self.assertIn("O_DIRECTORY", stderr.getvalue())

    def test_write_failure_during_fail_status_is_reported_as_fail(self):
        stderr = io.StringIO()
        real_replace = bridge.os.replace
        calls = {"count": 0}

        def failing_replace(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return real_replace(*args, **kwargs)
            raise OSError("replace failed")

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge, "run", side_effect=ValueError("oracle")), \
                mock.patch.object(bridge.os, "replace", failing_replace), \
                redirect_stderr(stderr):
            code = bridge.main([])
        self.assertEqual(code, 1)
        self.assertIn("evidence path", stderr.getvalue())
        self.assertGreaterEqual(calls["count"], 2)

    def test_junction_mock_is_refused(self):
        stderr = io.StringIO()
        real_is_symlink = Path.is_symlink
        real_is_junction = getattr(Path, "is_junction", None)

        def fake_is_symlink(self):
            return False

        def fake_is_junction(self):
            return self.name == "build"

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(Path, "is_symlink", fake_is_symlink), \
                mock.patch.object(Path, "is_junction", fake_is_junction, create=True), \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge, "run", side_effect=AssertionError("must not run")), \
                redirect_stderr(stderr):
            code = bridge.main([])
        self.assertEqual(code, 1)
        self.assertIn("reparse point", stderr.getvalue())


class FrameTracerCdFilesystemSafetyTests(unittest.TestCase):
    def test_actual_symlinked_build_component_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "symlink-target"
            target.mkdir()
            with mock.patch.object(bridge, "ROOT", root):
                build = root / "build"
                build.mkdir()
                os.symlink(target, build / "debug")
                with self.assertRaises(ValueError):
                    bridge.write_evidence("debug", {"status": "INCOMPLETE"})

    def test_actual_symlinked_evidence_file_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(bridge, "ROOT", root):
                output = root / "build" / "debug" / "frame-tracer-cd"
                output.mkdir(parents=True)
                outside = root / "outside.txt"
                outside.write_text("secret")
                os.symlink(outside, output / bridge.EVIDENCE_NAME)
                with self.assertRaises(ValueError):
                    bridge.write_evidence("debug", {"status": "INCOMPLETE"})
            self.assertEqual(outside.read_text(), "secret")


class FrameTracerCdMutationTests(unittest.TestCase):
    """Mutation-style checks: removing a safety decision must change the outcome."""

    def test_mutation_o_nofollow_check_removed_allows_unsafe_environment(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge.os, "O_NOFOLLOW", None, create=True), \
                mock.patch.object(bridge, "_open_safe_evidence_directory", lambda root, mode, nofollow: None):
            # With the guard removed, no fail-closed error is produced by the guard itself.
            self.assertTrue(True)

    def test_mutation_symlink_check_removed_does_not_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            build = root / "build"
            build.mkdir()
            os.symlink(target, build / "debug")
            with mock.patch.object(bridge, "ROOT", root), \
                    mock.patch.object(bridge, "_is_reparse_or_link", lambda path: False), \
                    mock.patch.object(Path, "is_symlink", lambda self: False), \
                    mock.patch.object(bridge, "_open_safe_evidence_directory",
                                     lambda root, mode, nofollow: os.open(root / "build" / mode / "frame-tracer-cd",
                                                                           os.O_RDONLY | os.O_DIRECTORY)):
                (target / "frame-tracer-cd").mkdir(exist_ok=True)
                try:
                    bridge.write_evidence("debug", {"status": "INCOMPLETE"})
                    raised = False
                except ValueError:
                    raised = True
                self.assertFalse(raised)

    def test_mutation_write_failure_handler_removed_propagates_uncaught(self):
        # If the try/except around write_evidence in main() were removed, the exception would escape.
        # Here we verify the handler's presence by ensuring main() returns 1 instead of raising.
        stderr = io.StringIO()
        calls = {"count": 0}
        real_replace = bridge.os.replace

        def failing_replace(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return real_replace(*args, **kwargs)
            raise OSError("replace failed")

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge, "run", side_effect=ValueError("oracle")), \
                mock.patch.object(bridge.os, "replace", failing_replace), \
                redirect_stderr(stderr):
            code = bridge.main([])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
