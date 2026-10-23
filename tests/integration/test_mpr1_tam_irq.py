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
RECORDS = ("CAP", "IRQ", "STEP", "RET", "WRW", "VDW", "HITS", "END")
STEPS = 40
IRQ1_BIT = 0x02
I_FLAG = 0x04
PUSH_BYTES = (0x1FD, 0x1FE, 0x1FF)  # WRAM offsets of the P / PCL / PCH stack pushes at S=$FF (page $21xx)
# Verdict names. UNSAFE_AFTER_SIDE_EFFECT: a side effect happened inside the TAM step and method A (a check at the
# next boundary) could not stop it. REJECTED_BEFORE_SIDE_EFFECT: MPR1 became a denied bank, but nothing was written
# before the boundary check, so that check stops the guest before any later side effect.
UNSAFE = "UNSAFE_AFTER_SIDE_EFFECT"
REJECTED = "REJECTED_BEFORE_SIDE_EFFECT"
PASS = "PASS"
CASES = [
    {"name": "n_irq_tam", "define": "CASE_TAM_IRQ", "irq": "at-tam", "i_flag": 0, "expect": UNSAFE},
    {"name": "ff_no_irq", "define": "CASE_TAM_NOIRQ", "irq": "none", "i_flag": 0, "expect": REJECTED},
    {"name": "ff_irq_masked", "define": "CASE_TAM_MASKED", "irq": "at-tam", "i_flag": 1, "expect": REJECTED},
    {"name": "f8_irq", "define": "CASE_TAM_F8", "irq": "at-tam", "i_flag": 0, "expect": PASS},
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
    # The first arrival at after_tam (None if the guest never got back there in the step budget).
    ret = records.get("RET", [None])[0]
    wram_new = {item["wram"]: item["new"] for item in side if "wram" in item}
    return {"measured": measured, "boundary": boundary, "boundary_detail": boundary_detail,
            "contract_irq": contract_irq, "mpr1": (mpr1_before, mpr1_after), "irq_asserted": irq_asserted,
            "irq_pending": irq_pending, "irq_taken": irq_taken, "side_effects": len(side),
            "side_regs": sorted({f"{item['reg']}" if "reg" in item else f"WRAM{item['wram']:04X}" for item in side}),
            "wram_new": wram_new, "ret": ret,
            "state_diffs": diffs, "before": before, "after": after}


class Mpr1TamIrqRealCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-mpr1-tam-irq-")
        folder = Path(cls._tmp.name)
        probe = api.compile_probe(folder, source_name=PROBE_SOURCE, target_name=PROBE_TARGET)
        cls.results = {}
        cls.labels = {}
        for case in CASES:
            rom, labels = build_case(case["define"], folder)
            records = run_probe(probe, rom, labels, case["irq"], folder)
            r = cls.results[case["name"]] = judge(records, labels)
            r["rom_sha256"] = hashlib.sha256(rom.read_bytes()).hexdigest()
            cls.labels[case["name"]] = labels
            ret = r["ret"]
            ret_text = "none" if ret is None else f"pc={ret['pc']:04X} p={ret['p']:02X} s={ret['s']:02X}"
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
                  f"ret={ret_text}")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_case_verdicts_match_measured_core_behaviour(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                self.assertEqual(self.results[case["name"]]["measured"], case["expect"])

    def test_tam_start_pending_state_is_asserted(self):
        # The precondition of each case, asserted from the "before" record (IRR/IDR/P read after the host's assert).
        for case in CASES:
            with self.subTest(case=case["name"]):
                before = self.results[case["name"]]["before"]
                pending_expected = case["irq"] == "at-tam"
                self.assertEqual(bool(before["irr"] & IRQ1_BIT), pending_expected)  # IRQ1 request latched
                self.assertEqual(bool(before["p"] & I_FLAG), bool(case["i_flag"]))  # I flag as the fixture sets it
                self.assertEqual(before["idr"] & IRQ1_BIT, 0)  # IRQ1 not disabled by IDR
                self.assertEqual(self.results[case["name"]]["irq_pending"], pending_expected and not case["i_flag"])
                self.assertEqual(before["mpr"][1], 0xF8)  # MPR1 = WRAM before the TAM in every case

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

    def test_normal_bank_keeps_mpr1_and_passes_method_a(self):
        f8 = self.results["f8_irq"]
        self.assertEqual(f8["mpr1"], (0xF8, 0xF8))
        self.assertEqual(f8["boundary"], contract.PASS)
        self.assertTrue(f8["irq_taken"])
        self.assertEqual(f8["side_regs"], ["WRAM01FD", "WRAM01FE", "WRAM01FF"])  # the push lands in WRAM only

    def test_normal_push_values_and_return_address(self):
        # The pushed bytes are the P (at 1FD), PCL (1FE) and PCH (1FF) of the return address = after_tam.
        f8 = self.results["f8_irq"]
        after_tam = self.labels["f8_irq"]["after_tam"]
        self.assertEqual(f8["wram_new"], {PUSH_BYTES[0]: f8["before"]["p"], PUSH_BYTES[1]: after_tam & 0xFF,
                                          PUSH_BYTES[2]: after_tam >> 8})
        self.assertEqual(f8["ret"]["pc"], after_tam)  # RTI returned to the pushed address

    def test_return_state_preserved_after_rti_or_tam(self):
        # Return PC and A/X/Y/P/S/MPR restored to the pre-TAM values. Only the MPR1 change is expected (the TAM).
        for name in ("ff_no_irq", "ff_irq_masked", "f8_irq"):
            with self.subTest(case=name):
                r = self.results[name]
                self.assertIsNotNone(r["ret"], "the guest did not return to after_tam within the step budget")
                ret, before = r["ret"], r["before"]
                self.assertEqual(ret["pc"], self.labels[name]["after_tam"])
                for key in ("a", "x", "y", "p", "s"):
                    self.assertEqual(ret[key], before[key], key)
                expected_mpr = list(before["mpr"])
                expected_mpr[1] = r["mpr1"][1]  # the TAM is the only change allowed to MPR
                self.assertEqual(ret["mpr"], expected_mpr)

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
