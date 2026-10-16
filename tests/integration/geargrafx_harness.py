"""Isolated Geargrafx trace harness: capability doctor + bounded real-core trace (Issue #6).

  doctor  report host tools, pinned Geargrafx core/debugger build and input files (sha256).
  trace   compile the original probe into a fresh per-job directory, run it once with a
          fixed instruction budget and a wall-clock timeout, capture PC/registers/MPR
          (ROM mapping)/SCSI-phase (CD event) records and write one structured JSON log.

Status: PASS (expected checkpoint reached, no forbidden label), FAIL (timeout, crash,
invalid input, checkpoint missed), BLOCKED (emulator/toolchain/input absent, debugger
capability missing, or NOT_SUPPORTED kind), SKIP (optional input deliberately not provided).
Only kind "cdread" has a RUN/HIT checkpoint judge (JUDGED_KINDS); the "api" probe emits
CAP/TRACE/OBS/BANK records and no RUN, so `--kind api` is BLOCKED as NOT_SUPPORTED.
Exit codes: PASS 0, FAIL 1, BLOCKED 3, SKIP 4.

No listener is opened: the probe talks only over its own stdout pipe, so there is
no network/MCP port to expose. Each job owns a private directory, HOME/TMPDIR and
process group; nothing is shared between concurrent jobs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import DEPENDENCIES, core_path, dependency_dir  # noqa: E402
from cdread_verify import check_halt_confirmation  # noqa: E402  (same halt rule as the #5 judge)

EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 3, "SKIP": 4}
TAGS = ("CAP", "OBS", "TRACE", "BANK", "HIT", "PHASE", "RUN", "BLOCK", "INJECT")
# probe exit codes (see geargrafx_*_probe.cpp)
PROBE_EXIT = {2: ("FAIL", "probe rejected arguments or input file"),
              3: ("FAIL", "core rejected the ROM (LoadBiosFromBuffer)"),
              4: ("FAIL", "core rejected the CUE/disc (LoadMedia)"),
              5: ("FAIL", "ROM entry is not the probe contract SEI"),
              6: ("BLOCKED", "instruction-step debugger capability missing")}
KINDS = {  # probe source, argv after the probe given (rom, cue, labels, steps)
    "cdread": ("geargrafx_cdread_probe.cpp", lambda r, c, l, s: [r, c, l, str(s)]),
    "api": ("geargrafx_api_probe.cpp", lambda r, c, l, s: [r, c, str(s), l]),
}
STEP_LIMIT = {"cdread": 200_000_000, "api": 2048}
MAX_OUTPUT_BYTES = 8 * 1024 * 1024  # per job, per stream; exceeding it kills the job (FAIL)
MAX_EVENTS = 20_000                 # trace records kept per job; more is FAIL
CUE_FILE = re.compile(r'^\s*FILE\s+(?:"([^"]*)"|(\S+))\s+\S+\s*$', re.IGNORECASE)


CHUNK_BYTES = 1024 * 1024  # every digest streams in reads of this size; no input is read whole


def _update_digest(digest, handle, reads=None):
    """Feed a binary stream into digest in CHUNK_BYTES reads; `reads` records each read's size."""
    while True:
        chunk = handle.read(CHUNK_BYTES)
        if not chunk:
            return digest
        if reads is not None:
            reads.append(len(chunk))
        digest.update(chunk)


def sha256(path, reads=None):
    with Path(path).open("rb") as handle:
        return _update_digest(hashlib.sha256(), handle, reads).hexdigest()


def describe_file(path, rom=False):
    """Existence/size/hash of an input; a ROM must be a non-empty 8 KiB multiple."""
    if path is None:
        return {"status": "SKIP", "reason": "not provided"}
    path = Path(path)
    if path.is_symlink():
        return {"status": "FAIL", "path": str(path), "reason": "symlink refused"}
    if not path.is_file():
        return {"status": "BLOCKED", "path": str(path), "reason": "missing"}
    size = path.stat().st_size
    info = {"path": str(path), "bytes": size, "sha256": sha256(path), "status": "READY"}
    if size == 0 or (rom and size % 8192):
        info.update(status="FAIL", reason="invalid size")
    return info


def tool_version(name):
    found = shutil.which(name)
    if not found:
        return {"status": "BLOCKED", "reason": f"{name} not on PATH"}
    out = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=30).stdout
    return {"status": "READY", "path": found, "version": (out.splitlines() or ["?"])[0]}


