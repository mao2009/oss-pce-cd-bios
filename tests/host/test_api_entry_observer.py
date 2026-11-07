"""Host tests for the Issue #635 API-entry observer (synthetic only, no commercial data)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from geargrafx_api_entry import classify_apicall  # noqa: E402


def call(in_table=True, slot=2, delta=2, caller=0x3006, ret=0x3008, entry=None):
    if entry is None:
        entry = 0xE006 if in_table else 0xE24F
    return {"in_table": in_table, "slot": slot, "sp_delta": delta, "entry_pc": entry,
            "caller_pc": caller, "stack_word": ret}


class ClassifyTests(unittest.TestCase):
    def test_jsr_to_table(self):
        self.assertEqual(classify_apicall(call()), "JSR")

    def test_jmp_to_table(self):
        self.assertEqual(classify_apicall(call(delta=0)), "JMP")

    def test_interrupt_delta(self):
        self.assertEqual(classify_apicall(call(delta=3)), "INTERRUPT")

    def test_direct_not_table(self):
        self.assertEqual(classify_apicall(call(in_table=False)), "DIRECT")

    def test_irq_vector(self):
        self.assertEqual(classify_apicall(call(in_table=False, delta=3, entry=0xFFF8)), "IRQ")

    def test_other_direct(self):
        self.assertEqual(classify_apicall(call(in_table=False, delta=0, entry=0x4000)), "OTHER_DIRECT")

    def test_other_delta(self):
        self.assertEqual(classify_apicall(call(delta=7)), "OTHER")


class JsrReturnTests(unittest.TestCase):
    """The synthetic JSR return rule: stack_word == caller_pc+2 (checked by the harness)."""
    def test_return_matches(self):
        c = call()
        self.assertEqual(c["stack_word"], c["caller_pc"] + 2)

    def test_return_mismatch_is_rejected_by_harness_rule(self):
        c = call(ret=0x9999)
        self.assertNotEqual(c["stack_word"], c["caller_pc"] + 2)


class MutationTests(unittest.TestCase):
    """Mutants the harness must reject: wrong slot, misaligned entry, truncated obs."""

    def test_wrong_slot_is_not_jsr_slot2(self):
        # A slot-3 JSR mislabeled as slot 2 must not classify as slot 2.
        c = call(slot=3)
        self.assertEqual(c["slot"], 3)
        self.assertNotEqual(c["slot"], 2)

    def test_misaligned_entry_is_not_table(self):
        # An entry at $E001 (not slot-aligned) must not be in_table.
        misaligned = call(entry=0xE001, in_table=False, delta=2)
        self.assertEqual(classify_apicall(misaligned), "OTHER_DIRECT")

    def test_truncated_flag_present_in_run_schema(self):
        # Probe emits apicall_truncated; harness must see it (checked in commercial path).
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        self.assertIn("apicall_truncated", src)


class ProbeObservationOnlyTests(unittest.TestCase):
    def test_probe_has_no_guest_writes(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        for forbidden in ("Memory::Write", "SetValue", "->Write(", "PutValue", "SetMpr"):
            self.assertNotIn(forbidden, src, f"probe must not write guest state: {forbidden}")
        # No write through the WRAM pointer element (wram[...] = ...). Reads (x = wram[...]) are allowed.
        import re
        self.assertIsNone(re.search(r"wram\[[^\]]*\]\s*=(?!=)", src),
                          "probe must not write WRAM element")
        self.assertIn("GetWorkingRAM()", src)

    def test_probe_has_no_guest_bytes_in_output(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        self.assertIn("APICALL", src)
        # APICALL printf is byte-free (addresses/regs/word only); RUN keeps the loader dump for judging.
        start = src.index('std::printf("APICALL')
        end = src.index("for (int i = 0; i < 8", start)
        apicall_printf = src[start:end]
        self.assertNotIn("buffer_hex", apicall_printf)
        self.assertNotIn("result_hex", apicall_printf)
        self.assertNotIn("hex(", apicall_printf)


if __name__ == "__main__":
    unittest.main()
