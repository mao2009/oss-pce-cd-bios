#!/usr/bin/env python3
"""Issue #365 (parent #173): free-running pre-write refusal for ST0/ST1/ST2 on the pinned real core.

The refusal is an in-core hook, not a harness single-step: a VERIFICATION-ONLY patch
(patches/geargrafx-b49ae82-st-write-guard.patch, against b49ae82a012eade566d72e9d61c9d95a5caa4da3)
makes the ST0/ST1/ST2 handlers call a guard before WriteFromCPU. The probe
(geargrafx_st_write_guard.cpp) runs the core with RunToVBlank and no debugger stepping.

Only the patched huc6280_opcodes object is substituted into the probe link; the stock source tree is
not modified. The GPL-3.0 core is linked locally and never distributed. Synthetic BIOS/CD only; no
private reference BIOS or ROM is read. Nothing here changes BLOCKED_ABI or release_eligible.

Cases:
* ``stN-armed``: guard installed; the ST is refused inside the core. Required: the guard saw exactly one
  ST call with the expected index/value, the VDC AR/R seen at the call equal the pre-store values, and
  the VDC after the frame equals the before values. The guest continues (BRA) with the store dropped.
* ``stN-unguarded`` (MUTATION): no hook; the same ST must land in the VDC.
* ``stN-after-tam-armed``: MPR0 is changed to $FF by TAM before the ST. ST writes ignore MPR (fixed I/O),
  so the store must still be refused by the opcode hook (an address-based MMIO monitor would miss it).
* ``stN-after-tam-unguarded``: same guest without the hook; the store must land.
* ``pos-write-armed``: positive control; a STA to allowlisted RAM is not an ST, is not consulted, and lands.

Run manually (not wired into the Makefile integration target):
    python3 tests/integration/geargrafx_st_write_guard.py
The "armed" field is true only when the core's ST path actually called the installed guard hook (observed
inside the probe's hook), not the case name. Clean-clone runs are recorded in the PR #430 thread, not here.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shlex  # noqa: E402
from geargrafx_api import build_command  # noqa: E402  (also puts tools/ on sys.path)
from environment import DEPENDENCIES, dependency_dir  # noqa: E402
from setup import debugger_overrides  # noqa: E402  (tools/setup.py: the flags that built the linked objects)
from geargrafx_reference_guard import synthetic_media  # noqa: E402

PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
PATCH = Path(__file__).resolve().parent / "patches" / "geargrafx-b49ae82-st-write-guard.patch"
PROBE_SOURCE = "geargrafx_st_write_guard.cpp"
FRAMES = 3
GUEST_BASE = 0x3E00
RAM_TARGET = 0x3E50
# kind -> (ST index passed to the hook, opcode value written, expected VDC effect)
ST_KINDS = {"st0": (0, 0x07), "st1": (2, 0x5A), "st2": (3, 0xA5)}
ARMED_CASES = ("st0-armed", "st1-armed", "st2-armed", "st0-after-tam-armed")
UNGUARDED_CASES = ("st0-unguarded", "st1-unguarded", "st2-unguarded", "st0-after-tam-unguarded")
POS_CASE = "pos-write-armed"


def _kind(case):
    return case[:3]


def _after_tam(case):
    return "after-tam" in case


def _landed(row, kind):
    if kind == "st0":
        return row["vdc_ar_before"] == 0 and row["vdc_ar_after"] == 0x07
    if kind == "st1":
        return row["vdc_r_before"] == 0 and (row["vdc_r_after"] & 0xFF) == 0x5A
    return row["vdc_r_before"] == 0 and (row["vdc_r_after"] >> 8) == 0xA5


def _common(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case} probe refused to run (exit {row.get('exit')}): {row.get('stderr', '')[-300:]}")
    if row.get("mode") != case:
        raise ValueError(f"{case} measured wrong mode: {row.get('mode')}")
    if row.get("status") != "ok":
        raise ValueError(f"{case}: guest did not reach its end (status={row.get('status')})")
    if type(row.get("armed")) is not bool:
        raise ValueError(f"{case}: probe did not report the armed state: {row.get('armed')!r}")
    for side in ("before", "after"):
        mpr = row.get(side, {}).get("mpr")
        if type(mpr) is not list or len(mpr) != 8:
            raise ValueError(f"{case}: malformed MPR {side}")
    for name in ("vdc_ar_before", "vdc_ar_after", "vdc_r_before", "vdc_r_after", "stop_pc",
                 "guard_calls", "guard_refusals"):
        if type(row.get(name)) is not int:
            raise ValueError(f"{case} missing/non-integer {name}: {row.get(name)!r}")
    if row["stop_pc"] != row["expected_end_pc"]:
        raise ValueError(f"{case}: guest stopped at {row['stop_pc']:#x}, expected {row['expected_end_pc']:#x}")
    mpr_before, mpr_after = row["before"]["mpr"], row["after"]["mpr"]
    if _after_tam(case):
        if mpr_before[0] != 0x00 or mpr_after[0] != 0xFF:
            raise ValueError(f"{case}: dynamic MPR0 change did not happen ({mpr_before[0]:#x}->{mpr_after[0]:#x})")
    elif mpr_before != mpr_after or mpr_before[0] != 0x00:
        raise ValueError(f"{case}: unexpected MPR configuration {mpr_before} -> {mpr_after}")


def check_armed(row, case):
    """The ST must be refused inside the core, with no VDC side effect at the call and after the frame."""
    _common(row, case)
    kind = _kind(case)
    index, value = ST_KINDS[kind]
    if row.get("armed") is not True:
        raise ValueError(f"{case}: run is not armed")
    if row["guard_calls"] != 1 or row["guard_refusals"] != 1:
        raise ValueError(f"{case}: guard calls/refusals {row['guard_calls']}/{row['guard_refusals']}, want 1/1")
    if row.get("guard_st_index") != index or row.get("guard_value") != value:
        raise ValueError(f"{case}: guard saw index {row.get('guard_st_index')} value {row.get('guard_value')}, "
                         f"want {index}/{value:#x}")
    if row.get("guard_ar_at_call") != row["vdc_ar_before"] or row.get("guard_r0_at_call") != row["vdc_r_before"]:
        raise ValueError(f"{case}: VDC changed before the guard call")
    if row["vdc_ar_before"] != row["vdc_ar_after"] or row["vdc_r_before"] != row["vdc_r_after"]:
        raise ValueError(f"{case}: VDC side effect landed although refused "
                         f"(AR {row['vdc_ar_before']:#x}->{row['vdc_ar_after']:#x}, "
                         f"R {row['vdc_r_before']:#x}->{row['vdc_r_after']:#x})")
    if _landed(row, kind):
        raise ValueError(f"{case}: ST effect present despite refusal")
    return "refused_inside_core_before_side_effect"


def check_unguarded(row, case):
    """Mutation (patched build, null hook): the ST must not be consulted and its VDC effect must land."""
    _common(row, case)
    if row.get("armed") is not False:
        raise ValueError(f"{case}: probe reports armed={row.get('armed')!r}, want False")
    if row["guard_calls"] != 0:
        raise ValueError(f"{case}: unguarded run consulted a guard")
    if not _landed(row, _kind(case)):
        raise ValueError(f"{case}: VDC side effect did not land without the hook")
    return "side_effect_landed_without_hook"


def check_pos(row):
    _common(row, POS_CASE)
    # No ST runs, so the installed hook is never consulted: the core-observed armed flag must be false.
    if row["armed"] is not False:
        raise ValueError("positive control: probe reports the hook consulted although no ST executed")
    if row["guard_calls"] != 0 or row["guard_refusals"] != 0:
        raise ValueError("positive control: a non-ST store reached the ST guard")
    if row.get("ram_before") == 0x5A or row.get("ram_after") != 0x5A:
        raise ValueError(f"positive control: allowlisted store did not land "
                         f"({row.get('ram_before')} -> {row.get('ram_after')})")
    return "allowed_store_landed"


def mutation_test_armed(row, case):
    """Falsified armed observations must be rejected by check_armed (proves the classifier can fail)."""
    kind = _kind(case)
    mutations = [
        ("probe_reports_unarmed", {"armed": False}),
        ("side_effect_landed", {"vdc_ar_after": 0x07} if kind == "st0" else
         {"vdc_r_after": 0x5A} if kind == "st1" else {"vdc_r_after": 0xA500}),
        ("no_guard_call", {"guard_calls": 0, "guard_refusals": 0}),
        ("accepted_not_refused", {"guard_refusals": 0}),
        ("wrong_opcode_index", {"guard_st_index": 1}),
        ("vdc_changed_before_call", {"guard_ar_at_call": 0x09}),
        ("guest_stopped_early", {"stop_pc": GUEST_BASE}),
    ]
    if _after_tam(case):
        mutations.append(("mpr_change_not_observed", {"after": {"mpr": [0x00] * 8}}))
    for name, overwrite in mutations:
        broken = copy.deepcopy(row)
        broken.update(overwrite)
        try:
            check_armed(broken, case)
        except ValueError:
            continue
        raise ValueError(f"mutation test FAILED: {case} {name} accepted by the armed classifier")


def mutation_test_unguarded(row, case):
    """A run that reports armed=true (or a missing armed field) must be rejected by check_unguarded."""
    for name, overwrite in (("probe_reports_armed", {"armed": True}), ("armed_missing", {"armed": None})):
        broken = copy.deepcopy(row)
        broken.update(overwrite)
        try:
            check_unguarded(broken, case)
        except ValueError:
            continue
        raise ValueError(f"mutation test FAILED: {case} {name} accepted by the unguarded classifier")


def build_probes(directory):
    """Stock probe objects with ONLY src/huc6280_opcodes.o replaced by the patched build."""
    source = dependency_dir("geargrafx").with_name(dependency_dir("geargrafx").name + "-debugger")
    make_dir = source / "platforms/libretro"
    objects = build_command(["make", "-s", "--no-print-directory", "-C", str(make_dir),
                             "--eval", "probe-objects:;@echo $(OBJECTS)", "probe-objects"]).stdout.split()
    objects = [(make_dir / p).resolve() for p in objects]
    if not objects or not all(p.is_file() for p in objects):
        raise ValueError("BLOCKED: official Geargrafx objects missing; run make setup")
    stock = [p for p in objects if p.name == "huc6280_opcodes.o" and p.parent.name == "src"]
    if len(stock) != 1:
        raise ValueError("BLOCKED: expected exactly one src/huc6280_opcodes.o in the link list")
    staged = directory / "patched-src"
    (staged / "src").mkdir(parents=True)
    shutil.copy2(source / "src/huc6280_opcodes.cpp", staged / "src/huc6280_opcodes.cpp")
    # ValueError (not CalledProcessError) so the caller reports BLOCKED/FAIL instead of a traceback.
    build_command(["patch", "-p1", "--batch", "--forward", "-d", str(staged), "-i", str(PATCH)])
    # Compile flags come from make itself, under the same overrides that built the stock objects.
    # Hand-copied flags drift (the debugger build has no -DGG_DISABLE_DISASSEMBLER and uses -O2, not -O0).
    overrides = debugger_overrides(make_dir)
    revision = DEPENDENCIES["geargrafx"]["revision"]
    flags_text = build_command(["make", "-s", "--no-print-directory", "-C", str(make_dir),
                                f"GIT_VERSION={revision}", *overrides,
                                "--eval", "print-cxx:;@echo $(CPPFLAGS) $(CXXFLAGS)", "print-cxx"]).stdout
    flags = shlex.split(flags_text)
    patched_obj = directory / "huc6280_opcodes_st_guard.o"
    # make prints INCLUDES relative to the platform directory, so compile from there.
    build_command(["g++", *flags, "-std=c++11", "-I" + str(staged / "src"), "-c",
                   str(staged / "src/huc6280_opcodes.cpp"), "-o", str(patched_obj)], cwd=make_dir)
    link = [patched_obj if p == stock[0] else p for p in objects]
    probe = directory / "st-write-guard-probe"
    build_command(["g++", *flags, "-std=c++11", str(Path(__file__).with_name(PROBE_SOURCE)), *map(str, link),
                   "-pthread", "-lm", "-o", str(probe)], cwd=make_dir)
    return probe


def run_case(probe, bios, cue, case):
    command = [str(probe), str(bios), str(cue), case, str(FRAMES)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        return {"mode": case, "exit": result.returncode, "stderr": result.stderr[-400:]}
    try:
        row = json.loads(result.stderr)
    except ValueError as error:
        raise ValueError(f"{case}: probe emitted no JSON observation") from error
    if type(row) is not dict:
        raise ValueError(f"{case}: observation is not an object")
    row["exit"] = 0
    # "armed" is NOT set here: it is the probe's own report of the guard it installed (see the .cpp).
    return row


def main():
    with tempfile.TemporaryDirectory(prefix="pce-st-write-guard-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = build_probes(directory)
        evidence = []
        verdicts = {}
        for case in ARMED_CASES:
            row = run_case(probe, bios, cue, case)
            verdicts[case] = check_armed(row, case)
            mutation_test_armed(row, case)
            evidence.append((case, verdicts[case], row))
        for case in UNGUARDED_CASES:
            row = run_case(probe, bios, cue, case)
            verdicts[case] = check_unguarded(row, case)
            mutation_test_unguarded(row, case)
            evidence.append((case, verdicts[case], row))
        row = run_case(probe, bios, cue, POS_CASE)
        verdicts[POS_CASE] = check_pos(row)
        evidence.append((POS_CASE, verdicts[POS_CASE], row))
    for case, verdict, row in evidence:
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "verdict": verdict,
                                        "row": row}, sort_keys=True))
    print("PASS issue-365/173 ST0/ST1/ST2 free-running pre-write refusal (synthetic, pinned core + verification patch)")
    print(f"  armed: {len(ARMED_CASES)} cases refused inside the core before side effect")
    print(f"  unguarded mutation: {len(UNGUARDED_CASES)} cases land without the hook")
    print("LIMIT: three ST opcodes only; not a general write interceptor; no gate change; no BIOS.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL/BLOCKED issue-365 ST write guard: {error}", file=sys.stderr)
        raise SystemExit(1) from error
