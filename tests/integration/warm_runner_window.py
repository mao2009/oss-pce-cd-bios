#!/usr/bin/env python3
"""Parent-side window management and fail-closed checks for the warm probe (Phase 2, design v4).

Two layers:
  WarmMachine                 pure logic. Receives lines with receive times (seconds, monotonic) and decides the
                              windows and classifications. Tested with a fake clock (no processes, no threads).
  run_series_with_windows()   real-time driver: Popen, two reader threads (stdout, stderr), a queue ordered by
                              receive time, a bounded wait for the exit, kill, wait, and a join with timeout.

Windows:
  process window  Popen .. limits.process_window          (load and everything before START 1 included)
  trial 1 window  START 1 receive .. limits.trial_window  (load excluded)
  trial 2 window  START 2 receive .. limits.trial_window  (judged independently of the process window)
Ties: when several deadlines have passed, the earliest deadline decides; on equal deadlines the process
window decides first.

Race rule (design section 4): lines are processed in receive-time order, and a decision is taken only after the
queue has been drained. A line whose receive time is at or before the decision time (t_kill) but that reaches
the machine only after the decision is counted as late_race and discarded; it never causes ABORTED.
"""
import heapq
import itertools
import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field

PREFIX = "@@WARM "
END_STATES = ("COMPLETED", "BUDGET_EXCEEDED", "TIMEOUT", "ABORTED", "BLOCKED")   # the runner's trial states
# States the probe may write on END and FINAL lines. TIMEOUT is decided by the windows here, never by the probe.
CHILD_END_STATES = ("COMPLETED", "BUDGET_EXCEEDED", "ABORTED", "BLOCKED")
REASON_B = "dynamic_mpr_detection_not_implemented"
JOIN_TIMEOUT_DEFAULT = 5.0


@dataclass(frozen=True)
class Limits:
    trial_window: float
    process_window: float
    join_timeout: float = JOIN_TIMEOUT_DEFAULT


CONTRACT_LIMITS = Limits(trial_window=120.0, process_window=600.0)   # contract 6.1
FAKE_LIMITS = Limits(trial_window=1.0, process_window=3.0)           # fake-clock tests (units)
REAL_LIMITS = Limits(trial_window=0.5, process_window=1.5)           # real-time tests (scale 0.5 s/unit)


@dataclass
class SeriesResult:
    series_state: str
    series_reason: str
    trials: dict            # {1: {"state", "reason"}, 2: {...}}
    exit_code: int
    errors: list = field(default_factory=list)
    noise_stdout: int = 0
    noise_stderr: int = 0
    discarded: int = 0
    late_race: int = 0
    json_text: object = None     # the last stderr JSON line, kept only when no window decided (None otherwise)
    final: object = None         # the (a) FINAL state, or None
    gate: bool = False           # True when the (b) gate line was accepted

    def to_dict(self):
        return {"series_state": self.series_state, "series_reason": self.series_reason,
                "trials": {str(k): v for k, v in self.trials.items()}, "exit_code": self.exit_code,
                "errors": list(self.errors), "noise_stdout": self.noise_stdout,
                "noise_stderr": self.noise_stderr, "discarded": self.discarded, "late_race": self.late_race}


