"""Issue #720 (P05 of #473): D/T mutation RED gaps (host-only, synthetic rows only).

Survey (no duplication): the real-core mutants and their host expectation pins
already exist and are NOT repeated here:

- ``tests/integration/geargrafx_stack_s5.py`` MUTANTS/SWEEP_MUTANTS + ``evaluate()``
  (D-off / D-wrong-bit / E-off, wrong-bit sweep in both directions, falsified rows).
- ``tests/host/test_stack_s5.py`` expectation pins (forbidden-iff-bit, sweep coverage,
  D-keeps-MPR1 vs E-shows-TAM, mid-transfer BLOCKED, mutant UNSAFE+visible).
- ``tests/integration/geargrafx_stack_s6.py`` MUTANTS/FALSE_POSITIVE_MUTANTS + ``evaluate()``
  (mut-tresume x6 / mut-tnomid x5 / mut-tfirst / mut-tseq / mut-tnoself + 3 false positives).
- ``tests/host/test_stack_s6.py`` expectation pins (5 opcodes, mid never SAFE,
  pre-start changes nothing, mutant presence/counts).
- ``tests/integration/geargrafx_st_boundary.py`` + ``tests/host/test_365_st_boundary_record.py``
  no-entry-check RED pattern (unguarded row cannot pass as refused, named ValueError).
- ``tests/host/test_block_preblock.py`` armed/unguarded classifier + falsification RED.
- Wiring pins (``make integration`` runs stack-s5/stack-s6, patch flag) in
  ``test_stack_s5.py``/``test_stack_s6.py``.

This file covers ONLY the gaps: each required mutant must RED through the real
named judge (``check_policy_row`` / ``check_sweep`` / ``check_refused`` /
``M.verdict`` / ``M.check_armed`` / ``check_completed``), D vs E confusion must
RED in both directions, the ST no-entry-check pattern must hold for D/T
pre-start, and import-failure/ERROR/timeout rows must never count as killed.
No emulator, no ROM, no core, no reference BIOS. Synthetic rows only.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_s5 as S5  # noqa: E402
import geargrafx_stack_s6 as S6  # noqa: E402
import geargrafx_stack_methods as M  # noqa: E402

G = S5.G
PINNED = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
GUEST = 0x1E00
TAM_NEXT = 0x1E02
HANDLER = G.HANDLER


def _snap(pc=GUEST, mpr=None, a=0xFF):
    if mpr is None:
        mpr = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
    return dict(pc=pc, a=a, x=0x34, y=0x07, p=0x04, s=0x04, transfer_state=0,
                cycles=0, irq_pending=0, mpr=list(mpr))


def _s5_row(case, refused, reason, instructions, stop_pc, stop_mpr,
            decode=0, io_reads=0, io_writes=0, after=0, budget=False,
            events=None, deferrals=None, entry=0):
    pair = [0, 0]
    row = dict(mode=case, exit=0, refused=refused, refusal_reason=reason,
               instructions=instructions, budget_stop=budget,
               decode_refusals=decode, io_reads=io_reads, io_writes=io_writes,
               instructions_after_refusal=after,
               stack_bytes_changed=0, cdram_bytes_changed=0, bram_bytes_changed=0,
               irr_before=2, irr_after=2, dst=[0, 0], events=list(events or []),
               events_truncated=False,
               refusal={"kind": "instruction" if refused else "none"},
               vdc={k: list(pair) for k in ("ar", "r0", "r1", "r7", "sr", "read_buffer")},
               start=_snap(), stop=_snap(pc=stop_pc, mpr=stop_mpr),
               refusal_instruction_start=_snap(pc=stop_pc, mpr=stop_mpr))
    if deferrals is not None:
        row["irq_deferrals"] = deferrals
    row["handler_entry_instruction"] = entry
    return row


def _sweep_row(case, refused, reason, instructions, stop_pc, stop_mpr,
               budget=False, io_reads=0, io_writes=0, after=0, events=None):
    pair = [0, 0]
    return dict(mode=case, exit=0, refused=refused, refusal_reason=reason,
                instructions=instructions, budget_stop=budget,
                io_reads=io_reads, io_writes=io_writes,
                instructions_after_refusal=after,
                stack_bytes_changed=0, cdram_bytes_changed=0, bram_bytes_changed=0,
                irr_before=0, irr_after=0, dst=[0, 0], events=list(events or []),
                events_truncated=False,
                refusal={"kind": "instruction" if refused else "none"},
                vdc={k: list(pair) for k in ("ar", "r0", "r1", "r7", "sr", "read_buffer")},
                start=_snap(), stop=_snap(pc=stop_pc, mpr=stop_mpr),
                refusal_instruction_start=_snap(pc=stop_pc, mpr=stop_mpr))


def _io_event(op, address):
    return {"seq": 1, "kind": "io", "op": op, "address": address, "value": 0,
            "decision": -1, "latched": False, "mpr1": 0xFF, "instruction": 1}


def _s6_row(**extra):
    snap = dict(pc=GUEST, a=0x5A, x=0x34, y=0x07, p=0x04, s=0x01,
                transfer_state=2, cycles=0, irq_pending=0, mpr=[0xF8] * 4 + [0] * 4)
    pair = [0, 0]
    row = dict(mode="AT:xtiim-armed", exit=0, refused=True,
               refusal_reason="transfer_mpr_changed", instructions=4,
               refusal={"kind": "instruction"}, io_reads=0, io_writes=0,
               io_after_refusal=0, instructions_after_refusal=0,
               stack_bytes_changed=3, cdram_bytes_changed=0, bram_bytes_changed=0,
               irr_before=0, irr_after=0, dst=[0, 0], events=[],
               events_truncated=False, wram_bytes_changed=2, t_refusals=1,
               t_at_refusal=[0x1E82, 0x4002, 2, 0], t_stop=[0x1E82, 0x4002, 2, 0],
               vdc={k: list(pair) for k in ("ar", "r0", "r1", "r7", "sr", "read_buffer")},
               start=dict(snap, transfer_state=0, s=0x04), stop=dict(snap),
               refusal_instruction_start=dict(snap))
    row.update(extra)
    return row


def _s6_completed_row(op="tii", wram=None):
    row = _s6_row(mode=f"AT:x{op}w-armed", refused=False,
                  refusal={"kind": "none"}, refusal_reason="none",
                  instructions=7, stop=dict(pc=0x1E07, a=0x5A, x=0x34, y=0x07,
                                           p=0x04, s=0x04, transfer_state=0,
                                           cycles=0, irq_pending=0,
                                           mpr=[0xF8] * 4 + [0] * 4),
                  refusal_instruction_start=dict(pc=GUEST, a=0x5A, x=0x34, y=0x07,
                                                 p=0x04, s=0x04, transfer_state=0,
                                                 cycles=0, irq_pending=0,
                                                 mpr=[0xF8] * 4 + [0] * 4),
                  wram_bytes_changed=S6.FULL[op] if wram is None else wram,
                  t_at_refusal=[0x1E82, 0x4002, 0, 0],
                  t_stop=[0x1E82, 0x4002, 0, 0])
    return row


class DOffWrongBitEOffRedTests(unittest.TestCase):
    """#647 mutants RED through the named S5 judges (gap: old suites pin dicts only)."""

    def test_d_off_reds_through_check_policy_row(self):
        start = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
        landed_mpr = [0xF8, 0xFF, 0, 0, 0, 0, 0, 0]
        ev = [_io_event(G.WRITE, 0x2104)]
        row = _s5_row("AD:irqtam-mut-doff", False, "mpr1", 2, HANDLER,
                      landed_mpr, decode=0, io_reads=1, events=ev, entry=1)
        row["refusal_instruction_start"] = _snap(pc=HANDLER, mpr=landed_mpr)
        self.assertTrue(S5.MUTANTS["AD:irqtam-mut-doff"][1](
            dict(row, decode_refusals=0)))
        with self.assertRaisesRegex(ValueError, "verdict"):
            S5.check_policy_row(row, "AD:irqtam-mut-doff",
                                S5.irq_expect("AD", "irqtam"))
        got, _ = M.verdict(row)
        self.assertEqual(got, M.UNSAFE)

    def test_d_wrong_bit_reds_through_check_policy_row(self):
        start = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
        landed_mpr = [0xF8, 0xFF, 0, 0, 0, 0, 0, 0]
        ev = [_io_event(G.WRITE, 0x2104)]
        row = _s5_row("AD:irqtam-mut-dbit", False, "mpr1", 2, HANDLER,
                      landed_mpr, decode=0, io_reads=1, events=ev, entry=1)
        row["refusal_instruction_start"] = _snap(pc=HANDLER, mpr=landed_mpr)
        self.assertTrue(S5.MUTANTS["AD:irqtam-mut-dbit"][1](
            dict(row, decode_refusals=0)))
        with self.assertRaisesRegex(ValueError, "verdict"):
            S5.check_policy_row(row, "AD:irqtam-mut-dbit",
                                S5.irq_expect("AD", "irqtam"))

    def test_e_off_reds_through_check_policy_row(self):
        landed_mpr = [0xF8, 0xFF, 0, 0, 0, 0, 0, 0]
        ev = [_io_event(G.WRITE, 0x2104)]
        row = _s5_row("AE:irqtam-mut-eoff", False, "mpr1", 2, HANDLER,
                      landed_mpr, decode=0, io_reads=1, events=ev,
                      deferrals=0, entry=1)
        row["refusal_instruction_start"] = _snap(pc=HANDLER, mpr=landed_mpr)
        self.assertTrue(S5.MUTANTS["AE:irqtam-mut-eoff"][1](
            dict(row, irq_deferrals=0)))
        with self.assertRaisesRegex(ValueError, "verdict"):
            S5.check_policy_row(row, "AE:irqtam-mut-eoff",
                                S5.irq_expect("AE", "irqtam"))

    def test_wrong_bit_sweep_reds_in_both_directions(self):
        start = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
        applied = [0xFF if 0x02 >> i & 1 else start[i] for i in range(8)]
        # False positive: D refuses an allowed TAM (mask 0x01).
        fp = _sweep_row("AD:tam01ff-mut-dbit", True, "decode_tam", 1,
                        GUEST, start)
        (mask, a), why = S5.SWEEP_MUTANTS["AD:tam01ff-mut-dbit"]
        self.assertEqual((mask, a), (0x01, 0xFF))
        self.assertTrue(why(dict(fp, refused=True, refusal_reason="decode_tam",
                                 instructions=1)))
        with self.assertRaisesRegex(ValueError, "want|budget|allowed"):
            S5.check_sweep(fp, "AD:tam01ff-mut-dbit", "AD", 0x01, 0xFF)
        # Missed refusal: TAM runs, A refuses after it (mask 0x02).
        missed = _sweep_row("AD:tam02ff-mut-dbit", True, "mpr1", 2,
                            TAM_NEXT, applied)
        (mask, a), why = S5.SWEEP_MUTANTS["AD:tam02ff-mut-dbit"]
        self.assertEqual((mask, a), (0x02, 0xFF))
        self.assertTrue(why(dict(missed, refused=True,
                                 refusal_reason="mpr1",
                                 stop={"mpr": applied})))
        with self.assertRaisesRegex(ValueError, "want"):
            S5.check_sweep(missed, "AD:tam02ff-mut-dbit", "AD", 0x02, 0xFF)

    def test_d_and_e_expectations_must_not_be_confused(self):
        # AD armed row must RED against the AE expectation and vice versa.
        start = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
        applied = [0xF8, 0xFF, 0, 0, 0, 0, 0, 0]
        ad = _s5_row("AD:irqtam-armed", True, "decode_tam", 1, GUEST,
                     start, decode=1, entry=0)
        ae = _s5_row("AE:irqtam-armed", True, "mpr1", 2, TAM_NEXT,
                     applied, entry=0)
        S5.check_policy_row(ad, "AD:irqtam-armed", S5.irq_expect("AD", "irqtam"))
        S5.check_policy_row(ae, "AE:irqtam-armed", S5.irq_expect("AE", "irqtam"))
        with self.assertRaisesRegex(ValueError, "refusal reason|MPR1|verdict|instructions|refused instruction"):
            S5.check_policy_row(ad, "AD:irqtam-armed", S5.irq_expect("AE", "irqtam"))
        with self.assertRaisesRegex(ValueError, "refusal reason|MPR1|verdict|instructions|refused instruction"):
            S5.check_policy_row(ae, "AE:irqtam-armed", S5.irq_expect("AD", "irqtam"))
        # Sweep method confusion: the same forbidden TAM has a different named
        # refusal under A (mpr1, 2 insns) vs A+D (decode_tam, 1 insn).
        forbidden_ad = _sweep_row("AD:tam02ff-armed", True, "decode_tam", 1,
                                  GUEST, start)
        S5.check_sweep(forbidden_ad, "AD:tam02ff-armed", "AD", 0x02, 0xFF)
        with self.assertRaisesRegex(ValueError, "want"):
            S5.check_sweep(forbidden_ad, "AD:tam02ff-armed", "A", 0x02, 0xFF)


