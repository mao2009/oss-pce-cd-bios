#!/usr/bin/env python3
"""Phase 2 window tests (standard library only; no Geargrafx, no ROM, no WSL).

Layers:
  1. fake clock: WarmMachine is driven with the schedules of fake_warm_child (units = seconds, FAKE_LIMITS =
     trial 1.0 unit, process 3.0 units). Every mode of the design table is checked exactly.
  2. real time: run_series_with_windows() runs fake_warm_child.py as a real child at scale 0.5 s per unit
     (REAL_LIMITS: trial 0.5 s, process 1.5 s). Timeout children sleep 10 x their window, so a kill is the only
     way the run can end before the child would exit on its own.
  3. fake process objects (no child at all) for the driver paths that need a hung pipe or a hung exit:
     the process window must bound the wait after both streams have ended, and a reader that never returns
     must be reported as reader_stuck without closing the pipes.
Run: python -m unittest -v test_warm_window.py   (from this directory)
"""
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_warm_child as child  # noqa: E402
import warm_runner_window as w  # noqa: E402

NONCE = "n0nce-test-7f3a"
CHILD = str(HERE / "fake_warm_child.py")
B = child.REASON_B


def replay_events(events, hang, rc, *, nonce=NONCE, limits=w.FAKE_LIMITS, t0=0.0):
    """Feed timed lines to WarmMachine in time order with a fake clock. Multi-line texts are split.
    Stream 'close' is a pipe event for the real child only; the machine never sees it."""
    m = w.WarmMachine(nonce, limits, t0)
    for t, stream, text in sorted(events, key=lambda e: e[0]):
        m.on_clock(t0 + t)
        if stream not in ("out", "err"):
            continue
        for line in text.split("\n"):
            m.feed(stream, line, t0 + t)
    m.on_clock(t0 + hang)
    if m.decision is None:
        m.on_exit(rc, t0 + hang)
    return m.result(), m


def replay_mode(mode, **kw):
    events, rc, hang = child.schedule(mode, NONCE)
    return replay_events(events, hang, rc, **kw)


