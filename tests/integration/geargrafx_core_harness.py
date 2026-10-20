"""HuC6280 core verification harness on the real Geargrafx core (test-only, NOT a BIOS ABI proof).

Each case JSRs one routine in bank0 with synthetic A/X/Y/P and a 32-byte zero-page seed
($20E0-$20FF), then records A/X/Y/P/SP/MPR and the ZP window before and after, cold and warm.
Status per cycle: RETURNED (harness_done reached after RTS), TRAPPED (IRQ/BRK vector taken) or
TIMEOUT (step budget used up). Only RETURNED with every expectation met is PASS.

Plug-in: --cases FILE.json, a list of {"name", "target" (symbol from the harness overlay or
build/<mode>/bios-dev/dev.lbl), "a", "x", "y", "p", "ram" (64 hex chars, optional),
"expect": {"a"/"x"/"y": int, "p_mask"/"p": int, "ram": {"offset": byte}}}. ZP bytes not named
in expect.ram must be unchanged (writes outside the window are not observed); plug-in cases cannot declare "want" (always PASS+RETURNED). Built-in self-test cases always run first and must behave.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnostics import OVERLAY_FILE_START  # noqa: E402

STEPS = 2048  # probe maximum, per cycle
CHECKPOINTS = ("harness_call", "harness_after", "harness_done", "harness_trap")
T_FLAG, I_FLAG = 0x20, 0x04
SELFTEST = [  # validates the harness; "want" is the verdict/status the harness must report
    {"name": "selftest_add", "target": "harness_selftest", "a": 0x40, "x": 0x02, "y": 0x99, "p": 0x05,
     "ram": "00" * 30 + "10ff", "want": ["PASS", "RETURNED"],
     "expect": {"a": 0x43, "x": 0x02, "y": 0x99, "p_mask": 0x01, "p": 0x00,
                "ram": {"0": 0x02, "30": 0x11, "31": 0x43}}},
    {"name": "selftest_add_carry_out", "target": "harness_selftest", "a": 0xff, "x": 0x01, "y": 0, "p": 0x04,
     "want": ["PASS", "RETURNED"],
     "expect": {"a": 0x00, "p_mask": 0x83, "p": 0x01, "ram": {"0": 0x01, "30": 0x01, "31": 0x00}}},  # C from ADC; N/Z from the final INC
    {"name": "neg_wrong_expectation", "target": "harness_selftest", "a": 1, "x": 1, "y": 0, "p": 0x04,
     "want": ["FAIL", "RETURNED"], "expect": {"a": 0x03, "ram": {"0": 1, "30": 1, "31": 2}}},
    {"name": "neg_undeclared_ram_write", "target": "harness_selftest", "a": 1, "x": 1, "y": 0, "p": 0x04,
     "want": ["FAIL", "RETURNED"], "expect": {"a": 0x02, "ram": {"0": 1, "31": 2}}},
    {"name": "neg_hang", "target": "harness_selftest_hang", "a": 0, "x": 0, "y": 0, "p": 0x04,
     "want": ["FAIL", "TIMEOUT"], "expect": {}},
    {"name": "neg_brk", "target": "harness_selftest_brk", "a": 0, "x": 0, "y": 0, "p": 0x04,
     "want": ["FAIL", "TRAPPED"], "expect": {}},
    {"name": "plugin_demo_dev_rom_ex_getver", "target": "api_slot_1E", "a": 0x11, "x": 0x22, "y": 0x33, "p": 0x04,
     "want": ["PASS", "RETURNED"], "expect": {"a": 0x11, "x": 3, "y": 0}},
]


def reject_duplicate_keys(pairs):
    """json object_pairs_hook: json.loads keeps the last duplicate silently, so refuse it."""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key {key!r}: the earlier value would be silently dropped")
        obj[key] = value
    return obj


def load_cases(text):
    return json.loads(text, object_pairs_hook=reject_duplicate_keys)


def parse_lbl(text):
    return {n: int(a, 16) for a, n in re.findall(r"^al ([0-9A-Fa-f]{6}) \.(\w+)$", text, re.M)}


def ram_writes(expect, name="case"):
    """Normalise expect.ram to {offset: value}; two spellings of one offset are an error, not last-wins."""
    writes = {}
    for key, value in expect.get("ram", {}).items():
        offset = int(key, 0)
        if offset in writes:
            raise ValueError(f"{name}: duplicate expect.ram offset {offset} ({key!r})")
        writes[offset] = value
    return writes


def validate(case, symbols, plugin=False):
    """Reject anything a judge could silently ignore; every problem is a ValueError.

    plugin=True (user --cases): "want" is refused outright, so a plug-in routine can only PASS.
    Only the built-in self-tests may declare an expected non-PASS verdict.
    """
    def byte(v):
        return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255
    if not isinstance(case, dict):
        raise ValueError(f"case {case!r}: must be a JSON object")
    if plugin and "want" in case:
        raise ValueError(f"case {case.get('name')!r}: plug-in cases cannot declare 'want'")
    expect = case.get("expect")
    if not isinstance(case.get("name"), str) or not isinstance(expect, dict) \
            or set(case) - {"name", "target", "a", "x", "y", "p", "ram", "expect", "want"} \
            or set(expect) - {"a", "x", "y", "p", "p_mask", "ram"} or ("p" in expect) != ("p_mask" in expect):
        raise ValueError(f"case {case.get('name')!r}: unknown/missing keys (p needs p_mask)")
    target = case.get("target")
    if not isinstance(target, str) or target not in symbols or not 0xE000 <= symbols[target] <= 0xFFFF:
        raise ValueError(f"{case['name']}: target {target!r} is not a bank0 symbol name")
    if not all(byte(case.get(r)) for r in "axyp") or case["p"] & T_FLAG or not case["p"] & I_FLAG:
        raise ValueError(f"{case['name']}: inputs must be bytes, P needs I=1 and T=0")
    if not isinstance(expect.get("ram", {}), dict) or not isinstance(case.get("ram", ""), str):
        raise ValueError(f"{case['name']}: ram seed must be hex text, expect.ram an object")
    if "p_mask" in expect and not (byte(expect["p_mask"]) and expect["p_mask"]):
        raise ValueError(f"{case['name']}: p_mask must be a non-zero byte, or P is never checked")
    if expect.get("p_mask", 0) & 0x30:  # B/T are not inputs (judge ignores them): a check on them is empty
        raise ValueError(f"{case['name']}: p_mask must not include B/T (0x30); they are not compared")
    if plugin and not (set(expect) & {"a", "x", "y"} or "p" in expect or expect.get("ram")):
        raise ValueError(f"{case['name']}: plug-in expect must check at least one of a/x/y/p/ram "
                         "(an empty expect would PASS any returning routine)")
    if not all(byte(expect[k]) for k in expect if k != "ram") or not all(
            re.fullmatch(r"0x[0-9a-fA-F]+|[1-9][0-9]*|0", k) and int(k, 0) < 32 and byte(v)
            for k, v in expect.get("ram", {}).items()):
        raise ValueError(f"{case['name']}: expect values must be bytes, ram offsets 0..31")
    ram_writes(expect, case["name"])  # rejects duplicate offsets such as "0" and "0x0"
    if len(bytes.fromhex(case.get("ram", "00" * 32))) != 32:
        raise ValueError(f"{case['name']}: ram seed must be 32 bytes")


def classify(records, cycle, row, symbols):
    phases = {r["phase"] for r in records["TRACE"] if r["cycle"] == cycle}
    if "harness_trap" in phases:
        return "TRAPPED"
    if {"harness_after", "harness_done"} <= phases and row["pc"] == symbols["harness_done"]:
        return "RETURNED"
    return "TIMEOUT"  # unfinished within the step budget: never PASS


def judge(records, case, symbols):
    """Return (verdict, statuses, failures, observations); PASS needs RETURNED + every expectation."""
    seed = bytes.fromhex(case.get("ram", "00" * 32))
    expect, failures, statuses, observed = case["expect"], [], {}, {}
    if [r["phase"] for r in records["OBS"]] != ["loaded", "cold", "nonreturning", "warm"]:
        return "FAIL", {}, ["probe observations incomplete"], {}
    for cycle, row in (("cold", records["OBS"][1]), ("warm", records["OBS"][3])):
        statuses[cycle] = status = classify(records, cycle, row, symbols)
        if status != "RETURNED":
            hint = (" (step budget of %d exhausted: infinite loop or routine longer than the budget)"
                    % STEPS) if status == "TIMEOUT" else ""
            failures.append(f"{cycle}: {status}{hint}")
            continue
        before, after = (next(r for r in records["TRACE"] if r["cycle"] == cycle and r["phase"] == p)
                         for p in ("harness_call", "harness_after"))
        ram = bytes.fromhex(row["work_ram_0200_hex"])
        pre, post = ram[:32], ram[32:64]
        keys = ("a", "x", "y", "p", "sp", "mpr")
        observed[cycle] = {"before": {k: before[k] for k in keys} | {"zp": pre.hex()},
                           "after": {k: after[k] for k in keys} | {"zp": post.hex()}}
        if pre != seed or [before[r] for r in "axy"] != [case[r] for r in "axy"] \
                or before["mpr"][:2] != [0xFF, 0xF8] or before["mpr"][7] != 0:
            failures.append(f"{cycle}: inputs not delivered or pre-snapshot clobbered")
        if (before["p"] ^ case["p"]) & 0xCF:  # B/T bits are not inputs
            failures.append(f"{cycle}: P input not delivered")
        if before["sp"] != 0xFF or after["sp"] != 0xFF or after["mpr"] != before["mpr"]:
            failures.append(f"{cycle}: SP/MPR not balanced across the call")
        for reg in "axy":
            if reg in expect and after[reg] != expect[reg]:
                failures.append(f"{cycle}: {reg}={after[reg]:#04x}, expected {expect[reg]:#04x}")
        if (after["p"] ^ expect.get("p", 0)) & expect.get("p_mask", 0):
            failures.append(f"{cycle}: P={after['p']:#04x} mismatches mask {expect['p_mask']:#04x}")
        want = bytearray(seed)
        for offset, value in ram_writes(expect, case["name"]).items():
            want[offset] = value
        if post != want:
            failures.append(f"{cycle}: ZP {post.hex()} != expected {bytes(want).hex()}")
    return ("PASS" if not failures else "FAIL"), statuses, failures, observed


def build_overlay(output):
    from environment import executable
    ca65, ld65 = executable("ca65"), executable("ld65")
    obj, overlay, labels = output / "core-harness.o", output / "overlay.bin", output / "core-harness.lbl"
    subprocess.run([ca65, str(ROOT / "tests/fixtures/core-harness.s"), "-o", str(obj)], check=True)
    subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/contract-probe.cfg"), "-o", str(overlay),
                    "-Ln", str(labels), str(obj)], check=True)
    code = overlay.read_bytes()
    if len(code) != 8192 or code[:0x800] != b"\xff" * 0x800 or code[0x1000:] != b"\xff" * 0x1000:
        raise ValueError("harness escaped the $E800-$EFFF test overlay")
    return code, parse_lbl(labels.read_text())


def image(base, code, symbols, case):
    data = bytearray(base)
    data[OVERLAY_FILE_START:0x1000] = code[OVERLAY_FILE_START:0x1000]
    data[0x1FF6:0x1FFE] = symbols["harness_trap"].to_bytes(2, "little") * 4
    data[0x1FFE:0x2000] = symbols["harness_entry"].to_bytes(2, "little")
    at = symbols["harness_input"] - 0xE000
    data[at:at + 36] = bytes(case[r] for r in "axyp") + bytes.fromhex(case.get("ram", "00" * 32))
    call = symbols["harness_call"] - 0xE000
    if data[call] != 0x20:
        raise ValueError("harness JSR opcode missing")
    data[call + 1:call + 3] = symbols[case["target"]].to_bytes(2, "little")
    return bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    parser.add_argument("--cases", type=Path, help="JSON list of plug-in routine cases")
    args = parser.parse_args()
    if sys.platform != "linux":  # pinned Geargrafx core is a Linux .so; never skip silently
        raise ValueError(f"real-core harness is Linux-pinned (platform {sys.platform}); BLOCKED, not PASS")
    from build_rom import verify_dev_rom
    from environment import DEPENDENCIES
    from geargrafx_api import compile_probe
    from geargrafx_contracts import execute
    dev = ROOT / "build" / args.mode / "bios-dev"
    base = (dev / "dev-only-not-compatible-syscard3.pce").read_bytes()
    verify_dev_rom(base, manifest=dev / "manifest.json")
    if base[OVERLAY_FILE_START:0x1000] != b"\xff" * (0x1000 - OVERLAY_FILE_START):
        raise ValueError("DEV ROM uses the $E800-$EFFF overlay window")
    out = ROOT / "build" / args.mode / "core-harness"
    out.mkdir(parents=True, exist_ok=True)
    code, harness = build_overlay(out)
    symbols = parse_lbl((dev / "dev.lbl").read_text()) | harness
    user = load_cases(args.cases.read_text(encoding="utf-8")) if args.cases else []
    if not isinstance(user, list):
        raise ValueError("--cases must be a JSON list")
    cases = SELFTEST + user
    for case in SELFTEST:
        validate(case, symbols)
    for case in user:
        validate(case, symbols, plugin=True)
    probe = compile_probe(out)
    labels = out / "trace-labels.txt"
    labels.write_text("".join(f"{n} {symbols[n]:04x}\n" for n in CHECKPOINTS))
    results, bad = [], []
    with tempfile.TemporaryDirectory(prefix="pce-core-harness-") as tmp:
        for index, case in enumerate(cases):
            rom = Path(tmp) / f"case{index:03d}.pce"
            rom.write_bytes(image(base, code, symbols, case))
            records = execute(probe, rom, "-", labels, media=False, steps=STEPS)
            verdict, statuses, failures, observed = judge(records, case, symbols)
            want = case.get("want", ["PASS", "RETURNED"])
            ok = verdict == want[0] and set(statuses.values()) == {want[1]}
            results.append({"name": case["name"], "target": case["target"], "verdict": verdict,
                            "status": statuses, "failures": failures, "observed": observed,
                            "rom_sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
                            "harness_behaved": ok})
            print(f"{case['name']}: {verdict} {statuses} {'ok' if ok else 'UNEXPECTED'} {failures}")
            if not ok:
                bad.append(case["name"])
    (out / "core-harness-evidence.json").write_text(json.dumps({
        "scope": "original harness on real Geargrafx, no-media HuCard load; NOT reference BIOS ABI",
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"], "steps_per_cycle": STEPS,
        "cases": results}, indent=2) + "\n")
    if bad:
        raise ValueError(f"unexpected verdicts: {bad}")
    print(f"PASS core harness: {len(results)} cases behaved as declared (cold+warm)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"core harness FAIL/BLOCKED: {error}") from error