class TMutantRedTests(unittest.TestCase):
    """#648 mutants RED through the named S6 judges (gap: old suites pin counts only)."""

    def test_tresume_reds_and_never_counts_as_safe(self):
        ev = [_io_event(G.WRITE, 0x2104),
              dict(seq=2, kind="io", op=G.WRITE, address=0x2103, value=0,
                   decision=-1, latched=False, mpr1=0xFF, instruction=6)]
        row = _s6_row(mode="AT:xtiim-mut-tresume", t_resumed=True,
                      instructions_after_refusal=5, io_after_refusal=2,
                      io_writes=2, events=ev, io_reads=0)
        self.assertTrue(S6.MUTANTS["AT:xtiim-mut-tresume"](row))
        got, _ = M.verdict(row, 3)
        self.assertEqual(got, M.UNSAFE)
        with self.assertRaisesRegex(ValueError, "verdict|resume|mode"):
            S6.check_refused(row, "AT:xtiim-mut-tresume", S6.mid_expect("tii", "m"))

    def test_tnomid_reds(self):
        for op in S6.OPS:
            case = f"AT:x{op}m-mut-tnomid"
            ev = [_io_event(G.WRITE, 0x2104),
                  dict(seq=2, kind="io", op=G.WRITE, address=0x2103, value=0,
                       decision=-1, latched=False, mpr1=0xFF, instruction=6)]
            row = _s6_row(mode=case, refused=False,
                          refusal={"kind": "none"}, refusal_reason="none",
                          instructions=6, io_writes=2, events=ev, io_reads=0,
                          wram_bytes_changed=S6.FULL[op],
                          t_at_refusal=[0x1E82, 0x4002, 0, 0],
                          t_stop=[0x1E82, 0x4002, 0, 0])
            self.assertIn(case, S6.MUTANTS)
            self.assertTrue(S6.MUTANTS[case](row))
            self.assertEqual(M.verdict(row, 3)[0], M.UNSAFE)
            with self.assertRaisesRegex(ValueError, "verdict"):
                S6.check_refused(row, case, S6.mid_expect(op, "m"))

    def test_tfirst_reds(self):
        case = "AT:xtiic-mut-tfirst"
        ev = [_io_event(G.WRITE, 0x2104),
              dict(seq=2, kind="io", op=G.WRITE, address=0x2103, value=0,
                   decision=-1, latched=False, mpr1=0xFF, instruction=6)]
        row = _s6_row(mode=case, refused=False, refusal={"kind": "none"},
                      refusal_reason="none", instructions=6, io_writes=2,
                      events=ev, io_reads=0, wram_bytes_changed=2,
                      t_at_refusal=[0x1E82, 0x4002, 0, 0],
                      t_stop=[0x1E82, 0x4002, 0, 0])
        self.assertTrue(S6.MUTANTS[case](row))
        self.assertEqual(M.verdict(row, 3)[0], M.UNSAFE)
        with self.assertRaisesRegex(ValueError, "verdict"):
            S6.check_refused(row, case, S6.PRESTART_EXPECT)

    def test_tseq_landed_reds(self):
        case = "AT:xtddc-mut-tseq"
        ev = [_io_event(G.WRITE, 0x2104),
              dict(seq=2, kind="io", op=G.WRITE, address=0x2103, value=0,
                   decision=-1, latched=False, mpr1=0xFF, instruction=6)]
        row = _s6_row(mode=case, refused=False, refusal={"kind": "none"},
                      refusal_reason="none", instructions=6, io_writes=2,
                      events=ev, io_reads=0, wram_bytes_changed=2,
                      t_at_refusal=[0x1E82, 0x4002, 0, 0],
                      t_stop=[0x1E82, 0x4002, 0, 0])
        self.assertTrue(S6.MUTANTS[case](row))
        self.assertEqual(M.verdict(row, 3)[0], M.UNSAFE)
        with self.assertRaisesRegex(ValueError, "verdict"):
            S6.check_refused(row, case, S6.PRESTART_EXPECT)

    def test_tseq_false_positives_red_through_completed(self):
        for case in S6.FALSE_POSITIVE_MUTANTS:
            with self.subTest(case=case):
                op = case.split("x")[1][:3]
                row = _s6_completed_row(op)
                row["mode"] = case
                row.update(refused=True, refusal_reason="transfer_range",
                           wram_bytes_changed=0)
                # The wrong address model refuses an allowed range: the
                # positive judge must RED (named "refused"/false positive).
                with self.assertRaisesRegex(ValueError, "refused"):
                    S6.check_completed(row, case, S6.FULL[op])

    def test_tnoself_reds_and_mid_opcode_check_is_named(self):
        row = _s6_row(mode=S6.SELF_MID_CASE,
                      instructions=3, refusal_reason="transfer_opcode_changed",
                      wram_bytes_changed=1,
                      t_at_refusal=[0x1E00, 0x4002, 3, 0],
                      t_stop=[0x1E00, 0x4002, 3, 0])
        got, failing = S6.check_refused(row, S6.SELF_MID_CASE, S6.SELF_MID_EXPECT)
        self.assertEqual((got, failing), (M.BLOCKED, ["resume_safe"]))
        # The same inside-transfer row must RED as a completed transfer.
        with self.assertRaisesRegex(ValueError, "refused|verdict"):
            S6.check_completed(row, S6.SELF_MID_CASE, 4)
        # Without the mid opcode check the self rewrite lands: UNSAFE, RED as refused.
        ev = [_io_event(G.WRITE, 0x2104),
              dict(seq=2, kind="io", op=G.WRITE, address=0x2103, value=0,
                   decision=-1, latched=False, mpr1=0xFF, instruction=6)]
        landed = _s6_row(mode="AT:xself-armed", refused=False,
                         refusal={"kind": "none"}, refusal_reason="none",
                         instructions=6, io_writes=2, events=ev, io_reads=0,
                         wram_bytes_changed=4,
                         t_at_refusal=[0x1E00, 0x4002, 0, 0],
                         t_stop=[0x1E00, 0x4002, 0, 0])
        self.assertEqual(M.verdict(landed, 3)[0], M.UNSAFE)
        with self.assertRaisesRegex(ValueError, "verdict"):
            S6.check_refused(landed, "AT:xself-armed", S6.SELF_EXPECT)

    def test_no_entry_check_pattern_for_t_prestart(self):
        # #712 technique for T: an unguarded landed pre-start row must never
        # pass as a refused pre-start row (named RED, not silent PASS).
        ev = [_io_event(G.WRITE, 0x2104),
              dict(seq=2, kind="io", op=G.WRITE, address=0x2103, value=0,
                   decision=-1, latched=False, mpr1=0xFF, instruction=6)]
        landed = _s6_row(mode="AT:xtiid-armed", refused=False,
                         refusal={"kind": "none"}, refusal_reason="none",
                         instructions=6, io_writes=2, events=ev, io_reads=0,
                         wram_bytes_changed=4,
                         t_at_refusal=[0x1E82, 0x4002, 0, 0],
                         t_stop=[0x1E82, 0x4002, 0, 0])
        with self.assertRaisesRegex(ValueError, "verdict"):
            S6.check_refused(landed, "AT:xtiid-armed", S6.PRESTART_EXPECT)
        # And a D/T sweep allowed row must RED if judged as refused.
        start = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
        applied = [0x80 if 0x0C >> i & 1 else start[i] for i in range(8)]
        allowed = _sweep_row("AD:tam0cff-armed", False, "none", 2,
                             TAM_NEXT, applied, budget=True)
        S5.check_sweep(allowed, "AD:tam0cff-armed", "AD", 0x0C, 0x80)
        with self.assertRaisesRegex(ValueError, "want"):
            S5.check_sweep(dict(allowed, refused=True,
                                refusal_reason="decode_tam", instructions=1,
                                stop=_snap(pc=GUEST, mpr=start),
                                refusal_instruction_start=_snap(pc=GUEST, mpr=start)),
                           "AD:tam0cff-armed", "AD", 0x0C, 0x80)