# (series_state, series_reason, {trial: (state, reason)}, exit_code). None in a reason = not checked.
EXPECT = {
    "normal": ("COMPLETED", "", {1: ("COMPLETED", None), 2: ("COMPLETED", None)}, 0),
    "trial1_timeout": ("TIMEOUT", "trial_wall_clock_during_trial_1",
                       {1: ("TIMEOUT", "trial_wall_clock_during_trial_1"), 2: ("BLOCKED", "not_run")}, 1),
    "trial2_timeout": ("TIMEOUT", "trial_wall_clock_during_trial_2",
                       {1: ("COMPLETED", None), 2: ("TIMEOUT", "trial_wall_clock_during_trial_2")}, 1),
    "trial2_timeout_after_trial1_completed": ("TIMEOUT", "trial_wall_clock_during_trial_2",
                                              {1: ("COMPLETED", None),
                                               2: ("TIMEOUT", "trial_wall_clock_during_trial_2")}, 1),
    "process_timeout": ("TIMEOUT", "process_wall_clock",
                        {1: ("BLOCKED", "not_run"), 2: ("BLOCKED", "not_run")}, 1),
    "process_timeout_after_end2": ("TIMEOUT", "process_wall_clock",
                                   {1: ("COMPLETED", None), 2: ("COMPLETED", None)}, 1),
    "close_fds_hang": ("TIMEOUT", "process_wall_clock",
                       {1: ("COMPLETED", None), 2: ("BLOCKED", "not_run")}, 1),
    "exit3": ("ABORTED", "no_start_marker", {1: ("BLOCKED", "not_run"), 2: ("BLOCKED", "not_run")}, 1),
    "midway": ("ABORTED", "rc0_no_final", {1: ("ABORTED", "rc0_no_final"), 2: ("BLOCKED", "not_run")}, 1),
    "seq_missing": ("ABORTED", "seq", {1: ("ABORTED", "seq"), 2: ("BLOCKED", "not_run")}, 1),
    "seq_dup": ("ABORTED", "seq", {1: ("ABORTED", "seq"), 2: ("BLOCKED", "not_run")}, 1),
    "malformed": ("ABORTED", "malformed", {1: ("ABORTED", "malformed"), 2: ("BLOCKED", "not_run")}, 1),
    "order_swap": ("ABORTED", "end_without_open_trial",
                   {1: ("COMPLETED", None), 2: ("BLOCKED", "not_run")}, 1),
    "no_start_marker": ("ABORTED", "no_start_marker", {1: ("BLOCKED", "not_run"), 2: ("BLOCKED", "not_run")}, 1),
    "state_mismatch": ("ABORTED", "json_mismatch", {1: ("COMPLETED", None), 2: ("COMPLETED", None)}, 1),
    "json_duplicate": ("ABORTED", "json_duplicate", {1: ("COMPLETED", None), 2: ("COMPLETED", None)}, 1),
    "late_end_after_kill": ("TIMEOUT", "trial_wall_clock_during_trial_1",
                            {1: ("TIMEOUT", "trial_wall_clock_during_trial_1"), 2: ("BLOCKED", "not_run")}, 1),
    "noise_flood": ("COMPLETED", "", {1: ("COMPLETED", None), 2: ("COMPLETED", None)}, 0),
    "nonce_wrong": ("ABORTED", "nonce_mismatch", {1: ("BLOCKED", "not_run"), 2: ("BLOCKED", "not_run")}, 1),
    "final_after_extra": ("ABORTED", "control_after_final",
                          {1: ("COMPLETED", None), 2: ("COMPLETED", None)}, 1),
    "final_no_json": ("ABORTED", "final_no_json", {1: ("COMPLETED", None), 2: ("COMPLETED", None)}, 1),
    "rc0_no_final": ("ABORTED", "rc0_no_final", {1: ("COMPLETED", None), 2: ("COMPLETED", None)}, 1),
    "unknown_end_state": ("ABORTED", "unknown_end_state", {1: ("ABORTED", "unknown_end_state"),
                                                           2: ("BLOCKED", "not_run")}, 1),
    "trial_number_mismatch": ("ABORTED", "trial_number_mismatch",
                              {1: ("ABORTED", "trial_number_mismatch"), 2: ("BLOCKED", "not_run")}, 1),
    "b_final_only": ("BLOCKED", B, {1: ("BLOCKED", B), 2: ("BLOCKED", B)}, 1),
    "b_with_start": ("ABORTED", "b_form", {1: ("ABORTED", "b_form"), 2: ("BLOCKED", "not_run")}, 1),
    "b_bad_reason": ("ABORTED", "b_form", {1: ("BLOCKED", "not_run"), 2: ("BLOCKED", "not_run")}, 1),
    "b_no_reason": ("ABORTED", "b_form", {1: ("BLOCKED", "not_run"), 2: ("BLOCKED", "not_run")}, 1),
    "b_then_end": ("ABORTED", "control_after_final", {1: ("BLOCKED", "not_run"), 2: ("BLOCKED", "not_run")}, 1),
    "trial2_window_cut_by_process": ("TIMEOUT", "process_wall_clock",
                                     {1: ("COMPLETED", None),
                                      2: ("TIMEOUT", "process_wall_clock_during_trial_2")}, 1),
}
END_STATES = {"COMPLETED", "BUDGET_EXCEEDED", "TIMEOUT", "ABORTED", "BLOCKED"}


def assert_expected(test, res, mode):
    state, reason, trials, code = EXPECT[mode]
    test.assertEqual(res.series_state, state, mode)
    if reason is not None:
        test.assertEqual(res.series_reason, reason, mode)
    for n, (tstate, treason) in trials.items():
        test.assertEqual(res.trials[n]["state"], tstate, f"{mode} trial {n}")
        if treason is not None:
            test.assertEqual(res.trials[n]["reason"], treason, f"{mode} trial {n} reason")
    test.assertEqual(res.exit_code, code, mode)
    for n in (1, 2):
        test.assertIn(res.trials[n]["state"], END_STATES, mode)
    test.assertNotIn("NOT_STARTED", repr(res.to_dict()), mode)


class FakeClockModes(unittest.TestCase):
    """Every mode of the design table, replayed with the fake clock (no processes)."""

    def test_all_modes_match_the_table(self):
        for mode in EXPECT:
            with self.subTest(mode=mode):
                res, _ = replay_mode(mode)
                assert_expected(self, res, mode)

    def test_noise_counts(self):
        res, _ = replay_mode("noise_flood")
        self.assertEqual(res.noise_stdout, 3000)
        self.assertEqual(res.noise_stderr, 3000)

    def test_b_final_only_is_blocked_with_reason(self):
        res, _ = replay_mode("b_final_only")
        self.assertEqual(res.series_state, "BLOCKED")
        self.assertEqual(res.series_reason, B)
        self.assertEqual(res.exit_code, 1)
        self.assertEqual(res.trials[1], {"state": "BLOCKED", "reason": B})
        self.assertEqual(res.trials[2], {"state": "BLOCKED", "reason": B})


