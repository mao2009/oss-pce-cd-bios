"""Real-core end-to-end check of the Issue #6 trace harness (needs make setup + make dev-cdread).

Runs the original CD read diagnostic ROM on the pinned debugger-enabled Geargrafx through
geargrafx_harness and asserts the classification of one positive and several real negatives.
Any BLOCKED doctor result stops with exit 3; it is never reported as PASS.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from geargrafx_harness import doctor, main as harness  # noqa: E402
from make_cd_image import CUE_NAME, build_image  # noqa: E402

FORBID = ["--forbid", "cd_halt_fail", "--forbid", "cd_unexpected_interrupt"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    folder = ROOT / "build" / args.mode / "cdread-probe"
    rom, labels = folder / "cdread-probe-not-bios.pce", folder / "trace-labels.txt"
    output = ROOT / "build" / args.mode / "trace"
    with tempfile.TemporaryDirectory(prefix="pce-trace-e2e-") as tmp:
        tmp = Path(tmp)
        build_image(tmp / "disc")
        cue = tmp / "disc" / CUE_NAME
        report = doctor(rom, cue, labels)
        if report["status"] != "PASS":
            print(json.dumps(report, indent=2), file=sys.stderr)
            print(f"{report['status']} trace harness prerequisites (not PASS)", file=sys.stderr)
            raise SystemExit(3 if report["status"] == "BLOCKED" else 1)
        bad_rom = tmp / "garbage-not-bios.pce"
        bad_rom.write_bytes(bytes(262144))  # System Card size, no SEI entry: must not pass
        short_rom = tmp / "truncated-not-bios.pce"
        short_rom.write_bytes(rom.read_bytes()[:1000])
        base = ["--rom", str(rom), "--cue", str(cue), "--labels", str(labels), "--expect", "cd_halt_ok", *FORBID]
        cases = {  # name: (argv overrides appended last, expected status)
            "positive": ([], "PASS"),
            "wall_clock_timeout": (["--timeout", "0.01"], "FAIL"),
            "instruction_budget": (["--steps", "1000"], "FAIL"),
            "wrong_expected_label": (["--expect", "cd_halt_fail"], "FAIL"),
            "invalid_rom_entry": (["--rom", str(bad_rom)], "FAIL"),
            "truncated_rom": (["--rom", str(short_rom)], "FAIL"),
            "missing_rom": (["--rom", str(tmp / "absent.pce")], "BLOCKED"),
        }
        results = {}
        for name, (extra, expected) in cases.items():
            path = output / f"{name}.json"
            code = harness(["trace", *base, *extra, "--output", str(path)])
            log = json.loads(path.read_text())
            if log["status"] != expected:
                raise SystemExit(f"FAIL {name}: expected {expected}, got {log['status']} ({log.get('reason')})")
            results[name] = {"status": log["status"], "exit": code, "reason": log.get("reason")}
    (output / "summary.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(f"PASS real Geargrafx trace harness: positive + {len(results) - 1} classified negatives -> {output}")


if __name__ == "__main__":
    main()
