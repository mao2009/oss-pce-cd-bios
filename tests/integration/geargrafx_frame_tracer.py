"""Run the bounded, observation-only Geargrafx normal-frame tracer.

This is a local diagnostic, not a BIOS boot or compatibility test.  It reuses the
existing pinned-core probe compiler and private process runner. Events contain
register/state metadata and RAM change counts/ranges/banks only, never guest bytes.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
OPENCODE_TEMP_ROOT = Path("/tmp/opencode")
JOB_PARENT = OPENCODE_TEMP_ROOT / "frame-tracer-jobs"
sys.path.insert(0, str(ROOT / "tests/integration"))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_harness import doctor, run_job, stage_disc  # noqa: E402

MAX_FRAMES = 600
MAX_EVENTS = 256
EVENT_KINDS = {"sample", "cd", "ram", "handoff", "region"}
RAM_BANK_RANGES = {
    "wram": ((0x2000, 0xf8, 0xfb), (0x8000, 0xf8, 0xfb)),
    "cdrom_ram": ((0x10000, 0x80, 0x87),),
    "card_ram": ((0x8000, 0x40, 0x5f), (0x30000, 0x68, 0x7f)),
}
CPU_REGIONS = {"unknown", "other", "cdrom_ram", "working_ram", "hardware"}


def run_schedule(start_frame, hold_frames, frames):
    """Return public-controller actions before/after whole-frame execution."""
    if (start_frame is None) != (hold_frames is None):
        raise ValueError("RUN start and hold must be specified together")
    if start_frame is None:
        return ()
    if not 1 <= start_frame <= frames or not 1 <= hold_frames <= frames or start_frame + hold_frames - 1 > frames:
        raise ValueError("RUN schedule is outside the requested frames")
    return ((start_frame, "press"), (start_frame + hold_frames - 1, "release"))


def validate_ram_deltas(rows, frames):
    """Validate cumulative, byte-free RAM metadata independently of event retention."""
    if type(rows) is not list or {row.get("region") for row in rows if type(row) is dict} != set(RAM_BANK_RANGES) or len(rows) != 3:
        raise ValueError("invalid RAM region summary")
    for row in rows:
        if type(row) is not dict:
            raise ValueError("invalid RAM region entry")
        region, present, availability = row.get("region"), row.get("present"), row.get("availability")
        if availability not in {"PRESENT", "ABSENT", "UNKNOWN"}:
            raise ValueError("invalid RAM availability")
        if (availability == "PRESENT") != (present is True) or (availability == "ABSENT") != (present is False) or \
                (availability == "UNKNOWN") != (present is None):
            raise ValueError("RAM availability is not distinct")
        updates, max_changed = row.get("updates"), row.get("max_changed")
        if type(updates) is not int or updates < 0 or type(max_changed) is not int or max_changed < 0:
            raise ValueError("invalid RAM update count")
        range_keys = ("first_frame", "last_frame", "min_offset", "max_offset")
        if availability != "PRESENT":
            if updates or max_changed or any(row.get(key) is not None for key in range_keys):
                raise ValueError("unavailable RAM has deltas")
            continue
        # Single confirmed table shared with parse_records and the C++ RAM header.
        # This accepts the same wram layouts (0x2000 or 0x8000) as the event path.
        if (row.get("size"), row.get("bank_first"), row.get("bank_last")) not in RAM_BANK_RANGES[region]:
            raise ValueError("unconfirmed RAM bank mapping")
        if updates:
            first, last, minimum, maximum = (row.get(key) for key in range_keys)
            if not all(type(value) is int for value in (first, last, minimum, maximum)) or not 1 <= first <= last <= frames or \
                    not 0 <= minimum <= maximum < row["size"] or not 1 <= max_changed <= row["size"]:
                raise ValueError("invalid RAM delta range")
        elif max_changed or any(row.get(key) is not None for key in range_keys):
            raise ValueError("empty RAM delta has range")
        correlation, scsi_frames = row.get("scsi_correlation"), row.get("scsi_update_frames")
        if correlation not in {"OBSERVED", "NONE_OBSERVED", "UNKNOWN"} or type(scsi_frames) is not list or \
                any(type(frame) is not int or not 1 <= frame <= frames for frame in scsi_frames) or scsi_frames != sorted(set(scsi_frames)):
            raise ValueError("invalid SCSI frame correlation")
        if correlation == "OBSERVED" and not scsi_frames:
            raise ValueError("observed SCSI correlation lacks frames")
        if correlation == "NONE_OBSERVED" and scsi_frames:
            raise ValueError("empty SCSI correlation has frames")


def normalized_absolute(path):
    """Return a lexical, normalized absolute path without following links."""
    return Path(os.path.abspath(os.fspath(path)))


def is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def existing_opencode_worktrees():
    """All existing direct children except this diagnostic's dedicated parent."""
    root = normalized_absolute(OPENCODE_TEMP_ROOT)
    parent = normalized_absolute(JOB_PARENT)
    try:
        return [child for child in root.iterdir() if child != parent and child.is_dir() and not child.is_symlink()]
    except OSError:
        return []


