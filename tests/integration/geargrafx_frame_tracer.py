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
# A bound computed only from entries that were actually read.  It is never the measured quantity
# itself and lives in its own row, so a reader cannot take it for the exact value.
LOWER_BOUND = "LOWER_BOUND"
MEASUREMENT_NAMES = ("core_capacity", "output_ring_dropped", "max_frame_core_entries",
                     "max_frame_scsi_events", "core_trace_dropped_frames")
# A1 PR-C: present only when max_frame_scsi_events is NOT_MEASURED.
SCSI_LOWER_BOUND = "scsi_events_lower_bound"
# Quantities that stay NOT_MEASURED for every tracer output format (none since PR-C).
NOT_MEASURED_REASONS = {}
# What END trace_api_guest_checks/trace_api_guest_changes do and do not show (see docs/frame-tracer-measurements.md).
GUEST_STATE_CHECK_SCOPE = (
    "guest CPU registers, MPRs, master clock, timer, IDR/IRR, CD/SCSI state and observed RAM compared only "
    "immediately before and after the tracer's own TraceLogger API calls (setup, then once per frame); this is "
    "not an observer non-interference claim: enabling core tracing inside RunToVBlank is not covered")
# How to read the validated END allowlist kept in the log as "summary".
SUMMARY_SCOPE = (
    "allowlist-filtered, validated END fields (unknown fields are rejected fail-closed); only 'measurements' carries a status. "
    "summary.max_frame_core_entries is the retained (post-clamp) maximum, whereas "
    "measurements.max_frame_core_entries is the pre-clamp maximum; summary.max_frame_scsi_events is exact only "
    "when measurements.max_frame_scsi_events is MEASURED, otherwise it is the scsi_events_lower_bound value")
SCSI_EXACT_SOURCE = ("SCSI entries read with GetEntry() per frame; exact only because every logged core entry was "
                     "read (no core-trace gap frame and no discontinuous frame)")
SCSI_LOWER_BOUND_SOURCE = ("largest per-frame count of SCSI entries that were actually read with GetEntry(); "
                           "SCSI entries overwritten before being read are not counted, so the true maximum is "
                           "at least this value")
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
        scsi_reason = "pre-PR-B tracer output: core-trace gaps and discontinuities cannot be ruled out"
    elif core["discontinuity_frames"]:
        reason = (f"core trace counters were discontinuous in {core['discontinuity_frames']} of {end['frames']} "
                  "frame(s) (unexpected Reset, capacity change or retained-count mismatch); per-frame counts for "
                  "those frames are unknown")
        for name in CORE_TRACE_MEASURED:
            table[name] = {"status": NOT_MEASURED, "reason": reason}
        scsi_reason = reason
    else:
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
        scsi_reason = None if not core["gap_frames"] else (
            f"{core['gap_frames']} of {end['frames']} frame(s) lost {core['lost']} core entries before they were read; "
            "how many of those were SCSI entries is unknown")
    # Fail-closed: the SCSI maximum is exact only when nothing logged was lost or skipped.
    if scsi_reason is None:
        table["max_frame_scsi_events"] = {
            "status": MEASURED, "value": end["max_frame_scsi_events"], "kind": "count",
            "unit": "SCSI trace entries (commands, phases, responses filter) logged in one frame",
            "frames": core["frames_accounted"], "source": SCSI_EXACT_SOURCE}
    else:
        table["max_frame_scsi_events"] = {"status": NOT_MEASURED, "reason": scsi_reason}
        table[SCSI_LOWER_BOUND] = {
            "status": LOWER_BOUND, "value": end["max_frame_scsi_events"], "bound_of": "max_frame_scsi_events",
            "kind": "lower_bound", "source": SCSI_LOWER_BOUND_SOURCE}
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
    if type(rows) is not list or len(rows) != 3 or \
            any(type(row) is not dict or type(row.get("region")) is not str for row in rows) or \
            {row.get("region") for row in rows} != set(RAM_BANK_RANGES):
        raise ValueError("invalid RAM region summary")
    for row in rows:
        if type(row) is not dict:
            raise ValueError("invalid RAM region entry")
        region, present, availability = row.get("region"), row.get("present"), row.get("availability")
        if type(availability) is not str or availability not in {"PRESENT", "ABSENT", "UNKNOWN"}:
            raise ValueError("invalid RAM availability")
        if (availability == "PRESENT") != (present is True) or (availability == "ABSENT") != (present is False) or \
                (availability == "UNKNOWN") != (present is None):
            raise ValueError("RAM availability is not distinct")
        updates, max_changed = row.get("updates"), row.get("max_changed")
        if type(updates) is not int or updates < 0 or type(max_changed) is not int or max_changed < 0:
            raise ValueError("invalid RAM update count")
        # Correlation is validated for every availability; PRESENT and unavailable rows
        # alike are published by _public_end(), so a private string in any row must not
        # survive as a public field. Missing values fail the membership/length checks.
        correlation, scsi_frames = row.get("scsi_correlation"), row.get("scsi_update_frames")
        if type(correlation) is not str or correlation not in {"OBSERVED", "NONE_OBSERVED", "UNKNOWN"} or type(scsi_frames) is not list or \
                any(type(frame) is not int or not 1 <= frame <= frames for frame in scsi_frames) or scsi_frames != sorted(set(scsi_frames)):
            raise ValueError("invalid SCSI frame correlation")
        if correlation == "OBSERVED" and not scsi_frames:
            raise ValueError("observed SCSI correlation lacks frames")
        if correlation == "NONE_OBSERVED" and scsi_frames:
            raise ValueError("empty SCSI correlation has frames")
        if availability != "PRESENT" and (scsi_frames or correlation == "OBSERVED"):
            raise ValueError("unavailable RAM has SCSI updates")
        range_keys = ("first_frame", "last_frame", "min_offset", "max_offset")
        if availability != "PRESENT":
            # C++ emits size/bank_first/bank_last only for present RAM; a present value
            # here (e.g. a private string) must not survive into the public summary.
            if any(row.get(key) is not None for key in ("size", "bank_first", "bank_last")):
                raise ValueError("unavailable RAM includes bank mapping")
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


