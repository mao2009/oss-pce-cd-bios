"""Condition matrix for the project-own src/ex_bcdbin.s on the real Geargrafx core (Issue #416).

OSS-ONLY: this project's spec (docs/ex-bcdbin.md). NOT the reference BIOS EX_BCDBIN; NOT a BIOS ABI check.
Expected values: independent spec model in tests/host/test_ex_bcdbin.py (model + oracle), never core output.
Run: python3 tests/integration/geargrafx_bcdbin_conditions.py [--output evidence.json] [--inject expect-value|expect-sp]
Exit: 0 PASS (all negative controls refused); 1 FAIL (includes --inject); 3 BLOCKED (not Linux, or pinned tools/core missing).
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests/host")]
from environment import DEPENDENCIES, core_path, executable  # noqa: E402
from test_ex_bcdbin import model, oracle  # noqa: E402

ROUTINE = ROOT / "src/ex_bcdbin.s"
HARNESS = ROOT / "tests/fixtures/bcdbin_cond_harness.s"
CFG = ROOT / "tests/fixtures/bcdbin.cfg"
BLOCKED_EXIT = 3
# (input C, X, Y, SP): mirrors c_tab / x_tab / y_tab / sp_tab in the harness
CASES = ((0, 0x5A, 0xA5, 0xFF), (1, 0x5A, 0xA5, 0xFF), (0, 0x00, 0x00, 0xFF), (1, 0xFF, 0xFF, 0xFF),
         (0, 0x12, 0x34, 0x80), (1, 0xAB, 0xCD, 0x80), (0, 0x5A, 0xA5, 0x80), (1, 0x00, 0xFF, 0xC0))
INPUTS = (0x00, 0x01, 0x09, 0x10, 0x19, 0x20, 0x42, 0x59, 0x90, 0x99, 0x0A, 0x0F, 0x9A, 0xA0, 0xC5, 0xFF)
HAND = {0x00: (0, 0), 0x42: (42, 0), 0x99: (99, 0), 0x0A: (0x0A, 1), 0x9A: (0x9A, 1), 0xC5: (0xC5, 1)}
BASE = 0x2200  # smoke marker; dump index = address - BASE
ARRAYS = {"a": 0x2300, "x": 0x2380, "y": 0x2400, "p": 0x2480, "sp": 0x2500}  # 128 records each
EXTRA = 0x2580 - BASE
# Negative controls: (name, old bytes, new bytes); each old pattern must occur exactly once in the built ROM.
ROM_MUTANTS = (
    ("derail: phx/phy -> nop nop (stack imbalance, returns to garbage)", "da 5a", "ea ea"),
    ("loop: phx/phy -> bra -2 (infinite loop inside the frame)", "da 5a", "80 fe"),
    ("sp-table: case 4 SP $80 -> $81 (routine unchanged)", "ff ff ff ff 80 80 80 c0", "ff ff ff ff 81 80 80 c0"),
    ("observed-sp: harness tsx -> nop (routine returns normally, SP not observed)", "ba 8e 14 20",
     "ea 8e 14 20"),
)
WORKER = ("import json, sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from geargrafx_smoke import execute\n"
          "try: print(json.dumps(execute(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))))\n"
          "except ValueError as error: print(json.dumps({'error': str(error)}))")


class Derailed(Exception):
    """The guest did not finish: a refusal recorded as a run outcome, not a crash."""


def expect(i, inject=None):
    """Independent expectation for record i = case*16 + input index. Balanced stack: SP after return = SP in."""
    case, k = divmod(i, len(INPUTS))
    _, x, y, sp = CASES[case]
    a, carry = model(INPUTS[k])
    if inject == "expect-value" and i == 6:  # deliberately wrong: $42 expected as $43
        a ^= 1
    if inject == "expect-sp":  # deliberately wrong SP expectation for every record
        sp -= 1
    return {"a": a, "c": carry, "x": x, "y": y, "sp": sp}


def mismatches(dumps, inject=None):
    """dumps: [cold, warm] RAM dumps starting at $2200. Returns (run, record, diff) tuples."""
    bad = []
    for run, ram in zip(("cold", "warm"), dumps):
        col = {name: ram[addr - BASE:addr - BASE + 128] for name, addr in ARRAYS.items()}
        for i in range(128):
            want = expect(i, inject)
            got = {"a": col["a"][i], "c": col["p"][i] & 1, "x": col["x"][i], "y": col["y"][i], "sp": col["sp"][i]}
            diff = {f: [got[f], want[f]] for f in want if got[f] != want[f]}
            if diff:
                bad.append((run, i, diff))
    return bad


def describe(bad):
    if not bad:
        return None
    run, i, diff = bad[0]
    return f"mismatch ({len(bad)} records): {run} case {i // 16} input ${INPUTS[i % 16]:02X} got/want {diff}"


def assemble(out):
    ca65, ld65 = executable("ca65"), executable("ld65")
    objects = []
    for source in (ROUTINE, HARNESS):
        objects.append(str(out / (source.stem + ".o")))
        subprocess.run([ca65, "--cpu", "huc6280", "-o", objects[-1], str(source)], check=True)
    rom = out / "bcdbin-cond-not-bios.pce"
    subprocess.run([ld65, "-C", str(CFG), "-o", str(rom), *objects], check=True)
    return rom


def run_core(core, rom):
    """Returns (core version, [cold dump, warm dump]). Raises Derailed when the guest hangs or never writes the marker."""
    try:
        result = subprocess.run([sys.executable, "-c", WORKER, str(ROOT / "tests/integration"), str(core), str(rom),
                                 str(EXTRA)], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise Derailed("guest hung: retro_run did not return within 60 s") from None
    if result.returncode:
        raise RuntimeError(f"Geargrafx worker failed: {result.stderr[-2000:]}")
    data = json.loads(result.stdout)
    if "error" in data:
        if data["error"].startswith("actual guest marker mismatch"):
            raise Derailed("guest did not reach the end marker (control flow derailed)")
        raise RuntimeError(data["error"])
    if data["core_version"] != DEPENDENCIES["geargrafx"]["revision"]:
        raise RuntimeError("core does not report the pinned revision")
    return data["core_version"], [bytes.fromhex(item) for item in data["cold_and_warm_reset_ram"]]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inject", choices=("expect-value", "expect-sp"))
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        print("BLOCKED real core: Linux-pinned target required", file=sys.stderr)
        return BLOCKED_EXIT
    try:
        core = core_path()
        executable("ca65"), executable("ld65")
    except (ValueError, OSError) as error:
        print(f"BLOCKED real core: {error}", file=sys.stderr)
        return BLOCKED_EXIT
    for a, want in HAND.items():
        assert model(a) == want == oracle(a), f"hand-computed case ${a:02X} disagrees with the model"

    with tempfile.TemporaryDirectory(prefix="pce-bcdbin-cond-") as tmp:
        out = Path(tmp)
        rom = assemble(out)
        data = rom.read_bytes()
        version, dumps = run_core(core, rom)  # unmutated ROM (positive control)
        positive = mismatches(dumps, args.inject)
        negatives = {
            "wrong expected value (core output)": describe(mismatches(dumps, "expect-value")),
            "wrong expected SP (core output)": describe(mismatches(dumps, "expect-sp")),
        }
        mutant_sha = {}
        for name, old, new in ROM_MUTANTS:
            if data.count(bytes.fromhex(old)) != 1:
                raise RuntimeError(f"mutant pattern not unique: {name}")
            mutant = out / "mutant.pce"
            mutant.write_bytes(data.replace(bytes.fromhex(old), bytes.fromhex(new)))
            mutant_sha[name] = sha(mutant.read_bytes())
            try:
                negatives[name] = describe(mismatches(run_core(core, mutant)[1]))
            except Derailed as error:
                negatives[name] = f"refused by run: {error}"
    refused = {name: text is not None for name, text in negatives.items()}
    status = "PASS" if not positive and all(refused.values()) else "FAIL"
    matrix = []
    for c, (cin, x, y, sp) in enumerate(CASES):
        bad = sum(1 for _, i, _ in positive if i // len(INPUTS) == c)
        matrix.append({"case": c, "input_c": cin, "x": x, "y": y, "sp": sp,
                       "records": 2 * len(INPUTS), "passed": 2 * len(INPUTS) - bad})
    evidence = {
        "schema_version": 1, "issue": 416, "status": status, "exit_code": 0 if status == "PASS" else 1,
        "scope": "OSS-only project routine src/ex_bcdbin.s; NOT the reference BIOS EX_BCDBIN; not a BIOS ABI check",
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"], "core_version": version,
        "core_sha256": sha(core.read_bytes()), "rom_sha256": sha(data),
        "probe_sha256": sha(HARNESS.read_bytes() + CFG.read_bytes()), "routine_sha256": sha(ROUTINE.read_bytes()),
        "raw_output_sha256": sha(dumps[0] + dumps[1]), "inject": args.inject,
        "model_source": "tests/host/test_ex_bcdbin.py model() and oracle() (spec formulations, not core output)",
        "matrix": matrix, "positive_mismatches": len(positive), "first_positive_mismatch": describe(positive),
        "negative_controls": {name: {"refused": refused[name], "reason": negatives[name]} for name in negatives},
        "negative_controls_refused": sum(refused.values()), "mutant_sha256": mutant_sha,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"{status}: {sum(c['passed'] for c in matrix)}/{2 * len(INPUTS) * len(CASES)} records passed; "
          f"negative controls refused {sum(refused.values())}/{len(refused)}; core {version}")
    if positive:
        print(describe(positive), file=sys.stderr)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
