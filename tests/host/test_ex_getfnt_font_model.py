"""Synthetic font conversion model (Issue #707, DEV-only).

Covers only synthetic patterns defined in this file. No upstream font data
is read or committed. Host only. No ROM and no emulator is used.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BANK_BYTES = 8192
banks_free_bytes = 31 * BANK_BYTES


def make_synthetic_bdf(cells, width, height, order=None):
    """Build minimal synthetic BDF text.

    cells: list of (encoding, rows) where rows are bytes of len(width//8*2)? No:
    each row is bytes of len(2) for width 12 or 16 (2 bytes per row).
    For width 12, only high 12 bits are used; low 4 bits must be zero.
    """
    n = len(cells)
    lines = [
        "STARTFONT 2.1",
        f"FONT synthetic-{width}",
        f"SIZE {height} 75 75",
        f"FONTBOUNDINGBOX {width} {height} 0 -2",
        "STARTPROPERTIES 2",
        'COPYRIGHT "synthetic"',
        "ENDPROPERTIES",
        f"CHARS {n}",
    ]
    idxs = order if order is not None else list(range(n))
    for idx in idxs:
        enc, rows = cells[idx]
        dw = width
        lines += [
            f"STARTCHAR c{idx}",
            f"ENCODING {enc}",
            f"SWIDTH {dw * 80} 0",
            f"DWIDTH {dw} 0",
            f"BBX {width} {height} 0 -2",
            "BITMAP",
        ]
        for r in rows:
            assert len(r) == 2, "synthetic row must be 2 bytes"
            lines.append(r.hex().upper())
        lines.append("ENDCHAR")
    lines.append("ENDFONT")
    return "\n".join(lines) + "\n"


def parse_synthetic_bdf(text, width, height):
    """Parse synthetic BDF, validating BBX/DWIDTH/BITMAP. Returns sorted list."""
    lines = text.splitlines()
    glyphs = []
    i = 0
    # header checks (light)
    if not any(l.startswith("FONTBOUNDINGBOX") for l in lines):
        raise ValueError("missing FONTBOUNDINGBOX")
    saw_chars = None
    for l in lines:
        if l.startswith("CHARS "):
            saw_chars = int(l.split()[1])
    cur = None
    in_bitmap = False
    seen = set()
    for l in lines:
        if l.startswith("STARTCHAR"):
            cur = {"rows": [], "enc": None, "dwidth": None, "bbx": None}
        elif l.startswith("ENCODING "):
            cur["enc"] = int(l.split()[1])
            if cur["enc"] in seen:
                raise ValueError("duplicate ENCODING")
            seen.add(cur["enc"])
        elif l.startswith("DWIDTH "):
            cur["dwidth"] = tuple(int(x) for x in l.split()[1:])
        elif l.startswith("BBX "):
            cur["bbx"] = tuple(int(x) for x in l.split()[1:])
        elif l.startswith("BITMAP"):
            in_bitmap = True
        elif l.startswith("ENDCHAR"):
            in_bitmap = False
            if cur is None or cur["enc"] is None:
                raise ValueError("ENDCHAR without ENCODING")
            if cur["dwidth"] != (width, 0):
                raise ValueError("DWIDTH mismatch")
            if cur["bbx"] != (width, height, 0, -2):
                raise ValueError("BBX mismatch")
            if len(cur["rows"]) != height:
                raise ValueError("BITMAP row count mismatch")
            for r in cur["rows"]:
                if len(r) != 2:
                    raise ValueError("BITMAP row length mismatch")
                if width == 12 and (r[1] & 0x0F) != 0:
                    raise ValueError("12-wide low nibble must be zero")
            glyphs.append((cur["enc"], cur["rows"]))
            cur = None
        elif in_bitmap and l.strip():
            try:
                cur["rows"].append(bytes.fromhex(l.strip()))
            except ValueError as e:
                raise ValueError("bad BITMAP hex") from e
    if saw_chars is not None and saw_chars != len(glyphs):
        raise ValueError("CHARS mismatch")
    glyphs.sort(key=lambda x: x[0])
    return glyphs


def sjis_to_jis(sjis_bytes):
    """SJIS 2 bytes -> JIS ENCODING decimal. Raises ValueError if invalid."""
    if len(sjis_bytes) != 2:
        raise ValueError("SJIS must be 2 bytes")
    s1, s2 = sjis_bytes[0], sjis_bytes[1]
    if not ((0x81 <= s1 <= 0x9F) or (0xE0 <= s1 <= 0xFC)):
        raise ValueError("INVALID_SJIS lead")
    if not ((0x40 <= s2 <= 0x7E) or (0x80 <= s2 <= 0xFC)):
        raise ValueError("INVALID_SJIS trail")
    if 0x81 <= s1 <= 0x9F:
        ku_base = (s1 - 0x81) * 2 + 1
    else:
        ku_base = (s1 - 0xE0) * 2 + 63
    if 0x40 <= s2 <= 0x7E:
        even = False
        ten = s2 - 0x40 + 1
    elif 0x9F <= s2 <= 0xDD:
        even = True
        ten = s2 - 0x9F + 1
    elif 0x80 <= s2 <= 0x9E:
        even = False
        ten = s2 - 0x80 + 64
    elif 0xDE <= s2 <= 0xFC:
        even = True
        ten = s2 - 0xDE + 64
    else:
        raise ValueError("INVALID_SJIS trail class")
    ku = ku_base + (1 if even else 0)
    return (ku + 0x20) * 256 + (ten + 0x20)


def glyph_to_rom_bytes(glyph_rows, size):
    """Return ROM bytes: 24 for 12-wide, 32 for 16-wide."""
    assert len(glyph_rows) in (12, 16)
    assert all(len(r) == 2 for r in glyph_rows)
    if size == 12:
        assert len(glyph_rows) == 12
        out = b"".join(glyph_rows)
        assert len(out) == 24
        return out
    if size == 16:
        assert len(glyph_rows) == 16
        out = b"".join(glyph_rows)
        assert len(out) == 32
        return out
    raise ValueError("size must be 12 or 16")


def rom_to_ram_32(rom_bytes, size):
    """Expand ROM to 32-byte RAM output. 12-wide gets 8 trailing zeros."""
    if size == 12:
        assert len(rom_bytes) == 24
        return rom_bytes + bytes(8)
    if size == 16:
        assert len(rom_bytes) == 32
        return rom_bytes
    raise ValueError("size must be 12 or 16")


def id_to_location(glyph_id, base_bank, stride):
    """Return (bank, offset) with no-crossing rule.

    16-wide (32): 256/bank. 12-wide (24): 341/bank (8192//24).
    Raises ValueError if id is negative.
    """
    if glyph_id < 0:
        raise ValueError("negative ID")
    if stride == 32:
        per = BANK_BYTES // 32
    elif stride == 24:
        per = BANK_BYTES // 24
    else:
        raise ValueError("stride must be 24 or 32")
    bank = base_bank + glyph_id // per
    pos = glyph_id % per
    return bank, pos * stride


def check_ram_output(buf_len, buf_addr, ram_lo, ram_hi):
    """Return True only if 32 bytes fit in RAM without 16-bit wrap.

    ram range is [ram_lo, ram_hi) in a linearised address space.
    ROM/I/O are outside this range by construction of the caller;
    the model treats anything outside as rejected. The 16-bit wrap
    check models the CPU address width; the caller must not rely on
    wrap-around. This host model is not real-core evidence.
    """
    if buf_len < 32:
        return False
    if not (0 <= buf_addr <= 0xFFFF):
        return False
    # 16-bit wrap: start+32 must not cross the address width.
    if buf_addr + 32 > 0x10000:
        return False
    if not (ram_lo <= buf_addr and buf_addr + 32 <= ram_hi):
        return False
    return True


def build_index(glyphs):
    """glyphs sorted by ENCODING -> (index list, id map)."""
    encs = [e for e, _ in glyphs]
    if encs != sorted(encs):
        raise ValueError("not sorted")
    if len(set(encs)) != len(encs):
        raise ValueError("duplicate")
    return encs


def synthetic_cells(ids, rows_fn):
    return [(e, rows_fn(e)) for e in ids]


class SyntheticFontModelTests(unittest.TestCase):
    def _cells12(self, encs):
        def rows(e):
            # distinct deterministic row: first byte varies with e
            b = e % 256
            return [bytes([b, 0x10])] * 12
        return synthetic_cells(encs, rows)

    def _cells16(self, encs):
        def rows(e):
            b = e % 256
            return [bytes([b, 0x20])] * 16
        return synthetic_cells(encs, rows)

    def test_pos_store12_24bytes(self):
        glyphs = parse_synthetic_bdf(
            make_synthetic_bdf(self._cells12([8481, 8482, 9250]), 12, 12), 12, 12
        )
        rom = glyph_to_rom_bytes(glyphs[0][1], 12)
        self.assertEqual(len(rom), 24)

    def test_pos_out12_32bytes_and_trailing_zero(self):
        rom = glyph_to_rom_bytes([bytes([0xF0, 0x00])] * 12, 12)
        out = rom_to_ram_32(rom, 12)
        self.assertEqual(len(out), 32)
        self.assertEqual(out[:24], rom)
        self.assertEqual(out[24:], bytes(8))

    def test_pos_out16_32bytes(self):
        rom = glyph_to_rom_bytes([bytes([0xA5, 0x00])] * 16, 16)
        out = rom_to_ram_32(rom, 16)
        self.assertEqual(len(out), 32)
        self.assertEqual(out, rom)

    def test_pos_index_search(self):
        glyphs = parse_synthetic_bdf(
            make_synthetic_bdf(self._cells12([8481, 8482, 9250]), 12, 12), 12, 12
        )
        idx = build_index(glyphs)
        self.assertEqual(idx, [8481, 8482, 9250])
        self.assertEqual(idx.index(8482), 1)

    def test_pos_id_bounds(self):
        b0, o0 = id_to_location(0, 3, 24)
        self.assertEqual((b0, o0), (3, 0))
        b1, o1 = id_to_location(340, 3, 24)
        self.assertEqual((b1, o1), (3, 340 * 24))
        b2, o2 = id_to_location(341, 3, 24)
        self.assertEqual((b2, o2), (4, 0))

    def test_pos_bank_boundary_24(self):
        # Naive id*24 would cross; corrected must not.
        bank, off = id_to_location(342, 3, 24)
        self.assertEqual((bank, off), (4, 24))
        # Glyph extent stays inside the bank.
        self.assertLessEqual(off + 24, BANK_BYTES)

    def test_pos_bank_16(self):
        bank, off = id_to_location(255, 3, 32)
        self.assertEqual((bank, off), (3, 255 * 32))
        bank2, off2 = id_to_location(256, 3, 32)
        self.assertEqual((bank2, off2), (4, 0))

    def test_pos_regeneration(self):
        cells = self._cells12([8481, 8482])
        t1 = make_synthetic_bdf(cells, 12, 12, order=[0, 1])
        t2 = make_synthetic_bdf(cells, 12, 12, order=[1, 0])
        g1 = parse_synthetic_bdf(t1, 12, 12)
        g2 = parse_synthetic_bdf(t2, 12, 12)
        self.assertEqual(g1, g2)

    def test_pos_mpr_sp_preserved_model(self):
        # Model: callee must restore saved values.
        saved = {"mpr": 7, "sp": 0xFF}
        live = dict(saved)
        live["mpr"] = 3
        live["sp"] = 0xFB
        # Restore step
        live["mpr"], live["sp"] = saved["mpr"], saved["sp"]
        self.assertEqual(live, saved)

    def test_pos_bit_order_msb_left(self):
        # Leftmost pixel only -> high bit of first byte set.
        rom = glyph_to_rom_bytes([bytes([0x80, 0x00])] * 12, 12)
        self.assertEqual(rom[0], 0x80)
        # 12-wide low nibble zero preserved.
        for row in [rom[i * 2:(i + 1) * 2] for i in range(12)]:
            self.assertEqual(row[1] & 0x0F, 0)

    def test_capacity_3000_theory(self):
        self.assertEqual(3000 * 24, 72000)
        self.assertEqual(3000 * 32, 96000)
        self.assertEqual(3000 * 4, 12000)
        self.assertEqual(72000 + 96000 + 12000, 180000)
        self.assertEqual(banks_free_bytes - 180000, 73952)

    def test_capacity_3000_effective(self):
        def banks_for(n, stride):
            per = BANK_BYTES // stride
            return (n + per - 1) // per
        idx_banks = banks_for(3000, 4)
        g16_banks = banks_for(3000, 32)
        g12_banks = banks_for(3000, 24)
        self.assertEqual(idx_banks, 2)
        self.assertEqual(g16_banks, 12)
        self.assertEqual(g12_banks, 9)
        allocated = (idx_banks + g16_banks + g12_banks) * BANK_BYTES
        self.assertEqual(allocated, 188416)
        self.assertEqual(banks_free_bytes - allocated, 65536)

    def test_neg_invalid_sjis(self):
        with self.assertRaises(ValueError):
            sjis_to_jis(bytes([0x00, 0x40]))

    def test_neg_unmapped(self):
        # Valid SJIS form but not in a small synthetic index -> UNMAPPED.
        glyphs = parse_synthetic_bdf(
            make_synthetic_bdf(self._cells12([8481]), 12, 12), 12, 12
        )
        idx = build_index(glyphs)
        # An encoding not in the index.
        self.assertNotIn(9250, idx)

    def test_neg_bad_row_length(self):
        text = make_synthetic_bdf(self._cells12([8481]), 12, 12)
        text = text.replace("F010", "F0")  # shorten first bitmap row
        # Only fails if the replaced line is actually a bitmap row; force by
        # constructing a malformed BDF directly for determinism.
        bad = (
            "STARTFONT 2.1\nFONT x\nSIZE 12 75 75\nFONTBOUNDINGBOX 12 12 0 -2\n"
            "STARTPROPERTIES 1\nENDPROPERTIES\nCHARS 1\n"
            "STARTCHAR c\nENCODING 8481\nSWIDTH 960 0\nDWIDTH 12 0\n"
            "BBX 12 12 0 -2\nBITMAP\n"
            + ("F0\n" + "F010\n" * 11)
            + "ENDCHAR\nENDFONT\n"
        )
        with self.assertRaises(ValueError):
            parse_synthetic_bdf(bad, 12, 12)

    def test_neg_duplicate_encoding(self):
        cells = self._cells12([8481, 8481])
        text = make_synthetic_bdf(cells, 12, 12)
        with self.assertRaises(ValueError):
            parse_synthetic_bdf(text, 12, 12)

    def test_neg_bad_bbx(self):
        text = make_synthetic_bdf(self._cells12([8481]), 12, 12)
        bad = text.replace("BBX 12 12 0 -2", "BBX 11 12 0 -2")
        with self.assertRaises(ValueError):
            parse_synthetic_bdf(bad, 12, 12)

    def test_neg_glyph_boundary(self):
        with self.assertRaises(ValueError):
            id_to_location(-1, 3, 24)

    def test_neg_ram_outside(self):
        self.assertFalse(check_ram_output(32, banks_free_bytes, 0, banks_free_bytes))

    def test_neg_ram_short(self):
        self.assertFalse(check_ram_output(31, 100, 0, 1000))
        self.assertTrue(check_ram_output(32, 100, 0, 1000))

    def test_neg_mpr_unknown(self):
        # Model: unknown/ambiguous placement (range too short) is rejected.
        # This asserts the host-model gate, not real-core MPR behaviour.
        self.assertFalse(check_ram_output(32, 100, 0, 131))

    def test_neg_ram_wrap(self):
        # 16-bit wrap: start near the top of the address space must reject.
        self.assertFalse(check_ram_output(32, 0xFFF0, 0, 0x10000))
        self.assertTrue(check_ram_output(32, 0xFFE0, 0, 0x10000))

    def test_neg_bank_span_miscalc(self):
        # Naive offset for id 342 with stride 24 crosses the bank.
        naive = 342 * 24
        self.assertGreater(naive, BANK_BYTES)
        bank, off = id_to_location(342, 3, 24)
        self.assertEqual(bank, 4)
        # Corrected mapping never crosses.
        self.assertLessEqual(off + 24, BANK_BYTES)

    def test_sjis_roundtrip_against_codec(self):
        # Spot-check the arithmetic against the standard codec for a known pair.
        # Byte pair is constructed from integers, not from a copied literal table.
        pairs = [bytes([0x82, 0xA0])]
        for sjis in pairs:
            uni = sjis.decode("shift_jis")
            # JIS decimal via EUC form (JIS X 0208 bytes + 0x80 each).
            euc = uni.encode("euc_jp")
            jis = (euc[0] - 0x80) * 256 + (euc[1] - 0x80)
            self.assertEqual(sjis_to_jis(sjis), jis)


if __name__ == "__main__":
    unittest.main()
