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
import os
import stat
import secrets

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
    _validate_evidence_mode(mode)
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
                       table["runs"][name]["core_trace_dropped_frames"]["value"],
                       scsi_text(table["runs"][name]))
                for name in ("observer_on", "observer_off")}
        print("PASS real Geargrafx synthetic CD-read + RAM observer: regions wram/cdrom_ram/card_ram; "
              "observed delta wram only; observer ON/OFF both completed (frame records not compared); no guest bytes; "
              f"core_capacity={table['core_capacity']['value']} (REPORTED_BY_CORE configuration); "
              f"output ring dropped ON={ring['observer_on']['value']}/{ring['observer_on']['pushed']} "
              f"OFF={ring['observer_off']['value']}/{ring['observer_off']['pushed']} pushed (MEASURED); "
              f"max_frame_core_entries ON={core['observer_on'][0]} OFF={core['observer_off'][0]} (MEASURED, pre-clamp); "
              f"core_trace_dropped_frames ON={core['observer_on'][1]} OFF={core['observer_off'][1]} (MEASURED); "
              f"max_frame_scsi_events ON={core['observer_on'][2]} OFF={core['observer_off'][2]}")


def scsi_text(table):
    row = table["max_frame_scsi_events"]
    if row["status"] == tracer.MEASURED:
        return f"{row['value']} (MEASURED)"
    return f"NOT_MEASURED, lower bound {table[tracer.SCSI_LOWER_BOUND]['value']} (LOWER_BOUND)"


EVIDENCE_NAME = "frame-tracer-measurements.json"


FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse_or_link(path):
    """True for a symlink or a Windows reparse point (junction). A missing path is not a link."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    except (OSError, TypeError, NotImplementedError):
        return True
    is_junction = getattr(path, "is_junction", None)
    try:
        if callable(is_junction) and is_junction():
            return True
    except (OSError, TypeError, NotImplementedError, RuntimeError):
        return True
    try:
        if os.path.islink(path):
            return True
    except (OSError, TypeError, NotImplementedError):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _validate_evidence_mode(mode):
    if type(mode) is not str or mode not in ("debug", "release"):
        raise ValueError("refusing to write frame-tracer evidence: invalid mode")


def _open_safe_evidence_directory(root, mode, nofollow):
    """Open ROOT/build/<mode>/frame-tracer-cd, creating it if needed, with every path component checked.

    Each component is opened with ``O_DIRECTORY | O_NOFOLLOW`` relative to its parent descriptor, so an
    intermediate symlink or reparse point cannot be substituted between the checks and the open.  A
    missing ``O_DIRECTORY``/``O_NOFOLLOW`` (or ``dir_fd``) support fails closed.
    """
    _validate_evidence_mode(mode)
    if nofollow is None:
        raise ValueError("refusing to write frame-tracer evidence: O_NOFOLLOW is unavailable")
    o_directory = getattr(os, "O_DIRECTORY", None)
    if o_directory is None:
        raise ValueError("refusing to write frame-tracer evidence: O_DIRECTORY is unavailable")
    parts = ("build", mode, "frame-tracer-cd")
    if _is_reparse_or_link(root):
        raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")
    try:
        current_fd = os.open(root, os.O_RDONLY | o_directory | nofollow)
    except (OSError, TypeError, NotImplementedError):
        raise ValueError("refusing to write frame-tracer evidence: directory open is unsupported or unsafe")
    try:
        root_st = os.fstat(current_fd)
    except (OSError, TypeError, NotImplementedError):
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise ValueError("refusing to write frame-tracer evidence: directory status is unsupported or unsafe")
    if stat.S_ISLNK(root_st.st_mode) or bool(getattr(root_st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT):
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")
    try:
        for index, part in enumerate(parts):
            path = root.joinpath(*parts[: index + 1])
            if _is_reparse_or_link(path):
                raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")
            try:
                os.mkdir(part, 0o755, dir_fd=current_fd)
            except FileExistsError:
                pass
            except (OSError, TypeError, NotImplementedError):
                raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")
            child_fd = None
            try:
                child_fd = os.open(part, os.O_RDONLY | o_directory | nofollow, dir_fd=current_fd)
            except (OSError, TypeError, NotImplementedError):
                raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")
            try:
                st = os.fstat(child_fd)
            except (OSError, TypeError, NotImplementedError):
                try:
                    os.close(child_fd)
                except OSError:
                    pass
                raise ValueError("refusing to write frame-tracer evidence: directory status is unsupported or unsafe")
            if stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT):
                try:
                    os.close(child_fd)
                except OSError:
                    pass
                raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")
            old_fd = current_fd
            current_fd = child_fd
            child_fd = None
            try:
                os.close(old_fd)
            except OSError:
                pass
        return current_fd
    except BaseException:
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise


def write_evidence(mode, record):
    """Write this diagnostic's own evidence file (overwritten in place; nothing else is touched).

    Every component of ``build/<mode>/frame-tracer-cd`` is opened through its parent descriptor with
    ``O_DIRECTORY | O_NOFOLLOW``, and the file is replaced through ``dir_fd``, so an intermediate or
    final link/reparse point cannot redirect the write. ``O_DIRECTORY``/``O_NOFOLLOW`` are required;
    environments without them fail closed.
    """
    _validate_evidence_mode(mode)
    output = ROOT / "build" / mode / "frame-tracer-cd"
    target = output / EVIDENCE_NAME
    chain = (ROOT / "build", ROOT / "build" / mode, output, target)

    def refuse_links():
        # Never follow a link out of build/: a symlinked folder or evidence file is refused, not written through.
        if any(path.is_symlink() for path in chain):
            raise ValueError("refusing to write frame-tracer evidence through a symbolic link")
        if any(callable(getattr(path, "is_junction", None)) and path.is_junction() for path in chain):
            raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")
        if any(_is_reparse_or_link(path) for path in chain):
            raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")

    refuse_links()  # before mkdir, so no directory is created through a linked parent

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("refusing to write frame-tracer evidence: O_NOFOLLOW is unavailable")

    parent_fd = None
    temp_name = None
    try:
        # Open (creating if necessary) every component of build/<mode>/frame-tracer-cd through its parent
        # descriptor, so an intermediate symlink/reparse point cannot be substituted.
        parent_fd = _open_safe_evidence_directory(ROOT, mode, nofollow)
        try:
            opened_parent = os.fstat(parent_fd)
        except (OSError, TypeError, NotImplementedError):
            raise ValueError("refusing to write frame-tracer evidence: directory status is unsupported or unsafe")
        if stat.S_ISLNK(opened_parent.st_mode) or bool(
                getattr(opened_parent, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")

        try:
            target_st = os.stat(EVIDENCE_NAME, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_st = None
        except (OSError, TypeError, NotImplementedError):
            raise ValueError("refusing to write frame-tracer evidence: target status is unsupported or unsafe")
        if target_st is not None and (stat.S_ISLNK(target_st.st_mode) or bool(
                getattr(target_st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)):
            raise ValueError("refusing to write frame-tracer evidence through a symbolic link or reparse point")
        if target_st is not None and not stat.S_ISREG(target_st.st_mode):
            raise ValueError("refusing to write frame-tracer evidence: target is not a regular file")

        temp_name = f".{EVIDENCE_NAME}.{secrets.token_hex(16)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
        fd = None
        created = False
        try:
            try:
                fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
            except (OSError, TypeError, NotImplementedError):
                temp_name = None
                raise ValueError("refusing to write frame-tracer evidence: temporary file creation is unsupported or unsafe")
            created = True
            try:
                handle = os.fdopen(fd, "wb", closefd=True)
            except (OSError, ValueError):
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
                if created:
                    try:
                        os.unlink(temp_name, dir_fd=parent_fd)
                    except (OSError, TypeError, NotImplementedError):
                        pass
                temp_name = None
                raise ValueError("refusing to write frame-tracer evidence: temporary file creation is unsupported or unsafe")
            fd = None  # ownership transferred to handle
            try:
                with handle:
                    handle.write((json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                    opened_stat = os.fstat(handle.fileno())
                    try:
                        path_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
                    except (OSError, TypeError, NotImplementedError):
                        raise ValueError("refusing to write frame-tracer evidence: temporary file status is unsupported or unsafe")
                    if (opened_stat.st_dev, opened_stat.st_ino, stat.S_IFMT(opened_stat.st_mode)) != \
                            (path_stat.st_dev, path_stat.st_ino, stat.S_IFMT(path_stat.st_mode)):
                        raise ValueError("refusing to write frame-tracer evidence: temporary file was replaced")
                    try:
                        os.replace(temp_name, EVIDENCE_NAME, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    except (OSError, TypeError, NotImplementedError):
                        raise ValueError("refusing to write frame-tracer evidence: atomic replacement is unsupported or unsafe")
                    temp_name = None
                    try:
                        os.fsync(parent_fd)
                    except (OSError, TypeError, NotImplementedError):
                        pass
            except (OSError, ValueError):
                if temp_name is not None:
                    try:
                        os.unlink(temp_name, dir_fd=parent_fd)
                    except (OSError, TypeError, NotImplementedError):
                        pass
                temp_name = None
                raise
        except BaseException:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except (OSError, TypeError, NotImplementedError):
                pass
        if parent_fd is not None:
            try:
                os.close(parent_fd)
            except OSError:
                pass


def status_record(status):
    """A counts-free record for a run that did not (or not yet) succeed: never success evidence."""
    return {"status": status, "evidence_valid": False, "runs": None,
            "scope": "frame tracer measurement table on synthetic CD media; diagnostic only, not BIOS evidence",
            "proprietary_content": "NONE", "guest_bytes": "NONE"}


def measurement_evidence(mode, on, off):
    """Counts-only measurement table for both runs; written under build/ (never guest bytes or traces)."""
    runs = {"observer_on": on.get("measurements"), "observer_off": off.get("measurements")}
    for name, table in runs.items():
        if type(table) is not dict or not set(tracer.MEASUREMENT_NAMES) <= set(table) or \
                set(table) - set(tracer.MEASUREMENT_NAMES) - {tracer.SCSI_LOWER_BOUND}:
            raise ValueError(f"{name}: measurement table missing or incomplete")
        # Fail-closed SCSI maximum: exact (MEASURED, no lower-bound row) exactly when no core-trace
        # frame lost entries; otherwise NOT_MEASURED with a separate LOWER_BOUND row.
        scsi, bound = table["max_frame_scsi_events"], table.get(tracer.SCSI_LOWER_BOUND)
        gap_free = table["core_trace_dropped_frames"].get("value") == 0
        if gap_free != (scsi.get("status") == tracer.MEASURED) or gap_free != (bound is None):
            raise ValueError(f"{name}: SCSI maximum status disagrees with core-trace gap frames")
        if gap_free and (type(scsi.get("value")) is not int or scsi["value"] < 0):
            raise ValueError(f"{name}: max_frame_scsi_events is not a measured count")
        if not gap_free and (scsi.get("status") != tracer.NOT_MEASURED or "value" in scsi or
                             bound.get("status") != tracer.LOWER_BOUND or type(bound.get("value")) is not int or
                             bound["value"] < 0):
            raise ValueError(f"{name}: SCSI lower bound is not separated from the NOT_MEASURED maximum")
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
                "status": "PASS", "evidence_valid": True,
                "core_capacity": runs["observer_on"]["core_capacity"], "runs": runs,
                "proprietary_content": "NONE", "guest_bytes": "NONE"}
    write_evidence(mode, evidence)
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args(argv)
    # Mark the evidence file INCOMPLETE before running, so a file left by an earlier successful run
    # can never stand in for this one; only a completed, checked run rewrites it as PASS.
    try:
        write_evidence(args.mode, status_record("INCOMPLETE"))
    except (OSError, ValueError) as error:
        print(f"FAIL: frame-tracer CD integration evidence path: {error}", file=sys.stderr)
        return 1
    try:
        run(args.mode)
    except IntegrationBlocked as error:
        try:
            write_evidence(args.mode, status_record("BLOCKED"))
        except (OSError, ValueError) as write_error:
            print(f"FAIL: frame-tracer CD integration evidence path: {write_error}", file=sys.stderr)
            return 1
        print(f"BLOCKED: frame-tracer CD integration: {error}", file=sys.stderr)
        return 3
    except (AssertionError, ValueError) as error:
        try:
            write_evidence(args.mode, status_record("FAIL"))
        except (OSError, ValueError) as write_error:
            print(f"FAIL: frame-tracer CD integration evidence path: {write_error}", file=sys.stderr)
            return 1
        print(f"FAIL: frame-tracer CD integration oracle assertion: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # unexpected diagnostic failures are failures, never blockers
        try:
            write_evidence(args.mode, status_record("FAIL"))
        except (OSError, ValueError) as write_error:
            print(f"FAIL: frame-tracer CD integration evidence path: {write_error}", file=sys.stderr)
            return 1
        print(f"FAIL: frame-tracer CD integration unexpected {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
