"""Provenance guard (Issue #8): seeded synthetic prohibited samples are rejected; original fixtures pass.

All prohibited samples are generated in memory from short markers; no real firmware, disc or ROM data.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import provenance_guard as guard
from provenance_guard import ProvenanceViolation, check_repo, resolve_base, ROOT

ISO_SIGNATURE = b'\x01CD001\x01'            # synthetic: volume descriptor marker only
RAW_SYNC = b'\x00' + b'\xff' * 10 + b'\x00'  # synthetic: sector sync marker only
ORIGINAL_ASM = b'; original synthetic fixture\n        .org $E000\n        rts\n'
LICENSE_LINE = b'; ' + b'SPDX-' + b'License-Identifier: GPL-2.0\n'  # built so this file has no marker literal


def sha(data):
    return hashlib.sha256(data).hexdigest()


def label_event(event, actor, name=guard.APPROVAL_LABEL, at='2026-10-16T01:00:00Z'):
    return {'event': event, 'label': {'name': name}, 'actor': {'login': actor}, 'created_at': at}


class ProvenanceGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture Author')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.write('fixtures/original.s', ORIGINAL_ASM)
        self.commit()
        self.base = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-m', 'synthetic fixture')

    def write(self, rel, data):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def rebase_here(self):
        self.base = self.git('rev-parse', 'HEAD')

    def check(self, granted=False):
        return check_repo(self.repo, self.base, approve=lambda: granted)

    def assertRejected(self, text, granted=False):
        with self.assertRaisesRegex(ProvenanceViolation, text):
            self.check(granted)

    # Positive: legitimate original content and the real repository pass.
    def test_original_text_fixture_passes(self):
        self.write('fixtures/new.s', b'; new original\n        nop\n')
        self.commit()
        self.assertEqual(self.check(), 1)

    def test_real_repository_tracked_files_pass(self):
        self.assertGreater(check_repo(ROOT, None), 0)

    def test_guard_and_its_tests_pass_their_own_scan(self):
        # Self-check regression: the guard source must not trip its own license/signature rules.
        for path in (ROOT / 'tools' / 'provenance_guard.py', Path(__file__)):
            with self.subTest(path=path.name), path.open('rb') as stream:
                self.assertEqual(guard.scan_stream(path.name, stream, set(), set()), [])

    def test_allowlisted_original_binary_passes_and_is_hash_pinned(self):
        data = b'\x00\x01 synthetic original binary'
        self.write('fixtures/tiny.dat', data)
        self.write(guard.ALLOWLIST, f'{sha(data)}\tfixtures/tiny.dat\n'.encode())
        self.commit()
        self.assertEqual(self.check(granted=True), 2)
        self.write('fixtures/tiny.dat', data + b'x')  # content change breaks the pin
        self.commit()
        self.assertRejected('unallowlisted binary', granted=True)

    # Negative: prohibited artifacts and signatures.
    def test_prohibited_extensions_rejected(self):
        for name in ('disc.cue', 'disc.bin', 'game.pce', 'disc.iso', 'track.wav', 'image.chd'):
            with self.subTest(name=name):
                self.write(f'fixtures/{name}', b'synthetic text body\n')
                self.commit()
                self.assertRejected(r'prohibited extension')
                self.git('rm', '-q', f'fixtures/{name}')
                self.git('commit', '-m', 'remove sample')

    def test_iso_signature_rejected_even_with_text_extension(self):
        self.write('fixtures/notes.txt', b'x' * 0x8000 + ISO_SIGNATURE)
        self.commit()
        self.assertRejected('ISO9660 volume descriptor signature')

    def test_signature_split_across_chunks_is_rejected(self):
        # Streaming regression: the marker straddles two 16-byte reads.
        with mock.patch.object(guard, 'CHUNK_SIZE', 16):
            self.write('fixtures/notes.txt', b'x' * 13 + ISO_SIGNATURE + b'y' * 40)
            self.commit()
            self.assertRejected('ISO9660 volume descriptor signature')

    def test_signature_rejected_even_when_allowlisted_and_approved(self):
        data = b'x' * 0x100 + ISO_SIGNATURE
        self.write('fixtures/notes.txt', data)
        self.write(guard.ALLOWLIST, f'{sha(data)}\tfixtures/notes.txt\n'.encode())
        self.commit()
        self.assertRejected('ISO9660 volume descriptor signature', granted=True)

    def test_raw_sector_sync_rejected(self):
        self.write('fixtures/notes.s', b'; ' + RAW_SYNC + b'\n')
        self.commit()
        self.assertRejected('CD-ROM raw sector sync signature')

    def test_unlabelled_binary_rejected(self):
        self.write('fixtures/blob', b'\x00' * 8192)
        self.commit()
        self.assertRejected('unallowlisted binary')

    def test_denylisted_hash_rejected_even_when_allowlisted(self):
        data = b'synthetic denied sample'
        self.write('fixtures/denied.s', data)
        self.write(guard.DENYLIST, f'{sha(data)}  synthetic\n'.encode())
        self.write(guard.ALLOWLIST, f'{sha(data)}\tfixtures/denied.s\n'.encode())
        self.commit()
        self.assertRejected('denylist', granted=True)

    def test_denylisted_hash_is_checked_with_no_allowlist_bypass(self):
        data = b'synthetic denied sample'
        self.write('fixtures/denied.s', data)
        self.write(guard.DENYLIST, f'{sha(data)}\n'.encode())
        self.commit()
        self.assertRejected('denylist')

    # Default-deny for unclear third-party code.
    def test_third_party_path_and_submodule_rejected(self):
        self.write('vendor/lib.s', b'; vendored\n')
        self.commit()
        self.assertRejected('third-party path')
        self.git('rm', '-rq', 'vendor')
        self.commit()
        self.write('.gitmodules', b'[submodule "x"]\n')
        self.commit()
        self.assertRejected('third-party path or submodule')

    def test_license_marker_in_code_rejected_until_audited(self):
        self.write('fixtures/imported.s', LICENSE_LINE + ORIGINAL_ASM)
        self.commit()
        self.assertRejected('third-party license marker')
        self.write(guard.ALLOWLIST, f'{sha(LICENSE_LINE + ORIGINAL_ASM)}\tfixtures/imported.s\n'.encode())
        self.commit()
        self.assertRejected('gated files changed')  # allowlist edit needs approval
        self.assertEqual(self.check(granted=True), 2)

    def test_license_marker_deep_in_file_is_rejected(self):
        # Previously only the first 4 KiB were searched; the whole file is now scanned.
        self.write('fixtures/deep.s', b'x' * 200_000 + LICENSE_LINE)
        self.commit()
        self.assertRejected('third-party license marker')

    # Allowlist/denylist edits require a verified maintainer approval.
    def test_allowlist_change_without_approval_fails(self):
        self.write(guard.ALLOWLIST, f'{"a" * 64}\tfixtures/original.s\n'.encode())
        self.commit()
        self.assertRejected('without maintainer approval')
        self.assertEqual(self.check(granted=True), 1)

    def test_approval_is_not_a_content_bypass(self):
        self.write('fixtures/disc.cue', b'synthetic\n')
        self.write(guard.ALLOWLIST, f'{sha(b"other content")}\tfixtures/disc.cue\n'.encode())  # hash does not match
        self.commit()
        self.assertRejected('prohibited extension', granted=True)

    def test_base_resolution_for_pull_request_and_push(self):
        self.write('fixtures/new.s', b'; new\n')
        self.commit()
        event = {'pull_request': {'base': {'sha': self.base}}}
        self.assertEqual(resolve_base(self.repo, 'pull_request', event), self.base)
        self.assertEqual(resolve_base(self.repo, 'push', {'before': '0' * 40}), None)
        with self.assertRaisesRegex(ValueError, 'base SHA missing'):
            resolve_base(self.repo, 'pull_request', {})

    # Approval: label alone is not enough; approver must be listed at base and hold a maintainer role.
    def test_approval_requires_trusted_maintainer_label(self):
        roles = {'maintainer': 'maintain', 'writer': 'write', 'admin': 'admin'}
        approvers = {'maintainer', 'writer', 'admin', 'author'}
        cases = [
            ([label_event('labeled', 'maintainer')], True),
            ([label_event('labeled', 'admin')], True),
            ([label_event('labeled', 'writer')], False),        # write role is not enough
            ([label_event('labeled', 'author')], False),        # PR author cannot self-approve
            ([label_event('labeled', 'stranger')], False),      # not in the trusted list
            ([], False),                                        # no label event
            ([label_event('labeled', 'maintainer', name='other')], False),
            ([label_event('labeled', 'maintainer'),
              label_event('unlabeled', 'maintainer', at='2026-10-16T02:00:00Z')], False),
        ]
        for events, expected in cases:
            with self.subTest(events=events):
                role_of = lambda login: roles.get(login, 'read')
                self.assertEqual(
                    guard.approval_from_events(events, 'author', approvers, role_of), expected)

    def test_approver_list_is_read_from_base_not_head(self):
        self.write(guard.APPROVERS, b'# trusted maintainers\nmaintainer\n')
        self.commit()
        self.rebase_here()
        self.write(guard.APPROVERS, b'maintainer\nattacker\n')  # PR head edits the list
        self.commit()
        self.assertEqual(guard.trusted_approvers(self.repo, self.base, 'author', lambda login: 'admin'), {'maintainer'})
        self.assertEqual(guard.approvers_at(self.repo, self.base), {'maintainer'})
        self.assertIsNone(guard.approvers_at(self.repo, 'HEAD~2'))  # setUp commit: no list yet

    def test_empty_approver_list_at_base_is_closed_not_bootstrapped(self):
        self.write(guard.APPROVERS, b'# no approvers yet\n')
        self.commit()
        self.rebase_here()
        self.write(guard.APPROVERS, b'attacker\n')  # PR tries to refill an empty base list
        self.commit()
        self.assertEqual(guard.trusted_approvers(self.repo, self.base, 'author', lambda login: 'admin'), set())

    # Bootstrap: base has no approver list yet, so only a list this PR creates can be trusted.
    def test_bootstrap_trusts_only_non_author_admins_listed_in_the_pr(self):
        self.write(guard.APPROVERS, b'author\nadmin1\nwriter1\n')
        self.commit()
        roles = {'author': 'admin', 'admin1': 'admin', 'writer1': 'write'}
        trusted = guard.trusted_approvers(self.repo, self.base, 'author', lambda login: roles.get(login, 'read'))
        self.assertEqual(trusted, {'admin1'})  # author cannot add itself; write role is not enough

    def test_bootstrap_without_list_in_pr_is_closed(self):
        self.write('fixtures/new.s', b'; new\n')
        self.commit()
        self.assertEqual(guard.trusted_approvers(self.repo, self.base, 'author', lambda login: 'admin'), set())

    # Deletions and renames of gated files are gated, and deleted files are never opened.
    def test_deleting_denylist_requires_approval(self):
        self.write(guard.DENYLIST, f'{"b" * 64}\n'.encode())
        self.commit()
        self.rebase_here()
        self.git('rm', '-q', guard.DENYLIST)
        self.commit()
        self.assertRejected('gated files changed without maintainer approval', granted=False)
        self.assertEqual(self.check(granted=True), 1)

    def test_deleting_allowlist_requires_approval(self):
        self.write(guard.ALLOWLIST, f'{"c" * 64}\tfixtures/original.s\n'.encode())
        self.commit()
        self.rebase_here()
        self.git('rm', '-q', guard.ALLOWLIST)
        self.commit()
        self.assertRejected('gated files changed without maintainer approval')

    def test_deleting_approver_list_requires_base_approver(self):
        self.write(guard.APPROVERS, b'maintainer\n')
        self.commit()
        self.rebase_here()
        self.git('rm', '-q', guard.APPROVERS)
        self.commit()
        self.assertRejected('gated files changed without maintainer approval')
        self.assertEqual(self.check(granted=True), 1)

    def test_rename_away_from_protected_path_requires_approval(self):
        self.write(guard.DENYLIST, f'{"d" * 64}\n'.encode())
        self.commit()
        self.rebase_here()
        self.git('mv', guard.DENYLIST, 'provenance/moved.txt')
        self.commit()
        self.assertRejected('provenance/denylist.sha256', granted=False)
        self.assertEqual(self.check(granted=True), 2)  # old path (D) and new path (A)

    def test_rename_into_protected_path_requires_approval(self):
        self.write('fixtures/notes.tsv', f'{"e" * 64}\tfixtures/original.s\n'.encode())
        self.write('provenance/.keep', b'')
        self.commit()
        self.rebase_here()
        self.git('mv', 'fixtures/notes.tsv', guard.ALLOWLIST)
        self.commit()
        self.assertRejected('gated files changed without maintainer approval')

    def test_deleted_prohibited_file_passes_without_being_opened(self):
        self.write('fixtures/disc.cue', b'synthetic\n')
        self.commit()
        self.rebase_here()
        self.git('rm', '-q', 'fixtures/disc.cue')
        self.commit()
        self.assertEqual(self.check(), 1)  # removal is allowed; the missing file is not read

    def test_changed_entries_report_each_status(self):
        self.write('fixtures/a.s', b'; a\n')
        self.write('fixtures/b.s', b'; b\n')
        self.commit()
        self.rebase_here()
        self.write('fixtures/a.s', b'; a changed\n')
        self.git('rm', '-q', 'fixtures/b.s')
        self.git('mv', 'fixtures/original.s', 'fixtures/renamed.s')
        self.commit()
        entries = sorted(guard.changed_entries(self.repo, self.base))
        self.assertEqual(entries, [('A', 'fixtures/renamed.s'), ('D', 'fixtures/b.s'),
                                   ('D', 'fixtures/original.s'), ('M', 'fixtures/a.s')])

    # PR approval end to end, with GitHub API answers faked.
    def github(self, events, roles):
        def fake(*arguments):
            if arguments[0].endswith('/events'):
                return '\n'.join(json.dumps(event) for event in events)
            login = arguments[0].rsplit('/', 2)[-2]
            return f'{roles.get(login, "none")}\n'
        return mock.patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'}), \
            mock.patch.object(guard, 'github_api', side_effect=fake)

    def test_pr_approval_bootstrap_flow(self):
        self.write(guard.APPROVERS, b'admin1\n')  # the PR creates the first list
        self.commit()
        roles = {'author': 'admin', 'admin1': 'admin'}
        event = {'pull_request': {'number': 7, 'user': {'login': 'author'}}}
        cases = [
            ([label_event('labeled', 'admin1')], True),   # listed non-author admin labels
            ([label_event('labeled', 'author')], False),  # author self-approval
        ]
        for events, expected in cases:
            env, api = self.github(events, roles)
            with self.subTest(events=events), env, api:
                self.assertEqual(guard.pr_approval('pull_request', event, self.repo, self.base), expected)

    def test_pr_approval_author_cannot_list_self_for_bootstrap(self):
        self.write(guard.APPROVERS, b'author\n')  # only the author is listed
        self.commit()
        roles = {'author': 'admin'}
        event = {'pull_request': {'number': 8, 'user': {'login': 'author'}}}
        env, api = self.github([label_event('labeled', 'author')], roles)
        with env, api:
            self.assertFalse(guard.pr_approval('pull_request', event, self.repo, self.base))

    def test_pr_approval_after_bootstrap_uses_base_list_only(self):
        self.write(guard.APPROVERS, b'admin1\n')
        self.commit()
        self.rebase_here()  # base now carries the trusted list
        self.write(guard.APPROVERS, b'admin1\nattacker\n')  # PR tries to add a new approver
        self.commit()
        roles = {'author': 'admin', 'admin1': 'admin', 'attacker': 'admin'}
        event = {'pull_request': {'number': 9, 'user': {'login': 'author'}}}
        env, api = self.github([label_event('labeled', 'attacker')], roles)
        with env, api:
            self.assertFalse(guard.pr_approval('pull_request', event, self.repo, self.base))

    def test_pr_approval_without_repository_slug_fails_closed(self):
        event = {'pull_request': {'number': 10, 'user': {'login': 'author'}}}
        with mock.patch.dict(os.environ, {'GITHUB_REPOSITORY': ''}):
            self.assertFalse(guard.pr_approval('pull_request', event, self.repo, self.base))

if __name__ == '__main__':
    unittest.main()
