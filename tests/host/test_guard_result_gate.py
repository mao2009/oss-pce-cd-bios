"""Result gate for the required guard jobs (Issue #688 follow-up, Owner decision).

A required guard job (guard-verify, guard-bios-dev, guard-core-harness, guard-bcdbin-core) must not pass when its upstream
job did not succeed. Each has a job-level `if: always()` and one gate step right after the trusted preamble:
    test "${{ needs.<upstream>.result }}" = "success"
The gate is the only trusted run this rule adds. These tests check the rule (guard_placement_violations) on the real
workflow texts, and run the gate command in a shell for each result value (SUCCESS, FAILURE, CANCELLED, SKIPPED, empty).
Positive and negative cases come first; each mutant must be refused by a named rule message.
"""
import os
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
GATE_LINE = "        run: test \"${{ needs.%s.result }}\" = \"success\""
GATE_NAME = "      - name: Require the real-core job to succeed (FAILURE, CANCELLED or SKIPPED fails this check)"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def problems_for(text, label):
    return guard.guard_placement_violations(text, label)


def gate_problems(text, label):
    return [p for p in problems_for(text, label) if "required guard job" in p or "result gate" in p
            or "trusted steps" in p or "first step" in p or "second step" in p or "third step" in p]


class GateSemanticsTests(unittest.TestCase):
    """Run the gate command in a shell for each upstream result value. Only SUCCESS may pass."""

    def setUp(self):
        self.sh = shutil.which("sh") or shutil.which("bash")
        if not self.sh:
            self.skipTest("no POSIX shell available to run the gate command")

    def run_gate(self, result):
        command = f'test "{result}" = "success"'
        return subprocess.run([self.sh, "-c", command], capture_output=True).returncode

    def test_success_passes(self):
        self.assertEqual(self.run_gate("success"), 0)

    def test_failure_fails(self):
        self.assertNotEqual(self.run_gate("failure"), 0)

    def test_cancelled_fails(self):
        self.assertNotEqual(self.run_gate("cancelled"), 0)

    def test_skipped_fails(self):
        self.assertNotEqual(self.run_gate("skipped"), 0)

    def test_unexpected_or_empty_result_fails(self):
        for value in ("", "neutral", "timed_out", "SUCCESS"):
            with self.subTest(value=value):
                self.assertNotEqual(self.run_gate(value), 0)


class GateRuleTests(unittest.TestCase):
    """The placement rule on the real workflow texts (positive, negative and mutants)."""

    def test_real_workflows_pass_the_gate_rule(self):
        for job, (rel, upstream) in WORKFLOWS.items():
            with self.subTest(job=job):
                text = read(rel)
                self.assertEqual(gate_problems(text, rel), [], f"{rel}: {job} must pass the gate rule")
                self.assertIn(GATE_LINE % upstream, text)
                self.assertIn(GATE_NAME, text)

    def test_gate_before_trusted_checkout_is_denied(self):
        for job, (rel, upstream) in WORKFLOWS.items():
            with self.subTest(job=job):
                text = read(rel)
                gate = GATE_NAME + "\n" + GATE_LINE % upstream + "\n"
                self.assertIn(gate, text)
                moved = text.replace(gate, "", 1)
                # put the gate in front of the job's first step (before the workflow-file checkout)
                job_key = f"  {job}:\n"
                moved = moved.replace(job_key, job_key, 1)
                first = moved.index(job_key) + len(job_key)
                steps_at = moved.index("    steps:\n", first) + len("    steps:\n")
                denied = moved[:steps_at] + gate + moved[steps_at:]
                self.assertTrue(gate_problems(denied, rel), f"{job}: gate before the trusted preamble must be DENY")

    def test_wrong_upstream_name_is_denied(self):
        for job, (rel, upstream) in WORKFLOWS.items():
            with self.subTest(job=job):
                text = read(rel)
                wrong = text.replace(GATE_LINE % upstream, GATE_LINE % "not-the-upstream", 1)
                self.assertTrue(gate_problems(wrong, rel), f"{job}: a gate naming another job must be DENY")

    def test_altered_gate_condition_is_denied(self):
        for job, (rel, upstream) in WORKFLOWS.items():
            for alt in ('!= "success"', '= "failure"', '= "skipped"'):
                with self.subTest(job=job, alt=alt):
                    text = read(rel)
                    altered = text.replace(GATE_LINE % upstream,
                                           f'        run: test "${{{{ needs.{upstream}.result }}}}" {alt}', 1)
                    self.assertTrue(gate_problems(altered, rel), f"{job}: an altered gate condition must be DENY")

    def test_missing_job_level_always_is_denied(self):
        for job, (rel, upstream) in WORKFLOWS.items():
            with self.subTest(job=job):
                text = read(rel)
                start = text.index(f"  {job}:\n")
                # the job block ends at the next job key (two spaces, a letter), not at the four-space body lines
                nxt = re.search(r"\n  [A-Za-z][\w-]*:\n", text[start + 5:])
                block_end = start + 5 + nxt.start() if nxt else len(text)
                block = text[start:block_end]
                self.assertIn("    if: always()\n", block)
                without = text[:start] + block.replace("    if: always()\n", "", 1) + text[block_end:]
                self.assertTrue(gate_problems(without, rel), f"{job}: a job without if: always() must be DENY")

    def test_mutant_removing_the_gate_is_detected(self):
        for job, (rel, upstream) in WORKFLOWS.items():
            with self.subTest(job=job):
                text = read(rel)
                mutant = text.replace(GATE_NAME + "\n" + GATE_LINE % upstream + "\n", "", 1)
                self.assertNotEqual(mutant, text, "the gate must exist before it can be removed")
                problems = problems_for(mutant, rel)
                self.assertTrue(any("result gate step" in p for p in problems),
                                f"{job}: removing the gate must be detected by the named rule (found {problems})")

    def test_existing_trusted_guard_still_passes_on_the_real_tree(self):
        # Regression: the placement check, run on the real repository workflows, still passes with the new rule.
        self.assertEqual(guard.check_guard_placement(root=ROOT), None)


if __name__ == "__main__":
    unittest.main()
