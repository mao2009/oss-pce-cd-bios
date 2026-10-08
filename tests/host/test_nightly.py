import json
import os
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from nightly import SCHEMA, decide, find_baseline, gh, main, valid_state


class NightlyTests(unittest.TestCase):
    def test_first_changed_identical_and_legacy(self):
        sha = "a" * 40
        baseline = {"schema": SCHEMA, "source_ref": "main", "source_sha": sha, "rom_sha256": "c" * 64, "status": "smoke-fixture-not-bios"}
        self.assertTrue(decide(sha, None))
        self.assertFalse(decide(sha, baseline))
        self.assertTrue(decide("b" * 40, baseline))
        self.assertTrue(decide(sha, {**baseline, "schema": "legacy"}))
        self.assertTrue(decide(sha, {**baseline, "source_ref": "feature"}))
        with self.assertRaises(ValueError):
            decide("invalid", baseline)

    def test_success_only_baseline_ignores_failed_current_and_expired(self):
        pages = [{"artifacts": [{"id": i, "expired": i == 5,
                                  "workflow_run": {"id": i}} for i in (5, 4, 3, 2, 1)]}]
        baseline = {"schema": SCHEMA, "source_ref": "main", "source_sha": "a" * 40, "rom_sha256": "c" * 64, "status": "smoke-fixture-not-bios"}
        calls = []

        def fake_gh(*args):
            calls.append(args)
            if "--paginate" in args:
                return json.dumps(pages)
            if args[0] == "api":
                run = int(args[-1].split("/")[-1])
                return json.dumps({"status": "completed", "conclusion": "failure" if run == 3 else "success",
                                   "path": ".github/workflows/nightly.yml", "head_branch": "main", "head_sha": "a" * 40})
            directory = Path(args[args.index("--dir") + 1])
            (directory / "nightly-state.json").write_text(json.dumps(baseline))
            return ""

        with patch("nightly.gh", side_effect=fake_gh):
            self.assertEqual(find_baseline("owner/repo", "4"), baseline)
        self.assertEqual([args[2] for args in calls if args[0] == "run"], ["2"])


