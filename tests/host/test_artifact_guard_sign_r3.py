"""Issue #337 round 3: signing-job `if:` regression tests (fail-closed key, tag, anchor and duplicate checks).

Each mutation is applied to the real api-source.yml in memory. The unmutated workflow must pass. On the round-2 guard
(a15b1ed) the tagged, anchored and duplicate-key cases below pass the check, which is the regression they pin.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

REL = '.github/workflows/api-source.yml'
JOBS = ('attest-cdread', 'attest-cdread-verdict')
VALUE = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
IF_LINE = '    if: ' + VALUE + '\n'


def real():
    return (guard.ROOT / REL).read_text(encoding='utf-8')


def mutate(job, new):
    """The real workflow with the approved `if:` line of `job` replaced by `new` (which must include a trailing newline)."""
    text = real()
    head = text.index(f'\n  {job}:\n')
    at = text.index(IF_LINE, head)
    return text[:at] + new + text[at + len(IF_LINE):]


def placement(text):
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, '.github', 'workflows').mkdir(parents=True)
        Path(tmp, REL).write_text(text, encoding='utf-8')
        guard.check_guard_placement(Path(tmp))


class SignFailClosedTests(unittest.TestCase):
    def assertRefused(self, text):
        self.assertNotEqual([], guard.attest_condition_violations(text, REL))
        with self.assertRaises(ValueError):
            placement(text)

    def test_unmutated_workflow_passes(self):
        self.assertEqual([], guard.attest_condition_violations(real(), REL))
        placement(real())

    def test_tagged_if_key_before_approved_line_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, "    !!str if: github.event_name == 'pull_request'\n" + IF_LINE))

    def test_tagged_if_key_after_approved_line_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + "    !!str if: github.event_name == 'pull_request'\n"))

    # The next three keep the approved line and add a second `if` key with the SAME value after it: PyYAML's last-wins
    # value is then still approved, so the round-2 guard passed them (they fail there) and only the key-form check refuses.
    def test_anchor_on_if_key_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + '    &k if: ' + VALUE + '\n'))
                self.assertRefused(mutate(job, '    &k if: ' + VALUE + '\n'))

    def test_tagged_quoted_if_key_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + '    !!str "if": ' + VALUE + '\n'))
                self.assertRefused(mutate(job, '    !!str "if": ' + VALUE + '\n'))

    def test_duplicate_plain_if_key_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + "    if: github.event_name == 'pull_request'\n"))

    def test_complex_key_if_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + '    ? if\n    : ' + VALUE + '\n'))
                self.assertRefused(mutate(job, '    ? if\n    : ' + VALUE + '\n'))

    def test_duplicate_other_job_key_is_refused_by_strict_loader(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + '    runs-on: ubuntu-24.04\n'))

    def test_anchor_on_job_value_is_refused_by_event_walk(self):
        for job in JOBS:
            with self.subTest(job=job):
                text = mutate(job, IF_LINE)
                head = text.index(f'\n  {job}:\n')
                at = text.index('    needs: ', head) + len('    needs: ')
                text = text[:at] + '&n ' + text[at:]
                self.assertRefused(text)


STAGE_WF = ("jobs:\n  build:\n    runs-on: ubuntu-24.04\n    steps:\n      - name: Save probe\n"
            "        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02\n"
            "        with:\n          name: probe\n          path: PATTERN\n")


class SignRound4Tests(unittest.TestCase):
    """Issue #337 round 4 (review residuals a-d). Each case mutates the real api-source.yml in memory and must be refused by
    --check-placement. On 20aeea3 (round 3) these mutations pass the placement check: that is the regression they pin."""

    PRODUCERS = (('attest-cdread', 'guard-bios-dev'), ('attest-cdread-verdict', 'guard-cdread-verdict'))
    ATTEST_USES = 'actions/attest-build-provenance@'

    def assertRefused(self, text, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            placement(text)

    def edit_job(self, job, fn):
        lines = real().splitlines()
        head = lines.index(f'  {job}:')
        end = next((j for j in range(head + 1, len(lines))
                    if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= 2), len(lines))
        return '\n'.join(fn(lines, head, end)) + '\n'

    def add_step(self, job, step):
        return self.edit_job(job, lambda L, h, e: L[:e] + step + L[e:])

    def insert_after(self, job, anchor, extra):
        def fn(L, h, e):
            i = next(i for i in range(h, e) if anchor in L[i])
            return L[:i + 1] + extra + L[i + 1:]
        return self.edit_job(job, fn)

    def replace_needs(self, job, new):
        def fn(L, h, e):
            i = next(i for i in range(h, e) if L[i].strip().startswith('needs:'))
            return L[:i] + new + L[i + 1:]
        return self.edit_job(job, fn)

    def test_unmutated_round4_checks_pass(self):
        self.assertEqual([], guard.signing_violations(real(), REL))
        self.assertEqual([], guard.rom_upload_violations(real(), REL))
        placement(real())

    def test_signing_text_in_a_signing_job_run_is_refused(self):   # (a)
        steps = [
            ['      - name: sign', '        run: cosign sign-blob evidence/cdread-probe/cdread-evidence.json'],
            ['      - run: |', '          sigstore sign evidence/cdread-probe/cdread-evidence.json'],
            ['      - run: gh attestation create evidence/cdread-probe/cdread-evidence.json'],
            ['      - run: curl -sS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" "$ACTIONS_ID_TOKEN_REQUEST_URL"'],
            ['      - {run: cosign verify-blob x}'],
            ['      - run: echo attest', '        continue-on-error: false'],
        ]
        for job, _ in self.PRODUCERS:
            for step in steps:
                with self.subTest(job=job, step=step[-1]):
                    self.assertRefused(self.add_step(job, step), r'round 4 \(a\)')

    def test_signing_text_in_a_new_job_is_refused(self):   # (a)
        extra = ['  sign-extra:', '    runs-on: ubuntu-24.04', '    permissions:', '      id-token: write',
                 '    steps:', '      - run: cosign sign-blob evidence/x.json']
        self.assertRefused(real() + '\n'.join(extra) + '\n', r'round 4 \(a\)')

    def test_new_job_with_attestations_write_is_refused(self):   # (a)
        extra = ['  mint:', '    runs-on: ubuntu-24.04', '    permissions:', '      attestations: write',
                 '    steps:', '      - run: echo hi']
        self.assertRefused(real() + '\n'.join(extra) + '\n', r'round 4 \(a\)')

    def test_needs_removal_or_weakening_on_a_signing_job_is_refused(self):   # (b)
        for job, producer in self.PRODUCERS:
            variants = {
                'removed': self.replace_needs(job, []),
                'empty flow list': self.replace_needs(job, ['    needs: []']),
                'wrong producer': self.replace_needs(job, ['    needs: bios-dev']),
                'block list without producer': self.replace_needs(job, ['    needs:', '      - bios-dev']),
            }
            for label, text in variants.items():
                with self.subTest(job=job, variant=label):
                    self.assertRefused(text, r'round 4 \(b\)')

    def test_continue_on_error_on_a_signing_job_is_refused(self):   # (c)
        for job, _ in self.PRODUCERS:
            with self.subTest(job=job, where='attest step'):
                text = self.insert_after(job, self.ATTEST_USES, ['        continue-on-error: true'])
                self.assertRefused(text, r'round 4 \(c\)')
            with self.subTest(job=job, where='job'):
                self.assertRefused(self.insert_after(job, '    runs-on:', ['    continue-on-error: false']), r'round 4 \(c\)')

    def test_new_rom_upload_path_is_refused(self):   # (d)
        for path in ['.intake/.artifact-stage/build/debug/evil/*.pce',
                     '.intake/.artifact-stage/build/debug/cdload-exec/payload.bin',
                     '.intake/.artifact-stage/build/debug/bios-dev/*']:
            with self.subTest(path=path):
                text = self.insert_after('guard-bios-dev', 'cdreset-probe/*.lbl', ['            ' + path])
                self.assertRefused(text, r'round 4 \(d\)')

    def stage(self, pattern, rel):
        with tempfile.TemporaryDirectory() as tmp:
            wf = Path(tmp, 'wf', '.github', 'workflows')
            wf.mkdir(parents=True)
            wf.joinpath('api-source.yml').write_text(STAGE_WF.replace('PATTERN', pattern), encoding='utf-8')
            intake = Path(tmp, 'intake')
            target = intake / rel
            target.parent.mkdir(parents=True)
            target.write_bytes(b'synthetic, not a ROM')
            guard.stage_artifacts('.github/workflows/api-source.yml', 'Save probe', root=intake,
                                  workflows_root=Path(tmp, 'wf'))

    def test_stage_refuses_a_rom_file_from_an_unapproved_pattern(self):   # (d)
        with self.assertRaisesRegex(ValueError, r'round 4 \(d\)'):
            self.stage('.intake/.artifact-stage/build/debug/evil/*.pce', 'build/debug/evil/x.pce')

    def test_stage_accepts_a_rom_file_from_the_approved_pattern(self):   # (d) positive control
        self.stage('.intake/.artifact-stage/build/debug/bios-dev/*.pce', 'build/debug/bios-dev/x.pce')
