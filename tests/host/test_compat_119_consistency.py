"""Consistency between the #116 verdict and the real #119 trace judge.

Each scenario is one probe log. The verdict is fed the job result that #119 run_job() returns,
and the reference is the real geargrafx_harness.classify() (parse_records() runs inside it).
Nothing from #119 is restated here. Both must give the same category (PASS / FAIL / BLOCKED).
"""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests' / 'integration'))
import geargrafx_harness as harness  # noqa: E402
from compat_evidence import STAGE_LABEL, verdict  # noqa: E402

BUDGET = 12_000_000
LABEL = STAGE_LABEL['playable']
FORBID = 'cd_halt_fail'
CAP_OK = {'instruction_step': True, 'cdrom_hardware': True, 'cdrom_media': True, 'disc_given': True}


def cap(**overrides):
    return {'tag': 'CAP', **CAP_OK, **overrides}


def hit(name, cycle):
    return {'tag': 'HIT', 'name': name, 'cycle': cycle}


def run_ev(cycle, reason=LABEL, step=1000, arrival=936, **overrides):
    ev = {'tag': 'RUN', 'name': reason, 'reason': reason, 'cycle': cycle, 'pc': 1, 'step': step,
          'halt_confirmed': True, 'halt_confirmation_steps': 64, 'halt_confirmation_required': 64,
          'halted_after_step': arrival}
    ev.update(overrides)
    return ev


def good(label=LABEL):
    return [cap(), hit(label, 'cold'), run_ev('cold', label), hit(label, 'warm'), run_ev('warm', label)]


def scenario(events, expected, **overrides):
    base = {'kind': 'cdread', 'exit_code': 0, 'timed_out': False, 'output_capped': False,
            'events': events, 'expect': LABEL, 'forbid': (FORBID,), 'budget': BUDGET}
    base.update(overrides)
    return base, expected


def _with(index, **overrides):
    """GOOD with one event replaced or merged by index."""
    events = copy.deepcopy(good())
    events[index].update(overrides)
    return events


SCENARIOS = {
    'good': scenario(good(), 'PASS'),
    'api_kind_not_supported': scenario(good(), 'BLOCKED', kind='api'),
    'probe_exit_2': scenario(good(), 'FAIL', exit_code=2),
    'probe_exit_6_debugger_missing': scenario(good(), 'BLOCKED', exit_code=6),
    'probe_exit_nonzero_other': scenario(good(), 'FAIL', exit_code=1),
    'timeout': scenario(good(), 'FAIL', timed_out=True),
    'output_capped': scenario(good(), 'FAIL', output_capped=True),
    'block_event': scenario(good() + [{'tag': 'BLOCK', 'reason': 'debugger missing'}], 'BLOCKED'),
    'no_cap_record': scenario([e for e in good() if e['tag'] != 'CAP'], 'BLOCKED'),
    'cap_instruction_step_false': scenario([cap(instruction_step=False)] + good()[1:], 'BLOCKED'),
    'cap_disc_not_given': scenario([cap(disc_given=False)] + good()[1:], 'BLOCKED'),
    'cap_cdrom_media_missing': scenario([{k: v for k, v in cap().items() if k != 'cdrom_media'}]
                                        + good()[1:], 'BLOCKED'),
    'cold_run_missing': scenario([e for e in good() if e != run_ev('warm')], 'FAIL'),
    'runs_reordered_warm_first': scenario([good()[0], hit(LABEL, 'cold'), hit(LABEL, 'warm'),
                                           run_ev('warm'), run_ev('cold')], 'FAIL'),
    'run_reason_cd_halt_fail': scenario(_with(4, reason='cd_halt_fail'), 'FAIL'),
    'run_budget_exhausted': scenario(_with(4, reason='budget'), 'FAIL'),
    'halt_not_confirmed': scenario(_with(2, halt_confirmed=False), 'FAIL'),
    'halt_confirmation_63': scenario(_with(2, halt_confirmation_steps=63), 'FAIL'),
    'halt_step_arrival_mismatch': scenario(_with(2, step=999), 'FAIL'),
    'step_over_budget': scenario(_with(2, step=BUDGET + 1, halted_after_step=BUDGET - 63), 'FAIL'),
    'step_negative': scenario(_with(2, step=-1, halted_after_step=-65), 'FAIL'),
    'step_float': scenario(_with(2, step=1000.0), 'FAIL'),
    'hit_cold_missing': scenario([cap(), run_ev('cold'), hit(LABEL, 'warm'), run_ev('warm')], 'FAIL'),
    'hit_duplicate_cold': scenario([cap(), hit(LABEL, 'cold'), hit(LABEL, 'cold'), run_ev('cold'),
                                    hit(LABEL, 'warm'), run_ev('warm')], 'FAIL'),
    'hit_after_run_only': scenario([cap(), run_ev('cold'), hit(LABEL, 'cold'), hit(LABEL, 'warm'),
                                    run_ev('warm')], 'FAIL'),
    'hit_before_and_after_run': scenario([cap(), hit(LABEL, 'cold'), run_ev('cold'), hit(LABEL, 'cold'),
                                          hit(LABEL, 'warm'), run_ev('warm')], 'FAIL'),
    'hit_for_other_cycle': scenario([cap(), hit(LABEL, 'cold'), run_ev('cold'), hit(LABEL, 'hot'),
                                     run_ev('hot')], 'FAIL'),
    'forbidden_label_reached': scenario(good() + [hit(FORBID, 'cold')], 'FAIL'),
}


