"""Issue #498: MPR1 pre-check (method A) against TAM + IRQ1 on the pinned Geargrafx core. Synthetic HuCARD only.

Each case is a separate build of tests/fixtures/mpr1_tam_irq.s, run on the UNMODIFIED core by
geargrafx_mpr1_tam_irq_probe.cpp in two modes:
  measure  the full STEPS budget is run; records every state, WRAM and VDC change (no stop).
  safety   the probe HALTS at the first instruction boundary where MPR1 is not the allowed stack bank: it runs no
           further guest instruction. This is a host-side stop of the emulated guest (the probe stops calling
           RunInstruction). It is NOT a guest-side safety mechanism. The HALT / HALTMEM / END records show the halt.
The guest executes TAM #$02 (MPR1 = A) at tam_site; the host asserts IRQ1 before that step where the case says so.
Method A only checks MPR1 at instruction boundaries, so the question is whether an IRQ push can happen inside the
TAM's own RunInstruction, before the boundary check sees MPR1 = $FF. Scheme A alone cannot stop that push.

The expected verdicts are the measured results on the pinned core (b49ae82). N-IRQ-TAM is EXPECTED to be
UNSAFE_AFTER_SIDE_EFFECT: it documents the hole. It is not a PASS, and it does not unblock any gate. If the core
behaviour changes, the test fails so the verdict is re-judged by hand. A green run does not mean the scheme is safe.
Expected return values come from the fixture code (the constants below), not from the probe's own pre-TAM record.
Missing core or tool: the test fails (never skipped). Run through the integration suite (make integration-test).
"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import executable  # noqa: E402
import geargrafx_api as api  # noqa: E402
import mpr1_stack_contract as contract  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/mpr1_tam_irq.s"
CFG = ROOT / "tests/fixtures/caller_stack_contract.cfg"  # the same 8 KiB HuCARD layout (ROM $E000, vectors $FFF6)
PROBE_SOURCE = "geargrafx_mpr1_tam_irq_probe.cpp"
PROBE_TARGET = "geargrafx-mpr1-tam-irq-probe"
LABEL_LINE = re.compile(r"^al ([0-9A-Fa-f]{6}) \.(\w+)$", re.M)
LABELS = ("tam_site", "after_tam", "irq1_entry")
RECORDS = ("CAP", "IRQ", "STEP", "RET", "WRW", "VDW", "HITS", "HALT", "HALTMEM", "END")
STEPS = 40
MODES = ("measure", "safety")
ALLOWED_STACK_MPR1 = next(iter(contract.ALLOWED_STACK_MPR1))  # $F8, from the #497 table (one allowed value)
IRQ1_BIT = 0x02
I_FLAG = 0x04
PUSH_BYTES = (0x1FD, 0x1FE, 0x1FF)  # WRAM offsets of the P / PCL / PCH stack pushes at S=$FF (page $21xx)
# Verdict names (what was measured, nothing more).
#   UNSAFE_AFTER_SIDE_EFFECT: a side effect and an IRQ acceptance happened inside the TAM step. The halt at the next
#     boundary comes after that side effect, so method A cannot have prevented it.
#   REJECTED_NO_WRAM_VDC_CHANGE_HALTED: MPR1 became a denied bank, no state change was observed in the TAM
#     step (WRAM/VDC value changes only), and the safety run halted with no guest instruction executed after it.
#   REJECTED_NO_WRAM_VDC_CHANGE_HALT_NOT_MEASURED: same observation, but no halt was measured. Not a pass.
UNSAFE = "UNSAFE_AFTER_SIDE_EFFECT"
REJECTED = "REJECTED_NO_WRAM_VDC_CHANGE_HALTED"
REJECTED_NO_HALT = "REJECTED_NO_WRAM_VDC_CHANGE_HALT_NOT_MEASURED"
PASS = "PASS"
# Fixture-derived constants (tests/fixtures/mpr1_tam_irq.s): A is loaded with $FF (or $F8 in CASE_TAM_F8) before the
# TAM; X=$FF and S=$FF are set by ldx/txs; Y=0 by ldy; P after the TAM is N (from the lda) with I=0 (cli) or I=1 (sei kept).
FIXTURE_X = 0xFF
FIXTURE_Y = 0x00
FIXTURE_S = 0xFF
FIXTURE_MPR0 = 0xF8  # lda #$F8 / tam #$01
CASES = [
    {"name": "n_irq_tam", "define": "CASE_TAM_IRQ", "irq": "at-tam", "i_flag": 0, "expect": UNSAFE,
     "a": 0xFF, "ret_p": None},
    {"name": "ff_no_irq", "define": "CASE_TAM_NOIRQ", "irq": "none", "i_flag": 0, "expect": REJECTED,
     "a": 0xFF, "ret_p": 0x80},
    {"name": "ff_irq_masked", "define": "CASE_TAM_MASKED", "irq": "at-tam", "i_flag": 1, "expect": REJECTED,
     "a": 0xFF, "ret_p": 0x84},
    {"name": "f8_irq", "define": "CASE_TAM_F8", "irq": "at-tam", "i_flag": 0, "expect": PASS,
     "a": 0xF8, "ret_p": 0x80},
]


def build_case(define, folder):
    obj, rom, ld_labels = folder / f"{define}.o", folder / f"{define}.pce", folder / f"{define}.ldlbl"
    subprocess.run([str(executable("ca65")), "-D", define, str(FIXTURE), "-o", str(obj)], check=True,
                   capture_output=True, text=True)
    subprocess.run([str(executable("ld65")), "-C", str(CFG), "-Ln", str(ld_labels), "-o", str(rom), str(obj)],
                   check=True, capture_output=True, text=True)
    labels = {name: int(address, 16) for address, name in LABEL_LINE.findall(ld_labels.read_text())}
    missing = [name for name in LABELS if name not in labels]
    if missing or rom.stat().st_size != 8192:
        raise ValueError(f"{define}: fixture missing labels {missing} or size {rom.stat().st_size} != 8192")
    return rom, labels


def run_probe(probe, rom, labels, irq, mode, folder):
    label_file = folder / f"{rom.stem}.{mode}.labels"
    label_file.write_text("".join(f"{name} {address:x}\n" for name, address in labels.items()))
    result = subprocess.run([str(probe), str(rom), str(label_file), str(STEPS), irq, mode, str(ALLOWED_STACK_MPR1)],
                            check=True, capture_output=True, text=True, timeout=120)
    records = {}
    for line in result.stdout.splitlines():  # the core prints banner lines too; only tagged JSON records count
        prefix, _, payload = line.partition(" ")
        if prefix in RECORDS:
            records.setdefault(prefix, []).append(json.loads(payload))
    if "STEP" not in records or "END" not in records or len(records["STEP"]) != 2:
        raise ValueError("probe produced no before/after STEP pair (tam_site not reached)")
    return records


def halt_evidence(records):
    """The safety-mode stop. demonstrated = the probe halted at a denied boundary, END repeats the halt state,
    no guest instruction ran after the halt (executed == halt step) and no WRAM/VDC change came after it."""
    halts = (records or {}).get("HALT", [])
    if not halts:
        return {"reached": False, "demonstrated": False}
    halt, memory, end = halts[0], records["HALTMEM"][0], records["END"][-1]
    later = [item for item in records.get("WRW", []) + records.get("VDW", []) if item["step"] > halt["step"]]
    demonstrated = (end["halted"] is True and end["executed"] == halt["step"] and end["executed"] < end["budget"]
                    and end["pc"] == halt["pc"] and end["mpr"] == halt["mpr"] and not later)
    return {"reached": True, "demonstrated": demonstrated, "step": halt["step"], "pc": halt["pc"],
            "a": halt["a"], "x": halt["x"], "y": halt["y"], "p": halt["p"], "s": halt["s"], "mpr": halt["mpr"],
            "irr": halt["irr"], "idr": halt["idr"], "stack": memory["stack"], "vdc_r0": memory["vdc_r0"],
            "later_side_effects": len(later)}


def judge(measure, safety, labels):
    """Classify one case from the real-core records. measure = the measure-mode run, safety = the safety-mode run."""
    step = {item["phase"]: item for item in measure["STEP"]}
    before, after = step["before"], step["after"]
    tam_step = before["step"]
    side = [item for item in measure.get("WRW", []) + measure.get("VDW", []) if item["step"] == tam_step]
    irq_taken = after["pc"] == labels["irq1_entry"]
    mpr1_before, mpr1_after = before["mpr"][1], after["mpr"][1]
    changed = mpr1_before != mpr1_after
    irq_asserted = bool(before["irr"] & IRQ1_BIT)  # observed request before the TAM
    irq_pending = bool(before["irr"] & ~before["idr"] & IRQ1_BIT) and not (before["p"] & I_FLAG)
    # Method A boundary verdict: MPR1 as it stands at the next instruction boundary (after the TAM).
    boundary, boundary_detail = contract.check_stack_bank(mpr1_after)
    # The #497 contract's IRQ window verdict for the same instruction (an overlap is UNMEASURED, a hole kept open).
    contract_irq = contract.judge_irq_window(changed, irq_asserted, irq_pending)
    halt = halt_evidence(safety)
    if changed and side and irq_taken:
        measured = UNSAFE
    elif changed and boundary == contract.FAIL and not side and not irq_taken:
        measured = REJECTED if halt["demonstrated"] else REJECTED_NO_HALT
    elif not changed and boundary == contract.PASS:
        measured = PASS
    else:
        measured = "UNEXPECTED"
    diffs = [key for key in ("pc", "a", "x", "y", "p", "s", "mpr", "irr", "idr") if before[key] != after[key]]
    # The first arrival at after_tam (None if the guest never got back there in the step budget).
    ret = measure.get("RET", [None])[0]
    wram_new = {item["wram"]: item["new"] for item in side if "wram" in item}
    return {"measured": measured, "boundary": boundary, "boundary_detail": boundary_detail,
            "contract_irq": contract_irq, "mpr1": (mpr1_before, mpr1_after), "irq_asserted": irq_asserted,
            "irq_pending": irq_pending, "irq_taken": irq_taken, "side_effects": len(side),
            "side_regs": sorted({f"{item['reg']}" if "reg" in item else f"WRAM{item['wram']:04X}" for item in side}),
            "wram_new": wram_new, "ret": ret, "halt": halt, "tam_side": side,
            "state_diffs": diffs, "before": before, "after": after}


class Mpr1TamIrqRealCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-mpr1-tam-irq-")
        folder = Path(cls._tmp.name)
        probe = api.compile_probe(folder, source_name=PROBE_SOURCE, target_name=PROBE_TARGET)
        cls.results = {}
        cls.labels = {}
        cls.runs = {}
        for case in CASES:
            rom, labels = build_case(case["define"], folder)
            measure = run_probe(probe, rom, labels, case["irq"], "measure", folder)
            safety = run_probe(probe, rom, labels, case["irq"], "safety", folder)
            cls.runs[case["name"]] = {"measure": measure, "safety": safety}
            r = cls.results[case["name"]] = judge(measure, safety, labels)
            r["rom_sha256"] = hashlib.sha256(rom.read_bytes()).hexdigest()
            cls.labels[case["name"]] = labels
            ret = r["ret"]
            ret_text = "none" if ret is None else f"pc={ret['pc']:04X} p={ret['p']:02X} s={ret['s']:02X}"
            h = r["halt"]
            halt_text = ("none" if not h["reached"] else
                         f"step={h['step']} pc={h['pc']:04X} s={h['s']:02X} p={h['p']:02X} "
                         f"mpr1={h['mpr'][1]:02X} stack={h['stack']} vdc_r0={h['vdc_r0']} "
                         f"later={h['later_side_effects']} demonstrated={h['demonstrated']}")
            print(f"MEASURE {case['name']}: measured={r['measured']} "
                  f"boundary={r['boundary']} "
                  f"contract_irq={r['contract_irq']} "
                  f"mpr1={r['mpr1']} side_effects={r['side_effects']} "
                  f"regs={r['side_regs']} diffs={r['state_diffs']} "
                  f"pc={r['before']['pc']:04X}->{r['after']['pc']:04X} "
                  f"s={r['before']['s']:02X}->{r['after']['s']:02X} "
                  f"p={r['before']['p']:02X}->{r['after']['p']:02X} "
                  f"irr={r['before']['irr']:02X} idr={r['before']['idr']:02X} "
                  f"wram_new={ {hex(k): hex(v) for k, v in sorted(r['wram_new'].items())} } "
                  f"ret={ret_text} halt[{halt_text}]")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_case_verdicts_match_measured_core_behaviour(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                self.assertEqual(self.results[case["name"]]["measured"], case["expect"])

    def test_tam_start_pending_state_is_asserted(self):
        # The precondition of each case, asserted from the fixture-set values and the "before" record.
        for case in CASES:
            with self.subTest(case=case["name"]):
                before = self.results[case["name"]]["before"]
                pending_expected = case["irq"] == "at-tam"
                self.assertEqual(bool(before["irr"] & IRQ1_BIT), pending_expected)  # IRQ1 request latched
                self.assertEqual(bool(before["p"] & I_FLAG), bool(case["i_flag"]))  # I flag as the fixture sets it
                self.assertEqual(before["idr"] & IRQ1_BIT, 0)  # IRQ1 not disabled by IDR
                self.assertEqual(self.results[case["name"]]["irq_pending"], pending_expected and not case["i_flag"])
                self.assertEqual(before["mpr"][1], 0xF8)  # MPR1 = WRAM before the TAM
                self.assertEqual(before["a"], case["a"])  # A loaded by the fixture before the TAM
                self.assertEqual(before["s"], FIXTURE_S)
                self.assertEqual(before["x"], FIXTURE_X)
                self.assertEqual(before["y"], FIXTURE_Y)

    def test_n_irq_tam_pins_known_hole_as_unsafe(self):
        # A pinned UNSAFE verdict (the hole is real on the pinned core). It is NOT a pass and does not unblock a gate.
        n = self.results["n_irq_tam"]
        self.assertEqual(n["measured"], UNSAFE)
        self.assertEqual(n["mpr1"], (0xF8, 0xFF))
        self.assertTrue(n["irq_taken"])
        self.assertGreater(n["side_effects"], 0)
        self.assertEqual(n["side_regs"], ["R0"])  # only the VDC R0 value change is observed (see the doc for the rest)
        self.assertEqual(n["contract_irq"][0], contract.UNMEASURED)  # the #497 contract keeps this window open

    def test_rejection_happens_before_any_side_effect(self):
        for name in ("ff_no_irq", "ff_irq_masked"):
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["side_effects"], 0)
                self.assertFalse(self.results[name]["irq_taken"])
                self.assertEqual(self.results[name]["boundary"], contract.FAIL)

    def test_safety_mode_halts_at_first_denied_boundary(self):
        # #498: after a rejection the guest is stopped. The halt must be demonstrated in the real core for every
        # denied MPR1 case: the probe runs no instruction after the halt, and END repeats the halt state.
        for name in ("n_irq_tam", "ff_no_irq", "ff_irq_masked"):
            with self.subTest(case=name):
                h = self.results[name]["halt"]
                self.assertTrue(h["reached"], "no halt at the denied boundary")
                self.assertTrue(h["demonstrated"])
                self.assertEqual(h["later_side_effects"], 0)
                self.assertEqual(h["mpr"][1], 0xFF)
                self.assertIn(self.results[name]["measured"], (UNSAFE, REJECTED))

    def test_halt_state_is_fixture_derived(self):
        # Halt points and values from the fixture (not from the probe's before-record).
        after_tam = self.labels["ff_no_irq"]["after_tam"]
        h = self.results["ff_no_irq"]["halt"]
        self.assertEqual(h["pc"], after_tam)  # halted at the instruction after TAM (the bra loop is not executed)
        self.assertEqual(h["s"], FIXTURE_S)  # no push: S stays $FF
        self.assertEqual(h["a"], 0xFF)
        self.assertEqual(h["x"], FIXTURE_X)
        self.assertEqual(self.results["ff_no_irq"]["side_effects"], 0)
        n = self.results["n_irq_tam"]["halt"]
        self.assertEqual(n["pc"], self.labels["n_irq_tam"]["irq1_entry"])  # halted before the RTI of the IRQ1 handler
        self.assertEqual(n["s"], FIXTURE_S - 3)  # measured: S dropped by 3 at the IRQ1 entry
        self.assertTrue(n["p"] & I_FLAG)  # the IRQ1 entry sets I
        self.assertEqual(n["mpr"][1], 0xFF)
        # Measured: only one VDC R0 value change is observed; WRAM $1FD-$1FF is unchanged (HALTMEM = 0; not evidence of the push
        # target). The push target (bank $FF, I/O write) is an ESTIMATE that matches this R0 change, not a measurement.
        self.assertEqual(self.results["n_irq_tam"]["side_regs"], ["R0"])

    def test_measure_mode_keeps_full_step_budget_and_no_halt(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                end = self.runs[case["name"]]["measure"]["END"][-1]
                self.assertEqual(end["budget"], STEPS - 1)  # the SEI before the loop is not in the budget
                self.assertEqual(end["executed"], STEPS - 1)
                self.assertFalse(end["halted"])
                self.assertNotIn("HALT", self.runs[case["name"]]["measure"])

    def test_safety_run_matches_measure_run_at_tam_step(self):
        # Same ROM, same host IRQ timing: the TAM step's state and side effects must not depend on the mode.
        for case in CASES:
            with self.subTest(case=case["name"]):
                m, s = self.runs[case["name"]]["measure"], self.runs[case["name"]]["safety"]
                # MPR2-6 are power-on values the fixture never sets; they differ between probe processes (seen in
                # 1 of 8 runs). Only the fixture-controlled MPR0/MPR1 and the reset-defined MPR7 are compared.
                def fixed(step):
                    return {**step, "mpr": [v for i, v in enumerate(step["mpr"]) if i in (0, 1, 7)]}
                self.assertEqual([fixed(x) for x in m["STEP"]], [fixed(x) for x in s["STEP"]])
                tam_m = [x for x in m.get("WRW", []) + m.get("VDW", []) if x["step"] == m["STEP"][0]["step"]]
                tam_s = [x for x in s.get("WRW", []) + s.get("VDW", []) if x["step"] == s["STEP"][0]["step"]]
                self.assertEqual(tam_m, tam_s)

    def test_normal_bank_keeps_mpr1_and_passes_method_a(self):
        f8 = self.results["f8_irq"]
        self.assertEqual(f8["mpr1"], (0xF8, 0xF8))
        self.assertEqual(f8["boundary"], contract.PASS)
        self.assertTrue(f8["irq_taken"])
        self.assertEqual(f8["side_regs"], ["WRAM01FD", "WRAM01FE", "WRAM01FF"])  # the push lands in WRAM only
        self.assertFalse(self.results["f8_irq"]["halt"]["reached"])  # no denied boundary, so no halt
        self.assertEqual(self.runs["f8_irq"]["safety"]["END"][-1]["executed"], STEPS - 1)

    def test_normal_push_values_and_return_address(self):
        # The pushed bytes are P (at 1FD, I clear = $80 from the fixture), PCL (1FE) and PCH (1FF) of after_tam.
        f8 = self.results["f8_irq"]
        after_tam = self.labels["f8_irq"]["after_tam"]
        self.assertEqual(f8["wram_new"], {PUSH_BYTES[0]: 0x80, PUSH_BYTES[1]: after_tam & 0xFF,
                                          PUSH_BYTES[2]: after_tam >> 8})
        self.assertEqual(f8["ret"]["pc"], after_tam)  # RTI returned to the pushed address

    def test_return_state_matches_fixture_constants(self):
        # Replaces the old check against the probe's own pre-TAM record. MPR2-7 (not set by the fixture) are the only
        # values still compared with the pre-TAM record.
        for case in CASES:
            if case["ret_p"] is None:
                continue
            name = case["name"]
            with self.subTest(case=name):
                r = self.results[name]
                self.assertIsNotNone(r["ret"], "the guest did not return to after_tam within the step budget")
                ret = r["ret"]
                self.assertEqual(ret["pc"], self.labels[name]["after_tam"])
                self.assertEqual(ret["a"], case["a"])
                self.assertEqual(ret["x"], FIXTURE_X)
                self.assertEqual(ret["s"], FIXTURE_S)
                self.assertEqual(ret["p"], case["ret_p"])
                self.assertEqual(ret["y"], FIXTURE_Y)
                self.assertEqual(ret["mpr"], [FIXTURE_MPR0, case["a"]] + r["before"]["mpr"][2:])  # MPR2-7: pre-TAM record

    def test_classifier_passes_normal_stack_push_only(self):
        # Synthetic: a WRAM stack push with $F8 and no denied boundary is the normal path, never UNSAFE.
        base = {"step": 3, "pc": 0xE00A, "a": 0xF8, "x": 0, "y": 0, "p": 0x0D, "s": 0xFC, "mpr": [0, 0xF8, 0, 0, 0, 0, 0, 0],
                "irr": 0x02, "idr": 0x00}
        measure = {"STEP": [dict(base, phase="before"), dict(base, phase="after", pc=0xE039)],
                   "WRW": [{"step": 3, "pc": 0xE00A, "wram": 0x1FD, "old": 0, "new": 1}]}
        verdict = judge(measure, None, {"irq1_entry": 0xE039})
        self.assertEqual(verdict["measured"], PASS)  # WRAM stack push with $F8 is the normal path, not a hole
        self.assertNotEqual(verdict["measured"], UNSAFE)


if __name__ == "__main__":
    unittest.main()