def validate_tracked_job_dir(path, identity=None):
    """Fail closed unless path is an owned direct child of the dedicated parent."""
    supplied = Path(path)
    if not supplied.is_absolute():
        return False, "not_absolute"
    job = normalized_absolute(supplied)
    parent = normalized_absolute(JOB_PARENT)
    opencode_root = normalized_absolute(OPENCODE_TEMP_ROOT)
    prohibited = (Path("/"), Path.home(), normalized_absolute(ROOT), normalized_absolute(OPENCODE_TEMP_ROOT))
    if job in prohibited:
        return False, "prohibited_path"
    try:
        root_status = os.lstat(opencode_root)
    except OSError:
        return False, "missing"
    if (not stat.S_ISDIR(root_status.st_mode) or stat.S_ISLNK(root_status.st_mode) or
            root_status.st_uid != os.getuid()):
        return False, "unsafe_root"
    if any(is_within(job, worktree) for worktree in existing_opencode_worktrees()):
        return False, "opencode_worktree"
    if job.parent != parent:
        return False, "not_direct_child"
    try:
        parent_status = os.lstat(parent)
    except OSError:
        return False, "missing"
    if not stat.S_ISDIR(parent_status.st_mode) or stat.S_ISLNK(parent_status.st_mode):
        return False, "unsafe_parent"
    if parent_status.st_uid != os.getuid():
        return False, "not_owned"
    try:
        current = os.lstat(job)
    except OSError:
        return False, "missing"
    if not stat.S_ISDIR(current.st_mode) or stat.S_ISLNK(current.st_mode):
        return False, "unsafe_job"
    uid = os.getuid()
    if current.st_uid != uid:
        return False, "not_owned"
    current_identity = (current.st_dev, current.st_ino, current.st_uid)
    if identity is not None and current_identity != identity:
        return False, "identity_changed"
    return True, "ok"


def ensure_private_root():
    """Return the dedicated root, creating it privately (mode 0700) when absent.

    An existing root is used only if it is a real directory (not a symlink),
    owned by the current user, and not accessible to group or others.  Any other
    state fails closed.  Nothing is removed here.
    """
    root = normalized_absolute(OPENCODE_TEMP_ROOT)
    try:
        os.mkdir(root, 0o700)
    except FileExistsError:
        pass
    except OSError as error:
        raise RuntimeError("unsafe frame tracer temporary root") from error
    try:
        status = os.lstat(root)
    except OSError as error:
        raise RuntimeError("unsafe frame tracer temporary root") from error
    if (not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode) or
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) & 0o077):
        raise RuntimeError("unsafe frame tracer temporary root")
    return root


def make_tracked_job_dir():
    """Create a current-user-owned job in the dedicated parent under a private root."""
    ensure_private_root()
    parent = normalized_absolute(JOB_PARENT)
    try:
        parent.mkdir(mode=0o700, parents=False, exist_ok=True)
        parent_status = os.lstat(parent)
    except OSError as error:
        raise RuntimeError("unsafe frame tracer temporary parent") from error
    if (not stat.S_ISDIR(parent_status.st_mode) or stat.S_ISLNK(parent_status.st_mode) or
            parent_status.st_uid != os.getuid()):
        raise RuntimeError("unsafe frame tracer temporary parent")
    try:
        path = Path(tempfile.mkdtemp(prefix="pce-frame-trace-", dir=parent))
        current = os.lstat(path)
    except OSError as error:
        raise RuntimeError("unable to create frame tracer temporary job") from error
    identity = (current.st_dev, current.st_ino, current.st_uid)
    valid, reason = validate_tracked_job_dir(path, identity)
    if not valid:
        raise RuntimeError(f"unsafe frame tracer temporary job: {reason}")
    return path, identity


