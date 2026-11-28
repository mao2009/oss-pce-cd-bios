"""Issue #38: $1C BM_DELETE is still the boot blocker on the independent commercial-CD path.

Synthetic real-core proof on the pinned Geargrafx core that the DEV jump-table entry $E054 (slot $1C)
is an unimplemented stub that halts fail-closed: a synthetic IPL payload that JSRs the real $E054 table
entry traps at api_unimplemented with X == $1C, the post-call marker is NOT written (no return), and
cold/warm agree. Companion ledger guards pin the BLOCKED_ABI state (no slot_1C.s override, NOT_READY,
mapping HYPOTHESIS, contract UNKNOWN, no JSR gate entry). A bare-RTS forward step would write the marker
and reach cd_halt_ok; this test requires the halt, so it cannot be confused with real-function progress.

NOT a compatible System Card BIOS. NOT genuine ABI evidence. NOT DEV_CALLABLE.
release_eligible=false. G5_OBSERVED=false. Synthetic media only; commercial media: NOT_RUN here
(no ./rom probing in this file).
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests" / "integration")]

SLOT = 0x1C
ENTRY = 0xE000 + SLOT * 3
PAYLOAD_SRC = Path(__file__).resolve().parent / "ipl_api_call_payload.s"
PRES_OFFSET = 0x2F80 - 0x2800  # payload marker offset inside the probe dump ($2800-$3FFF)
MARKER = 0xAB
BUDGET = 20_000_000


class BmDeleteBlockerSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch
        import build_rom
        from build_dev_ipl_loader_bios import build_ipl_loader_bios, parse_labels
        from environment import executable, dependency_dir
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-1c-blocker-")
        cls.out = Path(cls._tmp.name)
        dev_out = cls.out / "dev"
        with patch("sys.argv", ["build_rom.py", "--mode", "debug",
                                "--output", str(dev_out)]):
            assert build_rom.main() == 0
        original = dev_out / "dev-only-not-compatible-syscard3.pce"
        assert original.is_file()
        cls.normal = original.read_bytes()
        import geargrafx_ipl_loader as L
        cls.bios, _ = build_ipl_loader_bios(original, cls.out, L.SEL, "debug", "main")
        cls.bios_sha256 = hashlib.sha256(cls.bios.read_bytes()).hexdigest()
        cls.stub = parse_labels(cls.out / "ipl-loader-bios-main.lbl",
                                ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        for needed in ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"):
            assert needed in cls.stub, f"BLOCKED: label {needed} missing from the DEV image"
        cls.probe = compile_probe(cls.out, "geargrafx_cdread_probe.cpp", "geargrafx-1c-blocker-probe")
        # Synthetic one-sector payload: JSR $E054, then marker + halt loop.
        obj, rom, lbl, cfg = (cls.out / "bm_delete_call.o", cls.out / "bm_delete_call.bin",
                              cls.out / "bm_delete_call.lbl", cls.out / "bm_delete_call.cfg")
        cfg.write_text("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
                       "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n", encoding="ascii")
        subprocess.run([executable("ca65"), str(PAYLOAD_SRC), "-D", f"API_ENTRY=${ENTRY:04X}",
                        "-o", str(obj), "-g"], check=True)
        subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(rom), "-Ln", str(lbl),
                        str(obj)], check=True)
        code = rom.read_bytes()
        from build_cdload_exec import parse_labels as parse_payload_labels
        payload_labels = parse_payload_labels(lbl)
        assert payload_labels.get("payload_entry") == 0x3000
        assert 1 <= len(code) <= 2048
        cls.code, cls.halt = code, payload_labels["payload_halt"]
        body = L.body_for(code, 1)
        info = L.info_sector(2, 1, 0x3000, 0x3000, L.MPRS_STD, 0, sum(body) & 0xFFFF)
        cls.cue = L.make_disc(cls.out / "bm_delete_disc", L.case_disc({"n": 1, "iplblk": 2}, body, info))
        cls.cue_sha256 = hashlib.sha256((cls.out / "bm_delete_disc" / "disc.cue").read_bytes()
                                        if (cls.out / "bm_delete_disc" / "disc.cue").exists()
                                        else b"cue-missing").hexdigest()
        labels = cls.out / "bm_delete.labels"
        lines = (f"cd_halt_ok {cls.halt:04x}\n"
                 f"cd_halt_fail {cls.stub['cd_halt_fail']:04x}\n"
                 f"cd_unexpected_interrupt {cls.stub['cd_unexpected_interrupt']:04x}\n"
                 f"api_unimplemented {cls.stub['api_unimplemented']:04x}\n")
        labels.write_text(lines, encoding="utf-8")
        from geargrafx_cdread import execute
        records = execute(cls.probe, cls.bios, str(cls.cue), labels, BUDGET, None)
        if len(records["RUN"]) != 2:
            raise ValueError("probe did not complete cold and warm runs")
        cls.runs = records["RUN"]
        cls.rom_sha256_probe = records.get("rom_sha256", "")
        deps = json.loads((ROOT / "tools" / "dependencies.json").read_text(encoding="utf-8"))
        cls.cc65_pin = deps["cc65"]["revision"]
        cls.geargrafx_pin = deps["geargrafx"]["revision"]

    @classmethod
    def tearDownClass(cls):
        tmp = cls._tmp.name
        assert "pce-1c-blocker-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    def _classify(self, run, expected_slot=SLOT):
        from api_halt_classify import classify
        return classify(run, stub_address=self.stub["api_unimplemented"], expected_slot=expected_slot)

    # ---- real-core blocker proof ----

    def test_1c_entry_is_unimplemented_stub_halt_cold_and_warm(self):
        from api_halt_classify import API_UNIMPLEMENTED_HALT
        for run in self.runs:
            with self.subTest(cycle=run["cycle"]):
                verdict = self._classify(run)
                self.assertEqual(verdict["state"], API_UNIMPLEMENTED_HALT, verdict)
                self.assertTrue(run["halt_confirmed"], run)
                self.assertEqual(run["x"], SLOT, run)
                self.assertEqual(run["pc"], self.stub["api_unimplemented"], run)

    def test_1c_call_does_not_return_marker_unwritten(self):
        for run in self.runs:
            with self.subTest(cycle=run["cycle"]):
                dump = bytes.fromhex(run["buffer_hex"])
                self.assertGreater(len(dump), PRES_OFFSET)
                self.assertNotEqual(dump[PRES_OFFSET], MARKER,
                                    "marker written means the $E054 call returned (bare RTS), not a stub halt")

    def test_1c_cold_warm_agree(self):
        cold, warm = self.runs
        for key in ("reason", "halt_confirmed", "x", "pc"):
            self.assertEqual(cold[key], warm[key], key)
        self.assertEqual(bytes.fromhex(cold["buffer_hex"])[PRES_OFFSET],
                         bytes.fromhex(warm["buffer_hex"])[PRES_OFFSET])

    def test_1c_evidence_pins_versions_and_hashes(self):
        self.assertTrue(self.bios_sha256)
        self.assertEqual(self.rom_sha256_probe, self.bios_sha256)
        self.assertTrue(self.cc65_pin.startswith("71746c8"))
        self.assertTrue(self.geargrafx_pin.startswith("b49ae82a"))
        self.assertEqual(ENTRY, 0xE054)

    # ---- harness self-checks (no extra real-core runs) ----

    def test_harness_detects_wrong_slot_expectation(self):
        from api_halt_classify import API_SLOT_MISMATCH
        for run in self.runs:
            with self.subTest(cycle=run["cycle"]):
                verdict = self._classify(run, expected_slot=0x1D)
                self.assertEqual(verdict["state"], API_SLOT_MISMATCH, verdict)

    def test_bare_rts_would_not_pass_as_blocker(self):
        # A bare-RTS forward step returns to the payload: cd_halt_ok + marker written,
        # which this suite's halt assertions reject. Assert the recorded runs are not that.
        for run in self.runs:
            with self.subTest(cycle=run["cycle"]):
                self.assertNotEqual(run["reason"], "cd_halt_ok", run)

    # ---- ledger guards: $1C stays BLOCKED_ABI (host-only, guards shared-wiring drift) ----

    def test_ledger_slot_1c_has_no_dev_override(self):
        self.assertFalse((ROOT / "src" / "overrides" / "slot_1C.s").exists())
        self.assertFalse((ROOT / "src" / "overrides" / "slot_1c.s").exists())

    def test_ledger_dev_implementations_id28_not_ready(self):
        data = json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))
        slots = data["slots"] if isinstance(data, dict) and "slots" in data else data
        entry = next(s for s in slots if s.get("id") == 28)
        self.assertEqual(entry["candidate_name"], "BM_DELETE")
        self.assertEqual(entry["state"], "NOT_READY")
        self.assertIsNone(entry["implementation"])

    def test_ledger_api_contracts_slot28_unknown_hypothesis(self):
        data = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        apis = data["apis"] if isinstance(data, dict) and "apis" in data else data
        entry = next(a for a in apis if a.get("slot") == 28)
        self.assertEqual(entry["candidate_name"], "BM_DELETE")
        self.assertEqual(entry["candidate_entry"], "0xE054")
        self.assertEqual(entry["mapping_status"], "HYPOTHESIS")
        for field in ("arguments", "returns", "clobbered", "preserved", "stack", "mpr",
                      "ram", "flags", "irq", "errors"):
            with self.subTest(field=field):
                self.assertEqual(entry["contract"][field]["status"], "UNKNOWN")

    def test_ledger_no_jsr_gate_for_e054(self):
        gate = json.loads((ROOT / "spec" / "reference_jsr_gate.json").read_text(encoding="utf-8"))
        slots = gate.get("slots", {})
        self.assertNotIn("28", slots)
        self.assertNotIn("0xE054", json.dumps(slots))
        for sid, entry in slots.items():
            self.assertFalse(entry.get("release_eligible", False), sid)


if __name__ == "__main__":
    unittest.main()
