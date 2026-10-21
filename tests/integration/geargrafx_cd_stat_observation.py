"""Real Geargrafx device-layer observation for Issue #231 (CD_STAT, DEVELOPMENT-ONLY).

Reuses the Issue #144 media-state observation point (src/cd_media_state.s, built by tools/build_cd_media_state.py),
the #144 cdread probe (geargrafx_cdread_probe.cpp via geargrafx_cdread.execute) and the #144 judge (judge / check_pair).
Adds only tools/cd_stat_observation.judge_observation (step bounds and required fields). No CD_STAT ($E01B) call is
made; the BIOS callee ABI is UNKNOWN; real hardware is NOT RUN. Synthetic discs only; no ./rom/ input is read.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_cd_media_state import ERRORS, judge, verify_cd_media_state
from cd_stat_observation import judge_observation
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import BIN_NAME, CUE_NAME, DEFAULT_SECTORS, build_image

BUDGET = 12_000_000  # same bound as #144 (geargrafx_cd_media_state.py)
SECTOR = 2048


def rejected(name, action, expected):
    """The judge MUST raise, and for the stated reason."""
    try:
        action()
    except ValueError as error:
        if expected not in str(error):
            raise ValueError(f"{name}: rejected for the wrong reason: {error}") from error
        return {"status": "PASS_EXPECTED_FAILURE", "reason": str(error)}
    raise ValueError(f"{name}: accepted by the judge")


def points(records):
    """Device-layer observation points: CPU state, step, result state, RAM digest, SCSI phases, HIT checkpoints."""
    return {
        "runs": [{"cycle": r["cycle"], "reason": r["reason"], "pc": f"{r['pc']:04x}", "sp": r["sp"], "p": r["p"],
                  "mpr": r["mpr"], "step": r["step"], "result_state": r["result_hex"][:2],
                  "ram_sha256": hashlib.sha256(bytes.fromhex(r["buffer_hex"])[:SECTOR]).hexdigest(),
                  "scsi_phases": [p["scsi_phase"] for p in records["PHASE"] if p["cycle"] == r["cycle"]]}
                 for r in records["RUN"]],
        "hits": [{"name": h["name"], "cycle": h["cycle"], "step": h["step"], "pc": f"{h['pc']:04x}",
                  "buffer_head_sha256": hashlib.sha256(bytes.fromhex(h["buffer_head_hex"])).hexdigest()}
                 for h in records["HIT"]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    mode = parser.parse_args().mode
    base = (ROOT / "build" / mode / "bios-dev/dev-only-not-compatible-syscard3.pce").read_bytes()
    out = ROOT / "build" / mode / "cd-media-state"
    rom = out / "cd-media-state-not-bios.pce"
    payload = rom.read_bytes()
    digest = verify_cd_media_state(payload, base)
    symbols = json.loads((out / "manifest.json").read_text())["symbols"]
    labels = out / "trace-labels.txt"
    probe = compile_probe(out, "geargrafx_cdread_probe.cpp", "geargrafx-cdread-probe")
    results = {}
    with tempfile.TemporaryDirectory(prefix="pce-cd-stat-observation-") as tmp:
        folder = Path(tmp)
        good = folder / "good"
        manifest = build_image(good)
        runs = 0

        def paired(disc, budget=BUDGET, image=payload):
            """Run the same ROM with the given disc ("-" = no media) through the #144 paired loader."""
            nonlocal runs
            runs += 1
            path = folder / f"variant-{runs}-not-bios.pce"
            path.write_bytes(image)
            return execute(probe, path, f"card:{disc}", labels, budget)

        # Positive: paired control, same ROM and loader; only the inserted disc differs.
        with_disc = paired(good / CUE_NAME)
        without = paired("-")
        observed = judge_observation(with_disc, without, BUDGET)
        results["paired_control"] = {"status": "OBSERVED", **observed,
                                     "with_disc": points(with_disc), "without_disc": points(without)}

        # Negative: no media on the disc side, or the pair swapped.
        results["no_media_as_disc_side"] = rejected(
            "no_media_as_disc_side", lambda: judge_observation(without, without, BUDGET), "not exactly one-sided")
        results["swapped_pair"] = rejected(
            "swapped_pair", lambda: judge_observation(without, with_disc, BUDGET), "not exactly one-sided")

        # Negative: blank data disc (valid MODE1 headers, zero user data) is recognised as MEDIA_UNRECOGNISED.
        blank = folder / "blank"
        build_image(blank)
        raw = bytearray((blank / BIN_NAME).read_bytes())
        for lba in range(DEFAULT_SECTORS):
            raw[lba * 2352 + 16:lba * 2352 + 16 + SECTOR] = bytes(SECTOR)
        (blank / BIN_NAME).write_bytes(raw)
        blank_records = paired(blank / CUE_NAME)
        blank_state = judge(blank_records)
        if blank_state != "MEDIA_UNRECOGNISED":
            raise ValueError(f"blank data disc: state {blank_state}, expected MEDIA_UNRECOGNISED")
        results["blank_data_disc"] = {
            "state": blank_state,
            "rejected_as_disc": rejected("blank_data_disc", lambda: judge_observation(blank_records, without, BUDGET),
                                         "('MEDIA_UNRECOGNISED', 'NO_DATA')")}

        # Negative: zero-byte BIN, refused by the emulator before the guest runs (probe exit 4).
        empty = folder / "empty"
        empty.mkdir()
        (empty / BIN_NAME).write_bytes(b"")
        (empty / CUE_NAME).write_text((good / CUE_NAME).read_text())
        try:
            paired(empty / CUE_NAME)
        except subprocess.CalledProcessError as error:
            if error.returncode != 4:
                raise ValueError(f"zero-byte BIN: probe exit {error.returncode}, expected 4") from error
            results["zero_byte_bin"] = {"status": "OBSERVED_EMULATOR_BEHAVIOUR", "probe_exit": 4}
        else:
            raise ValueError("zero-byte BIN unexpectedly loaded")

        # Negative: device timeout. Selection is redirected to $1805 so the target never answers; firmware reports 0x82.
        at = symbols["ms_select"] - 0xE000
        stuck = bytearray(payload)
        if stuck[at:at + 3] != bytes((0x8D, 0x00, 0x18)):
            raise ValueError("ms_select is not STA $1800")
        stuck[at + 1] = 0x05
        # The paired control must be the same modified ROM, otherwise the judge rejects the pair for the wrong reason.
        stuck_records = paired(good / CUE_NAME, image=bytes(stuck))
        stuck_without = paired("-", image=bytes(stuck))
        results["selection_timeout"] = rejected(
            "selection_timeout", lambda: judge_observation(stuck_records, stuck_without, BUDGET), ERRORS[0x82])

        # Negative: step budget exhausted in the real core (reason "budget"), and steps beyond a smaller judge bound.
        short_with = paired(good / CUE_NAME, budget=1000)
        short_without = paired("-", budget=1000)
        results["step_budget_exhausted"] = rejected(
            "step_budget_exhausted", lambda: judge_observation(short_with, short_without, BUDGET),
            "not bounded by the step budget")
        results["step_over_judge_bound"] = rejected(
            "step_over_judge_bound", lambda: judge_observation(with_disc, without, 1),
            "not bounded by the step budget")

        # Negative: out-of-range judge inputs are BLOCKED before any record is read.
        for label, bad in (("zero", 0), ("negative", -1), ("boolean", True), ("string", "12000000")):
            results[f"budget_{label}"] = rejected(
                f"budget_{label}", lambda bad=bad: judge_observation(with_disc, without, bad), "BLOCKED: step budget")

    evidence = {
        "issue": 231,
        "scope": "device-layer observation of the Issue #144 media-state path (src/cd_media_state.s) on synthetic discs ONLY; "
                 "no CD_STAT ($E01B) call; BIOS callee ABI UNKNOWN; not a System Card or commercial-game result",
        "rom_sha256": digest,
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "geargrafx_source_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "disc": {key: manifest[key] for key in ("format", "sectors", "bin_sha256", "cue_sha256")},
        "budget": BUDGET,
        "results": results,
        # Declared, not observed: fixed by the scope above and the ROM source. Not checked at runtime.
        # cd_stat_called is derived from src/cd_media_state.s (no $E01B reference), not measured in the core.
        "declared": {"real_hardware": "NOT_RUN", "second_emulator": "NOT_RUN", "cd_stat_called": False,
                     "bios_abi": "UNKNOWN"},
        # judge_observation returns OBSERVED for the paired control or raises; every rejection above raises before this point.
        "status": "PASS" if observed["status"] == "OBSERVED" else "FAIL",
    }
    target = ROOT / "build" / mode / "cd-stat-observation"
    target.mkdir(parents=True, exist_ok=True)
    (target / "cd-stat-observation-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"OBSERVED real Geargrafx device layer: disc -> {observed['disc']}, no disc -> {observed['no_disc']}; "
          f"{len(results)} records written to {target}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"CD_STAT device observation FAIL/BLOCKED: {error}") from error