def cleanup_tracked_job_dir(path, identity):
    """Atomically quarantine and remove only the validated, original job."""
    valid, reason = validate_tracked_job_dir(path, identity)
    if not valid:
        return False, reason
    job = normalized_absolute(path)
    parent = normalized_absolute(JOB_PARENT)
    try:
        expected_parent = os.lstat(parent)
        nofollow = os.O_NOFOLLOW
    except (AttributeError, OSError):
        return False, "unsafe_parent"
    try:
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    except OSError:
        return False, "unsafe_parent"
    try:
        opened_parent = os.fstat(parent_fd)
        if (opened_parent.st_dev, opened_parent.st_ino, opened_parent.st_uid) != (
                expected_parent.st_dev, expected_parent.st_ino, expected_parent.st_uid):
            return False, "parent_changed"
        current = os.stat(job.name, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISDIR(current.st_mode) or stat.S_ISLNK(current.st_mode) or
                (current.st_dev, current.st_ino, current.st_uid) != identity):
            return False, "identity_changed"
        quarantine = f".pce-frame-trace-cleanup-{secrets.token_hex(16)}"
        os.rename(job.name, quarantine, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        quarantined = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISDIR(quarantined.st_mode) or stat.S_ISLNK(quarantined.st_mode) or
                (quarantined.st_dev, quarantined.st_ino, quarantined.st_uid) != identity):
            return False, "quarantine_identity_changed"
        shutil.rmtree(quarantine, dir_fd=parent_fd)
    except OSError:
        return False, "remove_failed"
    finally:
        os.close(parent_fd)
    return True, "removed"


