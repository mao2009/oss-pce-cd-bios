"""Issue #333 acceptance: host tests never fetch, build, need or run the real Geargrafx.

Real-Geargrafx tests live in tests/integration and run through `make test-real-core`
(`make integration` + `make integration-test`). `make test` is host-only and does not depend on them;
ci.yml and nightly.yml run `make test` and `make test-real-core` as separate required steps.
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Line-level execution paths: the Geargrafx fetch/build calls and a built-core presence check.
REAL_LINE = re.compile(r"""fetch\(\s*['"]geargrafx|build_geargrafx\(|core_path\(\)\.is_file\(\)""")
# A tests/integration Geargrafx script path (any spelling of the file name).
REAL_PATH = re.compile(r"geargrafx_\w+\.py")
LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output", "system"}


def offenders_in(source):
    """Line numbers of real-Geargrafx execution paths in one Python source."""
    found = []
    tree = ast.parse(source)
    # Module-level names bound to a Geargrafx script path (e.g. INTEGRATION = ... / "geargrafx_x.py").
    real_names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(c, ast.Constant) and isinstance(c.value, str) and REAL_PATH.search(c.value)
                for c in ast.walk(node.value)):
            real_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in LAUNCHERS:
            for arg in ast.walk(node):
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str) and REAL_PATH.search(arg.value)) \
                        or (isinstance(arg, ast.Name) and arg.id in real_names):
                    found.append(node.lineno)
                    break
    for number, line in enumerate(source.splitlines(), 1):
        # read_text() of a generator script (a file read, not an execution) is allowed.
        if REAL_LINE.search(line) and "read_text" not in line:
            found.append(number)
    return sorted(found)


def real_core_step(workflow_text):
    """Text of the workflow step whose run: line is `make test-real-core` (None if absent)."""
    for block in re.split(r"(?m)^(?=\s*- name:)", workflow_text):
        if re.search(r"(?m)^\s*run: make test-real-core\s*$", block):
            return block
    return None


def real_core_jobs(workflow_text):
    """Text of each job (under `jobs:`) that has a step running `make test-real-core`."""
    jobs = re.split(r"(?m)^jobs:[ \t]*$", workflow_text)[-1]
    blocks = re.split(r"(?m)^(?=  [A-Za-z0-9_-]+:[ \t]*$)", jobs)
    return [block for block in blocks if re.search(r"(?m)^\s*run: make test-real-core\s*$", block)]


# Job-level keys sit at exactly 4 spaces under a job id; step keys are indented deeper.
JOB_LEVEL_CONTINUE_ON_ERROR = re.compile(r"(?m)^    continue-on-error:")
JOB_LEVEL_IF = re.compile(r"(?m)^    if:[ \t]*(.*?)[ \t]*$")
# Job-level if: expressions allowed on the real-core job, pinned exactly. nightly.yml's build job is skipped when
# main's SHA is unchanged (change-aware plan); no other workflow may guard the real-core job.
def _assert_sharded_real_core_gate(testcase, text, workflow):
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "tools"))
        try:
            import ci_sharding as _sh
        finally:
            _sys.path.remove(str(ROOT / "tools"))
        shard_ids = sorted(_sh.INTEGRATION_SHARDS)
        testcase.assertEqual(shard_ids, ["rc-0", "rc-1", "rc-2", "rc-3"])
        # Every manifest shard has a CI job running exactly its slice.
        for sid in shard_ids:
            job = f"  real-core-shard-{sid.split('-')[1]}:\n"
            testcase.assertIn(job, text)
            testcase.assertIn(f"run: python3 tools/ci_sharding.py run-shard --shard {sid} "
                          f"--evidence-out intermediate/shard-{sid}/evidence.json", text)
        for job in ["host-tests", "integration-direct", "static-release",
                    "real-core-shard-0", "real-core-shard-1",
                    "real-core-shard-2", "real-core-shard-3"]:
            block = re.search(rf"(?m)^  {job}:\n((?:    [^\n]*\n|      [^\n]*\n|        [^\n]*\n)*)", text)
            testcase.assertIsNotNone(block, f"{workflow}: job {job} missing")
            testcase.assertEqual(JOB_LEVEL_CONTINUE_ON_ERROR.findall(block.group(0)), [],
                             f"{workflow}: job {job} is optional")
            # Shard jobs run unconditionally once scoped (no silent skip path):
            # the disabled docs_only fast path is enforced only by verify-full.
            testcase.assertEqual(JOB_LEVEL_IF.findall(block.group(0)), [],
                             f"{workflow}: job {job} must not carry a job-level if")
        # No sharding step may carry its own if:/continue-on-error (same
        # fail-closed rule). Steps start at 6-space `- ` items; the pattern
        # stays inside one step (continuation lines are 8+ spaces, never a
        # less-indented line and never another 6-space `- ` item).
        step_pat = (r"(?m)^      - (?:name|uses|run|with|id):[^\n]*\n"
                    r"(?:(?:      - (?:name|uses|run|with|id):[^\n]*|        [^\n]*)\n)*?"
                    r"        run: python3 tools/ci_sharding\.py[^\n]*")
        sharding_steps = re.findall(step_pat, text)
        testcase.assertTrue(sharding_steps, f"{workflow}: no sharding steps found")
        for step in sharding_steps:
            testcase.assertNotRegex(step, r"(?m)^ +continue-on-error:",
                                f"{workflow}: sharding step is optional")
            testcase.assertNotRegex(step, r"(?m)^        if:",
                                f"{workflow}: sharding step is guarded by if:")
        # The aggregate requires every shard explicitly and validates the manifest.
        testcase.assertIn("run: python3 tools/ci_sharding.py aggregate --evidence "
                      ".intake-shard-host/evidence.json .intake-shard-direct/evidence.json "
                      ".intake-shard-static/evidence.json .intake-shard-rc-0/evidence.json "
                      ".intake-shard-rc-1/evidence.json .intake-shard-rc-2/evidence.json "
                      ".intake-shard-rc-3/evidence.json", text)
        for gated in ["host-tests", "integration-direct", "static-release",
                      "real-core-shard-0", "real-core-shard-1",
                      "real-core-shard-2", "real-core-shard-3"]:
            testcase.assertIn(f'test "${{{{ needs.{gated}.result }}}}" = "success"', text)