class RecoveryTests(unittest.TestCase):
    def state(self, sha='a'):
        return {'schema': SCHEMA, 'source_ref': 'main', 'source_sha': sha * 40,
                'rom_sha256': 'c' * 64, 'status': 'smoke-fixture-not-bios'}

    def find(self, states=None, runs=None, expired=(), download_fail=(), pages=None):
        states = states if states is not None else {1: self.state()}
        runs = runs or {}
        pages = pages if pages is not None else [{'artifacts': [
            {'id': i, 'expired': i in expired, 'workflow_run': {'id': i}}
            for i in states]}]
        self.downloads = []

        def fake(*args):
            if '--paginate' in args:
                return json.dumps(pages)
            if args[0] == 'api':
                number = int(args[-1].split('/')[-1])
                metadata = {'status': 'completed', 'conclusion': 'success',
                            'path': '.github/workflows/nightly.yml', 'head_branch': 'main',
                            'head_sha': 'a' * 40}
                metadata.update(runs.get(number, {}))
                return json.dumps(metadata)
            number = int(args[2])
            self.downloads.append(number)
            if number in download_fail:
                raise subprocess.CalledProcessError(1, args, stderr='artifact download failed')
            content = states[number]
            directory = Path(args[args.index('--dir') + 1])
            if content is not None:
                (directory / 'nightly-state.json').write_text(
                    content if isinstance(content, str) else json.dumps(content))
            return ''

        with patch('nightly.gh', side_effect=fake):
            return find_baseline('owner/repo', '999')

    def test_no_artifacts_and_all_expired_rebuild(self):
        self.assertTrue(decide('a' * 40, self.find(states={})))
        self.assertTrue(decide('a' * 40, self.find(expired=(1,))))
        self.assertEqual(self.downloads, [])

    def test_download_failure_rebuilds_or_falls_back(self):
        self.assertIsNone(self.find(download_fail=(1,)))
        self.assertEqual(self.find(states={2: self.state(), 1: self.state()}, download_fail=(2,)), self.state())
        self.assertEqual(self.downloads, [2, 1])

    def test_corrupt_json_missing_file_and_nondict_rebuild(self):
        for content in ('{broken', None, [], 12, 'null'):
            with self.subTest(content=content):
                self.assertIsNone(self.find(states={1: content}))

    def test_invalid_and_incomplete_state_rebuild(self):
        for changes in ({'schema': 'wrong'}, {'source_ref': 'feature'}, {'source_sha': 'bad'},
                        {'rom_sha256': 'bad'}, {'status': 'failure'}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.find(states={1: {**self.state(), **changes}}))
        for field in self.state():
            incomplete = self.state()
            incomplete.pop(field)
            with self.subTest(missing=field):
                self.assertFalse(valid_state(incomplete))
                self.assertTrue(decide('a' * 40, incomplete))

    def test_other_branch_running_failed_or_wrong_workflow_not_baseline(self):
        for metadata in ({'head_branch': 'feature'}, {'status': 'in_progress'},
                         {'conclusion': 'failure'}, {'conclusion': 'cancelled'},
                         {'path': '.github/workflows/ci.yml'}):
            with self.subTest(metadata=metadata):
                self.assertIsNone(self.find(runs={1: metadata}))
                self.assertEqual(self.downloads, [])

    def test_state_must_match_owning_run_commit(self):
        self.assertIsNone(self.find(states={1: self.state('b')}))

    def test_failed_last_build_uses_last_trustworthy_sha(self):
        baseline = self.find(states={2: self.state('b'), 1: self.state()},
                             runs={2: {'conclusion': 'failure', 'head_sha': 'b' * 40}})
        self.assertTrue(decide('b' * 40, baseline))
        self.assertFalse(decide('a' * 40, baseline))

    def test_multiple_successes_select_newest_with_pagination(self):
        pages = [{'artifacts': [{'id': i, 'expired': False, 'workflow_run': {'id': i}}]}
                 for i in (1, 3, 2)]
        states = {1: self.state(), 2: self.state('b'), 3: self.state('d')}
        baseline = self.find(states=states, runs={3: {'head_sha': 'd' * 40}}, pages=pages)
        self.assertEqual(baseline, self.state('d'))
        self.assertEqual(self.downloads, [3])

    def test_malformed_newest_artifact_falls_back_to_complete_state(self):
        self.assertEqual(self.find(states={2: '{broken', 1: self.state()}), self.state())

    def test_api_outage_is_indeterminate_not_artifact_absence(self):
        with patch('nightly.gh', side_effect=subprocess.CalledProcessError(1, 'gh', stderr='HTTP 503')):
            with self.assertRaisesRegex(ValueError, 'indeterminate'):
                find_baseline('owner/repo', '999')
        with patch('nightly.gh', return_value='{broken'):
            with self.assertRaisesRegex(ValueError, 'invalid response'):
                find_baseline('owner/repo', '999')

    def test_workflow_metadata_api_error_is_indeterminate(self):
        pages = [{'artifacts': [{'id': 1, 'expired': False, 'workflow_run': {'id': 1}}]}]
        with patch('nightly.gh', side_effect=[json.dumps(pages), subprocess.CalledProcessError(1, 'gh')]):
            with self.assertRaisesRegex(ValueError, 'indeterminate'):
                find_baseline('owner/repo', '999')

    def test_transient_api_errors_retry_then_succeed(self):
        error = subprocess.CalledProcessError(1, 'gh', stderr='HTTP 503')
        with patch('nightly.subprocess.run', side_effect=[error, subprocess.CompletedProcess('gh', 0, '[]')]) as run:
            with patch('nightly.time.sleep') as sleep:
                self.assertEqual(gh('api', 'endpoint'), '[]')
                self.assertEqual(run.call_count, 2)
                sleep.assert_called_once_with(1)

    def test_exhausted_and_permanent_api_errors_fail(self):
        for status, attempts in (('503', 3), ('403', 1)):
            with self.subTest(status=status):
                error = subprocess.CalledProcessError(1, 'gh', stderr=f'HTTP {status}')
                with patch('nightly.subprocess.run', side_effect=error) as run, patch('nightly.time.sleep'):
                    with self.assertRaises(subprocess.CalledProcessError):
                        gh('api', 'endpoint')
                    self.assertEqual(run.call_count, attempts)

    def test_manual_and_scheduled_plan_have_identical_policy(self):
        for event in ('schedule', 'workflow_dispatch'):
            for baseline in (None, self.state(), self.state('b')):
                with self.subTest(event=event, baseline=baseline), tempfile.TemporaryDirectory() as directory:
                    output, summary = Path(directory) / 'output', Path(directory) / 'summary'
                    env = {'GITHUB_EVENT_NAME': event, 'SOURCE_SHA': 'a' * 40,
                           'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_RUN_ID': '999',
                           'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': str(summary)}
                    with patch.dict(os.environ, env), patch('nightly.find_baseline', return_value=baseline), \
                            patch('sys.argv', ['nightly.py', 'plan']):
                        main()
                    self.assertIn(f'build={str(decide("a" * 40, baseline)).lower()}', output.read_text())

    def test_api_failure_emits_no_skip_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            with patch.dict(os.environ, {'SOURCE_SHA': 'a' * 40, 'GITHUB_REPOSITORY': 'owner/repo',
                                        'GITHUB_RUN_ID': '999', 'GITHUB_OUTPUT': str(output)}), \
                    patch('nightly.find_baseline', side_effect=ValueError('decision indeterminate')), \
                    patch('sys.argv', ['nightly.py', 'plan']):
                with self.assertRaises(ValueError):
                    main()
            self.assertFalse(output.exists())

    def test_malformed_api_metadata_is_indeterminate(self):
        for pages in ({}, [{"artifacts": None}], [None]):
            with self.subTest(pages=pages), patch('nightly.gh', return_value=json.dumps(pages)):
                with self.assertRaisesRegex(ValueError, 'indeterminate'):
                    find_baseline('owner/repo', '999')
        pages = [{'artifacts': [{'id': 1, 'expired': False, 'workflow_run': {'id': 1}}]}]
        with patch('nightly.gh', side_effect=[json.dumps(pages), '{}']):
            with self.assertRaisesRegex(ValueError, 'indeterminate'):
                find_baseline('owner/repo', '999')


if __name__ == "__main__":
    unittest.main()