def parse_records(stdout, require_ram_observation=True):
    """Validate the small public-safe protocol emitted by the C++ tracer."""
    cap = end = None
    inputs = []
    events = []
    for line in stdout.splitlines():
        tag, separator, body = line.partition(" ")
        if tag not in {"CAP", "INPUT", "EVENT", "END"}:
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
        elif tag == "INPUT":
            inputs.append(row)
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
    if type(cap.get("ram_observation")) is not bool:
        raise ValueError("invalid RAM observation capability")
    if (cap.get("guest_mutation") is not False or cap.get("mmio_reads") is not False or
            cap.get("scsi_event_notification") is not True or cap.get("cpu_event_notification") is not True or
            cap.get("ram_delta_summary") is not True or
            (cap.get("ram_observation") is not True and require_ram_observation)):
        raise ValueError("tracer safety capability missing")
    capacity = cap.get("ring_capacity")
    if type(capacity) is not int or not 1 <= capacity <= MAX_EVENTS:
        raise ValueError("invalid ring capacity")
    requested = cap.get("run_input_requested")
    if type(requested) is not bool:
        raise ValueError("invalid RUN capability")
    start, hold = cap.get("run_start_frame"), cap.get("run_hold_frames")
    if requested:
        if run_schedule(start, hold, cap["frames"]) != tuple((row.get("frame"), row.get("action")) for row in inputs):
            raise ValueError("RUN input schedule mismatch")
        if [row.get("run_pressed") for row in inputs] != [True, False]:
            raise ValueError("RUN input API did not report press/release")
    elif inputs or start != 0 or hold != 0:
        raise ValueError("unexpected RUN input record")
    regions = cap.get("ram_regions")
    if type(regions) is not list:
        raise ValueError("invalid RAM region inventory")
    if cap.get("ram_observation") is True:
        for region in regions:
            if not isinstance(region, dict) or (region.get("size"), region.get("bank_first"), region.get("bank_last")) not in \
                    RAM_BANK_RANGES.get(region.get("ram"), ()):
                raise ValueError("unconfirmed RAM region mapping")
    elif regions:
        raise ValueError("RAM regions emitted while RAM observation is disabled")
    if len(events) > capacity or end.get("events") != len(events):
        raise ValueError("ring event count mismatch")
    if (type(end.get("dropped")) is not int or end["dropped"] < 0 or
            type(end.get("trace_events_dropped")) is not int or end["trace_events_dropped"] < 0 or
            end.get("overflow") is not (end["dropped"] > 0 or end["trace_events_dropped"] > 0)):
        raise ValueError("invalid overflow accounting")
    validate_ram_deltas(end.get("ram_deltas"), cap["frames"])
    for event in events:
        if event.get("kind") not in EVENT_KINDS or type(event.get("frame")) is not int or not 1 <= event["frame"] <= cap["frames"]:
            raise ValueError("invalid event header")
        if event["kind"] == "region":
            if (not all(type(event.get(key)) is int for key in ("event_cycle", "event_pc", "event_bank")) or
                    not 0 <= event["event_pc"] <= 0xffff or not 0 <= event["event_bank"] <= 0xff or
                    event.get("from_region") not in CPU_REGIONS or event.get("to_region") not in CPU_REGIONS or
                    type(event.get("trace_gap")) is not bool):
                raise ValueError("invalid event-time CPU region transition")
            if event["from_region"] == "unknown" and event["trace_gap"] is False:
                # The first retained event establishes a baseline; it is not a
                # loss claim.  Later unknown transitions must disclose loss.
                continue
            continue
        if (type(event.get("pc")) is not int or type(event.get("physical_pc")) is not int or
                type(event.get("pc_bank")) is not int or type(event.get("mpr")) is not list or len(event["mpr"]) != 8):
            raise ValueError("event lacks CPU/MPR snapshot")
        if any(type(value) is not int or not 0 <= value <= 255 for value in event["mpr"]):
            raise ValueError("invalid MPR snapshot")
        if event["kind"] == "ram":
            if cap.get("ram_observation") is not True:
                raise ValueError("RAM event emitted while RAM observation is disabled")
            ram, size = event.get("ram"), event.get("size")
            if ram not in RAM_BANK_RANGES or type(size) is not int:
                raise ValueError("invalid RAM classification")
            if (size, event.get("bank_first"), event.get("bank_last")) not in RAM_BANK_RANGES[ram]:
                raise ValueError("unconfirmed RAM bank mapping")
            if not all(type(event.get(key)) is int for key in ("changed", "first_offset", "last_offset")):
                raise ValueError("invalid RAM change event")
            if not 1 <= event["changed"] <= size or not 0 <= event["first_offset"] <= event["last_offset"] < size:
                raise ValueError("invalid RAM change range")
        if event["kind"] == "cd":
            if event.get("snapshot") != "FRAME_SNAPSHOT":
                raise ValueError("SCSI metadata lacks FRAME_SNAPSHOT label")
            if not all(type(event.get(key)) is int for key in ("cd_event", "command", "lba", "status")):
                raise ValueError("invalid CD event")
    return cap, events, end


FRAME_STATE_KEYS = ("master_clock", "pc", "pc_bank", "a", "x", "y", "p", "sp", "cd_phase", "cd_signals",
                    "cd_load_sector", "cd_load_count", "cd_irq_active", "cd_irq_enabled")


def validate_state_row(row):
    """Frame-boundary state from one run.  Only register, CD-register, hash and count fields."""
    for key in FRAME_STATE_KEYS:
        value = row.get(key)
        if type(value) is not int or (key != "cd_phase" and value < 0):
            raise ValueError(f"invalid state field {key}")
    value = row.get("ram_hash")
    if type(value) is not str or len(value) != 16 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("invalid RAM state hash")
    mpr = row.get("mpr")
    if type(mpr) is not list or len(mpr) != 8 or any(type(v) is not int or not 0 <= v <= 0xFF for v in mpr):
        raise ValueError("invalid MPR state")
    changed = row.get("ram_changed")
    if type(changed) is not dict or set(changed) != set(RAM_BANK_RANGES) or \
            any(type(v) is not int or v < 0 for v in changed.values()):
        raise ValueError("invalid RAM change counts")


