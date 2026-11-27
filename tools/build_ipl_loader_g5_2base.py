#!/usr/bin/env python3
"""Build the PUBLIC IPL loader + G5 2-base experimental wiring (CD_BASE 2-system bases).

DEV-ONLY, synthetic media only. NOT a compatible System Card BIOS.
NOT a BIOS ABI. NOT release eligible. NOT DEV_CALLABLE.

Combines the commercial-compatible PUBLIC IPL boot path
(tests/integration/ipl_loader_rom.s with IPL_PUBLIC: INFO sector + multi-
sector BODY, IPLSTA/IPLJMP, MPR2..6 mapping, 16-bit IPL_SEL) with the G5
experimental real-processing bodies:

  slot $02: api_slot_02 fallback (5 bytes) -> JMP cd_base_2base_entry + NOPs
  slot $33: $E099 table entry -> JMP experimental_slot_33 (EX_DSPOFF fragment)
  slot $03: $E009 table entry -> JMP cd_read_commercial_entry (CD_READ commercial V1,
    FIRST mode) by default, or JMP cd_read_link_entry (CD_READ link V1) with
    cd_read="link" for the Control ROM.
  slot $4A: $E0DE table entry stays on the linked fallback stub by default, or
    -> JMP ex_memopen_entry (EX_MEMOPEN preservation-only experimental body,
    src/ex_memopen.s) with cd_4a="experimental" (Issue #84; default keeps
    every existing test and the SIMULATED separation intact).
  slot $25: $E06F table entry stays on the linked fallback stub by default, or
    -> JMP ex_scrmod_entry (EX_SCRMOD B1 preservation-only SIMULATED scaffold,
    src/ex_scrmod.s) with cd_25="experimental" (Issue #47; default keeps
    every existing test and the SIMULATED separation intact).
  slot $24: $E06C table entry stays on the linked fallback stub by default, or
    -> JMP ex_dotmod_entry (EX_DOTMOD B1-analogous preservation-only SIMULATED
    scaffold, src/ex_dotmod.s) with cd_24="experimental" (Issue #46; default
    keeps every existing test and the SIMULATED separation intact).
  slot $36: $E0A2 table entry stays on the linked fallback stub by default, or
    -> JMP ex_satclr_entry (EX_SATCLR B1-analogous preservation-only SIMULATED
    scaffold, src/ex_satclr.s) with cd_36="experimental" (Issue #64; default
    keeps every existing test and the SIMULATED separation intact).

Reuses (no large new builder, no logic duplication):
  - tools/build_dev_ipl_loader_bios.py: loader source, bank0 CODE/VECT layout,
    IPL_SEL 16-bit contract, reset/SEI + vector checks, manifest discipline.
  - tools/build_experimental_bios_boot.py: ordered fail-closed patch helpers
    (_patch_slot02_to_proto, _redirect_slot33_to_body, _redirect_slot03_to_link)
    and unified_api_windows (API-area diff must be exactly the documented windows).
  - tools/build_rom.py: assemble_api_objects (normal DEV 18 overrides,
    byte-identical) + verify_dev_rom (tamper refusal).
  - src/cd_base_2base.s, src/cd_read_commercial.s (BOOT segment). Separate variant from proto/link V1.
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

$4A (KEY_BIOS/EX_MEMOPEN) is stub by default here. Immediate-RTS stays a
separate SIMULATED control (tools/build_slot02_return_loader.py), never wired
as real processing. Opt-in experimental wiring lives behind cd_4a=
"experimental" (Issue #84 preservation-only body, docs/84-4a-contract.md).
$25 (EX_SCRMOD) is stub by default here. Opt-in experimental wiring lives
behind cd_25="experimental" (Issue #47 B1 preservation-only SIMULATED
scaffold, docs/47-ex-scrmod-contract.md + docs/47-ex-scrmod-b1.md).
$24 (EX_DOTMOD) is stub by default here. Opt-in experimental wiring lives
behind cd_24="experimental" (Issue #46 B1-analogous preservation-only
SIMULATED scaffold, docs/46-ex-dotmod-contract.md + docs/46-ex-dotmod-b1.md).
$36 (EX_SATCLR) is stub by default here. Opt-in experimental wiring lives
behind cd_36="experimental" (Issue #64 B1-analogous preservation-only
SIMULATED scaffold, docs/64-ex-satclr-contract.md + docs/64-ex-satclr-b1.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__:
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, overrides, verify_dev_rom
    from .build_dev_ipl_loader_bios import LOADER_SOURCE, REQUIRED as LOADER_REQUIRED, parse_labels
    from .build_experimental_bios_boot import (
        SLOT02_PATCH_LEN,
        _patch_slot02_to_proto,
        _redirect_slot03_to_link,
        _redirect_slot33_to_body,
        _redirect_slot4a_to_body,
        _redirect_slot25_to_body,
        _redirect_slot24_to_body,
        _redirect_slot36_to_body,
        _redirect_slot35_to_body,
        _redirect_slot2c_to_body,
        unified_api_windows,
        unified_api_windows_4a,
        unified_api_windows_25,
        unified_api_windows_4a_25,
        unified_api_windows_24,
        unified_api_windows_4a_24,
        unified_api_windows_25_24,
        unified_api_windows_4a_25_24,
        unified_api_windows_36,
        unified_api_windows_4a_36,
        unified_api_windows_25_36,
        unified_api_windows_4a_25_36,
        unified_api_windows_24_36,
        unified_api_windows_4a_24_36,
        unified_api_windows_25_24_36,
        unified_api_windows_4a_25_24_36,
        unified_api_windows_35,
        unified_api_windows_4a_35,
        unified_api_windows_25_35,
        unified_api_windows_4a_25_35,
        unified_api_windows_24_35,
        unified_api_windows_4a_24_35,
        unified_api_windows_25_24_35,
        unified_api_windows_4a_25_24_35,
        unified_api_windows_36_35,
        unified_api_windows_4a_36_35,
        unified_api_windows_25_36_35,
        unified_api_windows_4a_25_36_35,
        unified_api_windows_24_36_35,
        unified_api_windows_4a_24_36_35,
        unified_api_windows_25_24_36_35,
        unified_api_windows_4a_25_24_36_35,
        unified_api_windows_2c,
        unified_api_windows_4a_25_24_36_35_2c,
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
        _redirect_slot4a_to_body,
        _redirect_slot25_to_body,
        _redirect_slot24_to_body,
        _redirect_slot36_to_body,
        _redirect_slot35_to_body,
        _redirect_slot2c_to_body,
        unified_api_windows,
        unified_api_windows_4a,
        unified_api_windows_25,
        unified_api_windows_4a_25,
        unified_api_windows_24,
        unified_api_windows_4a_24,
        unified_api_windows_25_24,
        unified_api_windows_4a_25_24,
        unified_api_windows_36,
        unified_api_windows_4a_36,
        unified_api_windows_25_36,
        unified_api_windows_4a_25_36,
        unified_api_windows_24_36,
        unified_api_windows_4a_24_36,
        unified_api_windows_25_24_36,
        unified_api_windows_4a_25_24_36,
        unified_api_windows_35,
        unified_api_windows_4a_35,
        unified_api_windows_25_35,
        unified_api_windows_4a_25_35,
        unified_api_windows_24_35,
        unified_api_windows_4a_24_35,
        unified_api_windows_25_24_35,
        unified_api_windows_4a_25_24_35,
        unified_api_windows_36_35,
        unified_api_windows_4a_36_35,
        unified_api_windows_25_36_35,
        unified_api_windows_4a_25_36_35,
        unified_api_windows_24_36_35,
        unified_api_windows_4a_24_36_35,
        unified_api_windows_25_24_36_35,
        unified_api_windows_4a_25_24_36_35,
        unified_api_windows_2c,
        unified_api_windows_4a_25_24_36_35_2c,
    )
    from environment import DEPENDENCIES, executable

GLUE_SOURCE = ROOT / "src" / "ipl_loader_g5_glue.s"
PROTO_SOURCE = ROOT / "src" / "cd_base_2base.s"
LINK_SOURCE_COMMERCIAL = ROOT / "src" / "cd_read_commercial.s"
LINK_SOURCE_V1 = ROOT / "src" / "cd_read_link.s"
LINK_SOURCE = LINK_SOURCE_COMMERCIAL  # default; see cd_read= param
LINK_ENTRY_COMMERCIAL = "cd_read_commercial_entry"
LINK_ENTRY_V1 = "cd_read_link_entry"
MEMOPEN_SOURCE = ROOT / "src" / "ex_memopen.s"
MEMOPEN_ENTRY = "ex_memopen_entry"
SCRMOD_SOURCE = ROOT / "src" / "ex_scrmod.s"
SCRMOD_ENTRY = "ex_scrmod_entry"
DOTMOD_SOURCE = ROOT / "src" / "ex_dotmod.s"
DOTMOD_ENTRY = "ex_dotmod_entry"
SATCLR_SOURCE = ROOT / "src" / "ex_satclr.s"
SATCLR_ENTRY = "ex_satclr_entry"
SPRDMA_SOURCE = ROOT / "src" / "ex_sprdma.s"
SPRDMA_ENTRY = "ex_sprdma_entry"
IRQON_SOURCE = ROOT / "src" / "ex_irqon.s"
IRQON_ENTRY = "ex_irqon_entry"
BANK0_CFG = ROOT / "src" / "bank0.cfg"

ROM_STEM = "dev-ipl-loader-g5-not-compatible"
EXP_LABELS = ("api_slot_02", "api_unimplemented", "cd_base_2base_entry",
              "api_slot_03", "cd_read_commercial_entry",
              "api_slot_33", "experimental_slot_33",
              "api_slot_4A", "ex_memopen_entry",
              "api_slot_25", "ex_scrmod_entry",
              "api_slot_24", "ex_dotmod_entry",
              "api_slot_36", "ex_satclr_entry",
              "api_slot_35", "ex_sprdma_entry",
              "api_slot_2C", "ex_irqon_entry",
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


def _canonical_dev_dir(mode: str) -> Path:
    """The canonical normal DEV ROM output directory for `mode`. Pure path (no IO)."""
    return ROOT / "build" / mode / "bios-dev"


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


def _atomic_write(path: Path, data: bytes):
    """Publish `path` without ever truncating the previous file. Fail-closed.

    Writes to a fresh mkstemp file in the same directory (O_EXCL: a planted
    name can only fail the build, never redirect the write), fsyncs, then
    atomically replaces the target with os.replace. A mid-write failure
    (e.g. ENOSPC) leaves the previous file intact and unlinks the partial
    temp file. Each file is complete-or-absent; multi-file atomicity across
    the rom/manifest/trace set is NOT guaranteed (documented residual limit).
    """
    _refuse_symlink(path)
    parent = path.parent
    prefix, suffix = path.name + ".part-", ".tmp"
    fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=prefix, suffix=suffix)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o644)
        _refuse_symlink(path)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
    names += [f"cd_base_2base_{variant}.o", f"cd_read_commercial_{variant}.o",
              f"cd_read_link_{variant}.o", f"ex_memopen_{variant}.o",
              f"ex_scrmod_{variant}.o", f"ex_dotmod_{variant}.o", f"ex_satclr_{variant}.o",
              f"ex_sprdma_{variant}.o", f"ex_irqon_{variant}.o",
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
                private_input: bool = False, cd_read: str = "commercial",
                cd_4a: str = "stub", cd_25: str = "stub", cd_24: str = "stub", cd_36: str = "stub", cd_35: str = "stub", cd_2c: str = "stub") -> dict:
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
        "experimental_sources": ((["src/cd_base_2base.s", "src/cd_read_commercial.s",
                                  "src/ex_dspoff.s (via glue, single source)"]
                                 if cd_read == "commercial" else
                                 ["src/cd_base_2base.s", "src/cd_read_link.s",
                                  "src/ex_dspoff.s (via glue, single source)"])
                                + (["src/ex_memopen.s"] if cd_4a == "experimental" else [])
                                + (["src/ex_scrmod.s"] if cd_25 == "experimental" else [])
                                + (["src/ex_dotmod.s"] if cd_24 == "experimental" else [])
                                + (["src/ex_satclr.s"] if cd_36 == "experimental" else [])
                                + (["src/ex_sprdma.s"] if cd_35 == "experimental" else [])
                                + (["src/ex_irqon.s"] if cd_2c == "experimental" else [])),
        "defines": list(defines), "symbols": dict(symbols), "labels": dict(link_labels),
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "pinned_geargrafx": DEPENDENCIES["geargrafx"]["revision"],
        "assumption": ("IPL_SEL (16-bit INDEX 01 for INFO/BODY) and the XSEL "
                       "record (proto INDEX01) derive from the same synthetic sel; "
                       "mismatch halts in-guest, never executes blindly. Payload bytes "
                       "are NOT embedded."),
        "payload_embedded_in_rom": False, "system_card_abi": "NO_CLAIM",
        "release_eligible": False, "dev_callable": False,
        "experimental_slots": (["$02", "$03", "$33"]
                                + (["$4A"] if cd_4a == "experimental" else [])
                                + (["$25"] if cd_25 == "experimental" else [])
                                + (["$24"] if cd_24 == "experimental" else [])
                                + (["$36"] if cd_36 == "experimental" else [])
                                + (["$35"] if cd_35 == "experimental" else [])
                                + (["$2C"] if cd_2c == "experimental" else [])),
        "cd_4a": cd_4a,
        "cd_25": cd_25,
        "cd_24": cd_24,
        "cd_36": cd_36,
        "cd_35": cd_35,
        "cd_2c": cd_2c,
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
                            private_input: bool = False,
                            cd_read: str = "commercial",
                            cd_4a: str = "stub",
                            cd_25: str = "stub",
                            cd_24: str = "stub",
                            cd_36: str = "stub",
                            cd_35: str = "stub",
                            cd_2c: str = "stub"):
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
    `cd_read` selects the $03 wiring: "commercial" (default, FIRST-mode
    LOCAL helper for the Experimental ROM) or "link" (CD_READ link V1 for
    the Control ROM). Anything else is refused.
    `cd_4a` selects the $4A wiring: "stub" (default; the $E0DE table entry
    stays on the linked fallback stub, preserving every existing test and the
    SIMULATED separation) or "experimental" (Issue #84 preservation-only
    body, $E0DE -> JMP ex_memopen_entry). Anything else is refused.
    `cd_25` selects the $25 wiring: "stub" (default; the $E06F table entry
    stays on the linked fallback stub, preserving every existing test and the
    SIMULATED separation) or "experimental" (Issue #47 B1 preservation-only
    SIMULATED scaffold, $E06F -> JMP ex_scrmod_entry). Anything else is refused.
    `cd_24` selects the $24 wiring: "stub" (default; the $E06C table entry
    stays on the linked fallback stub, preserving every existing test and the
    SIMULATED separation) or "experimental" (Issue #46 B1-analogous
    preservation-only SIMULATED scaffold, $E06C -> JMP ex_dotmod_entry).
    Anything else is refused.
    `cd_2c` selects the $2C wiring: "stub" (default; the $E084 table entry
    stays on the linked fallback stub, preserving every existing test and the
    SIMULATED separation) or "experimental" (Issue #54 D2 limited-functional
    body, $E084 -> JMP ex_irqon_entry: CR R05 bit3 set via the shared $2502
    shadow, $20F3/$20F7 + CPU I + IDR untouched). Anything else is refused.
    Only the ($2C-only) and (full $4A+$25+$24+$36+$35+$2C) experimental
    combinations are supported; any other cd_2c="experimental" combination
    is refused fail-closed (bounded variant lattice, no power-set growth).
    """
    if cd_read not in ("commercial", "link"):
        raise ValueError("cd_read must be 'commercial' or 'link'")
    if cd_4a not in ("stub", "experimental"):
        raise ValueError("cd_4a must be 'stub' or 'experimental'")
    if cd_25 not in ("stub", "experimental"):
        raise ValueError("cd_25 must be 'stub' or 'experimental'")
    if cd_24 not in ("stub", "experimental"):
        raise ValueError("cd_24 must be 'stub' or 'experimental'")
    if cd_36 not in ("stub", "experimental"):
        raise ValueError("cd_36 must be 'stub' or 'experimental'")
    if cd_35 not in ("stub", "experimental"):
        raise ValueError("cd_35 must be 'stub' or 'experimental'")
    if cd_2c not in ("stub", "experimental"):
        raise ValueError("cd_2c must be 'stub' or 'experimental'")
    if cd_2c == "experimental" and not (
            (cd_4a == "stub" and cd_25 == "stub" and cd_24 == "stub"
             and cd_36 == "stub" and cd_35 == "stub")
            or (cd_4a == "experimental" and cd_25 == "experimental"
                and cd_24 == "experimental" and cd_36 == "experimental"
                and cd_35 == "experimental")):
        raise ValueError("cd_2c='experimental' supports only the $2C-only unit "
                         "combination and the full ExpF combination "
                         "($4A+$25+$24+$36+$35+$2C); refusing unbounded lattice growth")
    link_source = LINK_SOURCE_COMMERCIAL if cd_read == "commercial" else LINK_SOURCE_V1
    link_entry = LINK_ENTRY_COMMERCIAL if cd_read == "commercial" else LINK_ENTRY_V1
    # MAJOR-1: experimental bodies are opt-in only. A stub default must not
    # require, assemble, link, or expect the body's label/symbol; otherwise
    # BOOT gains bytes and the default image is no longer byte-identical.
    # Labels are built incrementally so the (stub, stub, stub, stub, stub) default keeps the
    # exact prior order plus the always-present api_slot_25/api_slot_24/api_slot_36/api_slot_35 table labels.
    exp_labels = ["api_slot_02", "api_unimplemented", "cd_base_2base_entry",
                  "api_slot_03", link_entry,
                  "api_slot_33", "experimental_slot_33",
                  "api_slot_4A"]
    if cd_4a == "experimental":
        exp_labels.append(MEMOPEN_ENTRY)
    exp_labels.append("api_slot_25")
    if cd_25 == "experimental":
        exp_labels.append(SCRMOD_ENTRY)
    exp_labels.append("api_slot_24")
    if cd_24 == "experimental":
        exp_labels.append(DOTMOD_ENTRY)
    exp_labels.append("api_slot_36")
    if cd_36 == "experimental":
        exp_labels.append(SATCLR_ENTRY)
    exp_labels.append("api_slot_35")
    if cd_35 == "experimental":
        exp_labels.append(SPRDMA_ENTRY)
    exp_labels.append("api_slot_2C")
    if cd_2c == "experimental":
        exp_labels.append(IRQON_ENTRY)
    exp_labels.append("experimental_selection")
    exp_labels = tuple(exp_labels)
    all_labels = tuple(dict.fromkeys(list(LOADER_REQUIRED) + list(exp_labels)))
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    if type(sel) is not int or not 0 <= sel <= 0xFFFF:
        raise ValueError("IPL_SEL must be a 16-bit INDEX 01 LBA (0x0000..0xFFFF)")
    if type(track) is not int or not 1 <= track <= 99:
        raise ValueError("EXP_SELECTED_TRACK must be 1..99")
    _refuse_unsafe_variant(variant)
    canon_defines, _ = _normalize_defines(defines)  # is_public unused: manifest derives it
    # MAJOR-1: stub defaults must not require the experimental sources.
    required_sources = [PROTO_SOURCE, link_source, GLUE_SOURCE]
    if cd_4a == "experimental":
        required_sources.append(MEMOPEN_SOURCE)
    if cd_25 == "experimental":
        required_sources.append(SCRMOD_SOURCE)
    if cd_24 == "experimental":
        required_sources.append(DOTMOD_SOURCE)
    if cd_36 == "experimental":
        required_sources.append(SATCLR_SOURCE)
    if cd_35 == "experimental":
        required_sources.append(SPRDMA_SOURCE)
    if cd_2c == "experimental":
        required_sources.append(IRQON_SOURCE)
    required_sources = tuple(required_sources)
    for src in required_sources:
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
    # Both the input ROM's own directory and the canonical project output
    # directory (ROOT/build/<mode>/bios-dev) are guarded: a temp copy used
    # as input must not re-open the canonical tree. Realpath comparison
    # covers spellings, "..", relative paths and symlink aliases; the input
    # ROM and its manifest are left untouched.
    if _same_directory(output, Path(original_path).parent):
        raise ValueError("G5 IPL loader output must not be the normal DEV ROM directory "
                         "(it would replace the normal DEV manifest)")
    if _same_directory(output, _canonical_dev_dir(mode)):
        raise ValueError("G5 IPL loader output must not be the canonical normal DEV ROM directory "
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
    # MAJOR-1: link each experimental body only when it is selected. The
    # (stub x5) order (proto, link, glue) is unchanged, so the default
    # image stays byte-identical to the prior all-stub baseline.
    g5_sources = [(PROTO_SOURCE, exp_defs), (link_source, [])]
    if cd_4a == "experimental":
        g5_sources.append((MEMOPEN_SOURCE, []))
    if cd_25 == "experimental":
        g5_sources.append((SCRMOD_SOURCE, []))
    if cd_24 == "experimental":
        g5_sources.append((DOTMOD_SOURCE, []))
    if cd_36 == "experimental":
        g5_sources.append((SATCLR_SOURCE, []))
    if cd_35 == "experimental":
        g5_sources.append((SPRDMA_SOURCE, []))
    if cd_2c == "experimental":
        g5_sources.append((IRQON_SOURCE, []))
    g5_sources.append((GLUE_SOURCE, exp_defs))
    for src, extra in g5_sources:
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
    symbols = parse_labels(labels, all_labels)
    missing = [n for n in all_labels if n not in symbols]
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
    # (same order and checks as tools/build_experimental_bios_boot.py), plus
    # the opt-in slot-$4A / slot-$25 / slot-$24 / slot-$36 / slot-$35 redirects last (stub defaults keep prior
    # images byte-identical).
    patched = _patch_slot02_to_proto(
        bank_bytes, symbols["api_slot_02"], symbols["api_unimplemented"],
        symbols["cd_base_2base_entry"])
    patched = _redirect_slot33_to_body(
        patched, symbols["api_slot_33"], symbols["experimental_slot_33"])
    patched = _redirect_slot03_to_link(
        patched, symbols["api_slot_03"], symbols[link_entry])
    if cd_4a == "experimental":
        patched = _redirect_slot4a_to_body(
            patched, symbols["api_slot_4A"], symbols[MEMOPEN_ENTRY])
    if cd_25 == "experimental":
        patched = _redirect_slot25_to_body(
            patched, symbols["api_slot_25"], symbols[SCRMOD_ENTRY])
    if cd_24 == "experimental":
        patched = _redirect_slot24_to_body(
            patched, symbols["api_slot_24"], symbols[DOTMOD_ENTRY])
    if cd_36 == "experimental":
        patched = _redirect_slot36_to_body(
            patched, symbols["api_slot_36"], symbols[SATCLR_ENTRY])
    if cd_35 == "experimental":
        patched = _redirect_slot35_to_body(
            patched, symbols["api_slot_35"], symbols[SPRDMA_ENTRY])
    if cd_2c == "experimental":
        patched = _redirect_slot2c_to_body(
            patched, symbols["api_slot_2C"], symbols[IRQON_ENTRY])
    slot02_addr = symbols["api_slot_02"]
    if cd_4a == "experimental" and cd_25 == "experimental" and cd_24 == "experimental" and cd_36 == "experimental" and cd_35 == "experimental" and cd_2c == "experimental":
        allowed = unified_api_windows_4a_25_24_36_35_2c(slot02_addr)
    elif cd_2c == "experimental":
        allowed = unified_api_windows_2c(slot02_addr)
    elif cd_4a == "experimental" and cd_25 == "experimental" and cd_24 == "experimental" and cd_36 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_4a_25_24_36_35(slot02_addr)
    elif cd_4a == "experimental" and cd_25 == "experimental" and cd_24 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_4a_25_24_35(slot02_addr)
    elif cd_4a == "experimental" and cd_25 == "experimental" and cd_36 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_4a_25_36_35(slot02_addr)
    elif cd_4a == "experimental" and cd_24 == "experimental" and cd_36 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_4a_24_36_35(slot02_addr)
    elif cd_25 == "experimental" and cd_24 == "experimental" and cd_36 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_25_24_36_35(slot02_addr)
    elif cd_4a == "experimental" and cd_25 == "experimental" and cd_24 == "experimental" and cd_36 == "experimental":
        allowed = unified_api_windows_4a_25_24_36(slot02_addr)
    elif cd_4a == "experimental" and cd_25 == "experimental" and cd_24 == "experimental":
        allowed = unified_api_windows_4a_25_24(slot02_addr)
    elif cd_4a == "experimental" and cd_25 == "experimental" and cd_36 == "experimental":
        allowed = unified_api_windows_4a_25_36(slot02_addr)
    elif cd_4a == "experimental" and cd_24 == "experimental" and cd_36 == "experimental":
        allowed = unified_api_windows_4a_24_36(slot02_addr)
    elif cd_25 == "experimental" and cd_24 == "experimental" and cd_36 == "experimental":
        allowed = unified_api_windows_25_24_36(slot02_addr)
    elif cd_4a == "experimental" and cd_25 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_4a_25_35(slot02_addr)
    elif cd_4a == "experimental" and cd_24 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_4a_24_35(slot02_addr)
    elif cd_4a == "experimental" and cd_36 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_4a_36_35(slot02_addr)
    elif cd_25 == "experimental" and cd_24 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_25_24_35(slot02_addr)
    elif cd_25 == "experimental" and cd_36 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_25_36_35(slot02_addr)
    elif cd_24 == "experimental" and cd_36 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_24_36_35(slot02_addr)
    elif cd_4a == "experimental" and cd_25 == "experimental":
        allowed = unified_api_windows_4a_25(slot02_addr)
    elif cd_4a == "experimental" and cd_24 == "experimental":
        allowed = unified_api_windows_4a_24(slot02_addr)
    elif cd_4a == "experimental" and cd_36 == "experimental":
        allowed = unified_api_windows_4a_36(slot02_addr)
    elif cd_4a == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_4a_35(slot02_addr)
    elif cd_25 == "experimental" and cd_24 == "experimental":
        allowed = unified_api_windows_25_24(slot02_addr)
    elif cd_25 == "experimental" and cd_36 == "experimental":
        allowed = unified_api_windows_25_36(slot02_addr)
    elif cd_25 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_25_35(slot02_addr)
    elif cd_24 == "experimental" and cd_36 == "experimental":
        allowed = unified_api_windows_24_36(slot02_addr)
    elif cd_24 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_24_35(slot02_addr)
    elif cd_36 == "experimental" and cd_35 == "experimental":
        allowed = unified_api_windows_36_35(slot02_addr)
    elif cd_4a == "experimental":
        allowed = unified_api_windows_4a(slot02_addr)
    elif cd_25 == "experimental":
        allowed = unified_api_windows_25(slot02_addr)
    elif cd_24 == "experimental":
        allowed = unified_api_windows_24(slot02_addr)
    elif cd_36 == "experimental":
        allowed = unified_api_windows_36(slot02_addr)
    elif cd_35 == "experimental":
        allowed = unified_api_windows_35(slot02_addr)
    else:
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
    slot4a_window = set(range(3 * 0x4A, 3 * 0x4A + 3))
    if cd_4a == "experimental":
        if not (set(api_diff) & slot4a_window):
            raise ValueError("G5 image is missing the $4A patch window; refusing silent no-op")
        memopen_addr = symbols[MEMOPEN_ENTRY]
        if bytes(patched[3 * 0x4A:3 * 0x4A + 3]) != \
                bytes((0x4C, memopen_addr & 0xFF, (memopen_addr >> 8) & 0xFF)):
            raise ValueError("slot-$4A window is not the expected JMP body")
    slot25_window = set(range(3 * 0x25, 3 * 0x25 + 3))
    if cd_25 == "experimental":
        if not (set(api_diff) & slot25_window):
            raise ValueError("G5 image is missing the $25 patch window; refusing silent no-op")
        scrmod_addr = symbols[SCRMOD_ENTRY]
        if bytes(patched[3 * 0x25:3 * 0x25 + 3]) != \
                bytes((0x4C, scrmod_addr & 0xFF, (scrmod_addr >> 8) & 0xFF)):
            raise ValueError("slot-$25 window is not the expected JMP body")
    slot24_window = set(range(3 * 0x24, 3 * 0x24 + 3))
    if cd_24 == "experimental":
        if not (set(api_diff) & slot24_window):
            raise ValueError("G5 image is missing the $24 patch window; refusing silent no-op")
        dotmod_addr = symbols[DOTMOD_ENTRY]
        if bytes(patched[3 * 0x24:3 * 0x24 + 3]) != \
                bytes((0x4C, dotmod_addr & 0xFF, (dotmod_addr >> 8) & 0xFF)):
            raise ValueError("slot-$24 window is not the expected JMP body")
    slot36_window = set(range(3 * 0x36, 3 * 0x36 + 3))
    if cd_36 == "experimental":
        if not (set(api_diff) & slot36_window):
            raise ValueError("G5 image is missing the $36 patch window; refusing silent no-op")
        satclr_addr = symbols[SATCLR_ENTRY]
        if bytes(patched[3 * 0x36:3 * 0x36 + 3]) != \
                bytes((0x4C, satclr_addr & 0xFF, (satclr_addr >> 8) & 0xFF)):
            raise ValueError("slot-$36 window is not the expected JMP body")
    slot35_window = set(range(3 * 0x35, 3 * 0x35 + 3))
    if cd_35 == "experimental":
        if not (set(api_diff) & slot35_window):
            raise ValueError("G5 image is missing the $35 patch window; refusing silent no-op")
        sprdma_addr = symbols[SPRDMA_ENTRY]
        if bytes(patched[3 * 0x35:3 * 0x35 + 3]) != \
                bytes((0x4C, sprdma_addr & 0xFF, (sprdma_addr >> 8) & 0xFF)):
            raise ValueError("slot-$35 window is not the expected JMP body")
    slot2c_window = set(range(3 * 0x2C, 3 * 0x2C + 3))
    if cd_2c == "experimental":
        if not (set(api_diff) & slot2c_window):
            raise ValueError("G5 image is missing the $2C patch window; refusing silent no-op")
        irqon_addr = symbols[IRQON_ENTRY]
        if bytes(patched[3 * 0x2C:3 * 0x2C + 3]) != \
                bytes((0x4C, irqon_addr & 0xFF, (irqon_addr >> 8) & 0xFF)):
            raise ValueError("slot-$2C window is not the expected JMP body")
    proto = symbols["cd_base_2base_entry"]
    if bytes(patched[slot02_addr - 0xE000:slot02_addr - 0xE000 + SLOT02_PATCH_LEN]) != \
            bytes((0x4C, proto & 0xFF, (proto >> 8) & 0xFF, 0xEA, 0xEA)):
        raise ValueError("slot-$02 window is not the expected JMP proto+NOP pad")
    slot33_body = symbols["experimental_slot_33"]
    if bytes(patched[3 * 0x33:3 * 0x33 + 3]) != \
            bytes((0x4C, slot33_body & 0xFF, (slot33_body >> 8) & 0xFF)):
        raise ValueError("slot-$33 window is not the expected JMP body")
    link_entry_addr = symbols[link_entry]
    if bytes(patched[3 * 0x03:3 * 0x03 + 3]) != \
            bytes((0x4C, link_entry_addr & 0xFF, (link_entry_addr >> 8) & 0xFF)):
        raise ValueError("slot-$03 window is not the expected JMP link")
    # $4A stays a stub unless the experimental body is selected (SIMULATED
    # control stays separate, never wired here).
    at_4a = 3 * 0x4A
    if cd_4a == "stub":
        if bytes(patched[at_4a:at_4a + 3]) != bytes(original[at_4a:at_4a + 3]):
            raise ValueError("$4A table entry changed; SIMULATED control must stay separate")
    # $25 stays a stub unless the experimental body is selected.
    at_25 = 3 * 0x25
    if cd_25 == "stub":
        if bytes(patched[at_25:at_25 + 3]) != bytes(original[at_25:at_25 + 3]):
            raise ValueError("$25 table entry changed; stub default must stay separate")
    # $24 stays a stub unless the experimental body is selected.
    at_24 = 3 * 0x24
    if cd_24 == "stub":
        if bytes(patched[at_24:at_24 + 3]) != bytes(original[at_24:at_24 + 3]):
            raise ValueError("$24 table entry changed; stub default must stay separate")
    at_36 = 3 * 0x36
    if cd_36 == "stub":
        if bytes(patched[at_36:at_36 + 3]) != bytes(original[at_36:at_36 + 3]):
            raise ValueError("$36 table entry changed; stub default must stay separate")
    at_35 = 3 * 0x35
    if cd_35 == "stub":
        if bytes(patched[at_35:at_35 + 3]) != bytes(original[at_35:at_35 + 3]):
            raise ValueError("$35 table entry changed; stub default must stay separate")
    at_2c = 3 * 0x2C
    if cd_2c == "stub":
        if bytes(patched[at_2c:at_2c + 3]) != bytes(original[at_2c:at_2c + 3]):
            raise ValueError("$2C table entry changed; stub default must stay separate")
    # MEDIUM-2: build the final image in memory and verify it BEFORE any
    # disk write. A failed verification leaves no .pce behind and never
    # touches a previous good ROM/manifest/trace. Each final file is then
    # published via temp-file + atomic replace (never truncated in place),
    # so a mid-write failure keeps the previous file intact. Writes are
    # sequential, so multi-file atomicity is NOT guaranteed (a crash between
    # files can leave a mixed set); each file is complete-or-absent.
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
    _atomic_write(rom, data)
    link_labels = {
        "api_slot_02": symbols["api_slot_02"],
        "api_slot_03": symbols["api_slot_03"],
        "api_slot_33": symbols["api_slot_33"],
        "api_slot_4A": symbols["api_slot_4A"],
        "api_slot_25": symbols["api_slot_25"],
        "api_slot_24": symbols["api_slot_24"],
        "api_slot_36": symbols["api_slot_36"],
        "api_unimplemented": symbols["api_unimplemented"],
        "cd_base_2base_entry": symbols["cd_base_2base_entry"],
        "experimental_slot_33": symbols["experimental_slot_33"],
        link_entry: symbols[link_entry],
        "experimental_selection": symbols["experimental_selection"],
        "slot_02_table_entry": 0xE000 + 3 * 0x02,
        "slot_03_table_entry": 0xE000 + 3 * 0x03,
        "slot_33_table_entry": 0xE000 + 3 * 0x33,
        "slot_4a_table_entry": 0xE000 + 3 * 0x4A,
        "slot_25_table_entry": 0xE000 + 3 * 0x25,
        "slot_24_table_entry": 0xE000 + 3 * 0x24,
        "slot_36_table_entry": 0xE000 + 3 * 0x36,
        "slot_35_table_entry": 0xE000 + 3 * 0x35,
        "slot_2c_table_entry": 0xE000 + 3 * 0x2C,
    }
    # MAJOR-1: record each experimental entry only when linked.
    if cd_4a == "experimental":
        link_labels[MEMOPEN_ENTRY] = symbols[MEMOPEN_ENTRY]
    if cd_25 == "experimental":
        link_labels[SCRMOD_ENTRY] = symbols[SCRMOD_ENTRY]
    if cd_24 == "experimental":
        link_labels[DOTMOD_ENTRY] = symbols[DOTMOD_ENTRY]
    if cd_36 == "experimental":
        link_labels[SATCLR_ENTRY] = symbols[SATCLR_ENTRY]
    if cd_35 == "experimental":
        link_labels[SPRDMA_ENTRY] = symbols[SPRDMA_ENTRY]
    if cd_2c == "experimental":
        link_labels[IRQON_ENTRY] = symbols[IRQON_ENTRY]
    manifest = g5_manifest(sel, track, mode, variant, canon_defines, data, symbols,
                           link_labels, rom.name, private_input=private_input,
                           cd_read=cd_read, cd_4a=cd_4a, cd_25=cd_25, cd_24=cd_24, cd_36=cd_36, cd_35=cd_35,
                           cd_2c=cd_2c)
    _atomic_write(output / manifest_name,
                  json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    lines = "".join(f"{name} {value:04x}\n" for name, value in symbols.items())
    _atomic_write(output / trace_name, lines.encode("ascii"))
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
