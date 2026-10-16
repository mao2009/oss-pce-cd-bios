"""MOCK/interface tests of the Issue #6 trace harness. Fake probes are small Python scripts;
nothing here executes Geargrafx, so no result in this file is real-core evidence."""
import hashlib
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

CAP = 'CAP {"instruction_step":true,"cdrom_hardware":true,"cdrom_media":true,"disc_given":true}'
HALT = 64  # tools/build_cdread.py HALT_CONFIRM_STEPS
CHUNK = 1024 * 1024  # streaming read size the harness must use


def hit(name, cycle, tag="HIT"):
    return f'{tag} {json.dumps({"name": name, "cycle": cycle, "pc": 61440, "mpr": [255] * 8})}'


def run(name, cycle):
    return f'RUN {json.dumps({"name": name, "reason": name, "cycle": cycle, "pc": 1})}'


def run_ok(cycle, steps=HALT, arrival=10, drop=(), **overrides):
    """A RUN as the probe emits it after a confirmed cd_halt_ok: `steps` instructions on the label."""
    record = {"name": "cd_halt_ok", "reason": "cd_halt_ok", "cycle": cycle, "pc": 1, "step": arrival + steps,
              "halt_confirmed": True, "halted_after_step": arrival,
              "halt_confirmation_steps": steps, "halt_confirmation_required": HALT}
    record.update(overrides)
    for key in drop:
        del record[key]
    return f"RUN {json.dumps(record)}"


GOOD = [CAP, hit("cd_halt_ok", "cold"), run_ok("cold"), hit("cd_halt_ok", "warm"), run_ok("warm")]


def finished(stdout, code=0):
    return {"status": None, "reason": None, "exit_code": code, "stdout": stdout, "stderr": "", "elapsed_s": 0}


