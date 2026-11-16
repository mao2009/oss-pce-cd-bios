#!/usr/bin/env python3
"""Build the PUBLIC IPL loader + G5 experimental wiring (Issue #800, parent #635).

DEV-ONLY, synthetic media only. NOT a compatible System Card BIOS.
NOT a BIOS ABI. NOT release eligible. NOT DEV_CALLABLE.

Combines the commercial-compatible PUBLIC IPL boot path
(tests/integration/ipl_loader_rom.s with IPL_PUBLIC: INFO sector + multi-
sector BODY, IPLSTA/IPLJMP, MPR2..6 mapping, 16-bit IPL_SEL) with the G5
experimental real-processing bodies:

  slot $02: api_slot_02 fallback (5 bytes) -> JMP cd_base_proto_entry + NOPs
  slot $33: $E099 table entry -> JMP experimental_slot_33 (EX_DSPOFF fragment)
  slot $03: $E009 table entry -> JMP cd_read_link_entry (CD_READ link V1)

Reuses (no large new builder, no logic duplication):
  - tools/build_dev_ipl_loader_bios.py: loader source, bank0 CODE/VECT layout,
    IPL_SEL 16-bit contract, reset/SEI + vector checks, manifest discipline.
  - tools/build_experimental_bios_boot.py: ordered fail-closed patch helpers
    (_patch_slot02_to_proto, _redirect_slot33_to_body, _redirect_slot03_to_link)
    and unified_api_windows (API-area diff must be exactly the three windows).
  - tools/build_rom.py: assemble_api_objects (normal DEV 18 overrides,
    byte-identical) + verify_dev_rom (tamper refusal).
  - src/cd_base_proto.s, src/cd_read_link.s (unchanged, BOOT segment).
  - src/ipl_loader_g5_glue.s (new, tiny): XSEL selection record (same layout
    as experimental_bios_boot.s, for the proto's INDEX01 comparison) +
    experimental_slot_33 wrapper (.include ex_dspoff.s, single source).

The XSEL record and IPL_SEL are derived from the SAME synthetic INDEX 01 LBA
(sel, 16-bit). Mismatch is refused by construction (single sel input); a disc
whose INFO/BODY layout disagrees halts in-guest (IPL validation), never
executes blindly. The proto's INDEX01, the loader's INFO (INDEX01+1) and BODY
(INDEX01+IPLBLK) therefore bind to one synthetic LBA. Commercial LBA values
above 16 bits are BLOCKED (16-bit embedding contract, same as the PUBLIC
loader); no silent truncation.

$4A (KEY_BIOS/EX_MEMOPEN) is NEVER patched here. Immediate-RTS stays a
separate SIMULATED control (tools/build_slot02_return_loader.py), never wired
as real processing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

if __package__:
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, overrides, verify_dev_rom
    from .build_dev_ipl_loader_bios import LOADER_SOURCE, REQUIRED as LOADER_REQUIRED, parse_labels
    from .build_experimental_bios_boot import (
        SLOT02_PATCH_LEN,
        _patch_slot02_to_proto,
        _redirect_slot03_to_link,
        _redirect_slot33_to_body,
        unified_api_windows,
    )
    from .environment import DEPENDENCIES, executable
else:
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, overrides, verify_dev_rom
    from build_dev_ipl_loader_bios import LOADER_SOURCE, REQUIRED as LOADER_REQUIRED, parse_labels
    from build_experimental_bios_boot import (
        SLOT02_PATCH_LEN,
        _patch_slot02_to_proto,
        _redirect_slot03_to_link,
        _redirect_slot33_to_body,
        unified_api_windows,
    )
    from environment import DEPENDENCIES, executable

GLUE_SOURCE = ROOT / "src" / "ipl_loader_g5_glue.s"
PROTO_SOURCE = ROOT / "src" / "cd_base_proto.s"
LINK_SOURCE = ROOT / "src" / "cd_read_link.s"
BANK0_CFG = ROOT / "src" / "bank0.cfg"

ROM_STEM = "dev-ipl-loader-g5-not-compatible"
EXP_LABELS = ("api_slot_02", "api_unimplemented", "cd_base_proto_entry",
              "api_slot_03", "cd_read_link_entry",
              "api_slot_33", "experimental_slot_33",
              "experimental_selection")
ALL_LABELS = tuple(dict.fromkeys(list(LOADER_REQUIRED) + list(EXP_LABELS)))


def api_windows_for(slot02_addr: int) -> set:
    """API-area file offsets allowed to differ (three patch windows). Pure."""
    return unified_api_windows(slot02_addr)


# ---- Output safety (Issue #800 follow-up, PR #801 independent review) ----
# Mirrors the PR #799 output-safety policy: lstat without following links,
# refuse before any write, realpath same-directory comparison, no ancestor
# checks (policy parity). Check-then-use cannot fully exclude a concurrent
# writer swapping entries between the check and the write; callers must not
# run concurrent builds into one output directory (same residual TOCTOU
# limit as the documented ./rom handling).

_VARIANT_RE = re.compile(r"[A-Za-z0-9_-]+")
_VARIANT_MAX_LEN = 64


def _lstat_kind(path):
    """Classify `path` without following symlinks (copy of the PR #799 helper)."""
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return None
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _refuse_symlink(path):
    """Refuse a pre-planted symlink before generation writes through it."""
    if os.path.lexists(path) and _lstat_kind(path) == "symlink":
        raise ValueError(f"{path} is a symlink; refusing to write through it")


def _same_directory(first, second):
    """True when both paths name the same directory (symlinks resolved)."""
    return (os.path.normcase(os.path.realpath(first))
            == os.path.normcase(os.path.realpath(second)))


def _refuse_unsafe_output_dir(output):
    """Refuse a symlinked or non-directory output before mkdir (R1)."""
    if os.path.lexists(output):
        if _lstat_kind(output) == "symlink":
            raise ValueError(f"{output} is a symlink; refusing to write through it")
        if _lstat_kind(output) != "dir":
            raise ValueError(f"{output} is not a plain directory; refusing to write into it")


def _refuse_unsafe_variant(variant):
    """Constrain variant to a safe single path component, checked before any write.

    The variant is embedded in every generated filename, so anything but a
    plain component could escape the output directory (absolute/traversal
    paths) or collide with a generated leaf. Refuses empty/non-string names,
    path separators, absolute/drive/UNC paths, dot names, NUL bytes, names
    outside [A-Za-z0-9_-]+, and overlong names. "main", "t800" and
    "t800rel" stay usable.
    """
    if type(variant) is not str or not variant:
        raise ValueError(f"variant must be a non-empty string, got {variant!r}")
    if "/" in variant or "\\" in variant:
        raise ValueError(f"variant must be a single path component without directories: {variant!r}")
    if variant in (".", ".."):
        raise ValueError(f"variant must not be {variant!r}")
    if "\x00" in variant:
        raise ValueError("variant must not contain NUL bytes")
    if os.path.isabs(variant) or re.match(r"[A-Za-z]:", variant) or variant.startswith("\\\\"):
        raise ValueError(f"variant must not be an absolute or drive path: {variant!r}")
    if len(variant) > _VARIANT_MAX_LEN:
        raise ValueError(f"variant must be at most {_VARIANT_MAX_LEN} characters: {variant!r}")
    if _VARIANT_RE.fullmatch(variant) is None:
        raise ValueError(f"variant must match [A-Za-z0-9_-]+: {variant!r}")


def _g5_leaf_names(variant: str) -> list:
    """Every file build_ipl_loader_g5_rom may write into the output dir. Pure.

    Covers assemble_api_objects() leaves (api_table.s, api_table.o,
    per-override objects), the G5 objects, the linker bank/labels/map, the
    rewritten bank0 cfg, the ROM image, the manifest and the trace labels.
    A duplicate name fails closed (naming-scheme regression can never
    silently alias two outputs).
    """
    ov = overrides(ROOT)
    names = ["api_table.s", "api_table.o"]
    names += [f"{ov[slot].stem}.o" for slot in sorted(ov)]
    names += [f"cd_base_proto_{variant}.o", f"cd_read_link_{variant}.o",
              f"ipl_loader_g5_glue_{variant}.o", f"ipl_loader_{variant}.o",
              "bank0-ipl-loader-g5.cfg",
              f"bank0-{variant}-g5.bin", f"ipl-loader-g5-{variant}.lbl",
              f"ipl-loader-g5-{variant}.map",
              f"{ROM_STEM}-{variant}.pce",
              f"manifest-{variant}-g5.json", f"trace-labels-{variant}-g5.txt"]
    if len(set(names)) != len(names):
        raise ValueError(f"G5 leaf names collide for variant {variant!r}")
    return names


def bank0_cfg_text_g5(out: Path) -> Path:
    """bank0.cfg with BOOT (experimental) + CODE (PUBLIC loader) + VECT.

    BOOT stays at $F000 (experimental proto/link/glue, same as the G5 profile
    image); CODE follows BOOT (PUBLIC loader, relocatable within $F000-$FFF5);
    VECT replaces VECTORS at $FFF6 (loader vectors, no experimental VECTORS
    because cd_load_exec_probe.s is not linked here).
    """
    text = BANK0_CFG.read_text(encoding="utf-8")
    boot_needle = "    BOOT: load = ROM0, type = ro, start = $F000;\n"
    if text.count(boot_needle) != 1:
        raise ValueError("bank0.cfg layout changed: expected exactly one BOOT segment line")
    text = text.replace(
        boot_needle,
        "    BOOT: load = ROM0, type = ro, start = $F000;\n"
        "    CODE: load = ROM0, type = ro;\n")
    vec_needle = "    VECTORS: load = ROM0, type = ro, start = $FFF6;\n"
    if text.count(vec_needle) != 1:
        raise ValueError("bank0.cfg layout changed: expected exactly one VECTORS segment line")
    text = text.replace(vec_needle, "    VECT: load = ROM0, type = ro, start = $FFF6;\n")
    cfg = out / "bank0-ipl-loader-g5.cfg"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def _exp_defines(sel: int, track: int) -> list:
    """ca65 defines binding XSEL record to the same INDEX 01 as IPL_SEL."""
    return ["-D", f"EXP_SELECTED_KIND=$01",
            "-D", f"EXP_SELECTED_TRACK=${track:02X}",
            "-D", f"EXP_SELECTED_LBA_HI=${(sel >> 16) & 0x1F:02X}",
            "-D", f"EXP_SELECTED_LBA_MID=${(sel >> 8) & 0xFF:02X}",
            "-D", f"EXP_SELECTED_LBA_LO=${sel & 0xFF:02X}"]


def _normalize_defines(defines) -> tuple:
    """Validate caller defines against an explicit whitelist; return (canonical_argv, is_public). Pure.

    ca65 splices unknown argv items straight into its command line, so an
    open-ended defines list is an option-injection vector (`-l`/`-o`/
    `--create-dep`/`-I` would write outside the verified output tree, and a
    caller `-D IPL_SEL=...` could shadow the builder's own symbols). Only
    these exact inputs are accepted; everything else is refused before any
    mkdir/assemble/write:
      None                       -> (["-D", "IPL_PUBLIC"], True)   PUBLIC default
      []                         -> ([], False)                      DEV (existing contract)
      ["-D", "IPL_PUBLIC"]     -> (["-D", "IPL_PUBLIC"], True)   PUBLIC
      ["-D", "IPL_PUBLIC=1"]   -> (["-D", "IPL_PUBLIC"], True)   PUBLIC (value form normalized;
                                                                     the source only tests .ifdef,
                                                                     so the ROM bytes are unchanged)
    Tuples are accepted as lists; non-string items and any other shape or
    value (including `-D IPL_SEL=`/`EXP_SELECTED_*=` shadows) are refused.
    The returned canonical argv is the only thing ever passed to ca65, and
    the manifest records exactly that argv.
    """
    if defines is None:
        return ["-D", "IPL_PUBLIC"], True
    if isinstance(defines, tuple):
        defines = list(defines)
    if type(defines) is not list or any(type(item) is not str for item in defines):
        raise ValueError(f"defines must be None or a list of strings, got {defines!r}")
    if defines == []:
        return [], False
    if defines == ["-D", "IPL_PUBLIC"] or defines == ["-D", "IPL_PUBLIC=1"]:
        return ["-D", "IPL_PUBLIC"], True
    raise ValueError("defines must be None, [], [\"-D\", \"IPL_PUBLIC\"] or "
                     f"[\"-D\", \"IPL_PUBLIC=1\"]: {defines!r}")


def g5_manifest(sel: int, track: int, mode: str, variant: str, defines: list,
                data: bytes, symbols: dict, link_labels: dict, rom_name: str,
                private_input: bool = False) -> dict:
    # `defines` must be the canonical argv from _normalize_defines, so this
    # strict equality classifies exactly the build ca65 performed (MEDIUM-1:
    # no guessing from substrings like ["-I", "IPL_PUBLIC"]).
    is_public = list(defines) == ["-D", "IPL_PUBLIC"]
    manifest = {
        "status": ("PUBLIC-IPL-FORMAT-LOADER-G5-EXPERIMENTAL-synthetic-"
                   "not-compatible-not-a-bios-abi"
                   if is_public else
                   "DEV-IPL-FORMAT-LOADER-G5-EXPERIMENTAL-synthetic-"
                   "not-compatible-not-a-bios-abi"),
        "ipl_format": "public" if is_public else "dev",
        "mode": mode, "variant": variant, "rom_name": rom_name,
        "rom_sha256": hashlib.sha256(data).hexdigest(), "rom_bytes": IMAGE_BYTES,
        "loader_source": "tests/integration/ipl_loader_rom.s (unchanged, Issue #670)",
        "glue_source": "src/ipl_loader_g5_glue.s (Issue #800, XSEL + slot33 wrapper)",
        "experimental_sources": ["src/cd_base_proto.s", "src/cd_read_link.s",
                                 "src/ex_dspoff.s (via glue, single source)"],
        "defines": list(defines), "symbols": dict(symbols), "labels": dict(link_labels),
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "pinned_geargrafx": DEPENDENCIES["geargrafx"]["revision"],
        "assumption": ("IPL_SEL (16-bit INDEX 01 for INFO/BODY) and the XSEL "
                       "record (proto INDEX01) derive from the same synthetic sel; "
                       "mismatch halts in-guest, never executes blindly. Payload bytes "
                       "are NOT embedded."),
        "payload_embedded_in_rom": False, "system_card_abi": "NO_CLAIM",
        "release_eligible": False, "dev_callable": False,
        "experimental_slots": ["$02", "$03", "$33"],
        "reference_jsr": "0/81", "abi_verified": "0/81",
        "private_media_used": bool(private_input),
    }
    if private_input:
        manifest["ipl_sel_lba_redacted"] = True
        manifest["exp_selection_redacted"] = True
    else:
        manifest["ipl_sel_lba"] = sel
        manifest["exp_selected_track"] = track
        manifest["exp_selected_lba"] = sel
        manifest["private_media_used"] = False
    return manifest


def build_ipl_loader_g5_rom(original_path: Path, output: Path, sel: int,
                            mode: str = "debug", variant: str = "main",
                            defines=None, track: int = 1,
                            private_input: bool = False):
    """Return (rom_path, symbols). `sel` is the 16-bit INDEX 01 LBA.

    `defines` selects the loader format through an explicit whitelist
    (see _normalize_defines): None/default = PUBLIC, [] = DEV,
    ["-D", "IPL_PUBLIC"] or ["-D", "IPL_PUBLIC=1"] = PUBLIC (normalized).
    Anything else is refused before any mkdir/assemble/write, so no ca65
    option injection (`-l`/`-o`/`--create-dep`/`-I`) and no shadowing of the
    builder-owned `-D IPL_SEL=`/`EXP_SELECTED_*=` symbols is possible.
    `track` is the synthetic data-track number for the XSEL record (1..99).
    Only the (sel, track) pair with kind MODE1/2352 is embedded; commercial
    LBA above 16 bits is BLOCKED (no truncation).
    """
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    if type(sel) is not int or not 0 <= sel <= 0xFFFF:
        raise ValueError("IPL_SEL must be a 16-bit INDEX 01 LBA (0x0000..0xFFFF)")
    if type(track) is not int or not 1 <= track <= 99:
        raise ValueError("EXP_SELECTED_TRACK must be 1..99")
    _refuse_unsafe_variant(variant)
    canon_defines, _ = _normalize_defines(defines)  # is_public unused: manifest derives it
    for src in (PROTO_SOURCE, LINK_SOURCE, GLUE_SOURCE):
        if not src.is_file():
            raise ValueError(f"experimental source missing: {src}")
    original = Path(original_path).read_bytes()
    verify_dev_rom(original, manifest=Path(original_path).parent / "manifest.json")
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True,
                                 capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("G5 IPL loader toolchain is not the pinned cc65 build")
    output = Path(output)
    # R3: refuse the normal DEV ROM directory before any output write.
    # Realpath comparison covers spelling variants, "..", relative paths
    # and symlink aliases; the input ROM and its manifest are left untouched.
    if _same_directory(output, Path(original_path).parent):
        raise ValueError("G5 IPL loader output must not be the normal DEV ROM directory "
                         "(it would replace the normal DEV manifest)")
    # R1: refuse a symlinked or non-directory output before mkdir.
    _refuse_unsafe_output_dir(output)
    # R2: refuse symlinked generation leaves before the first write, so a
    # refused rebuild never partially overwrites and genuine rebuilds (plain
    # files) proceed. Covers assemble_api_objects() leaves too.
    for leaf in _g5_leaf_names(variant):
        _refuse_symlink(output / leaf)
    output.mkdir(parents=True, exist_ok=True)
    # Labels for the loader/proto/link/glue objects must survive in release
    # mode too: `reset`/`irq_stub`/etc. are local labels, and the pinned
    # linker only emits them to the -Ln file with debug info. `-g` never
    # changes ROM bytes (host tests assert debug/release byte-identity for
    # the normal DEV ROM; the same holds here because only -g/-l differ).
    # assemble_api_objects() keeps its own mode discipline (overrides always
    # get -g for verification); the G5 objects below always get -g so the
    # release image still carries the required symbols (Major review fix).
    g5_debug_flags = ["-g"]
    objects = assemble_api_objects(output, mode)
    exp_defs = _exp_defines(sel, track)
    for src, extra in ((PROTO_SOURCE, exp_defs), (LINK_SOURCE, []),
                       (GLUE_SOURCE, exp_defs)):
        obj = output / f"{src.stem}_{variant}.o"
        subprocess.run([ca65, str(src), *extra, "-o", str(obj), *g5_debug_flags],
                       check=True)
        objects.append(obj)
    loader_obj = output / f"ipl_loader_{variant}.o"
    subprocess.run([ca65, str(LOADER_SOURCE), "-D", f"IPL_SEL=${sel:04X}",
                    *canon_defines, "-o", str(loader_obj), *g5_debug_flags], check=True)
    objects.append(loader_obj)
    bank, labels = output / f"bank0-{variant}-g5.bin", output / f"ipl-loader-g5-{variant}.lbl"
    cfg = bank0_cfg_text_g5(output)
    subprocess.run([ld65, "-C", str(cfg), "-o", str(bank), "-Ln", str(labels),
                    "-m", str(output / f"ipl-loader-g5-{variant}.map"),
                    *map(str, objects)], check=True)
    symbols = parse_labels(labels, ALL_LABELS)
    missing = [n for n in ALL_LABELS if n not in symbols]
    if missing:
        raise ValueError(f"G5 IPL loader symbols missing: {missing}")
    bank_bytes = bank.read_bytes()
    if len(bank_bytes) != BANK_BYTES:
        raise ValueError("G5 IPL loader bank 0 is not 8 KiB")
    # Pre-patch API area must equal the normal DEV ROM (experimental bodies
    # live in BOOT, loader in CODE; neither touches $E000-$EFFF).
    if bank_bytes[:0x1000] != original[:0x1000]:
        raise ValueError("G5 pre-patch bank changed the API table/override area")
    # Ordered fail-closed patches: slot-$02 first, then slot-$33, then slot-$03
    # (same order and checks as tools/build_experimental_bios_boot.py).
    patched = _patch_slot02_to_proto(
        bank_bytes, symbols["api_slot_02"], symbols["api_unimplemented"],
        symbols["cd_base_proto_entry"])
    patched = _redirect_slot33_to_body(
        patched, symbols["api_slot_33"], symbols["experimental_slot_33"])
    patched = _redirect_slot03_to_link(
        patched, symbols["api_slot_03"], symbols["cd_read_link_entry"])
    slot02_addr = symbols["api_slot_02"]
    allowed = unified_api_windows(slot02_addr)
    api_diff = [i for i in range(0x1000) if bytes(patched)[i] != original[i]]
    if not set(api_diff) <= allowed:
        raise ValueError("G5 image changed the API area outside the "
                         "slot-$02/slot-$33/slot-$03 patch windows")
    slot02_window = set(range(slot02_addr - 0xE000,
                              slot02_addr - 0xE000 + SLOT02_PATCH_LEN))
    slot33_window = set(range(3 * 0x33, 3 * 0x33 + 3))
    slot03_window = set(range(3 * 0x03, 3 * 0x03 + 3))
    if (not (set(api_diff) & slot02_window)
            or not (set(api_diff) & slot33_window)
            or not (set(api_diff) & slot03_window)):
        raise ValueError("G5 image is missing a patch window; refusing silent no-op")
    proto = symbols["cd_base_proto_entry"]
    if bytes(patched[slot02_addr - 0xE000:slot02_addr - 0xE000 + SLOT02_PATCH_LEN]) != \
            bytes((0x4C, proto & 0xFF, (proto >> 8) & 0xFF, 0xEA, 0xEA)):
        raise ValueError("slot-$02 window is not the expected JMP proto+NOP pad")
    slot33_body = symbols["experimental_slot_33"]
    if bytes(patched[3 * 0x33:3 * 0x33 + 3]) != \
            bytes((0x4C, slot33_body & 0xFF, (slot33_body >> 8) & 0xFF)):
        raise ValueError("slot-$33 window is not the expected JMP body")
    link_entry = symbols["cd_read_link_entry"]
    if bytes(patched[3 * 0x03:3 * 0x03 + 3]) != \
            bytes((0x4C, link_entry & 0xFF, (link_entry >> 8) & 0xFF)):
        raise ValueError("slot-$03 window is not the expected JMP link")
    # $4A must stay a stub (SIMULATED control separate, never wired here).
    at_4a = 3 * 0x4A
    if bytes(patched[at_4a:at_4a + 3]) != bytes(original[at_4a:at_4a + 3]):
        raise ValueError("$4A table entry changed; SIMULATED control must stay separate")
    # MEDIUM-2: build the final image in memory and verify it BEFORE any
    # disk write. A failed verification leaves no .pce behind and never
    # touches a previous good ROM/manifest/trace. Writes are sequential, so
    # multi-file atomicity is NOT guaranteed (a crash between files can leave
    # a mixed set); each file is complete-or-absent, never partial, because
    # write_bytes/write_text either succeed or raise before publishing.
    data = bytes(patched) + b"\xff" * (IMAGE_BYTES - BANK_BYTES)
    if len(data) != IMAGE_BYTES or data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("G5 image changed size or unused banks")
    for name in LOADER_REQUIRED:
        if name != "sig" and not 0xF000 <= symbols[name] < 0xFFF6:
            raise ValueError(f"loader symbol {name} at {symbols[name]:04x} outside $F000-$FFF5")
    vector_words = [int.from_bytes(data[off:off + 2], "little")
                    for off in range(0x1FF6, 0x2000, 2)]
    if (vector_words[:3] != [symbols["irq_stub"]] * 3
            or vector_words[3] != symbols["irq_stub"]
            or vector_words[4] != symbols["reset"]):
        raise ValueError("vector words do not match irq_stub x4 + reset")
    reset_vector = int.from_bytes(data[0x1FFE:0x2000], "little")
    if reset_vector != symbols["reset"]:
        raise ValueError(f"reset vector {reset_vector:04x} is not the loader reset")
    if data[reset_vector & 0x1FFF] != 0x78:
        raise ValueError("loader reset must start with SEI (the probe contract)")
    # Final write targets are recreated on every build; re-check them here
    # in case an earlier stage planted a link (defense in depth; the R2
    # pre-check above is the gate, this is a backstop).
    rom_name = f"{ROM_STEM}-{variant}.pce"
    manifest_name = f"manifest-{variant}-g5.json"
    trace_name = f"trace-labels-{variant}-g5.txt"
    for leaf in (rom_name, manifest_name, trace_name):
        _refuse_symlink(output / leaf)
    rom = output / rom_name
    rom.write_bytes(data)
    link_labels = {
        "api_slot_02": symbols["api_slot_02"],
        "api_slot_03": symbols["api_slot_03"],
        "api_slot_33": symbols["api_slot_33"],
        "api_unimplemented": symbols["api_unimplemented"],
        "cd_base_proto_entry": symbols["cd_base_proto_entry"],
        "experimental_slot_33": symbols["experimental_slot_33"],
        "cd_read_link_entry": symbols["cd_read_link_entry"],
        "experimental_selection": symbols["experimental_selection"],
        "slot_02_table_entry": 0xE000 + 3 * 0x02,
        "slot_03_table_entry": 0xE000 + 3 * 0x03,
        "slot_33_table_entry": 0xE000 + 3 * 0x33,
    }
    manifest = g5_manifest(sel, track, mode, variant, canon_defines, data, symbols,
                           link_labels, rom.name, private_input=private_input)
    (output / manifest_name).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (output / trace_name).write_text(
        "".join(f"{name} {value:04x}\n" for name, value in symbols.items()),
        encoding="ascii")
    return rom, symbols


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    parser.add_argument("--sel", type=lambda s: int(s, 0), required=True,
                        help="16-bit INDEX 01 LBA (e.g. 0x109)")
    parser.add_argument("--track", type=int, default=1)
    parser.add_argument("--variant", default="main")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public", dest="public", action="store_true", default=True)
    parser.add_argument("--dev", dest="public", action="store_false")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    defines = ["-D", "IPL_PUBLIC"] if args.public else []
    try:
        rom, _ = build_ipl_loader_g5_rom(base, args.output, args.sel, args.mode,
                                         args.variant, defines, args.track)
        print(f"PASS G5 IPL loader (NOT compatible, NOT a BIOS ABI): {rom}")
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"G5 IPL loader FAIL/BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