REGION_KINDS = {"baseline", "after_gap", "transition"}
WARM_RESET_API = "GeargrafxCore::ResetMedia(true)"
REQUESTED_WARM_RESET = "requested_warm_reset"

# Public-output allowlists (#635): CAP/EVENT/END/RESET/INPUT are copied into the
# published log. Unknown fields are rejected fail-closed so a future tracer change
# or injected key (private path, media name, hash, arbitrary string) cannot leak.
CAP_ALLOWED = {
    "normal_frame_execution", "instruction_step", "guest_mutation", "mmio_reads",
    "scsi_event_notification", "cpu_event_notification", "ram_observation", "ram_delta_summary",
    "ram_regions", "ring_capacity", "frames", "disc_given", "run_start_frame", "run_hold_frames",
    "run_input_requested", "core_capacity_requested", "trace_api_guest_ram_checked",
    "region_gap_carry", "warm_reset_frame",
}
CAP_REGION_ALLOWED = {"ram", "size", "bank_first", "bank_last"}
INPUT_ALLOWED = {"frame", "action", "run_pressed"}
RESET_ALLOWED = {"frame", "kind", "api", "performed", "master_clock_reset", "ram_preserved"}
END_ALLOWED = {
    "frames", "events", "dropped", "trace_events_dropped", "overflow", "ram_deltas", "pushed",
    "core_capacity", "max_frame_core_entries", "max_frame_scsi_events",
    "trace_api_guest_checks", "trace_api_guest_changes", "core_trace",
}
RAM_DELTA_ALLOWED = {
    "region", "present", "availability", "size", "bank_first", "bank_last", "updates",
    "first_frame", "last_frame", "min_offset", "max_offset", "max_changed",
    "scsi_correlation", "scsi_update_frames",
}
EVENT_BASE_ALLOWED = {"kind", "frame"}
EVENT_CPU_ALLOWED = {
    "master_clock", "pc", "pc_page", "pc_bank", "physical_pc", "pc_is_wram", "a", "x", "y", "p", "sp", "mpr",
}
EVENT_CD_ALLOWED = {
    "snapshot", "cd_event", "command", "lba", "status", "scsi_phase", "scsi_signals",
    "load_sector", "load_count", "cd_irq_active", "cd_irq_enabled",
}
EVENT_RAM_ALLOWED = {"ram", "size", "changed", "first_offset", "last_offset", "bank_first", "bank_last"}
EVENT_REGION_ALLOWED = {
    "event_cycle", "event_pc", "event_bank", "from_region", "to_region", "trace_gap", "region_kind",
}


def _reject_unknown(row, allowed, record):
    if type(row) is not dict:
        raise ValueError(f"{record} body is not an object")
    if set(row) - allowed:
        raise ValueError(f"{record} contains unknown fields")