def parse_frame_records(stdout, expected_mode, frames):
    """Parse per-frame STATE and SCSI rows.  Any missing, duplicate or malformed row fails closed."""
    cap_row, states, scsi = None, {}, {}
    for line in stdout.splitlines():
        tag, separator, body = line.partition(" ")
        if tag == "CAP":
            cap_row = json.loads(body) if separator else None
            continue
        if tag not in {"STATE", "SCSI"}:
            continue
        if not separator:
            raise ValueError(f"{tag} missing JSON body")
        row = json.loads(body)
        if type(row) is not dict:
            raise ValueError(f"{tag} body is not an object")
        frame = row.get("frame")
        if type(frame) is not int or not 1 <= frame <= frames:
            raise ValueError(f"{tag} frame is outside the run")
        target = states if tag == "STATE" else scsi
        if frame in target:
            raise ValueError(f"duplicate {tag} frame")
        target[frame] = row
    if type(cap_row) is not dict or cap_row.get("trace_mode") != expected_mode or \
            cap_row.get("cpu_event_notification") is not (expected_mode == "full"):
        raise ValueError("trace mode does not match the requested run")
    expected = set(range(1, frames + 1))
    if set(states) != expected or set(scsi) != expected:
        raise ValueError("missing per-frame records")
    for row in states.values():
        validate_state_row(row)
    for row in scsi.values():
        if type(row.get("events")) is not int or row["events"] < 0 or type(row.get("incomplete")) is not bool:
            raise ValueError("invalid SCSI notification row")
    return states, scsi


def compare_runs(states_a, scsi_a, states_b, scsi_b, frames):
    """Run A and Run B must agree on every frame-boundary state and SCSI notification count."""
    for frame in range(1, frames + 1):
        if states_a[frame] != states_b[frame]:
            return False, f"frame {frame}: frame-boundary state differs between Run A and Run B"
        if scsi_a[frame]["events"] != scsi_b[frame]["events"]:
            return False, f"frame {frame}: SCSI notification count differs between runs"
    return True, "Run A and Run B agree on every frame-boundary state and SCSI notification count"


def apply_scsi_correlation(rows, states_a, scsi_b, frames):
    """Set SCSI correlation from Run B only.  Unknown inputs yield UNKNOWN, never OBSERVED.

    A frame counts toward a region only if Run A changed that region's bytes in that frame.
    An incomplete Run B SCSI observation on such a frame makes the region UNKNOWN.  Output-ring
    overflow is not an input here, and no CPU-trace entry is read.
    """
    for row in rows:
        region = row.get("region")
        row["scsi_update_frames"] = []
        if states_a is None or scsi_b is None or row.get("availability") != "PRESENT":
            row["scsi_correlation"] = "UNKNOWN"
            continue
        changed = [f for f in range(1, frames + 1) if states_a[f]["ram_changed"].get(region, 0)]
        if any(scsi_b[f]["incomplete"] for f in changed):
            row["scsi_correlation"] = "UNKNOWN"
            continue
        hits = [f for f in changed if scsi_b[f]["events"] > 0]
        row["scsi_correlation"] = "OBSERVED" if hits else "NONE_OBSERVED"
        row["scsi_update_frames"] = hits
    validate_ram_deltas(rows, frames)


def compare_and_correlate(stdout_a, stdout_b, end, frames):
    """Combine Run A (full) and Run B (SCSI-only).  Any failure marks correlation UNKNOWN."""
    states_a = scsi_b = None
    try:
        states_a, scsi_a = parse_frame_records(stdout_a, "full", frames)
        states_b, scsi_b = parse_frame_records(stdout_b, "scsi", frames)
        comparable, reason = compare_runs(states_a, scsi_a, states_b, scsi_b, frames)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        comparable, reason = False, f"dual-run rejected: {error}"
    if not comparable:
        states_a = scsi_b = None
    try:
        apply_scsi_correlation(end["ram_deltas"], states_a, scsi_b, frames)
    except (ValueError, TypeError, KeyError) as error:
        for row in end.get("ram_deltas", []):
            row["scsi_correlation"], row["scsi_update_frames"] = "UNKNOWN", []
        comparable, reason = False, f"correlation rejected: {error}"
    return {"status": "PASS" if comparable else "UNKNOWN", "reason": reason, "frames": frames,
            "run_a": "full (CPU+SCSI+RAM)", "run_b": "scsi-only (SCSI notification, CPU trace disabled)"}


