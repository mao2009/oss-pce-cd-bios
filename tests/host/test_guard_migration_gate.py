"""Migration rule for the required guard jobs (Issue #688 follow-up, stage 1).

Each required guard job (guard-verify, guard-bios-dev, guard-core-harness, guard-bcdbin-core) may have exactly one of two
complete structures, and nothing else:
  OLD: no job-level `if: always()` and no result gate (the structure on main now).
  NEW: job-level `if: always()` and exactly one result gate at index 3, right after the trusted preamble:
       test "${{ needs.<upstream>.result }}" = "success"
A mixed state (only one of the two) is refused. Any other step, order change or condition change is refused.
The strict rule that removes OLD is a separate stage and is not part of this module.
"""
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import artifact_guard as guard  # noqa: E402

WORKFLOWS = {
    "guard-verify": (".github/workflows/ci.yml", "verify-full"),
    "guard-bios-dev": (".github/workflows/api-source.yml", "bios-dev-full"),
    "guard-core-harness": (".github/workflows/core-harness.yml", "core-harness"),
    "guard-bcdbin-core": (".github/workflows/core-bcdbin.yml", "bcdbin-core"),
}
GATE_NAME = "      - name: Require the real-core job to succeed (FAILURE, CANCELLED or SKIPPED fails this check)"


def gate_run(upstream):
    return f'        run: test "${{{{ needs.{upstream}.result }}}}" = "success"'


