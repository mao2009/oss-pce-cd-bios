"""Direct BIOS API-entry observer for Issue #635 (synthetic + opt-in commercial).

Observation only: reuses the pinned-core single-step probe (geargrafx_api_entry_probe.cpp)
to record WRAM->BIOS transitions (caller PC, entry PC, slot, SP delta, entry regs,
return booleans). Synthetic controls prove the instrument distinguishes JSR-to-table,
JMP-to-table, direct-stub, and no-call.

Commercial mode is opt-in only: the probe runs in "commercial" mode and generates
no RAM dumps and no stack bytes at the C++ boundary. The harness requires an
explicit --commercial-output path outside the repository (local-only), writes
structural JSON only, never logs input/output paths or media identifiers, and
distinguishes API_ENTRY_CONFIRMED from PRE_API_BLOCKER_OBSERVED / UNKNOWN /
INCOMPLETE / BUDGET / FAIL (see classify_commercial).
"""
import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(Path(__file__).resolve().parent)]
from build_dev_ipl_loader_bios import build_ipl_loader_bios  # noqa: E402
from environment import DEPENDENCIES, executable  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
import geargrafx_ipl_loader as L  # noqa: E402


def entry_of(slot: int) -> int:
    return 0xE000 + slot * 3


def build_payload(out: Path, name: str, defines: list) -> tuple:
    out.mkdir(parents=True, exist_ok=True)
    obj, rom, lbl, cfg = out / f"{name}.o", out / f"{name}.bin", out / f"{name}.lbl", out / f"{name}.cfg"
    cfg.write_text("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
                   "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n", encoding="ascii")
    subprocess.run([executable("ca65"), str(Path(__file__).resolve().parent / "ipl_api_entry_payload.s"),
                    *defines, "-o", str(obj), "-g"], check=True)
    subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(rom), "-Ln", str(lbl), str(obj)], check=True)
    code = rom.read_bytes()
    if not 1 <= len(code) <= 2048:
        raise ValueError("payload must fit one sector")
    return code


def run_probe(probe, rom, cue, labels, budget, mode="synthetic"):
    # Paths stay local-only: errors are sanitized to fixed codes below (no paths logged).
    try:
        result = subprocess.run([str(probe), str(rom), str(cue), str(labels), str(budget), mode],
                                check=True, capture_output=True, text=True, timeout=900)
    except subprocess.CalledProcessError:
        raise ValueError(f"probe execution failed (mode {mode})")
    except subprocess.TimeoutExpired:
        raise ValueError("probe timeout")
    records = {"CAP": [], "HIT": [], "PHASE": [], "RUN": [], "APICALL": []}
    for line in result.stdout.splitlines():
        tag, _, body = line.partition(" ")
        if tag in records:
            records[tag].append(json.loads(body))
    if len(records["RUN"]) != 2:
        raise ValueError("probe did not complete cold and warm runs")
    if mode == "commercial":
        assert_commercial_clean(records)
    return records


def assert_commercial_clean(records):
    """Fail-closed audit of all commercial probe tags (CAP/HIT/PHASE/RUN/APICALL)."""
    for rows in records.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("commercial output row is not an object")
            if any(k.endswith("_hex") for k in row):
                raise ValueError("commercial output contains RAM dump fields")
            if {"stack_b0", "stack_b1", "stack_word"} & set(row):
                raise ValueError("commercial output contains raw stack values")


def validate_commercial_output(path, root=ROOT):
    """Fail-closed validation for the local-only commercial evidence path.

    Requires an absolute path outside the repository and outside every known
    worktree, whose parent exists as a private owned directory (0700, no
    symlink, no intermediate symlink), and whose file does not yet exist
    (no overwrite, symlink rejected). Returns the path or raises ValueError.
    """
    supplied = Path(path)
    if not supplied.is_absolute():
        raise ValueError("commercial output must be absolute")
    out = Path(os.path.abspath(os.fspath(supplied)))
    root_abs = Path(os.path.abspath(os.fspath(root)))
    if out == root_abs or out.is_relative_to(root_abs):
        raise ValueError("commercial output must be outside the repository")
    # Canonical checkout and any /tmp/opencode worktree child are also forbidden.
    candidates = [root_abs, Path("/home/loach/src/oss-pce-cd-bios")]
    opencode_root = Path("/tmp/opencode")
    try:
        if opencode_root.is_dir():
            for child in opencode_root.iterdir():
                try:
                    if child.is_dir() and (child / ".git").exists():
                        candidates.append(child)
                except OSError:
                    continue
    except OSError:
        pass
    for cand in candidates:
        try:
            if out == cand or out.is_relative_to(cand):
                raise ValueError("commercial output must be outside worktrees")
        except ValueError as error:
            if str(error) == "commercial output must be outside worktrees":
                raise
    parent = out.parent
    try:
        st = os.lstat(parent)
    except OSError:
        raise ValueError("commercial output parent missing")
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ValueError("commercial output parent is not a directory")
    if Path(os.path.realpath(parent)) != parent:
        raise ValueError("commercial output parent contains symlink")
    if st.st_uid != os.getuid():
        raise ValueError("commercial output parent not owned")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise ValueError("commercial output parent must be private (0700)")
    try:
        fst = os.lstat(out)
        if stat.S_ISLNK(fst.st_mode):
            raise ValueError("commercial output must not be symlink")
        raise ValueError("commercial output already exists (no overwrite)")
    except FileNotFoundError:
        pass
    return out


