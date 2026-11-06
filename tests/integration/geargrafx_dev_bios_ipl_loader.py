"""Issue #682: the Issue #670 multi-sector IPL loader inside a 256 KiB DEV image, through the pinned Geargrafx
LoadBiosFromBuffer + LoadMedia path. Synthetic media only. The probe's capability record must name that loader.

NOT a System Card BIOS, NOT a BIOS ABI, NOT CD_READ, NOT release eligible. The case list, the payloads and
the judges are the Issue #670 ones (geargrafx_ipl_loader.py), unchanged; only the ROM image (256 KiB, BIOS path) and
the disc prefix (no "card:") differ. An 8 KiB HuCard pass is NOT reported as a 256 KiB BIOS pass.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))
import geargrafx_ipl_loader as L
from build_dev_ipl_loader_bios import build_ipl_loader_bios
from geargrafx_api import compile_probe
from geargrafx_cdread import LOADERS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    original = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    if not original.is_file():
        raise SystemExit("BLOCKED: run make dev-rom first")
    out = ROOT / "build" / args.mode / "dev-ipl-loader-bios"
    rom, rom_symbols = build_ipl_loader_bios(original, out, L.SEL, args.mode, "main")
    mutant, mutant_symbols = build_ipl_loader_bios(original, out, L.SEL, args.mode, "mutant",
                                                   ["-D", "IPL_MUTANT_READBACK"])
    if rom.stat().st_size != 262144 or mutant.stat().st_size != 262144:
        raise ValueError("BIOS image must be exactly 262,144 bytes")
    rom_sha = hashlib.sha256(rom.read_bytes()).hexdigest()
    mutant_sha = hashlib.sha256(mutant.read_bytes()).hexdigest()
    public, public_symbols = build_ipl_loader_bios(original, out, L.SEL, args.mode, "public", ["-D", "IPL_PUBLIC"])
    public_mutant, public_mutant_symbols = build_ipl_loader_bios(
        original, out, L.SEL, args.mode, "public_mutant", ["-D", "IPL_PUBLIC", "-D", "IPL_MUTANT_READBACK"])
    for image in (public, public_mutant):
        if image.stat().st_size != 262144:
            raise ValueError("BIOS image must be exactly 262,144 bytes")
    public_sha = hashlib.sha256(public.read_bytes()).hexdigest()
    public_mutant_sha = hashlib.sha256(public_mutant.read_bytes()).hexdigest()
    roms = {"main": (rom, rom_symbols), "mutant": (mutant, mutant_symbols),
            "public": (public, public_symbols), "public_mutant": (public_mutant, public_mutant_symbols)}
    payloads = {}
    for key, (origin, tag) in L.PAY_ORIGINS.items():
        payloads[key] = L.build_payload(out, origin, tag)
    probe = compile_probe(out, "geargrafx_cdread_probe.cpp", "geargrafx-dev-ipl-loader-bios-probe")
    results = {}
    with tempfile.TemporaryDirectory(prefix="pce-dev-ipl-loader-bios-") as tmp:
        tmp = Path(tmp)
        for case in L.CASES:
            code, halt = payloads[case["pay"]]
            case_rom, case_symbols = roms[case.get("rom", "main")]
            labels = tmp / f"{case['name']}.labels"
            labels.write_text(f"cd_halt_ok {halt:04x}\n"
                              f"cd_halt_fail {case_symbols['cd_halt_fail']:04x}\n"
                              f"cd_unexpected_interrupt {case_symbols['cd_unexpected_interrupt']:04x}\n",
                              encoding="ascii")
            try:
                results[case["name"]] = L.run_case(probe, case_rom, labels, case, code, tmp, disc_prefix="",
                                                   expect_loader=LOADERS["disc"])
            except ValueError as error:
                raise SystemExit(f"FAIL {case['name']}: {error}") from error
        if hashlib.sha256(rom.read_bytes()).hexdigest() != rom_sha or \
                hashlib.sha256(mutant.read_bytes()).hexdigest() != mutant_sha or \
                hashlib.sha256(public.read_bytes()).hexdigest() != public_sha or \
                hashlib.sha256(public_mutant.read_bytes()).hexdigest() != public_mutant_sha:
            raise SystemExit("FAIL: a BIOS image changed during the test")
    evidence = {
        "scope": "DEV-only 256 KiB image with the Issue #670 loader, synthetic media, Geargrafx LoadBiosFromBuffer path",
        "mode": args.mode, "bios_image_bytes": 262144, "main_rom_sha256": rom_sha,
        "mutant_rom_sha256": mutant_sha, "public_rom_sha256": public_sha,
        "public_mutant_rom_sha256": public_mutant_sha, "ipl_sel_lba": L.SEL, "cases": results, "status": "PASS",
        "not_claimed": ["System Card compatibility", "BIOS ABI", "commercial media", "reference BIOS",
                        "release_eligible"],
    }
    (out / "dev-ipl-loader-bios-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                                                           encoding="utf-8")
    print(f"PASS DEV 256 KiB image IPL loader via LoadBiosFromBuffer path: {len(results)} cases (cold+warm each); "
          f"NOT compatible, NOT BIOS ABI, release_eligible=false")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"dev-ipl-loader-bios FAIL/BLOCKED: {error}") from error