def read_repo(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def job_span(text, job):
    """(start, end) character offsets of one job block (its key line through the next job key)."""
    start = text.index(f"  {job}:\n")
    nxt = re.search(r"\n  [A-Za-z][\w-]*:\n", text[start + 5:])
    return start, (start + 5 + nxt.start() + 1 if nxt else len(text))


def to_new(text, job, upstream):
    """Build the NEW structure from an OLD job: job-level always, and the gate right after the pyresolve step."""
    start, end = job_span(text, job)
    block = text[start:end]
    block = re.sub(r"(\n    needs: [^\n]+\n)", lambda m: m.group(1) + "    if: always()\n", block, count=1)
    lines = block.split("\n")
    out, armed, placed = [], False, False
    for line in lines:
        if not placed and line.strip() == "id: pyresolve":
            armed = True
        if armed and not placed and line.startswith("      - "):
            out.extend([GATE_NAME, gate_run(upstream)])
            placed = True
            armed = False
        out.append(line)
    assert placed, f"{job}: no pyresolve step found to anchor the gate"
    return text[:start] + "\n".join(out) + text[end:]


def problems(text, label="probe.yml"):
    return guard.guard_placement_violations(text, label)


def new_text(job):
    rel, upstream = WORKFLOWS[job]
    return to_new(read_repo(rel), job, upstream), rel, upstream


class MigrationSemanticsTests(unittest.TestCase):
    """The gate command, run in a shell for each upstream result value. Only SUCCESS passes."""

    def setUp(self):
        self.sh = shutil.which("sh") or shutil.which("bash")
        if not self.sh:
            self.skipTest("no POSIX shell available to run the gate command")

    def code(self, result):
        return subprocess.run([self.sh, "-c", f'test "{result}" = "success"'], capture_output=True).returncode

    def test_success_passes(self):
        self.assertEqual(self.code("success"), 0)

    def test_failure_cancelled_skipped_and_unexpected_fail(self):
        for value in ("failure", "cancelled", "skipped", "", "neutral", "timed_out"):
            with self.subTest(value=value):
                self.assertNotEqual(self.code(value), 0)


class MigrationAcceptTests(unittest.TestCase):
    """Positive cases: exactly the OLD and the complete NEW structure pass for every required guard job."""

    def test_old_structure_on_main_passes(self):
        for job, (rel, _upstream) in WORKFLOWS.items():
            with self.subTest(job=job):
                self.assertEqual(problems(read_repo(rel), rel), [])

    def test_complete_new_structure_passes(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, _upstream = new_text(job)
                self.assertIn(gate_run(_upstream), text)
                self.assertEqual(problems(text, rel), [], f"{job}: the complete NEW structure must pass")


class MigrationRefuseTests(unittest.TestCase):
    """Negative and mutant cases: each defect is refused by the migration rule."""

    def test_only_job_level_always_is_incomplete(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, upstream = new_text(job)
                mutant = text.replace(GATE_NAME + "\n" + gate_run(upstream) + "\n", "", 1)
                self.assertNotEqual(mutant, text)
                found = problems(mutant, rel)
                self.assertTrue(any("incomplete migration state" in p for p in found), found)

    def test_only_gate_without_always_is_incomplete(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, _upstream = new_text(job)
                start, end = job_span(text, job)
                block = text[start:end].replace("    if: always()\n", "", 1)
                mutant = text[:start] + block + text[end:]
                found = problems(mutant, rel)
                self.assertTrue(any("incomplete migration state" in p for p in found), found)

    def test_gate_deleted_is_detected(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, upstream = new_text(job)
                mutant = text.replace(GATE_NAME + "\n" + gate_run(upstream) + "\n", "", 1)
                found = problems(mutant, rel)
                self.assertTrue(any("incomplete migration state" in p for p in found), found)

    def test_always_deleted_is_detected(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, _upstream = new_text(job)
                start, end = job_span(text, job)
                mutant = text[:start] + text[start:end].replace("    if: always()\n", "", 1) + text[end:]
                found = problems(mutant, rel)
                self.assertTrue(any("incomplete migration state" in p for p in found), found)

    def test_wrong_upstream_name_is_refused(self):
        for job, (rel, upstream) in WORKFLOWS.items():
            with self.subTest(job=job):
                text, rel, upstream = new_text(job)
                mutant = text.replace(gate_run(upstream), gate_run("not-the-upstream"), 1)
                self.assertTrue(problems(mutant, rel))

    def test_altered_gate_condition_is_refused(self):
        for job in WORKFLOWS:
            for alt in ('!= "success"', '= "failure"', '= "skipped"'):
                with self.subTest(job=job, alt=alt):
                    text, rel, upstream = new_text(job)
                    mutant = text.replace(gate_run(upstream), f'        run: test "${{{{ needs.{upstream}.result }}}}" {alt}', 1)
                    self.assertTrue(problems(mutant, rel))

    def test_extra_step_inserted_before_gate_is_refused(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, upstream = new_text(job)
                extra = "      - name: extra\n        run: echo hi\n"
                mutant = text.replace(GATE_NAME + "\n", extra + GATE_NAME + "\n", 1)
                self.assertNotEqual(mutant, text)
                self.assertTrue(problems(mutant, rel))

    def test_gate_before_pyresolve_is_refused(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, upstream = new_text(job)
                start, end = job_span(text, job)
                block = text[start:end]
                gate_block = GATE_NAME + "\n" + gate_run(upstream) + "\n"
                moved = block.replace(gate_block, "", 1)
                anchor = "      - name: Trusted guard (main)"
                self.assertIn(anchor, moved)
                mutant = text[:start] + moved.replace(anchor, gate_block + anchor, 1) + text[end:]
                self.assertTrue(problems(mutant, rel))

    def test_trusted_checkout_order_violation_is_refused(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, upstream = new_text(job)
                start, end = job_span(text, job)
                block = text[start:end]
                # Put a second checkout (the workflow files) after the gate: the existing trusted-step rules refuse it.
                extra = ("      - name: Another checkout\n"
                         "        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n"
                         "        with:\n          persist-credentials: false\n")
                mutant = text[:start] + block.replace("      - name: Trusted guard (main)", extra + "      - name: Trusted guard (main)", 1) + text[end:]
                self.assertTrue(problems(mutant, rel))

    def test_existing_trusted_guard_regression_on_real_tree(self):
        self.assertIsNone(guard.check_guard_placement(root=ROOT))

    def test_uses_remain_full_sha_pinned(self):
        for rel in ("ci", "api-source", "core-harness", "core-bcdbin", "pages", "nightly"):
            text = read_repo(f".github/workflows/{rel}.yml")
            for line in text.splitlines():
                if "uses:" in line:
                    self.assertRegex(line, r"@[0-9a-f]{40}", f"{rel}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
