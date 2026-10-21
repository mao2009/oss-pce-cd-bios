"""Host gate for the slot-$09 (CD_STAT, candidate $E01B) synthetic fixture (Issue #230).

Synthetic only: the case list is author-defined. No emulator, no ROM, no ./rom/ access, no reference callee JSR.
The gate below refuses unsafe or unsourced declarations. For the committed fixture it can only return BLOCKED:
callee ABI (mode, return, flags, MPR, IRQ, work RAM, no-media, busy, invalid mode) is UNKNOWN in
docs/research/cd-stat-contract.md, so no case may be expected to PASS. Passing these tests is not evidence of
System Card behaviour and does not change spec/dev_implementations.json.
"""
import copy
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "cdstat-slot09-cases.json"
DEV_LEDGER = ROOT / "spec" / "dev_implementations.json"
FORBIDDEN_WORDS = ("PASS", "VERIFIED", "OBSERVED", "DEV_CALLABLE")
REQUIRED_CASE_IDS = {"caller_busy_path", "caller_ready_path", "no_media", "invalid_mode"}
TOP_KEYS = {"schema", "issue", "slot", "entry_candidate", "candidate_name", "abi_status", "release_eligible",
            "execution", "reference_jsr_count", "reference_rom_opened", "provenance", "callee_abi",
            "declaration", "cases"}
CASE_KEYS = {"id", "mode_a", "medium", "expected", "basis"}
CALLEE_KEYS = {"mode_input", "return_a", "return_xy", "flags", "stack_and_sp", "mpr", "irq", "work_ram",
               "no_media", "busy", "invalid_mode"}
MODE_A_VALUES = {"0x00", "0x01", "0x02"}  # F02, F03 (caller-sourced) and 0x02 (invalid-mode probe, F25)
BLOCKED, REFUSED = "BLOCKED", "REFUSED"


