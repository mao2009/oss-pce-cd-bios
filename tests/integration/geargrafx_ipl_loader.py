"""Issue #670 Phase 2: DEV-only multi-sector IPL loader on the pinned Geargrafx core (synthetic media only).

NOT a compatible System Card BIOS, NOT a BIOS ABI, NOT CD_READ, NOT release eligible. No commercial media,
no reference BIOS, no JSR gate. One ROM per host-derived layout (INDEX 01 LBA is embedded, payload bytes are not).

Each case builds a synthetic disc with an information sector (data-relative sector 1) and body sectors, runs
the ROM cold and warm, and judges the guest's own result block ($2F00) and the visible load area ($3000-$3FFF).
Negative cases must be refused before any load-area write; post-copy and read-failure cases must never reach
the payload halt. Partial copies are reported by the copied-sector count.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))
from build_cdload_exec import parse_labels, USER_OFFSET
from environment import executable
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image

SEL = 0x000109            # INDEX 01 of the synthetic data track (265); the ROM embeds this LBA
SECTORS = 300             # synthetic BIN frames; frames >= SECTORS do not exist (used for partial read failure)
SECTOR = 2048
SIG = b"PC Engine CD-ROM SYSTEM"
BUDGET = 60_000_000
BUF = 0x2200
RB = 0x2F00 - 0x2800      # result block offset inside the 6144-byte probe dump ($2800-$3FFF)
PRES = 0x2F80 - 0x2800    # payload result offset
LOAD_LO = 0x3000 - 0x2800
LOAD_HI = 0x4000 - 0x2800
PAY_ORIGINS = {"A": (0x3000, 0xA5), "B": (0x3000, 0xB6), "C": (0x3F00, 0xC7)}
MPRS_STD = [0x80, 0x81, 0x82, 0x83, 0x84]


def pattern(i: int, seed: int) -> int:
    return (i * 7 + (i >> 8) * 13 + seed) & 0xFF


def body_for(code: bytes, n: int, seed: int = 0x21) -> bytes:
    total = n * SECTOR
    data = bytearray(code) + bytearray(pattern(i, seed) for i in range(len(code), max(total, len(code))))
    return bytes(data[:total])


def info_sector(iplblk: int, n: int, sta: int, entry: int, mprs, openmode: int, cksum: int) -> bytearray:
    s = bytearray(SECTOR)
    s[0:3] = (iplblk & 0xFFFFFF).to_bytes(3, "big")
    s[3] = n & 0xFF
    s[4:6] = (sta & 0xFFFF).to_bytes(2, "little")
    s[6:8] = (entry & 0xFFFF).to_bytes(2, "little")
    s[8:13] = bytes(mprs)
    s[13] = openmode
    s[14:16] = (cksum & 0xFFFF).to_bytes(2, "little")
    s[32:32 + len(SIG)] = SIG
    return s


def build_loader(out: Path, sel: int, variant: str = "main", defines=()):
    out.mkdir(parents=True, exist_ok=True)
    ca65, ld65 = executable("ca65"), executable("ld65")
    obj = out / f"ipl_loader_rom_{variant}.o"
    rom, lbl = out / f"ipl-loader-rom-{variant}.pce", out / f"ipl-loader-rom-{variant}.lbl"
    subprocess.run([ca65, str(HERE / "ipl_loader_rom.s"), "-D", f"IPL_SEL=${sel:04X}", *defines,
                    "-o", str(obj), "-g"], check=True)
    subprocess.run([ld65, "-C", str(HERE / "ipl_loader_rom.cfg"), "-o", str(rom), "-Ln", str(lbl), str(obj)], check=True)
    if rom.stat().st_size != 0x2000:
        raise ValueError("loader ROM is not 8 KiB")
    return rom, parse_labels(lbl)


def build_payload(out: Path, origin: int, tag: int):
    out.mkdir(parents=True, exist_ok=True)
    ca65, ld65 = executable("ca65"), executable("ld65")
    name = f"ipl_payload_{origin:04x}_{tag:02x}"
    obj, bin_path, lbl, cfg = out / f"{name}.o", out / f"{name}.bin", out / f"{name}.lbl", out / f"{name}.cfg"
    cfg.write_text(f"MEMORY {{\n    RAM: start = ${origin:04X}, size = $0F00, file = %O;\n}}\n"
                   f"SEGMENTS {{\n    CODE: load = RAM, type = ro, start = ${origin:04X};\n}}\n", encoding="ascii")
    subprocess.run([ca65, str(HERE / "ipl_payload.s"), "-D", f"PAY_TAG=${tag:02X}", "-o", str(obj), "-g"], check=True)
    subprocess.run([ld65, "-C", str(cfg), "-o", str(bin_path), "-Ln", str(lbl), str(obj)], check=True)
    code = bin_path.read_bytes()
    symbols = parse_labels(lbl)
    if symbols.get("payload_entry") != origin or not 1 <= len(code) <= 255:
        raise ValueError("payload must start at its origin and be 1..255 bytes")
    return code, symbols["payload_halt"]


def make_disc(folder: Path, frames: dict) -> Path:
    """Synthetic MODE1 track with INDEX 01 at SEL (same layout rule as geargrafx_dev_ipl_boot)."""
    build_image(folder, SECTORS)
    cue = folder / CUE_NAME
    text = cue.read_text(encoding="ascii")
    if text.count("INDEX 01 00:00:00\n") != 1:
        raise ValueError("unexpected synthetic CUE layout")
    mm, ss, ff = SEL // (60 * 75), (SEL // 75) % 60, SEL % 75
    text = text.replace("INDEX 01 00:00:00\n",
                        f"INDEX 00 00:00:00\n    INDEX 01 {mm:02d}:{ss:02d}:{ff:02d}\n", 1)
    cue.write_text(text, encoding="ascii")
    bin_path = folder / BIN_NAME
    raw = bytearray(bin_path.read_bytes())
    for frame, data in frames.items():
        if frame < SECTORS:
            at = frame * RAW_BYTES + USER_OFFSET
            raw[at:at + SECTOR] = data
    bin_path.write_bytes(raw)
    return cue


def case_disc(case: dict, body_written: bytes, info: bytes) -> dict:
    # The information sector is written LAST so that an IPLBLK that overlaps it (negative case) still
    # leaves a valid signature; the loader must then refuse on the IPLBLK field, not on the signature.
    frames = {}
    for i in range(case["n"]):
        frames[SEL + case["iplblk"] + i] = body_written[i * SECTOR:(i + 1) * SECTOR]
    frames[SEL + 1] = bytes(info)
    return frames


def apply_edits(buf: bytearray, edits):
    for offset, value in edits:
        buf[offset] = value
    return buf


def judge(records: dict, case: dict, body: bytes, payload_tag: int):
    """Judge the guest's result block, the visible load area, and the payload results; cold AND warm."""
    if len(records["RUN"]) != 2:
        raise ValueError(f"{case['name']}: expected cold and warm runs")
    for run in records["RUN"]:
        cycle = run["cycle"]
        buf = bytes.fromhex(run["buffer_hex"])
        if len(buf) != 6144:
            raise ValueError(f"{case['name']} {cycle}: dump has {len(buf)} bytes")
        if run["reason"] != case["reason"] or run.get("halt_confirmed") is not True:
            raise ValueError(f"{case['name']} {cycle}: stopped with {run['reason']} "
                             f"(halt_confirmed={run.get('halt_confirmed')}), expected {case['reason']}")
        code, copied = buf[RB], buf[RB + 2]
        if (code, copied) != (case["code"], case["copied"]):
            raise ValueError(f"{case['name']} {cycle}: guest code/copied = {code}/{copied}, "
                             f"expected {case['code']}/{case['copied']}")
        if case["copied"] and case["code"] in (0,):
            srcsum = buf[RB + 6] | buf[RB + 7] << 8
            rdsum = buf[RB + 8] | buf[RB + 9] << 8
            hdr = buf[RB + 10] | buf[RB + 11] << 8
            if case.get("public"):
                # PUBLIC format: no header sum. Only the copy is verified: readback must equal the disc-side sum.
                if not (srcsum == rdsum):
                    raise ValueError(f"{case['name']} {cycle}: sums src={srcsum:04x} rd={rdsum:04x}")
            elif not (srcsum == rdsum == hdr):
                raise ValueError(f"{case['name']} {cycle}: sums src={srcsum:04x} rd={rdsum:04x} hdr={hdr:04x}")
        # visible load area: the loaded prefix must equal the body, everything else must still be the initial zeros
        dst = case["sta"]
        expected = bytearray(0x1000)
        for a in range(0x3000, 0x4000):
            k = a - dst
            if 0 <= k < case["copied"] * SECTOR:
                expected[a - 0x3000] = body[k] ^ case.get("visible_xor", 0)
        if buf[LOAD_LO:LOAD_HI] != bytes(expected):
            raise ValueError(f"{case['name']} {cycle}: visible load area differs from the copied prefix")
        pay = buf[PRES:PRES + 15]
        if case["payload_ran"]:
            if pay[0] != payload_tag or pay[8] != 0x5A:
                raise ValueError(f"{case['name']} {cycle}: payload did not run to completion: {pay.hex()}")
            # DEV: the raw value is the bank. PUBLIC: bank = raw + $80 (mpr_add), observed on the pinned core.
            # MPR2..6 as the payload observed them (pay[5] = MPR2 via TMA, pay[11..14] = MPR3..6 via TMA).
            # Expected: raw + mpr_add, mod 256. PUBLIC: mpr_add = $80 (observed mapping). DEV: 0 (raw is the bank).
            for name, offset, raw in (("MPR2", 5, case["mprs"][0]), ("MPR3", 11, case["mprs"][1]),
                                      ("MPR4", 12, case["mprs"][2]), ("MPR5", 13, case["mprs"][3]),
                                      ("MPR6", 14, case["mprs"][4])):
                want = (raw + case.get("mpr_add", 0)) & 0xFF
                if pay[offset] != want:
                    raise ValueError(f"{case['name']} {cycle}: {name} at payload entry {pay[offset]:02x}, "
                                     f"expected {want:02x}")
            for slot, addr in ((1, 0x3FFF), (2, 0x4000), (3, 0x5FFF), (4, 0x6000)):
                k = addr - dst
                if 0 <= k < len(body):
                    if pay[slot] != body[k]:
                        raise ValueError(f"{case['name']} {cycle}: payload byte at ${addr:04X} = {pay[slot]:02x}, "
                                         f"expected {body[k]:02x}")
            k = 0x4000 - dst
            if 0 <= k < len(body) and (pay[6] != body[k] or pay[7] != body[k]):
                raise ValueError(f"{case['name']} {cycle}: $4000 changed across bank switch: {pay[6]:02x} {pay[7]:02x}")
            # payload's own sum of $4000-$5FFF, checked only when the body covers the whole window
            if 0 <= 0x4000 - dst and 0x6000 - dst <= len(body):
                window = body[0x4000 - dst:0x6000 - dst]
                want_sum = sum(window) & 0xFFFF
                got_sum = pay[9] | pay[10] << 8
                if got_sum != want_sum:
                    raise ValueError(f"{case['name']} {cycle}: payload window sum {got_sum:04x} != {want_sum:04x}")
        elif any(pay):
            raise ValueError(f"{case['name']} {cycle}: payload results present but the payload must not run")


