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




def _run_mutated_source(mutator, script):
    """Run a small script against a mutated copy of the evidence module."""
    import subprocess as _sp
    import sys as _sys
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        source_path = Path(__file__).resolve().parents[2] / "tests" / "integration" / "geargrafx_frame_tracer_cd.py"
        mutated = mutator(source_path.read_text())
        (temporary / "geargrafx_frame_tracer_cd.py").write_text(mutated)
        script_path = temporary / "mutant_check.py"
        script_path.write_text(script)
        integration_dir = str(Path(__file__).resolve().parents[1] / "integration")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(temporary) + os.pathsep + integration_dir
        return _sp.run([_sys.executable, str(script_path)], cwd=str(temporary), env=env,
                       capture_output=True, text=True, timeout=60)


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

    def test_dir_fd_unsupported_mkdir_fails_closed(self):
        stderr = io.StringIO()
        real_mkdir = bridge.os.mkdir

        def failing_mkdir(*args, **kwargs):
            if "dir_fd" in kwargs:
                raise NotImplementedError("dir_fd unsupported")
            return real_mkdir(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge.os, "mkdir", failing_mkdir), \
                redirect_stderr(stderr):
            code = bridge.main([])
        self.assertEqual(code, 1)
        self.assertIn("evidence path", stderr.getvalue())

    def test_dir_fd_unsupported_open_fails_closed(self):
        stderr = io.StringIO()
        real_open = bridge.os.open

        def failing_open(*args, **kwargs):
            if "dir_fd" in kwargs:
                raise TypeError("dir_fd unsupported")
            return real_open(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge.os, "open", failing_open), \
                redirect_stderr(stderr):
            code = bridge.main([])
        self.assertEqual(code, 1)
        self.assertIn("evidence path", stderr.getvalue())

    def test_dir_fd_unsupported_stat_fails_closed(self):
        stderr = io.StringIO()
        real_stat = bridge.os.stat

        def failing_stat(*args, **kwargs):
            if "dir_fd" in kwargs:
                raise NotImplementedError("dir_fd unsupported")
            return real_stat(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge.os, "stat", failing_stat), \
                redirect_stderr(stderr):
            code = bridge.main([])
        self.assertEqual(code, 1)
        self.assertIn("evidence path", stderr.getvalue())

    def test_dir_fd_unsupported_replace_fails_closed(self):
        stderr = io.StringIO()
        real_replace = bridge.os.replace

        def failing_replace(*args, **kwargs):
            if "src_dir_fd" in kwargs or "dst_dir_fd" in kwargs:
                raise TypeError("dir_fd unsupported")
            return real_replace(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(bridge, "ROOT", Path(temporary)), \
                mock.patch.object(bridge.os, "replace", failing_replace), \
                redirect_stderr(stderr):
            code = bridge.main([])
        self.assertEqual(code, 1)
        self.assertIn("evidence path", stderr.getvalue())

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

    def test_temp_name_collision_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(bridge, "ROOT", root):
                output = root / "build" / "debug" / "frame-tracer-cd"
                output.mkdir(parents=True)
                colliding = output / (".%s.aaaa.tmp" % bridge.EVIDENCE_NAME)
                colliding.write_text("keep-collision")
                with mock.patch.object(bridge.secrets, "token_hex", lambda n: "aaaa"):
                    with self.assertRaises(ValueError):
                        bridge.write_evidence("debug", {"status": "INCOMPLETE"})
                self.assertEqual(colliding.read_text(), "keep-collision")

    def test_fdopen_failure_closes_descriptor(self):
        real_fdopen = bridge.os.fdopen
        real_close = bridge.os.close
        closed = {"count": 0}

        def failing_fdopen(fd, *args, **kwargs):
            raise OSError("fdopen failed")

        def counting_close(fd):
            closed["count"] += 1
            return real_close(fd)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(bridge, "ROOT", root),                     mock.patch.object(bridge.os, "fdopen", failing_fdopen),                     mock.patch.object(bridge.os, "close", counting_close):
                with self.assertRaises(ValueError):
                    bridge.write_evidence("debug", {"status": "INCOMPLETE"})
        self.assertGreaterEqual(closed["count"], 1)

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
    """Mutation checks: disabling a safety decision changes the outcome.

    A single guard may be removed without making the write unsafe because the remaining
    layers still protect the boundary; that is expected and is documented per test.
    """

    def test_mutation_is_reparse_or_link_disabled_keeps_symlink_refusal(self):
        """Disabling _is_reparse_or_link alone must not bypass the symlink refusal."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            build = root / "build"
            build.mkdir()
            os.symlink(target, build / "debug")
            (target / "frame-tracer-cd").mkdir()

            with mock.patch.object(bridge, "ROOT", root), \
                    mock.patch.object(bridge, "_is_reparse_or_link", lambda path: False):
                with self.assertRaises(ValueError) as context:
                    bridge.write_evidence("debug", {"status": "INCOMPLETE"})
            self.assertIn("symbolic link", str(context.exception).lower())

    def test_mutation_path_is_symlink_disabled_keeps_reparse_refusal(self):
        """Disabling Path.is_symlink alone must not bypass reparse-point refusal."""
        real_is_reparse = bridge._is_reparse_or_link

        def fake_is_symlink(self):
            return False

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            build = root / "build"
            build.mkdir()
            os.symlink(target, build / "debug")
            (target / "frame-tracer-cd").mkdir()

            with mock.patch.object(Path, "is_symlink", fake_is_symlink), \
                    mock.patch.object(bridge, "ROOT", root):
                with self.assertRaises(ValueError):
                    bridge.write_evidence("debug", {"status": "INCOMPLETE"})

    def test_mutation_all_guards_disabled_allows_unsafe_write(self):
        """Disabling every layer at once removes protection; documents the layered defence."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            build = root / "build"
            build.mkdir()
            os.symlink(target, build / "debug")
            (target / "frame-tracer-cd").mkdir()

            def naive_open(root, mode, nofollow):
                current_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                for part in ("build", mode, "frame-tracer-cd"):
                    try:
                        os.mkdir(part, 0o755, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY, dir_fd=current_fd)
                    os.close(current_fd)
                    current_fd = next_fd
                return current_fd

            with mock.patch.object(bridge, "ROOT", root), \
                    mock.patch.object(bridge, "_is_reparse_or_link", lambda path: False), \
                    mock.patch.object(Path, "is_symlink", lambda self: False), \
                    mock.patch.object(bridge, "_open_safe_evidence_directory", naive_open):
                try:
                    bridge.write_evidence("debug", {"status": "INCOMPLETE"})
                    raised = False
                except ValueError:
                    raised = True
                self.assertFalse(raised)

    def test_mutation_o_nofollow_check_removed_is_caught_by_open(self):
        """Removing the explicit O_NOFOLLOW availability check still fails closed at open time."""
        def mutator(source):
            old = """    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("refusing to write frame-tracer evidence: O_NOFOLLOW is unavailable")
"""
            new = """    nofollow = getattr(os, "O_NOFOLLOW", None)
"""
            assert old in source
            return source.replace(old, new)

        script = """import os
from io import StringIO
from contextlib import redirect_stderr
import tempfile
from pathlib import Path
import geargrafx_frame_tracer_cd as bridge
# Force O_NOFOLLOW to be unavailable in the mutated module's os.
bridge.os.O_NOFOLLOW = None
stderr = StringIO()
with tempfile.TemporaryDirectory() as temporary, \
        __import__("unittest.mock", fromlist=["patch"]).patch.object(bridge, "ROOT", Path(temporary)), \
        redirect_stderr(stderr):
    try:
        bridge.write_evidence("debug", {"status": "INCOMPLETE"})
        print("RAISED=False")
    except Exception as exc:
        print("RAISED=" + type(exc).__name__ + ":" + str(exc))
"""
        result = _run_mutated_source(mutator, script)
        self.assertEqual(result.returncode, 0)
        self.assertIn("RAISED=", result.stdout)
        # Either ValueError from a downstream check or an AttributeError/TypeError is acceptable:
        # the point is that the mutation does not silently succeed.
        self.assertNotIn("RAISED=False", result.stdout)

    def test_mutation_mode_validation_removed_from_helper_only(self):
        def mutator(source):
            old = """    _validate_evidence_mode(mode)
    if nofollow is None:"""
            new = """    if nofollow is None:"""
            assert old in source
            return source.replace(old, new, 1)


        script = """import os
from pathlib import Path
import geargrafx_frame_tracer_cd as bridge
nofollow = getattr(os, "O_NOFOLLOW", None)
root = Path(__file__).resolve().parent if False else None
import tempfile
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    try:
        bridge._open_safe_evidence_directory(root, "..", nofollow)
        print("RAISED=False")
    except Exception as exc:
        print("RAISED=" + type(exc).__name__ + ":" + str(exc))
"""
        result = _run_mutated_source(mutator, script)
        self.assertEqual(result.returncode, 0)
        self.assertIn("RAISED=False", result.stdout)

    def test_mutation_mode_validation_removed_allows_traversal(self):
        def mutator(source):
            old = "    _validate_evidence_mode(mode)\n"
            assert old in source
            return source.replace(old, "")

        script = """import os
from io import StringIO
from contextlib import redirect_stderr
import tempfile
from pathlib import Path
import geargrafx_frame_tracer_cd as bridge
stderr = StringIO()
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(bridge, "ROOT", root), redirect_stderr(stderr):
        try:
            bridge.write_evidence("..", {"status": "INCOMPLETE"})
            print("RAISED=False")
        except Exception as exc:
            print("RAISED=" + type(exc).__name__ + ":" + str(exc))
    outside = root / "frame-tracer-cd" / bridge.EVIDENCE_NAME
    print("OUTSIDE_EXISTS=" + str(outside.exists()))
"""
        result = _run_mutated_source(mutator, script)
        self.assertEqual(result.returncode, 0)
        self.assertIn("RAISED=False", result.stdout)
        self.assertIn("OUTSIDE_EXISTS=True", result.stdout)

    def test_mutation_write_failure_handler_removed(self):
        def mutator(source):
            old = """        try:
            write_evidence(args.mode, status_record("FAIL"))
        except (OSError, ValueError) as write_error:
            print(f"FAIL: frame-tracer CD integration evidence path: {write_error}", file=sys.stderr)
            return 1
"""
            new = """        write_evidence(args.mode, status_record("FAIL"))
"""
            assert old in source
            return source.replace(old, new)

        script = """import os
from io import StringIO
from contextlib import redirect_stderr
import tempfile
from pathlib import Path
import geargrafx_frame_tracer_cd as bridge
stderr = StringIO()
calls = {"count": 0}
real_replace = bridge.os.replace
def failing_replace(*args, **kwargs):
    calls["count"] += 1
    if calls["count"] == 1:
        return real_replace(*args, **kwargs)
    raise OSError("replace failed")
with tempfile.TemporaryDirectory() as temporary, \
        __import__("unittest.mock", fromlist=["patch"]).patch.object(bridge, "ROOT", Path(temporary)), \
        __import__("unittest.mock", fromlist=["patch"]).patch.object(bridge, "run", side_effect=ValueError("oracle")), \
        __import__("unittest.mock", fromlist=["patch"]).patch.object(bridge.os, "replace", failing_replace), \
        redirect_stderr(stderr):
    code = bridge.main([])
print("RETURN_CODE=" + str(code))
print("STDERR=" + stderr.getvalue())
"""
        result = _run_mutated_source(mutator, script)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Traceback", result.stderr + result.stdout)


class FrameTracerCdModePathBoundaryTests(unittest.TestCase):
    """Negative tests for strict mode validation (MAJOR path boundary)."""

    INVALID_MODES = ("..", ".", "../outside", "/tmp", "debug/../release", "", 123, None,
                     [], "debug\\\\release", "C:\\\\tmp", object())

    def test_write_evidence_rejects_invalid_mode_without_side_effects(self):
        for mode in self.INVALID_MODES:
            with self.subTest(mode=repr(mode)):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    sentinel_dir = root / "build" / "debug" / "frame-tracer-cd"
                    sentinel_dir.mkdir(parents=True)
                    sentinel_file = sentinel_dir / bridge.EVIDENCE_NAME
                    sentinel_file.write_text("sentinel")
                    sibling = sentinel_dir / "unrelated.txt"
                    sibling.write_text("keep")
                    before = sorted(p.name for p in sentinel_dir.iterdir())
                    with mock.patch.object(bridge, "ROOT", root):
                        with self.assertRaises(ValueError):
                            bridge.write_evidence(mode, {"status": "INCOMPLETE"})
                    self.assertEqual(sentinel_file.read_text(), "sentinel")
                    self.assertEqual(sibling.read_text(), "keep")
                    self.assertEqual(sorted(p.name for p in sentinel_dir.iterdir()), before)
                    # No disallowed traversal directory was created at root/build siblings.
                    self.assertFalse((root / "outside").exists())

    def test_open_safe_evidence_directory_rejects_invalid_mode(self):
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            self.skipTest("O_NOFOLLOW unavailable")
        for mode in self.INVALID_MODES:
            with self.subTest(mode=repr(mode)):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    with self.assertRaises(ValueError):
                        bridge._open_safe_evidence_directory(root, mode, nofollow)

    def test_valid_modes_write_evidence(self):
        for mode in ("debug", "release"):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    with mock.patch.object(bridge, "ROOT", root):
                        bridge.write_evidence(mode, {"status": "INCOMPLETE"})
                    path = root / "build" / mode / "frame-tracer-cd" / bridge.EVIDENCE_NAME
                    self.assertTrue(path.is_file())
                    self.assertEqual(json.loads(path.read_text())["status"], "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
