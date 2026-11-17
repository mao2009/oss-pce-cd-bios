#!/usr/bin/env python3
"""Build the $4A SIMULATED diagnostic variant of the PUBLIC IPL G5 loader (Issue #793).

DEV-ONLY, synthetic + opt-in commercial observation. NOT a compatible System
Card BIOS. NOT a BIOS ABI. NOT release eligible. NOT DEV_CALLABLE.

Method: build the STANDARD G5 experimental image with
tools/build_ipl_loader_g5.py::build_ipl_loader_g5_rom (PUBLIC INFO + multi-BODY
+ $02 proto / $03 link / $33 dspoff, $4A stays stub), then binary-patch EXACTLY
the 5 fallback bytes of slot $4A (`ldx #$4A; jmp api_unimplemented`) with the
assembled SIMULATED stub (immediate RTS + NOP pad, from
tools/build_slot02_return_loader.py). Every assertion is fail-closed:

- standard G5 ROM builds and verifies first (no diagnostic without standard);
- pre-patch bytes must be exactly the $4A fallback naming label-derived
  api_unimplemented (no hardcoded fallback address);
- stub must start with RTS and fit in 5 bytes (side-effect-free RTS/NOP only);
- diagnostic diff vs standard must be exactly the $4A window (RESET, vectors,
  IPL loader, $02/$03/$33 windows unchanged);
- diagnostic patch goes to a separate file and never overwrites the standard
  ROM image (the standard image itself is (re)built deterministically by
  the standard builder);
- mismatch anywhere is BLOCKED (no silent no-op, no partial patch).

Result classification (never genuine):
- REAL: APIs wired in the standard G5 image ($02/$03/$33 + 18 DEV).
- SIMULATED: $4A immediate return only (conditional fabrication).
- UNKNOWN: genuine $4A ABI unconfirmed.
- BLOCKED: safety/contract refusal.

The SIMULATED success must never be confused with genuine $4A success.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

if __package__:
    from .build_ipl_loader_g5 import (
        ROM_STEM,
        _atomic_write,
        _refuse_symlink,
        _refuse_unsafe_variant,
        build_ipl_loader_g5_rom,
        parse_labels,
    )
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT
    from .build_slot02_return_loader import (
        assemble_stub_bytes,
        apply_patches,
        patch_preview,
    )
else:
    from build_ipl_loader_g5 import (
        ROM_STEM,
        _atomic_write,
        _refuse_symlink,
        _refuse_unsafe_variant,
        build_ipl_loader_g5_rom,
        parse_labels,
    )
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT
    from build_slot02_return_loader import (
        assemble_stub_bytes,
        apply_patches,
        patch_preview,
    )

DIAG_SUFFIX = "-4a-sim"
SLOT_4A = 0x4A
TABLE_ENTRY_4A = 0xE000 + 3 * SLOT_4A  # $E0DE (issue statement)

# MAJOR-1: every file assemble_stub_bytes() may write into the output dir.
# Checked with lstat (no symlink following) BEFORE stub generation starts;
# a single bad leaf refuses the whole build. Plain files stay usable (the
# assembler overwrites them on rebuild). Check-then-use cannot exclude a
# concurrent writer swapping entries between the check and the assembler
# run; callers must not share one output directory between concurrent builds
# (same residual TOCTOU limit as the documented ./rom handling and the
# existing G5 leaf pre-checks).
_STUB_LEAVES = ("slot02_return_stub.o", "slot02_return_stub.cfg",
                 "slot02_return_stub.bin")


def _g5_trace_path(output: Path, variant: str) -> Path:
    return output / f"ipl-loader-g5-{variant}.lbl"


def _refuse_stub_leaves(output: Path) -> None:
    """Refuse symlinked stub-generation leaves before the assembler runs.

    MAJOR-1: lstat-based, no symlink following (dangling symlinks refuse
    the same way: os.path.lexists is true and lstat reports symlink).
    Raises before any ca65/ld65/file write when any of the three leaves is
    a symlink. Plain files and absent paths pass (normal rebuild contract).
    """
    for leaf in _STUB_LEAVES:
        _refuse_symlink(output / leaf)


def build_ipl_loader_g5_4a_simulated(
    original_path: Path,
    output: Path,
    sel: int,
    mode: str = "debug",
    variant: str = "main",
    defines=None,
    track: int = 1,
    private_input: bool = False,
):
    """Build (std_rom, diag_rom, std_syms, diag_info). Fail-closed.

    `sel` is the 16-bit INDEX 01 LBA, `track` the XSEL data-track number.
    `defines` follows build_ipl_loader_g5_rom (None = PUBLIC). `variant` names
    the STANDARD image; the diagnostic gets f"{variant}{DIAG_SUFFIX}".
    """
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    _refuse_unsafe_variant(variant)
    diag_variant = f"{variant}{DIAG_SUFFIX}"
    _refuse_unsafe_variant(diag_variant)
    output = Path(output)
    # MAJOR (CodeRabbit): refuse existing/symlinked diagnostic outputs
    # BEFORE the standard builder writes anything. Otherwise a refused rerun
    # (same output, same variant, different sel) would replace the control
    # ROM/manifest/trace while the retained diagnostic manifest still records
    # the old standard_sha256. The pre-publish re-check below is kept as
    # defense in depth (TOCTOU/residual policy unchanged). Naming contract:
    # diagnostic ROM "{ROM_STEM}-{variant}-4a-sim.pce", diagnostic manifest
    # "manifest-{variant}-4a-sim.json".
    _pre_diag_rom = output / f"{ROM_STEM}-{diag_variant}.pce"
    _pre_man_path = output / f"manifest-{diag_variant}.json"
    _refuse_symlink(_pre_diag_rom)
    _refuse_symlink(_pre_man_path)
    if _pre_diag_rom.exists():
        raise ValueError("BLOCKED: diagnostic ROM already exists (no overwrite)")
    if _pre_man_path.exists():
        raise ValueError("BLOCKED: diagnostic manifest already exists (no overwrite)")
    # Build + verify the STANDARD G5 image first (no diagnostic without it).
    std_rom, std_syms = build_ipl_loader_g5_rom(
        original_path, output, sel, mode, variant, defines, track,
        private_input=private_input)
    std_data = std_rom.read_bytes()
    if len(std_data) != IMAGE_BYTES:
        raise ValueError("BLOCKED: standard G5 image size changed")
    # Label-derived $4A fallback + unimplemented (no hardcoded code address).
    trace = _g5_trace_path(output, variant)
    addrs = parse_labels(trace, ("api_slot_4A", "api_unimplemented"))
    missing = [n for n in ("api_slot_4A", "api_unimplemented") if n not in addrs]
    if missing:
        raise ValueError(f"BLOCKED: standard G5 labels missing {missing}")
    slot_addr = addrs["api_slot_4A"]
    unimpl_addr = addrs["api_unimplemented"]
    if not 0xE000 <= slot_addr <= 0xFFFF:
        raise ValueError("BLOCKED: api_slot_4A outside BIOS window")
    # The $4A TABLE entry ($E0DE) must still point at the fallback (G5 leaves
    # $4A unwired by design; diagnostic patches the fallback only).
    tab_off = TABLE_ENTRY_4A - 0xE000
    want_table = bytes((0x4C, slot_addr & 0xFF, (slot_addr >> 8) & 0xFF))
    if bytes(std_data[tab_off:tab_off + 3]) != want_table:
        raise ValueError("BLOCKED: $4A table entry is not JMP api_slot_4A (unexpected G5 layout)")
    # MAJOR-1: refuse symlinked stub-generation leaves BEFORE the assembler
    # runs (lstat-based; plain-file rebuilds still pass).
    _refuse_stub_leaves(output)
    # Assemble the SIMULATED stub (RTS-first, side-effect-free, fits 5 bytes).
    stub = assemble_stub_bytes(output, mode)
    off, before, after = patch_preview(std_data, SLOT_4A, slot_addr, unimpl_addr, stub)
    # patch_preview already verified the 5 pre-patch bytes; apply + verify
    # the total diff is exactly the $4A fallback window.
    patched = apply_patches(std_data, [(off, before, after)])
    # Standard windows must be intact in the diagnostic ($02 proto JMP,
    # $33/$03 table JMPs); only the $4A fallback window may differ.
    if "api_slot_02" not in std_syms or "api_slot_33" not in std_syms or "api_slot_03" not in std_syms:
        raise ValueError("BLOCKED: standard G5 symbols missing wired slots")
    # Vectors unchanged (diagnostic moves nothing).
    std_words = [int.from_bytes(std_data[o:o + 2], "little") for o in range(0x1FF6, 0x2000, 2)]
    new_words = [int.from_bytes(patched[o:o + 2], "little") for o in range(0x1FF6, 0x2000, 2)]
    if std_words != new_words:
        raise ValueError("BLOCKED: diagnostic moved the vectors")
    if len(patched) != IMAGE_BYTES or patched[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("BLOCKED: diagnostic changed image size/unused banks")
    # The diagnostic patch goes to separate files and never overwrites the
    # standard ROM image (the standard image itself is rebuilt by the
    # standard builder). MEDIUM-1: inspect
    # BOTH final outputs BEFORE the first publish, so a bad manifest path
    # can never leave an orphan diagnostic ROM behind. The manifest content
    # is built and validated in memory before the ROM is published.
    diag_name = f"{ROM_STEM}-{diag_variant}.pce"
    diag_rom = output / diag_name
    if diag_rom == std_rom:
        raise ValueError("BLOCKED: diagnostic name collides with standard ROM")
    man_name = f"manifest-{diag_variant}.json"
    man_path = output / man_name
    _refuse_symlink(diag_rom)
    _refuse_symlink(man_path)
    if diag_rom.exists():
        raise ValueError("BLOCKED: diagnostic ROM already exists (no overwrite)")
    if man_path.exists():
        raise ValueError("BLOCKED: diagnostic manifest already exists (no overwrite)")
    diag_info = {
        "status": "DIAGNOSTIC-SIMULATED-$4A-ONLY-not-compatible-not-a-bios-abi",
        "standard_rom": std_rom.name,
        "standard_sha256": hashlib.sha256(std_data).hexdigest(),
        "diagnostic_rom": diag_rom.name,
        "diagnostic_sha256": hashlib.sha256(patched).hexdigest(),
        "slot": f"${SLOT_4A:02X}",
        "table_entry": f"${TABLE_ENTRY_4A:04X}",
        "fallback_addr": f"${slot_addr:04X}",
        "unimplemented_addr": f"${unimpl_addr:04X}",
        "patch_offset": off,
        "before": before.hex(),
        "after": after.hex(),
        "classification": "SIMULATED (immediate RTS; NOT genuine $4A ABI)",
        "genuine_abi": "UNKNOWN",
        "dev_callable": False,
        "release_eligible": False,
        "reference_jsr": "0/81",
        "abi_verified": "0/81",
        "private_media_used": bool(private_input),
    }
    man_bytes = (json.dumps(diag_info, indent=2, sort_keys=True) + "\n").encode("utf-8")
    # Publish via temp + os.replace (no truncation of previous files). Each
    # file is complete-or-absent; multi-file atomicity across the ROM +
    # manifest pair is NOT guaranteed (a crash between the two writes can
    # leave a mixed set). If the manifest publish fails, remove the orphan
    # diagnostic ROM published just above so a manifest-less image is never
    # left behind; the pre-existing standard ROM is never touched.
    _atomic_write(diag_rom, patched)
    try:
        _atomic_write(man_path, man_bytes)
    except BaseException:
        try:
            diag_rom.unlink()
        except OSError:
            pass
        raise
    return std_rom, diag_rom, std_syms, diag_info


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["debug", "release"], default="debug")
    p.add_argument("--sel", type=lambda s: int(s, 0), required=True)
    p.add_argument("--track", type=int, default=1)
    p.add_argument("--variant", default="main")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--public", dest="public", action="store_true", default=True)
    p.add_argument("--dev", dest="public", action="store_false")
    args = p.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    defines = ["-D", "IPL_PUBLIC"] if args.public else []
    try:
        std_rom, diag_rom, _, _ = build_ipl_loader_g5_4a_simulated(
            base, args.output, args.sel, args.mode, args.variant, defines, args.track)
        print(f"PASS standard (NOT compatible): {std_rom}")
        print(f"PASS diagnostic SIMULATED $4A (NOT genuine): {diag_rom}")
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as e:
        print(f"G5 $4A diagnostic FAIL/BLOCKED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
