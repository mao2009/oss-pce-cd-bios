"""Isolated Geargrafx trace harness: capability doctor + bounded real-core trace (Issue #6).

  doctor  report host tools, pinned Geargrafx core/debugger build and input files (sha256).
  trace   compile the original probe into a fresh per-job directory, run it once with a
          fixed instruction budget and a wall-clock timeout, capture PC/registers/MPR
          (ROM mapping)/SCSI-phase (CD event) records and write one structured JSON log.

Status: PASS (expected checkpoint reached, no forbidden label), FAIL (timeout, crash,
invalid input, checkpoint missed), BLOCKED (emulator/toolchain/input absent, debugger
capability missing), SKIP (optional input deliberately not provided).
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


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def describe_file(path, rom=False):
    """Existence/size/hash of an input; a ROM must be a non-empty 8 KiB multiple."""
    if path is None:
        return {"status": "SKIP", "reason": "not provided"}
    path = Path(path)
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


def run_job(command, job_dir, timeout):
    """Run one process group; a timeout kills the whole group and is FAIL, never PASS."""
    start = time.monotonic()
    try:
        process = subprocess.Popen(command, cwd=job_dir, env=isolated_env(job_dir), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as error:
        return {"status": "BLOCKED", "reason": f"cannot execute: {error}", "exit_code": None,
                "stdout": "", "stderr": "", "elapsed_s": 0.0}
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        status, reason = None, None
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        status, reason = "FAIL", f"timeout after {timeout}s"
    return {"status": status, "reason": reason, "exit_code": process.returncode, "stdout": stdout,
            "stderr": stderr[-3000:], "elapsed_s": round(time.monotonic() - start, 3)}


def parse_records(stdout):
    """Every 'TAG {json}' line in emission order; malformed lines are a FAIL, not ignored."""
    events = []
    for line in stdout.splitlines():
        tag, _, body = line.partition(" ")
        if tag in TAGS:
            events.append({"tag": tag, **json.loads(body)})
    return events


def classify(result, expect, forbid=()):
    """Decide PASS/FAIL/BLOCKED from a finished job; anything unproven is not PASS."""
    if result["status"]:
        return result["status"], result["reason"], []
    code = result["exit_code"]
    if code in PROBE_EXIT:
        return (*PROBE_EXIT[code], [])
    if code != 0:
        return "FAIL", f"probe exited {code}", []
    try:
        events = parse_records(result["stdout"])
    except ValueError as error:
        return "FAIL", f"malformed trace record: {error}", []
    blocks = [e for e in events if e["tag"] == "BLOCK"]
    if blocks:
        return "BLOCKED", blocks[0].get("reason", "probe blocked"), events
    caps = [e for e in events if e["tag"] == "CAP"]
    if len(caps) != 1 or caps[0].get("instruction_step") is not True:
        return "BLOCKED", "no instruction-step capability record", events
    if any(e["tag"] == "RUN" and e.get("reason") == "budget" for e in events):
        return "FAIL", "instruction budget exhausted before a halt checkpoint (deterministic timeout)", events
    names = lambda e: {e.get("name"), e.get("phase"), e.get("reason")}  # noqa: E731
    hit = [label for label in forbid if any(label in names(e) for e in events)]
    if hit:
        return "FAIL", f"forbidden checkpoint reached: {', '.join(hit)}", events
    cycles = {e["cycle"] for e in events if "cycle" in e} or {None}
    for cycle in sorted(cycles, key=str):
        if not any(expect in names(e) and e.get("cycle") == cycle for e in events):
            return "FAIL", f"expected checkpoint {expect!r} not reached in {cycle or 'run'}", events
    return "PASS", f"checkpoint {expect!r} reached in {len(cycles)} cycle(s)", events


def trace(args):
    report = doctor(args.rom, args.cue, args.labels)
    log = {"schema_version": 1, "evidence": "REAL_GEARGRAFX_CORE", "probe_kind": args.kind,
           "expect": args.expect, "forbid": args.forbid, "steps": args.steps, "timeout_s": args.timeout,
           "doctor": report}
    if report["status"] != "PASS":
        bad = [k for k, v in report.items() if isinstance(v, dict) and v.get("status") in ("FAIL", "BLOCKED")]
        log.update(status=report["status"], reason=f"doctor: {', '.join(bad)}", evidence="NOT_RUN")
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
        shutil.copytree(Path(args.cue).parent, disc)  # copy CUE with its BIN into the job
        labels = job_dir / "labels.txt"
        shutil.copyfile(args.labels, labels)
        result = run_job([str(probe), *argv(str(rom), str(disc / Path(args.cue).name), str(labels), args.steps)],
                         job_dir, args.timeout)
        status, reason, events = classify(result, args.expect, args.forbid)
        log.update(status=status, reason=reason, exit_code=result["exit_code"], elapsed_s=result["elapsed_s"],
                   stderr_tail=result["stderr"], probe_sha256=sha256(probe), rom_sha256=sha256(rom),
                   geargrafx_revision=DEPENDENCIES["geargrafx"]["revision"],
                   counts={tag: sum(e["tag"] == tag for e in events) for tag in TAGS}, trace=events)
        if result["exit_code"] is None:
            log["evidence"] = "NOT_RUN"
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
        print(f"{log['status']}: {log.get('reason', args.command)} -> {args.output}")
    else:
        sys.stdout.write(text)
    return EXIT[log["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
