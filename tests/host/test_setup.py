"""Exercise cache reuse/recovery using small original archives, not network mocks."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import setup as bootstrap
from environment import dependency_dir

try:
    import fcntl
except ImportError:  # Windows: the flock test below is POSIX-only (setup.py refuses to run there)
    fcntl = None


def _can_symlink():
    """Windows creates symlinks only with Developer Mode or elevation (WinError 1314 otherwise)."""
    with tempfile.TemporaryDirectory() as directory:
        try:
            os.symlink(__file__, Path(directory) / 'probe')
            return True
        except OSError:
            return False


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)
        self.env = patch.dict(os.environ, {'TOOLS_DIR': str(self.cache)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.archive = self.cache / 'cc65-test.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as tar:
            for name, content in {'project/src/test.c': b'int x;\n', 'project/Makefile': b'all:\n'}.items():
                item = tarfile.TarInfo(name)
                item.size = len(content)
                tar.addfile(item, io.BytesIO(content))
        self.dep = {'revision': 'test', 'url': 'unused',
                    'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        self.pin = patch.dict(bootstrap.DEPENDENCIES, {'cc65': self.dep})
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def test_normal_cache_reuses_sources_and_generated_outputs(self):
        source = bootstrap.fetch('cc65')
        before = (source / 'src/test.c').stat().st_mtime_ns
        (source / 'wrk').mkdir()
        (source / 'wrk/test.o').write_bytes(b'generated object')
        (source / 'bin').mkdir()
        binary = source / 'bin/ca65'
        binary.write_bytes(b'generated binary')
        self.assertEqual(bootstrap.fetch('cc65'), source)
        self.assertEqual((source / 'src/test.c').stat().st_mtime_ns, before)
        self.assertEqual(binary.read_bytes(), b'generated binary')
        (source / 'wrk/test.o').write_bytes(b'updated by normal build')
        self.assertEqual(bootstrap.fetch('cc65'), source)

    def test_changed_same_length_source_is_rejected(self):
        source = bootstrap.fetch('cc65')
        (source / 'src/test.c').write_bytes(b'int y;\n')
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            bootstrap.fetch('cc65')
        self.assertEqual((source / 'src/test.c').read_bytes(), b'int y;\n')

    def test_deleted_or_truncated_source_is_rejected(self):
        source = bootstrap.fetch('cc65')
        for damage in ('truncate', 'delete'):
            with self.subTest(damage=damage):
                target = source / 'src/test.c'
                target.write_bytes(b'int x;\n')
                target.write_bytes(b'') if damage == 'truncate' else target.unlink()
                with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
                    bootstrap.fetch('cc65')

    @unittest.skipIf(os.name == 'nt', 'POSIX exec bits: Windows has no executable mode to change (NOT_RUN)')
    def test_changed_executable_permission_is_rejected(self):
        source = bootstrap.fetch('cc65')
        (source / 'src/test.c').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            bootstrap.fetch('cc65')

    def test_extra_source_is_rejected(self):
        source = bootstrap.fetch('cc65')
        extra = source / 'src/extra.c'
        extra.write_text('unreviewed source')
        with self.assertRaisesRegex(ValueError, 'unexpected source'):
            bootstrap.fetch('cc65')

    @unittest.skipUnless(_can_symlink(), 'symlink creation needs a privilege this host lacks (Windows: NOT_RUN)')
    def test_symlink_in_cache_is_rejected(self):
        source = bootstrap.fetch('cc65')
        (source / 'src/extra.c').symlink_to(source / 'src/test.c')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            bootstrap.fetch('cc65')

    def test_corrupt_or_forged_manifest_is_rejected(self):
        source = bootstrap.fetch('cc65')
        manifest = source / '.source-integrity.json'
        original = manifest.read_text()
        for content in ('{broken', '{}'):
            with self.subTest(content=content):
                manifest.write_text(content)
                with self.assertRaisesRegex(ValueError, 'manifest'):
                    bootstrap.fetch('cc65')
        manifest.write_text(original)
        self.assertEqual(bootstrap.fetch('cc65'), source)

    def test_forged_source_and_matching_manifest_still_fail(self):
        source = bootstrap.fetch('cc65')
        (source / 'src/test.c').write_bytes(b'int y;\n')
        manifest = source / '.source-integrity.json'
        saved = json.loads(manifest.read_text())
        saved['files']['src/test.c']['sha256'] = hashlib.sha256(b'int y;\n').hexdigest()
        manifest.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, 'verified archive'):
            bootstrap.fetch('cc65')

    def test_legacy_cache_is_verified_before_migration(self):
        source = bootstrap.fetch('cc65')
        (source / '.source-integrity.json').unlink()
        self.assertEqual(bootstrap.fetch('cc65'), source)
        self.assertTrue((source / '.source-integrity.json').is_file())
        (source / '.source-integrity.json').unlink()
        (source / 'src/test.c').write_bytes(b'int y;\n')
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            bootstrap.fetch('cc65')
        self.assertFalse((source / '.source-integrity.json').exists())

    def test_interrupted_extraction_can_resume_without_partial_publication(self):
        extract = tarfile.TarFile.extractall

        def interrupted(tar, *args, **kwargs):
            extract(tar, *args, **kwargs)
            raise OSError('simulated interrupted extraction')

        with patch.object(tarfile.TarFile, 'extractall', interrupted):
            with self.assertRaises(OSError):
                bootstrap.fetch('cc65')
        self.assertFalse(dependency_dir('cc65').exists())
        source = bootstrap.fetch('cc65')
        self.assertTrue((source / '.source-integrity.json').is_file())
        self.assertFalse(source.with_name(source.name + '.extracting').exists())

    def test_unowned_staging_directory_is_not_deleted(self):
        # A directory that only has the reserved '.extracting' name was not created by this run; fetch must not remove it.
        destination = dependency_dir('cc65')
        staging = destination.with_name(destination.name + '.extracting')
        staging.mkdir(parents=True)
        (staging / 'user-notes.txt').write_text('not created by setup')
        source = bootstrap.fetch('cc65')
        self.assertEqual((staging / 'user-notes.txt').read_text(), 'not created by setup')
        self.assertTrue((source / '.source-integrity.json').is_file())

    def test_failed_extraction_leaves_no_staging_directory_behind(self):
        extract = tarfile.TarFile.extractall

        def interrupted(tar, *args, **kwargs):
            extract(tar, *args, **kwargs)
            raise OSError('simulated interrupted extraction')

        with patch.object(tarfile.TarFile, 'extractall', interrupted):
            with self.assertRaises(OSError):
                bootstrap.fetch('cc65')
        leftovers = [p.name for p in dependency_dir('cc65').parent.iterdir() if 'extracting' in p.name]
        self.assertEqual(leftovers, [])

    def test_corrupt_archive_and_unverified_cache_are_rejected(self):
        data = self.archive.read_bytes()
        self.archive.write_bytes(b'corrupt archive')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            bootstrap.fetch('cc65')
        self.archive.write_bytes(data)
        dependency_dir('cc65').mkdir()
        with self.assertRaisesRegex(ValueError, 'unverified existing'):
            bootstrap.fetch('cc65')

    @unittest.skipIf(fcntl is None, "POSIX flock semantics; tools/setup.py is POSIX-only (Windows: NOT_RUN)")
    def test_parallel_setup_waits_for_exclusive_lock(self):
        # Verify the main entrypoint actually blocks on the lock before fetching.
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import setup; '
                'setup.shutil.which=lambda _: "present"; '
                'setup.fetch=lambda *args: (_ for _ in ()).throw(ValueError("lock acquired")); '
                'sys.argv=["setup.py", "cc65"]; setup.main()')
        with (self.cache / '.setup.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            child = subprocess.Popen([sys.executable, '-c', code, str(Path(bootstrap.__file__).parent)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    child.communicate(timeout=0.2)
                fcntl.flock(lock, fcntl.LOCK_UN)
                _, stderr = child.communicate(timeout=5)
                self.assertIn('lock acquired', stderr)
                self.assertEqual(child.returncode, 1)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()


if __name__ == '__main__':
    unittest.main()
