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
# Status words for the per-run measurement table.  A configuration value the core
# reports is never called MEASURED; a value the tracer cannot obtain exactly stays
# NOT_MEASURED, and its raw END field (if any) is not promoted to a measurement.
MEASURED = "MEASURED"
REPORTED_BY_CORE = "REPORTED_BY_CORE"
NOT_MEASURED = "NOT_MEASURED"
MEASUREMENT_NAMES = ("core_capacity", "output_ring_dropped", "max_frame_core_entries",
                     "max_frame_scsi_events", "core_trace_dropped_frames")
# Quantities that stay NOT_MEASURED for every tracer output format.
NOT_MEASURED_REASONS = {
    "max_frame_scsi_events": "counts only SCSI entries the core still retains; entries lost in a core-trace gap "
                             "are not counted (fail-closed handling and a lower bound are PR-C)",
}
# A1 PR-B: measured from the END core_trace accounting.  Without it (pre-PR-B output) or when the
# core counters were discontinuous, they stay NOT_MEASURED with these reasons.
CORE_TRACE_MEASURED = ("max_frame_core_entries", "core_trace_dropped_frames")
LEGACY_NOT_MEASURED_REASONS = {
    "max_frame_core_entries": "pre-PR-B tracer output: no core_trace accounting, and the legacy END field is "
                              "computed after the core-gap clamp, so it can under-count",
    "core_trace_dropped_frames": "pre-PR-B tracer output: no core_trace accounting; only the total of lost core "
                                 "entries (trace_events_dropped) is emitted",
}
CORE_TRACE_KEYS = {"total_logged", "sequence_delta", "logged_accounted", "consumed", "lost", "frames_accounted",
                   "discontinuity_frames", "gap_frames", "max_frame_logged"}
MAX_CORE_CAPACITY = 100000  # pinned core TRACE_BUFFER_SIZE (src/trace_logger.h at the pinned revision)
CORE_TRACE_SOURCE = ("pinned core TraceLogger::GetTotalLogged() delta between consecutive post-RunToVBlank "
                     "samples, cross-checked per frame with GetSequence(), GetCount() and GetCapacity()")


def measurements(cap, end):
    """Classify the five tracer quantities for one validated run (no estimates, no defaults)."""
    table = {
        "core_capacity": {"status": REPORTED_BY_CORE, "value": end["core_capacity"],
                          "requested": cap.get("core_capacity_requested"),
                          "kind": "configuration", "source": "pinned core TraceLogger::GetCapacity()"},
        "output_ring_dropped": {"status": MEASURED, "value": end["dropped"], "pushed": end["pushed"],
                                "retained": end["events"], "ring_capacity": cap["ring_capacity"],
                                "kind": "count", "source": "tracer output ring (pushed == retained + dropped)"},
    }
    if cap.get("core_capacity_requested") is None:
        del table["core_capacity"]["requested"]  # pre-PR-B output: the field was not emitted
    for name, reason in NOT_MEASURED_REASONS.items():
        table[name] = {"status": NOT_MEASURED, "reason": reason}
    core = end.get("core_trace")
    if core is None:
        for name, reason in LEGACY_NOT_MEASURED_REASONS.items():
            table[name] = {"status": NOT_MEASURED, "reason": reason}
        return table
    if core["discontinuity_frames"]:
        reason = (f"core trace counters were discontinuous in {core['discontinuity_frames']} of {end['frames']} "
                  "frame(s) (unexpected Reset, capacity change or retained-count mismatch); per-frame counts for "
                  "those frames are unknown")
        for name in CORE_TRACE_MEASURED:
            table[name] = {"status": NOT_MEASURED, "reason": reason}
        return table
    frames = core["frames_accounted"]
    table["max_frame_core_entries"] = {
        "status": MEASURED, "value": core["max_frame_logged"], "kind": "count",
        "unit": "core trace entries logged in one frame, before the capacity clamp",
        "frames": frames, "retained_max": end["max_frame_core_entries"],
        "counted_types": "entry types the tracer enables: TRACE_CPU and filtered TRACE_SCSI",
        "source": CORE_TRACE_SOURCE}
    table["core_trace_dropped_frames"] = {
        "status": MEASURED, "value": core["gap_frames"], "lost_entries": core["lost"], "kind": "count",
        "unit": "frames in which the core overwrote entries before the tracer read them",
        "frames": frames, "source": CORE_TRACE_SOURCE + "; lost = logged - capacity when positive"}
    return table


