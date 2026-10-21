#!/usr/bin/env python3
"""Issue #366 (parent #173): free-running IRQ-entry refusal for IRQ2 / IRQ1 / TIQ on the pinned real core.

The refusal is an in-core hook, not a harness single-step: a VERIFICATION-ONLY patch
(patches/geargrafx-b49ae82-irq-entry-guard.patch, against b49ae82a012eade566d72e9d61c9d95a5caa4da3)
makes HuC6280::HandleIRQ call gg_verify_irq_entry_guard after the vector is selected and before the
entry stack pushes and the PC change. The probe (geargrafx_irq_entry_guard.cpp) runs the core with
RunToVBlank and no debugger stepping.

The patched source tree is staged in a temporary directory and every core object is rebuilt from it
(the hook is inline-header code, so no single object is a safe substitute). The stock tree is not
modified. The GPL-3.0 core is linked locally and never distributed. Synthetic BIOS/CD only; no private
reference BIOS or ROM is read. Nothing here changes BLOCKED_ABI or release_eligible.

Cases (vector in irq2 / irq1 / tiq):
* ``<v>-armed``: hook refuses every entry. Required: the hook was called once with the vector, the entry
  stack bytes were zero at the call and after the frame, the PC stayed at the guest loop, the handler marker
  and A were not written.
* ``<v>-unguarded`` (positive control): null hook; the entry lands (stack holds PC hi, PC lo, P with I and B
  clear) and the handler runs (marker = $77).
* ``<v>-masked``: IDR masks the vector; CLI is executed; the hook must not be consulted, nothing is pushed.
* ``<v>-region-allow``: hook allows an entry whose handler is inside page 1; the entry lands.
* ``<v>-outregion``: hook refuses an entry whose handler is outside page 1 (out-of-guard-region vector).
* libretro: BLOCKED (no libretro probe build; the libretro build has no execute breakpoint / PC API).

Run manually (not wired into the Makefile integration target):
    python3 tests/integration/geargrafx_irq_entry_guard.py
Status: see the PR description. Not PASS on any build until the run below prints PASS lines.
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
from geargrafx_api import build_command  # noqa: E402  (also puts tools/ on sys.path)
from environment import dependency_dir  # noqa: E402
from geargrafx_reference_guard import synthetic_media  # noqa: E402

PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
PATCH = Path(__file__).resolve().parent / "patches" / "geargrafx-b49ae82-irq-entry-guard.patch"
PROBE_SOURCE = "geargrafx_irq_entry_guard.cpp"
FRAMES = 2
GUEST_LOOP_PC = 0x3E01        # BRA * after CLI (the entry pushes this PC)
HANDLER_LOOP_PC = 0x3F85      # BRA * at the end of the handler (LDA/STA/BRA)
HANDLER = 0x3F80
OUT_HANDLER = 0x4010
VECTORS = {"irq2": 0xFFF6, "irq1": 0xFFF8, "tiq": 0xFFFA}
VARIANTS = ("armed", "unguarded", "masked", "region-allow", "outregion")
CASES = tuple(f"{v}-{w}" for v in VECTORS for w in VARIANTS)
PASS, FAIL = "PASS", "FAIL"


def _ints(row, key, count):
    value = row.get(key)
    if type(value) is not list or len(value) != count or not all(type(x) is int for x in value):
        raise ValueError(f"malformed {key}: {value!r}")
    return value


def _common(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case} probe refused to run (exit {row.get('exit')}): {row.get('stderr', '')[-300:]}")
    if row.get("mode") != case or row.get("status") != "ok":
        raise ValueError(f"{case} measured wrong mode/status: {row.get('mode')}/{row.get('status')}")
    for name in ("guard_calls", "guard_refusals", "guard_allows", "stop_pc", "a_after", "marker_after"):
        if type(row.get(name)) is not int:
            raise ValueError(f"{case} missing/non-integer {name}: {row.get(name)!r}")
    _ints(row, "stack_after", 3)
    _ints(row, "stack_at_call", 3)


def _entered(row):
    """Stack holds PC hi, PC lo (return = guest loop PC) and P with I and B clear."""
    hi, lo, p = row["stack_after"]
    return (hi << 8 | lo) == GUEST_LOOP_PC and (p & 0x14) == 0


def check_armed(row, case):
    _common(row, case)
    vector = VECTORS[case.split("-")[0]]
    if row["guard_calls"] < 1 or row["guard_refusals"] != row["guard_calls"] or row["guard_allows"] != 0:
        raise ValueError(f"{case}: calls/refusals/allows {row['guard_calls']}/{row['guard_refusals']}/{row['guard_allows']}")
    if row.get("vector_at_call") != vector or row.get("handler_at_call") != HANDLER:
        raise ValueError(f"{case}: hook saw vector {row.get('vector_at_call')} handler {row.get('handler_at_call')}")
    if row.get("pc_at_call") != GUEST_LOOP_PC:
        raise ValueError(f"{case}: PC at the hook call was {row.get('pc_at_call'):#x}, want guest loop")
    if row["stack_at_call"] != [0, 0, 0] or row["stack_after"] != [0, 0, 0]:
        raise ValueError(f"{case}: stack written although refused ({row['stack_at_call']} -> {row['stack_after']})")
    if row["stop_pc"] != GUEST_LOOP_PC:
        raise ValueError(f"{case}: PC left the guest loop ({row['stop_pc']:#x})")
    if row["marker_after"] != 0 or row["a_after"] != 0:
        raise ValueError(f"{case}: handler instruction executed (marker={row['marker_after']}, A={row['a_after']})")
    return "refused_before_stack_write_and_pc_change"


def check_unguarded(row, case):
    _common(row, case)
    if row.get("guarded") is not False or row["guard_calls"] != 0:
        raise ValueError(f"{case}: unguarded run consulted a guard")
    if not _entered(row):
        raise ValueError(f"{case}: entry stack {row['stack_after']} not written")
    if row["stop_pc"] != HANDLER_LOOP_PC or row["marker_after"] != 0x77 or row["a_after"] != 0x77:
        raise ValueError(f"{case}: handler did not run (PC={row['stop_pc']:#x}, marker={row['marker_after']})")
    return "entry_landed_handler_ran_without_hook"


def check_masked(row, case):
    _common(row, case)
    if row["guard_calls"] != 0:
        raise ValueError(f"{case}: masked vector reached the hook ({row['guard_calls']} calls)")
    if row["stack_after"] != [0, 0, 0] or row["stop_pc"] != GUEST_LOOP_PC:
        raise ValueError(f"{case}: masked vector was taken (stack {row['stack_after']}, PC {row['stop_pc']:#x})")
    if row["marker_after"] != 0 or row["a_after"] != 0:
        raise ValueError(f"{case}: handler ran for a masked vector")
    return "mask_respected_no_entry"


def check_region_allow(row, case):
    _common(row, case)
    if row["guard_refusals"] != 0 or row["guard_allows"] < 1:
        raise ValueError(f"{case}: calls/refusals/allows {row['guard_calls']}/{row['guard_refusals']}/{row['guard_allows']}")
    if row.get("handler_at_call") != HANDLER:
        raise ValueError(f"{case}: handler {row.get('handler_at_call')} not the in-region handler")
    if not _entered(row) or row["stop_pc"] != HANDLER_LOOP_PC or row["marker_after"] != 0x77:
        raise ValueError(f"{case}: allowed entry did not land and run the handler")
    return "in_region_entry_allowed_and_landed"


def check_outregion(row, case):
    _common(row, case)
    if row["guard_calls"] < 1 or row["guard_refusals"] != row["guard_calls"] or row["guard_allows"] != 0:
        raise ValueError(f"{case}: calls/refusals/allows {row['guard_calls']}/{row['guard_refusals']}/{row['guard_allows']}")
    if row.get("handler_at_call") != OUT_HANDLER:
        raise ValueError(f"{case}: hook saw handler {row.get('handler_at_call')}, want {OUT_HANDLER:#x}")
    if row["stack_after"] != [0, 0, 0] or row["stop_pc"] != GUEST_LOOP_PC:
        raise ValueError(f"{case}: out-of-region entry landed (stack {row['stack_after']}, PC {row['stop_pc']:#x})")
    if row["marker_after"] != 0 or row["a_after"] != 0:
        raise ValueError(f"{case}: out-of-region handler executed")
    return "out_of_region_entry_refused_before_stack_write"


CHECKS = {"armed": check_armed, "unguarded": check_unguarded, "masked": check_masked,
          "region-allow": check_region_allow, "outregion": check_outregion}


def mutation_test(row, case):
    """Falsified observations must be rejected by the classifier (proves it can fail)."""
    if case.endswith("-armed"):
        mutations = [
            ("stack_landed", {"stack_after": [0x3E, 0x01, 0x00]}),
            ("no_call", {"guard_calls": 0, "guard_refusals": 0}),
            ("accepted", {"guard_refusals": 0, "guard_allows": 1}),
            ("pc_moved_to_handler", {"stop_pc": HANDLER_LOOP_PC}),
            ("handler_ran", {"marker_after": 0x77}),
        ]
    elif case.endswith("-outregion"):
        mutations = [("in_region_handler_seen", {"handler_at_call": HANDLER}),
                     ("stack_landed", {"stack_after": [0x3E, 0x01, 0x00]})]
    elif case.endswith("-masked"):
        mutations = [("hook_consulted", {"guard_calls": 1}),
                     ("stack_landed", {"stack_after": [0x3E, 0x01, 0x00]})]
    elif case.endswith("-unguarded"):
        mutations = [("no_entry", {"stack_after": [0, 0, 0]}),
                     ("handler_missing", {"marker_after": 0})]
    else:
        mutations = [("refused_instead", {"guard_refusals": 1, "guard_allows": 0, "stack_after": [0, 0, 0]})]
    for name, overwrite in mutations:
        broken = copy.deepcopy(row)
        broken.update(overwrite)
        try:
            CHECKS[case.split("-", 1)[1]](broken, case)
        except ValueError:
            continue
        raise ValueError(f"mutation test FAILED: {case} {name} accepted by the classifier")


def build_probes(directory):
    """Stage the pinned tree, apply the verification patch, rebuild every object, link the probe."""
    source = dependency_dir("geargrafx").with_name(dependency_dir("geargrafx").name + "-debugger")
    staged = directory / "patched-core"
    shutil.copytree(source, staged, symlinks=True)
    build_command(["patch", "-p1", "--batch", "--forward", "-d", str(staged), "-i", str(PATCH)])
    make_dir = staged / "platforms/libretro"
    objects = build_command(["make", "-s", "--no-print-directory", "-C", str(make_dir),
                             "--eval", "probe-objects:;@echo $(OBJECTS)", "probe-objects"]).stdout.split()
    if not objects:
        raise ValueError("BLOCKED: no core objects listed; run make setup")
    for obj in staged.rglob("*.o"):
        obj.unlink()  # never reuse stock objects: every object is rebuilt from the patched tree
    # ValueError (not CalledProcessError) so the caller reports BLOCKED/FAIL instead of a traceback.
    build_command(["make", "-s", "-C", str(make_dir), "-j", str(os.cpu_count() or 2), *objects])
    objs = [(make_dir / p).resolve() for p in objects]
    if not all(p.is_file() for p in objs):
        raise ValueError("BLOCKED: patched core objects missing after rebuild")
    probe = directory / "irq-entry-guard-probe"
    # The pinned Makefile compiles every object with GG_DISABLE_DISASSEMBLER (and ZSTD/Z7 flags); the probe
    # must match, or HuC6280 member layout differs between the probe TU and the core objects (segfault).
    flags = ["-std=c++11", "-O0", "-fno-rtti", "-D__LIBRETRO__", "-DGG_DISABLE_DISASSEMBLER",
             "-DZ7_ST", "-DZSTD_DISABLE_ASM", "-DGG_DISABLE_VGMRECORDER",
             "-DGG_ENABLE_CDROM_CUEBIN_READAHEAD", "-I" + str(staged / "src"), "-I" + str(make_dir),
             "-I" + str(staged / "platforms/shared/dependencies/miniz"),
             "-I" + str(staged / "platforms/shared/dependencies/json")]
    build_command(["g++", *flags, str(Path(__file__).with_name(PROBE_SOURCE)), *map(str, objs),
                   "-pthread", "-lm", "-o", str(probe)])
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
    return row


def main():
    with tempfile.TemporaryDirectory(prefix="pce-irq-entry-guard-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = build_probes(directory)
        for case in CASES:
            row = run_case(probe, bios, cue, case)
            verdict = CHECKS[case.split("-", 1)[1]](row, case)
            mutation_test(row, case)
            print(f"{PASS} {case}: {verdict} (core {PINNED_CORE_REVISION}, synthetic BIOS/CD, free-running)")
            print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                            "core_revision": PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                            "hook": "VERIFICATION_ONLY_PATCH", "verdict": verdict,
                                            "row": row}, sort_keys=True))
    # Libretro: no probe build exists. Reported BLOCKED, never PASS.
    print("BLOCKED libretro irq-entry guard: no libretro harness drives this probe (the libretro shared object "
          "is not run here); libretro is not observed and is not PASS.")
    print("LIMIT: entry refusal only; ST0/1/2 and block transfers are out of scope; the IRQ is injected via IRR; "
          "no gate change; no BIOS.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"{FAIL} issue-366 IRQ entry guard: {error}", file=sys.stderr)
        raise SystemExit(1) from error
