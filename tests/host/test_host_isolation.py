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
ALLOWED_JOB_IF = {".github/workflows/nightly.yml": {"needs.plan.outputs.build == 'true'"}}


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


if __name__ == "__main__":
    unittest.main()
