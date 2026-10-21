"""Full 0..255 real-core sweep of EX_BINBCD through the DEV ROM entry $E0B4 (slot $3C).

Run: make dev-sweep
  or: python3 tests/integration/dev_bcd_full_sweep.py [path-to-dev-rom.pce]
The repository root is taken from this file's own location, so the script runs from any checkout
(it is not tied to a particular working directory).
Reuses guest()/entry_address()/run_probe()/expected_binbcd() from tests/integration/geargrafx_dev_bcd.py.
Checks per input: A, C, X, Y, P.D==0, P.I==1 (set by the guest's SEI), return PC, SP==$FF, MPR1/MPR7.
N/Z/V are recorded but NOT checked (unspecified by the DEV contract).
Negative: the 100..255 refusal subset is reported separately; a wrong-entry expectation must FAIL.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROM = Path(sys.argv[1] if len(sys.argv) > 1
           else REPO / "build" / "debug" / "bios-dev" / "dev-only-not-compatible-syscard3.pce").resolve()
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests" / "integration"))
import geargrafx_dev_bcd as t  # noqa: E402
import geargrafx_api as api  # noqa: E402
from build_rom import verify_dev_rom  # noqa: E402

data = ROM.read_bytes()
verify_dev_rom(data, manifest=ROM.parent / "manifest.json")
reset = int.from_bytes(data[0x1FFE:0x2000], "little")
probe = api.compile_probe(ROM.parent)
entry = t.entry_address(0x3C)
results, failures = [], []

with tempfile.TemporaryDirectory(prefix="pce-dev-bcd-full-") as tmp:
    folder = Path(tmp)
    (folder / "original.bin").write_bytes(bytes(2352 * 32))
    cue = folder / "original.cue"
    cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
    for value in range(256):
        want_a, want_c = t.expected_binbcd(value)
        code, halt = t.guest(reset, entry, value)
        variant = bytearray(data)
        variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
        rom = folder / f"in-{value:02x}.pce"
        rom.write_bytes(variant)
        rows = t.run_probe(probe, rom.resolve(), cue)
        cold = next(r for r in rows if r["phase"] == "cold")
        stored = bytes.fromhex(cold["work_ram_0200_hex"])[:4]
        a, p, x, y = stored[0], stored[1], stored[2], stored[3]
        checks = {
            "A": a == want_a, "C": (p & 1) == want_c, "X": x == 0x5A, "Y": y == 0xA5,
            "D": (p & 0x08) == 0, "I": (p & 0x04) != 0,
            "return_pc": cold["pc"] == halt, "SP": cold["sp"] == 0xFF,
            "MPR1": cold["mpr"][1] == 0xF8, "MPR7": cold["mpr"][7] == 0,
        }
        bad = [k for k, ok in checks.items() if not ok]
        rec = {"input": value, "expected_a": want_a, "expected_c": want_c, "A": a, "P": p, "X": x, "Y": y,
               "NZV_unchecked": p & 0xE2, "pc": cold["pc"], "halt": halt, "sp": cold["sp"],
               "mpr": cold["mpr"], "range": "valid" if value <= 99 else "refusal", "failed": bad}
        results.append(rec)
        if bad:
            failures.append(rec)

    neg_wrong = t.run_case_expect_fail(probe, data, folder, cue, reset, "wrong-decode", entry, 0x42,
                                       (42, 0, 0x5A, 0xA5))

summary = {
    "scope": "DEV ROM real-core full sweep, project-own EX_BINBCD, NOT System Card ABI",
    "rom_sha256": hashlib.sha256(data).hexdigest(),
    "inputs_run": len(results), "domain": "0..255",
    "valid_0_99": sum(r["range"] == "valid" for r in results),
    "refusal_100_255": sum(r["range"] == "refusal" for r in results),
    "failures": len(failures), "failed_inputs": [r["input"] for r in failures],
    "negative_wrong_entry_detected": neg_wrong,
}
(ROM.parent / "dev-bcd-full-sweep.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2) + "\n")
print(json.dumps(summary, indent=2))
print(f"{'PASS' if not failures else 'FAIL'} DEV ROM EX_BINBCD $3C full domain: {len(results)} inputs, "
      f"{len(failures)} failures")
sys.exit(0 if not failures else 1)