ALLOWED_JOB_IF = {
    ".github/workflows/nightly.yml": {"needs.plan.outputs.build == 'true'"},
    ".github/workflows/ci.yml": {"needs.scope.outputs.docs_only != 'true'"},
}


class DetectorTests(unittest.TestCase):
    def test_detects_job_level_guards_on_real_core_job(self):
        text = "jobs:\n  verify:\n    continue-on-error: true\n    steps:\n      - name: x\n        run: make test-real-core\n"
        jobs = real_core_jobs(text)
        self.assertEqual(len(jobs), 1)
        self.assertTrue(JOB_LEVEL_CONTINUE_ON_ERROR.search(jobs[0]))
        guarded = text.replace("    steps:", "    if: github.event_name == 'push'\n    steps:")
        self.assertEqual(JOB_LEVEL_IF.findall(real_core_jobs(guarded)[0]), ["github.event_name == 'push'"])
        clean = text.replace("    continue-on-error: true\n", "")
        self.assertEqual(JOB_LEVEL_CONTINUE_ON_ERROR.findall(real_core_jobs(clean)[0]), [])
        self.assertEqual(JOB_LEVEL_IF.findall(real_core_jobs(clean)[0]), [])

    def test_detects_direct_launch_of_geargrafx_integration_script(self):
        cases = [
            'subprocess.run([sys.executable, str(ROOT / "tests/integration/geargrafx_bcdbin.py")])\n',
            'SCRIPT = ROOT / "tests/integration" / "geargrafx_smoke.py"\nsubprocess.run([sys.executable, str(SCRIPT)])\n',
            'subprocess.Popen(["python", "tests/integration/geargrafx_api.py"])\n',
            'subprocess.run([sys.executable,\n                 str(ROOT / "tests/integration/geargrafx_st_bypass.py")])\n',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertTrue(offenders_in(source))

    def test_ignores_reads_and_docstrings(self):
        cases = [
            'text = (ROOT / "tests/integration/geargrafx_cdread.py").read_text(encoding="utf-8")\n',
            '"""Shape as tests/integration/geargrafx_cdread.py writes it."""\n',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(offenders_in(source), [])

    def test_detects_fetch_build_and_core_presence(self):
        for source in ["bootstrap.fetch('geargrafx')\n", "bootstrap.build_geargrafx(d, 1)\n",
                       "self.assertTrue(core_path().is_file())\n"]:
            with self.subTest(source=source):
                self.assertTrue(offenders_in(source))


class HostIsolationTests(unittest.TestCase):
    def test_host_tests_do_not_fetch_build_need_or_run_real_geargrafx(self):
        offenders = []
        for path in sorted((ROOT / "tests/host").glob("*.py")):
            if path.name == Path(__file__).name:
                continue
            for number in offenders_in(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], "real Geargrafx fetch/build/need/run found in tests/host")

    def test_make_test_is_host_only_and_real_core_gate_is_separate(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        recipe = re.search(r"^test:[^\n]*\n((?:\t[^\n]*\n)+)", makefile, re.M)
        self.assertIsNotNone(recipe, "Makefile test target not found")
        self.assertNotIn("integration", recipe.group(1), "make test must not need Geargrafx")
        real = re.search(r"^test-real-core:[^\n]*\n((?:\t[^\n]*\n)+)", makefile, re.M)
        self.assertIsNotNone(real, "Makefile test-real-core target not found")
        self.assertIn("$(MAKE) integration-test", real.group(1))
        self.assertIn("$(MAKE) integration ", real.group(1))
        self.assertRegex(makefile, r"(?m)^integration-test:\n\t[^\n]*discover -s tests/integration")

    def test_ci_required_job_runs_host_and_real_core_gates(self):
        for workflow in (".github/workflows/ci.yml", ".github/workflows/nightly.yml"):
            with self.subTest(workflow=workflow):
                text = (ROOT / workflow).read_text(encoding="utf-8")
                self.assertRegex(text, r"run: make test\n")
                if workflow == ".github/workflows/nightly.yml":
                    self.assertRegex(text, r"run: make test-real-core\n")
                    step = real_core_step(text)
                    self.assertIsNotNone(step, f"{workflow}: no step runs make test-real-core")
                    # The real-core gate must be required: no continue-on-error, no if: guard on its step.
                    self.assertNotRegex(step, r"(?m)^\s*(?:-\s+)?continue-on-error:", f"{workflow}: real-core step is optional")
                    self.assertNotRegex(step, r"(?m)^\s*(?:-\s+)?if:", f"{workflow}: real-core step is guarded by if:")
                    # A job-level continue-on-error or if: on the job that holds the step makes the gate optional too.
                    jobs = real_core_jobs(text)
                    self.assertTrue(jobs, f"{workflow}: no job runs make test-real-core")
                    for job in jobs:
                        self.assertEqual(JOB_LEVEL_CONTINUE_ON_ERROR.findall(job), [],
                                         f"{workflow}: job running make test-real-core is optional")
                        self.assertLessEqual(set(JOB_LEVEL_IF.findall(job)), ALLOWED_JOB_IF.get(workflow, set()),
                                             f"{workflow}: job running make test-real-core is guarded by if:")
                else:
                    # ci.yml runs the sharded real-core gate (Issue #846): the serial
                    # `make test-real-core` step is replaced by shard jobs plus a
                    # fail-closed aggregate. The serial form must not come back
                    # silently alongside the shards (double run / divergence).
                    self.assertNotRegex(text, r"run: make test-real-core\n",
                                        f"{workflow}: serial make test-real-core must stay replaced by shards")
                    _assert_sharded_real_core_gate(self, text, workflow)


    def test_documentation_fast_path_disabled_and_full_result_required(self):
        # The docs-only fast path is disabled: the only value ever written to docs_only is false, so every change runs
        # the full job. The required names must still reject both a failed scope and a failed full job.
        for workflow, required, full in (
                (".github/workflows/api-source.yml", "bios-dev", "bios-dev-full"),):
            with self.subTest(workflow=workflow):
                text = (ROOT / workflow).read_text(encoding="utf-8")
                self.assertIn(f"  {full}:\n    needs: scope\n    if: needs.scope.outputs.docs_only != 'true'", text)
                self.assertIn(f"  {required}:\n", text)
                self.assertIn(f"    needs: [scope, {full}]\n    if: always()\n", text)
                self.assertIn('test "${{ needs.scope.result }}" = "success"', text)
                self.assertIn(f'test "${{{{ needs.{full}.result }}}}" = "success"', text)
                self.assertIn(f'test "${{{{ needs.{full}.result }}}}" = "skipped"', text)
                # Disabled fast path: the only value written to docs_only is false. The Markdown contract tests that read
                # docs/*.md run only in the full job, so no change may be classified documentation-only.
                self.assertIn('echo "docs_only=false" >> "$GITHUB_OUTPUT"', text)
                self.assertNotIn("docs_only=true", text)
        # ci.yml runs the sharded gate (Issue #846): verify-full aggregates the
        # shard jobs, and `verify` still enforces scope + verify-full.
        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        for job in ["host-tests", "integration-direct", "static-release",
                    "real-core-shard-0", "real-core-shard-1",
                    "real-core-shard-2", "real-core-shard-3"]:
            # Shard jobs run unconditionally once scoped (no silent skip path):
            # the disabled docs_only fast path is enforced only by verify-full.
            self.assertIn(f"  {job}:\n    needs: scope\n", text)
            block = re.search(rf"(?m)^  {re.escape(job)}:\n((?:    [^\n]*\n)+)", text)
            self.assertIsNotNone(block, job)
            self.assertNotIn("if:", block.group(1).split("steps:")[0],
                             f"{job} must not carry a job-level if")
        self.assertIn("    needs: [scope, host-tests, integration-direct, static-release, "
                      "real-core-shard-0, real-core-shard-1, real-core-shard-2, real-core-shard-3]\n"
                      "    if: always() && needs.scope.outputs.docs_only != 'true'", text)
        self.assertIn("  verify:\n", text)
        self.assertIn("    needs: [scope, verify-full]\n    if: always()\n", text)
        self.assertIn('test "${{ needs.scope.result }}" = "success"', text)
        self.assertIn('test "${{ needs.verify-full.result }}" = "success"', text)
        for gated in ["host-tests", "integration-direct", "static-release",
                      "real-core-shard-0", "real-core-shard-1",
                      "real-core-shard-2", "real-core-shard-3"]:
            self.assertIn(f'test "${{{{ needs.{gated}.result }}}}" = "success"', text)
        self.assertIn('echo "docs_only=false" >> "$GITHUB_OUTPUT"', text)
        self.assertNotIn("docs_only=true", text)


if __name__ == "__main__":
    unittest.main()