class WindowArithmetic(unittest.TestCase):
    """Window boundaries, origins, ordering of deadlines, and the race rule (explicit fake times)."""

    def test_end_exactly_at_trial1_deadline_is_accepted(self):
        # START 1 at 0.7 opens a 1.0 window that closes at 1.7; END 1 received at 1.7 is inside it
        ev = [(0.7, "out", child.ctl(NONCE, 1, "START 1")),
              (1.7, "out", child.ctl(NONCE, 2, "END 1 COMPLETED")),
              (1.8, "out", child.ctl(NONCE, 3, "START 2")),
              (2.0, "out", child.ctl(NONCE, 4, "END 2 COMPLETED")),
              (2.0, "out", child.ctl(NONCE, 5, "FINAL COMPLETED")),
              (2.0, "err", child.jtext(child.NORMAL_JSON))]
        res, _ = replay_events(ev, 2.0, 0)
        self.assertEqual(res.series_state, "COMPLETED")

    def test_end_just_after_trial1_deadline_is_a_timeout(self):
        ev = [(0.7, "out", child.ctl(NONCE, 1, "START 1")),
              (1.71, "out", child.ctl(NONCE, 2, "END 1 COMPLETED"))]
        res, _ = replay_events(ev, 10.0, 0)
        self.assertEqual(res.series_state, "TIMEOUT")
        self.assertEqual(res.trials[1]["state"], "TIMEOUT")
        self.assertEqual(res.discarded, 1)

    def test_trial_window_starts_at_start_not_at_popen(self):
        # Popen at absolute -0.5; START 1 received at absolute 0.0 (schedule time 0.5 relative to Popen).
        # The trial window ends at 1.0 (START + 1.0), not at 0.5 (Popen + 1.0).
        ev = [(0.5, "out", child.ctl(NONCE, 1, "START 1"))]
        res, m = replay_events(ev, 10.0, 0, t0=-0.5)
        self.assertEqual(res.trials[1]["reason"], "trial_wall_clock_during_trial_1")
        self.assertEqual(m.t_kill, 1.0)

    def test_process_window_is_from_popen_and_cuts_trial1_when_earlier(self):
        # Popen at 0, START 1 at 2.9: trial 1 would end at 3.9, the process window ends at 3.0
        ev = [(2.9, "out", child.ctl(NONCE, 1, "START 1"))]
        res, _ = replay_events(ev, 10.0, 0)
        self.assertEqual(res.series_state, "TIMEOUT")
        self.assertEqual(res.series_reason, "process_wall_clock")
        self.assertEqual(res.trials[1]["reason"], "process_wall_clock_during_trial_1")

    def test_process_timeout_before_start1_blocks_both_trials(self):
        res, _ = replay_events([], 10.0, 0)
        self.assertEqual(res.series_reason, "process_wall_clock")
        self.assertEqual(res.trials[1], {"state": "BLOCKED", "reason": "not_run"})

    def test_trial2_window_is_independent_of_the_process_window(self):
        # START 2 at 0.5 closes at 1.5 (trial window); the process window (3.0) is not involved
        ev = [(0.0, "out", child.ctl(NONCE, 1, "START 1")),
              (0.1, "out", child.ctl(NONCE, 2, "END 1 COMPLETED")),
              (0.5, "out", child.ctl(NONCE, 3, "START 2"))]
        res, _ = replay_events(ev, 10.0, 0)
        self.assertEqual(res.trials[2]["reason"], "trial_wall_clock_during_trial_2")
        self.assertEqual(res.trials[1]["state"], "COMPLETED")

    def test_trial2_cut_by_process_at_2_5_units(self):
        # START 2 at 2.5: trial 2 window would end at 3.5, the process window ends at 3.0
        res, _ = replay_mode("trial2_window_cut_by_process")
        self.assertEqual(res.trials[2]["reason"], "process_wall_clock_during_trial_2")
        self.assertEqual(res.trials[1]["state"], "COMPLETED")

    def test_start2_at_0_5_units_times_out_trial2(self):
        ev = [(0.0, "out", child.ctl(NONCE, 1, "START 1")),
              (0.1, "out", child.ctl(NONCE, 2, "END 1 COMPLETED")),
              (0.5, "out", child.ctl(NONCE, 3, "START 2"))]
        res, _ = replay_events(ev, 10.0, 0)
        self.assertEqual(res.series_state, "TIMEOUT")
        self.assertEqual(res.trials[2]["state"], "TIMEOUT")

    def test_kill_rule_discards_lines_after_the_decision(self):
        res, m = replay_mode("late_end_after_kill")
        self.assertEqual(res.trials[1]["state"], "TIMEOUT")
        self.assertIsNone(m.end_states.get(1))
        self.assertGreaterEqual(res.discarded, 1)
        self.assertEqual(res.late_race, 0)            # received after t_kill: discarded, not a race

    def test_line_received_before_kill_but_after_decision_is_a_race_not_an_error(self):
        # design section 4: a line received at or before t_kill is processed; if it reaches the machine only
        # after the decision it is counted as late_race (never ABORTED)
        m = w.WarmMachine(NONCE, w.FAKE_LIMITS, 0.0)
        m.feed("out", child.ctl(NONCE, 1, "START 1"), 0.0)
        m.on_clock(1.1)                                    # decision: trial 1 expired at 1.0
        self.assertEqual(m.t_kill, 1.0)
        m.feed("out", child.ctl(NONCE, 2, "END 1 COMPLETED"), 0.9)
        res = m.result()
        self.assertEqual(res.series_state, "TIMEOUT")
        self.assertEqual(res.errors, [])
        self.assertEqual(res.late_race, 1)

    def test_earlier_trial_deadline_wins_over_a_later_process_deadline(self):
        # START 1 at 0.0 (trial deadline 1.0), process deadline 3.0; a line at 3.5 finds both expired
        m = w.WarmMachine(NONCE, w.FAKE_LIMITS, 0.0)
        m.feed("out", child.ctl(NONCE, 1, "START 1"), 0.0)
        m.feed("out", child.ctl(NONCE, 2, "END 1 COMPLETED"), 3.5)
        res = m.result()
        self.assertEqual(res.trials[1]["reason"], "trial_wall_clock_during_trial_1")
        self.assertEqual(m.t_kill, 1.0)

    def test_equal_deadlines_process_window_decides_first(self):
        # START 1 at 2.0 (trial deadline 3.0) equals the process deadline 3.0; a line at 3.5 finds both expired
        m = w.WarmMachine(NONCE, w.FAKE_LIMITS, 0.0)
        m.feed("out", child.ctl(NONCE, 1, "START 1"), 2.0)
        m.feed("out", child.ctl(NONCE, 2, "END 1 COMPLETED"), 3.5)
        res = m.result()
        self.assertEqual(res.series_reason, "process_wall_clock")
        self.assertEqual(res.trials[1]["reason"], "process_wall_clock_during_trial_1")