def job(s):
    """The #119 run_job() result for a scenario; timeouts and output caps carry status FAIL."""
    stdout = '\n'.join(f"{e['tag']} {json.dumps({k: v for k, v in e.items() if k != 'tag'})}" for e in s['events'])
    status, reason = None, None
    if s['timed_out']:
        status, reason = 'FAIL', 'timeout'
    elif s['output_capped']:
        status, reason = 'FAIL', 'output exceeded'
    return {'status': status, 'reason': reason, 'exit_code': s['exit_code'], 'stdout': stdout}


def classify_119(s):
    """Reference: the real #119 classify() on the job result."""
    return harness.classify(job(s), s['expect'], s['forbid'], s['budget'], s['kind'])[0]


def probe(s):
    """What the #116 verdict receives as raw #119 output for a scenario."""
    return {'result': job(s), 'forbid': list(s['forbid']), 'budget': s['budget'], 'kind': s['kind']}


def compat_record(stage='playable'):
    label = STAGE_LABEL[stage]
    return {'schema': 'pce-compat-evidence-v1', 'stage': stage, 'result': 'PASS',
            'claim_scope': 'single-launch', 'rom': {'sha256': 'a' * 64},
            'emulator': {'kind': 'real-core', 'name': 'Geargrafx', 'version': '1.0', 'commit': 'b' * 40},
            'fixture': {'id': 'local-retail-001', 'sha256': 'd' * 64, 'origin': 'local-proprietary'},
            'region': 'JP', 'checkpoints': [{'name': label, 'expected': label, 'observed': label}],
            'trace_deltas': [{'name': 'irq-vector', 'delta': 1}], 'blocker': None}


def claim(record, expected):
    """Reporter's claim equal to the #119 category, so only real overclaims count."""
    record['result'] = expected
    if expected != 'PASS':
        record['blocker'] = 'scenario ' + expected
    return record


def attestation_119(status, record):
    """#119 attestation shape: verified only when its own judge says PASS."""
    return {'verified': status == 'PASS', 'rom_sha256': record['rom']['sha256'],
            'fixture_sha256': record['fixture']['sha256'], 'emulator_commit': record['emulator']['commit'],
            'checkpoints': [{'name': c['name'], 'observed': c['expected']}
                            for c in record['checkpoints']] if status == 'PASS' else []}


class Consistency119Tests(unittest.TestCase):
    def test_every_scenario_gets_the_same_category_from_both_judges(self):
        for name, (s, expected) in SCENARIOS.items():
            with self.subTest(scenario=name):
                self.assertEqual(classify_119(s), expected)
                record = claim(compat_record(), expected)
                # Verified attestation in every case, so only the probe path decides the category.
                runner = attestation_119('PASS', record)
                self.assertEqual(verdict(record, runner, probe(s))['result'], expected)

    def test_attestation_is_verified_only_for_pass(self):
        for name, (s, expected) in SCENARIOS.items():
            with self.subTest(scenario=name):
                record = claim(compat_record(), expected)
                runner = attestation_119(classify_119(s), record)
                result = verdict(record, runner, probe(s))['result']
                self.assertEqual(result == 'PASS', expected == 'PASS')

    def test_good_log_passes_at_every_upper_stage_on_both_sides(self):
        for stage, label in STAGE_LABEL.items():
            with self.subTest(stage=stage):
                s = dict(SCENARIOS['good'][0], expect=label, events=good(label))
                self.assertEqual(classify_119(s), 'PASS')
                record = compat_record(stage)
                runner = attestation_119('PASS', record)
                self.assertEqual(verdict(record, runner, probe(s))['result'], 'PASS')


if __name__ == '__main__':
    unittest.main()