def doctor(rom=None, cue=None, labels=None):
    pin = DEPENDENCIES["geargrafx"]
    report = {"host": f"{platform.system()} {platform.machine()}",
              "geargrafx_pin": {"revision": pin["revision"], "archive_sha256": pin["sha256"]},
              "g++": tool_version("g++"), "make": tool_version("make")}
    try:
        core = core_path()
        report["libretro_core"] = {"status": "READY", "path": str(core), "sha256": sha256(core)}
    except (ValueError, AttributeError) as error:  # AttributeError: os.getuid on non-POSIX hosts
        report["libretro_core"] = {"status": "BLOCKED", "reason": str(error)}
    try:
        source = dependency_dir("geargrafx").with_name(dependency_dir("geargrafx").name + "-debugger")
        stamp = source / ".verified-archive-sha256"
        ok = stamp.is_file() and stamp.read_text().strip() == pin["sha256"]
        report["debugger_build"] = ({"status": "READY", "path": str(source)} if ok else
                                    {"status": "BLOCKED", "path": str(source),
                                     "reason": "verified debugger source missing; run make setup"})
    except AttributeError as error:
        report["debugger_build"] = {"status": "BLOCKED", "reason": str(error)}
    report["rom"] = describe_file(rom, rom=True)
    report["cue"] = describe_file(cue)
    report["labels"] = describe_file(labels)
    states = [item["status"] for item in report.values() if isinstance(item, dict) and "status" in item]
    report["status"] = ("FAIL" if "FAIL" in states else "BLOCKED" if "BLOCKED" in states else "PASS")
    return report


def isolated_env(job_dir):
    """Private HOME/TMP/XDG so the emulator never reads or writes another job's profile."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"}
    for key in ("HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        env[key] = str(job_dir)
    return env


def kill_tree(process):
    """Kill the job's whole process tree: its process group on POSIX, `taskkill /T` on Windows."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass  # already exited; wait() below still reaps the leader
    process.wait()


def run_job(command, job_dir, timeout, max_output=MAX_OUTPUT_BYTES):
    """Run one process tree with stdout/stderr going to capped files in job_dir.

    A timeout or an over-long output kills the whole tree and is FAIL, never PASS. Output is
    never held in memory beyond the cap: the probe writes to disk and is polled while it runs.
    """
    start = time.monotonic()
    out_path, err_path = job_dir / "stdout.log", job_dir / "stderr.log"
    try:
        with out_path.open("wb") as out, err_path.open("wb") as err:
            process = subprocess.Popen(command, cwd=job_dir, env=isolated_env(job_dir), stdout=out,
                                       stderr=err, start_new_session=True)
            status = reason = None
            while process.poll() is None:
                if time.monotonic() - start > timeout:
                    kill_tree(process)
                    status, reason = "FAIL", f"timeout after {timeout}s"
                    break
                if max(out_path.stat().st_size, err_path.stat().st_size) > max_output:
                    kill_tree(process)
                    status, reason = "FAIL", f"output exceeded {max_output} bytes"
                    break
                time.sleep(0.05)
    except OSError as error:
        return {"status": "BLOCKED", "reason": f"cannot execute: {error}", "exit_code": None,
                "stdout": "", "stderr": "", "elapsed_s": 0.0}
    if status is None and max(out_path.stat().st_size, err_path.stat().st_size) > max_output:
        status, reason = "FAIL", f"output exceeded {max_output} bytes"
    stdout = out_path.read_bytes()[:max_output].decode("utf-8", "replace")
    stderr = err_path.read_bytes()[-3000:].decode("utf-8", "replace")
    return {"status": status, "reason": reason, "exit_code": process.returncode, "stdout": stdout,
            "stderr": stderr, "elapsed_s": round(time.monotonic() - start, 3)}


def parse_records(stdout):
    """Every 'TAG {json}' line in emission order; malformed lines or too many records are a FAIL."""
    events = []
    for line in stdout.splitlines():
        tag, _, body = line.partition(" ")
        if tag in TAGS:
            if len(events) >= MAX_EVENTS:
                raise ValueError(f"more than {MAX_EVENTS} trace records")
            events.append({"tag": tag, **json.loads(body)})
    return events


def cue_data_files(cue):
    """File names a CUE sheet references; any name that could leave the CUE's folder is refused."""
    names = []
    for line in Path(cue).read_text(errors="replace").splitlines():
        match = CUE_FILE.match(line)
        if match:
            names.append(match.group(1) if match.group(1) is not None else match.group(2))
    if not names:
        raise ValueError("CUE names no FILE entry")
    for name in names:
        if name in ("", ".", "..") or any(c in name for c in "/\\:\x00"):
            raise ValueError(f"unsafe CUE file name {name!r}")
    return names


def stage_disc(cue, dest):
    """Copy the CUE and only the data files it names into dest; the rest of its folder is never read."""
    cue = Path(cue)
    if cue.is_symlink():
        raise ValueError("symlinked CUE refused")
    if not cue.is_file():
        raise FileNotFoundError(f"CUE missing: {cue.name}")
    sources = [cue]
    for name in cue_data_files(cue):
        source = cue.parent / name
        if source.is_symlink():
            raise ValueError(f"symlinked data file refused: {name}")
        if not source.is_file():
            raise FileNotFoundError(f"CUE data file missing: {name}")
        sources.append(source)
    for source in sources:
        shutil.copyfile(source, dest / source.name)
    return [source.name for source in sources]