def classify_apicall(call):
    """Return JSR/JMP/INTERRUPT/DIRECT/IRQ/OTHER based on SP delta and entry PC."""
    if call["in_table"]:
        if call["sp_delta"] == 2:
            return "JSR"
        if call["sp_delta"] == 0:
            return "JMP"
        if call["sp_delta"] == 3:
            return "INTERRUPT"
        return "OTHER"
    # Non-table BIOS entry: stub direct vs interrupt vector vs other.
    if call.get("entry_pc") == 0xE24F:
        return "DIRECT"
    if call["sp_delta"] == 3 or call.get("entry_pc", 0) >= 0xFFF0:
        return "IRQ"
    return "OTHER_DIRECT"


def jsr_confirmed(call):
    """True only with table entry + SP delta 2 + internal return MATCH (MPR1 WRAM)."""
    return (call.get("in_table") is True and call.get("sp_delta") == 2
            and call.get("stack_check") == "MATCH"
            and call.get("return_matches_caller") is True
            and isinstance(call.get("mpr"), list) and len(call["mpr"]) == 8
            and call["mpr"][1] == 0xF8)


def classify_commercial(records):
    """Verdict for a commercial run: distinguish confirmed entry from pre-API blocks.

    Returns (verdict, detail) where verdict is one of
    API_ENTRY_CONFIRMED / PRE_API_BLOCKER_OBSERVED / UNKNOWN / INCOMPLETE / BUDGET / FAIL.
    Never promotes a budget or a truncated trace to a confirmation.
    """
    runs = records.get("RUN", [])
    apis = records.get("APICALL", [])
    if len(runs) != 2:
        return "FAIL", "missing cold/warm runs"
    if any(r.get("reason") == "budget" or r.get("halt_confirmed") is not True for r in runs):
        return "BUDGET", "no confirmed halt (budget is not a halt)"
    if any(r.get("apicall_truncated") is True for r in runs):
        return "INCOMPLETE", "APICALL trace truncated"
    by_cycle = {}
    for cycle in ("cold", "warm"):
        calls = [c for c in apis if c.get("cycle") == cycle]
        cruns = [r for r in runs if r.get("cycle") == cycle]
        if len(cruns) != 1:
            return "FAIL", f"run count for {cycle}"
        by_cycle[cycle] = (calls, cruns[0])
    cold_calls, cold_run = by_cycle["cold"]
    warm_calls, warm_run = by_cycle["warm"]
    if not cold_calls and not warm_calls:
        if cold_run["reason"] == "cd_halt_fail" and warm_run["reason"] == "cd_halt_fail":
            return "PRE_API_BLOCKER_OBSERVED", "no API entry; IPL validation halt in both cycles"
        return "UNKNOWN", "no API entry and no clear pre-API halt"
    # Confirmed entry needs a table JSR/JMP in both cycles, same slot+method, stub halt.
    def first_entry(calls):
        if len(calls) != 1:
            return None
        call = calls[0]
        method = classify_apicall(call)
        if method == "JSR":
            if not jsr_confirmed(call):
                return None
        elif method != "JMP":
            return None
        return (call["slot"], method)
    cold_first, warm_first = first_entry(cold_calls), first_entry(warm_calls)
    if cold_first is None or warm_first is None:
        return "UNKNOWN", "entry present without JSR/JMP evidence"
    if cold_first != warm_first:
        return "UNKNOWN", "cold/warm entries differ"
    if cold_run["reason"] != "api_unimplemented" or warm_run["reason"] != "api_unimplemented":
        return "UNKNOWN", "entry does not lead to stub halt"
    return "API_ENTRY_CONFIRMED", f"first table entry slot {cold_first[0]} {cold_first[1]} in both cycles"


