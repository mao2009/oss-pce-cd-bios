"""Synthetic: the three reference BIOS entry points refuse unless the JSR gate returns ALLOWED.

No BIOS, no ROM and no emulator is touched. The bios_path used here does not exist, so every
refusal must come from the gate before that path is opened, never from a FileNotFound.
"""
import copy
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import reference_jsr_gate as G
import reference_bios_runner as runner
import geargrafx_reference_adapter as adapter
from reference_case_execute import execute_case
from reference_bios_runner import Blocked

MISSING_BIOS = "rom/does-not-exist-synthetic.pce"
SLOT_1E_REASON = "NO_REVIEWED_GATE_RECORD"  # the committed ledger has no record for slot $1E
GATE_REFUSAL = "BLOCKED: reference JSR gate refused slot-1e"  # prefix of G.require_allowed's refusal


class _TrackedBios:
    """bios_path stand-in: counts any attempt to open or resolve it (os.fspath calls __fspath__)."""

    def __init__(self):
        self.opened = 0

    def __fspath__(self):
        self.opened += 1
        raise AssertionError("bios_path was opened")


class GateEnforcementTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "schema": "oss-pce-cd-bios/reference-bios-run-config/v1",
            "api_id": "slot-1e", "bios_path": MISSING_BIOS, "bios_sha256": "a" * 64,
            "test_rom_path": "test.pce", "test_rom_sha256": "b" * 64,
            "emulator_revision": "pinned-revision", "max_instructions": 100,
            "ram_write_allowlist": [{"start": 0x2000, "end": 0x2100}],
            "isolated_run": True, "reviewed_abi": True, "approved_fixture": True,
        }
        self.case = {
            "id": "slot-1e/candidate_nominal", "api_id": "slot-1e",
            "reviewed_abi": True, "measurement_status": "NOT_MEASURED",
            "expected": None, "timeout_instructions": 100,
            "input_fixture": {"mode": "synthetic_guest_jsr", "initial_a": 0xAA,
                              "initial_x": 0x55, "initial_y": 0xCC, "initial_sp": 255,
                              "expected_entry": "0xE05A"},
        }
        # An existing file, so the adapter-presence check passes and the gate is what refuses.
        self.adapter = str(Path(__file__))
        self.jsr_cfg = {"api_id": "slot-1e", "max_instructions": 100,
                        "ram_write_allowlist": [{"start": 0x2000, "end": 0x4000}],
                        "bios_path": MISSING_BIOS}

    def test_run_refuses_with_gate_reason_not_file_error(self):
        for api_id, reason in (("slot-1e", SLOT_1E_REASON), ("slot-01", "LEDGER_H4_UNCONTROLLED")):
            with self.subTest(api_id=api_id):
                with self.assertRaises(Blocked) as ctx:
                    runner.run(dict(self.cfg, api_id=api_id), self.adapter)
                self.assertIn(reason, str(ctx.exception))
                self.assertNotIn("file missing", str(ctx.exception))

    def test_execute_case_refuses_with_gate_reason(self):
        with self.assertRaises(Blocked) as ctx:
            execute_case(self.case, self.cfg, self.adapter)
        self.assertIn(SLOT_1E_REASON, str(ctx.exception))

    def _no_probe(self):
        # Any reach of probe compilation or spawning fails the test with "probe reached".
        stack = ExitStack()
        for owner, name in ((adapter, "compile_probe"), (adapter.subprocess, "run")):
            stack.enter_context(mock.patch.object(owner, name, side_effect=AssertionError("probe reached")))
        return stack

    def test_observe_jsr_refuses_with_gate_reason(self):
        cfg = dict(self.jsr_cfg, bios_path=_TrackedBios())
        with self._no_probe(), self.assertRaises(G.GateRefused) as ctx:
            adapter.observe_jsr(cfg)
        self.assertTrue(str(ctx.exception).startswith(GATE_REFUSAL))
        self.assertIn(SLOT_1E_REASON, str(ctx.exception))
        self.assertEqual(cfg["bios_path"].opened, 0)

    def test_caller_flags_cannot_bypass(self):
        for flag in ({"allow": True}, {"execution": "ALLOWED"}, {"gate_allowed": True}):
            with self.subTest(flag=flag), self._no_probe():
                with self.assertRaisesRegex(G.GateRefused, GATE_REFUSAL):
                    adapter.observe_jsr(dict(self.jsr_cfg, **flag))

    def test_observe_reset_only_refused_by_gate_before_bios_access(self):
        cfg = {"api_id": "reference-reset-only", "max_instructions": 100,
               "bios_path": _TrackedBios()}
        with self._no_probe(), self.assertRaises(G.GateRefused) as ctx:
            adapter.observe(cfg)
        self.assertIn("GATE_TARGET_UNMAPPED", str(ctx.exception))
        self.assertEqual(cfg["bios_path"].opened, 0)
        for call in (lambda: runner.run(self.cfg, self.adapter, allow=True),
                     lambda: execute_case(self.case, self.cfg, self.adapter, allow=True),
                     lambda: G.require_allowed("slot-1e", allow=True)):
            with self.assertRaises(TypeError):
                call()

    def test_missing_gate_record_refuses(self):
        ledger, contracts = G.load()
        ledger = copy.deepcopy(ledger)
        del ledger["slots"]["1"]
        with mock.patch.object(G, "load", return_value=(ledger, contracts)):
            with self.assertRaises(G.GateRefused) as ctx:
                G.require_allowed("slot-01")
        self.assertIn("NO_REVIEWED_GATE_RECORD", str(ctx.exception))

    def test_unmapped_target_refuses(self):
        for api_id in ("slot-ff", "slot-1E", "reference-reset-only", None, "slot-1e ", 30):
            with self.subTest(api_id=api_id), self.assertRaisesRegex(G.GateRefused, "GATE_TARGET_UNMAPPED"):
                G.require_allowed(api_id)

    def test_refusal_precedes_any_bios_path_access(self):
        # Any access to bios_path would hash or spawn the adapter; neither may be reached.
        with mock.patch.object(runner, "sha", side_effect=AssertionError("bios hashed")), \
                self._no_probe():
            with self.assertRaisesRegex(Blocked, SLOT_1E_REASON):
                runner.run(self.cfg, self.adapter)
            with self.assertRaisesRegex(G.GateRefused, GATE_REFUSAL):
                adapter.observe_jsr(self.jsr_cfg)
            with self.assertRaisesRegex(Blocked, SLOT_1E_REASON):
                execute_case(self.case, self.cfg, self.adapter)


if __name__ == "__main__":
    unittest.main()
