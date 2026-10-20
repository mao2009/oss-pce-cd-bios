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


class AdResetFixture(unittest.TestCase):
    def test_positive_resets_are_model_ok(self):
        for name in ("idle_reset", "transfer_boundary", "playing_boundary"):
            c = fx.CASES[name]
            with self.subTest(name=name):
                self.assertEqual(fx.judge(c, fx.model(c)), fx.MODEL_OK)

    def test_mutated_observations_fail(self):
        c = fx.CASES["transfer_boundary"]
        good = fx.model(c)
        mutations = {
            "stuck_busy": {"busy_after": 1},
            "playing_survives": {"state_after": "PLAYING"},
            "over_budget": {"polls": fx.POLL_BUDGET + 1},
            "zero_polls": {"polls": 0},
            "bool_polls": {"polls": True},
            "abort_flag_missing": {"aborted": False},
        }
        for name, patch in mutations.items():
            with self.subTest(name=name):
                self.assertEqual(fx.judge(c, {**good, **patch}), fx.FAIL)

    def test_wrong_entry_in_observation_is_rejected(self):
        c = fx.CASES["idle_reset"]
        self.assertEqual(fx.judge(c, {**fx.model(c), "entry": 0xE00C}), fx.REJECTED)

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

    def test_no_pass_verdict_and_cli_fails_closed(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fx.main()
        out = buf.getvalue()
        self.assertEqual(code, 3)
        self.assertNotIn("PASS", out.replace("MODEL_OK", ""))
        self.assertIn("release_eligible=false", out)
        self.assertIn("abi_status=BLOCKED_ABI", out)


if __name__ == "__main__":
    unittest.main()
