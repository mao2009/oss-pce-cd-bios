"""Issue #498: MPR1 pre-check (method A) against TAM + IRQ1 on the pinned Geargrafx core. Synthetic HuCARD only.

Each case is a separate build of tests/fixtures/mpr1_tam_irq.s, run on the UNMODIFIED core by
geargrafx_mpr1_tam_irq_probe.cpp. The guest executes TAM #$02 (MPR1 = A) at tam_site; the host asserts IRQ1 before
that step where the case says so. Method A only checks MPR1 at instruction boundaries, so the question is whether
an IRQ push can happen inside the TAM's own RunInstruction, before the boundary check sees MPR1 = $FF.

The expected verdicts are the measured results on the pinned core (b49ae82). N-IRQ-TAM is EXPECTED to be
UNSAFE_AFTER_SIDE_EFFECT: it documents the hole. It is not a PASS, and it does not unblock any gate. If the core
behaviour changes, the test fails so the verdict is re-judged by hand.
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
RECORDS = ("CAP", "IRQ", "STEP", "WRW", "VDW", "HITS", "END")
STEPS = 40
IRQ1_BIT = 0x02
I_FLAG = 0x04
# Verdict names. UNSAFE_AFTER_SIDE_EFFECT: a side effect happened inside the TAM step and method A (a check at the
# next boundary) could not stop it. REJECTED_BEFORE_SIDE_EFFECT: MPR1 became a denied bank, but nothing was written
# before the boundary check, so that check stops the guest before any later side effect.
UNSAFE = "UNSAFE_AFTER_SIDE_EFFECT"
REJECTED = "REJECTED_BEFORE_SIDE_EFFECT"
PASS = "PASS"
CASES = [
    {"name": "n_irq_tam", "define": "CASE_TAM_IRQ", "irq": "at-tam", "expect": UNSAFE},
    {"name": "ff_no_irq", "define": "CASE_TAM_NOIRQ", "irq": "none", "expect": REJECTED},
    {"name": "ff_irq_masked", "define": "CASE_TAM_MASKED", "irq": "at-tam", "expect": REJECTED},
    {"name": "f8_irq", "define": "CASE_TAM_F8", "irq": "at-tam", "expect": PASS},
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


def run_probe(probe, rom, labels, irq, folder):
    label_file = folder / f"{rom.stem}.labels"
    label_file.write_text("".join(f"{name} {address:x}\n" for name, address in labels.items()))
    result = subprocess.run([str(probe), str(rom), str(label_file), str(STEPS), irq],
                            check=True, capture_output=True, text=True, timeout=120)
    records = {}
    for line in result.stdout.splitlines():  # the core prints banner lines too; only tagged JSON records count
        prefix, _, payload = line.partition(" ")
        if prefix in RECORDS:
            records.setdefault(prefix, []).append(json.loads(payload))
    if "STEP" not in records or "END" not in records or len(records["STEP"]) != 2:
        raise ValueError("probe produced no before/after STEP pair (tam_site not reached)")
    return records


def judge(records, labels):
    """Classify one case from the real-core records. Returns a dict with the measured facts and the verdicts."""
    step = {item["phase"]: item for item in records["STEP"]}
    before, after = step["before"], step["after"]
    tam_step = before["step"]
    side = [item for item in records.get("WRW", []) + records.get("VDW", []) if item["step"] == tam_step]
    irq_taken = after["pc"] == labels["irq1_entry"]
    mpr1_before, mpr1_after = before["mpr"][1], after["mpr"][1]
    changed = mpr1_before != mpr1_after
    irq_asserted = bool(before["irr"] & IRQ1_BIT)  # observed request before the TAM
    irq_pending = bool(before["irr"] & ~before["idr"] & IRQ1_BIT) and not (before["p"] & I_FLAG)
    # Method A boundary verdict: MPR1 as it stands at the next instruction boundary (after the TAM).
    boundary, boundary_detail = contract.check_stack_bank(mpr1_after)
    # The #497 contract's IRQ window verdict for the same instruction (an overlap is UNMEASURED, a hole kept open).
    contract_irq = contract.judge_irq_window(changed, irq_asserted, irq_pending)
    if changed and side and irq_taken:
        measured = UNSAFE
    elif changed and boundary == contract.FAIL and not side and not irq_taken:
        measured = REJECTED
    elif not changed and boundary == contract.PASS:
        measured = PASS
    else:
        measured = "UNEXPECTED"
    diffs = [key for key in ("pc", "a", "x", "y", "p", "s", "mpr", "irr", "idr") if before[key] != after[key]]
    return {"measured": measured, "boundary": boundary, "boundary_detail": boundary_detail,
            "contract_irq": contract_irq, "mpr1": (mpr1_before, mpr1_after), "irq_asserted": irq_asserted,
            "irq_pending": irq_pending, "irq_taken": irq_taken, "side_effects": len(side),
            "side_regs": sorted({f"{item['reg']}" if "reg" in item else f"WRAM{item['wram']:04X}" for item in side}),
            "state_diffs": diffs, "before": before, "after": after}


class Mpr1TamIrqRealCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-mpr1-tam-irq-")
        folder = Path(cls._tmp.name)
        probe = api.compile_probe(folder, source_name=PROBE_SOURCE, target_name=PROBE_TARGET)
        cls.results = {}
        for case in CASES:
            rom, labels = build_case(case["define"], folder)
            records = run_probe(probe, rom, labels, case["irq"], folder)
            cls.results[case["name"]] = judge(records, labels)
            cls.results[case["name"]]["rom_sha256"] = hashlib.sha256(rom.read_bytes()).hexdigest()
            print(f"MEASURE {case['name']}: measured={cls.results[case['name']]['measured']} "
                  f"boundary={cls.results[case['name']]['boundary']} "
                  f"contract_irq={cls.results[case['name']]['contract_irq']} "
                  f"mpr1={cls.results[case['name']]['mpr1']} side_effects={cls.results[case['name']]['side_effects']} "
                  f"regs={cls.results[case['name']]['side_regs']} diffs={cls.results[case['name']]['state_diffs']} "
                  f"pc={cls.results[case['name']]['before']['pc']:04X}->{cls.results[case['name']]['after']['pc']:04X} "
                  f"s={cls.results[case['name']]['before']['s']:02X}->{cls.results[case['name']]['after']['s']:02X} "
                  f"p={cls.results[case['name']]['before']['p']:02X}->{cls.results[case['name']]['after']['p']:02X} "
                  f"irr={cls.results[case['name']]['before']['irr']:02X} idr={cls.results[case['name']]['before']['idr']:02X}")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_case_verdicts_match_measured_core_behaviour(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                self.assertEqual(self.results[case["name"]]["measured"], case["expect"])

    def test_n_irq_tam_is_a_known_hole_not_a_pass(self):
        n = self.results["n_irq_tam"]
        self.assertEqual(n["mpr1"], (0xF8, 0xFF))
        self.assertTrue(n["irq_taken"])
        self.assertGreater(n["side_effects"], 0)
        self.assertEqual(n["contract_irq"][0], contract.UNMEASURED)  # the #497 contract keeps this window open

    def test_rejection_happens_before_any_side_effect(self):
        for name in ("ff_no_irq", "ff_irq_masked"):
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["side_effects"], 0)
                self.assertFalse(self.results[name]["irq_taken"])
                self.assertEqual(self.results[name]["boundary"], contract.FAIL)

    def test_normal_bank_keeps_mpr1_and_passes_method_a(self):
        f8 = self.results["f8_irq"]
        self.assertEqual(f8["mpr1"], (0xF8, 0xF8))
        self.assertEqual(f8["boundary"], contract.PASS)
        self.assertTrue(f8["irq_taken"])
        self.assertEqual(f8["side_regs"], ["WRAM01FD", "WRAM01FE", "WRAM01FF"])  # the push lands in WRAM only

    def test_classifier_rejects_wrong_records(self):
        # Synthetic negative: an IRQ taken without any MPR1 change and without a boundary failure must not be UNSAFE.
        base = {"step": 3, "pc": 0xE00A, "a": 0xF8, "x": 0, "y": 0, "p": 0x0D, "s": 0xFC, "mpr": [0, 0xF8, 0, 0, 0, 0, 0, 0],
                "irr": 0x02, "idr": 0x00}
        records = {"STEP": [dict(base, phase="before"), dict(base, phase="after", pc=0xE039)],
                   "WRW": [{"step": 3, "pc": 0xE00A, "wram": 0x1FD, "old": 0, "new": 1}]}
        verdict = judge(records, {"irq1_entry": 0xE039})
        self.assertEqual(verdict["measured"], PASS)  # WRAM stack push with $F8 is the normal path, not a hole
        self.assertNotEqual(verdict["measured"], UNSAFE)


if __name__ == "__main__":
    unittest.main()
