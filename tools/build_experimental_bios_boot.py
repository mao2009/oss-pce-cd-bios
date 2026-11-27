#!/usr/bin/env python3
"""Build the UNIFIED opt-in experimental 256 KiB System-Card-sized DEV image (G5 triple-link).

NOT a compatible System Card BIOS. NOT a BIOS ABI implementation. NOT release eligible.
The image has original reset code that validates a host-built selection record (derived from an
original synthetic CUE's INDEX 01), then reuses this repository's synthetic-only READ(6), RAM-copy,
and payload-handoff diagnostics. It does not parse a TOC and does not consume private ROM media.

Unified REAL processing (experimental image only; normal DEV ROM untouched):
  slot $02: api_slot_02 fallback (5 bytes: LDX #$02; JMP api_unimplemented) is
    replaced by JMP cd_base_proto_entry + NOP pad (src/cd_base_proto.s, Agent A).
  slot $33: the $E099 table entry is redirected from the linked nonreturning $33
    fallback stub to the BOOT-region experimental_slot_33 body
    (src/experimental_bios_boot.s includes src/ex_dspoff.s, Agent B).
  slot $03: the $E009 table entry is redirected from the linked $03 fallback
    stub to the BOOT-region cd_read_link_entry body (src/cd_read_link.s,
    Agent B link V1). The $03 entry uses the link's own EXPERIMENTAL input
    discipline (proto base $2400-$2402 + block $20F2-$20F4 + window
    $20F5-$20F7, MPR1=$F8, work-RAM stack, IRQs masked) and never claims the
    genuine CD_READ ABI (cd_read_api_slot_03 stays NOT_IMPLEMENTED).

Patches apply in fail-closed order (slot-$02 first, then slot-$33, then slot-$03).
The API-area diff vs the normal DEV ROM must be EXACTLY the union of the three documented
windows; any other delta fails. Both callees are linked into the experimental
BOOT region (>= $F000, file offsets >= 0x1000). NOT a genuine ABI, NOT
compatible, release_eligible=False, dev_callable=False.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .build_cdload_exec import DISC_SECTORS, EXEC_ADDRESS, EXPECTED_MPR, SECTOR_BYTES, encode_sector, parse_labels
    from .cue_analysis import analyze, user_sector
    from .environment import DEPENDENCIES, executable
    from .experimental_track_selection import assembler_defines, selection_from_cue, verify_media_identity
else:
    from build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from build_cdload_exec import DISC_SECTORS, EXEC_ADDRESS, EXPECTED_MPR, SECTOR_BYTES, encode_sector, parse_labels
    from cue_analysis import analyze, user_sector
    from environment import DEPENDENCIES, executable
    from experimental_track_selection import assembler_defines, selection_from_cue, verify_media_identity

MS_RES = 0x3F40
EXPERIMENTAL_BOOT_FLAG = "EXPERIMENTAL_BIOS_BOOT"
ROM_NAME = "experimental-dev-only-not-compatible-syscard-sized.pce"
REQUIRED = ("experimental_bios_reset", "experimental_bios_init", "experimental_bios_selection_halt", "experimental_selection", "cd_entry",
            "cd_buffer_ready", "cd_data_done", "cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt", "cd_cdb",
             "cd_payload_return", "cd_set_read_lba", "cd_entry_selected",
             "cd_base_proto_entry", "experimental_slot_33", "cd_read_link_entry")
# Experimental slot-$02 wiring (Agent A, this image only; normal ROM untouched).
# api_slot_02 fallback (5 bytes: LDX #$02; JMP api_unimplemented) is replaced by
# JMP cd_base_proto_entry + NOP pad. NOT a genuine ABI, NOT compatible,
# release_eligible=False.
SLOT02_PATCH_LEN = 5
_PROTO_PATCH_LABELS = ("api_slot_02", "api_unimplemented", "cd_base_proto_entry")
# Experimental slot-$33 wiring (Agent B, Issue #61; this image only; normal ROM
# keeps its nonreturning stub because OVERRIDES ends at $E7FB, 5 bytes below the
# $E800 probe overlay). The $E099 table entry (file offset 3*$33, table starts
# at $E000) is redirected from the linked $33 fallback stub to the BOOT-region
# body. NOT the reference ABI, release_eligible=False.
_SLOT33_TABLE = 3 * 0x33
_SLOT33_PATCH_LABELS = ("api_slot_33", "experimental_slot_33")
# Experimental slot-$03 wiring (triple-link; this image only; normal ROM keeps
# its nonreturning $03 fallback stub). The $E009 table entry (file offset
# 3*$03, table starts at $E000) is redirected from the linked $03 fallback stub
# to the BOOT-region cd_read_link_entry body (src/cd_read_link.s, Agent-B link
# V1). The wired entry keeps the link's EXPERIMENTAL input discipline (proto
# base state $2400-$2402 + block $20F2-$20F4 + window $20F5-$20F7, MPR1=$F8,
# work-RAM stack, IRQs masked by the caller); it is NOT the genuine CD_READ ABI
# (genuine slot/entry/return semantics stay UNKNOWN/BLOCKED, release_eligible
# stays False, and cd_read_api_slot_03 stays NOT_IMPLEMENTED).
_SLOT03_TABLE = 3 * 0x03
_SLOT03_PATCH_LABELS = ("api_slot_03", "cd_read_link_entry")
# Experimental slot-$4A wiring (Issue #84; this image only when selected; normal
# ROM keeps its nonreturning $4A fallback stub, and the 2-base builder default
# keeps the table on the stub). The $E0DE table entry (file offset 3*$4A,
# table starts at $E000) is redirected from the linked $4A fallback stub to the
# BOOT-region ex_memopen_entry body (src/ex_memopen.s, preservation-only
# experimental service). NOT the genuine KEY_BIOS/EX_MEMOPEN ABI (genuine
# slot/entry/return semantics stay UNKNOWN/BLOCKED, release_eligible stays
# False).
_SLOT4A_TABLE = 3 * 0x4A
_SLOT4A_PATCH_LABELS = ("api_slot_4A", "ex_memopen_entry")
# Experimental slot-$25 wiring (Issue #47 B1; this image only when selected;
# normal ROM keeps its nonreturning $25 fallback stub, and the 2-base builder
# default keeps the table on the stub). The $E06F table entry (file offset
# 3*$25, table starts at $E000) is redirected from the linked $25 fallback
# stub to the BOOT-region ex_scrmod_entry body (src/ex_scrmod.s,
# preservation-only SIMULATED scaffold). NOT the genuine EX_SCRMOD ABI
# (genuine slot/entry/return semantics stay UNKNOWN/BLOCKED, release_eligible
# stays False).
_SLOT25_TABLE = 3 * 0x25
_SLOT25_PATCH_LABELS = ("api_slot_25", "ex_scrmod_entry")
# B1 body length in bytes (src/ex_scrmod.s: PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/
# RTS). The redirect helper requires the whole span inside BOOT (start-only
# is not enough: an entry at $FFF5 with a valid prologue would otherwise
# overlap VECTORS at $FFF6). Pinned against the 9-byte structural pin in
# tests/host/test_build_g5_25_host.py and
# tests/integration/test_g5_scrmod_25.py (change together).
_SLOT25_BODY_LEN = 9
# Experimental slot-$24 wiring (Issue #46 B1-analogous; this image only when
# selected; normal ROM keeps its nonreturning $24 fallback stub, and the
# 2-base builder default keeps the table on the stub). The $E06C table entry
# (file offset 3*$24, table starts at $E000) is redirected from the linked
# $24 fallback stub to the BOOT-region ex_dotmod_entry body
# (src/ex_dotmod.s, preservation-only SIMULATED scaffold). NOT the genuine
# EX_DOTMOD ABI (genuine slot/entry/return semantics stay UNKNOWN/BLOCKED,
# release_eligible stays False).
_SLOT24_TABLE = 3 * 0x24
_SLOT24_PATCH_LABELS = ("api_slot_24", "ex_dotmod_entry")
# B1-analogous body length in bytes (src/ex_dotmod.s:
# PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/RTS). Same BOOT-span discipline as $25.
# Pinned against the 9-byte structural pin in
# tests/host/test_build_g5_24_host.py and
# tests/integration/test_g5_dotmod_24.py (change together).
_SLOT24_BODY_LEN = 9
# Experimental slot-$36 wiring (Issue #64 B1-analogous; this image only when
# selected; normal ROM keeps its nonreturning $36 fallback stub, and the
# 2-base builder default keeps the table on the stub). The $E0A2 table entry
# (file offset 3*$36, table starts at $E000) is redirected from the linked
# $36 fallback stub to the BOOT-region ex_satclr_entry body
# (src/ex_satclr.s, preservation-only SIMULATED scaffold). NOT the genuine
# EX_SATCLR ABI (genuine slot/entry/return semantics stay UNKNOWN/BLOCKED,
# release_eligible stays False).
_SLOT36_TABLE = 3 * 0x36
_SLOT36_PATCH_LABELS = ("api_slot_36", "ex_satclr_entry")
# B1-analogous body length in bytes (src/ex_satclr.s:
# PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/RTS). Same BOOT-span discipline as $25.
# Pinned against the 9-byte structural pin in
# tests/host/test_build_g5_36_host.py and
# tests/integration/test_g5_satclr_36.py (change together).
_SLOT36_BODY_LEN = 9
# Experimental slot-$35 wiring (Issue #63 B1-analogous; this image only when
# selected; normal ROM keeps its nonreturning $35 fallback stub, and the
# 2-base builder default keeps the table on the stub). The $E09F table entry
# (file offset 3*$35, table starts at $E000) is redirected from the linked
# $35 fallback stub to the BOOT-region ex_sprdma_entry body
# (src/ex_sprdma.s, preservation-only SIMULATED scaffold). NOT the genuine
# EX_SPRDMA ABI (genuine slot/entry/return semantics stay UNKNOWN/BLOCKED,
# release_eligible stays False).
_SLOT35_TABLE = 3 * 0x35
_SLOT35_PATCH_LABELS = ("api_slot_35", "ex_sprdma_entry")
# B1-analogous body length in bytes (src/ex_sprdma.s:
# PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/RTS). Same BOOT-span discipline as $25.
# Pinned against the 9-byte structural pin in
# tests/host/test_build_g5_35_host.py and
# tests/integration/test_g5_sprdma_35.py (change together).
_SLOT35_BODY_LEN = 9
# Experimental slot-$2C wiring (Issue #54 D2 limited-functional; this image
# only when selected; normal ROM keeps its nonreturning $2C fallback stub, and
# the 2-base builder default keeps the table on the stub). The $E084 table entry
# (file offset 3*$2C, table starts at $E000) is redirected from the linked
# $2C fallback stub to the BOOT-region ex_irqon_entry body (src/ex_irqon.s,
# CR R05 bit3 set via the shared $2502 shadow). NOT the genuine EX_IRQON ABI
# (genuine slot/entry/return semantics stay UNKNOWN/BLOCKED, $20F3/$20F7
# mirrors + CPU I + IDR stay untouched, release_eligible stays False).
_SLOT2C_TABLE = 3 * 0x2C
_SLOT2C_PATCH_LABELS = ("api_slot_2C", "ex_irqon_entry")
# D2-functional body length in bytes (src/ex_irqon.s: PHP/PHA/LDA/CMP/BEQ/
# PLA/PLP/SEC/RTS reject path + SEI/TMA/PHA/LDA/TAM/LDA/STA/LDA/ORA/STA/STA/
# PLA/TAM/PLA/PLP/CLC/RTS ok path = 44 bytes). Same BOOT-span discipline as
# $25 (entry + len must end at or before $FFF6). Pinned against the 44-byte
# structural pin in tests/host/test_build_g5_2c_host.py and
# tests/integration/test_g5_irqon_2c.py (change together).
_SLOT2C_BODY_LEN = 44
# Experimental slot-$2B wiring (Issue #53 D2 limited-functional; this image
# only when selected; normal ROM keeps its nonreturning $2B fallback stub, and
# the 2-base builder default keeps the table on the stub). The $E081 table entry
# (file offset 3*$2B, table starts at $E000) is redirected from the linked
# $2B fallback stub to the BOOT-region ex_rcroff_entry body (src/ex_rcroff.s,
# CR R05 bit2 clear via the shared $2502 shadow, bit3 preserved). NOT the
# genuine EX_RCROFF ABI (genuine slot/entry/return semantics stay
# UNKNOWN/BLOCKED, $20F3/$20F7 mirrors + CPU I + IDR stay untouched,
# release_eligible stays False).
_SLOT2B_TABLE = 3 * 0x2B
_SLOT2B_PATCH_LABELS = ("api_slot_2B", "ex_rcroff_entry")
# D2-functional body length in bytes (src/ex_rcroff.s: PHP/PHA/LDA/CMP/BEQ/
# PLA/PLP/SEC/RTS reject path + SEI/TMA/PHA/LDA/TAM/LDA/STA/LDA/AND/STA/STA/
# PLA/TAM/PLA/PLP/CLC/RTS ok path = 44 bytes). Same BOOT-span discipline as
# $2C (entry + len must end at or before $FFF6). Pinned against the 44-byte
# structural pin in tests/host/test_build_g5_2b_host.py and
# tests/integration/test_g5_rcroff_2b.py (change together).
_SLOT2B_BODY_LEN = 44
# Merged input branches whose heads are recorded (never hardcoded) in the manifest.
_MERGED_REFS = ("origin/agent-a/cd-base-proto-g5", "origin/agent-b/ex-dspoff-real-cpu-g5",
                "origin/agent-b/cd-read-link-g5")
PAYLOAD_LABELS = ("payload_entry", "payload_done")


def parse_needed_labels(path: Path, names) -> dict:
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        if name in names:
            value = int(address, 16)
            if found.setdefault(name, value) != value:
                raise ValueError(f"contradictory addresses for label {name}")
    return found


def _experimental_boot_units(selected):
    """Link units for an already-derived record; kept private to this builder."""
    return [
        (ROOT / "src/experimental_bios_boot.s", assembler_defines(selected)),
        (ROOT / "src/cd_load_exec_probe.s", ["-D", EXPERIMENTAL_BOOT_FLAG]),
        (ROOT / "src/cd_base_proto.s", []),
        (ROOT / "src/cd_read_link.s", []),
    ]


def _patch_slot02_to_proto(bank: bytes, slot02_addr: int, unimpl_addr: int, proto_addr: int) -> bytes:
    """Return bank with api_slot_02 fallback replaced by JMP proto+NOPs. Fail-closed."""
    for name, addr in (("api_slot_02", slot02_addr), ("api_unimplemented", unimpl_addr),
                        ("cd_base_proto_entry", proto_addr)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot02_addr <= 0xFFF6 - SLOT02_PATCH_LEN:
        raise ValueError(f"api_slot_02 at ${slot02_addr:04X} outside FALLBACK/code area")
    if not 0xE100 <= proto_addr <= 0xFFF6:
        raise ValueError(f"cd_base_proto_entry at ${proto_addr:04X} outside bank0 body")
    off = slot02_addr - 0xE000
    if off + SLOT02_PATCH_LEN > len(bank):
        raise ValueError("api_slot_02 offset outside bank")
    before = bytes((0xA2, 0x02, 0x4C, unimpl_addr & 0xFF, (unimpl_addr >> 8) & 0xFF))
    if bytes(bank[off:off + SLOT02_PATCH_LEN]) != before:
        raise ValueError(f"pre-patch bytes at ${slot02_addr:04X} are not the slot-02 fallback sequence")
    after = bytes((0x4C, proto_addr & 0xFF, (proto_addr >> 8) & 0xFF, 0xEA, 0xEA))
    patched = bytearray(bank)
    patched[off:off + SLOT02_PATCH_LEN] = after
    return bytes(patched)


def _redirect_slot33_to_body(bank: bytes, slot33_stub: int, slot33_body: int) -> bytes:
    """Return bank with the $E099 table entry redirected from stub to body. Fail-closed.

    The table must still hold the linked fallback stub (nothing else may be
    redirected), and the body must sit in BOOT and start with the fragment's
    PHP/PHA prologue (guards against silent body drift).
    """
    for name, addr in (("api_slot_33", slot33_stub), ("experimental_slot_33", slot33_body)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot33_stub < 0xFFF6:
        raise ValueError(f"slot $33 fallback stub ${slot33_stub:04X} outside bank0 body")
    if not 0xF000 <= slot33_body < 0xFFF6:
        raise ValueError(f"experimental slot $33 body ${slot33_body:04X} outside the BOOT region")
    out = bytearray(bank)
    want_stub = bytes((0x4C, slot33_stub & 0xFF, slot33_stub >> 8))
    if bytes(out[_SLOT33_TABLE:_SLOT33_TABLE + 3]) != want_stub:
        raise ValueError("slot $33 table entry is not the linked fallback stub; "
                         "refusing to redirect an unexpected table")
    out[_SLOT33_TABLE:_SLOT33_TABLE + 3] = bytes((0x4C, slot33_body & 0xFF, slot33_body >> 8))
    body_off = slot33_body - 0xE000
    if bytes(out[body_off:body_off + 2]) != bytes((0x08, 0x48)):  # PHP, PHA
        raise ValueError("experimental slot $33 body does not start with the fragment prologue")
    return bytes(out)


def _redirect_slot03_to_link(bank: bytes, slot03_stub: int, link_entry: int) -> bytes:
    """Return bank with the $E009 table entry redirected from stub to link. Fail-closed.

    The table must still hold the linked fallback stub (nothing else may be
    redirected), and the link body must sit in BOOT and start with the link's
    PHP/PHA prologue (guards against silent body drift). The wired entry keeps
    the link's EXPERIMENTAL contract only; it never claims the genuine $03 ABI.
    """
    for name, addr in (("api_slot_03", slot03_stub), ("cd_read_link_entry", link_entry)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot03_stub < 0xFFF6:
        raise ValueError(f"slot $03 fallback stub ${slot03_stub:04X} outside bank0 body")
    if not 0xF000 <= link_entry < 0xFFF6:
        raise ValueError(f"experimental slot $03 link ${link_entry:04X} outside the BOOT region")
    out = bytearray(bank)
    want_stub = bytes((0x4C, slot03_stub & 0xFF, slot03_stub >> 8))
    if bytes(out[_SLOT03_TABLE:_SLOT03_TABLE + 3]) != want_stub:
        raise ValueError("slot $03 table entry is not the linked fallback stub; "
                         "refusing to redirect an unexpected table")
    out[_SLOT03_TABLE:_SLOT03_TABLE + 3] = bytes((0x4C, link_entry & 0xFF, link_entry >> 8))
    body_off = link_entry - 0xE000
    if bytes(out[body_off:body_off + 2]) != bytes((0x08, 0x48)):  # PHP, PHA
        raise ValueError("experimental slot $03 link does not start with the link prologue")
    return bytes(out)


def _redirect_slot4a_to_body(bank: bytes, slot4a_stub: int, memopen_entry: int) -> bytes:
    """Return bank with the $E0DE table entry redirected from stub to body. Fail-closed.

    The table must still hold the linked fallback stub (nothing else may be
    redirected), and the body must sit in BOOT and start with the body's
    PHP/PHA prologue (guards against silent body drift). The wired entry keeps
    the EXPERIMENTAL preservation contract only; it never claims the genuine
    $4A ABI (genuine KEY_BIOS/EX_MEMOPEN semantics stay UNKNOWN/BLOCKED).
    """
    for name, addr in (("api_slot_4A", slot4a_stub), ("ex_memopen_entry", memopen_entry)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot4a_stub < 0xFFF6:
        raise ValueError(f"slot $4A fallback stub ${slot4a_stub:04X} outside bank0 body")
    if not 0xF000 <= memopen_entry < 0xFFF6:
        raise ValueError(f"experimental slot $4A body ${memopen_entry:04X} outside the BOOT region")
    out = bytearray(bank)
    want_stub = bytes((0x4C, slot4a_stub & 0xFF, slot4a_stub >> 8))
    if bytes(out[_SLOT4A_TABLE:_SLOT4A_TABLE + 3]) != want_stub:
        raise ValueError("slot $4A table entry is not the linked fallback stub; "
                         "refusing to redirect an unexpected table")
    out[_SLOT4A_TABLE:_SLOT4A_TABLE + 3] = bytes((0x4C, memopen_entry & 0xFF, memopen_entry >> 8))
    body_off = memopen_entry - 0xE000
    if bytes(out[body_off:body_off + 2]) != bytes((0x08, 0x48)):  # PHP, PHA
        raise ValueError("experimental slot $4A body does not start with the body prologue")
    return bytes(out)


def _redirect_slot25_to_body(bank: bytes, slot25_stub: int, scrmod_entry: int) -> bytes:
    """Return bank with the $E06F table entry redirected from stub to body. Fail-closed.

    The table must still hold the linked fallback stub (nothing else may be
    redirected), and the body must sit in BOOT and start with the body's
    PHP/PHA prologue (guards against silent body drift). The wired entry keeps
    the EXPERIMENTAL B1 preservation contract only (SIMULATED scaffold); it
    never claims the genuine $25 ABI (genuine EX_SCRMOD semantics stay
    UNKNOWN/BLOCKED, T04/T08/T09 NOT_APPLICABLE: no I/O window, no shadow,
    no mask).
    """
    for name, addr in (("api_slot_25", slot25_stub), ("ex_scrmod_entry", scrmod_entry)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot25_stub < 0xFFF6:
        raise ValueError(f"slot $25 fallback stub ${slot25_stub:04X} outside bank0 body")
    if not 0xF000 <= scrmod_entry <= 0xFFF6 - _SLOT25_BODY_LEN:
        raise ValueError(f"experimental slot $25 body ${scrmod_entry:04X} outside the BOOT region "
                         f"(9-byte span must end at or before $FFF6)")
    out = bytearray(bank)
    want_stub = bytes((0x4C, slot25_stub & 0xFF, slot25_stub >> 8))
    if bytes(out[_SLOT25_TABLE:_SLOT25_TABLE + 3]) != want_stub:
        raise ValueError("slot $25 table entry is not the linked fallback stub; "
                         "refusing to redirect an unexpected table")
    out[_SLOT25_TABLE:_SLOT25_TABLE + 3] = bytes((0x4C, scrmod_entry & 0xFF, scrmod_entry >> 8))
    body_off = scrmod_entry - 0xE000
    if bytes(out[body_off:body_off + 2]) != bytes((0x08, 0x48)):  # PHP, PHA
        raise ValueError("experimental slot $25 body does not start with the body prologue")
    return bytes(out)


def _redirect_slot24_to_body(bank: bytes, slot24_stub: int, dotmod_entry: int) -> bytes:
    """Return bank with the $E06C table entry redirected from stub to body. Fail-closed.

    The table must still hold the linked fallback stub (nothing else may be
    redirected), and the body must sit in BOOT and start with the body's
    PHP/PHA prologue (guards against silent body drift). The wired entry keeps
    the EXPERIMENTAL B1-analogous preservation contract only (SIMULATED
    scaffold); it never claims the genuine $24 ABI (genuine EX_DOTMOD
    semantics stay UNKNOWN/BLOCKED, T04/T08/T09 NOT_APPLICABLE: no I/O window,
    no shadow, no mask).
    """
    for name, addr in (("api_slot_24", slot24_stub), ("ex_dotmod_entry", dotmod_entry)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot24_stub < 0xFFF6:
        raise ValueError(f"slot $24 fallback stub ${slot24_stub:04X} outside bank0 body")
    if not 0xF000 <= dotmod_entry <= 0xFFF6 - _SLOT24_BODY_LEN:
        raise ValueError(f"experimental slot $24 body ${dotmod_entry:04X} outside the BOOT region "
                         f"(9-byte span must end at or before $FFF6)")
    out = bytearray(bank)
    want_stub = bytes((0x4C, slot24_stub & 0xFF, slot24_stub >> 8))
    if bytes(out[_SLOT24_TABLE:_SLOT24_TABLE + 3]) != want_stub:
        raise ValueError("slot $24 table entry is not the linked fallback stub; "
                         "refusing to redirect an unexpected table")
    out[_SLOT24_TABLE:_SLOT24_TABLE + 3] = bytes((0x4C, dotmod_entry & 0xFF, dotmod_entry >> 8))
    body_off = dotmod_entry - 0xE000
    if bytes(out[body_off:body_off + 2]) != bytes((0x08, 0x48)):  # PHP, PHA
        raise ValueError("experimental slot $24 body does not start with the body prologue")
    return bytes(out)


def _redirect_slot36_to_body(bank: bytes, slot36_stub: int, satclr_entry: int) -> bytes:
    """Return bank with the $E0A2 table entry redirected from stub to body. Fail-closed.

    The table must still hold the linked fallback stub (nothing else may be
    redirected), and the body must sit in BOOT and start with the body's
    PHP/PHA prologue (guards against silent body drift). The wired entry keeps
    the EXPERIMENTAL B1-analogous preservation contract only (SIMULATED
    scaffold); it never claims the genuine $36 ABI (genuine EX_SATCLR
    semantics stay UNKNOWN/BLOCKED, T04/T08/T09 NOT_APPLICABLE: no I/O window,
    no shadow, no mask).
    """
    for name, addr in (("api_slot_36", slot36_stub), ("ex_satclr_entry", satclr_entry)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot36_stub < 0xFFF6:
        raise ValueError(f"slot $36 fallback stub ${slot36_stub:04X} outside bank0 body")
    if not 0xF000 <= satclr_entry <= 0xFFF6 - _SLOT36_BODY_LEN:
        raise ValueError(f"experimental slot $36 body ${satclr_entry:04X} outside the BOOT region "
                         f"(9-byte span must end at or before $FFF6)")
    out = bytearray(bank)
    want_stub = bytes((0x4C, slot36_stub & 0xFF, slot36_stub >> 8))
    if bytes(out[_SLOT36_TABLE:_SLOT36_TABLE + 3]) != want_stub:
        raise ValueError("slot $36 table entry is not the linked fallback stub; "
                         "refusing to redirect an unexpected table")
    out[_SLOT36_TABLE:_SLOT36_TABLE + 3] = bytes((0x4C, satclr_entry & 0xFF, satclr_entry >> 8))
    body_off = satclr_entry - 0xE000
    if bytes(out[body_off:body_off + 2]) != bytes((0x08, 0x48)):  # PHP, PHA
        raise ValueError("experimental slot $36 body does not start with the body prologue")
    return bytes(out)


def _redirect_slot35_to_body(bank: bytes, slot35_stub: int, sprdma_entry: int) -> bytes:
    """Return bank with the $E09F table entry redirected from stub to body. Fail-closed.

    The table must still hold the linked fallback stub (nothing else may be
    redirected), and the body must sit in BOOT and start with the body's
    PHP/PHA prologue (guards against silent body drift). The wired entry keeps
    the EXPERIMENTAL B1-analogous preservation contract only (SIMULATED
    scaffold); it never claims the genuine $35 ABI (genuine EX_SPRDMA
    semantics stay UNKNOWN/BLOCKED, T04/T08/T09 NOT_APPLICABLE: no I/O window,
    no shadow, no mask).
    """
    for name, addr in (("api_slot_35", slot35_stub), ("ex_sprdma_entry", sprdma_entry)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot35_stub < 0xFFF6:
        raise ValueError(f"slot $35 fallback stub ${slot35_stub:04X} outside bank0 body")
    if not 0xF000 <= sprdma_entry <= 0xFFF6 - _SLOT35_BODY_LEN:
        raise ValueError(f"experimental slot $35 body ${sprdma_entry:04X} outside the BOOT region "
                         f"(9-byte span must end at or before $FFF6)")
    out = bytearray(bank)
    want_stub = bytes((0x4C, slot35_stub & 0xFF, slot35_stub >> 8))
    if bytes(out[_SLOT35_TABLE:_SLOT35_TABLE + 3]) != want_stub:
        raise ValueError("slot $35 table entry is not the linked fallback stub; "
                         "refusing to redirect an unexpected table")
    out[_SLOT35_TABLE:_SLOT35_TABLE + 3] = bytes((0x4C, sprdma_entry & 0xFF, sprdma_entry >> 8))
    body_off = sprdma_entry - 0xE000
    if bytes(out[body_off:body_off + 2]) != bytes((0x08, 0x48)):  # PHP, PHA
        raise ValueError("experimental slot $35 body does not start with the body prologue")
    return bytes(out)


def _redirect_slot2c_to_body(bank: bytes, slot2c_stub: int, irqon_entry: int) -> bytes:
    """Return bank with the $E084 table entry redirected from stub to body. Fail-closed.

    The table must still hold the linked fallback stub (nothing else may be
    redirected), and the body must sit in BOOT and start with the body's
    PHP/PHA prologue (guards against silent body drift). The wired entry keeps
    the EXPERIMENTAL D2 limited-functional contract only (CR bit3 set via the
    shared $2502 shadow, $20F3/$20F7 + CPU I + IDR untouched); it never claims
    the genuine $2C ABI (genuine EX_IRQON semantics stay UNKNOWN/BLOCKED).
    """
    for name, addr in (("api_slot_2C", slot2c_stub), ("ex_irqon_entry", irqon_entry)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot2c_stub < 0xFFF6:
        raise ValueError(f"slot $2C fallback stub ${slot2c_stub:04X} outside bank0 body")
    if not 0xF000 <= irqon_entry <= 0xFFF6 - _SLOT2C_BODY_LEN:
        raise ValueError(f"experimental slot $2C body ${irqon_entry:04X} outside the BOOT region "
                         f"(44-byte span must end at or before $FFF6)")
    out = bytearray(bank)
    want_stub = bytes((0x4C, slot2c_stub & 0xFF, slot2c_stub >> 8))
    if bytes(out[_SLOT2C_TABLE:_SLOT2C_TABLE + 3]) != want_stub:
        raise ValueError("slot $2C table entry is not the linked fallback stub; "
                         "refusing to redirect an unexpected table")
    out[_SLOT2C_TABLE:_SLOT2C_TABLE + 3] = bytes((0x4C, irqon_entry & 0xFF, irqon_entry >> 8))
    body_off = irqon_entry - 0xE000
    if bytes(out[body_off:body_off + 2]) != bytes((0x08, 0x48)):  # PHP, PHA
        raise ValueError("experimental slot $2C body does not start with the body prologue")
    return bytes(out)


def _redirect_slot2b_to_body(bank: bytes, slot2b_stub: int, rcroff_entry: int) -> bytes:
    """Return bank with the $E081 table entry redirected from stub to body. Fail-closed.

    The table must still hold the linked fallback stub (nothing else may be
    redirected), and the body must sit in BOOT and start with the body's
    PHP/PHA prologue (guards against silent body drift). The wired entry keeps
    the EXPERIMENTAL D2 limited-functional contract only (CR bit2 clear via
    the shared $2502 shadow, bit3 preserved, $20F3/$20F7 + CPU I + IDR
    untouched, RCR R06 untouched); it never claims the genuine $2B ABI
    (genuine EX_RCROFF semantics stay UNKNOWN/BLOCKED).
    """
    for name, addr in (("api_slot_2B", slot2b_stub), ("ex_rcroff_entry", rcroff_entry)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot2b_stub < 0xFFF6:
        raise ValueError(f"slot $2B fallback stub ${slot2b_stub:04X} outside bank0 body")
    if not 0xF000 <= rcroff_entry <= 0xFFF6 - _SLOT2B_BODY_LEN:
        raise ValueError(f"experimental slot $2B body ${rcroff_entry:04X} outside the BOOT region "
                         f"(44-byte span must end at or before $FFF6)")
    out = bytearray(bank)
    want_stub = bytes((0x4C, slot2b_stub & 0xFF, slot2b_stub >> 8))
    if bytes(out[_SLOT2B_TABLE:_SLOT2B_TABLE + 3]) != want_stub:
        raise ValueError("slot $2B table entry is not the linked fallback stub; "
                         "refusing to redirect an unexpected table")
    out[_SLOT2B_TABLE:_SLOT2B_TABLE + 3] = bytes((0x4C, rcroff_entry & 0xFF, rcroff_entry >> 8))
    body_off = rcroff_entry - 0xE000
    if bytes(out[body_off:body_off + 2]) != bytes((0x08, 0x48)):  # PHP, PHA
        raise ValueError("experimental slot $2B body does not start with the body prologue")
    return bytes(out)


def unified_api_windows(slot02_addr: int) -> set:
    """File offsets in the first 4 KiB allowed to differ from the normal DEV ROM.

    Exactly the slot-$02 5-byte fallback window plus the slot-$33 3-byte table
    redirect plus the slot-$03 3-byte table redirect. Pure and fail-closed:
    bad addresses or overlapping windows fail.
    """
    if type(slot02_addr) is not int or not 0xE100 <= slot02_addr <= 0xFFF6 - SLOT02_PATCH_LEN:
        raise ValueError(f"api_slot_02 at {slot02_addr!r} outside FALLBACK/code area")
    windows = set(range(slot02_addr - 0xE000, slot02_addr - 0xE000 + SLOT02_PATCH_LEN))
    windows |= set(range(_SLOT33_TABLE, _SLOT33_TABLE + 3))
    windows |= set(range(_SLOT03_TABLE, _SLOT03_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a(slot02_addr: int) -> set:
    """File offsets allowed to differ when the experimental $4A body is wired.

    Base three-window set plus the slot-$4A 3-byte table redirect. Pure and
    fail-closed like the base set; the default unified_api_windows() is
    untouched so existing stub-default images keep their exact contract.
    """
    windows = set(unified_api_windows(slot02_addr))
    windows |= set(range(_SLOT4A_TABLE, _SLOT4A_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_25(slot02_addr: int) -> set:
    """File offsets allowed to differ when the experimental $25 body is wired.

    Base three-window set plus the slot-$25 3-byte table redirect. Pure and
    fail-closed like the base set; the default unified_api_windows() is
    untouched so existing stub-default images keep their exact contract.
    """
    windows = set(unified_api_windows(slot02_addr))
    windows |= set(range(_SLOT25_TABLE, _SLOT25_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$25 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25(slot02_addr: int) -> set:
    """File offsets allowed to differ when both experimental bodies are wired.

    Base three-window set plus the slot-$4A and slot-$25 3-byte table
    redirects. Pure and fail-closed like the base set.
    """
    windows = set(unified_api_windows_4a(slot02_addr))
    windows |= set(range(_SLOT25_TABLE, _SLOT25_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_24(slot02_addr: int) -> set:
    """File offsets allowed to differ when the experimental $24 body is wired.

    Base three-window set plus the slot-$24 3-byte table redirect. Pure and
    fail-closed like the base set; the default unified_api_windows() is
    untouched so existing stub-default images keep their exact contract.
    """
    windows = set(unified_api_windows(slot02_addr))
    windows |= set(range(_SLOT24_TABLE, _SLOT24_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$24 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_24(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A and $24 bodies are wired."""
    windows = set(unified_api_windows_4a(slot02_addr))
    windows |= set(range(_SLOT24_TABLE, _SLOT24_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$24 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_25_24(slot02_addr: int) -> set:
    """File offsets allowed to differ when $25 and $24 bodies are wired."""
    windows = set(unified_api_windows_25(slot02_addr))
    windows |= set(range(_SLOT24_TABLE, _SLOT24_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$25/slot-$24 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25_24(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $25 and $24 are wired."""
    windows = set(unified_api_windows_4a_25(slot02_addr))
    windows |= set(range(_SLOT24_TABLE, _SLOT24_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25/slot-$24 patch windows overlap; refusing ambiguous wiring")
    return windows

def unified_api_windows_36(slot02_addr: int) -> set:
    """File offsets allowed to differ when the experimental $36 body is wired."""
    windows = set(unified_api_windows(slot02_addr))
    windows |= set(range(_SLOT36_TABLE, _SLOT36_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$36 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_36(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A and $36 bodies are wired."""
    windows = set(unified_api_windows_4a(slot02_addr))
    windows |= set(range(_SLOT36_TABLE, _SLOT36_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$36 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_25_36(slot02_addr: int) -> set:
    """File offsets allowed to differ when $25 and $36 bodies are wired."""
    windows = set(unified_api_windows_25(slot02_addr))
    windows |= set(range(_SLOT36_TABLE, _SLOT36_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$25/slot-$36 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25_36(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $25 and $36 are wired."""
    windows = set(unified_api_windows_4a_25(slot02_addr))
    windows |= set(range(_SLOT36_TABLE, _SLOT36_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25/slot-$36 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_24_36(slot02_addr: int) -> set:
    """File offsets allowed to differ when $24 and $36 bodies are wired."""
    windows = set(unified_api_windows_24(slot02_addr))
    windows |= set(range(_SLOT36_TABLE, _SLOT36_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$24/slot-$36 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_24_36(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $24 and $36 bodies are wired."""
    windows = set(unified_api_windows_4a_24(slot02_addr))
    windows |= set(range(_SLOT36_TABLE, _SLOT36_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$24/slot-$36 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_25_24_36(slot02_addr: int) -> set:
    """File offsets allowed to differ when $25, $24 and $36 bodies are wired."""
    windows = set(unified_api_windows_25_24(slot02_addr))
    windows |= set(range(_SLOT36_TABLE, _SLOT36_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$25/slot-$24/slot-$36 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25_24_36(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $25, $24 and $36 are wired."""
    windows = set(unified_api_windows_4a_25_24(slot02_addr))
    windows |= set(range(_SLOT36_TABLE, _SLOT36_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25/slot-$24/slot-$36 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when the experimental $35 body is wired."""
    windows = set(unified_api_windows(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A and $35 bodies are wired."""
    windows = set(unified_api_windows_4a(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_25_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $25 and $35 bodies are wired."""
    windows = set(unified_api_windows_25(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$25/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $25 and $35 are wired."""
    windows = set(unified_api_windows_4a_25(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_24_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $24 and $35 bodies are wired."""
    windows = set(unified_api_windows_24(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$24/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_24_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $24 and $35 bodies are wired."""
    windows = set(unified_api_windows_4a_24(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$24/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_25_24_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $25, $24 and $35 bodies are wired."""
    windows = set(unified_api_windows_25_24(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$25/slot-$24/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25_24_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $25, $24 and $35 are wired."""
    windows = set(unified_api_windows_4a_25_24(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25/slot-$24/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_36_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $36 and $35 bodies are wired."""
    windows = set(unified_api_windows_36(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$36/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_36_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $36 and $35 bodies are wired."""
    windows = set(unified_api_windows_4a_36(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$36/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_25_36_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $25, $36 and $35 bodies are wired."""
    windows = set(unified_api_windows_25_36(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$25/slot-$36/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25_36_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $25, $36 and $35 are wired."""
    windows = set(unified_api_windows_4a_25_36(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25/slot-$36/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_24_36_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $24, $36 and $35 bodies are wired."""
    windows = set(unified_api_windows_24_36(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$24/slot-$36/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_24_36_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $24, $36 and $35 bodies are wired."""
    windows = set(unified_api_windows_4a_24_36(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$24/slot-$36/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_25_24_36_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $25, $24, $36 and $35 bodies are wired."""
    windows = set(unified_api_windows_25_24_36(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$25/slot-$24/slot-$36/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25_24_36_35(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $25, $24, $36 and $35 are wired."""
    windows = set(unified_api_windows_4a_25_24_36(slot02_addr))
    windows |= set(range(_SLOT35_TABLE, _SLOT35_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25/slot-$24/slot-$36/slot-$35 patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_2c(slot02_addr: int) -> set:
    """File offsets allowed to differ when the experimental $2C body is wired.

    Base three-window set plus the slot-$2C 3-byte table redirect. Pure and
    fail-closed like the base set; the default unified_api_windows() is
    untouched so existing stub-default images keep their exact contract.
    """
    windows = set(unified_api_windows(slot02_addr))
    windows |= set(range(_SLOT2C_TABLE, _SLOT2C_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$2C patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25_24_36_35_2c(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $25, $24, $36, $35 and $2C are wired."""
    windows = set(unified_api_windows_4a_25_24_36_35(slot02_addr))
    windows |= set(range(_SLOT2C_TABLE, _SLOT2C_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25/slot-$24/slot-$36/slot-$35/slot-$2C patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_2b(slot02_addr: int) -> set:
    """File offsets allowed to differ when the experimental $2B body is wired.

    Base three-window set plus the slot-$2B 3-byte table redirect. Pure and
    fail-closed like the base set; the default unified_api_windows() is
    untouched so existing stub-default images keep their exact contract.
    """
    windows = set(unified_api_windows(slot02_addr))
    windows |= set(range(_SLOT2B_TABLE, _SLOT2B_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$2B patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_4a_25_24_36_35_2c_2b(slot02_addr: int) -> set:
    """File offsets allowed to differ when $4A, $25, $24, $36, $35, $2C and $2B are wired (full ExpG)."""
    windows = set(unified_api_windows_4a_25_24_36_35_2c(slot02_addr))
    windows |= set(range(_SLOT2B_TABLE, _SLOT2B_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3 + 3 + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$4A/slot-$25/slot-$24/slot-$36/slot-$35/slot-$2C/slot-$2B patch windows overlap; refusing ambiguous wiring")
    return windows


def unified_api_windows_2c_2b(slot02_addr: int) -> set:
    """File offsets allowed to differ when $2C and $2B are wired ($2C->$2B chain)."""
    windows = set(unified_api_windows_2c(slot02_addr))
    windows |= set(range(_SLOT2B_TABLE, _SLOT2B_TABLE + 3))
    if len(windows) != SLOT02_PATCH_LEN + 3 + 3 + 3 + 3:
        raise ValueError("slot-$02/slot-$33/slot-$03/slot-$2C/slot-$2B patch windows overlap; refusing ambiguous wiring")
    return windows


def _git_output(*args: str) -> str:
    """Run a read-only git command against this repository. Fail-closed."""
    try:
        # errors="replace": commit subjects may carry non-locale bytes;
        # undecodable bytes must never crash the builder (e.g. cp932 hosts).
        # Strict downstream checks (40-hex SHAs, exact matches) stay fail-closed.
        result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                                text=True, errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"BLOCKED: cannot query git {' '.join(args)}: {error}") from error
    if result.returncode != 0:
        raise ValueError(f"BLOCKED: git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _discover_merge_commit(short_ref: str) -> str | None:
    """Find the single merge commit that integrated short_ref (branch deleted post-merge).

    Pure derivation from main history (never hardcoded, never guessed): the
    merge subject must match the GitHub PR-merge shape
    ("Merge pull request #N from <owner>/<short>") or the union sub-merge shape
    ("Merge remote-tracking branch 'origin/<short>' into ..."). Zero or
    multiple matches -> None (UNRESOLVED downstream, never a guess).

    Subject match alone never proves integration: the caller must verify the
    merge commit's parent relationship and ancestry before recording any
    integrated HEAD (see unified_repo_provenance).
    """
    try:
        out = _git_output("log", "--format=%H %s", "--merges")
    except ValueError:
        return None
    short = re.escape(short_ref)
    patterns = (re.compile(rf"^Merge pull request #\d+ from \S+/{short}$"),
                re.compile(rf"^Merge remote-tracking branch 'origin/{short}' into \S+$"))
    found = []
    for line in out.splitlines():
        if len(line) < 42 or line[40] != " ":
            continue
        sha, _, subject = line.partition(" ")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        if any(pattern.match(subject) for pattern in patterns):
            found.append(sha)
    if len(found) != 1:
        return None
    return found[0]


def _merge_second_parent(merge_sha: str) -> str | None:
    """Return the integrated branch HEAD (second parent) of a merge commit.

    Pure derivation (never guessed): `git rev-list --parents -n 1 <merge>`
    must yield exactly `<merge> <parent1> <parent2>`; the second parent is
    the integrated branch HEAD for standard 2-parent GitHub/union merges.
    Any other shape (non-merge, octopus, shallow-missing parents, git
    error) -> None (UNRESOLVED downstream, never a guess).
    """
    if not re.fullmatch(r"[0-9a-f]{40}", merge_sha or ""):
        return None
    try:
        out = _git_output("rev-list", "--parents", "-n", "1", merge_sha)
    except ValueError:
        return None
    parts = out.strip().split()
    if len(parts) != 3 or parts[0] != merge_sha:
        return None
    second = parts[2]
    if not re.fullmatch(r"[0-9a-f]{40}", second):
        return None
    return second


def unified_repo_provenance() -> dict:
    """Derive G5-native manifest provenance at build time; never hardcoded or guessed.

    branch: current branch (detached-HEAD fallback names the HEAD instead).
    head: full 40-hex HEAD that built this image.
    merged_heads: the confirmed integrated branch HEAD for each merged input
      (rev-parse when the ref is present; the merge commit's second parent
      when the ref is absent but a single integrating merge is discoverable;
      UNRESOLVED when the true HEAD cannot be determined by merge method,
      shallow history, or ambiguity -- never a subject-match guess).
    merge_commits: the integrating merge commit for each absent ref (or
      UNRESOLVED when no single merge is discoverable / parents unavailable).
      For present refs the merge commit is not resolved (direct ref path).
      Kept as a separate meaning from merged_heads for audit; existing
      merged_heads consumers (40-hex ancestry check) keep working.
    pins: cc65/Geargrafx revisions from tools/environment.py DEPENDENCIES.
    """
    head = _git_output("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError(f"BLOCKED: repo HEAD is not a 40-hex SHA: {head!r}")
    branch = _git_output("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD" or not branch:
        for env in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME"):
            if os.environ.get(env):
                branch = os.environ[env]
                break
        else:
            branch = f"detached@{head[:12]}"
    merged_heads: dict = {}
    merge_commits: dict = {}
    for ref in _MERGED_REFS:
        try:
            sha = _git_output("rev-parse", ref)
        except ValueError:
            # Branch ref absent (deleted post-merge, shallow clone, ...):
            # discover the integrating merge commit instead of guessing.
            # Subject match alone never proves integration: the merge's
            # parent relationship and ancestry are verified below; anything
            # undeterminable -> UNRESOLVED, never a guessed HEAD.
            short = ref.removeprefix("origin/")
            discovered = _discover_merge_commit(short)
            if discovered is None:
                merged_heads[ref] = "UNRESOLVED (no single merge commit at build time)"
                merge_commits[ref] = "UNRESOLVED (no single merge commit at build time)"
                continue
            # The discovered merge itself must be integrated into HEAD;
            # a subject-only match outside history proves nothing.
            try:
                _git_output("merge-base", "--is-ancestor", discovered, "HEAD")
            except ValueError:
                merged_heads[ref] = ("UNRESOLVED (discovered merge %s not an ancestor "
                                     "of HEAD; subject match alone proves nothing)" % discovered)
                merge_commits[ref] = discovered
                continue
            second = _merge_second_parent(discovered)
            if second is None:
                merged_heads[ref] = ("UNRESOLVED (merge %s parents undeterminable; "
                                     "cannot confirm integrated HEAD)" % discovered)
                merge_commits[ref] = discovered
                continue
            try:
                _git_output("merge-base", "--is-ancestor", second, "HEAD")
            except ValueError:
                merged_heads[ref] = ("UNRESOLVED (integrated HEAD %s not an ancestor "
                                     "of HEAD; subject match alone proves nothing)" % second)
                merge_commits[ref] = discovered
                continue
            merged_heads[ref] = second
            merge_commits[ref] = discovered
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(f"BLOCKED: merged ref {ref} is not a 40-hex SHA: {sha!r}")
        # Re-verify at build time that the resolved SHA is actually integrated
        # into HEAD. A ref that has advanced beyond HEAD is NOT integrated and
        # must fail closed rather than record a wrong SHA.
        try:
            _git_output("merge-base", "--is-ancestor", sha, "HEAD")
        except ValueError as error:
            raise ValueError(
                f"BLOCKED: merged ref {ref} resolves to {sha}, which is not an "
                f"ancestor of HEAD (non-integrated / drift): {error}") from error
        merged_heads[ref] = sha
        merge_commits[ref] = "UNRESOLVED (direct ref present; merge commit not resolved)"
    try:
        pinned_cc65 = DEPENDENCIES["cc65"]["revision"]
        pinned_geargrafx = DEPENDENCIES["geargrafx"]["revision"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"BLOCKED: pinned toolchain revisions unavailable: {error}") from error
    if not pinned_cc65 or not pinned_geargrafx:
        raise ValueError("BLOCKED: pinned toolchain revisions are empty")
    return {"branch": branch, "head": head, "merged_heads": merged_heads,
            "merge_commits": merge_commits,
            "pinned_cc65": pinned_cc65, "pinned_geargrafx": pinned_geargrafx}


def experimental_boot_units(cue_path):
    """Opt-in link units; derive the record from the exact CUE to be mounted."""
    if cue_path is None:
        raise ValueError("experimental boot requires the exact mounted synthetic CUE")
    selected = selection_from_cue(cue_path, cue_path)
    return _experimental_boot_units(selected)


def build_experimental_payload(output: Path, mode="debug") -> tuple[bytes, dict]:
    """Build the standalone synthetic payload before its final medium is selected."""
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    ca65, ld65 = executable("ca65"), executable("ld65")
    debug_flags = ["-g"] if mode == "debug" else []
    output.mkdir(parents=True, exist_ok=True)
    payload_obj = output / "cd_load_exec_payload.o"
    subprocess.run([ca65, str(ROOT / "src/cd_load_exec_payload.s"), "-o", str(payload_obj), *debug_flags], check=True)
    payload_bin, payload_labels = output / "payload.bin", output / "payload.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/cd_load_exec_payload.cfg"), "-o", str(payload_bin),
                    "-Ln", str(payload_labels), str(payload_obj)], check=True)
    code, symbols = payload_bin.read_bytes(), parse_labels(payload_labels)
    if symbols.get("payload_entry") != EXEC_ADDRESS or not 1 <= len(code) <= 255:
        raise ValueError("payload must start at EXEC_ADDRESS and be 1..255 bytes")
    return code, symbols


def build_experimental_bios_boot(original_path: Path, output: Path, mode="debug", cue_path=None, payload_code=None, payload_symbols=None) -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    if cue_path is None:
        raise ValueError("experimental boot requires the exact mounted synthetic CUE")
    if type(payload_code) is not bytes or not 1 <= len(payload_code) <= 255:
        raise ValueError("experimental boot requires a finalized 1..255-byte payload before media selection")
    selected = selection_from_cue(cue_path, cue_path)
    # The selection is made only after media finalization.  Recheck immediately
    # before producing a ROM that embeds it, rather than trusting pathname equality.
    verify_media_identity(selected["media_identity"], cue_path)
    if user_sector(cue_path, analyze(cue_path), selected["lba"]) != encode_sector(selected["lba"], payload_code):
        raise ValueError("finalized selected sector does not contain the experimental ROM payload")
    original = original_path.read_bytes()
    verify_dev_rom(original, manifest=original_path.parent / "manifest.json")
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("experimental boot compiler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    debug_flags = ["-g"] if mode == "debug" else []
    objects = assemble_api_objects(output, mode)
    for source, defines in _experimental_boot_units(selected):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), *defines, "-o", str(obj), *debug_flags], check=True)
        objects.append(obj)
    bank, labels = output / "bank0.bin", output / "experimental-boot.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(bank), "-Ln", str(labels),
                    "-m", str(output / "experimental-boot.map"), *map(str, objects)], check=True)
    rom_symbols = parse_needed_labels(labels, REQUIRED)
    missing = [name for name in REQUIRED if name not in rom_symbols]
    if missing:
        raise ValueError(f"experimental boot symbols missing: {missing}")
    bank_bytes = bank.read_bytes()
    if len(bank_bytes) != BANK_BYTES:
        raise ValueError("experimental boot bank is not 8 KiB")
    # Unified wiring: ALL THREE callees are linked into the experimental BOOT region,
    # so the pre-patch bank must still match the normal DEV ROM across the whole
    # API table/override area. Only this experimental image diverges.
    if bank_bytes[:0x1000] != original[:0x1000]:
        raise ValueError("experimental pre-patch bank changed the API table/override area")
    patch_addrs = parse_needed_labels(labels, _PROTO_PATCH_LABELS)
    if any(n not in patch_addrs for n in _PROTO_PATCH_LABELS):
        raise ValueError(f"experimental slot-02 patch symbols missing: {_PROTO_PATCH_LABELS}")
    slot33_addrs = parse_needed_labels(labels, _SLOT33_PATCH_LABELS)
    if any(n not in slot33_addrs for n in _SLOT33_PATCH_LABELS):
        raise ValueError(f"experimental slot-33 patch symbols missing: {_SLOT33_PATCH_LABELS}")
    slot03_addrs = parse_needed_labels(labels, _SLOT03_PATCH_LABELS)
    if any(n not in slot03_addrs for n in _SLOT03_PATCH_LABELS):
        raise ValueError(f"experimental slot-03 patch symbols missing: {_SLOT03_PATCH_LABELS}")
    # Ordered fail-closed patches: slot-$02 first, then slot-$33, then slot-$03.
    patched = _patch_slot02_to_proto(bank_bytes, patch_addrs["api_slot_02"],
                                     patch_addrs["api_unimplemented"],
                                     patch_addrs["cd_base_proto_entry"])
    patched = _redirect_slot33_to_body(patched, slot33_addrs["api_slot_33"],
                                       slot33_addrs["experimental_slot_33"])
    patched = _redirect_slot03_to_link(patched, slot03_addrs["api_slot_03"],
                                       slot03_addrs["cd_read_link_entry"])
    slot02_addr = patch_addrs["api_slot_02"]
    slot33_stub, slot33_body = slot33_addrs["api_slot_33"], slot33_addrs["experimental_slot_33"]
    slot03_stub, link_entry = slot03_addrs["api_slot_03"], slot03_addrs["cd_read_link_entry"]
    rom = output / ROM_NAME
    rom.write_bytes(bytes(patched) + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    if len(data) != IMAGE_BYTES or data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("experimental image changed size or unused banks")
    # Whole-image API-area diff vs the normal DEV ROM must lie ENTIRELY inside
    # the union of the slot-$02, slot-$33 and slot-$03 windows, and ALL THREE
    # windows must take effect: any other delta (including the probe-overlay
    # window) fails, as does a silent no-op patch. A window byte that
    # coincidentally matches (same low byte on jump targets) is allowed;
    # unexpected bytes are not, so all three patches are additionally verified
    # byte-exact below.
    # BOOT divergence (>= $F000, offset >= 0x1000) is expected (experimental
    # reset/loader/proto/dspoff/link) and covered by the vector/SEI checks that follow.
    allowed = unified_api_windows(slot02_addr)
    api_diff = [i for i in range(0x1000) if data[i] != original[i]]
    if not set(api_diff) <= allowed:
        raise ValueError("experimental image changed the API area outside the "
                         "slot-$02/slot-$33/slot-$03 patch windows")
    slot02_window = set(range(slot02_addr - 0xE000, slot02_addr - 0xE000 + SLOT02_PATCH_LEN))
    slot33_window = set(range(_SLOT33_TABLE, _SLOT33_TABLE + 3))
    slot03_window = set(range(_SLOT03_TABLE, _SLOT03_TABLE + 3))
    if (not (set(api_diff) & slot02_window) or not (set(api_diff) & slot33_window)
            or not (set(api_diff) & slot03_window)):
        raise ValueError("experimental image is missing a unified patch window; "
                         "refusing a silent no-op")
    if bytes(data[slot02_addr - 0xE000:slot02_addr - 0xE000 + SLOT02_PATCH_LEN]) != \
            bytes((0x4C, patch_addrs["cd_base_proto_entry"] & 0xFF,
                   (patch_addrs["cd_base_proto_entry"] >> 8) & 0xFF, 0xEA, 0xEA)):
        raise ValueError("slot-$02 window is not the expected JMP proto+NOP pad")
    if bytes(data[_SLOT33_TABLE:_SLOT33_TABLE + 3]) != \
            bytes((0x4C, slot33_body & 0xFF, (slot33_body >> 8) & 0xFF)):
        raise ValueError("slot-$33 window is not the expected JMP body")
    if bytes(data[_SLOT03_TABLE:_SLOT03_TABLE + 3]) != \
            bytes((0x4C, link_entry & 0xFF, (link_entry >> 8) & 0xFF)):
        raise ValueError("slot-$03 window is not the expected JMP link")
    reset_vector = int.from_bytes(data[0x1FFE:0x2000], "little")
    if reset_vector != rom_symbols["experimental_bios_reset"] or rom_symbols["experimental_bios_init"] != reset_vector + 1:
        raise ValueError("reset vector is not the experimental one-byte SEI followed by init")
    if data[reset_vector & 0x1FFF] != 0x78:
        raise ValueError("experimental reset must start with SEI")

    if payload_symbols is None:
        code, pay_symbols = build_experimental_payload(output, mode)
        if code != payload_code:
            raise ValueError("experimental ROM payload differs from the finalized media payload")
    else:
        # Custom proto-test payload (Agent-A): entry must be EXEC, 1..255 bytes,
        # symbols must carry payload_entry/payload_done. Media containment was
        # already verified via user_sector above; no standard-payload equality.
        if set(payload_symbols) != set(PAYLOAD_LABELS):
            raise ValueError(f"custom payload symbols must be {PAYLOAD_LABELS}")
        if payload_symbols.get("payload_entry") != EXEC_ADDRESS:
            raise ValueError("custom payload_entry must equal EXEC_ADDRESS")
        for name in PAYLOAD_LABELS:
            addr = payload_symbols[name]
            if type(addr) is not int or not EXEC_ADDRESS <= addr < EXEC_ADDRESS + 0x800:
                raise ValueError(f"custom {name} ${addr!r} outside payload window")
        code, pay_symbols = payload_code, dict(payload_symbols)
    symbols = {name: rom_symbols[name] for name in REQUIRED}
    symbols.update({name: pay_symbols[name] for name in PAYLOAD_LABELS})
    # Verified halt-classification addresses from the link (harness-D native
    # requirement); every address below is link-derived, never hardcoded.
    slot_symbols = parse_needed_labels(labels, ("api_slot_02", "api_slot_03", "api_slot_33", "api_unimplemented"))
    for name in ("api_slot_02", "api_slot_03", "api_slot_33", "api_unimplemented"):
        if name not in slot_symbols:
            raise ValueError(f"experimental slot wiring symbol missing from the link: {name}")
        symbols[name] = slot_symbols[name]
    link_labels = {"api_slot_02": slot_symbols["api_slot_02"],
                   "api_slot_03": slot_symbols["api_slot_03"],
                   "api_slot_33": slot_symbols["api_slot_33"],
                   "api_unimplemented": slot_symbols["api_unimplemented"],
                   "cd_base_proto_entry": rom_symbols["cd_base_proto_entry"],
                   "experimental_slot_33": rom_symbols["experimental_slot_33"],
                   "cd_read_link_entry": rom_symbols["cd_read_link_entry"],
                   "slot_02_table_entry": 0xE000 + 3 * 0x02,
                   "slot_03_table_entry": 0xE000 + 3 * 0x03,
                   "slot_33_table_entry": 0xE000 + 3 * 0x33}
    # The shared integration judge uses semantic trace names; these aliases point to this image's new reset labels.
    trace_symbols = dict(symbols)
    trace_symbols.update({"dev_boot_entry": symbols["experimental_bios_init"],
                          "dev_selection_halt": symbols["experimental_bios_selection_halt"]})
    (output / "trace-labels.txt").write_text("".join(f"{name} {value:04x}\n" for name, value in trace_symbols.items()), encoding="ascii")
    # Building can take long enough for an external writer to replace a test
    # input.  The integration performs the final mount-adjacent check as well.
    verify_media_identity(selected["media_identity"], cue_path)
    provenance = unified_repo_provenance()
    manifest = {
        "status": "EXPERIMENTAL-DEV-ONLY-not-compatible-not-a-bios-abi",
        "mode": mode, "rom_name": ROM_NAME, "rom_sha256": hashlib.sha256(data).hexdigest(), "rom_bytes": IMAGE_BYTES,
        "payload_sha256": hashlib.sha256(code).hexdigest(), "payload_bytes": len(code), "payload_checksum": sum(code) & 0xFF,
        "payload_entry_offset": 0, "exec_address": EXEC_ADDRESS,
        "selection_contract": {"host_side_cue_analysis": True, "guest_toc": "NOT_IMPLEMENTED", **selected},
        "default_request": {"lba": selected["lba"], "count": 1}, "sector_bytes": SECTOR_BYTES,
        "symbols": symbols, "trace_symbols": trace_symbols, "labels": link_labels,
        "expected_mpr": EXPECTED_MPR,
        "pinned_cc65": provenance["pinned_cc65"], "pinned_geargrafx": provenance["pinned_geargrafx"],
        "api_implementation": API_IMPLEMENTATION,
        "cd_read_api_slot_03": "NOT_IMPLEMENTED", "system_card_abi": "NO_CLAIM", "release_eligible": False,
        "dev_callable": False, "private_media_used": False,
        "branch": provenance["branch"], "head": provenance["head"],
        "merged_heads": provenance["merged_heads"],
        "merge_commits": provenance["merge_commits"],
        "cd_base_proto": {"contract": "CD_BASE_PROTO_V1-experimental-only-not-genuine-abi",
                            "slot": "$02", "entry": "$E006",
                            "proto_entry": f"${patch_addrs['cd_base_proto_entry']:04X}",
                            "patch": {"target": "api_slot_02", "length_bytes": SLOT02_PATCH_LEN,
                                      "before": "ldx #$02; jmp api_unimplemented",
                                      "after": f"jmp ${patch_addrs['cd_base_proto_entry']:04X} + NOP pad"},
                            "assumption": ("EXPERIMENTAL processing: LBA/index/state/MPR/RAM handling "
                                           "per src/cd_base_proto.s; NOT genuine System Card CD_BASE, "
                                           "NOT an ABI claim, NOT compatibility evidence."),
                            "reference_jsr": "0/81", "abi_verified": "0/81",
                            "state": "$2400-$2407 owned, $2408-$240A scratch"},
        "experimental_slot_33": {
            "table_entry": "0xE099",
            "table_bytes": "4C %02x %02x" % (slot33_body & 0xFF, slot33_body >> 8),
            "fallback_stub": f"{slot33_stub:#06x}",
            "body": f"{slot33_body:#06x}",
            "contract": ("project-own DEV EX_DSPOFF (work-RAM shadow $2502/$2503, AND #$3F "
                           "immediate write, C=0 ok / C=1 unseeded reject); NOT the reference ABI"),
            "normal_rom_table_entry": "unchanged nonreturning stub",
        },
        "cd_read_link": {
            "slot": "$03", "entry": "$E009",
            "table_entry": "0xE009",
            "table_bytes": "4C %02x %02x" % (link_entry & 0xFF, link_entry >> 8),
            "fallback_stub": f"{slot03_stub:#06x}",
            "link_entry": f"${link_entry:04X}",
            "contract": ("CD_READ_LINK_V1-experimental-only-not-genuine-abi: 24-bit "
                           "base+block WITH carry, refuse BEFORE any READ(6) on invalid "
                           "state/window/cap/range, else READ(6) 1 sector to $2800; "
                           "state $2400-$2407 preserved, LINK_RES $2320-$2329; "
                           "wired via the $E009 table entry as an EXPERIMENTAL "
                           "contract only (inputs $2400-$2402 base + $20F2-$20F4 "
                           "block + $20F5-$20F7 window, MPR1=$F8, work-RAM stack, "
                           "IRQs masked); NOT the genuine CD_READ ABI"),
            "reference_jsr": "0/81", "abi_verified": "0/81",
            "normal_rom_table_entry": "unchanged nonreturning stub",
        },
        "unified_bios": {"agents": ["agent-a/cd-base-proto-g5", "agent-b/ex-dspoff-real-cpu-g5",
                                      "agent-b/cd-read-link-g5"],
                         "slots": ["$02", "$03", "$33"],
                         "patch_order": ["slot-02", "slot-33", "slot-03"],
                         "linkage": ("CD_BASE $02 proto computes base state $2400-$2402; "
                                     "CD_READ link $03 consumes that state with block/window "
                                     "inputs (no test pre-seeding of $2400 needed); EX_DSPOFF "
                                     "$33 keeps its real processing"),
                         "contract": ("UNIFIED experimental-only image: $02 CD_BASE proto + $03 "
                                      "CD_READ link + $33 EX_DSPOFF fragment as REAL processing; "
                                      "NOT genuine ABI, NOT compatibility evidence."),
                         "reference_jsr": "0/81", "abi_verified": "0/81",
                         "dev_callable": False, "release_eligible": False},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    parser.add_argument("--cue", type=Path, required=True,
                        help="the exact synthetic CUE that will be mounted for this image")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    output = ROOT / "build" / args.mode / "experimental-bios-boot"
    payload, _ = build_experimental_payload(output, args.mode)
    rom = build_experimental_bios_boot(base, output, args.mode, args.cue, payload)
    print(f"PASS experimental DEV-only 256 KiB image (NOT compatible, NOT a BIOS ABI): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"experimental-bios-boot build FAIL/BLOCKED: {error}") from error
