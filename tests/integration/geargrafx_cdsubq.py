"""Real Geargrafx Q-subchannel device observation (Issue #261, diagnostic only).

Reuses the #15 CD-DA probe ROM (src/cdda_probe.s, built by `make dev-cdda`) and its frontend, runs it on the
self-made mixed-mode CUE/BIN, and judges the three $DD replies with tools/cdsubq_observation.py. Each reply
must match exactly one LBA in its checkpoint window, and the LBAs just outside that window must be NEGATIVE.

Scope: the pinned Geargrafx SCSI/CD layer only. The System Card CD_SUBQ ABI (arguments, returns, Q buffer
layout) is NOT measured and stays UNKNOWN; a PASS here is not a BIOS compatibility result.
Requires the pinned Geargrafx and cc65 from `make setup`; without them this fails closed (BLOCKED).
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
from build_cdda import AUDIO_SECTORS, DATA_SECTORS
from cdda_verify import check_run
from cdsubq_observation import observe_run
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from geargrafx_cdda import execute
from make_cd_image import CUE_NAME, build_image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    output = ROOT / "build" / args.mode / "cdda-probe"
    rom = output / "cdda-probe-not-bios.pce"
    labels = output / "trace-labels.txt"
    if not rom.exists() or not labels.exists():
        raise ValueError("CD-DA probe not built; run make dev-cdda first")
    probe = compile_probe(output, "geargrafx_cdda_probe.cpp", "geargrafx-cdda-probe")
    with tempfile.TemporaryDirectory(prefix="pce-cdsubq-") as tmp:
        folder = Path(tmp)
        disc = build_image(folder / "mixed", DATA_SECTORS, AUDIO_SECTORS, distinct_right=True)
        records = execute(probe, rom, folder / "mixed" / CUE_NAME, labels)
    run, points = check_run(records)
    observed = observe_run(run, points)
    evidence = {
        "scope": "pinned Geargrafx SCSI/CD-layer $DD (READ SUBCODE-Q) device observation at known medium positions; "
                 "NOT the System Card CD_SUBQ ABI, NOT BIOS behaviour, NOT real hardware",
        "issue": 261, "parent_issue": 19,
        "rom_sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "disc": {"format": "CUE/BIN, track 01 MODE1/2352 (LBA 0-15) + track 02 AUDIO (LBA 16-79)",
                 "bin_sha256": disc["bin_sha256"], "cue_sha256": disc["cue_sha256"],
                 "generator": "tools/make_cd_image.py"},
        "observations": {str(index): record for index, record in observed.items()},
        "checkpoint_steps": {str(i): [points[i][0]["step"], points[i][1]["step"] if points[i][1] else None]
                             for i in sorted(points)},
        "cd_subq_abi": "UNKNOWN", "bios_api_claim": "NONE", "real_hardware": "NOT_TESTED",
        "status": "PASS",
    }
    (output / "cdsubq-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"PASS real Geargrafx $DD device observation: {len(observed)} Q replies matched one medium LBA each "
          f"({', '.join(str(r['lba']) for r in observed.values())}), window boundaries rejected; "
          f"NOT the System Card CD_SUBQ ABI (UNKNOWN)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdsubq integration FAIL/BLOCKED: {error}") from error