JUDGED_KINDS = ("cdread",)  # kinds whose probe emits the RUN/HIT checkpoint records classify() reads
REQUIRED_CYCLES = ("cold", "warm")
CAP_FLAGS = ("cdrom_hardware", "cdrom_media", "disc_given")


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def classify(result, expect, forbid=(), budget=None, kind="cdread"):
    """Decide PASS/FAIL/BLOCKED from a finished job; anything unproven is not PASS.

    PASS needs exactly one RUN per cycle, cold then warm, each ending on `expect` with a confirmed
    halt (64 consecutive instructions on the label, the same rule as the #5 judge), and exactly one
    success HIT for that cycle recorded before its RUN. Any RUN with another reason (failure or
    budget) is FAIL even when the success HIT is present.
    """
    if kind not in JUDGED_KINDS:
        return "BLOCKED", f"NOT_SUPPORTED: kind {kind!r} has no RUN/HIT checkpoint judge", []
    if result["status"]:
        return result["status"], result["reason"], []
    code = result["exit_code"]
    if code in PROBE_EXIT:
        return (*PROBE_EXIT[code], [])
    if code != 0:
        return "FAIL", f"probe exited {code}", []
    try:
        events = parse_records(result["stdout"])
    except (ValueError, TypeError) as error:
        return "FAIL", f"trace rejected: {error}", []
    blocks = [e for e in events if e["tag"] == "BLOCK"]
    if blocks:
        return "BLOCKED", blocks[0].get("reason", "probe blocked"), events
    caps = [e for e in events if e["tag"] == "CAP"]
    if len(caps) != 1 or caps[0].get("instruction_step") is not True:
        return "BLOCKED", "no instruction-step capability record", events
    if any(caps[0].get(flag) is not True for flag in CAP_FLAGS):
        return "BLOCKED", "incomplete capability record (CD hardware, media or disc not reported)", events
    runs = [e for e in events if e["tag"] == "RUN"]
    if any(e.get("reason") == "budget" for e in runs):
        return "FAIL", "instruction budget exhausted before a halt checkpoint (deterministic timeout)", events
    other = [str(e.get("reason")) for e in runs if e.get("reason") != expect]
    if other:
        return "FAIL", f"run ended without {expect!r}: {', '.join(other)}", events
    names = lambda e: {e.get("name"), e.get("phase"), e.get("reason")}  # noqa: E731
    hit = [label for label in forbid if any(label in names(e) for e in events)]
    if hit:
        return "FAIL", f"forbidden checkpoint reached: {', '.join(hit)}", events
    cycles = [e.get("cycle") for e in runs]
    if cycles != list(REQUIRED_CYCLES):
        return "FAIL", f"RUN records must be exactly one cold then one warm, got {cycles}", events
    for run in runs:
        cycle = run["cycle"]
        if not _is_count(run.get("step")):
            return "FAIL", f"{cycle}: instruction count is not a non-negative integer", events
        if budget is not None and run["step"] > budget:
            return "FAIL", f"{cycle}: {run['step']} instructions exceed the budget of {budget}", events
        try:
            check_halt_confirmation(run)
        except ValueError as error:
            return "FAIL", str(error), events
    for cycle in REQUIRED_CYCLES:
        run_at = next(i for i, e in enumerate(events) if e["tag"] == "RUN" and e.get("cycle") == cycle)
        hits = [i for i, e in enumerate(events)
                if e["tag"] == "HIT" and e.get("name") == expect and e.get("cycle") == cycle]
        if len(hits) != 1 or hits[0] > run_at:
            return "FAIL", f"{cycle}: success checkpoint HIT missing, repeated or after its RUN", events
    return "PASS", f"checkpoint {expect!r} reached with confirmed halt in cold and warm", events


def fixture_digest(disc, names, reads=None):
    """One sha256 over the staged data files, in CUE order: identifies the disc without copying it."""
    digest = hashlib.sha256()
    for name in names:
        with (Path(disc) / name).open("rb") as handle:
            _update_digest(digest, handle, reads)
    return digest.hexdigest()


def attestation(status, rom_sha, fixture_sha, expect):
    """Hash-only runner attestation in the shape docs/compat-evidence-schema.md (#116) consumes.

    Carries no guest memory or trace records, so it is the only part of a run that may be
    shared with the compatibility-evidence record.
    """
    passed = status == "PASS"
    return {"verified": passed, "rom_sha256": rom_sha, "fixture_sha256": fixture_sha,
            "emulator_commit": DEPENDENCIES["geargrafx"]["revision"],
            "checkpoints": [{"name": expect, "observed": expect}] if passed else []}