CASES = [
    # ---- positive: same ROM, different payloads, boundary and load-address variants ----
    dict(name="pos_n5_3000_payloadA", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, reason="cd_halt_ok", code=0, copied=5, payload_ran=True),
    dict(name="pos_n5_3000_payloadB_same_rom", pay="B", n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, reason="cd_halt_ok", code=0, copied=5, payload_ran=True),
    dict(name="pos_n8_crosses_4000_and_6000", pay="A", n=8, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, reason="cd_halt_ok", code=0, copied=8, payload_ran=True),
    dict(name="pos_n3_at_3F00_covers_old_pcex_markers", pay="C", n=3, iplblk=2, sta=0x3F00, entry=0x3F00,
         mprs=MPRS_STD, openmode=0, reason="cd_halt_ok", code=0, copied=3, payload_ran=True),
    # ---- negative: refused before any load-area write ----
    dict(name="neg_signature_byte", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, info_edits=[(33, 0x01)], reason="cd_halt_fail", code=20, copied=0, payload_ran=False),
    dict(name="neg_iplbln_zero", pay="A", n=0, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, reason="cd_halt_fail", code=22, copied=0, payload_ran=False),
    dict(name="neg_iplbln_11", pay="A", n=11, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, reason="cd_halt_fail", code=22, copied=0, payload_ran=False),
    dict(name="neg_iplblk_zero", pay="A", n=5, iplblk=0, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, reason="cd_halt_fail", code=21, copied=0, payload_ran=False),
    dict(name="neg_iplblk_one", pay="A", n=5, iplblk=1, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, reason="cd_halt_fail", code=21, copied=0, payload_ran=False),
    dict(name="neg_iplsta_2800_below_load_area", pay="A", n=5, iplblk=2, sta=0x2800, entry=0x3000,
         mprs=MPRS_STD, openmode=0, reason="cd_halt_fail", code=23, copied=0, payload_ran=False),
    dict(name="neg_iplsta_6000_overruns_7fff", pay="A", n=5, iplblk=2, sta=0x6000, entry=0x6000,
         mprs=MPRS_STD, openmode=0, reason="cd_halt_fail", code=23, copied=0, payload_ran=False),
    dict(name="neg_entry_beyond_image", pay="A", n=2, iplblk=2, sta=0x3000, entry=0x7000, mprs=MPRS_STD,
         openmode=0, reason="cd_halt_fail", code=24, copied=0, payload_ran=False),
    dict(name="neg_entry_below_iplsta", pay="A", n=2, iplblk=2, sta=0x3000, entry=0x2FFF, mprs=MPRS_STD,
         openmode=0, reason="cd_halt_fail", code=24, copied=0, payload_ran=False),
    dict(name="neg_mpr2_not_ram_bank", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0x00, 0x81, 0x82, 0x83, 0x84], openmode=0, reason="cd_halt_fail", code=25, copied=0,
         payload_ran=False),
    dict(name="neg_openmode_nonzero", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=1, reason="cd_halt_fail", code=26, copied=0, payload_ran=False),
    dict(name="neg_reserved_byte_nonzero", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, info_edits=[(20, 0x01)], reason="cd_halt_fail", code=28, copied=0, payload_ran=False),
    dict(name="neg_body_lba_overflows_16bit", pay="A", n=5, iplblk=0xFFFF, sta=0x3000, entry=0x3000,
         mprs=MPRS_STD, openmode=0, reason="cd_halt_fail", code=27, copied=0, payload_ran=False),
    # base LBA fits, but the last body sector (IPL_SEL + IPLBLK + N - 1) would wrap past $FFFF
    dict(name="neg_body_lba_tail_wraps_16bit", pay="A", n=3, iplblk=0xFEF5, sta=0x3000, entry=0x3000,
         mprs=MPRS_STD, openmode=0, reason="cd_halt_fail", code=27, copied=0, payload_ran=False),
    # final body sector lands exactly on 0xFFFF: exclusive end 0x10000 exceeds the 16-bit tail check
    dict(name="neg_body_lba_final_sector_at_ffff", pay="A", n=2, iplblk=0xFEF5, sta=0x3000, entry=0x3000,
         mprs=MPRS_STD, openmode=0, reason="cd_halt_fail", code=27, copied=0, payload_ran=False),
    # 24-bit IPLBLK high byte is nonzero: the loader only supports the lower 16 bits (LBA width limit).
    dict(name="neg_iplblk_high_byte_nonzero", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, info_edits=[(0, 0x01)], reason="cd_halt_fail", code=21, copied=0, payload_ran=False),
    dict(name="neg_info_sector_eject_in_data", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, inject="eject-in-data", reason="cd_halt_fail", code=4, copied=0, payload_ran=False),
    # ---- post-copy and partial-copy: copied, NOT executed ----
    dict(name="neg_body_sector2_corrupt_on_disc", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=MPRS_STD, openmode=0, body_edits={2: [(100, 0x40)]}, reason="cd_halt_fail", code=30, copied=5,
         payload_ran=False),
    dict(name="partial_read_beyond_disc_after_5_sectors", pay="A", n=10, iplblk=30, sta=0x3000, entry=0x3000,
         mprs=MPRS_STD, openmode=0, reason="cd_halt_fail", code=5, copied=5, payload_ran=False),
    # error 31 fault injection: a TEST-ONLY mutant ROM writes each byte XOR 1, so the readback sum differs
    # from the disc-side sum. The guest must halt with 31 after the full copy and never jump to the entry.
    dict(name="fault_readback_mismatch_error31", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=MPRS_STD, openmode=0, rom="mutant", visible_xor=0x01, reason="cd_halt_fail", code=31, copied=5,
         payload_ran=False),
    # ==== PUBLIC IPL format (Issue #635, build IPL_PUBLIC). Bank = raw + $80 (observed black-box, pinned core).
    # No DEV header sum is required. Only the copy (readback == disc-side sum) is verified.
    # NOTE: this ROM's expectations intentionally differ from the DEV rule "raw $00 is refused" (neg_mpr2_not_ram_bank),
    # which stays for the DEV ROM only.
    dict(name="pub_n5_mpr_raw_0_4_zero_checksum_fields", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, mpr_add=0x80, info_edits=[(14, 0), (15, 0)],
         reason="cd_halt_ok", code=0, copied=5, payload_ran=True),
    dict(name="pub_n5_header_bytes_14_15_nonzero_ignored", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, mpr_add=0x80, info_edits=[(14, 0x34), (15, 0x12)],
         reason="cd_halt_ok", code=0, copied=5, payload_ran=True),
    dict(name="pub_n1_minimum_count", pay="A", n=1, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, mpr_add=0x80, info_edits=[(14, 0), (15, 0)],
         reason="cd_halt_ok", code=0, copied=1, payload_ran=True),
    dict(name="pub_n10_maximum_count_fills_3000_7fff", pay="A", n=10, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, mpr_add=0x80, info_edits=[(14, 0), (15, 0)],
         reason="cd_halt_ok", code=0, copied=10, payload_ran=True),
    dict(name="pub_mpr_raw_7_maps_to_bank_87", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[7, 6, 5, 4, 3], openmode=0, rom="public", public=True, mpr_add=0x80, info_edits=[(14, 0), (15, 0)],
         reason="cd_halt_ok", code=0, copied=5, payload_ran=True),
    dict(name="pub_neg_mpr_raw_8_refused", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 8, 4], openmode=0, rom="public", public=True, info_edits=[(14, 0), (15, 0)],
         reason="cd_halt_fail", code=25, copied=0, payload_ran=False),
    dict(name="pub_neg_mpr_raw_ff_refused", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 0xFF], openmode=0, rom="public", public=True, info_edits=[(14, 0), (15, 0)],
         reason="cd_halt_fail", code=25, copied=0, payload_ran=False),
    dict(name="pub_neg_mpr_raw_80_refused_dev_value", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0x80, 1, 2, 3, 4], openmode=0, rom="public", public=True, info_edits=[(14, 0), (15, 0)],
         reason="cd_halt_fail", code=25, copied=0, payload_ran=False),
    dict(name="pub_n10_at_3f00_crosses_8000", pay="C", n=10, iplblk=2, sta=0x3F00, entry=0x3F00,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, mpr_add=0x80,
         info_edits=[(14, 0), (15, 0)], reason="cd_halt_ok", code=0, copied=10, payload_ran=True),
    dict(name="pub_n20_at_3000_crosses_c000", pay="A", n=20, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, mpr_add=0x80,
         info_edits=[(14, 0), (15, 0)], reason="cd_halt_ok", code=0, copied=20, payload_ran=True),
    dict(name="pub_n11_above_dev_limit", pay="A", n=11, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, mpr_add=0x80,
         info_edits=[(14, 0), (15, 0)], reason="cd_halt_ok", code=0, copied=11, payload_ran=True),
    dict(name="pub_reserved_nonzero_ignored", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, mpr_add=0x80,
         info_edits=[(14, 0), (15, 0), (20, 0x01), (25, 0xFF)], reason="cd_halt_ok", code=0, copied=5,
         payload_ran=True),
    dict(name="pub_neg_iplsta_top_overrun_e000", pay="A", n=16, iplblk=2, sta=0x7000, entry=0x7000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, info_edits=[(14, 0), (15, 0)],
         reason="cd_halt_fail", code=23, copied=0, payload_ran=False),
    dict(name="pub_neg_iplbln_32_refused", pay="A", n=32, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, info_edits=[(14, 0), (15, 0)],
         reason="cd_halt_fail", code=22, copied=0, payload_ran=False),
    # 16-bit load-end overflow boundary: IPLSTA $3000 + N*2048 wraps past $FFFF for
    # N=26..31 (e.g. N=26: actual end $10000, wrapped high byte $00). Must refuse
    # before any copy (copied=0, payload never runs), not wrap and copy.
    *[dict(name=f"pub_neg_load_end_overflow_n{n}", pay="A", n=n, iplblk=2, sta=0x3000, entry=0x3000,
          mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, info_edits=[(14, 0), (15, 0)],
          reason="cd_halt_fail", code=23, copied=0, payload_ran=False) for n in range(26, 32)],
    dict(name="pub_post_sum_preserving_change_accepted_documented_limit", pay="A", n=5, iplblk=2, sta=0x3000,
         entry=0x3000, mprs=[0, 1, 2, 3, 4], openmode=0, rom="public", public=True, mpr_add=0x80,
         info_edits=[(14, 0), (15, 0)], sum_preserving=True, reason="cd_halt_ok", code=0, copied=5,
         payload_ran=True),
    dict(name="pub_fault_readback_mismatch_error31", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000,
         mprs=[0, 1, 2, 3, 4], openmode=0, rom="public_mutant", public=True, mpr_add=0x80,
         info_edits=[(14, 0), (15, 0)], visible_xor=0x01, reason="cd_halt_fail", code=31, copied=5,
         payload_ran=False),
    # ==== DEV boundary for the DEV checksum (main ROM): 0x0000 and 0xFFFF are enforced, not special-cased.
    dict(name="dev_neg_checksum_0000_mismatch", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, info_edits=[(14, 0), (15, 0)], reason="cd_halt_fail", code=30, copied=5, payload_ran=False),
    dict(name="dev_neg_checksum_ffff_mismatch", pay="A", n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_STD,
         openmode=0, info_edits=[(14, 0xFF), (15, 0xFF)], reason="cd_halt_fail", code=30, copied=5,
         payload_ran=False),
]


