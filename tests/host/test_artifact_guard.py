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

    def test_guard_exit_status_must_not_be_swallowed(self):
        run = '        run: |\n          ' + CALL
        bad = {
            'or true': f'        run: {CALL} || true',
            'background': f'        run: {CALL} &',
            'pipe': f'        run: {CALL} | cat',
            'semicolon': f'        run: {CALL} ; true',
            'and then or': f'        run: {CALL} && echo x || true',
            'and tail inline': f'        run: {CALL} && echo done',
            'block or true': run + ' || true',
            'block continuation': run + ' \\n          || true',
            'shell python': '        shell: python\n' + run,
            'shell echo': '        shell: echo {0}\n' + run,
        }
        for label, step in bad.items():
            with self.subTest(bad=label):
                text = GUARDED.replace(run, step)
                self.assertNotEqual(text, GUARDED)
                self.assertNotEqual([], self.violations(text))

    def test_accepted_run_forms(self):
        run = '        run: |\n          ' + CALL
        for step in (f'        run: {CALL}',
                     '        run: >-\n          ' + CALL, run,
                     '        shell: bash\n' + run, '        shell: "bash"\n' + run):
            with self.subTest(step=step):
                self.assertEqual([], self.violations(GUARDED.replace(run, step)))

    def test_single_live_line_is_required(self):
        run = '        run: |\n          ' + CALL
        bad = {
            'multi-line and': run + ' && echo a\n          echo done',
            'second line': run + '\n          echo done',
            'and tail': run + ' && echo done',
            'comment tail': run + ' # note',
            'set +e': '        run: |\n          set +e\n          ' + CALL,
            'set +o errexit': '        run: |\n          set +o errexit\n          ' + CALL,
        }
        for label, step in bad.items():
            with self.subTest(bad=label):
                text = GUARDED.replace(run, step)
                self.assertNotEqual(text, GUARDED)
                self.assertNotEqual([], self.violations(text))

    def test_flow_style_steps_raise_clear_error(self):
        with self.assertRaisesRegex(ValueError, 'w.yml: flow-style steps unsupported'):
            self.violations('jobs:\n  build:\n    steps: [ {uses: ' + UPLOAD + '} ]\n')

    def test_build_step_inserted_between_guard_and_upload_is_refused(self):
        build = '      - name: Build\n        run: make build\n'
        self.assertNotEqual([], self.violations(GUARDED.replace(SAVE_STEP, build + SAVE_STEP)))
        mutate = '      - name: Mutate\n        run: cp x build/x\n'
        self.assertNotEqual([], self.violations(GUARDED.replace(SAVE_STEP, mutate + SAVE_STEP)))

    def test_guard_must_be_the_step_immediately_before_each_upload(self):
        build = '      - name: Build\n        run: make build\n'
        text = 'jobs:\n  build:\n    steps:\n' + GUARD_STEP + SAVE_STEP + build + SAVE_STEP
        self.assertNotEqual([], self.violations(text))
        text = 'jobs:\n  build:\n    steps:\n' + GUARD_STEP + build + SAVE_STEP
        self.assertNotEqual([], self.violations(text))

    def test_reordered_guard_after_build_is_refused(self):
        build = '      - name: Build\n        run: make build\n'
        text = 'jobs:\n  build:\n    steps:\n' + SAVE_STEP + build + GUARD_STEP
        self.assertNotEqual([], self.violations(text))

    def test_dotted_job_name_cannot_borrow_another_jobs_guard(self):
        # Before the fix, "a.b" and "a.c" both parsed as job None, so a guard in one satisfied the other.
        text = 'jobs:\n  a.b:\n    steps:\n' + GUARD_STEP + '  a.c:\n    steps:\n' + SAVE_STEP
        self.assertNotEqual([], self.violations(text))

    def test_quoted_job_name_cannot_borrow_another_jobs_guard(self):
        text = 'jobs:\n  "a.b":\n    steps:\n' + GUARD_STEP + "  'a.c':\n    steps:\n" + SAVE_STEP
        self.assertNotEqual([], self.violations(text))

    def test_dotted_job_names_each_with_own_guard_pass(self):
        # Legitimate dotted names must not be mistaken for a duplicate or missing guard.
        text = ('jobs:\n  build.x:\n    steps:\n' + GUARD_STEP + SAVE_STEP
                + '  build.y:\n    steps:\n' + GUARD_STEP.replace('Guard', 'Guard2') + SAVE_STEP)
        self.assertEqual([], self.violations(text))

    def test_duplicate_job_name_is_refused(self):
        text = 'jobs:\n  build:\n    steps:\n' + GUARD_STEP + '  build:\n    steps:\n' + SAVE_STEP
        with self.assertRaisesRegex(ValueError, 'duplicate job name'):
            self.violations(text)

    def test_guard_step_with_env_or_working_directory_is_refused(self):
        run = '        run: |\n          ' + CALL
        bad = {
            'env RUNNER_TEMP': '        env:\n          RUNNER_TEMP: /tmp/x\n' + run,
            'env PATH': '        env:\n          PATH: /tmp/x\n' + run,
            'working-directory': '        working-directory: /tmp\n' + run,
            'duplicate run': run + '\n        run: true',
            'extra key': '        timeout-minutes: 1\n' + run,
        }
        for label, step in bad.items():
            with self.subTest(bad=label):
                text = GUARDED.replace(run, step)
                self.assertNotEqual(text, GUARDED)
                self.assertNotEqual([], self.violations(text))

    def test_job_or_workflow_env_and_defaults_are_refused(self):
        bad = {
            'job env': GUARDED.replace('    steps:\n', '    env:\n      PATH: /tmp/x\n    steps:\n', 1),
            'top env': 'env:\n  RUNNER_TEMP: /tmp/x\n' + GUARDED,
            'job defaults': GUARDED.replace('    steps:\n', '    defaults:\n      run:\n        working-directory: /tmp\n    steps:\n', 1),
            'job container': GUARDED.replace('    steps:\n', '    container: other-image\n    steps:\n', 1),
        }
        for label, text in bad.items():
            with self.subTest(bad=label):
                self.assertNotEqual([], self.violations(text))

    def test_upload_name_cannot_smuggle_shell_into_guard_call(self):
        # MAJOR-1: the guard call embeds the upload step's name; a quote-breaking name must not make the
        # run line "guard "Save" || true; echo "x"" pass as a bare guard call (guard failure swallowed, upload runs).
        name = 'Save" || true; echo "x'
        run = f'        run: python3 tools/artifact_guard.py --post-build w.yml "{name}"'
        text = ('jobs:\n  build:\n    steps:\n' + '      - name: Guard\n' + run + '\n'
                + f'      - name: {name}\n        uses: {UPLOAD}\n        with:\n          path: build/x\n')
        self.assertNotEqual([], self.violations(text))

    def test_upload_name_with_expansion_or_escape_characters_is_refused(self):
        for name in ('Save $(rm -rf x)', 'Save `id`', 'Save \\\\ x', 'Save $HOME'):
            with self.subTest(name=name):
                call = f'python3 tools/artifact_guard.py --post-build w.yml "{name}"'
                text = ('jobs:\n  build:\n    steps:\n      - name: Guard\n        run: |\n          ' + call + '\n'
                        + f'      - name: {name}\n        uses: {UPLOAD}\n        with:\n          path: build/x\n')
                self.assertNotEqual([], self.violations(text))

    def test_run_line_with_extra_argument_is_refused(self):
        run = '        run: |\n          ' + CALL
        text = GUARDED.replace(run, '        run: ' + CALL + ' --x')
        self.assertNotEqual([], self.violations(text))

    def test_upload_step_if_and_continue_on_error_are_refused(self):
        # MAJOR-2: only name, uses, with are allowed on the upload step.
        bad = {
            'if': SAVE_STEP.replace('        uses:', '        if: ${{ false }}\n        uses:'),
            'continue-on-error': SAVE_STEP.replace('        uses:', '        continue-on-error: true\n        uses:'),
            'env': SAVE_STEP.replace('        uses:', '        env:\n          X: 1\n        uses:'),
        }
        for label, step in bad.items():
            with self.subTest(bad=label):
                self.assertNotEqual([], self.violations(GUARDED.replace(SAVE_STEP, step)))

    def test_guard_in_other_job_that_is_needed_is_refused(self):
        # needs: is not a substitute for a guard in the upload's own job, immediately before it.
        text = ('jobs:\n  build:\n    steps:\n' + GUARD_STEP
                + '  publish:\n    needs: build\n    steps:\n' + SAVE_STEP)
        self.assertNotEqual([], self.violations(text))

    def test_make_check_path_enforces_placement_not_only_patterns(self):
        # check.py calls check_guard_placement; a build step inserted after the guard must fail there too.
        import tempfile
        rel = '.github/workflows/w.yml'
        call = f'python3 tools/artifact_guard.py --post-build {rel} "Save"'
        guard_step = f'      - name: Guard\n        run: |\n          {call}\n'
        build = '      - name: Build\n        run: make build\n'
        text = ('jobs:\n  build:\n    steps:\n' + guard_step + build
                + f'      - name: Save\n        uses: {UPLOAD}\n        with:\n          path: build/x\n')
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.github', 'workflows').mkdir(parents=True)
            Path(tmp, rel).write_text(text, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'guard step is not the step immediately before'):
                guard.check_guard_placement(Path(tmp))


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
