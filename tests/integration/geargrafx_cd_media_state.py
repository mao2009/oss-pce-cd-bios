"""Real Geargrafx paired control for the Issue #144 CD media-state observation point (development-only).

Same ROM, same loader ("card:" in the cdread probe), cold and warm: the only variable is whether a disc is
inserted. The probe only reads guest state. Geargrafx only; real hardware is NOT RUN.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_cd_media_state import ERRORS, check_pair, judge, verify_cd_media_state
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import BIN_NAME, CUE_NAME, DEFAULT_SECTORS, build_image

BUDGET = 12_000_000


def must_fail(name, records, expected):
    try:
        judge(records)
    except ValueError as error:
        if expected not in str(error):
            raise ValueError(f"{name}: rejected for the wrong reason: {error}") from error
        return {"status": "PASS_EXPECTED_FAILURE", "reason": str(error)}
    raise ValueError(f"{name}: accepted by the judge")


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
    with tempfile.TemporaryDirectory(prefix="pce-media-state-") as tmp:
        folder = Path(tmp)
        good = folder / "good"
        manifest = build_image(good)
        run = lambda path, disc: execute(probe, path, f"card:{disc}", labels, BUDGET)
        with_disc, without = run(rom, good / CUE_NAME), run(rom, "-")
        pair = check_pair(with_disc, without)
        results["paired_control"] = {"status": "OBSERVED", "with_disc": pair[0], "without_disc": pair[1],
                                     "variable": "CdRomMedia::LoadCueFromFile before ResetMedia(false)",
                                     "cap_with_disc": with_disc["CAP"][0], "cap_without_disc": without["CAP"][0],
                                     "run_with_disc": [{k: r[k] for k in ("cycle", "reason", "x", "sp", "mpr", "result_hex")}
                                                       for r in with_disc["RUN"]],
                                     "run_without_disc": [{k: r[k] for k in ("cycle", "reason", "x", "sp", "mpr", "result_hex")}
                                                          for r in without["RUN"]]}
        # Invalid media 1: valid MODE1 headers, all user data zero (read succeeds, signature absent).
        blank = folder / "blank"
        build_image(blank)
        raw = bytearray((blank / BIN_NAME).read_bytes())
        for lba in range(DEFAULT_SECTORS):
            raw[lba * 2352 + 16:lba * 2352 + 16 + 2048] = bytes(2048)
        (blank / BIN_NAME).write_bytes(raw)
        state = judge(run(rom, blank / CUE_NAME))
        if state != "MEDIA_UNRECOGNISED":
            raise ValueError(f"blank data disc judged {state}")
        results["blank_data_disc"] = {"status": "OBSERVED", "state": state}
        # Invalid media 2: zero-byte BIN. The drive refuses it before the guest runs.
        empty = folder / "empty"
        empty.mkdir()
        (empty / BIN_NAME).write_bytes(b"")
        (empty / CUE_NAME).write_text((good / CUE_NAME).read_text())
        try:
            run(rom, empty / CUE_NAME)
        except subprocess.CalledProcessError as error:
            if error.returncode != 4:
                raise ValueError(f"zero-byte BIN: probe exit {error.returncode}, expected 4") from error
            results["zero_byte_bin"] = {"status": "OBSERVED_EMULATOR_BEHAVIOUR", "probe_exit": 4,
                                        "observation": "CdRomMedia::LoadCueFromFile rejects it; the guest never runs"}
        else:
            raise ValueError("zero-byte BIN unexpectedly loaded")
        # Timeout negative: selection redirected to $1805; the firmware must report an error, never a media state.
        at = symbols["ms_select"] - 0xE000
        stuck = bytearray(payload)
        if stuck[at:at + 3] != bytes((0x8D, 0x00, 0x18)):
            raise ValueError("ms_select is not STA $1800")
        stuck[at + 1] = 0x05
        path = folder / "stuck.pce"
        path.write_bytes(stuck)
        results["selection_timeout"] = must_fail("selection_timeout", run(path, good / CUE_NAME), ERRORS[0x82])
        # The same judge refuses the real no-disc run when it is presented as the disc side of a pair.
        try:
            check_pair(without, with_disc)
        except ValueError:
            results["swapped_pair"] = {"status": "PASS_EXPECTED_FAILURE"}
        else:
            raise ValueError("swapped pair accepted")
    (out / "cd-media-state-evidence.json").write_text(json.dumps({
        "scope": "firmware-side media-state observation point ONLY; CD_READ/CD_BOOT/BIOS ABI NOT_IMPLEMENTED",
        "rom_sha256": digest, "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "disc_bin_sha256": manifest["bin_sha256"], "real_hardware": "NOT_RUN", "results": results,
    }, indent=2, sort_keys=True) + "\n")
    print(f"OBSERVED real Geargrafx paired control: disc -> {pair[0]}, no disc -> {pair[1]}; "
          "blank disc -> MEDIA_UNRECOGNISED; zero-byte BIN refused; selection timeout rejected")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"CD media-state integration FAIL/BLOCKED: {error}") from error
