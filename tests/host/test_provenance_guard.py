"""Provenance guard (Issue #8): seeded synthetic prohibited samples are rejected; original fixtures pass.

All prohibited samples are generated in memory from short markers; no real firmware, disc or ROM data.
"""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import provenance_guard as guard
from provenance_guard import ProvenanceViolation, check_repo, resolve_base, ROOT

ISO_SIGNATURE = b'\x01CD001\x01'            # synthetic: volume descriptor marker only
RAW_SYNC = b'\x00' + b'\xff' * 10 + b'\x00'  # synthetic: sector sync marker only
ORIGINAL_ASM = b'; original synthetic fixture\n        .org $E000\n        rts\n'
LICENSE_LINE = b'; ' + b'SPDX-' + b'License-Identifier: GPL-2.0\n'  # built so this file has no marker literal


def sha(data):
    return hashlib.sha256(data).hexdigest()


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

    def check(self, approval=''):
        return check_repo(self.repo, self.base, approval=approval)

    def assertRejected(self, text, approval=''):
        with self.assertRaisesRegex(ProvenanceViolation, text):
            self.check(approval)

    # Positive: legitimate original content and the real repository pass.
    def test_original_text_fixture_passes(self):
        self.write('fixtures/new.s', b'; new original\n        nop\n')
        self.commit()
        self.assertEqual(self.check(), 1)

    def test_real_repository_tracked_files_pass(self):
        self.assertGreater(check_repo(ROOT, None), 0)

    def test_allowlisted_original_binary_passes_and_is_hash_pinned(self):
        data = b'\x00\x01 synthetic original binary'
        self.write('fixtures/tiny.dat', data)
        self.write(guard.ALLOWLIST, f'{sha(data)}\tfixtures/tiny.dat\n'.encode())
        self.commit()
        self.assertEqual(self.check(approval='granted'), 2)
        self.write('fixtures/tiny.dat', data + b'x')  # content change breaks the pin
        self.commit()
        self.assertRejected('unallowlisted binary', approval='granted')

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
        self.assertRejected('denylist', approval='granted')

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
        self.assertRejected('gated files changed', approval='')  # allowlist edit needs approval
        self.assertEqual(self.check(approval='granted'), 2)

    # Allowlist/denylist edits require explicit approval.
    def test_allowlist_change_without_approval_fails(self):
        self.write(guard.ALLOWLIST, f'{"a" * 64}\tfixtures/original.s\n'.encode())
        self.commit()
        self.assertRejected('without maintainer approval')
        self.assertEqual(self.check(approval='granted'), 1)

    def test_approval_is_not_a_content_bypass(self):
        self.write('fixtures/disc.cue', b'synthetic\n')
        self.write(guard.ALLOWLIST, f'{sha(b"synthetic" + b"\\n")}\tfixtures/disc.cue\n'.encode())
        self.commit()
        self.assertRejected('prohibited extension', approval='granted')

    def test_base_resolution_for_pull_request_and_push(self):
        self.write('fixtures/new.s', b'; new\n')
        self.commit()
        event = {'pull_request': {'base': {'sha': self.base}}}
        self.assertEqual(resolve_base(self.repo, 'pull_request', event), self.base)
        self.assertEqual(resolve_base(self.repo, 'push', {'before': '0' * 40}), None)
        with self.assertRaisesRegex(ValueError, 'base SHA missing'):
            resolve_base(self.repo, 'pull_request', {})


if __name__ == '__main__':
    unittest.main()