class ImportFailureNotKilledTests(unittest.TestCase):
    """Probe ERROR / timeout / missing evidence must never count as killed."""

    def test_probe_failure_rows_raise_and_are_not_visible(self):
        failed = {"mode": "AD:irqtam-mut-doff", "exit": 1,
                  "stderr": "probe crashed"}
        with self.assertRaisesRegex(ValueError, "probe failed"):
            S5._row_ok(failed, "AD:irqtam-mut-doff")
        self.assertFalse(S5.MUTANTS["AD:irqtam-mut-doff"][1](failed))
        failed6 = {"mode": "AT:xtiim-mut-tresume", "exit": 1,
                   "stderr": "timeout after 120s"}
        with self.assertRaisesRegex(ValueError, "probe failed"):
            S6._row_ok(failed6, "AT:xtiim-mut-tresume")
        self.assertFalse(S6.MUTANTS["AT:xtiim-mut-tresume"](failed6))

    def test_timeout_marker_is_not_a_detection(self):
        row = _s6_row(mode="AT:xtiim-mut-tresume", t_resumed=True,
                      instructions_after_refusal=5, io_after_refusal=2,
                      io_writes=0, events=[], io_reads=0, exit=1,
                      stderr="TimeoutExpired")
        with self.assertRaisesRegex(ValueError, "probe failed"):
            S6._row_ok(row, "AT:xtiim-mut-tresume")
        self.assertFalse(S6.MUTANTS["AT:xtiim-mut-tresume"](
            dict(t_resumed=True, instructions_after_refusal=5,
                 io_after_refusal=0)))

    def test_missing_evidence_is_not_killed(self):
        start = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
        row = _s5_row("AD:irqtam-armed", True, "decode_tam", 1, GUEST,
                      start, decode=1, entry=0)
        broken = copy.deepcopy(row)
        del broken["decode_refusals"]
        with self.assertRaises((ValueError, KeyError, TypeError)):
            S5.check_policy_row(broken, "AD:irqtam-armed",
                                S5.irq_expect("AD", "irqtam"))
        broken6 = _s6_row()
        del broken6["t_stop"]
        with self.assertRaises((ValueError, KeyError, TypeError)):
            S6.check_refused(broken6, "AT:xtiim-armed", S6.mid_expect("tii", "m"))

    def test_error_rows_do_not_pass_as_refused(self):
        start = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
        row = _s5_row("AD:irqtam-mut-doff", False, "mpr1", 2, HANDLER,
                      [0xF8, 0xFF, 0, 0, 0, 0, 0, 0], decode=0, io_reads=0,
                      events=[], entry=1)
        row["exit"] = 2
        row["stderr"] = "ERROR: harness timeout"
        with self.assertRaisesRegex(ValueError, "probe failed"):
            S5.check_policy_row(row, "AD:irqtam-mut-doff",
                                S5.irq_expect("AD", "irqtam"))


