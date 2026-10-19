"""Artifact guard (Issue #227): synthetic linked-executable samples are refused; fixture outputs pass.

Samples are generated in memory from short signature bytes; no BIOS, disc, ROM or real Geargrafx binary is used.
"""
from pathlib import Path
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard
from artifact_guard import check_upload_artifacts, entry_problem, upload_patterns, ROOT

ELF_PROBE = b'\x7fELF\x02\x01\x01' + b'\x00' * 57   # synthetic ELF header marker only
MACHO = b'\xcf\xfa\xed\xfe' + b'\x00' * 12          # synthetic Mach-O marker only
AR_ARCHIVE = b'!<arch>\n' + b'\x00' * 16            # synthetic ar marker only
PE_STUB = b'MZ' + b'\x00' * 58 + (0x40).to_bytes(4, 'little') + b'PE\x00\x00'
FIXTURE_PCE = b'\x4d\x5a' + b'\x00' * 6 + b'PCE fixture'   # starts with "MZ" but has no PE header
PLAIN_JSON = b'{"synthetic": true}\n'

UPLOAD = 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'
HEAD = 'jobs:\n  build:\n    steps:\n      - name: Save\n'


def workflow(paths=(), inline=None, path_first=False):
    uses = f'        uses: {UPLOAD}\n'
    if inline is not None:
        section = f'        with:\n          path: {inline}\n'
    else:
        section = '        with:\n          path: |\n' + ''.join(f'            {p}\n' for p in paths)
    return HEAD + (section + uses if path_first else uses + section)


