"""Issue #721: TransferEnd state-reset order + no-resume contract (host pinning).

Synthetic only: no emulator, BIOS, ROM or Geargrafx core is used. The
pinned-source order fact (TransferEnd sets ``m_transfer_state = 0`` at
line 687 BEFORE the pops at lines 688-690; TransferStart sets state 2
at line 670 BEFORE the pushes at 672-674) is pinned here through the
#499 driver's expectation table, which the real-core driver checks on
the pinned core (``make stack-access-guard``). Real-core evidence
comes only from that driver; this file adds no real-core run.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_access_guard as G  # noqa: E402

DOC = ROOT / "docs" / "721-transferend-noresume.md"
DESIGN_DOC = ROOT / "docs" / "473-production-safety-mechanism-design.md"


def _snap(**fields):
    snap = dict(pc=G.GUEST, a=0x5A, x=0x34, y=0x07, p=0x04, s=0x04, transfer_state=0, cycles=0, irq_pending=0,
                mpr=[0xF8, 0xF8, 0, 0, 0, 0, 0, 0])
    snap.update(fields)
    return snap


def _vdc():
    return {name: [5, 5] for name in ("ar", "r0", "r1", "r7", "sr", "read_buffer")}


def _row(case, **fields):
    row = dict(mode=case, exit=0, guarded=True, mutation=0, halted=False, latched=False, refused=False,
               frames_run=2, frame_budget=2, guard_calls=0, guard_refusals=0, guard_latched_calls=0,
               instructions=10, instructions_after_refusal=0, io_reads=0, io_writes=0, io_before_refusal=0,
               io_after_refusal=0, refusal=dict(op=0, s=0, transfer_state=0, mpr1=0, cycles=0, vdc_ar=0),
               harness_mpr1_flip=False, flip_instruction=0, start=_snap(), refusal_instruction_start=_snap(),
               at_refusal=_snap(), stop=_snap(), vdc=_vdc(), irr_before=0, irr_after=0,
               stack_window_before=[0, 0, 0, 0, 0],
               stack_window_after=[0, 0, 0, 0, 0], stack_bytes_changed=0, dst=[0, 0], events_truncated=False,
               events=[])
    row.update(fields)
    return row


def _armed_row(route):
    """A synthetic armed row matching the driver's expectation table."""
    want = G.ROUTES[route]["armed"]
    s = 0x01 if want["op"] in (G.POP8, G.POP16) else 0x04
    first = _snap(pc=want.get("instruction_pc", G.GUEST), s=s,
                  transfer_state=want.get("instruction_transfer_state", 0),
                  mpr=[0xF8, 0xFF, 0, 0, 0, 0, 0, 0])
    stop = copy.deepcopy(first)
    for key in want["changed"]:
        if key == "pc":
            stop["pc"] = want["pc"]
        elif key == "transfer_state":
            stop["transfer_state"] = want["stop_transfer_state"]
        elif key == "mpr":
            first["mpr"][1] = 0xF8
            stop["mpr"][1] = want["stop_mpr1"]
        else:
            stop[key] = first[key] ^ 0xFF
    if "pc" not in want["changed"]:
        stop["pc"] = first["pc"]
    window = want.get("window", [0, 0, 0, 0, 0])
    return _row(f"{route}-armed", halted=True, latched=True, refused=True, guard_calls=want["calls"],
                guard_refusals=want["latched"] + 1, guard_latched_calls=want["latched"],
                refusal=dict(op=want["op"], s=s, transfer_state=want.get("refusal_transfer_state", 0), mpr1=0xFF,
                             cycles=0, vdc_ar=5),
                refusal_instruction_start=first, at_refusal=copy.deepcopy(first), stop=stop,
                stack_window_before=list(window), stack_window_after=list(window),
                stack_bytes_changed=want.get("stack_bytes_changed", 0), dst=[want.get("dst", 0)] * 2,
                events=[dict(seq=0, kind="stack", op=want["op"], address=4, value=0, decision=0, latched=False,
                             mpr1=0xF8, instruction=1)])


class TransferEndOrderTests(unittest.TestCase):
    """The reset-then-access order, pinned through the driver table (#721 §1-§2)."""

    def test_transferend_clears_state_before_the_first_pop(self):
        tend = G.ROUTES["tend"]["armed"]
        self.assertEqual(tend["refusal_transfer_state"], 0)      # guard sees state 0 (line 687 ran)
        self.assertEqual(tend["instruction_transfer_state"], 1)  # instruction entered with state 1
        self.assertEqual(tend["stop_transfer_state"], 0)
        self.assertEqual(tend["op"], G.POP8)

    def test_transferstart_sets_state_before_the_first_push(self):
        tstart = G.ROUTES["tstart"]["armed"]
        self.assertEqual(tstart["refusal_transfer_state"], 2)    # guard sees state 2 (line 670 ran)
        self.assertEqual(tstart["stop_transfer_state"], 2)

    def test_ledger_records_the_order_and_the_701_correction(self):
        text = DOC.read_text(encoding="utf-8")
        for s in ("668-683", "685-693", "line 687", "lines 688-690", "line 670", "672-674",
                  "reset-then-access", "INACCURATE", "MATCHES"):
            self.assertIn(s, text)

    def test_701_doc_fix_is_tracked_as_separate_dependency(self):
        """Regression: stays GREEN whether or not the #701 doc misprint is fixed.

        The #701 section-2 sentence (pop-then-reset) is INACCURATE against the
        pinned source (reset at line 687 BEFORE the pops at lines 688-690);
        correcting that doc sentence is a separate #701 doc-fix dependency, not
        a condition for this contract. This test therefore pins only the
        Geargrafx-confirmed order (via the driver table, see the two tests
        above) and the ledger's out-of-scope note, without pinning the old
        doc wording, so it passes both before and after that fix.
        """
        ledger = DOC.read_text(encoding="utf-8")
        self.assertIn("#701", ledger)
        self.assertIn("edited here (out of scope)", ledger)
        # The design doc still discusses TransferEnd either way; require only
        # the symbol, never the old (inaccurate) sentence order wording.
        design = DESIGN_DOC.read_text(encoding="utf-8")
        self.assertIn("OPCodes_TransferEnd", design)
        # Never promote unmeasured items to PASS via this doc reference.
        self.assertNotIn("release_eligible=true", design + ledger)

    def test_pop_then_reset_prediction_is_rejected(self):
        """Negative: a pop-then-reset core would present refusal state 1; rejected."""
        row = _armed_row("tend")
        row["refusal"] = dict(row["refusal"], transfer_state=1)
        with self.assertRaisesRegex(ValueError, "transfer state"):
            G.check_armed(row, "tend-armed")