class DriverWithFakeProcesses(unittest.TestCase):
    """The driver paths that need no child: a bounded wait after both streams end, and reader_stuck."""

    class _EofStream:
        def __init__(self):
            self.closed = False

        def readline(self):
            return b""

        def close(self):
            self.closed = True

    class _BlockingStream:
        def __init__(self, release):
            self.release = release
            self.closed = False

        def readline(self):
            self.release.wait()
            return b""

        def close(self):
            self.closed = True

    class _FakeProc:
        """Never exits on its own. wait() raises TimeoutExpired until kill() has been called."""

        def __init__(self, stdout, stderr):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = None
            self.killed = False

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

        exit_after = None      # seconds after which the child "exits"; wait() then ignores its timeout
        exit_rc = 0

        def wait(self, timeout=None):
            if self.returncode is None and self.exit_after is not None:
                time.sleep(self.exit_after)
                self.returncode = self.exit_rc
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fake-warm-child", timeout)
            return self.returncode

    def test_exit_observed_after_the_process_deadline_is_a_timeout(self):
        # MINOR 8: the child exits at about 0.3 s, but the process window is 0.1 s. wait() returns the exit
        # late, so the exit is not accepted: the series is TIMEOUT (process_wall_clock), not COMPLETED.
        made = {}

        def popen(cmd, stdout, stderr):
            p = self._FakeProc(self._EofStream(), self._EofStream())
            p.exit_after = 0.3
            p.exit_rc = 0
            made["p"] = p
            return p

        limits = w.Limits(trial_window=0.1, process_window=0.1, join_timeout=1.0)
        res = w.run_series_with_windows(["unused"], NONCE, limits=limits, popen=popen)
        self.assertEqual(res.series_state, "TIMEOUT")
        self.assertEqual(res.series_reason, "process_wall_clock")
        self.assertEqual(res.exit_code, 1)

    def test_hung_exit_after_both_streams_end_is_bounded_by_the_process_window(self):
        # MAJOR 1: no pipe output, both streams at EOF, the child never exits: the process window must end it
        made = {}

        def popen(cmd, stdout, stderr):
            made["p"] = self._FakeProc(self._EofStream(), self._EofStream())
            return made["p"]

        limits = w.Limits(trial_window=0.1, process_window=0.4, join_timeout=1.0)
        started = time.monotonic()
        res = w.run_series_with_windows(["unused"], NONCE, limits=limits, popen=popen)
        elapsed = time.monotonic() - started
        self.assertEqual(res.series_state, "TIMEOUT")
        self.assertEqual(res.series_reason, "process_wall_clock")
        self.assertTrue(made["p"].killed)
        self.assertLess(elapsed, 2.0)
        self.assertEqual(res.trials[1], {"state": "BLOCKED", "reason": "not_run"})

    def test_reader_that_never_returns_is_reader_stuck_and_pipes_stay_open(self):
        # MINOR 7 / MINOR 3: join times out, so reader_stuck is recorded and the pipes are not closed
        release = threading.Event()
        made = {}
        blocking = self._BlockingStream(release)
        eof = self._EofStream()

        def popen(cmd, stdout, stderr):
            made["p"] = self._FakeProc(blocking, eof)
            return made["p"]

        limits = w.Limits(trial_window=0.1, process_window=0.4, join_timeout=0.2)
        try:
            res = w.run_series_with_windows(["unused"], NONCE, limits=limits, popen=popen)
            self.assertEqual(res.series_state, "ABORTED")
            self.assertEqual(res.series_reason, "reader_stuck")
            self.assertIn("reader_stuck", res.errors)
            self.assertFalse(blocking.closed)
            self.assertFalse(eof.closed)
        finally:
            release.set()


