"""MOCK/interface tests of the Issue #6 trace harness. Fake probes are small Python scripts;
nothing here executes Geargrafx, so no result in this file is real-core evidence."""
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/integration"))
import geargrafx_harness as harness  # noqa: E402

CAP = 'CAP {"instruction_step":true,"cdrom_hardware":true}'


def hit(name, cycle, tag="HIT"):
    return f'{tag} {json.dumps({"name": name, "cycle": cycle, "pc": 61440, "mpr": [255] * 8})}'


def run(name, cycle):
    return f'RUN {json.dumps({"name": name, "reason": name, "cycle": cycle, "pc": 1})}'


def finished(stdout, code=0):
    return {"status": None, "reason": None, "exit_code": code, "stdout": stdout, "stderr": "", "elapsed_s": 0}


class ClassifyTests(unittest.TestCase):
    def test_pass_requires_checkpoint_in_every_cycle(self):
        ok = "\n".join([CAP, hit("cd_halt_ok", "cold"), run("cd_halt_ok", "cold"),
                        hit("cd_halt_ok", "warm"), run("cd_halt_ok", "warm")])
        status, _, events = harness.classify(finished(ok), "cd_halt_ok")
        self.assertEqual(status, "PASS")
        self.assertEqual([e["tag"] for e in events], ["CAP", "HIT", "RUN", "HIT", "RUN"])
        cold_only = "\n".join([CAP, run("cd_halt_ok", "cold"), hit("cd_select", "warm")])
        self.assertEqual(harness.classify(finished(cold_only), "cd_halt_ok")[0], "FAIL")

    def test_forbidden_label_and_budget_exhaustion_fail(self):
        forbidden = "\n".join([CAP, run("cd_halt_ok", "cold"), hit("cd_halt_fail", "cold")])
        self.assertEqual(harness.classify(finished(forbidden), "cd_halt_ok", ["cd_halt_fail"])[0], "FAIL")
        budget = "\n".join([CAP, hit("cd_halt_ok", "cold"), run("budget", "cold")])
        status, reason, _ = harness.classify(finished(budget), "cd_halt_ok")
        self.assertEqual(status, "FAIL")
        self.assertIn("budget", reason)

    def test_capability_block_and_exit_codes_never_pass(self):
        self.assertEqual(harness.classify(finished(run("cd_halt_ok", None)), "cd_halt_ok")[0], "BLOCKED")
        no_step = 'CAP {"instruction_step":false}\n' + run("cd_halt_ok", "cold")
        self.assertEqual(harness.classify(finished(no_step), "cd_halt_ok")[0], "BLOCKED")
        block = 'BLOCK {"reason":"no media"}\n' + CAP
        self.assertEqual(harness.classify(finished(block), "x")[:2], ("BLOCKED", "no media"))
        self.assertEqual(harness.classify(finished("", 6), "x")[0], "BLOCKED")
        for code in (2, 3, 4, 5, 1, -9, 139):
            self.assertEqual(harness.classify(finished(CAP + "\n" + run("x", "cold"), code), "x")[0], "FAIL", code)

    def test_malformed_record_is_fail(self):
        self.assertEqual(harness.classify(finished(CAP + "\nRUN {broken"), "x")[0], "FAIL")

    def test_timeout_result_is_fail_even_with_good_output(self):
        result = finished("\n".join([CAP, run("x", "cold")]))
        result.update(status="FAIL", reason="timeout after 1s")
        self.assertEqual(harness.classify(result, "x")[0], "FAIL")


class RunJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def script(self, body):
        path = self.dir / "fake_probe.py"
        path.write_text(textwrap.dedent(body))
        return [sys.executable, str(path)]

    def test_timeout_is_fail_and_kills_the_process_group(self):
        marker = self.dir / "child-alive"
        command = self.script(f"""
            import subprocess, sys, time
            subprocess.Popen([sys.executable, "-c",
                "import time; time.sleep(1.5); open({str(marker)!r}, 'w').write('x')"])
            time.sleep(60)
        """)
        start = time.monotonic()
        result = harness.run_job(command, self.dir, 0.5)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual((result["status"], result["reason"]), ("FAIL", "timeout after 0.5s"))
        time.sleep(2)
        self.assertFalse(marker.exists(), "grandchild survived the job timeout")

    def test_completion_just_inside_timeout_is_not_timeout(self):
        result = harness.run_job(self.script(f"print({CAP!r})"), self.dir, 30)
        self.assertIsNone(result["status"])
        self.assertEqual(result["exit_code"], 0)

    def test_missing_executable_is_blocked(self):
        result = harness.run_job([str(self.dir / "no-such-probe")], self.dir, 5)
        self.assertEqual(result["status"], "BLOCKED")

    def test_job_profile_is_private(self):
        command = self.script("import json, os; print(json.dumps({k: os.environ.get(k) for k in "
                              "('HOME', 'TMPDIR', 'XDG_CONFIG_HOME', 'SECRET_FROM_PARENT')}))")
        with patch.dict(os.environ, {"SECRET_FROM_PARENT": "leak"}):
            env = json.loads(harness.run_job(command, self.dir, 30)["stdout"])
        self.assertEqual(env, {"HOME": str(self.dir), "TMPDIR": str(self.dir),
                               "XDG_CONFIG_HOME": str(self.dir), "SECRET_FROM_PARENT": None})


class StageAndLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.disc, self.dest = self.dir / "disc-src", self.dir / "job"
        self.disc.mkdir()
        self.dest.mkdir()
        (self.disc / "game.bin").write_bytes(b"track")
        (self.disc / "secret.bin").write_bytes(b"unrelated")
        (self.disc / "notes.txt").write_text("unrelated")
        self.cue = self.disc / "game.cue"
        self.cue.write_text('FILE "game.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')

    def test_only_cue_and_named_files_are_copied(self):
        self.assertEqual(harness.stage_disc(self.cue, self.dest), ["game.cue", "game.bin"])
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["game.bin", "game.cue"])

    def test_unsafe_names_are_refused(self):
        for name in ("../notes.txt", "/etc/passwd", "sub/game.bin", "..\\notes.txt", "C:evil.bin", ".."):
            self.cue.write_text(f'FILE "{name}" BINARY\n')
            with self.assertRaises(ValueError, msg=name):
                harness.stage_disc(self.cue, self.dest)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_cue_without_file_entry_and_missing_bin(self):
        self.cue.write_text("TRACK 01 MODE1/2352\n")
        with self.assertRaises(ValueError):
            harness.stage_disc(self.cue, self.dest)
        self.cue.write_text('FILE "absent.bin" BINARY\n')
        with self.assertRaises(FileNotFoundError):
            harness.stage_disc(self.cue, self.dest)

    def test_symlinked_data_file_or_cue_is_refused(self):
        link = self.disc / "link.bin"
        try:
            os.symlink(self.disc / "secret.bin", link)
        except OSError as error:  # Windows without symlink privilege
            self.skipTest(f"cannot create symlink: {error}")
        self.cue.write_text('FILE "link.bin" BINARY\n')
        with self.assertRaises(ValueError):
            harness.stage_disc(self.cue, self.dest)
        cue_link = self.dir / "linked.cue"
        os.symlink(self.cue, cue_link)
        with self.assertRaises(ValueError):
            harness.stage_disc(cue_link, self.dest)

    def test_output_over_cap_kills_job_and_is_fail(self):
        command = [sys.executable, "-c", "import sys\nwhile True: sys.stdout.write('x' * 4096)"]
        start = time.monotonic()
        result = harness.run_job(command, self.dir, 20, max_output=20_000)
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual((result["status"], result["reason"]), ("FAIL", "output exceeded 20000 bytes"))
        self.assertLessEqual(len(result["stdout"]), 20_000)

    def test_event_cap_is_fail(self):
        lines = "\n".join([CAP] + [run("x", "cold")] * 3)
        with patch.object(harness, "MAX_EVENTS", 3):
            with self.assertRaises(ValueError):
                harness.parse_records(lines)
            self.assertEqual(harness.classify(finished(lines), "x")[0], "FAIL")