def trace(args):
    report = doctor(args.rom, args.cue, args.labels)
    log = {"schema_version": 1, "evidence": "REAL_GEARGRAFX_CORE", "probe_kind": args.kind,
           "expect": args.expect, "forbid": args.forbid, "steps": args.steps, "timeout_s": args.timeout,
           "limits": {"output_bytes": MAX_OUTPUT_BYTES, "events": MAX_EVENTS},
           "artifact_policy": "LOCAL_ONLY: trace may hold raw guest RAM dumps; never upload as a CI artifact",
           "doctor": report}
    if report["status"] != "PASS":
        bad = [k for k, v in report.items() if isinstance(v, dict) and v.get("status") in ("FAIL", "BLOCKED")]
        log.update(status=report["status"], reason=f"doctor: {', '.join(bad)}", evidence="NOT_RUN")
        return log
    if args.kind not in JUDGED_KINDS:
        log.update(status="BLOCKED", reason=f"NOT_SUPPORTED: kind {args.kind!r} has no RUN/HIT checkpoint judge",
                   evidence="NOT_RUN")
        return log
    if not 1 <= args.steps <= STEP_LIMIT[args.kind]:
        log.update(status="FAIL", reason=f"steps outside 1..{STEP_LIMIT[args.kind]}", evidence="NOT_RUN")
        return log
    from geargrafx_api import compile_probe
    job_dir = Path(tempfile.mkdtemp(prefix=f"pce-gg-job-{os.getpid()}-"))
    try:
        source, argv = KINDS[args.kind]
        try:
            probe = compile_probe(job_dir, source, "probe")
        except (ValueError, subprocess.CalledProcessError) as error:
            log.update(status="BLOCKED", reason=f"probe build: {error}", evidence="NOT_RUN")
            return log
        rom = job_dir / "input.pce"
        shutil.copyfile(args.rom, rom)
        disc = job_dir / "disc"
        disc.mkdir()
        try:
            staged = stage_disc(args.cue, disc)  # only the CUE and the data files it names
        except FileNotFoundError as error:
            log.update(status="BLOCKED", reason=str(error), evidence="NOT_RUN")
            return log
        except ValueError as error:
            log.update(status="FAIL", reason=f"invalid disc input: {error}", evidence="NOT_RUN")
            return log
        labels = job_dir / "labels.txt"
        shutil.copyfile(args.labels, labels)
        result = run_job([str(probe), *argv(str(rom), str(disc / Path(args.cue).name), str(labels), args.steps)],
                         job_dir, args.timeout)
        status, reason, events = classify(result, args.expect, args.forbid, args.steps, args.kind)
        log.update(status=status, reason=reason, exit_code=result["exit_code"], elapsed_s=result["elapsed_s"],
                   stderr_tail=result["stderr"], probe_sha256=sha256(probe), rom_sha256=sha256(rom),
                   geargrafx_revision=DEPENDENCIES["geargrafx"]["revision"],
                   counts={tag: sum(e["tag"] == tag for e in events) for tag in TAGS}, trace=events)
        if result["exit_code"] is None:
            log["evidence"] = "NOT_RUN"
        log["attestation"] = attestation(log["status"], report["rom"].get("sha256"),
                                         fixture_digest(disc, staged[1:]), args.expect)
        return log
    finally:
        if not args.keep_job:
            shutil.rmtree(job_dir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "trace"):
        command = sub.add_parser(name)
        command.add_argument("--rom", type=Path)
        command.add_argument("--cue", type=Path)
        command.add_argument("--labels", type=Path)
        command.add_argument("--output", type=Path)
    run = sub.choices["trace"]
    run.add_argument("--kind", choices=sorted(KINDS), default="cdread")
    run.add_argument("--expect", required=True, help="label that must be reached in every cycle")
    run.add_argument("--forbid", action="append", default=[], help="label that makes the run FAIL")
    run.add_argument("--steps", type=int, default=12_000_000, help="instruction budget per cycle")
    run.add_argument("--timeout", type=float, default=300.0, help="wall-clock seconds; exceeding is FAIL")
    run.add_argument("--keep-job", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "doctor":
        log = doctor(args.rom, args.cue, args.labels)
    else:
        for name in ("rom", "cue", "labels"):
            if getattr(args, name) is None:
                parser.error(f"trace requires --{name}")
        log = trace(args)
    text = json.dumps(log, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
        if "attestation" in log:  # hash-only; the full log stays local
            side = args.output.with_name(args.output.stem + ".attestation.json")
            side.write_text(json.dumps(log["attestation"], indent=2, sort_keys=True) + "\n")
        print(f"{log['status']}: {log.get('reason', args.command)} -> {args.output}")
    else:
        sys.stdout.write(text)
    return EXIT[log["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