def run_case(probe, rom, labels_path, case, pay_code, folder: Path, disc_prefix="card:", expect_loader=None):
    body = body_for(pay_code, case["n"])
    body_written = bytearray(body)
    for sector, edits in case.get("body_edits", {}).items():
        for offset, value in edits:
            body_written[sector * SECTOR + offset] ^= value
    body_written = bytes(body_written)
    if case.get("sum_preserving"):
        # Corrupt two bytes with XOR 1 whose sum effects are +1 and -1: the 16-bit additive sum is unchanged.
        # Chosen from the body so the corruption is deterministic; the sum equality is asserted, not assumed.
        p = next((i for i in range(0x100, len(body) - 1) if body[i] & 1 == 0 and body[i + 1] & 1 == 1
                  and (i // SECTOR) == 2), None)
        if p is None:
            raise ValueError(f"{case['name']}: no byte pair to corrupt with a sum-preserving change")
        body_written = bytearray(body_written)
        body_written[p] ^= 1
        body_written[p + 1] ^= 1
        body_written = bytes(body_written)
        if sum(body_written) & 0xFFFF != sum(body) & 0xFFFF:
            raise ValueError(f"{case['name']}: test construction did not preserve the sum")
    cksum = sum(body) & 0xFFFF  # the header carries the checksum of the body as ORIGINALLY built
    info = info_sector(case["iplblk"], case["n"], case["sta"], case["entry"], case["mprs"], case["openmode"], cksum)
    apply_edits(info, case.get("info_edits", []))
    disc = folder / case["name"]
    cue = make_disc(disc, case_disc(case, body_written, info))
    inject = case.get("inject")
    # "card:" = the pinned probe's paired-control path: the 8 KiB ROM is loaded as a HuCard and the CUE is
    # inserted afterwards with CD hardware enabled. The System Card BIOS path (LoadBiosFromBuffer) rejects
    # an 8 KiB image, so this loader is NOT exercised as a System Card BIOS.
    # disc_prefix="" selects the System Card BIOS load path (Issue #682 uses a 256 KiB image on that path).
    records = execute(probe, rom, f"{disc_prefix}{cue}", labels_path, BUDGET, inject)
    if expect_loader is not None:
        # the probe's own capability record must name the load path; a silent fallback would fail here
        caps = records["CAP"]
        if len(caps) != 1 or caps[0].get("loader") != expect_loader or caps[0].get("cdrom_media") is not True:
            raise ValueError(f"{case['name']}: load path is {caps}, expected loader {expect_loader!r} with media")
    judge(records, case, body_written, PAY_ORIGINS[case["pay"]][1])
    return {"status": "PASS", "copied": case["copied"], "guest_code": case["code"], "reason": case["reason"],
            "body_sha256": hashlib.sha256(body_written).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    out = ROOT / "build" / args.mode / "ipl-loader"
    out.mkdir(parents=True, exist_ok=True)
    rom, rom_labels = build_loader(out, SEL)
    rom_sha = hashlib.sha256(rom.read_bytes()).hexdigest()
    mutant, mutant_labels = build_loader(out, SEL, "mutant", ["-D", "IPL_MUTANT_READBACK"])
    mutant_sha = hashlib.sha256(mutant.read_bytes()).hexdigest()
    public, public_labels = build_loader(out, SEL, "public", ["-D", "IPL_PUBLIC"])
    public_sha = hashlib.sha256(public.read_bytes()).hexdigest()
    public_mutant, public_mutant_labels = build_loader(out, SEL, "public_mutant",
                                                       ["-D", "IPL_PUBLIC", "-D", "IPL_MUTANT_READBACK"])
    public_mutant_sha = hashlib.sha256(public_mutant.read_bytes()).hexdigest()
    roms = {"main": (rom, rom_labels), "mutant": (mutant, mutant_labels),
            "public": (public, public_labels), "public_mutant": (public_mutant, public_mutant_labels)}
    payloads = {}
    for key, (origin, tag) in PAY_ORIGINS.items():
        code, halt = build_payload(out, origin, tag)
        payloads[key] = (code, halt)
    probe = compile_probe(out, "geargrafx_cdread_probe.cpp", "geargrafx-ipl-loader-probe")
    results = {}
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pce-ipl-loader-") as tmp:
        tmp = Path(tmp)
        for case in CASES:
            code, halt = payloads[case["pay"]]
            case_rom, case_labels = roms[case.get("rom", "main")]
            labels = tmp / f"{case['name']}.labels"
            labels.write_text(f"cd_halt_ok {halt:04x}\ncd_halt_fail {case_labels['cd_halt_fail']:04x}\n"
                              f"cd_unexpected_interrupt {case_labels['cd_unexpected_interrupt']:04x}\n", encoding="ascii")
            try:
                results[case["name"]] = run_case(probe, case_rom, labels, case, code, tmp)
            except ValueError as error:
                raise SystemExit(f"FAIL {case['name']}: {error}") from error
        # Mutation check (verification only): disable the high-byte rejection and confirm the high-byte case
        # no longer refuses with code 21. This must not alter the production loader or the case count.
        highbyte_case = next(c for c in CASES if c["name"] == "neg_iplblk_high_byte_nonzero")
        hb_mutant, hb_labels = build_loader(out, SEL, "highbyte_mutant", ["-D", "IPL_MUTANT_HIGHBYTE"])
        hb_labels_path = tmp / "highbyte_mutant.labels"
        hb_labels_path.write_text(
            f"cd_halt_ok {payloads[highbyte_case['pay']][1]:04x}\n"
            f"cd_halt_fail {hb_labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {hb_labels['cd_unexpected_interrupt']:04x}\n", encoding="ascii")
        # With only the high-byte refusal disabled, the valid low 16 bits (iplblk=2) must be
        # accepted: run the same case through run_case()/judge() with normal-execution
        # expectations, so halt_confirmed, code 0, copied 5, payload completion and the
        # cold+warm readback/visible-memory checks are all verified -- not a substring match.
        # Note: this proves the mutant accepts the low 16 bits; it is NOT 24-bit LBA support.
        mutant_case = {**highbyte_case, "name": highbyte_case["name"] + "_mutant",
                       "reason": "cd_halt_ok", "code": 0, "copied": 5, "payload_ran": True}
        try:
            run_case(probe, hb_mutant, hb_labels_path, mutant_case, payloads[mutant_case["pay"]][0], tmp)
        except ValueError as err:
            raise SystemExit(f"FAIL mutation: mutant did not execute normally "
                             f"(expected cd_halt_ok, code 0, copied 5, payload ran): {err}") from err
        print(f"MUTATION CHECK high-byte (verification only): mutant executed normally "
              f"(cd_halt_ok, code 0, copied 5, payload ran, cold+warm); case={highbyte_case['name']}")

        if hashlib.sha256(rom.read_bytes()).hexdigest() != rom_sha:
            raise SystemExit("FAIL: loader ROM changed during the test")
        if hashlib.sha256(mutant.read_bytes()).hexdigest() != mutant_sha:
            raise SystemExit("FAIL: mutant loader ROM changed during the test")
        if hashlib.sha256(public.read_bytes()).hexdigest() != public_sha or \
                hashlib.sha256(public_mutant.read_bytes()).hexdigest() != public_mutant_sha:
            raise SystemExit("FAIL: public loader ROM changed during the test")
        passed = len(results)  # every case that did not match its expectation raised above
    evidence = {"scope": "DEV-only multi-sector IPL loader, synthetic media only; NOT compatible, NOT a BIOS ABI",
                "geargrafx_probe": "geargrafx_cdread_probe.cpp (pinned)", "mode": args.mode,
                "loader_rom_sha256": rom_sha, "loader_symbols": {k: v for k, v in rom_labels.items()
                                                                   if k in ("reset", "fail", "cd_halt_fail")},
                "cases": results, "status": "PASS",
                "limitations": ["real-core LBA 0x000109 only; LBA is 16-bit in this loader (the tail wrap is refused)",
                                "integrity is a DEV-only 16-bit additive sum: single-byte and most copy errors are "
                                "detected; sum-preserving corruption (e.g. swapped bytes) is NOT detected",
                                "the payload window sum is checked only when the body covers $4000-$5FFF",
                                "error 31 (readback mismatch) is reached only through the TEST-ONLY mutant ROM "
                                "(IPL_MUTANT_READBACK: written byte XOR 1); no core-level or bus-level fault injection",
                                "load area initial content is assumed zero (checked by every visible-area comparison)",
                                "PUBLIC format (IPL_PUBLIC ROMs): no header sum; a sum-preserving disc change is accepted "
                                "(case pub_post_sum_preserving_change_accepted_documented_limit)",
                                "PUBLIC format MPR rule: bank = raw + $80 for raw 0..7, observed black-box on the pinned "
                                "core with the pure System Card BIOS (Issue #635 run); not a hardware observation",
                                "IPLBLK is a 24-bit field but the LBA path is 16-bit: a nonzero high byte is refused (code 21); "
                                "full 24-bit support requires Owner-approved widening of the internal LBA path; "
                                "LBAs beyond the READ(6) 21-bit limit require a different command strategy, "
                                "whose support is currently UNKNOWN",
                                "data-track length is not available to the guest; boundary overflow is detected only when "
                                "READ(6) fails after a partial copy (partial_read_beyond_disc_after_5_sectors); pre-copy "
                                "rejection is not implemented and is documented in docs/670-ipl-information-data-block-acceptance.md"],
                "public_loader_rom_sha256": public_sha, "public_mutant_rom_sha256": public_mutant_sha}
    (out / "ipl-loader-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PASS multi-sector IPL loader, DEV and PUBLIC-format ROMs (synthetic): {passed}/{len(CASES)} cases "
          f"(cold+warm each); NOT compatible, NOT BIOS, release_eligible=false")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"ipl-loader FAIL/BLOCKED: {error}") from error