class TargetVersionEvidenceTests(unittest.TestCase):
    """Mutation target version / expectations / safety scope (gap: no host pin)."""

    def test_pinned_core_and_hook_are_shared_by_d_and_t_drivers(self):
        self.assertEqual(G.PINNED_CORE_REVISION, PINNED)
        self.assertEqual(S6.G.PINNED_CORE_REVISION, PINNED)
        self.assertIs(S5.G, S6.G)

    def test_mutation_ids_match_the_648_spec(self):
        tresume = sorted(c for c in S6.MUTANTS if c.endswith("mut-tresume"))
        tnomid = sorted(c for c in S6.MUTANTS if c.endswith("mut-tnomid"))
        self.assertEqual(len(tresume), 6)
        self.assertEqual(len(tnomid), 5)
        self.assertIn("AT:xtiic-mut-tfirst", S6.MUTANTS)
        self.assertIn("AT:xtddc-mut-tseq", S6.MUTANTS)
        self.assertEqual(S6.SELF_MID_CASE, "AT:xself-mut-tnoself")
        self.assertEqual(S6.SELF_MID_EXPECT["reason"], "transfer_opcode_changed")
        self.assertEqual(S6.SELF_MID_EXPECT["verdict"], M.BLOCKED)

    def test_d_mutation_ids_match_the_647_spec(self):
        self.assertEqual(set(S5.MUTANTS),
                         {"AD:irqtam-mut-doff", "AD:irqtam-mut-dbit",
                          "AE:irqtam-mut-eoff"})
        self.assertEqual(set(S5.SWEEP_MUTANTS),
                         {"AD:tam01ff-mut-dbit", "AD:tam02ff-mut-dbit"})

    def test_mid_transfer_refusal_is_blocked_not_safe(self):
        for op in S6.OPS:
            for kind in S6.MID_KINDS:
                with self.subTest(op=op, kind=kind):
                    self.assertEqual(S6.mid_expect(op, kind)["verdict"], M.BLOCKED)
                    self.assertIn("resume_safe",
                                  S6.mid_expect(op, kind)["failing"])
        self.assertEqual(S6.SELF_MID_EXPECT["verdict"], M.BLOCKED)


if __name__ == "__main__":
    unittest.main()
