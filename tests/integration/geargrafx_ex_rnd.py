"""Run the project-own EX_RND probe on the real Geargrafx core (Issue #72, opt-in).

Evidence scope: this project's own LFSR routine only. No System Card code runs and nothing here
confirms the reference $3E/$E0BA ABI. Reproducibility and zero-state behaviour are checked on
the core; the reference contract is not.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import DEPENDENCIES, core_path  # noqa: E402
from ex_rnd import build_probe, rnd_sequence  # noqa: E402
from geargrafx_ex_binbcd import run_worker  # noqa: E402  (shared real-core worker, RAM $2200-$25FF)

# RAM offsets relative to $2200 (the window run_worker returns): phases at $2300/$2400/$2500.
PHASES = (("zero-state", 0x0000, 0x100, 0), ("seed-1234", 0x1234, 0x200, 0), ("seed-0", 0x0000, 0x300, 0))


def mismatches(ram):
    """Compare guest outputs with the host model; return human-readable failures."""
    if ram[:4] != b"RND!":
        return [f"completion marker missing: {ram[:4].hex()}"]
    bad = []
    if ram[4]:  # one-byte error flag at $2204 (offset 4); $2205 holds the seed pattern
        bad.append("X not preserved (error flag set)")
    for name, seed, offset, _ in PHASES:
        want = bytes(rnd_sequence(seed, 256))
        got = ram[offset:offset + 256]
        if got != want:
            first = next(i for i in range(256) if got[i] != want[i])
            bad.append(f"{name}: first mismatch at call {first} got {got[first]:02x} want {want[first]:02x}")
    return bad


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    core = core_path()
    out = ROOT / "build" / args.mode / "ex-rnd"
    rom, symbols = build_probe(out)
    observed = run_worker(core, rom)
    if observed["core_version"] != DEPENDENCIES["geargrafx"]["revision"]:
        raise ValueError("Geargrafx core does not report the pinned revision")
    ram = bytes.fromhex(observed["ram_hex"])
    bad = mismatches(ram)
    if bad:
        raise ValueError("guest/model mismatch: " + "; ".join(bad[:8]))
    # Negative: EOR #$B4 -> EOR #$B5 must be caught by the same real-core comparison.
    data = bytearray(rom.read_bytes())
    offset = symbols["ex_rnd_taps"] - 0xE000
    if data[offset:offset + 2] != b"\x49\xb4":
        raise ValueError("mutation point is not EOR #$B4")
    data[offset + 1] = 0xB5
    negative = out / "mutated-taps.pce"
    negative.write_bytes(data)
    if not mismatches(bytes.fromhex(run_worker(core, negative)["ram_hex"])):
        raise ValueError("mutated routine passed on the real core")
    (out / "ex-rnd-evidence.json").write_text(json.dumps({
        "scope": "project-own EX_RND LFSR only; System Card $3E ABI UNCONFIRMED",
        "status": "PASS", "calls_per_phase": 256, "frames": 10,
        "phases": {name: {"seed": seed, "ram_offset": offset} for name, seed, offset, _ in PHASES},
        "rom_sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
        "core_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
        "geargrafx_revision": observed["core_version"], "mutation_negative": "PASS_EXPECTED_FAILURE",
    }, indent=2, sort_keys=True) + "\n")
    print("PASS real Geargrafx EX_RND (project-own LFSR): 3 phases x 256 calls match host model; mutation rejected")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"EX_RND integration FAIL/BLOCKED: {error}") from error