def trace(args):
    if not 1 <= args.frames <= MAX_FRAMES or not 1 <= args.events <= MAX_EVENTS:
        return {"status": "FAIL", "reason": f"limits are frames 1..{MAX_FRAMES}, events 1..{MAX_EVENTS}",
                "evidence": "NOT_RUN"}
    try:
        schedule = run_schedule(args.run_start_frame, args.run_hold_frames, args.frames)
    except ValueError as error:
        return {"status": "FAIL", "reason": str(error), "evidence": "NOT_RUN"}
    # Do not invoke doctor() on caller media: it records input paths and hashes.
    # The core/tool readiness result is retained without private input metadata.
    report = doctor(None, None, None)
    log = {"schema_version": 1, "scope": "normal-frame core observation; not BIOS boot or compatibility evidence",
           "artifact_policy": "LOCAL_ONLY: never upload traces; events contain no guest bytes",
           "core_readiness": report["status"], "frames": args.frames, "ring_capacity": args.events,
           "run_input": {"requested": bool(schedule), "schedule": schedule}}
    if report["status"] != "PASS":
        log.update(status=report["status"], reason="doctor prerequisites", evidence="NOT_RUN")
        return log
    try:
        job, job_identity = make_tracked_job_dir()
    except (OSError, RuntimeError) as error:
        log.update(status="BLOCKED", reason=str(error), evidence="NOT_RUN")
        return log
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
        result = run_job([str(probe), str(rom), cue, str(args.frames), str(args.events),
                           str(args.run_start_frame or 0), str(args.run_hold_frames or 0),
                           "1" if args.observe_ram else "0"], job, args.timeout)
        if result["status"]:
            log.update(status=result["status"], reason=result["reason"], evidence="NOT_RUN")
            return log
        if result["exit_code"] != 0:
            log.update(status="FAIL", reason=f"probe exited {result['exit_code']}", evidence="NOT_RUN")
            return log
        try:
            cap, events, end = parse_records(result["stdout"], require_ram_observation=args.observe_ram)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            log.update(status="FAIL", reason=f"trace rejected: {error}", evidence="NOT_RUN")
            return log
        dual = {"status": "NOT_RUN", "reason": "RAM observation disabled: no SCSI correlation claimed"}
        if args.observe_ram:
            # Run B: a separate process, CPU trace category disabled, so Run A's shared
            # bounded buffer never decides the SCSI correlation.
            run_b = run_job([str(probe), str(rom), cue, str(args.frames), str(args.events),
                             str(args.run_start_frame or 0), str(args.run_hold_frames or 0), "1", "scsi"],
                            job, args.timeout)
            if run_b["status"] or run_b["exit_code"] != 0:
                log.update(status="FAIL", reason="SCSI-only run did not complete", evidence="NOT_RUN")
                return log
            dual = compare_and_correlate(result["stdout"], run_b["stdout"], end, args.frames)
            dual["elapsed_s_run_a"], dual["elapsed_s_run_b"] = result["elapsed_s"], run_b["elapsed_s"]
        log.update(status="PASS", reason="bounded normal-frame trace completed", evidence="REAL_GEARGRAFX_CORE",
                   elapsed_s=result["elapsed_s"], capability=cap, events=events, summary=end,
                   scsi_correlation_basis=dual)
        return log
    finally:
        cleaned, cleanup_reason = cleanup_tracked_job_dir(job, job_identity)
        log["cleanup"] = "REMOVED" if cleaned else f"FAILED_{cleanup_reason.upper()}"
        if not cleaned:
            log.update(status="FAIL", reason=f"cleanup failed: {cleanup_reason}", evidence="NOT_RUN")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--cue", type=Path)
    parser.add_argument("--frames", type=int, default=2)
    parser.add_argument("--events", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--run-start-frame", type=int, help="frame before which to press RUN via Geargrafx's public controller API")
    parser.add_argument("--run-hold-frames", type=int, help="number of complete frames for which RUN is held")
    parser.add_argument("--no-observe-ram", dest="observe_ram", action="store_false",
                        help="disable RAM snapshots; useful only for observer A/B diagnostics")
    parser.set_defaults(observe_ram=True)
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