class ClassifyTests(unittest.TestCase):
    def check(self, lines, expect="cd_halt_ok", forbid=(), budget=None):
        return harness.classify(finished("\n".join(lines)), expect, forbid, budget)

    def test_pass_requires_confirmed_run_in_both_cycles(self):
        status, _, events = self.check(GOOD, budget=12_000_000)
        self.assertEqual(status, "PASS")
        self.assertEqual([e["tag"] for e in events], ["CAP", "HIT", "RUN", "HIT", "RUN"])

    def test_hit_without_run_is_not_pass(self):
        self.assertEqual(self.check([CAP, hit("cd_halt_ok", "cold"), hit("cd_halt_ok", "warm")])[0], "FAIL")

    def test_run_missing_in_one_cycle_is_fail(self):
        cold_only = [CAP, hit("cd_halt_ok", "cold"), run_ok("cold"), hit("cd_halt_ok", "warm")]
        warm_only = [CAP, hit("cd_halt_ok", "cold"), hit("cd_halt_ok", "warm"), run_ok("warm")]
        for lines in (cold_only, warm_only):
            self.assertEqual(self.check(lines)[0], "FAIL", lines)

    def test_duplicate_or_reordered_runs_are_fail(self):
        duplicate = GOOD + [run_ok("cold")]
        reordered = [CAP, hit("cd_halt_ok", "cold"), hit("cd_halt_ok", "warm"), run_ok("warm"), run_ok("cold")]
        for lines in (duplicate, reordered):
            self.assertEqual(self.check(lines)[0], "FAIL", lines)

    def test_success_hit_with_failing_run_is_fail(self):
        for bad in (run("cd_halt_fail", "warm"), run("cd_unexpected_interrupt", "warm"), run("budget", "warm")):
            lines = [CAP, hit("cd_halt_ok", "cold"), run_ok("cold"), hit("cd_halt_ok", "warm"), bad]
            self.assertEqual(self.check(lines)[0], "FAIL", bad)  # no forbid list needed

    def test_halt_confirmation_63_steps_is_fail_and_64_passes(self):
        short = [CAP, hit("cd_halt_ok", "cold"), run_ok("cold", steps=63), hit("cd_halt_ok", "warm"),
                 run_ok("warm", steps=63)]
        status, reason, _ = self.check(short)
        self.assertEqual(status, "FAIL")
        self.assertIn("63", reason)
        self.assertEqual(self.check(GOOD)[0], "PASS")

    def test_halt_not_confirmed_or_inconsistent_steps_are_fail(self):
        cases = [{"halt_confirmed": False}, {"step": 10 + 63}, {"halted_after_step": -1},
                 {"halted_after_step": "10"}, {"step": "74"}, {"step": True},
                 {"halt_confirmation_required": 63}]
        for override in cases:
            lines = [CAP, hit("cd_halt_ok", "cold"), run_ok("cold", **override), hit("cd_halt_ok", "warm"),
                     run_ok("warm")]
            self.assertEqual(self.check(lines)[0], "FAIL", override)
        dropped = [CAP, hit("cd_halt_ok", "cold"), run_ok("cold", drop=("halt_confirmation_steps",)),
                   hit("cd_halt_ok", "warm"), run_ok("warm")]
        self.assertEqual(self.check(dropped)[0], "FAIL")

    def test_budget_exhaustion_and_instruction_count_outside_budget_are_fail(self):
        budget_stop = [CAP, hit("cd_halt_ok", "cold"), run("budget", "cold"), hit("cd_halt_ok", "warm"),
                       run_ok("warm")]
        status, reason, _ = self.check(budget_stop)
        self.assertEqual(status, "FAIL")
        self.assertIn("budget", reason)
        over = [CAP, hit("cd_halt_ok", "cold"), run_ok("cold", arrival=10), hit("cd_halt_ok", "warm"),
                run_ok("warm")]
        self.assertEqual(harness.classify(finished("\n".join(over)), "cd_halt_ok", (), 50)[0], "FAIL")
        self.assertEqual(harness.classify(finished("\n".join(GOOD)), "cd_halt_ok", (), 74)[0], "PASS")

    def test_incomplete_capability_record_is_blocked(self):
        for cap in ('CAP {"instruction_step":true,"cdrom_hardware":true,"disc_given":true}',
                    'CAP {"instruction_step":true,"cdrom_hardware":true,"cdrom_media":false,"disc_given":true}',
                    'CAP {"instruction_step":true,"cdrom_hardware":true,"cdrom_media":true}'):
            self.assertEqual(self.check([cap] + GOOD[1:])[0], "BLOCKED", cap)
        self.assertEqual(self.check([CAP, CAP] + GOOD[1:])[0], "BLOCKED")

    def test_spoofed_success_labels_are_fail(self):
        spoof_phase = [CAP, 'PHASE {"name":"cd_halt_ok","cycle":"cold"}', hit("cd_halt_ok", "warm"),
                       run_ok("warm")]
        self.assertEqual(self.check(spoof_phase)[0], "FAIL")
        hit_after_run = [CAP, run_ok("cold"), hit("cd_halt_ok", "cold"), hit("cd_halt_ok", "warm"), run_ok("warm")]
        self.assertEqual(self.check(hit_after_run)[0], "FAIL")
        other_cycle = [CAP, hit("cd_halt_ok", "cold"), run_ok("cold"), hit("cd_halt_ok", "hot"), run_ok("hot")]
        self.assertEqual(self.check(other_cycle)[0], "FAIL")

    def test_partial_evidence_never_yields_verified(self):
        partial = [CAP, hit("cd_halt_ok", "cold"), run_ok("cold", steps=63), hit("cd_halt_ok", "warm"), run_ok("warm")]
        status, _, _ = self.check(partial)
        self.assertFalse(harness.attestation(status, "a" * 64, "b" * 64, "cd_halt_ok")["verified"])

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
            self.assertEqual(harness.classify(finished("\n".join(GOOD), code), "cd_halt_ok")[0], "FAIL", code)

    def test_unsupported_kind_never_passes_even_with_cdread_records(self):
        # The API probe has no RUN/HIT contract; a cdread-shaped PASS log must not leak to kind "api".
        ok_text = "\n".join(GOOD)
        self.assertEqual(harness.classify(finished(ok_text), "cd_halt_ok", (), None, kind="api")[0], "BLOCKED")
        self.assertEqual(harness.classify(finished(ok_text), "cd_halt_ok", (), None, kind="cdread")[0], "PASS")

    def test_api_probe_output_without_run_is_not_judged_as_cdread(self):
        # Shape of geargrafx_api_probe.cpp output: CAP/TRACE/OBS/BANK, no RUN and no HIT records.
        api_out = "\n".join([CAP, 'OBS {"phase":"loaded"}', 'TRACE {"phase":"cd_halt_ok"}',
                             'BANK {"mpr7":1,"physical_e000":8192,"byte_e000":255}'])
        self.assertEqual(self.check(api_out.split("\n"))[0], "FAIL")  # cdread judge: no RUN records
        self.assertEqual(harness.classify(finished(api_out), "cd_halt_ok", (), None, kind="api")[0], "BLOCKED")

    def test_malformed_record_is_fail(self):
        self.assertEqual(harness.classify(finished(CAP + "\nRUN {broken"), "x")[0], "FAIL")

    def test_timeout_result_is_fail_even_with_good_output(self):
        result = finished("\n".join(GOOD))
        result.update(status="FAIL", reason="timeout after 1s")
        self.assertEqual(harness.classify(result, "cd_halt_ok")[0], "FAIL")


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


