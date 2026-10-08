"""Assemble an original smoke fixture, never a BIOS image."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

from environment import DEPENDENCIES, ROOT, executable

RESET = bytes.fromhex("78 d8 d4 a9 ff 53 01 a9 f8 53 02 a2 ff 9a 73 40 e0 00 22 04 00 43 02 8d 04 22 a9 5a 8d 05 22")


def verify(data):
    if len(data) != 8192:
        raise ValueError(f"expected headerless 8192-byte fixture, got {len(data)}")
    expected = bytearray(b"\xff" * 8192)
    expected[:len(RESET)] = RESET
    expected[0x30:0x32] = b"\x80\xfe"
    expected[0x40:0x44] = b"PCE!"
    expected[0x1ff6:] = struct.pack("<5H", 0xe030, 0xe030, 0xe030, 0xe030, 0xe000)
    if data != expected:
        raise ValueError("fixture layout/opcodes/vector/padding mismatch (not a generic BIOS validator)")
    return hashlib.sha256(data).hexdigest()


def build(output, mode="debug", source=None, config=None):
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    ca65, ld65 = executable("ca65"), executable("ld65")
    # Reject accidental use of an unpinned PATH tool. Overrides remain explicit.
    for name, tool in [("ca65", ca65), ("ld65", ld65)]:
        version = subprocess.run([tool, "--version"], capture_output=True, text=True, check=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError(f"{name} does not report the pinned cc65 build ID")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    source = source or ROOT / "tests/fixtures/smoke.s"
    config = config or ROOT / "tests/fixtures/smoke.cfg"
    command = [ca65, "--cpu", "huc6280", "-o", str(output / "smoke.o"), str(source)]
    if mode == "debug":
        command += ["-g", "-l", str(output / "smoke.lst")]
    subprocess.run(command, check=True)
    subprocess.run([ld65, "-C", str(config), "-o", str(output / "smoke-test-not-bios.pce"),
                    "-m", str(output / "smoke.map"), "-Ln", str(output / "smoke.lbl"),
                    str(output / "smoke.o")], check=True)
    rom = output / "smoke-test-not-bios.pce"
    digest = verify(rom.read_bytes())
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout)
    metadata = {"schema_version": 1, "status": "original-smoke-fixture-not-bios",
                "source_sha": commit, "working_tree_dirty": dirty, "mode": mode,
                "format": "headerless-hucard", "rom_bytes": 8192, "rom_sha256": digest,
                "cc65_revision": DEPENDENCIES["cc65"]["revision"],
                "system_card_abi": "NOT_IMPLEMENTED", "cd_boot": "NOT_TESTED",
                "emulator_execution": "see separate integration evidence"}
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(f"PASS fixture structure: 8192 bytes, sha256={digest}")
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default=os.environ.get("MODE", "debug"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        print(f"PASS fixture structure: {verify(args.verify.read_bytes())}")
    else:
        build(args.output or ROOT / "build" / args.mode, args.mode)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"build: {error}") from error
