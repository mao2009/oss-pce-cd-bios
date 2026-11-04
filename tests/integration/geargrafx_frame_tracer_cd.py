"""Real-core #603 bridge: trace the existing original synthetic CD-read diagnostic.

This is an opt-in observation only.  It first applies the existing CD-read
oracle to a real Geargrafx guest run, then runs that same ROM/media pair through
the normal-frame RAM observer both on and off.  The trace reports metadata only.
"""
import argparse
import json
from pathlib import Path
import subprocess
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


class IntegrationBlocked(RuntimeError):
    """A documented prerequisite prevented this diagnostic from running."""


TracerBlocked = IntegrationBlocked


def execute_cd_diagnostic(directory, rom, cue, labels):
    """Run the established diagnostic and preserve its explicit blocker outcomes."""
    try:
        probe = compile_probe(directory, "geargrafx_cdread_probe.cpp", "geargrafx-frame-tracer-cdread")
    except ValueError as error:
        # compile_probe uses this documented prefix for missing pinned tools/core objects.
        if str(error).startswith("BLOCKED:"):
            raise IntegrationBlocked(f"CD diagnostic probe build: {error}") from error
        raise
    try:
        return execute(probe, rom, cue, labels, STEP_BUDGET)
    except subprocess.CalledProcessError as error:
        # Match the established CD-read diagnostic meaning for this probe exit.
        if error.returncode == 7:
            raise IntegrationBlocked("CD diagnostic probe: core did not enable CD hardware (probe exit 7)") from error
        raise


def trace(rom, cue, output, observe_ram):
    return tracer.trace(argparse.Namespace(rom=rom, cue=cue, frames=40, events=256, timeout=60.0,
                                           run_start_frame=None, run_hold_frames=None,
                                           observe_ram=observe_ram))


def run(mode):
    """Run the bridge; callers classify blocked tracer and probe execution separately."""
    folder = ROOT / "build" / mode / "cdread-probe"
    rom, labels = folder / "cdread-probe-not-bios.pce", folder / "trace-labels.txt"
    if not rom.is_file() or not labels.is_file():
        missing = [name for path, name in ((rom, "ROM"), (labels, "trace labels")) if not path.is_file()]
        raise IntegrationBlocked(
            f"missing generated CD-read artifacts ({', '.join(missing)}); run make dev-cdread first"
        )
    with tempfile.TemporaryDirectory(prefix="pce-frame-tracer-cd-", dir=str(tracer.ensure_private_root())) as temporary:
        temporary = Path(temporary)
        manifest = build_image(temporary / "disc")
        cue = temporary / "disc" / CUE_NAME
        # Existing guest diagnostic and oracle: this is the precondition for the observer run.
        records = execute_cd_diagnostic(temporary, rom, cue, labels)
        symbols = json.loads((folder / "manifest.json").read_text())["symbols"]
        check_success(records, symbols, [sector_payload(i) for i in range(DEFAULT_SECTORS)], *DEFAULT_REQUEST)
        on, off = trace(rom, cue, temporary / "on.json", True), trace(rom, cue, temporary / "off.json", False)
        for name, result in (("ON", on), ("OFF", off)):
            if result.get("status") == "BLOCKED":
                raise IntegrationBlocked(f"RAM observer {name}: {result.get('reason', 'unspecified blocker')}")
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
        table = measurement_evidence(mode, on, off)
        ring = {name: table["runs"][name]["output_ring_dropped"] for name in ("observer_on", "observer_off")}
        core = {name: (table["runs"][name]["max_frame_core_entries"]["value"],
                       table["runs"][name]["core_trace_dropped_frames"]["value"])
                for name in ("observer_on", "observer_off")}
        print("PASS real Geargrafx synthetic CD-read + RAM observer: regions wram/cdrom_ram/card_ram; "
              "observed delta wram only; observer ON/OFF both completed (frame records not compared); no guest bytes; "
              f"core_capacity={table['core_capacity']['value']} (REPORTED_BY_CORE configuration); "
              f"output ring dropped ON={ring['observer_on']['value']}/{ring['observer_on']['pushed']} "
              f"OFF={ring['observer_off']['value']}/{ring['observer_off']['pushed']} pushed (MEASURED); "
              f"max_frame_core_entries ON={core['observer_on'][0]} OFF={core['observer_off'][0]} (MEASURED, pre-clamp); "
              f"core_trace_dropped_frames ON={core['observer_on'][1]} OFF={core['observer_off'][1]} (MEASURED); "
              f"{', '.join(sorted(tracer.NOT_MEASURED_REASONS))} NOT_MEASURED")


def measurement_evidence(mode, on, off):
    """Counts-only measurement table for both runs; written under build/ (never guest bytes or traces)."""
    runs = {"observer_on": on.get("measurements"), "observer_off": off.get("measurements")}
    for name, table in runs.items():
        if type(table) is not dict or set(table) != set(tracer.MEASUREMENT_NAMES):
            raise ValueError(f"{name}: measurement table missing or incomplete")
        ring = table["output_ring_dropped"]
        if table["core_capacity"].get("status") != tracer.REPORTED_BY_CORE or ring.get("status") != tracer.MEASURED:
            raise ValueError(f"{name}: unexpected measurement status")
        if ring["pushed"] != ring["retained"] + ring["value"]:
            raise ValueError(f"{name}: output ring push accounting mismatch")
        if any(table[key].get("status") != tracer.NOT_MEASURED for key in tracer.NOT_MEASURED_REASONS):
            raise ValueError(f"{name}: a NOT_MEASURED quantity was reported as measured")
        # The in-repo tracer emits core_trace accounting, so on the real core these two must be
        # MEASURED; a legacy or discontinuous result is a failure here, not a silent downgrade.
        for key in tracer.CORE_TRACE_MEASURED:
            row = table[key]
            if row.get("status") != tracer.MEASURED or type(row.get("value")) is not int or row["value"] < 0:
                raise ValueError(f"{name}: {key} is not a measured count")
        drops = table["core_trace_dropped_frames"]
        if drops["value"] > drops.get("frames", -1) or (drops["value"] == 0) != (drops.get("lost_entries") == 0):
            raise ValueError(f"{name}: core trace dropped-frame count is inconsistent")
    capacities = {table["core_capacity"]["value"] for table in runs.values()}
    if len(capacities) != 1:
        raise ValueError(f"core trace capacity differs between observer runs: {sorted(capacities)}")
    evidence = {"scope": "frame tracer measurement table on synthetic CD media; diagnostic only, not BIOS evidence",
                "core_capacity": runs["observer_on"]["core_capacity"], "runs": runs,
                "proprietary_content": "NONE", "guest_bytes": "NONE"}
    output = ROOT / "build" / mode / "frame-tracer-cd"
    output.mkdir(parents=True, exist_ok=True)
    (output / "frame-tracer-measurements.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args(argv)
    try:
        run(args.mode)
    except IntegrationBlocked as error:
        print(f"BLOCKED: frame-tracer CD integration: {error}", file=sys.stderr)
        return 3
    except (AssertionError, ValueError) as error:
        print(f"FAIL: frame-tracer CD integration oracle assertion: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # unexpected diagnostic failures are failures, never blockers
        print(f"FAIL: frame-tracer CD integration unexpected {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