class WarmMachine:
    """Classifies one series. All times are seconds on one clock; t0 is the Popen time."""

    def __init__(self, nonce, limits, t0):
        self.nonce = nonce
        self.limits = limits
        self.proc_deadline = t0 + limits.process_window
        self.expected_seq = 1
        self.start_seen = []           # trial numbers in START order
        self.open = None               # trial number whose window is open
        self.open_deadline = None
        self.trials = {1: None, 2: None}
        self.end_states = {}           # trial -> state from its END line
        self.final = None              # state from the (a) FINAL line
        self.closed = False            # after FINAL or the (b) gate line
        self.b_final = False
        self.json = None
        self.exited = False
        self.rc = None
        self.decision = None           # ("trial", n) or ("process",) once a window expired
        self.t_kill = None
        self.errors = []
        self.series = None             # (state, reason) once final
        self.noise_stdout = 0
        self.noise_stderr = 0
        self.discarded = 0
        self.late_race = 0

    # ---- helpers ----
    def stopped(self):
        return self.decision is not None or bool(self.errors) or self.series is not None

    def next_deadline(self):
        if self.decision is not None or self.exited:
            return float("inf")
        if self.open is not None:
            return min(self.open_deadline, self.proc_deadline)
        return self.proc_deadline

    def _abort(self, reason):
        if not self.errors:
            self.errors.append(reason)

    def _decide_trial(self, n, t):
        self.decision = ("trial", n)
        self.t_kill = t
        self.trials[n] = {"state": "TIMEOUT", "reason": f"trial_wall_clock_during_trial_{n}"}
        self.open = None
        self.series = ("TIMEOUT", f"trial_wall_clock_during_trial_{n}")

    def _decide_process(self, t):
        self.decision = ("process",)
        self.t_kill = t
        if self.open is not None:
            n = self.open
            self.trials[n] = {"state": "TIMEOUT", "reason": f"process_wall_clock_during_trial_{n}"}
            self.open = None
        self.series = ("TIMEOUT", "process_wall_clock")

    def _check_deadlines(self, t):
        """Decide if an expired deadline is at or before t. Earliest deadline first; process first on ties."""
        cands = []
        if not self.exited:
            cands.append((self.proc_deadline, 0, "process", None))
        if self.open is not None:
            cands.append((self.open_deadline, 1, "trial", self.open))
        for deadline, _rank, kind, n in sorted(cands):
            if t > deadline:
                if kind == "process":
                    self._decide_process(deadline)
                else:
                    self._decide_trial(n, deadline)
                return True
        return False

    # ---- inputs ----
    def feed(self, stream, text, recv):
        if self.decision is not None:
            self.discarded += 1
            if recv <= self.t_kill:
                self.late_race += 1
            return
        if self.errors or self.series is not None:
            self.discarded += 1
            return
        if self._check_deadlines(recv):
            self.discarded += 1
            return
        if stream == "err":
            if text.startswith(PREFIX):
                self._abort("control_on_stderr")
            elif text.startswith("{"):
                if self.json is not None:
                    self._abort("json_duplicate")
                else:
                    self.json = text
            else:
                self.noise_stderr += 1
            return
        if not text.startswith(PREFIX):
            self.noise_stdout += 1
            return
        self._control(text, recv)

    def on_clock(self, now):
        if self.stopped() or self.exited:
            return
        self._check_deadlines(now)            # strict: a line exactly at the deadline is still inside

    def on_exit(self, rc, t):
        if self.exited:
            return
        self.exited = True
        self.rc = rc
        if self.decision is not None or self.errors or self.series is not None:
            return
        if self.b_final:
            if rc != 1:
                return self._abort("b_rc_not_1")
            self.series = ("BLOCKED", REASON_B)
            for n in (1, 2):
                self.trials[n] = {"state": "BLOCKED", "reason": REASON_B}
            return
        if not self.start_seen:
            return self._abort("no_start_marker")
        if self.final is None:
            return self._abort("rc0_no_final" if rc == 0 else f"no_final_rc{rc}")
        if rc != (0 if self.final == "COMPLETED" else 1):
            return self._abort("rc_mismatch")
        if self.json is None:
            return self._abort("final_no_json")
        try:
            rec = json.loads(self.json)
        except ValueError:
            return self._abort("json_malformed")
        if not isinstance(rec, dict) or rec.get("end_state") != self.final:
            return self._abort("json_mismatch")
        listed = rec.get("trials")
        if not isinstance(listed, list) or len(listed) != 2:
            return self._abort("json_mismatch")
        for entry in listed:
            n = entry.get("index") if isinstance(entry, dict) else None
            if n not in (1, 2):
                return self._abort("json_mismatch")
            # a trial that never ran is listed as BLOCKED; a trial that ended must match its END line
            expected_state = self.end_states[n] if n in self.end_states else "BLOCKED"
            if entry.get("end_state") != expected_state:
                return self._abort("json_mismatch")
        self.series = (self.final, None)

    # ---- control lines ----
    def _control(self, text, recv):
        toks = text.split(" ")
        if len(toks) < 2 or toks[1] != self.nonce:
            return self._abort("nonce_mismatch")
        if self.closed:
            return self._abort("control_after_final")
        if len(toks) >= 4 and toks[2] == "FINAL" and toks[3] == "BLOCKED":
            # the (b) gate line carries no seq (design section 6, MINOR 1)
            if self.start_seen or len(toks) != 5 or toks[4] != REASON_B:
                return self._abort("b_form")
            self.closed = True
            self.b_final = True
            return
        if len(toks) < 4:
            return self._abort("malformed")
        try:
            seq = int(toks[2])
        except ValueError:
            return self._abort("malformed")
        if seq != self.expected_seq:
            return self._abort("seq")
        self.expected_seq += 1
        kind = toks[3]
        if kind == "START":
            if len(toks) != 5 or toks[4] not in ("1", "2"):
                return self._abort("malformed")
            n = int(toks[4])
            if self.open is not None:
                return self._abort("start_while_open")
            if n == 1:
                if self.start_seen:
                    return self._abort("order")
            else:
                if self.start_seen != [1] or self.end_states.get(1) is None:
                    return self._abort("order")
                if self.end_states[1] != "COMPLETED":
                    return self._abort("start2_after_failed_trial1")
            self.start_seen.append(n)
            self.open = n
            self.open_deadline = recv + self.limits.trial_window
            return
        if kind == "END":
            if len(toks) != 6 or toks[4] not in ("1", "2"):
                return self._abort("malformed")
            if toks[5] not in CHILD_END_STATES:
                return self._abort("unknown_end_state")
            n = int(toks[4])
            if self.open is None:
                return self._abort("end_without_open_trial")
            if n != self.open:
                return self._abort("trial_number_mismatch")
            self.trials[n] = {"state": toks[5], "reason": None}
            self.end_states[n] = toks[5]
            self.open = None
            return
        if kind == "FINAL":
            if len(toks) != 5 or toks[4] not in CHILD_END_STATES:
                return self._abort("malformed")
            if self.open is not None or 1 not in self.end_states:
                return self._abort("final_before_end")
            expected = self.end_states[2] if 2 in self.start_seen else self.end_states[1]
            if toks[4] != expected:
                return self._abort("final_state_mismatch")
            self.final = toks[4]
            self.closed = True
            return
        return self._abort("malformed")

    # ---- result ----
    def result(self):
        if self.errors:
            # any protocol error is fail-closed: ABORTED, even after a window decision
            self.series = ("ABORTED", self.errors[0])
        elif self.series is None:
            self.series = ("ABORTED", "unfinished")
        state, reason = self.series
        if state == "ABORTED" and self.open is not None:
            self.trials[self.open] = {"state": "ABORTED", "reason": reason}
        for n in (1, 2):
            if self.trials[n] is None:
                self.trials[n] = {"state": "BLOCKED", "reason": "not_run"}
        code = 0 if state == "COMPLETED" else 1
        return SeriesResult(series_state=state, series_reason=reason or "", trials=self.trials, exit_code=code,
                            errors=list(self.errors), noise_stdout=self.noise_stdout,
                            noise_stderr=self.noise_stderr, discarded=self.discarded, late_race=self.late_race,
                            json_text=self.json if self.decision is None else None,
                            final=self.final, gate=self.b_final)