def validate_core_trace(cap, end):
    """Validate the PR-B core accounting; return False for a pre-PR-B (legacy) output.

    Every check is an invariant of a single run of the C++ accounting, never a comparison with
    another run.  Legacy output is accepted only when every PR-B field is absent.
    """
    pr_b = {"core_trace": end, "trace_api_guest_checks": end, "trace_api_guest_changes": end,
            "core_capacity_requested": cap, "trace_api_guest_ram_checked": cap}
    present = {key for key, row in pr_b.items() if key in row}
    if not present:
        return False
    if present != set(pr_b):
        raise ValueError(f"core trace accounting fields incomplete: missing {sorted(set(pr_b) - present)}")
    frames, capacity = cap["frames"], end["core_capacity"]
    requested = cap["core_capacity_requested"]
    if type(requested) is not int or not 0 <= requested <= MAX_CORE_CAPACITY:
        raise ValueError("invalid requested core trace capacity")
    if requested and capacity != requested:
        raise ValueError("core trace capacity differs from the requested capacity")
    if cap["trace_api_guest_ram_checked"] is not cap["ram_observation"]:
        raise ValueError("guest RAM check does not match RAM observation")
    checks, changes = end["trace_api_guest_checks"], end["trace_api_guest_changes"]
    if type(checks) is not int or type(changes) is not int or checks != frames + 1:
        raise ValueError("guest state was not checked around every trace API block")
    if changes != 0:
        raise ValueError("guest state changed across tracer trace API calls")
    core = end["core_trace"]
    if type(core) is not dict or set(core) != CORE_TRACE_KEYS or \
            any(type(core[key]) is not int or core[key] < 0 for key in CORE_TRACE_KEYS):
        raise ValueError("invalid core trace accounting fields")
    accounted, discontinuous, gaps = core["frames_accounted"], core["discontinuity_frames"], core["gap_frames"]
    logged, lost, consumed, max_logged = core["logged_accounted"], core["lost"], core["consumed"], core["max_frame_logged"]
    if accounted + discontinuous != frames or gaps > accounted:
        raise ValueError("core trace frame accounting does not cover every frame")
    if lost != end["trace_events_dropped"] or consumed + lost != logged:
        raise ValueError("core trace entry accounting mismatch (consumed + lost != logged)")
    if (gaps == 0) != (lost == 0):
        raise ValueError("core trace gap frames disagree with lost entries")
    if max_logged > logged or logged > accounted * max_logged:
        raise ValueError("per-frame core maximum is inconsistent with the logged total")
    # Each gap frame loses at most (its logged - capacity) <= (max_logged - capacity) entries.  With
    # lost >= 1 this also rejects a gap whose per-frame maximum does not exceed the capacity.
    if gaps and lost > gaps * (max_logged - capacity):
        raise ValueError("core trace loss is impossible for the per-frame maximum and capacity")
    if not gaps and max_logged > capacity:
        raise ValueError("a frame logged more than the core capacity without a gap")
    if end["max_frame_core_entries"] != min(max_logged, capacity):
        raise ValueError("retained per-frame maximum is not the clamped pre-clamp maximum")
    if not discontinuous and not core["total_logged"] == core["sequence_delta"] == logged:
        raise ValueError("core trace counters disagree (total_logged, sequence_delta, logged)")
    return True


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
    # Output ring: the independent push counter must equal retained + dropped, and a ring
    # that dropped anything must be full.  Otherwise the dropped count is not trusted.
    if type(end.get("pushed")) is not int or end["pushed"] != end["events"] + end["dropped"]:
        raise ValueError("output ring push accounting mismatch (pushed != events + dropped)")
    if end["dropped"] > 0 and end["events"] != capacity:
        raise ValueError("output ring dropped events while not full")
    # Core-buffer fields: required and internally consistent.  core_capacity is the core's
    # configured size; the two per-frame maxima are validated but not promoted (see measurements()).
    core_capacity = end.get("core_capacity")
    if type(core_capacity) is not int or core_capacity < 1:
        raise ValueError("invalid core trace capacity")
    max_entries, max_scsi = end.get("max_frame_core_entries"), end.get("max_frame_scsi_events")
    if type(max_entries) is not int or not 0 <= max_entries <= core_capacity:
        raise ValueError("invalid per-frame core entry field")
    if type(max_scsi) is not int or not 0 <= max_scsi <= max_entries:
        raise ValueError("invalid per-frame SCSI event field")
    if type(cap.get("frames")) is not int or not 1 <= cap["frames"] <= MAX_FRAMES or end.get("frames") != cap["frames"]:
        raise ValueError("invalid frame count")
    validate_core_trace(cap, end)
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


def trace(args):
    # Callers that predate PR-B pass no core_capacity: keep the pinned core's default buffer.
    core_capacity = getattr(args, "core_capacity", None)
    if not 1 <= args.frames <= MAX_FRAMES or not 1 <= args.events <= MAX_EVENTS or \
            (core_capacity is not None and (type(core_capacity) is not int or
                                            not 1 <= core_capacity <= MAX_CORE_CAPACITY)):
        return {"status": "FAIL", "reason": f"limits are frames 1..{MAX_FRAMES}, events 1..{MAX_EVENTS}, "
                                            f"core capacity 1..{MAX_CORE_CAPACITY}",
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
                           "1" if args.observe_ram else "0", str(core_capacity or 0)], job, args.timeout)
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
        if cap.get("core_capacity_requested") != (core_capacity or 0):
            log.update(status="FAIL", reason="trace rejected: tracer did not apply the requested core capacity",
                       evidence="NOT_RUN")
            return log
        log.update(status="PASS", reason="bounded normal-frame trace completed", evidence="REAL_GEARGRAFX_CORE",
                   elapsed_s=result["elapsed_s"], capability=cap, events=events, summary=end,
                   measurements=measurements(cap, end))
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
    parser.add_argument("--core-capacity", type=int,
                        help=f"core trace buffer entries (1..{MAX_CORE_CAPACITY}); default keeps the core's own")
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