def raw_step(section):
    """An upload step whose `with:` section is given verbatim (for unusual path forms)."""
    return HEAD + f'        uses: {UPLOAD}\n' + section


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def make(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_fixture_outputs_pass(self):
        self.assertIsNone(entry_problem(self.make('smoke.pce', FIXTURE_PCE)))
        self.assertIsNone(entry_problem(self.make('evidence.json', PLAIN_JSON)))
        self.assertIsNone(entry_problem(self.make('geargrafx-evidence.json', PLAIN_JSON)))

    def test_linked_executable_without_suffix_is_refused(self):
        self.assertIsNotNone(entry_problem(self.make('geargrafx-cdread-probe', ELF_PROBE)))

    def test_object_and_archive_are_refused(self):
        self.assertIsNotNone(entry_problem(self.make('probe.o', PLAIN_JSON)))
        self.assertIsNotNone(entry_problem(self.make('libgeargrafx.a', AR_ARCHIVE)))
        self.assertIsNotNone(entry_problem(self.make('probe.map', AR_ARCHIVE)))

    def test_macho_and_pe_signatures_are_refused(self):
        self.assertIsNotNone(entry_problem(self.make('probe.bin.dat', MACHO)))
        self.assertIsNotNone(entry_problem(self.make('probe.dat', PE_STUB)))

    def test_short_mz_fixture_is_not_refused(self):
        self.assertIsNone(entry_problem(self.make('rom.dat', FIXTURE_PCE)))

    def test_mz_with_ne_or_le_header_is_refused(self):
        for tag in (b'NE', b'LE', b'LX'):
            with self.subTest(tag=tag):
                data = b'MZ' + b'\x00' * 58 + (0x40).to_bytes(4, 'little') + tag + b'\x00\x00'
                self.assertIn('executable', entry_problem(self.make('old-' + tag.decode() + '.dat', data)))

    def test_archives_are_refused_by_suffix_and_magic(self):
        for suffix in ('.zip', '.tar', '.gz', '.7z', '.tgz', '.xz', '.rar'):
            with self.subTest(suffix=suffix):
                self.assertIn('archive suffix', entry_problem(self.make('a' + suffix, PLAIN_JSON)))
        for name, magic in (('zip', b'PK\x03\x04'), ('gz', b'\x1f\x8b\x08'), ('7z', b'7z\xbc\xaf\x27\x1c'),
                            ('bz2', b'BZh9'), ('xz', b'\xfd7zXZ\x00'), ('rar', b'Rar!\x1a\x07')):
            with self.subTest(magic=name):
                self.assertEqual('archive signature', entry_problem(self.make('blob-' + name + '.dat', magic + b'\x00' * 8)))
        tar = b'\x00' * 257 + b'ustar\x00' + b'\x00' * 10
        self.assertEqual('archive signature', entry_problem(self.make('blob.tar.dat', tar)))

    def test_pe_header_past_4k_is_refused(self):
        # Regression (review finding 3): e_lfanew above the old 4096-byte head window was never checked.
        pe_offset = 5000
        data = b'MZ' + b'\x00' * 58 + pe_offset.to_bytes(4, 'little') + b'\x00' * (pe_offset - 64) + b'PE\x00\x00'
        self.assertIn('PE/NE/LE executable', entry_problem(self.make('late-pe.dat', data)))

    def test_mz_with_pe_header_beyond_eof_is_refused(self):
        # Regression (review finding 3): a header that cannot be read in full must fail closed.
        data = b'MZ' + b'\x00' * 58 + (0x1000).to_bytes(4, 'little') + b'\x00' * 40
        self.assertIsNotNone(entry_problem(self.make('truncated-pe.dat', data)))

    def test_symlink_is_refused(self):
        target = self.make('target.pce', FIXTURE_PCE)
        link = self.root / 'link.pce'
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable on this host')
        self.assertEqual(entry_problem(link), 'symlink')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / '.github' / 'workflows').mkdir(parents=True)

    def write_workflow(self, text, name='ci.yml'):
        (self.root / '.github' / 'workflows' / name).write_text(text, encoding='utf-8')

    def write_build(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_block_and_inline_path_forms_are_parsed(self):
        self.assertEqual(upload_patterns(workflow(['build/debug/*.pce', 'build/debug/*.json'])),
                         ['build/debug/*.pce', 'build/debug/*.json'])
        self.assertEqual(upload_patterns(workflow(inline='build/debug/*.pce')), ['build/debug/*.pce'])

    def test_path_before_uses_is_parsed_as_a_whole_step(self):
        # Regression (review finding 1): key order inside the step must not drop its patterns.
        self.assertEqual(upload_patterns(workflow(['build/debug/*.pce'], path_first=True)), ['build/debug/*.pce'])
        self.write_workflow(workflow(['build/debug/*.pce'], path_first=True))
        self.write_build('build/debug/linked-probe.pce', ELF_PROBE)
        with self.assertRaisesRegex(ValueError, 'linked-probe'):
            check_upload_artifacts(self.root)

    def test_keep_chomping_block_is_parsed(self):
        # Regression (review finding 3): `|+` used to become a literal pattern that matched nothing.
        section = '        with:\n          path: |+\n            build/debug/*.pce\n'
        self.assertEqual(upload_patterns(raw_step(section)), ['build/debug/*.pce'])
        self.write_workflow(raw_step(section))
        self.write_build('build/debug/linked-probe.pce', ELF_PROBE)
        with self.assertRaisesRegex(ValueError, 'linked-probe'):
            check_upload_artifacts(self.root)

    def test_flow_list_is_parsed(self):
        # Regression (review finding 3): `[a, b]` used to become one literal pattern that matched nothing.
        section = '        with:\n          path: [build/debug/*.pce, build/debug/linked-probe]\n'
        self.assertEqual(upload_patterns(raw_step(section)), ['build/debug/*.pce', 'build/debug/linked-probe'])
        self.write_workflow(raw_step(section))
        self.write_build('build/debug/linked-probe', ELF_PROBE)
        with self.assertRaisesRegex(ValueError, 'linked-probe'):
            check_upload_artifacts(self.root)

    def test_unrecognised_path_forms_are_refused(self):
        for section in ('        with:\n          path: >\n            build/debug/*.pce\n',
                        '        with:\n          path: ["build/a,b.pce"]\n',
                        '        with:\n          path:\n'):
            with self.subTest(section=section):
                self.write_workflow(raw_step(section))
                with self.assertRaises(ValueError):
                    check_upload_artifacts(self.root)

    def test_upload_step_without_path_fails_closed(self):
        self.write_workflow(raw_step('        with:\n          name: x\n'))
        with self.assertRaisesRegex(ValueError, 'without a parseable path'):
            check_upload_artifacts(self.root)

    def test_glob_that_reaches_a_linked_executable_fails_closed(self):
        self.write_workflow(workflow(['build/debug/**']))
        self.write_build('build/debug/cdread-probe/geargrafx-cdread-probe', ELF_PROBE)
        with self.assertRaisesRegex(ValueError, 'geargrafx-cdread-probe'):
            check_upload_artifacts(self.root)

    def test_directory_match_is_checked_recursively(self):
        # Regression (review finding 2): a glob matching a directory used to pass its contents unchecked.
        self.write_workflow(workflow(['build/debug/*.json']))
        self.write_build('build/debug/evidence.json/nested/linked-probe', ELF_PROBE)
        with self.assertRaisesRegex(ValueError, 'linked-probe'):
            check_upload_artifacts(self.root)

    def test_unreadable_directory_fails_closed(self):
        # os.walk skips directories it cannot list; the guard must refuse instead of passing them unseen.
        self.write_workflow(workflow(['build/debug/*.json']))
        self.write_build('build/debug/evidence.json/nested/file.txt', PLAIN_JSON)
        real = guard.os.walk
        def failing(top, onerror=None, **kwargs):
            if onerror:
                onerror(PermissionError('simulated unreadable directory'))
            return real(top, onerror=onerror, **kwargs)
        with unittest.mock.patch.object(guard.os, 'walk', failing):
            with self.assertRaises(PermissionError):
                check_upload_artifacts(self.root)

    def test_symlink_inside_matched_directory_is_refused(self):
        self.write_workflow(workflow(['build/debug/*.json']))
        self.write_build('build/debug/evidence.json/target.pce', FIXTURE_PCE)
        link = self.root / 'build/debug/evidence.json/link.pce'
        try:
            link.symlink_to(self.root / 'build/debug/evidence.json/target.pce')
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'link.pce'):
            check_upload_artifacts(self.root)

    def test_probe_directory_is_not_matched_by_fixture_globs(self):
        self.write_workflow(workflow(['build/debug/*.pce', 'build/debug/*.json']))
        self.write_build('build/debug/geargrafx-api-probe', ELF_PROBE)
        self.write_build('build/debug/smoke-test-not-bios.pce', FIXTURE_PCE)
        check_upload_artifacts(self.root)

    def test_quoted_path_key_next_to_env_is_refused(self):
        # Review finding 3: `"path":` was invisible to the parser, so the ELF-magic file below passed.
        self.write_build('build/probe', ELF_PROBE)
        self.write_workflow(HEAD + f'        uses: {UPLOAD}\n        env:\n          X: 1\n        with:\n'
                            '          "path": build/probe\n')
        with self.assertRaisesRegex(ValueError, 'ambiguous path key'):
            check_upload_artifacts(self.root)
        for section in ('          path : build/probe\n', "          'path': build/probe\n",
                        '          path: a\n          "path": b\n'):
            with self.subTest(section=section):
                with self.assertRaises(ValueError):
                    upload_patterns(raw_step('        with:\n' + section))

    def test_tilde_home_pattern_is_refused(self):
        for pattern in ('~/x', '~', '~/.ssh/*'):
            with self.subTest(pattern=pattern):
                self.write_workflow(workflow([pattern]))
                with self.assertRaisesRegex(ValueError, 'unsafe pattern'):
                    check_upload_artifacts(self.root)

    def test_post_build_checks_real_output_and_fails_closed(self):
        step = 'Save'
        self.write_workflow(workflow(['build/out/*.pce']))
        rel = '.github/workflows/ci.yml'
        with self.assertRaisesRegex(ValueError, 'matched no built artifact'):   # build output missing
            guard.check_post_build(rel, step, self.root)
        self.write_build('build/out/ok.pce', FIXTURE_PCE)
        guard.check_post_build(rel, step, self.root)
        self.write_build('build/out/bad.pce', ELF_PROBE)
        with self.assertRaisesRegex(ValueError, 'executable signature'):
            guard.check_post_build(rel, step, self.root)
        with self.assertRaisesRegex(ValueError, 'expected exactly one'):   # unknown step name
            guard.check_post_build(rel, 'Nope', self.root)

    def test_unsafe_or_expression_patterns_are_refused(self):
        for pattern in ('../outside/*.pce', '/abs/*.pce', '${{ github.workspace }}/*.pce'):
            with self.subTest(pattern=pattern):
                self.write_workflow(workflow([pattern]))
                with self.assertRaises(ValueError):
                    check_upload_artifacts(self.root)

    def test_uses_key_spellings_are_parsed_as_upload_steps(self):
        # Regression (review finding 2): case, spacing before the colon and quotes must not skip the step.
        sha = UPLOAD.split('@', 1)[1]
        spellings = (f'uses: Actions/Upload-Artifact@{sha}',
                     f'uses : actions/upload-artifact@{sha}',
                     f'"uses": "actions/upload-artifact@{sha}"')
        for uses_line in spellings:
            with self.subTest(uses=uses_line):
                text = HEAD + f'        {uses_line}\n        with:\n          path: |\n            build/debug/*.pce\n'
                self.assertEqual(upload_patterns(text), ['build/debug/*.pce'])
                self.write_workflow(text)
                self.write_build('build/debug/linked-probe.pce', ELF_PROBE)
                with self.assertRaisesRegex(ValueError, 'linked-probe'):
                    check_upload_artifacts(self.root)

    def test_upload_reference_that_is_not_a_parsed_step_fails_closed(self):
        # Regression (review finding 2): an upload-artifact mention outside a parsed step must not pass silently.
        self.write_workflow(workflow(['build/debug/*.json']) + '        run: echo upload-artifact\n')
        with self.assertRaisesRegex(ValueError, 'not a parsed step'):
            check_upload_artifacts(self.root)
        self.write_workflow(HEAD + '        uses: docker://example/upload-artifact:1\n')
        with self.assertRaisesRegex(ValueError, 'not a parsed step'):
            check_upload_artifacts(self.root)

    def test_brace_extglob_and_alternation_patterns_are_refused(self):
        # Regression (review finding 1): pathlib matched nothing for these, so an ELF in the tree was accepted.
        self.write_build('build/debug/linked-probe', ELF_PROBE)
        self.write_build('build/other/linked-probe', ELF_PROBE)
        for pattern in ('build/{debug,other}/*.pce', 'build/@(debug|other)/*.pce', 'build/debug/*.{pce,json}',
                        'build/(debug)/*.pce', 'build/debug|other/*.pce'):
            with self.subTest(pattern=pattern):
                self.write_workflow(workflow([pattern]))
                with self.assertRaisesRegex(ValueError, 'unsupported pattern'):
                    check_upload_artifacts(self.root)

    def test_yaml_extension_workflow_is_scanned(self):
        # Review MAJOR: a new *.yaml workflow was never globbed, so its ELF upload bypassed the guard.
        self.write_workflow(workflow(['build/debug/*.pce']), name='ci.yml')
        self.write_workflow(workflow(['build/probe/*']), name='extra.yaml')
        self.write_build('build/probe/linked-probe', ELF_PROBE)
        with self.assertRaisesRegex(ValueError, r'extra\.yaml: build/probe/linked-probe'):
            check_upload_artifacts(self.root)
        for name, data in (('probe.exe', PLAIN_JSON), ('probe.obj', PLAIN_JSON), ('macho', MACHO), ('pe.dat', PE_STUB)):
            with self.subTest(name=name):
                self.write_build('build/probe/' + name, data)
                self.write_build('build/probe/linked-probe', FIXTURE_PCE)
                with self.assertRaisesRegex(ValueError, 'extra.yaml'):
                    check_upload_artifacts(self.root)
                (self.root / 'build/probe' / name).unlink()

    def test_pages_artifact_composite_action_and_release_routes_are_guarded(self):
        pages = workflow(['site']).replace('upload-artifact', 'upload-pages-artifact')
        self.write_workflow(pages)
        self.write_build('site/tool', ELF_PROBE)
        with self.assertRaisesRegex(ValueError, 'site/tool'):
            check_upload_artifacts(self.root)
        self.write_workflow(workflow(['build/debug/*.pce']))
        action = self.root / '.github' / 'actions' / 'save'
        action.mkdir(parents=True)
        (action / 'action.yaml').write_text(workflow(['site']), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'action.yaml: site/tool'):
            check_upload_artifacts(self.root)
        for route in ('run: gh release upload v1 build/x', 'uses: softprops/action-gh-release@v2',
                      'uses: ncipollo/release-action@v1', 'uses: ./.github/actions/up',
                      'uses: o/r/.github/workflows/u.yml@v1'):
            with self.subTest(route=route):
                self.write_workflow(workflow(['build/debug/*.pce']) + f'      - {route}\n', name='rel.yaml')
                with self.assertRaisesRegex(ValueError, 'not a parsed step'):
                    check_upload_artifacts(self.root)

    def test_prose_containing_release_is_not_a_false_positive(self):
        self.write_workflow(workflow(['build/debug/*.pce']) + '      - name: pass through release gate\n', name='ok.yaml')
        self.write_build('build/debug/smoke-test-not-bios.pce', FIXTURE_PCE)
        check_upload_artifacts(self.root)

    def test_legitimate_evidence_and_self_made_rom_in_yaml_workflow_pass(self):
        self.write_workflow(workflow(['build/debug/*.pce', 'build/debug/*.json']), name='ok.yaml')
        self.write_build('build/debug/geargrafx-evidence.json', PLAIN_JSON)
        self.write_build('build/debug/smoke-test-not-bios.pce', FIXTURE_PCE)
        check_upload_artifacts(self.root)

    def test_missing_upload_steps_fail_closed(self):
        self.write_workflow('jobs:\n  build:\n    steps:\n      - run: make check\n')
        with self.assertRaisesRegex(ValueError, 'no upload-artifact'):
            check_upload_artifacts(self.root)


