#!/usr/bin/env python3
"""Build the DEV-only slot-$02 RETURNING experiment loader (Issue #756).

NOT a compatible System Card BIOS. NOT CD_BASE. NOT a BIOS ABI.
NOT release eligible. SIMULATED/CONDITIONAL observation only.

Method: build a NORMAL loader BIOS with tools/build_dev_ipl_loader_bios.py
(byte-identical pipeline, same pinned cc65), then binary-patch EXACTLY the 5
fallback bytes of api_slot_02 (`ldx #$02; jmp api_unimplemented`) with the
assembled experiment stub (immediate RTS + NOP pad). Every assertion is
fail-closed:

- the pre-patch bytes must be exactly the fallback sequence naming the
  label-derived api_unimplemented address;
- the assembled stub must start with RTS ($60) and fit in 5 bytes;
- the whole-image diff must be exactly those 5 bytes (all labels, vectors,
  loader code, and every other slot's fallback stay put);
- the patch offset must lie inside bank 0 ($E000-$FFF6).

The base (unpatched) image is kept beside the experiment image as the control:
the same payload on the base image must still halt at api_unimplemented.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

if __package__:
    from .build_dev_ipl_loader_bios import build_ipl_loader_bios, parse_labels
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT
    from .environment import DEPENDENCIES, executable
else:
    from build_dev_ipl_loader_bios import build_ipl_loader_bios, parse_labels
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT
    from environment import DEPENDENCIES, executable

STUB_SOURCE = ROOT / "tests" / "integration" / "slot02_return_stub.s"
PATCH_LEN = 5  # `ldx #$02` (2) + `jmp api_unimplemented` (3)
RTS, NOP = 0x60, 0xEA
PUBLIC_DEFINES = ["-D", "IPL_PUBLIC"]


def resolve_defines(defines) -> list:
    """Map the caller's loader-format choice to ca65 defines (pure).

    None (omitted) means the PUBLIC default; an explicit empty list means DEV
    (no IPL_PUBLIC); a non-empty list is used as-is. The old ``or`` coercion
    silently turned an explicit DEV request ([]) into PUBLIC -- refused now.
    """
    if defines is None:
        return list(PUBLIC_DEFINES)
    return list(defines)

ASSUMPTION_TEXT = (
    "SIMULATED return: slot $02 returns immediately (RTS) preserving all "
    "registers, flags, MPRs and RAM. Post-return behavior is CONDITIONAL on "
    "this fabrication; it is NOT genuine CD_BASE behavior, NOT an ABI claim, "
    "and NOT compatibility evidence."
)


def assemble_stub_bytes(out: Path, mode: str = "debug") -> bytes:
    """Assemble the experiment stub to flat bytes (pinned cc65, fail-closed)."""
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("experiment stub compiler/linker is not the pinned cc65 build")
    out.mkdir(parents=True, exist_ok=True)
    obj = out / "slot02_return_stub.o"
    subprocess.run([ca65, str(STUB_SOURCE), "-o", str(obj), *([] if mode == "release" else ["-g"])],
                   check=True)
    cfg = out / "slot02_return_stub.cfg"
    cfg.write_text("MEMORY { RAM: start = $0000, size = $0100, file = %O; }\n"
                   'SEGMENTS { CODE: load = RAM, type = ro, start = $0000; }\n', encoding="ascii")
    flat = out / "slot02_return_stub.bin"
    subprocess.run([ld65, "-C", str(cfg), "-o", str(flat), str(obj)], check=True)
    code = flat.read_bytes()
    return validate_stub_bytes(code)


def validate_stub_bytes(stub: bytes) -> bytes:
    """Fail-closed stub content check (pure; RTS-first, side-effect-free, fits)."""
    if not stub or stub[0] != RTS:
        raise ValueError("experiment stub must start with RTS ($60)")
    if len(stub) > PATCH_LEN:
        raise ValueError(f"experiment stub ({len(stub)} bytes) does not fit the 5-byte fallback slot")
    if any(b != RTS and b != NOP for b in stub):
        raise ValueError("experiment stub must be side-effect-free (RTS/NOP only)")
    return bytes(stub)


def patch_preview(base: bytes, slot02_addr: int, unimpl_addr: int, stub: bytes) -> tuple[int, bytes, bytes]:
    """Pure patch computation: (file offset, before, after). Raises fail-closed."""
    stub = validate_stub_bytes(stub)
    if type(slot02_addr) is not int or not 0xE000 <= slot02_addr <= 0xFFFF:
        raise ValueError(f"api_slot_02 address {slot02_addr!r} outside BIOS window $E000-$FFFF")
    if type(unimpl_addr) is not int or not 0xE000 <= unimpl_addr <= 0xFFFF:
        raise ValueError(f"api_unimplemented address {unimpl_addr!r} outside BIOS window $E000-$FFFF")
    if slot02_addr < 0xE100 or slot02_addr + PATCH_LEN > 0xFFF6:
        raise ValueError("api_slot_02 is outside the FALLBACK/code area")
    off = slot02_addr - 0xE000
    if off + PATCH_LEN > len(base):
        raise ValueError("api_slot_02 offset is outside the image")
    before = bytes((0xA2, 0x02, 0x4C, unimpl_addr & 0xFF, (unimpl_addr >> 8) & 0xFF))
    if bytes(base[off:off + PATCH_LEN]) != before:
        raise ValueError(f"pre-patch bytes at ${slot02_addr:04X} are not the slot-02 fallback sequence")
    after = bytes(stub) + bytes((NOP,)) * (PATCH_LEN - len(stub))
    if len(after) != PATCH_LEN:
        raise ValueError("patched sequence length mismatch")
    return off, before, after


def apply_patch(base: bytes, off: int, before: bytes, after: bytes) -> bytes:
    """Apply a verified patch; the total image diff must be exactly the patch."""
    if bytes(base[off:off + len(before)]) != before:
        raise ValueError("base image changed under the verified patch (refusing)")
    patched = bytearray(base)
    patched[off:off + len(after)] = after
    diffs = [i for i, (a, b) in enumerate(zip(base, patched)) if a != b]
    if len(base) != len(patched) or diffs != list(range(off, off + len(after))):
        raise ValueError("patched image differs outside the verified 5-byte window")
    return bytes(patched)


def experiment_manifest(base_name: str, exp_name: str, data: bytes, symbols: dict, sel_note: str,
                        mode: str, variant: str, defines: list) -> dict:
    """Manifest for the experiment image (pure; host-testable)."""
    return {
        "status": "DEV-ONLY-SLOT02-RETURN-EXPERIMENT-not-compatible-not-a-bios-abi-SIMULATED",
        "issue": 756,
        "parent_tracker": 635,
        "mode": mode, "variant": variant, "rom_name": exp_name,
        "rom_sha256": hashlib.sha256(data).hexdigest(), "rom_bytes": IMAGE_BYTES,
        "base_rom_name": base_name,
        "method": (f"normal loader pipeline (tests/integration/ipl_loader_rom.s + pinned cc65 "
                   f"{DEPENDENCIES['cc65']['revision']}) plus a 5-byte patch of the api_slot_02 "
                   f"fallback to the assembled tests/integration/slot02_return_stub.s (RTS+NOP)"),
        "patch": {"target": "api_slot_02", "length_bytes": PATCH_LEN,
                  "before": "ldx #$02; jmp api_unimplemented", "after": "rts + nop pad"},
        "assumption": ASSUMPTION_TEXT,
        "ipl_format": "public" if "IPL_PUBLIC" in defines else "dev",
        "ipl_sel": sel_note,
        "defines": list(defines), "symbols": dict(symbols),
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "pinned_geargrafx": DEPENDENCIES["geargrafx"]["revision"],
        "system_card_abi": "NO_CLAIM", "release_eligible": False,
        "dev_callable": False, "experiment_only": True,
        "verdict_prefix": "SIMULATED_ (never API_ENTRY_CONFIRMED, never compatibility)",
    }


def _clean_variant_files(output: Path, variant: str) -> None:
    """Remove this variant's regenerable build artifacts (gitignored out dir only).

    The normal pipeline overwrites freely; the experiment keeps a no-overwrite
    guard as a concurrent-run backstop, so stale files from a previous local
    run of the SAME variant are removed up front. Only exact variant filenames
    are touched; anything else (including other variants) is left alone.
    """
    base_variant = f"{variant}-base"
    exp_name = f"dev-ipl-loader-bios-not-compatible-{variant}.pce"
    for name in (f"dev-ipl-loader-bios-not-compatible-{base_variant}.pce",
                 f"ipl-loader-bios-{base_variant}.lbl", f"ipl-loader-bios-{base_variant}.map",
                 f"ipl_loader_{base_variant}.o", f"manifest-{base_variant}.json",
                 exp_name, f"manifest-{variant}-experiment.json"):
        try:
            (output / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ValueError(f"cannot clean stale {name}: {error}") from None


def build_slot02_return_bios(original_path: Path, output: Path, sel: int, mode: str = "debug",
                             variant: str = "slot02ret", defines=None,
                             private_input: bool = False):
    """Build (base_rom, exp_rom, symbols). `defines` selects the loader format.

    None (default) means PUBLIC; pass [] explicitly for the DEV loader.
    """
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    defines = resolve_defines(defines)
    output.mkdir(parents=True, exist_ok=True)
    _clean_variant_files(output, variant)
    base_variant = f"{variant}-base"
    base_rom, _ = build_ipl_loader_bios(original_path, output, sel, mode, base_variant, defines,
                                        private_input=private_input)
    base_labels = output / f"ipl-loader-bios-{base_variant}.lbl"
    addrs = parse_labels(base_labels, ("api_slot_02", "api_unimplemented"))
    if "api_slot_02" not in addrs or "api_unimplemented" not in addrs:
        raise ValueError("base labels missing api_slot_02/api_unimplemented")
    base = base_rom.read_bytes()
    if len(base) != IMAGE_BYTES:
        raise ValueError("base image is not 256 KiB")
    stub = assemble_stub_bytes(output, mode)
    off, before, after = patch_preview(base, addrs["api_slot_02"], addrs["api_unimplemented"], stub)
    patched = apply_patch(base, off, before, after)
    exp_name = base_rom.name.replace(f"-{base_variant}.pce", f"-{variant}.pce")
    if exp_name == base_rom.name:
        raise ValueError("experiment ROM name did not change (refusing overwrite risk)")
    exp_rom = output / exp_name
    if exp_rom.exists():
        raise ValueError("experiment ROM already exists (no overwrite)")
    exp_rom.write_bytes(patched)
    data = exp_rom.read_bytes()
    if len(data) != IMAGE_BYTES or data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("experiment image changed outside bank 0")
    vector_words = [int.from_bytes(data[off_:off_ + 2], "little") for off_ in range(0x1FF6, 0x2000, 2)]
    base_words = [int.from_bytes(base[off_:off_ + 2], "little") for off_ in range(0x1FF6, 0x2000, 2)]
    if vector_words != base_words:
        raise ValueError("experiment patch moved the vectors (refusing)")
    sel_note = "redacted (private media)" if private_input else f"${sel:04X} (synthetic)"
    manifest = experiment_manifest(base_rom.name, exp_name, data, addrs, sel_note, mode, variant,
                                   defines)
    (output / f"manifest-{variant}-experiment.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # NOTE: no probe labels are written here. Patching moved nothing, so the
    # runner reuses the base label addresses explicitly per payload/run.
    return base_rom, exp_rom, addrs
