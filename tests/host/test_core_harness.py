"""Host-only judge tests for the HuC6280 core harness; synthetic records, NOT real-core evidence."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/integration"))
import geargrafx_core_harness as h  # noqa: E402

SYM = {"harness_done": 0xE910, "harness_selftest": 0xE940}
CASE = {"name": "c", "target": "harness_selftest", "a": 1, "x": 2, "y": 3, "p": 4, "expect": {"a": 9}}


def records(phases=("harness_after", "harness_done"), a_after=9):
    row = {"a": 1, "x": 2, "y": 3, "p": 4, "sp": 0xFF, "mpr": [0xFF, 0xF8, 1, 2, 3, 4, 5, 0]}
    trace = [{**row, "phase": p, "cycle": c, **({"a": a_after} if p != "harness_call" else {})}
             for c in ("cold", "warm") for p in ("harness_call", *phases)]
    pc = SYM["harness_done"] if "harness_done" in phases else SYM["harness_selftest"]
    obs = {"pc": pc, "work_ram_0200_hex": "00" * 64}
    return {"TRACE": trace, "OBS": [{"phase": p, **obs} for p in ("loaded", "cold", "nonreturning", "warm")]}


class JudgeTest(unittest.TestCase):
    def test_statuses(self):
        for phases, verdict, status in ((("harness_after", "harness_done"), "PASS", "RETURNED"),
                                        ((), "FAIL", "TIMEOUT"), (("harness_trap",), "FAIL", "TRAPPED")):
            got = h.judge(records(phases), CASE, SYM)
            self.assertEqual((got[0], set(got[1].values())), (verdict, {status}))

    def test_late_or_partial_return_is_timeout(self):
        late = records()
        for row in late["OBS"]:
            row["pc"] = SYM["harness_selftest"]
        for rec in (late, records(("harness_after",))):
            self.assertEqual(set(h.judge(rec, CASE, SYM)[1].values()), {"TIMEOUT"})

    def test_wrong_result_or_incomplete_output_fails(self):
        self.assertEqual(h.judge(records(a_after=8), CASE, SYM)[0], "FAIL")
        self.assertEqual(h.judge({"TRACE": [], "OBS": []}, CASE, SYM)[0], "FAIL")

    def test_plugin_case_cannot_accept_a_failing_verdict(self):
        # A plug-in "want" of FAIL/TIMEOUT/TRAPPED would let a broken routine exit 0.
        for want in (["FAIL", "RETURNED"], ["FAIL", "TIMEOUT"], ["FAIL", "TRAPPED"], ["PASS", "TIMEOUT"],
                     ["PASS", "RETURNED"]):
            with self.assertRaises(ValueError):
                h.validate({**CASE, "want": want}, SYM, plugin=True)
        h.validate(CASE, SYM, plugin=True)
        h.validate({**CASE, "want": ["FAIL", "RETURNED"]}, SYM, plugin=False)  # built-in self-tests may

    def test_plugin_expect_needs_a_checked_field(self):
        # {} would PASS on any RETURNED routine with an unchanged ZP window: wrong A/X/Y/P undetected.
        for expect in ({}, {"ram": {}}):
            with self.assertRaises(ValueError):
                h.validate({**CASE, "expect": expect}, SYM, plugin=True)
        for expect in ({"a": 9}, {"x": 0}, {"y": 0}, {"p": 0, "p_mask": 1}, {"ram": {"0": 1}}):
            h.validate({**CASE, "expect": expect}, SYM, plugin=True)
        h.validate({**CASE, "expect": {}}, SYM, plugin=False)  # self-tests with an empty expect stay valid

    def test_non_dict_plugin_case_is_value_error(self):
        for bad in ("abc", ["x"], 5, None):
            with self.assertRaises(ValueError):
                h.validate(bad, SYM, plugin=True)

    def test_timeout_message_names_the_budget(self):
        failures = h.judge(records(()), CASE, SYM)[2]
        self.assertTrue(failures and all("budget" in f for f in failures), failures)

    def test_zero_p_mask_is_rejected(self):
        with self.assertRaises(ValueError):
            h.validate({**CASE, "expect": {"p": 0, "p_mask": 0}}, SYM)

    def test_p_mask_on_b_t_bits_is_rejected(self):
        # judge() ignores B/T as inputs, so a mask on 0x30 could compare nothing and still PASS.
        for mask in (0x10, 0x20, 0x30, 0x31):
            with self.assertRaises(ValueError):
                h.validate({**CASE, "expect": {"p": 0, "p_mask": mask}}, SYM, plugin=True)
        h.validate({**CASE, "expect": {"p": 0, "p_mask": 0x0F}}, SYM, plugin=True)

    def test_duplicate_ram_offsets_are_rejected(self):
        # "0" and "0x0" name the same byte; the later one silently won before.
        with self.assertRaises(ValueError):
            h.validate({**CASE, "expect": {"ram": {"0": 1, "0x0": 2}}}, SYM, plugin=True)

    def test_invalid_inputs_rejected(self):
        for bad in ({"p": 0x24}, {"p": 0}, {"a": 256}, {"a": True}, {"target": "nope"}, {"ram": "00"},
                    {"carry": 1}, {"expect": {"p": 1}}, {"expect": {"A": 1}}, {"expect": {"ram": {"32": 1}}},
                    {"expect": {"ram": {"-1": 1}}}, {"expect": {"ram": {"0": 256}}}, {"ram": 5}):
            with self.assertRaises(ValueError):
                h.validate({**CASE, **bad}, SYM)


if __name__ == "__main__":
    unittest.main()
