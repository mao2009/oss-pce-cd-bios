"""Issue #544 bootstrap regression tests: pull_request_target is refused for every workflow except the exact policy-guard.yml shape.

Old behaviour (origin/main): every pull_request_target trigger is refused. New behaviour: only the exact policy shape below
passes; every other name or shape is refused. Run this file against origin/main's guard to see the old refusal (the exact
shape then fails), and against this branch's guard to see the new one.
"""
from pathlib import Path
import sys
import tempfile
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
        # placement runs both: the value checks (_privilege_problems) and the text and node checks (scope_key_violations)
        refused = problems(text) or guard.scope_key_violations(text, POLICY)
        self.assertTrue(refused, text[:200])

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


PIN = '11bd71901bbe5b1630ceea73d27597364c9af683'
OTHER_SHA = 'a' * 40


class Hardening(unittest.TestCase):
    """P0 review of #547: each variant must be refused (RED before the fix, GREEN after)."""

    def assert_refused(self, text):
        # placement runs both the value checks and the text and node checks (scope_key_violations)
        self.assertTrue(problems(text) or guard.scope_key_violations(text, POLICY), text[:200])

    # (1) pull_request_target.types: exactly the four expected events, no empty, missing, duplicate or extra one
    def test_types_missing_is_refused(self):
        self.assert_refused(GOOD.replace('    types: [opened, synchronize, reopened, ready_for_review]\n', ''))

    def test_types_null_is_refused(self):
        self.assert_refused(GOOD.replace('pull_request_target:\n    types: [opened, synchronize, reopened, ready_for_review]',
                                         'pull_request_target:'))

    def test_types_empty_is_refused(self):
        self.assert_refused(GOOD.replace('types: [opened, synchronize, reopened, ready_for_review]', 'types: []'))

    def test_types_duplicate_is_refused(self):
        self.assert_refused(GOOD.replace('types: [opened, synchronize, reopened, ready_for_review]',
                                         'types: [opened, opened, synchronize, reopened, ready_for_review]'))

    def test_types_missing_one_expected_event_is_refused(self):
        self.assert_refused(GOOD.replace('types: [opened, synchronize, reopened, ready_for_review]',
                                         'types: [opened, synchronize, reopened]'))

    def test_types_extra_event_is_refused(self):
        self.assert_refused(GOOD.replace('types: [opened, synchronize, reopened, ready_for_review]',
                                         'types: [opened, synchronize, reopened, ready_for_review, closed]'))

    # (2) actions/checkout pinned to one audited full SHA, not any 40-hex SHA
    def test_checkout_other_full_sha_is_refused(self):
        self.assert_refused(GOOD.replace(f'actions/checkout@{PIN}', f'actions/checkout@{OTHER_SHA}', 1))

    # (3) job name and display name exact
    def test_job_display_name_mismatch_is_refused(self):
        self.assert_refused(GOOD.replace('    name: policy-guard', '    name: policy-guard-x', 1))

    def test_job_display_name_missing_is_refused(self):
        self.assert_refused(GOOD.replace('    name: policy-guard\n', '', 1))

    # (4) no extra top-level keys (defaults, env, concurrency, run-name ...)
    def test_top_level_env_is_refused(self):
        self.assert_refused(GOOD.replace('permissions:\n', 'env:\n  A: b\npermissions:\n', 1))

    def test_top_level_defaults_is_refused(self):
        self.assert_refused(GOOD.replace('permissions:\n', 'defaults:\n  run:\n    shell: bash\npermissions:\n', 1))

    def test_top_level_concurrency_is_refused(self):
        self.assert_refused(GOOD.replace('permissions:\n', 'concurrency: x\npermissions:\n', 1))

    def test_top_level_run_name_is_refused(self):
        self.assert_refused(GOOD.replace('name: Policy guard\n', 'name: Policy guard\nrun-name: x\n', 1))

    # (5) checkout: exact keys and values
    def test_checkout_fetch_depth_is_refused(self):
        self.assert_refused(GOOD.replace('persist-credentials: false\n      - name: Checkout pull',
                                         'persist-credentials: false\n          fetch-depth: 0\n      - name: Checkout pull', 1))

    def test_checkout_token_key_is_refused(self):
        self.assert_refused(GOOD.replace('path: .trusted', 'path: .trusted\n          token: x', 1))

    def test_run_step_shell_key_is_refused(self):
        self.assert_refused(GOOD.replace('        run: python3', '        shell: bash\n        run: python3', 1))

    # (7) YAML on: key: a bare on: (True) and a quoted "on" are the same trigger; both at once is ambiguous
    def test_on_key_twice_is_refused(self):
        self.assert_refused(GOOD.replace('on:\n', '"on":\n  pull_request_target:\n    types: [opened]\non:\n', 1))

    def test_case_variant_duplicate_on_key_is_refused(self):
        # YAML 1.1 reads ON as True: the duplicate is caught by counting top-level on keys in the text (scope check)
        text = GOOD.replace('on:\n', 'ON:\n  push: {}\non:\n', 1)
        self.assertTrue(guard.scope_key_violations(text, POLICY))

    def test_good_shape_passes_the_on_count(self):
        self.assertEqual(guard.scope_key_violations(GOOD, POLICY), [])

    def test_step_name_with_github_context_is_refused(self):
        self.assert_refused(GOOD.replace('      - name: Checkout pull request head as data only',
                                         '      - name: ${{ toJSON(github) }}', 1))

    def test_step_name_with_bracket_token_is_refused(self):
        self.assert_refused(GOOD.replace('      - name: Policy check', "      - name: ${{ github['token'] }}", 1))

    def test_flow_form_on_and_upper_on_both_refused_by_scope(self):
        flow = ('name: t\n{on: {pull_request_target: {types: [opened]}}, ON: {push: {}}, '
                'permissions: {contents: read}, jobs: {}}\n')
        self.assertTrue(guard.scope_key_violations(flow, POLICY))

    def test_on_key_must_be_spelled_on(self):
        self.assertTrue(guard.scope_key_violations(GOOD.replace('on:\n', 'ON:\n', 1), POLICY))

    def test_persist_credentials_no_is_refused_by_scope(self):
        # YAML 1.1 reads no as False: the policy file accepts only the literal false
        self.assertTrue(guard.scope_key_violations(GOOD.replace('persist-credentials: false', 'persist-credentials: no', 1),
                                                   POLICY))

    def test_permissions_read_all_is_refused(self):
        self.assert_refused(GOOD.replace('permissions:\n  contents: read', 'permissions: read-all', 1))

    def test_head_checkout_extra_key_is_refused(self):
        self.assert_refused(GOOD.replace('path: .candidate\n          persist-credentials: false',
                                         'path: .candidate\n          persist-credentials: false\n          submodules: true', 1))

    # 20f0930 review: three low items, fixed as owner decisions (no risk acceptance)
    def test_persist_credentials_zero_is_refused(self):
        # Python: 0 == False, so a value compare passes 0; the policy needs the boolean False
        self.assert_refused(GOOD.replace('persist-credentials: false', 'persist-credentials: 0', 1))

    def test_persist_credentials_zero_on_head_checkout_is_refused(self):
        self.assert_refused(GOOD.replace('persist-credentials: false\n      - name: Policy check',
                                         'persist-credentials: 0\n      - name: Policy check', 1))

    def test_explicit_bool_tag_on_a_key_is_refused(self):
        # !!bool yes: is an explicit tag on a key (the bool value of yes); it must not become a key by conversion
        self.assert_refused(GOOD.replace('permissions:\n', '!!bool yes: x\npermissions:\n', 1))

    def test_explicit_str_tag_on_trigger_key_is_refused(self):
        self.assert_refused(GOOD.replace('on:\n', '!!str on:\n', 1))

    def test_merge_key_in_checkout_with_is_refused(self):
        self.assert_refused(GOOD.replace('          path: .trusted\n',
                                         '          <<: {persist-credentials: false}\n          path: .trusted\n', 1))

    def test_duplicate_key_that_overwrites_path_is_refused(self):
        # the later duplicate wins in a dict parse, so the dict looks exact while the text sets two paths
        self.assert_refused(GOOD.replace('          path: .trusted\n',
                                         '          path: .evil\n          path: .trusted\n', 1))

    def test_anchor_is_refused(self):
        self.assert_refused(GOOD.replace('      - name: Policy check', '      - name: &n Policy check', 1))

    def test_alias_is_refused(self):
        # a valid anchor and alias: the alias copies the first step's name into the policy step (both text, both exact)
        text = GOOD.replace('      - name: Checkout trusted main (base SHA)', '      - name: &n Checkout trusted main (base SHA)', 1)
        self.assert_refused(text.replace('      - name: Policy check', '      - name: *n', 1))

    # dc1fab9 review: three more items, fixed (owner decision: no risk acceptance)
    def test_duplicate_persist_credentials_in_with_is_refused(self):
        self.assert_refused(GOOD.replace('persist-credentials: false\n      - name: Checkout pull',
                                         'persist-credentials: false\n          persist-credentials: false\n'
                                         '      - name: Checkout pull', 1))

    def test_top_level_name_reading_the_token_is_refused(self):
        self.assert_refused(GOOD.replace('name: Policy guard', "name: ${{ github['token'] }}", 1))

    def test_toJSON_github_context_is_refused(self):
        self.assert_refused(GOOD.replace('      - name: Policy check', '      - name: ${{ toJSON(github) }}', 1))

    def test_yaml_directive_is_refused(self):
        self.assert_refused('%YAML 1.2\n---\n' + GOOD)

    # 1715448 review: CR-only line break before a directive; bare github context in the top-level name
    def test_directive_after_cr_only_line_break_is_refused(self):
        self.assert_refused(('# c\r%YAML 1.2\r---\r' + GOOD).replace('\n', '\r'))

    def test_bare_github_context_in_top_level_name_is_refused(self):
        self.assert_refused(GOOD.replace('name: Policy guard', 'name: ${{ github }}', 1))

    # 6e96d5c review: BOM before a directive; Unicode line separators that YAML 1.1 and 1.2 read differently
    def test_bom_before_directive_is_refused(self):
        self.assert_refused('﻿%YAML 1.2\n---\n' + GOOD)

    def test_unicode_line_separator_in_key_is_refused(self):
        self.assert_refused(GOOD.replace('on:', ' on:', 1))

    def test_nel_line_break_is_refused(self):
        self.assert_refused(GOOD.replace('on:', '\x85on:', 1))

    def test_zero_width_character_in_run_is_refused(self):
        self.assert_refused(GOOD.replace('python3 .trusted', 'python3​ .trusted', 1))

    # 9f6b95a review: a lone CR line break is read differently by parsers (YAML 1.2 CR is a break; a line reader is not)
    def test_lone_cr_line_break_is_refused(self):
        self.assert_refused(GOOD.replace('\n', '\r'))

    def test_lone_cr_is_refused_on_the_placement_read_path(self):
        # f31a38e review: read_text turns CR into LF, so only the raw bytes show the lone CR to the placement check
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.github' / 'workflows' / 'policy-guard.yml'
            path.parent.mkdir(parents=True)
            path.write_bytes((GOOD + '# x\r# y\n').encode('utf-8'))
            with self.assertRaisesRegex(ValueError, 'lone CR'):
                guard.check_guard_placement(Path(tmp))

    def test_quoted_on_key_with_same_trigger_passes(self):
        self.assertEqual(problems(GOOD.replace('on:\n', '"on":\n', 1)), [])


if __name__ == '__main__':
    unittest.main()
