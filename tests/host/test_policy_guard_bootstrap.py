"""Issue #544 bootstrap regression tests: pull_request_target is refused for every workflow except the exact policy-guard.yml shape.

Old behaviour (origin/main): every pull_request_target trigger is refused. New behaviour: only the exact policy shape below
passes; every other name or shape is refused. Run this file against origin/main's guard to see the old refusal (the exact
shape then fails), and against this branch's guard to see the new one.
"""
from pathlib import Path
import sys
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard  # noqa: E402

POLICY = '.github/workflows/policy-guard.yml'
GOOD = """\
name: Policy guard
on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]
permissions:
  contents: read
jobs:
  policy-guard:
    name: policy-guard
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout trusted main (base SHA)
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          path: .trusted
          persist-credentials: false
      - name: Checkout pull request head as data only
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          path: .candidate
          persist-credentials: false
      - name: Policy check
        run: python3 .trusted/tools/policy_guard.py --candidate .candidate --trusted .trusted
"""


def problems(text, label=POLICY):
    return guard._privilege_problems(yaml.safe_load(text), label)


class ExactShape(unittest.TestCase):
    def test_exact_policy_shape_is_accepted(self):
        self.assertEqual(problems(GOOD), [])

    def test_same_shape_under_another_name_is_refused(self):
        # old and new: pull_request_target is refused for any file but the exact policy file
        self.assertTrue(problems(GOOD, label='.github/workflows/ci.yml'))


class Variants(unittest.TestCase):
    def assert_refused(self, text):
        self.assertTrue(problems(text), text[:200])

    def test_extra_trigger_is_refused(self):
        self.assert_refused(GOOD.replace('on:\n', 'on:\n  push: {}\n', 1))

    def test_write_permission_is_refused(self):
        self.assert_refused(GOOD.replace('contents: read', 'contents: write', 1))

    def test_id_token_permission_is_refused(self):
        self.assert_refused(GOOD.replace('  contents: read', '  contents: read\n  id-token: write', 1))

    def test_secret_reference_is_refused(self):
        self.assert_refused(GOOD.replace('python3 .trusted/tools/policy_guard.py --candidate .candidate --trusted .trusted',
                                         'echo ${{ secrets.DEPLOY_KEY }} && python3 .trusted/tools/policy_guard.py '
                                         '--candidate .candidate --trusted .trusted'))

    def test_head_checked_out_as_trusted_is_refused(self):
        self.assert_refused(GOOD.replace('ref: ${{ github.event.pull_request.base.sha }}',
                                         'ref: ${{ github.event.pull_request.head.sha }}', 1))

    def test_persisted_credentials_are_refused(self):
        self.assert_refused(GOOD.replace('persist-credentials: false', 'persist-credentials: true', 1))

    def test_unpinned_checkout_is_refused(self):
        self.assert_refused(GOOD.replace('actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683', 'actions/checkout@v4', 1))

    def test_run_from_candidate_is_refused(self):
        self.assert_refused(GOOD.replace('python3 .trusted/tools/policy_guard.py', 'python3 .candidate/tools/policy_guard.py'))

    def test_extra_job_is_refused(self):
        self.assert_refused(GOOD + '  second:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo ok\n')

    def test_job_if_is_refused(self):
        self.assert_refused(GOOD.replace('    runs-on: ubuntu-24.04', '    if: false\n    runs-on: ubuntu-24.04', 1))

    def test_extra_step_is_refused(self):
        self.assert_refused(GOOD + '      - run: echo extra\n')

    def test_extra_trigger_types_are_refused(self):
        self.assert_refused(GOOD.replace('types: [opened, synchronize, reopened, ready_for_review]', 'types: [closed]'))


if __name__ == '__main__':
    unittest.main()
