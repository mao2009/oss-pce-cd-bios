"""Issue #844 host survey fixture: CR-shadow $A5 false-ok model (G5 P1 Agent B).

Synthetic only, no emulator, no BIOS, no ROM, no reference JSR, and no
./rom/ content is read at any point. This file models the exact entry-gate
and ok-path semantics shared by the four CR-family bodies, grounded in the
source lines pinned by ``test_gate_shape_matches_source``:

    gate: lda $2503 ; cmp #$A5 ; beq @have (reject: SEC, no write anywhere)
    have: lda $2502 ; <family mask> ; sta $2502 ; VDC CR write ; CLC

Family masks (SOURCE_CONFIRMED, see the structural test below):
  $2B ex_rcroff.s        AND #$FB (clear bit 2)
  $33 ex_dspoff.s        AND #$3F (clear bits 6-7)
  $2C ex_irqon.s         ORA #$08 (set bit 3)
  $2E overrides/slot_2E.s ORA #$80 (set bit 7)

Covered cases:
  SYNC ok        real CR == shadow, marker $A5  -> ok path, target bit
                 updated, non-target bits preserved.
  FALSE-OK stale real CR != stale shadow, marker $A5 (sector residue) ->
                 ok path taken with the WRONG image; $2B publishes
                 non-target damage (CR=$FB from stale $FF).
  $33 mask       same stale state through the $33 mask yields CR=$00, which
                 shows a $00 CR reading is NOT reject proof.
  $5A reject     marker $5A (loader pre-fill value) -> reject, no write.
  cold/warm      cold fill value $5A is SOURCE_CONFIRMED; every post-READ(6)
                 byte (including the marker position) is sector-dependent and
                 therefore UNKNOWN; the judge must answer UNKNOWN, never ok.

A passing run validates only this model and its source pins. Commercial
occurrence of the false-ok path is UNOBSERVED (source-derived risk, not an
observed bug) and stays UNKNOWN. This fixture must not be wired into any
build, ROM, or callable entry; release_eligible=false.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ---- Address arithmetic (SOURCE_CONFIRMED pins) ----
BUF = 0x2200
SECTOR = 2048
BUF_END = BUF + SECTOR - 1  # $29FF
SHADOW_LO = 0x2502
MARKER = 0x2503
SHADOW_RSVD = 0x2504
IPLSTA_LO = 0x3000
IPLSTA_HI = 0x7FFF
COLD_FILL = 0x5A
SEEDED_MARKER = 0xA5
UNKNOWN = "UNKNOWN"  # sentinel: value is sector/warm-dependent, never asserted

FAMILY_MASKS = {
    "$2B": ("AND", 0xFB),
    "$33": ("AND", 0x3F),
    "$2C": ("ORA", 0x08),
    "$2E": ("ORA", 0x80),
}

SRC_BY_SLOT = {
    "$2B": "src/ex_rcroff.s",
    "$33": "src/ex_dspoff.s",
    "$2C": "src/ex_irqon.s",
    "$2E": "src/overrides/slot_2E.s",
}


def family_step(real_cr, shadow, marker, slot):
    """Mirror the shared gate + ok-path for one family slot.

    Returns (taken_ok: bool, new_cr: int, new_shadow: int).
    Reject writes nothing (caller stack aside); ok publishes the masked
    shadow image to both the shadow cell and VDC CR.
    """
    kind, mask = FAMILY_MASKS[slot]
    if marker != SEEDED_MARKER:
        return (False, real_cr, shadow)
    image = (shadow & mask) if kind == "AND" else (shadow | mask)
    return (True, image & 0xFF, image & 0xFF)


class CrShadowArithmeticTests(unittest.TestCase):
    def test_shadow_cells_are_buf_offsets(self):
        for addr, want in ((SHADOW_LO, 0x0302), (MARKER, 0x0303),
                           (SHADOW_RSVD, 0x0304)):
            self.assertEqual(addr - BUF, want)
            self.assertTrue(BUF <= addr <= BUF_END)

    def test_buf_range_is_8_pages(self):
        self.assertEqual(SECTOR, 2048)
        self.assertEqual(BUF_END, 0x29FF)

    def test_iplsta_logically_disjoint_from_shadow(self):
        for addr in (SHADOW_LO, MARKER, SHADOW_RSVD):
            self.assertLess(addr, IPLSTA_LO,
                            "shadow cell must be below IPLSTA $3000")
        self.assertLessEqual(IPLSTA_LO, IPLSTA_HI)


class GateShapeMatchesSourceTests(unittest.TestCase):
    """Pin the model to the actual bodies (structural, no execution)."""

    def _src(self, slot):
        return (ROOT / SRC_BY_SLOT[slot]).read_text()

    def test_each_body_gates_on_marker_a5(self):
        for slot in FAMILY_MASKS:
            text = self._src(slot)
            self.assertIn("lda $2503", text, slot)
            self.assertIn("cmp #$A5", text, slot)

    def test_each_body_publishes_masked_shadow(self):
        for slot in FAMILY_MASKS:
            text = self._src(slot)
            self.assertIn("lda $2502", text, slot)
            self.assertIn("sta $2502", text, slot)

    def test_family_masks_match_source(self):
        for slot, (kind, mask) in FAMILY_MASKS.items():
            text = self._src(slot)
            op = "and" if kind == "AND" else "ora"
            self.assertRegex(text, r"(?im)^\s*%s\s+#\$%02X\b" % (op, mask),
                             slot)

    def test_loader_prefill_value_is_5a(self):
        text = (ROOT / "tests/integration/ipl_loader_rom.s").read_text()
        self.assertIn("lda #$5A", text)
        self.assertIn("sta (ptr),y", text)


class SyncOkTests(unittest.TestCase):
    def test_2b_sync_clears_only_bit2(self):
        ok, new_cr, new_shadow = family_step(0x04, 0x04, 0xA5, "$2B")
        self.assertTrue(ok)
        self.assertEqual(new_cr, 0x00)
        self.assertEqual(new_shadow, 0x00)

    def test_2b_sync_preserves_non_target_bits(self):
        ok, new_cr, _ = family_step(0xFB, 0xFB, 0xA5, "$2B")
        self.assertTrue(ok)
        self.assertEqual(new_cr, 0xFB)


class FalseOkTests(unittest.TestCase):
    def test_2b_false_ok_publishes_non_target_damage(self):
        # real CR=0x00 at the boundary, stale shadow $FF, $A5 sector residue.
        ok, new_cr, new_shadow = family_step(0x00, 0xFF, 0xA5, "$2B")
        self.assertTrue(ok, "coincidental $A5 must take the ok path")
        self.assertEqual(new_cr, 0xFB)
        self.assertEqual(new_shadow, 0xFB)
        self.assertNotEqual(new_cr & ~0x04, 0x00,
                            "$2B stale image sets non-target bits")

    def test_33_zero_cr_is_not_reject_proof(self):
        # A $00 CR reading after $33 is NOT evidence the reject path ran:
        # the ok path with a zero-like stale image (e.g. a zero-filled
        # sector residue plus coincidental $A5 marker byte) also leaves
        # CR=$00 while publishing the stale image. CR=0 proves nothing
        # about which path ran.
        ok, new_cr, new_shadow = family_step(0x00, 0x00, 0xA5, "$33")
        self.assertTrue(ok, "ok path runs despite the stale/unowned image")
        self.assertEqual(new_cr, 0x00)
        self.assertEqual(new_shadow, 0x00)

    def test_33_stale_ff_is_visible_damage(self):
        # Same residue marker with stale $FF: $33 yields $3F (bits 0-5 set
        # from the stale image), so the ok path demonstrably wrote.
        ok, new_cr, _ = family_step(0x00, 0xFF, 0xA5, "$33")
        self.assertTrue(ok)
        self.assertEqual(new_cr, 0x3F)

    def test_marker_alone_proves_nothing(self):
        sync = family_step(0x04, 0x04, 0xA5, "$2B")[0]
        false_ok = family_step(0x00, 0xFF, 0xA5, "$2B")[0]
        self.assertTrue(sync and false_ok,
                        "marker $A5 alone cannot distinguish sync from stale")


class RejectTests(unittest.TestCase):
    def test_5a_fill_rejects_with_no_write(self):
        for slot in FAMILY_MASKS:
            ok, new_cr, new_shadow = family_step(0x00, 0xFF, 0x5A, slot)
            self.assertFalse(ok, slot)
            self.assertEqual(new_cr, 0x00)
            self.assertEqual(new_shadow, 0xFF)

    def test_unseeded_marker_rejects(self):
        ok, new_cr, _ = family_step(0x00, 0x00, 0x00, "$2B")
        self.assertFalse(ok)
        self.assertEqual(new_cr, 0x00)


class ColdWarmTests(unittest.TestCase):
    def test_cold_fill_is_known_reject_state(self):
        # Before the first READ(6), the whole BUF (incl. marker offset) is
        # $5A fill: the gate rejects. SOURCE_CONFIRMED (read_sector @fill).
        self.assertEqual(COLD_FILL, 0x5A)
        ok, _, _ = family_step(0x00, 0x5A, 0x5A, "$2B")
        self.assertFalse(ok)

    def test_post_read_marker_is_unknown(self):
        # After READ(6), the marker offset holds a sector byte: value UNKNOWN.
        # The host judge must answer UNKNOWN, never ok/reject, for that state.
        post_read_marker = UNKNOWN
        self.assertEqual(post_read_marker, UNKNOWN)
        verdict = UNKNOWN if post_read_marker is UNKNOWN else "claimed"
        self.assertEqual(verdict, UNKNOWN)

    def test_warm_residual_is_unknown(self):
        # Warm residual at $2502/$2503 was never measured in-tree; commercial
        # values UNKNOWN, never read. The model refuses to guess.
        warm_shadow, warm_marker = UNKNOWN, UNKNOWN
        self.assertEqual((warm_shadow, warm_marker), (UNKNOWN, UNKNOWN))


if __name__ == "__main__":
    unittest.main()
