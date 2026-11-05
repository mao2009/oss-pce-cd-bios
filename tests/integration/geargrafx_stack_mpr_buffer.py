#!/usr/bin/env python3
"""Issue #654 (S8 of #473): the TAM/TMA buffer (m_mpr_buffer) and TMA #$00 on the pinned real core.

S5 (#647) observed MPR0-7 only. The pinned source keeps a second piece of state for TAM/TMA:
  Memory::SetMprTAM(bits, value): bits == 0 -> returns without any change; else m_mpr_buffer = value and each selected
                                  MPR = value.
  Memory::GetMprTMA(bits):        bits == 0 -> returns m_mpr_buffer; else returns the OR of the selected MPRs and
                                  stores it in m_mpr_buffer.
  Memory::Reset():                m_mpr_buffer = 0.
The verification-only patch adds a read-only accessor (Memory::GG_VerifyMprBuffer). The probe records A, the buffer
and MPR1 at every instruction start (route mb_<scenario>; MPR3 = $01 and MPR4 = $02 for distinct OR values):
  tam1    TAM #$04 (A = $F8); LDA #$00; TMA #$00       single MPR
  tamm    TAM #$0C (A = $F8); LDA #$00; TMA #$00       two MPRs
  tmam    TMA #$18; LDA #$00; TMA #$00                 multi-bit TMA: OR of MPR3 | MPR4 = $03
  tam0    TMA #$10; LDA #$55; TAM #$00; TMA #$00       TAM with no bit leaves the buffer
  refuse  TMA #$10; LDA #$FF; TAM #$02; TMA #$00       forbidden TAM (MPR1 -> $FF): refused by D before it runs, or
                                                       run (A, E) and refused at the next instruction
  irq     IRQ1 pending, I = 0; TAM #$04 (A = $F8); the handler runs LDA #$00; TMA #$00
  irqf    IRQ1 pending, I = 0; TAM #$02 (A = $FF)     IRQ-in-TAM with a forbidden TAM
Each value below follows from the pinned source and was written before the first CI run (checked against a local
exploratory MinGW build of an earlier revision, supplementary). A is cleared before every TMA #$00, so the value it
returns is checked in A independently of the accessor; a broken accessor (replaced by a constant) is caught by the
buffer checkpoints. The added buffer check on refused rows holds by construction on a correct run (the refused
instruction never runs); it is exercised by a falsified row, and the D mutants are caught first by the refusal
point.

Refused rows are judged on the eight criteria of geargrafx_stack_methods.py plus one added check: the buffer at the
stop equals the buffer at the refused instruction's start, and (for D) the buffer before the TAM, so a refusal that
lets the TAM's buffer write through is not judged SAFE. The eight criteria themselves are unchanged.

Not covered: real hardware (#655), the libretro build (#103). Synthetic BIOS/CD only. Not a production mechanism.
#173/#367/#459, BLOCKED_ABI, release_eligible, DEV_CALLABLE and ABI VERIFIED are unchanged.

    python3 tests/integration/geargrafx_stack_mpr_buffer.py      (make stack-mpr-buffer; part of make integration)
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geargrafx_stack_methods as M  # noqa: E402
G = M.G

SAFE, UNSAFE = M.SAFE, M.UNSAFE
H, GUEST = G.HANDLER, G.GUEST
INITIAL = 0x00          # Memory::Reset
# Checkpoints: (instruction start, A or None, buffer, MPR1 or None).
COMPLETED = {
    "tam1": [(2, None, 0xF8, 0xF8), (3, 0x00, 0xF8, 0xF8), (4, 0xF8, 0xF8, 0xF8)],
    "tamm": [(2, None, 0xF8, 0xF8), (3, 0x00, 0xF8, 0xF8), (4, 0xF8, 0xF8, 0xF8)],
    "tmam": [(2, 0x03, 0x03, 0xF8), (3, 0x00, 0x03, 0xF8), (4, 0x03, 0x03, 0xF8)],
    "tam0": [(2, 0x02, 0x02, 0xF8), (4, 0x55, 0x02, 0xF8), (5, 0x02, 0x02, 0xF8)],
    "irq": [(2, None, 0xF8, 0xF8), (3, 0x00, 0xF8, 0xF8), (4, 0xF8, 0xF8, 0xF8)],   # handler: TMA #$00 = TAM's value
}
COMPLETED_MPRS = {"tam1": {2: 0xF8}, "tamm": {2: 0xF8, 3: 0xF8}, "tmam": {3: 0x01, 4: 0x02},
                  "tam0": {4: 0x02}, "irq": {2: 0xF8}}
# Refused rows: case -> (eight-criteria expectation, buffer before the TAM, buffer at the stop).
REFUSED = {
    "AD:mb_refuse-allowed": (dict(verdict=SAFE, failing=set(), index=3, pc=0x1E04, reason="decode_tam"), 0x02, 0x02),
    "A:mb_refuse-allowed": (dict(verdict=SAFE, failing=set(), index=4, pc=0x1E06, reason="mpr1"), 0x02, 0xFF),
    "AE:mb_refuse-allowed": (dict(verdict=SAFE, failing=set(), index=4, pc=0x1E06, reason="mpr1"), 0x02, 0xFF),
    "AD:mb_irqf-allowed": (dict(verdict=SAFE, failing=set(), index=1, pc=GUEST, reason="decode_tam"), INITIAL, INITIAL),
    "AE:mb_irqf-allowed": (dict(verdict=SAFE, failing=set(), index=2, pc=0x1E02, reason="mpr1"), INITIAL, 0xFF),
    "A:mb_irqf-allowed": (dict(verdict=UNSAFE, failing={"io_prevented", "resume_safe"}, index=2, pc=H, io=M.PUSH_IO,
                               reason="mpr1"), INITIAL, 0xFF),
}
# Mutants: case -> description; each must make its correct expectation fail.
MUTANTS = {
    "AD:mb_refuse-mut-doff": ("AD:mb_refuse-allowed", "D off: the TAM runs and writes the buffer"),
    "AD:mb_refuse-mut-dbit": ("AD:mb_refuse-allowed", "D checks the wrong bit: the TAM runs"),
    "A:mb_tmam-mut-mbconst": ("A:mb_tmam-allowed", "broken observation: the accessor reports a constant"),
    "A:mb_tam1-mut-mbconst": ("A:mb_tam1-allowed", "broken observation"),
}


def _row_ok(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case}: probe failed (exit {row.get('exit')}: {row.get('stderr', '')[-120:]})")
    M._snapshot_fields(row, case)
    mb = row.get("mb")
    if type(mb) is not dict or mb.get("active") is not True or type(mb.get("timeline")) is not list:
        raise ValueError(f"{case}: missing #654 observation")
    if not mb["timeline"] or mb["timeline"][0]["i"] != 1:
        raise ValueError(f"{case}: timeline does not start at instruction 1")


def _at(row, i):
    for e in row["mb"]["timeline"]:
        if e["i"] == i:
            return e
    raise ValueError(f"{row['mode']}: no timeline entry for instruction {i}")


def check_completed(row, case):
    _row_ok(row, case)
    scenario = case.split(":")[1].split("-", 1)[0][3:]
    if row.get("refused") is not False:
        raise ValueError(f"{case}: refused ({row.get('refusal_reason')})")
    if _at(row, 1)["buf"] != INITIAL:
        raise ValueError(f"{case}: initial buffer {_at(row, 1)['buf']:#x}, want {INITIAL:#x} (Memory::Reset)")
    for i, a, buf, mpr1 in COMPLETED[scenario]:
        e = _at(row, i)
        if (a is not None and e["a"] != a) or e["buf"] != buf or (mpr1 is not None and e["mpr1"] != mpr1):
            raise ValueError(f"{case}: instruction {i}: A/buffer/MPR1 {e['a']:#x}/{e['buf']:#x}/{e['mpr1']:#x}, "
                             f"want {a}/{buf:#x}/{mpr1}")
    for index, value in COMPLETED_MPRS[scenario].items():
        if row["stop"]["mpr"][index] != value:
            raise ValueError(f"{case}: MPR{index} {row['stop']['mpr'][index]:#x}, want {value:#x}")
    if row["mb"]["stop_buf"] != COMPLETED[scenario][-1][2]:
        raise ValueError(f"{case}: buffer at the stop {row['mb']['stop_buf']:#x}")
    if row["io_reads"] or row["io_writes"]:
        raise ValueError(f"{case}: hardware page accessed")
    if row["instructions_after_refusal"] != 0:
        raise ValueError(f"{case}: instructions after a refusal")
    want_entry = 2 if scenario == "irq" else 0
    if row.get("handler_entry_instruction") != want_entry:
        raise ValueError(f"{case}: handler entry {row.get('handler_entry_instruction')}, want {want_entry}")
    return [(i, _at(row, i)["a"], _at(row, i)["buf"]) for i, *_ in COMPLETED[scenario]]


def check_refused(row, case):
    _row_ok(row, case)
    expect, before, stop = REFUSED[case]
    got, failing = M.check_armed(row, case, expect)
    if row["instructions_after_refusal"] != 0:   # also on the UNSAFE row: nothing ran after the refusal
        raise ValueError(f"{case}: {row['instructions_after_refusal']} instructions after the refusal")
    if row.get("refusal_reason") != expect["reason"]:
        raise ValueError(f"{case}: refusal reason {row.get('refusal_reason')!r}, want {expect['reason']!r}")
    tam_index = 3 if "refuse" in case else 1
    if _at(row, tam_index)["buf"] != before:
        raise ValueError(f"{case}: buffer before the TAM {_at(row, tam_index)['buf']:#x}, want {before:#x}")
    at_refusal = _at(row, expect["index"])["buf"]
    if row["mb"]["stop_buf"] != stop or row["mb"]["stop_buf"] != at_refusal:
        raise ValueError(f"{case}: buffer at the stop {row['mb']['stop_buf']:#x} (at the refused instruction's start "
                         f"{at_refusal:#x}), want {stop:#x}")
    return got, failing, before, stop


def check_unguarded(row, case):
    _row_ok(row, case)
    if row.get("refused") is not False or _at(row, 5)["a"] != 0xFF or _at(row, 5)["buf"] != 0xFF:
        raise ValueError(f"{case}: unguarded TAM #$02 did not leave A = buffer = $FF for TMA #$00")
    return "TAM ran: MPR1 = $FF, buffer = $FF, TMA #$00 = $FF"


def _cases():
    for method in ("A", "AD"):
        for scenario in COMPLETED:
            yield f"{method}:mb_{scenario}-allowed"
    yield from REFUSED
    yield "A:mb_refuse-unguarded"
    yield from MUTANTS


def cases():
    return list(dict.fromkeys(_cases()))


def evaluate(rows):
    completed, refused, detections = {}, {}, []
    for method in ("A", "AD"):
        for scenario in COMPLETED:
            case = f"{method}:mb_{scenario}-allowed"
            completed[case] = check_completed(rows[case], case)
    for case in REFUSED:
        refused[case] = check_refused(rows[case], case)
        if refused[case][0] == SAFE:
            for name, broken in M.falsified(rows[case]):
                if M.verdict(broken)[0] == SAFE:
                    raise ValueError(f"{case}: falsified {name} still judged SAFE")
                detections.append((case, f"falsified:{name}", M.verdict(broken)[0]))
            broken = copy.deepcopy(rows[case])
            broken["mb"]["stop_buf"] ^= 0x01
            try:
                check_refused(broken, case)
            except ValueError:
                detections.append((case, "falsified:mpr_buffer", "rejected"))
            else:
                raise ValueError(f"{case}: a changed buffer at the stop was not rejected")
    control = check_unguarded(rows["A:mb_refuse-unguarded"], "A:mb_refuse-unguarded")
    for case, (correct, label) in MUTANTS.items():
        row = copy.deepcopy(rows[case])
        _row_ok(row, case)
        row["mode"] = correct   # judge the mutant run with the correct row's expectation
        check = check_refused if correct in REFUSED else check_completed
        try:
            check(row, correct)
        except ValueError as error:
            detections.append((case, f"mutant ({label})", f"rejected: {error}"[:150]))
        else:
            raise ValueError(f"mutation {case}: passed the correct expectation ({label})")
    return completed, refused, control, detections


def main():
    from geargrafx_reference_guard import synthetic_media
    with tempfile.TemporaryDirectory(prefix="pce-stack-mb-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = G.build_probes(directory)
        rows = {case: G.run_case(probe, bios, cue, case) for case in cases()}
    completed, refused, control, detections = evaluate(rows)
    for case, row in rows.items():
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": G.PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "row": row}, sort_keys=True))
    for case, label, result in detections:
        print(f"DETECTED {case} [{label}]: {result}")
    for case, points in completed.items():
        print(f"BUFFER {case}: " + ", ".join(f"instr {i}: A={a:#04x} buffer={b:#04x}" for i, a, b in points))
    for case, (got, failing, before, stop) in refused.items():
        print(f"REFUSED {case}: {got}({','.join(failing) or '-'}) buffer before the TAM {before:#04x}, at the stop "
              f"{stop:#04x}")
    print(f"CONTROL A:mb_refuse-unguarded: {control}")
    print(f"EXPECTATIONS_MET issue-654 m_mpr_buffer / TMA #$00: {len(rows)} real-core runs, {len(detections)} "
          f"detections, expectations met (pinned core {G.PINNED_CORE_REVISION}, synthetic BIOS/CD, verification-only "
          "patch)")
    print("RESULT issue-654 (observed fixtures only): TAM writes the buffer (single and multiple MPRs); TAM #$00 "
          "leaves it; a multi-bit TMA stores the OR; TMA #$00 returns it, also in an IRQ handler entered inside the "
          "TAM. Method D refuses the forbidden TAM before it runs: the buffer keeps its value from before the TAM. "
          "Methods A and E let the TAM run and refuse the next instruction: the buffer holds the forbidden value at "
          "the stop (not hidden). IRQ-in-TAM with a forbidden TAM stays UNSAFE under A alone.")
    print("UNKNOWN: real hardware (#655), the libretro build (#103). Not a production mechanism; no gate changes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError) as error:
        print(f"FAIL issue-654 m_mpr_buffer: {error}", file=sys.stderr)
        raise SystemExit(1) from error