class StreamingDigestTests(unittest.TestCase):
    """Digests are streamed in CHUNK_BYTES reads; no input is ever held whole in memory."""

    class Zeros:
        """A virtual file of `size` zero bytes that records every read size; it allocates no disk."""
        ZERO = bytes(CHUNK)

        def __init__(self, size):
            self.left = size

        def read(self, n=-1):
            take = min(n, self.left)
            self.left -= take
            return self.ZERO[:take]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.no_whole_reads = patch.object(Path, "read_bytes", side_effect=AssertionError("read_bytes used"))
        self.no_whole_reads.start()
        self.addCleanup(self.no_whole_reads.stop)

    def test_chunk_size_is_one_mib(self):
        self.assertEqual(getattr(harness, "CHUNK_BYTES", None), CHUNK)

    def test_sha256_matches_whole_file_digest(self):
        data = os.urandom(2 * CHUNK + 123)  # spans several chunks
        path = self.dir / "multi.bin"
        path.write_bytes(data)
        reads = []
        self.assertEqual(harness.sha256(path, reads), hashlib.sha256(data).hexdigest())
        self.assertEqual(sum(reads), len(data))

    def test_fixture_digest_matches_concatenation_in_cue_order(self):
        disc = self.dir / "disc"
        disc.mkdir()
        first, second = os.urandom(CHUNK + 7), os.urandom(31)
        (disc / "a.bin").write_bytes(first)
        (disc / "b.bin").write_bytes(second)
        expected = hashlib.sha256(first + second).hexdigest()
        reads = []
        self.assertEqual(harness.fixture_digest(disc, ["a.bin", "b.bin"], reads), expected)
        self.assertEqual(sum(reads), CHUNK + 7 + 31)

    def test_reads_stay_bounded_as_the_file_grows(self):
        for size in (3 * CHUNK + 5, 256 * CHUNK):  # 256 MiB virtual file
            reads = []
            digest = harness._update_digest(hashlib.sha256(), self.Zeros(size), reads)
            if size < 8 * CHUNK:  # value check only where the reference fits comfortably
                self.assertEqual(digest.hexdigest(), hashlib.sha256(bytes(size)).hexdigest())
            self.assertEqual(sum(reads), size)
            self.assertLessEqual(max(reads), CHUNK)  # memory per read never grows with the file
            self.assertEqual(len(reads), -(-size // CHUNK))

    def test_describe_file_uses_streaming_hash(self):
        path = self.dir / "rom.pce"
        path.write_bytes(bytes(8192))
        self.assertEqual(harness.describe_file(path, rom=True)["sha256"], hashlib.sha256(bytes(8192)).hexdigest())


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

    def test_api_kind_is_not_supported_and_never_probed(self):
        rom, cue, labels = self.dir / "r.pce", self.dir / "d.cue", self.dir / "l.txt"
        rom.write_bytes(bytes(8192))
        cue.write_text("x")
        labels.write_text("x 1")
        out = self.dir / "api.json"
        with patch.object(harness, "doctor", return_value={"status": "PASS"}), \
                patch("geargrafx_api.compile_probe", side_effect=AssertionError("api probe was built")):
            code = harness.main(["trace", "--kind", "api", "--rom", str(rom), "--cue", str(cue),
                                 "--labels", str(labels), "--expect", "x", "--output", str(out)])
        log = json.loads(out.read_text())
        self.assertEqual((code, log["status"], log["evidence"]), (3, "BLOCKED", "NOT_RUN"))
        self.assertIn("NOT_SUPPORTED", log["reason"])

    def test_step_budget_bounds(self):
        rom = self.dir / "r.pce"
        rom.write_bytes(bytes(8192))
        with patch.object(harness, "doctor", return_value={"status": "PASS"}):
            for steps, kind in ((0, "cdread"), (200_000_001, "cdread")):
                args = harness.argparse.Namespace(rom=rom, cue=rom, labels=rom, kind=kind, expect="x",
                                                  forbid=[], steps=steps, timeout=1, keep_job=False)
                self.assertEqual(harness.trace(args)["status"], "FAIL", (steps, kind))


if __name__ == "__main__":
    unittest.main()
