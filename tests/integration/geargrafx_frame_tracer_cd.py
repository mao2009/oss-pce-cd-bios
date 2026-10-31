"""Real-core #603 bridge: trace the existing original synthetic CD-read diagnostic.

This is an opt-in observation only.  It first applies the existing CD-read
oracle to a real Geargrafx guest run, then runs that same ROM/media pair through
the normal-frame RAM observer both on and off.  The trace reports metadata only.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_cdread import DEFAULT_REQUEST, STEP_BUDGET, execute  # noqa: E402
from cdread_verify import check_success  # noqa: E402
from make_cd_image import CUE_NAME, DEFAULT_SECTORS, build_image, sector_payload  # noqa: E402
import geargrafx_frame_tracer as tracer  # noqa: E402


def trace(rom, cue, output, observe_ram):
    return tracer.trace(argparse.Namespace(rom=rom, cue=cue, frames=40, events=256, timeout=60.0,
                                           run_start_frame=None, run_hold_frames=None,
                                           observe_ram=observe_ram))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    folder = ROOT / "build" / args.mode / "cdread-probe"
    rom, labels = folder / "cdread-probe-not-bios.pce", folder / "trace-labels.txt"
    if not rom.is_file() or not labels.is_file():
        raise ValueError("run make dev-cdread first")
    with tempfile.TemporaryDirectory(prefix="pce-frame-tracer-cd-", dir="/tmp/opencode") as temporary:
        temporary = Path(temporary)
        manifest = build_image(temporary / "disc")
        cue = temporary / "disc" / CUE_NAME
        # Existing guest diagnostic and oracle: this is the precondition for the observer run.
        probe = compile_probe(temporary, "geargrafx_cdread_probe.cpp", "geargrafx-frame-tracer-cdread")
        records = execute(probe, rom, cue, labels, STEP_BUDGET)
        symbols = json.loads((folder / "manifest.json").read_text())["symbols"]
        check_success(records, symbols, [sector_payload(i) for i in range(DEFAULT_SECTORS)], *DEFAULT_REQUEST)
        on, off = trace(rom, cue, temporary / "on.json", True), trace(rom, cue, temporary / "off.json", False)
        if on["status"] != "PASS" or off["status"] != "PASS":
            raise ValueError(f"frame trace did not complete: on={on['status']} off={off['status']}")
        expected = {"wram", "cdrom_ram", "card_ram"}
        regions = {row["ram"] for row in on["capability"]["ram_regions"]}
        if regions != expected:
            raise ValueError(f"RAM region classification {sorted(regions)}, expected {sorted(expected)}")
        if {event["ram"] for event in on["events"] if event["kind"] == "ram"} != {"wram"}:
            raise ValueError("synthetic CD-read trace did not have the expected main-RAM-only delta")
        # Both modes completed. Do not assert record equality: separate normal-frame
        # processes can land at different instruction boundaries around CD polling.
        # This is therefore not an observer noninterference claim.
        print("PASS real Geargrafx synthetic CD-read + RAM observer: regions wram/cdrom_ram/card_ram; "
              "observed delta wram only; observer ON/OFF both completed (frame records not compared); no guest bytes")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"frame-tracer CD integration FAIL/BLOCKED: {error}") from error
