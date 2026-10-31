"""Host-only checks for DEV internal-boot evidence classification and no-media rejection."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
import geargrafx_dev_boot_internal as boot  # noqa: E402


def no_media_records(symbols):
    buffer = bytearray(boot.BUFFER_BASE + 6144 - boot.BUFFER_BASE)
    buffer[boot.MS_OFF:boot.MS_OFF + 7] = bytes((boot.MS_NO_DATA, boot.MS_STAGE_DONE, 0, 0, 0, 0, 0))

    def run(cycle):
        return {"cycle": cycle, "reason": "dev_media_halt", "pc": symbols["dev_media_halt"],
                "halt_confirmed": True, "halt_confirmation_steps": 64, "halt_confirmation_required": 64,
                "halted_after_step": 100, "step": 164, "buffer_hex": buffer.hex()}

    def hits(cycle):
        return [{"cycle": cycle, "name": name} for name in
                ("dev_boot_entry", "cd_media_state", "ms_done", "dev_media_halt")]

    return {"CAP": [{"cdrom_hardware": True, "cdrom_media": False, "disc_given": False}],
            "RUN": [run("cold"), run("warm")], "HIT": hits("cold") + hits("warm"),
            "PHASE": []}


class DevBootInternalEvidenceTests(unittest.TestCase):
    symbols = {"dev_media_halt": 0xE123}

    def test_no_media_is_blocked_in_json_and_not_counted_as_pass(self):
        no_media = boot.no_media_result(no_media_records(self.symbols), self.symbols)
        results = {"payload_from_lba5": {"status": "PASS"}, "no_media_stops_at_media_check": no_media,
                   "transfer_byte_tampered": {"status": "PASS_EXPECTED_FAILURE"}}
        evidence = boot.evidence_record({"rom_sha256": "r", "payload_sha256": "p"}, b"payload", results,
                                        {"RUN": [], "HIT": [], "PHASE": []})
        encoded = json.dumps(evidence, sort_keys=True)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["tests"]["no_media_stops_at_media_check"]["status"], "BLOCKED")
        self.assertEqual(boot.passed_scenarios(decoded["tests"]), 1)

    def test_no_media_judge_requires_rejection_integrity_for_cold_and_warm(self):
        records = no_media_records(self.symbols)
        boot.judge_no_media(records, self.symbols)
        mutations = boot.no_media_mutations(records, self.symbols)
        self.assertEqual(set(mutations), {
            "cold_reason_budget", "cold_state_unrecognised", "cold_data_in", "cold_payload_path_label",
            "warm_reason_budget", "warm_state_unrecognised", "warm_data_in", "warm_payload_path_label",
        })
        for index, cycle in enumerate(("cold", "warm")):
            broken = copy.deepcopy(records)
            broken["RUN"][index]["reason"] = "cd_halt_ok"
            with self.subTest(cycle=cycle), self.assertRaises(ValueError):
                boot.judge_no_media(broken, self.symbols)


if __name__ == "__main__":
    unittest.main()
