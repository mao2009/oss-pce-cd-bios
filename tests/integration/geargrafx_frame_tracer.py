"""Run the bounded, observation-only Geargrafx normal-frame tracer.

This is a local diagnostic, not a BIOS boot or compatibility test.  It reuses the
existing pinned-core probe compiler and private process runner.  Events contain
register/state metadata and working-RAM change ranges only, never guest bytes.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/integration"))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_harness import doctor, run_job, stage_disc  # noqa: E402

MAX_FRAMES = 600
MAX_EVENTS = 256
EVENT_KINDS = {"sample", "cd", "wram", "handoff"}


def parse_records(stdout):
    """Validate the small public-safe protocol emitted by the C++ tracer."""
    cap = end = None
    events = []
    for line in stdout.splitlines():
        tag, separator, body = line.partition(" ")
        if tag not in {"CAP", "EVENT", "END"}:
            continue
        if not separator:
            raise ValueError(f"{tag} missing JSON body")
        row = json.loads(body)
        if type(row) is not dict:
            raise ValueError(f"{tag} body is not an object")
        if tag == "CAP":
            if cap is not None:
                raise ValueError("duplicate CAP")
            cap = row
        elif tag == "EVENT":
            events.append(row)
        else:
            if end is not None:
                raise ValueError("duplicate END")
            end = row
    if cap is None or end is None:
        raise ValueError("missing CAP or END")
    if cap.get("normal_frame_execution") is not True or cap.get("instruction_step") is not False:
        raise ValueError("not a normal-frame trace")
    if (cap.get("guest_mutation") is not False or cap.get("mmio_reads") is not False or
            cap.get("scsi_event_notification") is not True):
        raise ValueError("tracer safety capability missing")
    capacity = cap.get("ring_capacity")
    if type(capacity) is not int or not 1 <= capacity <= MAX_EVENTS:
        raise ValueError("invalid ring capacity")
    if len(events) > capacity or end.get("events") != len(events):
        raise ValueError("ring event count mismatch")
    if (type(end.get("dropped")) is not int or end["dropped"] < 0 or
            type(end.get("scsi_events_dropped")) is not int or end["scsi_events_dropped"] < 0 or
            end.get("overflow") is not (end["dropped"] > 0 or end["scsi_events_dropped"] > 0)):
        raise ValueError("invalid overflow accounting")
    for event in events:
        if event.get("kind") not in EVENT_KINDS or type(event.get("frame")) is not int or not 1 <= event["frame"] <= cap["frames"]:
            raise ValueError("invalid event header")
        if (type(event.get("pc")) is not int or type(event.get("physical_pc")) is not int or
                type(event.get("pc_bank")) is not int or type(event.get("mpr")) is not list or len(event["mpr"]) != 8):
            raise ValueError("event lacks CPU/MPR snapshot")
        if any(type(value) is not int or not 0 <= value <= 255 for value in event["mpr"]):
            raise ValueError("invalid MPR snapshot")
        if event["kind"] == "wram" and not all(type(event.get(key)) is int for key in ("changed", "first_wram", "last_wram")):
            raise ValueError("invalid WRAM change event")
        if event["kind"] == "cd" and not all(type(event.get(key)) is int for key in ("cd_event", "command", "lba", "status")):
            raise ValueError("invalid CD event")
    return cap, events, end


def trace(args):
    if not 1 <= args.frames <= MAX_FRAMES or not 1 <= args.events <= MAX_EVENTS:
        return {"status": "FAIL", "reason": f"limits are frames 1..{MAX_FRAMES}, events 1..{MAX_EVENTS}",
                "evidence": "NOT_RUN"}
    # Do not invoke doctor() on caller media: it records input paths and hashes.
    # The core/tool readiness result is retained without private input metadata.
    report = doctor(None, None, None)
    log = {"schema_version": 1, "scope": "normal-frame core observation; not BIOS boot or compatibility evidence",
           "artifact_policy": "LOCAL_ONLY: never upload traces; events contain no guest bytes",
            "core_readiness": report["status"], "frames": args.frames, "ring_capacity": args.events}
    if report["status"] != "PASS":
        log.update(status=report["status"], reason="doctor prerequisites", evidence="NOT_RUN")
        return log
    job = Path(tempfile.mkdtemp(prefix="pce-frame-trace-"))
    try:
        try:
            probe = compile_probe(job, "geargrafx_frame_tracer.cpp", "geargrafx-frame-tracer")
        except Exception as error:  # compile_probe supplies a sanitized failure description
            log.update(status="BLOCKED", reason=f"probe build: {error}", evidence="NOT_RUN")
            return log
        rom = job / "input.pce"
        shutil.copyfile(args.rom, rom)
        cue = "-"
        if args.cue is not None:
            disc = job / "disc"
            disc.mkdir()
            try:
                staged = stage_disc(args.cue, disc)
            except FileNotFoundError as error:
                log.update(status="BLOCKED", reason="disc staging input missing", evidence="NOT_RUN")
                return log
            except ValueError as error:
                log.update(status="FAIL", reason="invalid disc input", evidence="NOT_RUN")
                return log
            cue = str(disc / staged[0])
        result = run_job([str(probe), str(rom), cue, str(args.frames), str(args.events)], job, args.timeout)
        if result["status"]:
            log.update(status=result["status"], reason=result["reason"], evidence="NOT_RUN")
            return log
        if result["exit_code"] != 0:
            log.update(status="FAIL", reason=f"probe exited {result['exit_code']}", evidence="NOT_RUN")
            return log
        try:
            cap, events, end = parse_records(result["stdout"])
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            log.update(status="FAIL", reason=f"trace rejected: {error}", evidence="NOT_RUN")
            return log
        log.update(status="PASS", reason="bounded normal-frame trace completed", evidence="REAL_GEARGRAFX_CORE",
                   elapsed_s=result["elapsed_s"], capability=cap, events=events, summary=end)
        return log
    finally:
        shutil.rmtree(job, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--cue", type=Path)
    parser.add_argument("--frames", type=int, default=2)
    parser.add_argument("--events", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        args.output.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        parser.error("--output must be outside the repository (local trace evidence only)")
    log = trace(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(log, indent=2, sort_keys=True) + "\n")
    print(f"{log['status']}: {log['reason']} -> {args.output}")
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 3, "SKIP": 4}[log["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