def load(path=FIXTURE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def gate(data):
    """Return (verdict, reasons). REFUSED on any unsafe or unsourced field; BLOCKED when only ABI is open."""
    reasons = []
    if set(data) != TOP_KEYS:
        reasons.append("top-level keys must match the schema exactly")
    if data.get("abi_status") != "BLOCKED_ABI":
        reasons.append("abi_status must stay BLOCKED_ABI")
    if data.get("release_eligible") is not False:
        reasons.append("release_eligible must be false")
    if data.get("execution") != "NOT_RUN":
        reasons.append("execution must be NOT_RUN")
    if data.get("reference_jsr_count") != 0 or type(data.get("reference_jsr_count")) is not int:
        reasons.append("reference JSR count must be integer 0")
    if data.get("reference_rom_opened") is not False:
        reasons.append("reference ROM must not be opened")
    decl = data.get("declaration", {})
    if decl.get("entry") != "0xE01B":
        reasons.append("entry must be 0xE01B")
    if decl.get("approval") is not None:
        reasons.append("DEV approval must stay null (not approved by this fixture)")
    if decl.get("rom_sha256") is not None:
        reasons.append("declared ROM hash is not ROM verification; must be null")
    callee = data.get("callee_abi", {})
    if set(callee) != CALLEE_KEYS:
        reasons.append("callee_abi keys must be exactly the known set")
    for key, value in callee.items():
        if value != "UNKNOWN":
            reasons.append(f"callee_abi.{key} must stay UNKNOWN without a source")
    cases = data.get("cases", [])
    ids = [c.get("id") for c in cases]
    if len(ids) != len(set(ids)) or not REQUIRED_CASE_IDS <= set(ids):
        reasons.append("cases must be unique and include busy, ready, no-media and invalid-mode")
    for c in cases:
        if set(c) != CASE_KEYS:
            reasons.append(f"case {c.get('id')} has unknown or missing fields")
        if c.get("expected") != "UNKNOWN":
            reasons.append(f"case {c.get('id')} must expect UNKNOWN, not a pass")
        if c.get("mode_a") not in MODE_A_VALUES:
            reasons.append(f"case {c.get('id')} mode_a must be one of the caller-sourced or probe values")
    text = json.dumps(data, ensure_ascii=False).upper()
    for word in FORBIDDEN_WORDS:
        if word in text:
            reasons.append(f"forbidden word {word} present")
    return (REFUSED if reasons else BLOCKED), reasons


def _mutants():
    def m(f):
        def apply(d):
            f(d)
            return d
        return apply

    # name -> (mutation, substring the refusal reason must contain)
    return {
        "wrong_entry": (m(lambda d: d["declaration"].update(entry="0xE01C")), "entry must be"),
        "jsr_called": (m(lambda d: d.update(reference_jsr_count=1)), "JSR count"),
        "jsr_as_bool": (m(lambda d: d.update(reference_jsr_count=False)), "JSR count"),
        "rom_opened": (m(lambda d: d.update(reference_rom_opened=True)), "ROM must not"),
        "approval_set": (m(lambda d: d["declaration"].update(approval={"approved_by": "x", "review_ref": "y"})),
                         "approval must stay null"),
        "release_eligible": (m(lambda d: d.update(release_eligible=True)), "release_eligible"),
        "declared_rom_hash": (m(lambda d: d["declaration"].update(rom_sha256="0" * 64)), "ROM hash"),
        "sourced_return": (m(lambda d: d["callee_abi"].update(return_a="0x00")), "callee_abi.return_a"),
        "pass_expectation": (m(lambda d: d["cases"][0].update(expected="PASS")), "must expect UNKNOWN"),
        "missing_no_media": (m(lambda d: d.update(cases=[c for c in d["cases"] if c["id"] != "no_media"])),
                             "unique and include"),
        "duplicate_case": (m(lambda d: d["cases"].append(dict(d["cases"][0]))), "unique and include"),
        "abi_resolved": (m(lambda d: d.update(abi_status="RESOLVED")), "abi_status must stay"),
        "extra_case_field": (m(lambda d: d["cases"][0].update(expected_a="0x00")), "unknown or missing fields"),
        "lowercase_verified": (m(lambda d: d["cases"][0].update(basis="verified by run")), "VERIFIED"),
        "callee_key_removed": (m(lambda d: d["callee_abi"].pop("return_a")), "known set"),
        "mode_out_of_range": (m(lambda d: d["cases"][0].update(mode_a="0x7F")), "mode_a must be"),
    }


class CdStatSlot09Gate(unittest.TestCase):
    def test_committed_fixture_is_blocked_not_refused(self):
        verdict, reasons = gate(load())
        self.assertEqual((verdict, reasons), (BLOCKED, []))

    def test_unsafe_declarations_are_refused(self):
        base = load()
        for name, (mutate, needle) in _mutants().items():
            with self.subTest(mutant=name):
                verdict, reasons = gate(mutate(copy.deepcopy(base)))
                self.assertEqual(verdict, REFUSED)
                self.assertTrue(any(needle in r for r in reasons), reasons)

    def test_every_case_expects_unknown_and_zero_jsr(self):
        data = load()
        self.assertEqual(data["reference_jsr_count"], 0)
        self.assertTrue(REQUIRED_CASE_IDS <= {c["id"] for c in data["cases"]})
        for c in data["cases"]:
            self.assertEqual(c["expected"], "UNKNOWN", c["id"])

    def test_dev_ledger_slot9_is_unchanged(self):
        ledger = json.loads(DEV_LEDGER.read_text(encoding="utf-8"))
        slot = next(s for s in ledger["slots"] if s["id"] == 9)
        self.assertEqual(slot["candidate_name"], "CD_STAT")
        self.assertEqual(slot["state"], "NOT_READY")
        self.assertIsNone(slot["implementation"])
        self.assertEqual(slot["approval"], {"approved_by": None, "review_ref": None})


if __name__ == "__main__":
    unittest.main()