class RetentionAxisTests(unittest.TestCase):
    """Stop-state dirt is pinned: tstart loses only state, tend also loses A/X/Y."""

    def test_tstart_stop_is_blocked_on_transfer_state_only(self):
        verdict, changed = G.check_armed(_armed_row("tstart"), "tstart-armed")
        self.assertEqual((verdict, changed), (G.BLOCKED, ["transfer_state"]))

    def test_tend_stop_is_blocked_on_pc_state_and_axy(self):
        verdict, changed = G.check_armed(_armed_row("tend"), "tend-armed")
        self.assertEqual(verdict, G.BLOCKED)
        self.assertEqual(set(changed), {"pc", "transfer_state", "a", "x", "y"})

    def test_ledger_marks_counters_buffer_and_full_wram_blocked(self):
        text = DOC.read_text(encoding="utf-8")
        for s in ("BLOCKED", "m_mpr_buffer", "Transfer counters", "full WRAM", "5-byte window",
                  "Unobserved is BLOCKED"):
            self.assertIn(s, text)


class NoResumeContractTests(unittest.TestCase):
    """Any resumed / unhalted / unlatched / post-refusal-I/O row is rejected."""

    def test_resumed_guest_is_rejected_on_both_transfer_routes(self):
        for route in ("tstart", "tend"):
            row = _armed_row(route)
            row["instructions_after_refusal"] = 1
            with self.assertRaisesRegex(ValueError, "resumed", msg=route):
                G.check_armed(row, f"{route}-armed")

    def test_missing_halt_or_latch_is_rejected(self):
        for field, value in (("halted", False), ("latched", False)):
            row = _armed_row("tend")
            row[field] = value
            with self.assertRaises(ValueError, msg=field):
                G.check_armed(row, "tend-armed")

    def test_post_refusal_io_is_rejected(self):
        row = _armed_row("tend")
        row["events"].append(dict(seq=9, kind="io", op=G.READ, address=0x2102, value=0, decision=-1,
                                  latched=False, mpr1=0xFF, instruction=1))
        row["io_reads"] = 1
        with self.assertRaisesRegex(ValueError, "I/O page accessed"):
            G.check_armed(row, "tend-armed")

    def test_resume_mutants_stay_listed(self):
        for mutant in ("mut-nolatch", "mut-ignore", "mut-nostop"):
            self.assertIn(mutant, G.MUTANTS)
            self.assertIn("tstart", G.MUTANTS[mutant])
        # tend resumes are covered by the pop-order mutant and the falsified
        # resumed/not_halted/not_latched rows (see FalsifiedRowTests).
        self.assertIn("tend", G.MUTANTS["mut-popinc"])


class FalsifiedRowTests(unittest.TestCase):
    """The classifier must fail on falsified transfer rows (RED proofs)."""

    def test_falsified_transfer_rows_are_rejected(self):
        for route in ("tstart", "tend"):
            row = _armed_row(route)
            for name in ("resumed", "not_halted", "not_latched", "s_moved",
                         "transfer_state_changed", "stack_wram_written"):
                broken = dict(row)
                for name2, bad in G.falsified_rows(row, f"{route}-armed"):
                    if name2 == name:
                        with self.subTest(route=route, falsification=name):
                            with self.assertRaises(ValueError):
                                G.check_armed(bad, f"{route}-armed")
                        break
                else:
                    self.fail(f"missing falsification {name} for {route}")

    def test_byte_silence_is_not_state_proof(self):
        """tend/tstart armed rows prevent the side effect yet stay BLOCKED."""
        for route in ("tstart", "tend"):
            verdict, _ = G.check_armed(_armed_row(route), f"{route}-armed")
            self.assertEqual(verdict, G.BLOCKED)


class LedgerGateTests(unittest.TestCase):
    def test_gates_and_provenance_are_stated(self):
        text = DOC.read_text(encoding="utf-8")
        for s in ("release_eligible=false", "ABI VERIFIED 0/81", "DEV_CALLABLE 0",
                  "BLOCKED", "synthetic", "No `./rom/`", "NOT_RUN"):
            self.assertIn(s, text)
        self.assertNotIn("release_eligible=true", text)


if __name__ == "__main__":
    unittest.main()