def _public_row(row, allowed):
    """Allowlist copy for the published log (defence in depth behind parse_records)."""
    return {key: row[key] for key in allowed if key in row}


def _public_cap(cap):
    public = _public_row(cap, CAP_ALLOWED)
    public["ram_regions"] = [_public_row(region, CAP_REGION_ALLOWED)
                             for region in cap.get("ram_regions", []) if type(region) is dict]
    return public


def _public_end(end):
    public = _public_row(end, END_ALLOWED)
    if type(end.get("core_trace")) is dict:
        public["core_trace"] = _public_row(end["core_trace"], CORE_TRACE_KEYS)
    if type(end.get("ram_deltas")) is list:
        public["ram_deltas"] = [_public_row(row, RAM_DELTA_ALLOWED)
                                for row in end["ram_deltas"] if type(row) is dict]
    return public


def _public_event(event):
    kind = event.get("kind")
    if kind == "region":
        allowed = EVENT_BASE_ALLOWED | EVENT_REGION_ALLOWED
    elif kind == "cd":
        allowed = EVENT_BASE_ALLOWED | EVENT_CPU_ALLOWED | EVENT_CD_ALLOWED
    elif kind == "ram":
        allowed = EVENT_BASE_ALLOWED | EVENT_CPU_ALLOWED | EVENT_RAM_ALLOWED
    else:
        allowed = EVENT_BASE_ALLOWED | EVENT_CPU_ALLOWED
    return _public_row(event, allowed)


def validate_regions_and_resets(cap, end, events, resets, has_core_trace):
    """A1 PR-C: REGION classification and the optional warm reset, checked within one run.

    The tracer carries trace_gap across frames until the next REGION event, so "unknown" with
    trace_gap=false can only be the run's single baseline.  Pre-PR-C output (no region_gap_carry)
    is accepted without these checks, and must then contain no PR-C records.
    """
    regions = [event for event in events if event["kind"] == "region"]
    pr_c = {key for key in ("region_gap_carry", "warm_reset_frame") if key in cap}
    if not pr_c:
        if resets or any("region_kind" in event for event in regions):
            raise ValueError("PR-C records without the PR-C capability")
        return
    if pr_c != {"region_gap_carry", "warm_reset_frame"}:
        raise ValueError("PR-C capability fields incomplete")
    if not has_core_trace:
        raise ValueError("PR-C output lacks the PR-B core trace accounting")
    if cap["region_gap_carry"] is not True:
        raise ValueError("REGION gap carry capability missing")
    frames, warm = cap["frames"], cap["warm_reset_frame"]
    if type(warm) is not int or not (warm == 0 or 2 <= warm <= frames):
        raise ValueError("invalid warm reset frame")
    expected = [] if not warm else [{"frame": warm, "kind": "warm", "api": WARM_RESET_API, "performed": True,
                                     "master_clock_reset": True, "ram_preserved": "backup_ram_only"}]
    if resets != expected:
        raise ValueError("warm reset record does not match the requested reset")
    # The reset frame's RAM delta includes the reset's own writes: it is never attributed to SCSI.
    if warm and any(warm in row.get("scsi_update_frames", ()) for row in end["ram_deltas"]):
        raise ValueError("warm reset frame attributed as a SCSI-correlated RAM update")
    core = end["core_trace"]
    gaps_possible = core["gap_frames"] > 0 or core["discontinuity_frames"] > 0
    for index, event in enumerate(regions):
        kind, gap, from_unknown = event.get("region_kind"), event["trace_gap"], event["from_region"] == "unknown"
        if type(kind) is not str or kind not in REGION_KINDS or event["to_region"] == "unknown":
            raise ValueError("invalid REGION classification")
        if kind != ("transition" if not from_unknown else "after_gap" if gap else "baseline") or \
                (kind == "transition" and gap):
            raise ValueError("REGION classification disagrees with from_region/trace_gap")
        if kind == "baseline" and index != 0:
            raise ValueError("a REGION baseline after the first REGION event hides a gap")
        if kind == "after_gap" and not gaps_possible:
            raise ValueError("REGION gap without any core-trace gap or discontinuity")
    if regions and end["dropped"] == 0 and regions[0]["from_region"] != "unknown":
        raise ValueError("first REGION event of a complete output ring does not start from unknown")


