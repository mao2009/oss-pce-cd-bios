"""Consistency between the #116 stage-evidence verdict and the #119 trace judge.

#119's classify() lives on its own branch, so judge_119() restates its rules from the issue
spec (items 1-9) instead of importing or copying that code. summarize() plays the runner: it
turns one probe log into the stage_evidence summary that #116 consumes. For every scenario
both sides must return the same category (PASS / FAIL / BLOCKED).
"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from compat_evidence import STAGE_LABEL, verdict

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


def judge_119(s):
    """#119 rules restated from the issue spec (items 1-9). Returns PASS, FAIL or BLOCKED."""
    if s['kind'] != 'cdread':
        return 'BLOCKED'                                             # 1 NOT_SUPPORTED
    if s['timed_out'] or s['output_capped']:
        return 'FAIL'                                                # 8
    if s['exit_code'] == 6:
        return 'BLOCKED'                                             # probe: debugger capability missing
    if s['exit_code'] != 0:
        return 'FAIL'                                                # 8 probe abnormal exit
    events, expect = s['events'], s['expect']
    caps = [e for e in events if e['tag'] == 'CAP']
    if len(caps) != 1 or caps[0].get('instruction_step') is not True:
        return 'BLOCKED'                                             # 7
    if any(caps[0].get(f) is not True for f in ('cdrom_hardware', 'cdrom_media', 'disc_given')):
        return 'BLOCKED'                                             # 7
    runs = [e for e in events if e['tag'] == 'RUN']
    if any(r.get('reason') == 'budget' for r in runs):
        return 'FAIL'                                                # 3, 5
    if any(r.get('reason') != expect for r in runs):
        return 'FAIL'                                                # 3
    for label in s['forbid']:                                        # 8
        if any(label in (e.get('name'), e.get('phase'), e.get('reason')) for e in events):
            return 'FAIL'
    if [r.get('cycle') for r in runs] != ['cold', 'warm']:           # 2
        return 'FAIL'
    for r in runs:
        step = r.get('step')
        if type(step) is not int or step < 0 or step > s['budget']:  # 4, 5
            return 'FAIL'
        arrival = r.get('halted_after_step')
        if r.get('halt_confirmed') is not True or r.get('halt_confirmation_steps') != 64 \
                or r.get('halt_confirmation_required') != 64:        # 4
            return 'FAIL'
        if type(arrival) is not int or step != arrival + 64:         # 4
            return 'FAIL'
    for cycle in ('cold', 'warm'):                                   # 6
        run_at = next(i for i, e in enumerate(events) if e['tag'] == 'RUN' and e.get('cycle') == cycle)
        hits = [i for i, e in enumerate(events)
                if e['tag'] == 'HIT' and e.get('name') == expect and e.get('cycle') == cycle]
        if len(hits) != 1 or hits[0] > run_at:
            return 'FAIL'
    return 'PASS'


def summarize(s, stage_label, stage='playable'):
    """The runner side: stage_evidence (#116 schema) computed from the same probe log."""
    events = s['events']
    caps = [e for e in events if e['tag'] == 'CAP']
    flags = caps[0] if len(caps) == 1 else {}
    runs = []
    for index, e in enumerate(events):
        if e['tag'] != 'RUN':
            continue
        same = [i for i, h in enumerate(events)
                if h['tag'] == 'HIT' and h.get('name') == s['expect'] and h.get('cycle') == e.get('cycle')]
        runs.append({'cycle': e.get('cycle'), 'reason': e.get('reason'), 'step': e.get('step'),
                     'halt_confirmed': e.get('halt_confirmed'),
                     'halt_confirmation_steps': e.get('halt_confirmation_steps'),
                     'halt_confirmation_required': e.get('halt_confirmation_required'),
                     'halted_after_step': e.get('halted_after_step'),
                     'hits_before_run': sum(1 for i in same if i < index),
                     'hits': len(same)})
    forbid_hits = sorted({label for label in s['forbid'] for e in events
                          if label in (e.get('name'), e.get('phase'), e.get('reason'))})
    return {'stage': stage, 'expect': stage_label, 'probe_kind': s['kind'],
            'exit_code': s['exit_code'], 'timed_out': s['timed_out'], 'output_capped': s['output_capped'],
            'budget': s['budget'], 'cap': {f: flags.get(f) for f in CAP_OK}, 'forbid_hits': forbid_hits,
            'runs': runs}


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
                self.assertEqual(judge_119(s), expected)
                record = claim(compat_record(), expected)
                runner = {'verified': True, 'rom_sha256': 'a' * 64, 'fixture_sha256': 'd' * 64,
                          'emulator_commit': 'b' * 40,
                          'checkpoints': [{'name': LABEL, 'observed': LABEL}],
                          'stage_evidence': summarize(s, LABEL)}
                self.assertEqual(verdict(record, runner)['result'], expected)

    def test_attestation_is_verified_only_for_pass(self):
        for name, (s, expected) in SCENARIOS.items():
            with self.subTest(scenario=name):
                status = judge_119(s)
                record = claim(compat_record(), expected)
                runner = dict(attestation_119(status, record), stage_evidence=summarize(s, LABEL))
                result = verdict(record, runner)['result']
                self.assertEqual(result == 'PASS', expected == 'PASS')

    def test_good_log_passes_at_every_upper_stage_on_both_sides(self):
        for stage, label in STAGE_LABEL.items():
            with self.subTest(stage=stage):
                s = dict(SCENARIOS['good'][0], expect=label, events=good(label))
                self.assertEqual(judge_119(s), 'PASS')
                record = compat_record(stage)
                runner = {'verified': True, 'rom_sha256': 'a' * 64, 'fixture_sha256': 'd' * 64,
                          'emulator_commit': 'b' * 40, 'checkpoints': [{'name': label, 'observed': label}],
                          'stage_evidence': summarize(s, label, stage)}
                self.assertEqual(verdict(record, runner)['result'], 'PASS')


if __name__ == '__main__':
    unittest.main()