def _reader(stream, name, q, clock):
    try:
        for raw in iter(stream.readline, b""):
            q.put((name, raw.decode("utf-8", "replace").rstrip("\r\n"), clock()))
    finally:
        q.put((name, None, clock()))


def _close_pipes(proc):
    for pipe in (proc.stdout, proc.stderr):
        try:
            pipe.close()
        except OSError:
            pass


def run_series_with_windows(command, nonce, *, clock=time.monotonic, limits=CONTRACT_LIMITS,
                            popen=subprocess.Popen):
    """Real-time driver. Windows decide; the child is killed on a decision; every wait is bounded."""
    proc = popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    t0 = clock()
    q = queue.Queue()
    readers = [threading.Thread(target=_reader, args=(proc.stdout, "out", q, clock), daemon=True),
               threading.Thread(target=_reader, args=(proc.stderr, "err", q, clock), daemon=True)]
    for th in readers:
        th.start()
    machine = WarmMachine(nonce, limits, t0)
    eof = set()
    pending = []                       # heap ordered by receive time
    counter = itertools.count()

    def drain(block_timeout=0.0):
        """Move queued items into pending. Blocks at most block_timeout when the queue is empty."""
        try:
            item = q.get(timeout=block_timeout) if block_timeout > 0 else q.get_nowait()
        except queue.Empty:
            return
        while True:
            name, text, recv = item
            if text is None:
                eof.add(name)
            else:
                heapq.heappush(pending, (recv, next(counter), name, text))
            try:
                item = q.get_nowait()
            except queue.Empty:
                return

    def feed_due(limit):
        while pending and pending[0][0] <= limit:
            recv, _n, name, text = heapq.heappop(pending)
            machine.feed(name, text, recv)

    def wait_exit():
        """Wait for the child, bounded by the process window. Returns the return code, or None on a decision."""
        while True:
            remaining = max(0.0, machine.proc_deadline - clock())
            try:
                rc = proc.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                machine.on_clock(clock())
                if machine.stopped():
                    return None
                continue
            # MINOR 8: the exit is accepted only if it was observed inside the process window
            machine.on_clock(clock())
            return None if machine.stopped() else rc

    # 1. stream phase: both streams end, or a window decides, or an error is found
    while not machine.stopped() and len(eof) < 2:
        dl = machine.next_deadline()
        drain(block_timeout=min(0.05, max(0.0, dl - clock())) if dl != float("inf") else 0.05)
        feed_due(dl)
        if machine.stopped():
            break
        if clock() > dl:
            drain()                    # pick up lines stamped before the deadline before deciding
            if pending and pending[0][0] <= dl:
                continue
            machine.on_clock(clock())
    # 2. exit phase: child exited normally (both streams ended, no decision), otherwise kill
    rc = None
    if not machine.stopped():
        rc = wait_exit()
    if rc is not None and not machine.stopped():
        drain()
        feed_due(float("inf"))
        machine.on_exit(rc, clock())
    else:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=limits.join_timeout)
        except subprocess.TimeoutExpired:
            machine.errors.append("kill_wait_timeout")
    # 3. join the readers; close the pipes only when every reader has finished
    stuck = False
    for th in readers:
        try:
            th.join(limits.join_timeout)
        except RuntimeError:
            stuck = True
        if th.is_alive():
            stuck = True
    if stuck:
        machine.errors.append("reader_stuck")
    else:
        _close_pipes(proc)
    drain()
    feed_due(float("inf"))
    return machine.result()