class RealTime(unittest.TestCase):
    """Real child processes at 0.5 s per unit. Timeout children sleep 10 x their window."""

    def run_real(self, mode, scale=0.5, noise=None):
        cmd = [sys.executable, CHILD, mode, NONCE, "--scale", str(scale)]
        if noise is not None:
            cmd += ["--noise", str(noise)]
        started = time.monotonic()
        res = w.run_series_with_windows(cmd, NONCE, limits=w.REAL_LIMITS)
        return res, time.monotonic() - started

    def test_normal_completes(self):
        res, _ = self.run_real("normal")
        assert_expected(self, res, "normal")

    def test_trial1_timeout_kills_the_child_before_it_exits(self):
        res, elapsed = self.run_real("trial1_timeout")
        assert_expected(self, res, "trial1_timeout")
        self.assertGreaterEqual(elapsed, 0.45)     # the trial window (0.5 s) was honoured
        self.assertLess(elapsed, 4.0)              # the child would have slept 5.0 s; it was killed

    def test_trial2_timeout_kills_the_child_before_it_exits(self):
        res, elapsed = self.run_real("trial2_timeout")
        assert_expected(self, res, "trial2_timeout")
        self.assertLess(elapsed, 4.0)

    def test_process_timeout_kills_the_child_before_it_exits(self):
        res, elapsed = self.run_real("process_timeout")
        assert_expected(self, res, "process_timeout")
        self.assertLess(elapsed, 6.0)              # the child would have slept 15.0 s

    def test_close_fds_hang_is_ended_by_the_process_window(self):
        # MAJOR 1 (real). Both pipes are closed by the child, which then sleeps 15 s. The runner's
        # bounded wait after EOF must end it at the process window (1.5 s). Trial 1 stays COMPLETED.
        # On Windows the child's closed pipes do not deliver EOF to the reader, so this run ends through the process
        # window (the probe_pipe_hang.py experiment). The EOF path is covered by the fake-process tests instead.
        res, elapsed = self.run_real("close_fds_hang")
        assert_expected(self, res, "close_fds_hang")
        self.assertLess(elapsed, 6.0)

    def test_noise_flood_does_not_block_the_pipes(self):
        # MINOR 6: 20 000 unprefixed lines per stream
        res, _ = self.run_real("noise_flood", noise=20000)
        assert_expected(self, res, "noise_flood")
        self.assertEqual(res.noise_stdout, 20000)
        self.assertEqual(res.noise_stderr, 20000)

    def test_exit3_is_aborted(self):
        res, _ = self.run_real("exit3")
        assert_expected(self, res, "exit3")

    def test_b_final_only_is_blocked(self):
        res, _ = self.run_real("b_final_only")
        assert_expected(self, res, "b_final_only")

    def test_child_schedule_is_printed_in_order(self):
        out = subprocess.run([sys.executable, CHILD, "normal", NONCE, "--scale", "0.01"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith(w.PREFIX)]
        self.assertEqual([ln.split(" ")[3] for ln in lines], ["START", "END", "START", "END", "FINAL"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
