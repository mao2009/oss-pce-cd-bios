#!/usr/bin/env python3
"""Build the G5 DEV profile ROM (Issue #798): normal DEV 18 APIs + experimental $02/$03/$33.

Thin integration wrapper, NOT a new builder. The normal DEV ROM is rebuilt
through tools/build_rom.py (byte-identical, untouched) and the G5 image is
produced by tools/build_experimental_bios_boot.py (triple-link wiring reused
as-is: slot $02 -> cd_base_proto, table $E009 -> cd_read_link, table $E099 ->
EX_DSPOFF fragment, RESET -> synthetic selection -> READ(6) -> PCEX IPL ->
EXEC $3000 -> MARK return). This module only renames the artifact into the
G5 profile layout, records the profile provenance, and offers a
self-contained entry (synthetic media + default payload generated
deterministically) so `build_rom.py --profile g5-experimental` works alone.

NOT a compatible System Card BIOS. NOT a BIOS ABI. NOT release eligible.
DEV_EXPERIMENTAL is never treated as genuine ABI. Synthetic media only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__:
    from .build_experimental_bios_boot import build_experimental_bios_boot, build_experimental_payload
    from .build_rom import IMAGE_BYTES, ROOT, build, overrides, verify_dev_rom
    from .make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image
    from .build_cdload_exec import USER_OFFSET, encode_sector
    from .experimental_track_selection import selection_from_cue, snapshot_media
else:
    from build_experimental_bios_boot import build_experimental_bios_boot, build_experimental_payload
    from build_rom import IMAGE_BYTES, ROOT, build, overrides, verify_dev_rom
    from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image
    from build_cdload_exec import USER_OFFSET, encode_sector
    from experimental_track_selection import selection_from_cue, snapshot_media

G5_PROFILE_ID = "g5-experimental"
G5_ROM_NAME = "dev-g5-experimental-not-compatible.pce"
EXIT_OK, EXIT_ERROR = 0, 2


def build_g5_dev_rom(base_path: Path, output: Path, mode: str = "debug", cue: Path | None = None,
                     payload_code: bytes | None = None, payload_symbols: dict | None = None,
                     rom_name: str = G5_ROM_NAME) -> Path:
    """Build the G5 profile ROM next to an already-built normal DEV ROM.

    `base_path` must be a verified normal DEV ROM (verify_dev_rom refuses a
    tampered base). `cue` is the exact synthetic CUE to mount; its INDEX 01
    sector must already contain `payload_code` (BLOCKED otherwise -- the same
    finalized-media contract as build_experimental_bios_boot). The ROM bytes
    depend only on the host-derived selection (track/LBA), never on payload
    bytes, so one G5 image boots every disc sharing its INDEX 01.
    """
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    if cue is None:
        raise ValueError("G5 profile boot requires the exact synthetic CUE to mount")
    if payload_code is None:
        raise ValueError("G5 profile boot requires the finalized payload bytes")
    base_path, output, cue = Path(base_path), Path(output), Path(cue)
    if not cue.is_file():
        raise ValueError(f"G5 profile CUE is not a file: {cue}")
    # The G5 manifest must never replace the normal DEV manifest: refuse an
    # output directory that resolves to the normal ROM's own directory.
    if output.resolve() == base_path.parent.resolve():
        raise ValueError("G5 profile output must not be the normal DEV ROM directory "
                         "(it would replace the normal DEV manifest)")
    base_bytes = base_path.read_bytes()
    verify_dev_rom(base_bytes, manifest=base_path.parent / "manifest.json")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pce-g5-profile-") as tmp:
        tmpdir = Path(tmp)
        built = build_experimental_bios_boot(base_path, tmpdir, mode, cue,
                                             payload_code, payload_symbols)
        rom_bytes = built.read_bytes()
        manifest = json.loads((tmpdir / "manifest.json").read_text(encoding="utf-8"))
        trace_labels = (tmpdir / "trace-labels.txt").read_bytes()
    if len(rom_bytes) != IMAGE_BYTES:
        raise ValueError("G5 profile image is not 256 KiB")
    target = output / rom_name
    target.write_bytes(rom_bytes)
    manifest["rom_name"] = rom_name
    manifest["g5_profile"] = {
        "profile": G5_PROFILE_ID,
        "normal_dev_rom": base_path.name,
        "normal_dev_rom_sha256": hashlib.sha256(base_bytes).hexdigest(),
        "normal_dev_overrides": len(overrides(ROOT)),
        "experimental_slots": ["$02", "$03", "$33"],
        "entry": ("RESET -> synthetic selection -> READ(6) INDEX 01 -> PCEX IPL "
                  "validate -> copy EXEC $3000 -> jmp entry -> API calls -> MARK return"),
        "synthetic_only": True,
        "reference_jsr": "0/81",
        "abi_verified": "0/81",
        "dev_callable": False,
        "release_eligible": False,
        "genuine_abi": "NO_CLAIM",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (output / "trace-labels.txt").write_bytes(trace_labels)
    print(f"G5 DEV profile: {target} (DEV-only, NOT compatible, NOT a BIOS ABI)")
    return target


def run_profile(output: Path, mode: str = "debug", cue: Path | None = None) -> Path:
    """Self-contained G5 build: normal ROM + deterministic synthetic media + default payload + ROM.

    Without `cue`, a deterministic single-track disc (INDEX 01 = LBA 0) is
    generated under <output>/media and embedded with the default payload, so
    repeated runs produce byte-identical ROMs. With `cue`, that exact CUE is
    used and must already contain the default payload bytes (BLOCKED
    otherwise); the caller (e.g. the integration test with its own scenario
    payloads) should call build_g5_dev_rom directly instead.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    base = build(ROOT / "build" / mode / "bios-dev", mode, root=ROOT)
    if cue is None:
        media = output / "media"
        build_image(media)
        pay_dir = output / "payload-build"
        payload_code, _ = build_experimental_payload(pay_dir, mode)
        raw = bytearray((media / BIN_NAME).read_bytes())
        raw[0 * RAW_BYTES + USER_OFFSET:0 * RAW_BYTES + USER_OFFSET + 2048] = encode_sector(0, payload_code)
        (media / BIN_NAME).write_bytes(bytes(raw))
        # The payload embed changes BIN sector 0: refresh the generated disc
        # manifest before snapshotting so its integrity metadata stays exact.
        disc_manifest_path = media / "disc-manifest.json"
        disc_manifest = json.loads(disc_manifest_path.read_text(encoding="utf-8"))
        final_bin = (media / BIN_NAME).read_bytes()
        if hashlib.sha256((media / CUE_NAME).read_bytes()).hexdigest() != disc_manifest["cue_sha256"]:
            raise ValueError("generated G5 CUE changed unexpectedly; refusing stale manifest")
        disc_manifest["bin_sha256"] = hashlib.sha256(final_bin).hexdigest()
        users = list(disc_manifest["user_data_sha256"])
        users[0] = hashlib.sha256(
            final_bin[0 * RAW_BYTES + USER_OFFSET:0 * RAW_BYTES + USER_OFFSET + 2048]).hexdigest()
        disc_manifest["user_data_sha256"] = users
        disc_manifest_path.write_text(json.dumps(disc_manifest, indent=2, sort_keys=True) + "\n")
        # Idempotent rebuilds: reuse the previous snapshot only when it is
        # byte-identical to the freshly prepared source. Anything else (user
        # content, partial/corrupt snapshot) is refused, never deleted.
        mounted = media / "mounted"
        if mounted.exists():
            try:
                same = (mounted.is_dir()
                        and (mounted / CUE_NAME).is_file()
                        and (mounted / BIN_NAME).is_file()
                        and (mounted / CUE_NAME).read_bytes() == (media / CUE_NAME).read_bytes()
                        and (mounted / BIN_NAME).read_bytes() == (media / BIN_NAME).read_bytes()
                        and sorted(p.name for p in mounted.iterdir()) == sorted([BIN_NAME, CUE_NAME]))
            except OSError as error:
                raise ValueError(f"cannot verify the existing media snapshot: {error}") from error
            if not same:
                raise ValueError(f"media snapshot {mounted} is not the generated identical "
                                 "snapshot; refusing to replace unknown content")
            cue = mounted / CUE_NAME
        else:
            cue = snapshot_media(media / CUE_NAME, mounted)
        selected = selection_from_cue(cue, cue)
        if selected["lba"] != 0 or selected["track"] != 1:
            raise ValueError(f"generated G5 media has unexpected selection: {selected}")
    else:
        cue = Path(cue)
        pay_dir = output / "payload-build"
        payload_code, _ = build_experimental_payload(pay_dir, mode)
    return build_g5_dev_rom(base, output, mode, cue, payload_code)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", default=os.environ.get("MODE", "debug"))
    parser.add_argument("--cue", type=Path, default=None,
                        help="exact synthetic CUE to mount (must embed the default payload); "
                             "omitted: deterministic media is generated under <output>/media")
    args = parser.parse_args()
    try:
        run_profile(args.output or ROOT / "build" / args.mode / "bios-dev-g5", args.mode, args.cue)
        return EXIT_OK
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