def parse_records(stdout, require_ram_observation=True):
    """Validate the small public-safe protocol emitted by the C++ tracer."""
    cap = end = None
    inputs = []
    events = []
    resets = []
    for line in stdout.splitlines():
        tag, separator, body = line.partition(" ")
        if tag not in {"CAP", "INPUT", "EVENT", "END", "RESET"}:
            continue
        if not separator:
            raise ValueError(f"{tag} missing JSON body")
        row = json.loads(body)
        if type(row) is not dict:
            raise ValueError(f"{tag} body is not an object")
        if tag == "CAP":
            if cap is not None:
                raise ValueError("duplicate CAP")
            _reject_unknown(row, CAP_ALLOWED, "CAP")
            cap = row
        elif tag == "INPUT":
            _reject_unknown(row, INPUT_ALLOWED, "INPUT")
            inputs.append(row)
        elif tag == "RESET":
            _reject_unknown(row, RESET_ALLOWED, "RESET")
            resets.append(row)
        elif tag == "EVENT":
            events.append(row)
        else:
            if end is not None:
                raise ValueError("duplicate END")
            _reject_unknown(row, END_ALLOWED, "END")
            end = row
    if cap is None or end is None:
        raise ValueError("missing CAP or END")
    # Required CAP frames are validated before any RUN/INPUT/EVENT code indexes cap["frames"].
    if type(cap.get("frames")) is not int or not 1 <= cap["frames"] <= MAX_FRAMES:
        raise ValueError("invalid frame count")
    if cap.get("normal_frame_execution") is not True or cap.get("instruction_step") is not False:
        raise ValueError("not a normal-frame trace")
    if type(cap.get("ram_observation")) is not bool:
        raise ValueError("invalid RAM observation capability")
    if type(cap.get("disc_given")) is not bool:
        raise ValueError("invalid disc_given capability")
    # The tracer never writes guest state; the only exception is a requested, recorded warm reset,
    # which must be declared as such (checked against the RESET record in validate_regions_and_resets).
    warm_requested = type(cap.get("warm_reset_frame")) is int and cap["warm_reset_frame"] > 0
    allowed_mutation = REQUESTED_WARM_RESET if warm_requested else False
    mutation = cap.get("guest_mutation")
    if (type(mutation) is not type(allowed_mutation) or mutation != allowed_mutation or
            cap.get("mmio_reads") is not False or
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
    if type(start) is not int or type(hold) is not int or start < 0 or hold < 0:
        raise ValueError("invalid RUN schedule fields")
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
            if not isinstance(region, dict) or type(region.get("ram")) is not str or \
                    (region.get("size"), region.get("bank_first"), region.get("bank_last")) not in \
                    RAM_BANK_RANGES.get(region.get("ram"), ()):
                raise ValueError("unconfirmed RAM region mapping")
            _reject_unknown(region, CAP_REGION_ALLOWED, "CAP ram_regions entry")
    elif regions:
        raise ValueError("RAM regions emitted while RAM observation is disabled")
    if type(end.get("events")) is not int or len(events) > capacity or end["events"] != len(events):
        raise ValueError("ring event count mismatch")
    # PR-B: a discontinuous core-trace frame lost an unknown number of entries, so it must also be
    # reported as overflow (trace_events_dropped counts only the losses that are known exactly).
    core = end.get("core_trace")
    discontinuous = type(core) is dict and type(core.get("discontinuity_frames")) is int and \
        core["discontinuity_frames"] > 0
    if (type(end.get("dropped")) is not int or end["dropped"] < 0 or
            type(end.get("trace_events_dropped")) is not int or end["trace_events_dropped"] < 0 or
            end.get("overflow") is not (end["dropped"] > 0 or end["trace_events_dropped"] > 0 or discontinuous)):
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
    has_core_trace = validate_core_trace(cap, end)
    if core_capacity > MAX_CORE_CAPACITY:
        raise ValueError("invalid core trace capacity")
    validate_ram_deltas(end.get("ram_deltas"), cap["frames"])
    for row in end["ram_deltas"]:
        _reject_unknown(row, RAM_DELTA_ALLOWED, "END ram_deltas entry")
    for row in inputs:
        if type(row.get("frame")) is not int or not 1 <= row["frame"] <= cap["frames"] or \
                type(row.get("action")) is not str or row.get("action") not in {"press", "release"} or type(row.get("run_pressed")) is not bool:
            raise ValueError("invalid INPUT record")
    for row in resets:
        # Type-only here; value mismatches are reported by validate_regions_and_resets
        # ("does not match the requested"). Strict bool/int keeps True distinct from 1.
        if type(row.get("frame")) is not int or type(row.get("kind")) is not str or \
                type(row.get("api")) is not str or type(row.get("performed")) is not bool or \
                type(row.get("master_clock_reset")) is not bool or type(row.get("ram_preserved")) is not str or \
                not 1 <= row["frame"] <= cap["frames"]:
            raise ValueError("invalid RESET record")
    for event in events:
        if type(event.get("kind")) is not str or event.get("kind") not in EVENT_KINDS or type(event.get("frame")) is not int or not 1 <= event["frame"] <= cap["frames"]:
            raise ValueError("invalid event header")
        kind = event.get("kind")
        if kind == "region":
            _reject_unknown(event, EVENT_BASE_ALLOWED | EVENT_REGION_ALLOWED, "EVENT")
        elif kind == "cd":
            _reject_unknown(event, EVENT_BASE_ALLOWED | EVENT_CPU_ALLOWED | EVENT_CD_ALLOWED, "EVENT")
        elif kind == "ram":
            _reject_unknown(event, EVENT_BASE_ALLOWED | EVENT_CPU_ALLOWED | EVENT_RAM_ALLOWED, "EVENT")
        else:
            _reject_unknown(event, EVENT_BASE_ALLOWED | EVENT_CPU_ALLOWED, "EVENT")
        if event["kind"] == "region":
            if (not all(type(event.get(key)) is int for key in ("event_cycle", "event_pc", "event_bank")) or
                    not 0 <= event["event_pc"] <= 0xffff or not 0 <= event["event_bank"] <= 0xff or
                    type(event.get("from_region")) is not str or type(event.get("to_region")) is not str or
                    event.get("from_region") not in CPU_REGIONS or event.get("to_region") not in CPU_REGIONS or
                    type(event.get("trace_gap")) is not bool):
                raise ValueError("invalid event-time CPU region transition")
            if event["from_region"] == "unknown" and event["trace_gap"] is False:
                # The first retained event establishes a baseline; it is not a
                # loss claim.  Later unknown transitions must disclose loss.
                continue
            continue
        if (type(event.get("pc")) is not int or not 0 <= event["pc"] <= 0xffff or
                type(event.get("physical_pc")) is not int or not 0 <= event["physical_pc"] <= 0x1fffff or
                type(event.get("pc_bank")) is not int or not 0 <= event["pc_bank"] <= 0xff or
                type(event.get("mpr")) is not list or len(event["mpr"]) != 8):
            raise ValueError("event lacks CPU/MPR snapshot")
        if type(event.get("master_clock")) is not int or event["master_clock"] < 0 or \
                type(event.get("pc_page")) is not int or not 0 <= event["pc_page"] <= 0xff or \
                type(event.get("pc_is_wram")) is not bool or \
                any(type(event.get(key)) is not int or not 0 <= event[key] <= 0xff for key in ("a", "x", "y", "p", "sp")):
            raise ValueError("invalid event CPU snapshot")
        if any(type(value) is not int or not 0 <= value <= 255 for value in event["mpr"]):
            raise ValueError("invalid MPR snapshot")
        if event["kind"] == "ram":
            if cap.get("ram_observation") is not True:
                raise ValueError("RAM event emitted while RAM observation is disabled")
            ram, size = event.get("ram"), event.get("size")
            if type(ram) is not str or ram not in RAM_BANK_RANGES or type(size) is not int:
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
            # C++ also emits the CD snapshot (scsi_phase etc.). Host synthetics omit it;
            # when present it must be an int, never a private string.
            if "scsi_phase" in event and type(event["scsi_phase"]) is not int:
                raise ValueError("invalid CD snapshot")
            for key in ("scsi_signals", "load_sector", "load_count", "cd_irq_active", "cd_irq_enabled"):
                if key in event and (type(event[key]) is not int or event[key] < 0):
                    raise ValueError("invalid CD snapshot")
    validate_regions_and_resets(cap, end, events, resets, has_core_trace)
    return cap, events, end


def trace(args):
    # Callers that predate PR-B pass no core_capacity: keep the pinned core's default buffer.
    core_capacity = getattr(args, "core_capacity", None)
    # Callers that predate PR-C pass no warm_reset_frame: a cold-boot-only run.
    warm_reset = getattr(args, "warm_reset_frame", None)
    if not 1 <= args.frames <= MAX_FRAMES or not 1 <= args.events <= MAX_EVENTS or \
            (core_capacity is not None and (type(core_capacity) is not int or
                                            not 1 <= core_capacity <= MAX_CORE_CAPACITY)) or \
            (warm_reset is not None and (type(warm_reset) is not int or not 2 <= warm_reset <= args.frames)):
        return {"status": "FAIL", "reason": f"limits are frames 1..{MAX_FRAMES}, events 1..{MAX_EVENTS}, "
                                            f"core capacity 1..{MAX_CORE_CAPACITY}, warm reset frame 2..frames",
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
    except (OSError, RuntimeError):
        # Never leak the OS error text (job paths) into the public log.
        log.update(status="BLOCKED", reason="job directory creation failed", evidence="NOT_RUN")
        return log
    try:
        try:
            probe = compile_probe(job, "geargrafx_frame_tracer.cpp", "geargrafx-frame-tracer")
        except Exception:
            # ProbeBuildError carries cmd/stderr with absolute build paths; keep it local-only.
            log.update(status="BLOCKED", reason="probe build failed", evidence="NOT_RUN")
            return log
        rom = job / "input.pce"
        try:
            shutil.copyfile(args.rom, rom)
        except OSError:
            # Missing/unreadable ROM or copy I/O error: never publish the OS error text
            # (absolute paths, media names). The finally block still runs cleanup, and
            # a failed cleanup flips this to FAIL (fail-closed).
            log.update(status="BLOCKED", reason="ROM copy failed", evidence="NOT_RUN")
            return log
        cue = "-"
        if args.cue is not None:
            disc = job / "disc"
            try:
                disc.mkdir()
                staged = stage_disc(args.cue, disc)
            except FileNotFoundError:
                log.update(status="BLOCKED", reason="disc staging input missing", evidence="NOT_RUN")
                return log
            except ValueError:
                log.update(status="FAIL", reason="invalid disc input", evidence="NOT_RUN")
                return log
            except OSError:
                log.update(status="BLOCKED", reason="disc staging failed", evidence="NOT_RUN")
                return log
            cue = str(disc / staged[0])
        result = run_job([str(probe), str(rom), cue, str(args.frames), str(args.events),
                           str(args.run_start_frame or 0), str(args.run_hold_frames or 0),
                           "1" if args.observe_ram else "0", str(core_capacity or 0), str(warm_reset or 0)],
                          job, args.timeout)
        if result["status"]:
            # run_job "cannot execute: <OSError>" embeds a command path; publish a fixed code.
            reason = result["reason"]
            if type(reason) is str and reason.startswith("cannot execute"):
                reason = "cannot execute probe"
            log.update(status=result["status"], reason=reason, evidence="NOT_RUN")
            return log
        if result["exit_code"] != 0:
            log.update(status="FAIL", reason=f"probe exited {result['exit_code']}", evidence="NOT_RUN")
            return log
        try:
            cap, events, end = parse_records(result["stdout"], require_ram_observation=args.observe_ram)
        except (ValueError, TypeError, json.JSONDecodeError):
            log.update(status="FAIL", reason="trace rejected", evidence="NOT_RUN")
            return log
        if cap.get("core_capacity_requested") != (core_capacity or 0):
            log.update(status="FAIL", reason="trace rejected: tracer did not apply the requested core capacity",
                       evidence="NOT_RUN")
            return log
        if cap.get("warm_reset_frame") != (warm_reset or 0):
            log.update(status="FAIL", reason="trace rejected: tracer did not apply the requested warm reset",
                       evidence="NOT_RUN")
            return log
        if cap.get("disc_given") != (args.cue is not None):
            log.update(status="FAIL", reason="trace rejected: tracer did not apply the requested disc input",
                       evidence="NOT_RUN")
            return log
        if cap.get("ram_observation") != args.observe_ram:
            log.update(status="FAIL", reason="trace rejected: tracer did not apply the requested RAM observation",
                       evidence="NOT_RUN")
            return log
        log.update(status="PASS", reason="bounded normal-frame trace completed", evidence="REAL_GEARGRAFX_CORE",
                   elapsed_s=result["elapsed_s"], capability=_public_cap(cap),
                   events=[_public_event(event) for event in events], summary=_public_end(end),
                   measurements=measurements(cap, end), guest_state_check_scope=GUEST_STATE_CHECK_SCOPE,
                   summary_scope=SUMMARY_SCOPE)
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
    parser.add_argument("--warm-reset-frame", type=int,
                        help="frame (2..frames) before which the core's public ResetMedia(true) warm reset runs")
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