CALL = 'python3 tools/artifact_guard.py --post-build w.yml "Save"'
GUARD_STEP = f'      - name: Guard\n        run: |\n          {CALL}\n'
SAVE_STEP = f'      - name: Save\n        uses: {UPLOAD}\n        with:\n          path: build/x\n'
GUARDED = 'jobs:\n  build:\n    steps:\n' + GUARD_STEP + SAVE_STEP


class GuardPlacementTests(unittest.TestCase):
    def violations(self, text):
        return guard.guard_placement_violations(text, 'w.yml')

    def test_well_formed_guard_passes(self):
        self.assertEqual([], self.violations(GUARDED))

    def test_each_defect_is_a_violation(self):
        head = 'jobs:\n  build:\n    steps:\n'
        defects = {
            'if': GUARDED.replace('        run: |', '        if: ${{ false }}\n        run: |'),
            'continue-on-error': GUARDED.replace('        run: |', '        continue-on-error: true\n        run: |'),
            'commented': GUARDED.replace('          ' + CALL, '          # ' + CALL),
            'other job': head + GUARD_STEP + '  other:\n    steps:\n' + SAVE_STEP,
            'after upload': head + SAVE_STEP + GUARD_STEP,
            'duplicate': head + GUARD_STEP + GUARD_STEP.replace('Guard', 'Guard2') + SAVE_STEP,
            'not a run': GUARDED.replace('        run: |\n          ' + CALL, '        name2: x\n        with:\n          x: ' + CALL),
            'missing': GUARDED.replace(CALL, 'true'),
        }
        for label, text in defects.items():
            with self.subTest(defect=label):
                self.assertNotEqual([], self.violations(text))


    def test_call_must_be_the_run_command(self):
        run = '        run: |\n          ' + CALL
        bad = {
            'echo comment': f'        run: echo # {CALL}',
            'echo call': f'        run: echo {CALL}',
            'env only': f'        env:\n          X: {CALL}\n        run: true',
            'block echo': '        run: |\n          echo ' + CALL,
            'comment first': '        run: |\n          # ' + CALL,
        }
        for label, step in bad.items():
            with self.subTest(bad=label):
                text = GUARDED.replace(run, step)
                self.assertNotEqual(text, GUARDED)
                self.assertNotEqual([], self.violations(text))

    def test_accepted_run_forms(self):
        run = '        run: |\n          ' + CALL
        for step in (f'        run: {CALL}',
                     '        run: python3 tools/artifact_guard.py --post-build w.yml "Save" --x',
                     '        run: >\n          ' + CALL, run + '\n          echo done'):
            with self.subTest(step=step):
                self.assertEqual([], self.violations(GUARDED.replace(run, step)))

    def test_flow_style_steps_raise_clear_error(self):
        with self.assertRaisesRegex(ValueError, 'w.yml: flow-style steps unsupported'):
            self.violations('jobs:\n  build:\n    steps: [ {uses: ' + UPLOAD + '} ]\n')


class RepositoryTests(unittest.TestCase):
    def test_every_repository_upload_step_has_a_post_build_guard_before_it(self):
        # Owner decision: the guard runs after the build and before upload-artifact, in every upload workflow.
        for path in sorted((ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            rel = path.relative_to(ROOT).as_posix()
            self.assertEqual([], guard.guard_placement_violations(path.read_text(encoding='utf-8'), rel))

    def test_repository_upload_patterns_are_visible_to_the_guard(self):
        patterns = []
        for path in sorted((ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            patterns.extend(upload_patterns(path.read_text(encoding='utf-8')))
        self.assertIn('build/debug/cdread-probe/*.pce', patterns)
        self.assertIn('build/debug/*.pce', patterns)
        self.assertIn('${{ runner.temp }}/verdict/cdread-verdict.json', patterns)

    def test_repository_workflows_pass_without_local_build_outputs(self):
        check_upload_artifacts(ROOT)


if __name__ == '__main__':
    unittest.main()