def synthetic_case(tmp: Path, probe, bios, stub, name, defines, expect_slot, expect_method, budget=20_000_000):
    code = build_payload(tmp, name, defines)
    body = L.body_for(code, 1)
    info = L.info_sector(2, 1, 0x3000, 0x3000, L.MPRS_STD, 0, sum(body) & 0xFFFF)
    cue = L.make_disc(tmp / name, L.case_disc({"n": 1, "iplblk": 2}, body, info))
    labels = tmp / f"{name}.labels"
    labels.write_text(f"cd_halt_ok {stub['payload_halt']:04x}\n" if "payload_halt" in stub else "", encoding="ascii")
    # payload_halt address comes from the payload label file; the loader BIOS does not contain it.
    # The harness maps the payload halt to cd_halt_ok for the probe's halt set.
    # Read it from the build label file written by build_payload.
    import re
    lbl_text = (tmp / f"{name}.lbl").read_text()
    m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", lbl_text, re.M)
    if not m:
        raise ValueError(f"{name}: payload_halt label missing")
    halt = int(m.group(1), 16)
    labels.write_text(f"cd_halt_ok {halt:04x}\n"
                      f"cd_halt_fail {stub['cd_halt_fail']:04x}\n"
                      f"cd_unexpected_interrupt {stub['cd_unexpected_interrupt']:04x}\n"
                      f"api_unimplemented {stub['api_unimplemented']:04x}\n", encoding="ascii")
    records = run_probe(probe, bios, cue, labels, budget, "synthetic")
    verdicts = []
    for cycle in ("cold", "warm"):
        calls = [c for c in records["APICALL"] if c["cycle"] == cycle]
        runs = [r for r in records["RUN"] if r["cycle"] == cycle]
        if len(runs) != 1:
            raise ValueError(f"{name} {cycle}: RUN count")
        verdicts.append({"cycle": cycle, "calls": calls, "run": runs[0]})
    # Judge per cycle
    fails = []
    for v in verdicts:
        calls, run = v["calls"], v["run"]
        if expect_method == "SPIN":
            if calls:
                fails.append(f"{v['cycle']}: unexpected APICALL on spin {calls}")
            if run["reason"] != "budget" or run["halt_confirmed"]:
                fails.append(f"{v['cycle']}: spin must be budget, got {run['reason']} {run['halt_confirmed']}")
        elif expect_method == "NOCALL":
            if calls:
                fails.append(f"{v['cycle']}: unexpected APICALL {calls}")
            if run["reason"] != "cd_halt_ok" or not run["halt_confirmed"]:
                fails.append(f"{v['cycle']}: expected cd_halt_ok halt, got {run['reason']} {run['halt_confirmed']}")
        else:
            if len(calls) != 1:
                fails.append(f"{v['cycle']}: expected exactly one APICALL, got {len(calls)}")
                continue
            call = calls[0]
            method = classify_apicall(call)
            if expect_method == "DIRECT":
                if call["in_table"]:
                    fails.append(f"{v['cycle']}: direct stub was classified as table entry")
                if run["reason"] != "api_unimplemented" or run["x"] != 2:
                    fails.append(f"{v['cycle']}: direct stub halt X!=2")
            else:
                if not call["in_table"] or call["slot"] != expect_slot:
                    fails.append(f"{v['cycle']}: slot {call} != {expect_slot}")
                if method != expect_method:
                    fails.append(f"{v['cycle']}: method {method} != {expect_method} (delta {call['sp_delta']})")
                if expect_method == "JSR":
                    if call["mpr"][1] != 0xF8:
                        fails.append(f"{v['cycle']}: MPR1 != $F8, stack assumption invalid")
                    elif call.get("stack_check") != "MATCH" or call.get("return_matches_caller") is not True:
                        fails.append(f"{v['cycle']}: JSR return not confirmed by internal check")
                # Entry regs must match preset (JMP case loads MARK into A before jumping).
                if not (call["caller_a"] in (0xAA, 0xAB) and call["caller_x"] == 0x11 and call["caller_y"] == 0x22):
                    fails.append(f"{v['cycle']}: entry regs {call} != preset AA/AB,11,22")
                if run["reason"] != "api_unimplemented" or run["x"] != expect_slot:
                    fails.append(f"{v['cycle']}: halt {run['reason']} x={run['x']}")
    if fails:
        raise ValueError(f"{name} failed: {fails}")
    return verdicts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    parser.add_argument("--commercial-rom", type=Path)
    parser.add_argument("--commercial-cue", type=Path)
    parser.add_argument("--commercial-labels", type=Path)
    parser.add_argument("--commercial-output", type=Path,
                        help="local-only evidence JSON outside the repo (commercial mode requires this)")
    args = parser.parse_args()

    original = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    if not original.is_file():
        raise SystemExit("BLOCKED: run make dev-rom first")
    out = ROOT / "build" / args.mode / "api-entry"
    out.mkdir(parents=True, exist_ok=True)

    if args.commercial_rom:
        # Opt-in commercial mode: caller supplies DEV BIOS + local CUE + labels + local-only output.
        if not args.commercial_cue or not args.commercial_labels or not args.commercial_output:
            raise SystemExit("FAIL: commercial mode needs --commercial-cue, --commercial-labels and --commercial-output")
        try:
            dest = validate_commercial_output(args.commercial_output)
        except ValueError as error:
            raise SystemExit(f"FAIL commercial output rejected: {error}")
        probe = compile_probe(out, "geargrafx_api_entry_probe.cpp", "geargrafx-api-entry-probe")
        budget = 80_000_000
        try:
            records = run_probe(probe, args.commercial_rom, args.commercial_cue, args.commercial_labels, budget, "commercial")
        except ValueError as error:
            raise SystemExit(f"FAIL commercial observation: {error}")
        verdict, detail = classify_commercial(records)
        # Structural summary only (no paths/hashes/bytes; no stack values).
        for cycle in ("cold", "warm"):
            calls = [c for c in records["APICALL"] if c["cycle"] == cycle]
            runs = [r for r in records["RUN"] if r["cycle"] == cycle]
            trunc = runs[0].get("apicall_truncated", False) if runs else False
            print(f"cycle={cycle} apicalls={len(calls)} truncated={trunc} runs={[(r['reason'], r['halt_confirmed']) for r in runs]}")
            for c in calls:
                print(f"  caller={hex(c['caller_pc'])} entry={hex(c['entry_pc'])} table={c['in_table']} slot={c['slot']} "
                      f"delta={c['sp_delta']} method={classify_apicall(c)} return={c.get('stack_check')}")
        evidence = {"verdict": verdict, "detail": detail, "apicalls": records["APICALL"],
                    "runs": [{k: r[k] for k in ("cycle", "reason", "pc", "x", "halt_confirmed", "step")} |
                             {"apicall_truncated": r.get("apicall_truncated", False)} for r in records["RUN"]]}
        try:
            with open(dest, "x") as handle:
                handle.write(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        except OSError:
            raise SystemExit("FAIL commercial evidence save failed")
        if verdict in ("API_ENTRY_CONFIRMED", "PRE_API_BLOCKER_OBSERVED"):
            print(f"OBSERVED commercial API-entry: {verdict} (structural only)")
            return 0
        print(f"NOT-CONFIRMED commercial API-entry: {verdict} ({detail})")
        return 1

    # Synthetic controls
    bios, _ = build_ipl_loader_bios(original, out, L.SEL, args.mode, "main")
    from build_cdload_exec import parse_labels
    stub = parse_labels(out / "ipl-loader-bios-main.lbl")
    for needed in ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"):
        if needed not in stub:
            raise SystemExit(f"BLOCKED: label {needed} missing")
    if stub["api_unimplemented"] != 0xE24F:
        raise SystemExit(f"FAIL: payload STUB $E24F != BIOS stub {hex(stub['api_unimplemented'])}")
    probe = compile_probe(out, "geargrafx_api_entry_probe.cpp", "geargrafx-api-entry-probe")
    with tempfile.TemporaryDirectory(prefix="pce-api-entry-") as tmp:
        tmp = Path(tmp)
        cases = [
            ("jsr_slot02", [f"-DJSR_SLOT=${entry_of(0x02):04X}"], 0x02, "JSR", 20_000_000),
            ("jsr_slot03", [f"-DJSR_SLOT=${entry_of(0x03):04X}"], 0x03, "JSR", 20_000_000),
            ("jmp_slot02", [f"-DJMP_SLOT=${entry_of(0x02):04X}"], 0x02, "JMP", 20_000_000),
            ("direct_stub", ["-DDIRECT_STUB"], 0x02, "DIRECT", 20_000_000),
            ("nocall", ["-DNOCALL"], None, "NOCALL", 20_000_000),
            ("spin", ["-DSPIN"], None, "SPIN", 500_000),
        ]
        for name, defines, slot, method, budget in cases:
            verdicts = synthetic_case(tmp, probe, bios, stub, name, defines, slot, method, budget)
            print(f"PASS {name} cold/warm method={method} slot={slot}")
    print("PASS synthetic API-entry controls (JSR/JMP/DIRECT/NOCALL/SPIN distinguished)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
