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
import tempfile
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


def old_text(job):
    rel, _upstream = WORKFLOWS[job]
    return read_repo(rel)


def replace_in_job(text, job, old, new):
    """Replace inside the given job's block only (the first occurrence elsewhere in the file must not be touched)."""
    start, end = job_span(text, job)
    block = text[start:end]
    assert block.count(old) == 1, (job, old)
    return text[:start] + block.replace(old, new, 1) + text[end:]


def with_job_key(text, job, extra):
    """Insert one job-level key line directly after the job's needs: line (OLD or NEW text)."""
    start, end = job_span(text, job)
    block = text[start:end]
    block = re.sub(r"(\n    needs: [^\n]+\n)", lambda m: m.group(1) + extra, block, count=1)
    return text[:start] + block + text[end:]


class MigrationYamlRuleTests(unittest.TestCase):
    """Job-level keys are read from the parsed YAML. Each mutant below was accepted before this fix (review of #697)."""

    def test_if_false_is_refused_on_old_and_new(self):
        for job in WORKFLOWS:
            with self.subTest(job=job, form="OLD"):
                found = problems(with_job_key(old_text(job), job, "    if: false\n"), WORKFLOWS[job][0])
                self.assertTrue(any("job-level `if` other than exactly always()" in p for p in found), found)
            with self.subTest(job=job, form="NEW"):
                text, rel, _upstream = new_text(job)
                mutant = replace_in_job(text, job, "    if: always()\n", "    if: false\n")
                found = problems(mutant, rel)
                self.assertTrue(any("job-level `if` other than exactly always()" in p for p in found), found)

    def test_always_with_extra_condition_is_refused(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, _upstream = new_text(job)
                mutant = replace_in_job(text, job, "    if: always()\n", "    if: always() && false\n")
                found = problems(mutant, rel)
                self.assertTrue(any("job-level `if` other than exactly always()" in p for p in found), found)

    def test_continue_on_error_is_refused_on_old_and_new(self):
        for job in WORKFLOWS:
            with self.subTest(job=job, form="OLD"):
                found = problems(with_job_key(old_text(job), job, "    continue-on-error: true\n"), WORKFLOWS[job][0])
                self.assertTrue(any("continue-on-error" in p for p in found), found)
            with self.subTest(job=job, form="NEW"):
                text, rel, _upstream = new_text(job)
                found = problems(with_job_key(text, job, "    continue-on-error: true\n"), rel)
                self.assertTrue(any("continue-on-error" in p for p in found), found)

    def test_neighbour_job_injection_is_refused(self):
        # The exact bypass from the review: the always() moved out of the guard job and into a neighbouring job.
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, _upstream = new_text(job)
                start, end = job_span(text, job)
                mutant = text[:start] + text[start:end].replace("    if: always()\n", "", 1) + text[end:]
                injected = ("  zz:  # c\n    runs-on: ubuntu-24.04\n    if: always()\n"
                            "    steps:\n      - run: echo hi\n")
                mutant = mutant.rstrip("\n") + "\n" + injected
                found = problems(mutant, rel)
                self.assertTrue(any("incomplete migration state" in p for p in found), found)

    def test_comment_on_job_header_is_read_correctly(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, _upstream = new_text(job)
                commented = text.replace(f"  {job}:\n", f"  {job}:  # a comment on the header\n", 1)
                self.assertEqual(problems(commented, rel), [])

    def test_gate_with_extra_space_is_refused(self):
        for job in WORKFLOWS:
            with self.subTest(job=job):
                text, rel, upstream = new_text(job)
                mutant = text.replace(gate_run(upstream), gate_run(upstream).replace(" = ", "  = ", 1), 1)
                self.assertTrue(problems(mutant, rel))


def drop_uploads(text, job):
    """The job without any upload-artifact step (the bypass found in the review of e11869e)."""
    start, end = job_span(text, job)
    steps = re.split(r"(?m)^(?=      - )", text[start:end])
    kept = [x for x in steps if "upload-artifact" not in x]
    assert len(kept) < len(steps), f"{job}: no upload step to remove"
    return text[:start] + "".join(kept) + text[end:]


def drop_job(text, job):
    start, end = job_span(text, job)
    return text[:start] + text[end:]


def other_job(text, job, upstream):
    """The name of a job that always succeeds and is neither this job nor its upstream (see with_ok_job)."""
    return "zz-always-ok"


def with_ok_job(text):
    """The workflow plus a trivial neighbour job that succeeds (what a needs: swapped to it would wrongly trust)."""
    return text.rstrip("\n") + "\n  zz-always-ok:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: 'true'\n"


def set_needs(text, job, value):
    start, end = job_span(text, job)
    block = text[start:end]
    if value is None:
        block, n = re.subn(r"\n    needs: [^\n]+\n", "\n", block, count=1)
    else:
        block, n = re.subn(r"(\n    needs: )[^\n]+(\n)", lambda m: m.group(1) + value + m.group(2), block, count=1)
    assert n == 1, job
    return text[:start] + block + text[end:]


def gate_block(upstream):
    return GATE_NAME + "\n" + gate_run(upstream) + "\n"


def variants(job):
    """OLD and NEW text of one job, each with and without its upload steps. Yields (form, text, rel, upstream)."""
    rel, upstream = WORKFLOWS[job]
    for form, text in (("OLD", read_repo(rel)), ("NEW", to_new(read_repo(rel), job, upstream))):
        yield form, text, rel, upstream
        yield form + "-no-upload", drop_uploads(text, job), rel, upstream


class RequiredJobIndependentOfUploadTests(unittest.TestCase):
    """The review of e11869e: the migration rule must not depend on the job having an upload step."""

    def refused(self, mutant, rel, reason, ctx):
        found = problems(mutant, rel)
        self.assertTrue(any(reason in p for p in found), f"{ctx}: expected {reason!r} in {found}")

    def test_unmutated_old_and_new_pass_with_and_without_upload(self):
        for job in WORKFLOWS:
            for form, text, rel, _up in variants(job):
                with self.subTest(job=job, form=form):
                    self.assertEqual(required_only(text, rel), [])

    def test_removing_the_final_upload_does_not_hide_a_violation(self):
        for job in WORKFLOWS:
            rel, upstream = WORKFLOWS[job]
            new = to_new(read_repo(rel), job, upstream)
            cases = {
                "mixed (always without gate)": (replace_in_job(new, job, gate_block(upstream), ""), "incomplete migration state"),
                "if: false": (replace_in_job(new, job, "    if: always()\n", "    if: false\n"), "job-level `if` other than exactly always()"),
                "continue-on-error": (with_job_key(new, job, "    continue-on-error: true\n"), "may not set continue-on-error"),
            }
            for label, (mutant, reason) in cases.items():
                with self.subTest(job=job, case=label):
                    self.refused(drop_uploads(mutant, job), rel, reason, label)

    def test_upstream_continue_on_error_is_refused(self):
        """continue-on-error on the upstream makes needs.<upstream>.result 'success' for a failure: the gate is vacuous."""
        for job in WORKFLOWS:
            for form, text, rel, upstream in variants(job):
                with self.subTest(job=job, form=form):
                    start, end = job_span(text, upstream)
                    block = text[start:end]
                    mutant_block = re.sub(r"(\A[^\n]*\n)", lambda m: m.group(1) + "    continue-on-error: true\n", block, count=1)
                    mutant = text[:start] + mutant_block + text[end:]
                    self.refused(mutant, rel, f"upstream job {upstream!r} of required guard job {job!r} may not set continue-on-error", form)

    def test_trusted_checkout_values_are_case_sensitive(self):
        for job in WORKFLOWS:
            rel, _up = WORKFLOWS[job]
            with self.subTest(job=job):
                text = read_repo(rel)
                start, end = job_span(text, job)
                block = text[start:end].replace("          ref: main\n", "          ref: MAIN\n", 1)
                self.assertNotEqual(block, text[start:end])
                self.refused(text[:start] + block + text[end:], rel, "the first three steps must be the checkout", job)

    def test_missing_required_job_is_refused(self):
        for job in WORKFLOWS:
            rel, _ = WORKFLOWS[job]
            with self.subTest(job=job):
                self.refused(drop_job(read_repo(rel), job), rel, f"required guard job {job!r} is missing", job)

    def test_renamed_required_job_is_refused(self):
        for job in WORKFLOWS:
            rel, _ = WORKFLOWS[job]
            with self.subTest(job=job):
                text = read_repo(rel)
                mutant = text.replace(f"  {job}:\n", f"  {job}-renamed:\n", 1)
                self.assertNotEqual(mutant, text)
                self.refused(mutant, rel, f"required guard job {job!r} is missing", job)

    def test_name_only_dummy_job_is_refused(self):
        for job in WORKFLOWS:
            rel, upstream = WORKFLOWS[job]
            with self.subTest(job=job):
                text = read_repo(rel)
                start, end = job_span(text, job)
                dummy = (f"  {job}:\n    needs: {upstream}\n    runs-on: ubuntu-24.04\n"
                         "    steps:\n      - run: echo ok\n      - run: echo ok\n      - run: echo ok\n")
                self.refused(text[:start] + dummy + text[end:], rel, "the first three steps must be the checkout", job)
                bare = f"  {job}:\n    needs: {upstream}\n    runs-on: ubuntu-24.04\n    steps: []\n"
                found = required_only(text[:start] + bare + text[end:], rel)
                self.assertTrue(any("steps must be a list of at least the three" in p for p in found), found)
                with self.assertRaises(ValueError):   # the full placement check also fails closed on flow-style steps
                    problems(text[:start] + bare + text[end:], rel)

    def test_needs_pointing_at_another_successful_job_is_refused(self):
        for job in WORKFLOWS:
            for form, text, rel, upstream in variants(job):
                with self.subTest(job=job, form=form):
                    self.refused(set_needs(with_ok_job(text), job, other_job(text, job, upstream)), rel, "needs must be exactly", form)

    def test_needs_with_extra_entry_is_refused(self):
        for job in WORKFLOWS:
            for form, text, rel, upstream in variants(job):
                with self.subTest(job=job, form=form):
                    mutant = set_needs(with_ok_job(text), job, f"[{upstream}, {other_job(text, job, upstream)}]")
                    self.refused(mutant, rel, "needs must be exactly", form)

    def test_missing_needs_is_refused(self):
        for job in WORKFLOWS:
            for form, text, rel, _up in variants(job):
                with self.subTest(job=job, form=form):
                    self.refused(set_needs(text, job, None), rel, "needs must be exactly", form)

    def test_if_false_and_always_and_false_are_refused_without_upload(self):
        for job in WORKFLOWS:
            for bad in ("false", "always() && false", "success()", "'always()  '", "true"):
                for form, text, rel, _up in variants(job):
                    with self.subTest(job=job, form=form, value=bad):
                        mutant = with_job_key(text, job, f"    if: {bad}\n") if form.startswith("OLD") else \
                            replace_in_job(text, job, "    if: always()\n", f"    if: {bad}\n")
                        self.refused(mutant, rel, "job-level `if` other than exactly always()", form)

    def test_continue_on_error_in_any_form_is_refused_without_upload(self):
        for job in WORKFLOWS:
            for value in ("true", "false", "${{ always() }}"):
                for form, text, rel, _up in variants(job):
                    with self.subTest(job=job, form=form, value=value):
                        self.refused(with_job_key(text, job, f"    continue-on-error: {value}\n"), rel,
                                     "may not set continue-on-error", form)

    def test_result_gate_deleted_duplicated_moved_or_altered_is_refused(self):
        for job in WORKFLOWS:
            rel, upstream = WORKFLOWS[job]
            new = to_new(read_repo(rel), job, upstream)
            gate = gate_block(upstream)
            after_guard = "      - name: Trusted guard (main)"
            moved_early = replace_in_job(new, job, gate, "").replace(
                "      - name: Resolve python3 (trusted)", gate + "      - name: Resolve python3 (trusted)", 1)
            cases = {
                "deleted": (replace_in_job(new, job, gate, ""), "incomplete migration state"),
                "duplicated": (replace_in_job(new, job, gate, gate + gate), "exactly one result gate step"),
                "moved earlier (index 2)": (moved_early, None),
                "command altered": (replace_in_job(new, job, gate_run(upstream), gate_run(upstream).replace('"success"', '"skipped"')),
                                    "incomplete migration state"),
                "gate step given an if": (replace_in_job(new, job, gate_run(upstream), gate_run(upstream) + "\n        if: false"),
                                          "exactly one result gate step"),
                "gate wrong upstream": (replace_in_job(new, job, gate_run(upstream), gate_run("other-job")), "incomplete migration state"),
            }
            for label, (mutant, reason) in cases.items():
                if label == "moved earlier (index 2)":
                    self.assertNotEqual(moved_early, new)
                for no_upload in (False, True):
                    with self.subTest(job=job, case=label, no_upload=no_upload):
                        text = drop_uploads(mutant, job) if no_upload else mutant
                        found = problems(text, rel)
                        if reason is None:   # a moved gate is refused by the index rule or by the preamble rule
                            self.assertTrue(any("result gate step" in p or "first three steps" in p for p in found), found)
                        else:
                            self.assertTrue(any(reason in p for p in found), f"{label}: {found}")

    def test_gate_moved_after_following_step_is_refused(self):
        for job in WORKFLOWS:
            rel, upstream = WORKFLOWS[job]
            new = to_new(read_repo(rel), job, upstream)
            gate = gate_block(upstream)
            start, end = job_span(new, job)
            block = new[start:end].replace(gate, "", 1)
            # Re-insert the gate one step later (after the step that follows the pyresolve step).
            parts = re.split(r"(?m)^(?=      - )", block)
            parts.insert(5, gate)   # head, checkout, trusted, pyresolve, next, <gate>
            mutant = new[:start] + "".join(parts) + new[end:]
            for no_upload in (False, True):
                with self.subTest(job=job, no_upload=no_upload):
                    text = drop_uploads(mutant, job) if no_upload else mutant
                    self.refused(text, rel, "exactly one result gate step, at index 3", job)

    def test_required_job_name_defined_in_another_workflow_is_refused(self):
        job = "guard-verify"
        rel_other = WORKFLOWS["guard-core-harness"][0]
        text = read_repo(rel_other)
        mutant = text.rstrip("\n") + f"\n  {job}:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo ok\n"
        self.refused(mutant, rel_other, f"required guard job name {job!r} belongs to", job)

    def test_unparseable_or_ambiguous_yaml_is_refused(self):
        for job in WORKFLOWS:
            rel, _ = WORKFLOWS[job]
            text = read_repo(rel)
            for label, mutant in (("duplicate key", with_job_key(text, job, "    runs-on: ubuntu-24.04\n")),
                                  ("alias", with_job_key(text, job, "    zz: *x\n").replace(f"  {job}:\n", f"  {job}: &x\n", 1)),
                                  ("broken", text + "\n  : : [\n")):
                with self.subTest(job=job, case=label):
                    found = required_only(mutant, rel)
                    self.assertTrue(any("cannot be read" in p for p in found), found)

    def test_comment_and_neighbour_text_do_not_change_the_reading(self):
        for job in WORKFLOWS:
            rel, upstream = WORKFLOWS[job]
            new = to_new(read_repo(rel), job, upstream)
            start, end = job_span(new, job)
            # `if: always()` only in a comment inside the job, and as a real key in no job: must read as OLD+gate = mixed.
            mutant = new[:start] + new[start:end].replace("    if: always()\n", "    # if: always()\n", 1) + new[end:]
            self.refused(drop_uploads(mutant, job), rel, "incomplete migration state", job)

    def test_check_guard_placement_refuses_without_the_upload_and_without_the_job(self):
        """The entry point `make check` and the trusted copy use: files with no upload step are checked too."""
        for job in WORKFLOWS:
            rel, upstream = WORKFLOWS[job]
            new = to_new(read_repo(rel), job, upstream)
            cases = {
                "no upload, if: false": (drop_uploads(replace_in_job(new, job, "    if: always()\n", "    if: false\n"), job),
                                         "job-level `if` other than exactly always()"),
                "job removed": (drop_job(read_repo(rel), job), f"required guard job {job!r} is missing"),
            }
            for label, (mutant, reason) in cases.items():
                with self.subTest(job=job, case=label), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    shutil.copytree(ROOT / ".github", root / ".github")
                    shutil.copytree(ROOT / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
                    (root / rel).write_text(mutant, encoding="utf-8")
                    with self.assertRaises(ValueError) as caught:
                        guard.check_guard_placement(root)
                    self.assertIn(reason, str(caught.exception))

    def test_check_guard_placement_refuses_a_required_workflow_with_no_upload_at_all(self):
        """Every upload-artifact step of the file removed (build job too): the no-upload branch must still check the jobs."""
        for job in WORKFLOWS:
            rel, upstream = WORKFLOWS[job]
            text = replace_in_job(to_new(read_repo(rel), job, upstream), job, "    if: always()\n", "    if: false\n")
            steps = re.split(r"(?m)^(?=      - |  [A-Za-z][\w-]*:)", text)   # split at steps and at job keys
            mutant = "".join(x for x in steps if "upload-artifact" not in x)
            self.assertNotIn("upload-artifact", mutant)
            with self.subTest(job=job), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                shutil.copytree(ROOT / ".github", root / ".github")
                shutil.copytree(ROOT / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
                (root / rel).write_text(mutant, encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    guard.check_guard_placement(root)
                self.assertIn("job-level `if` other than exactly always()", str(caught.exception))

    def test_check_guard_placement_refuses_a_deleted_required_workflow(self):
        for job in WORKFLOWS:
            rel, _ = WORKFLOWS[job]
            with self.subTest(job=job), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                shutil.copytree(ROOT / ".github", root / ".github")
                shutil.copytree(ROOT / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
                (root / rel).unlink()
                with self.assertRaises(ValueError) as caught:
                    guard.check_guard_placement(root)
                self.assertIn("the workflow holding required guard jobs is missing", str(caught.exception))


def required_only(text, rel):
    return guard.required_guard_job_violations(text, rel)


if __name__ == "__main__":
    unittest.main()
