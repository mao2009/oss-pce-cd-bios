"""Positive/negative host tests for the AD_RESET (slot $10) synthetic fixture (Issue #293).

Synthetic only: the model and judge in tools/adreset_fixture.py. No emulator, no ROM, no reference
JSR. Well-formed reset cases are MODEL_OK at best; mutated observations (stuck busy, playback that
survives reset, over-budget polls, wrong abort flag) must be FAIL; out-of-scope or unauthorized
requests must be REJECTED; malformed input must be BLOCKED. Nothing here is a callee PASS.
"""
import contextlib
import importlib.util
import io
import pathlib
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "adreset_fixture.py"
spec = importlib.util.spec_from_file_location("adreset_fixture", TOOL)
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


# Hand-written expected observations, independent of tools/adreset_fixture.py::model().
REFERENCE_OBS = {
    "idle_reset": {"entry": 0xE030, "state_after": "IDLE", "busy_after": 0, "polls": 1, "aborted": False},
    "transfer_boundary": {"entry": 0xE030, "state_after": "IDLE", "busy_after": 0, "polls": 1, "aborted": True},
    "playing_boundary": {"entry": 0xE030, "state_after": "IDLE", "busy_after": 0, "polls": 1, "aborted": True},
}


class AdResetFixture(unittest.TestCase):
    def test_positive_resets_are_model_ok(self):
        for name in ("idle_reset", "transfer_boundary", "playing_boundary"):
            c = fx.CASES[name]
            with self.subTest(name=name):
                # Self-consistency of the synthetic model only; the REFERENCE_OBS line is the independent check.
                self.assertEqual(fx.judge(c, fx.model(c)), fx.MODEL_OK)
                self.assertEqual(fx.judge(c, REFERENCE_OBS[name]), fx.MODEL_OK)

    def test_mutated_observations_fail(self):
        c = fx.CASES["transfer_boundary"]
        good = REFERENCE_OBS["transfer_boundary"]
        mutations = {
            "stuck_busy": {"busy_after": 1},
            "playing_survives": {"state_after": "PLAYING"},
            "over_budget": {"polls": fx.POLL_BUDGET + 1},
            "zero_polls": {"polls": 0},
            "abort_flag_missing": {"aborted": False},
            "wrong_entry_observed": {"entry": 0xE00C},
        }
        for name, patch in mutations.items():
            with self.subTest(name=name):
                self.assertEqual(fx.judge(c, {**good, **patch}), fx.FAIL)

    def test_mistyped_observations_are_blocked(self):
        # Type mismatches are malformed input (BLOCKED), not a model/observation disagreement (FAIL).
        c = fx.CASES["transfer_boundary"]
        good = REFERENCE_OBS["transfer_boundary"]
        mistyped = {
            "bool_polls": {"polls": True},
            "float_polls": {"polls": 1.0},
            "none_polls": {"polls": None},
            "str_polls": {"polls": "1"},
            "bool_busy": {"busy_after": False},
            "float_busy": {"busy_after": 0.0},
            "none_busy": {"busy_after": None},
            "int_aborted": {"aborted": 1},
            "none_aborted": {"aborted": None},
            "none_state_after": {"state_after": None},
            "float_entry": {"entry": 57392.0},
        }
        for name, patch in mistyped.items():
            with self.subTest(name=name):
                self.assertEqual(fx.judge(c, {**good, **patch}), fx.BLOCKED)

    def test_wrong_entry_in_observation_is_fail(self):
        c = fx.CASES["idle_reset"]
        self.assertEqual(fx.judge(c, {**fx.model(c), "entry": 0xE00C}), fx.FAIL)

    def test_float_or_bool_case_entry_is_blocked(self):
        for entry in (57392.0, True):
            with self.subTest(entry=entry):
                c = {**fx.CASES["idle_reset"], "entry": entry}
                self.assertEqual(fx.judge(c, fx.model(c)), fx.BLOCKED)

    def test_non_bool_forbidden_flags_are_blocked(self):
        base = fx.CASES["idle_reset"]
        for flag in fx.FORBIDDEN_FLAGS:
            for value in (0, 1, "false", "true", None):
                with self.subTest(flag=flag, value=value):
                    c = {**base, flag: value}
                    self.assertEqual(fx.judge(c, fx.model(c)), fx.BLOCKED)

    def test_missing_or_non_string_op_is_blocked(self):
        base = fx.CASES["idle_reset"]
        for name, c in {
            "op_missing": {k: v for k, v in base.items() if k != "op"},
            "op_none": {**base, "op": None},
            "op_int": {**base, "op": 5},
        }.items():
            with self.subTest(name=name):
                self.assertEqual(fx.judge(c, fx.model(c)), fx.BLOCKED)

    def test_out_of_scope_and_unauthorized_are_rejected(self):
        # Even with an otherwise well-formed observation, these requests must never pass.
        base = fx.CASES["idle_reset"]
        requests = {
            "other_slot": {**base, "entry": 0xE00C},
            "other_op": {**base, "op": "seek"},
            "execution": {**base, "execution_allowed": True},
            "side_effects": {**base, "side_effects_allowed": True},
            "unsafe_io": {**base, "unsafe_io_allow": True},
            "real_bios_jsr": {**base, "real_bios_jsr": True},
        }
        for name, c in requests.items():
            with self.subTest(name=name):
                self.assertEqual(fx.judge(c, fx.model(c)), fx.REJECTED)

    def test_malformed_or_missing_input_is_blocked(self):
        self.assertEqual(fx.judge(fx.CASES["malformed_state"], fx.model(fx.CASES["malformed_state"])), fx.BLOCKED)
        self.assertEqual(fx.judge(fx.CASES["idle_reset"], None), fx.BLOCKED)

    def test_non_mapping_or_incomplete_case_is_blocked_without_exception(self):
        # Real callers run judge(c, model(c)); neither step may raise on malformed case records.
        for c in (None, [], {"entry": fx.ENTRY_CANDIDATE, "op": "reset"}, {**fx.CASES["idle_reset"], "state": None}):
            with self.subTest(case=c):
                self.assertEqual(fx.judge(c, fx.model(c)), fx.BLOCKED)

    def test_observation_without_entry_field_is_blocked(self):
        c = fx.CASES["idle_reset"]
        obs = {k: v for k, v in REFERENCE_OBS["idle_reset"].items() if k != "entry"}
        self.assertEqual(fx.judge(c, obs), fx.BLOCKED)
        self.assertEqual(fx.judge(c, {}), fx.BLOCKED)

    def test_bool_is_not_accepted_as_busy_or_abort_flag(self):
        c = fx.CASES["idle_reset"]
        good = REFERENCE_OBS["idle_reset"]
        self.assertEqual(fx.judge(c, {**good, "busy_after": False}), fx.BLOCKED)
        self.assertEqual(fx.judge(c, {**good, "aborted": 0}), fx.BLOCKED)

    def test_no_pass_verdict_and_cli_fails_closed(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fx.main()
        out = buf.getvalue()
        self.assertEqual(code, 3)
        lines = out.splitlines()
        self.assertEqual(lines[0], "[synthetic model, not a callee result]")
        self.assertTrue(all(line.startswith(("idle", "transfer", "playing", "out_of", "unauth", "unsafe", "malformed"))
                            for line in lines[1:]))
        self.assertNotIn("PASS", out.replace("MODEL_OK", ""))
        self.assertIn("release_eligible=false", out)
        self.assertIn("abi_status=BLOCKED_ABI", out)


if __name__ == "__main__":
    unittest.main()