class ArtifactPolicyTests(unittest.TestCase):
    def test_attestation_is_hash_only_and_verified_only_on_pass(self):
        ok = harness.attestation("PASS", "a" * 64, "b" * 64, "cd_halt_ok")
        self.assertEqual(set(ok), {"verified", "rom_sha256", "fixture_sha256", "emulator_commit", "checkpoints"})
        self.assertTrue(ok["verified"])
        self.assertEqual(ok["checkpoints"], [{"name": "cd_halt_ok", "observed": "cd_halt_ok"}])
        self.assertEqual(len(ok["emulator_commit"]), 40)
        failed = harness.attestation("FAIL", "a" * 64, "b" * 64, "cd_halt_ok")
        self.assertFalse(failed["verified"])
        self.assertEqual(failed["checkpoints"], [])

    def test_trace_outputs_are_not_uploaded_by_ci(self):
        root = Path(__file__).resolve().parents[2]
        for workflow in (root / ".github/workflows").glob("*.yml"):
            text = workflow.read_text(encoding="utf-8")
            for block in text.split("uses: actions/upload-artifact")[1:]:
                paths = block.split("if-no-files-found")[0]
                self.assertNotIn("trace", paths, workflow.name)
                self.assertNotIn(".bin", paths, workflow.name)
                self.assertNotIn("rom/", paths, workflow.name)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"TOOLS_DIR": str(self.dir / "tools"), "GEARGRAFX_CORE": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_emulator_is_blocked(self):
        report = harness.doctor()
        self.assertEqual(report["libretro_core"]["status"], "BLOCKED")
        self.assertEqual(report["debugger_build"]["status"], "BLOCKED")
        self.assertEqual(report["rom"]["status"], "SKIP")
        self.assertEqual(report["status"], "BLOCKED")

    def test_input_files_hash_and_invalid_sizes(self):
        good, empty, odd = self.dir / "a.pce", self.dir / "b.pce", self.dir / "c.pce"
        good.write_bytes(bytes(8192))
        empty.write_bytes(b"")
        odd.write_bytes(bytes(8193))
        self.assertEqual(harness.describe_file(good, rom=True)["status"], "READY")
        self.assertEqual(len(harness.describe_file(good, rom=True)["sha256"]), 64)
        self.assertEqual(harness.describe_file(empty, rom=True)["status"], "FAIL")
        self.assertEqual(harness.describe_file(odd, rom=True)["status"], "FAIL")
        self.assertEqual(harness.describe_file(self.dir / "absent")["status"], "BLOCKED")
        self.assertEqual(harness.doctor(rom=odd)["status"], "FAIL")  # invalid input outranks BLOCKED

    def test_trace_without_emulator_is_blocked_not_run(self):
        rom, cue, labels = self.dir / "r.pce", self.dir / "d.cue", self.dir / "l.txt"
        rom.write_bytes(bytes(8192))
        cue.write_text("x")
        labels.write_text("x 1")
        out = self.dir / "log.json"
        code = harness.main(["trace", "--rom", str(rom), "--cue", str(cue), "--labels", str(labels),
                             "--expect", "x", "--output", str(out)])
        log = json.loads(out.read_text())
        self.assertEqual((code, log["status"], log["evidence"]), (3, "BLOCKED", "NOT_RUN"))

    def test_step_budget_bounds(self):
        rom = self.dir / "r.pce"
        rom.write_bytes(bytes(8192))
        with patch.object(harness, "doctor", return_value={"status": "PASS"}):
            for steps, kind in ((0, "cdread"), (2049, "api"), (200_000_001, "cdread")):
                args = harness.argparse.Namespace(rom=rom, cue=rom, labels=rom, kind=kind, expect="x",
                                                  forbid=[], steps=steps, timeout=1, keep_job=False)
                self.assertEqual(harness.trace(args)["status"], "FAIL", (steps, kind))


if __name__ == "__main__":
    unittest.main()
