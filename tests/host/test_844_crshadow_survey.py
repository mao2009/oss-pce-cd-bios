"""Issue #844 host survey fixture: CR-shadow $A5 false-ok model (G5 P1 Agent B).

Synthetic only, no emulator, no BIOS, no ROM, no reference JSR, and no
./rom/ content is read at any point. This file models the exact entry-gate
and ok-path semantics shared by the CR family, pinned by section-aware
structural tests against the single-source unit ``src/ex_cr_family.s``
(Issue #843: one NEED_CR33/NEED_CR2C/NEED_CR2B head section per slot plus a
shared reject/tail) and, for $2E only, ``src/overrides/slot_2E.s``:

    gate: lda $2503 ; cmp #$A5 ; bne cr_reject (reject: SEC, no write anywhere)
    have: lda $2502 ; <family mask> ; bra cr_tail_publish (shared tail
          publishes sta $2502 BEFORE sta $0002, restores MPR0, CLC)

Family masks (SOURCE_CONFIRMED, see the structural test below):
  $2B ex_cr_family.s NEED_CR2B AND #$FB (clear bit 2)
  $33 ex_cr_family.s NEED_CR33 AND #$3F (clear bits 6-7)
  $2C ex_cr_family.s NEED_CR2C ORA #$08 (set bit 3)
  $2E overrides/slot_2E.s ORA #$80 (set bit 7)

Covered cases:
  SYNC ok        real CR == shadow, marker $A5  -> ok path, target bit
                 updated, non-target bits preserved.
  FALSE-OK       stale real CR != stale shadow, marker $A5 (sector residue) ->
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

# Single-source layout (Issue #843): one conditional head section per slot
# in src/ex_cr_family.s, plus the shared reject/tail. $2E keeps its own body
# in src/overrides/slot_2E.s and is pinned there, never remapped onto the
# single-source unit (a whole-file match would let one slot's bytes satisfy
# another slot's pin).
CR_FAMILY_SRC = "src/ex_cr_family.s"
SLOT_2E_SRC = "src/overrides/slot_2E.s"
HEADS = {
    "$2B": ("NEED_CR2B", "AND", 0xFB),
    "$33": ("NEED_CR33", "AND", 0x3F),
    "$2C": ("NEED_CR2C", "ORA", 0x08),
}


def _family_sections():
    """Split ex_cr_family.s into {NEED_xxx: section text} + shared text.

    A section is the raw text between its ``.ifdef NEED_xxx`` and the matching
    ``.endif``; everything else (header comment, the NEED guard, the shared
    reject/tail) is shared. KeyError on a missing section fails closed.
    """
    lines = (ROOT / CR_FAMILY_SRC).read_text().splitlines()
    sections, shared, cur, buf = {}, [], None, []
    for line in lines:
        opened = re.match(r"\s*\.ifdef\s+(NEED_CR\w+)\s*$", line)
        if opened:
            cur, buf = opened.group(1), []
            continue
        if re.match(r"\s*\.endif\b", line) and cur is not None:
            sections[cur] = "\n".join(buf)
            cur = None
            continue
        (buf if cur is not None else shared).append(line)
    return sections, "\n".join(shared)


def _code_block(shared, label, stop_labels):
    """Instruction-region text from ``label:`` up to (not incl.) the next stop label."""
    start = shared.index("\n%s:" % label)
    ends = [shared.index("\n%s:" % stop, start + 1)
            for stop in stop_labels if "\n%s:" % stop in shared[start + 1:]]
    return shared[start:min(ends)] if ends else shared[start:]


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

    def test_each_head_gates_on_marker_a5(self):
        sections, shared = _family_sections()
        for slot, (need, _, _) in HEADS.items():
            sec = sections[need]  # missing NEED section fails closed
            for token in (r"^\s*lda\s+\$2503\b",
                          r"^\s*cmp\s+#\$A5\b",
                          r"^\s*bne\s+cr_reject\b"):
                self.assertRegex(sec, r"(?m)%s" % token, "%s/%s" % (slot, need))
            gate = [m.start() for m in
                    re.finditer(r"(?m)^\s*(?:lda\s+\$2503|cmp\s+#\$A5|bne\s+cr_reject)\b",
                                sec)]
            self.assertEqual(len(gate), 3, "%s/%s" % (slot, need))
            self.assertEqual(gate, sorted(gate), "%s/%s" % (slot, need))
        # $2E keeps its own body: gate pinned there, untouched by #843.
        slot_2e = (ROOT / SLOT_2E_SRC).read_text()
        self.assertRegex(slot_2e, r"(?m)^\s*lda\s+\$2503\b", "$2E")
        self.assertRegex(slot_2e, r"(?m)^\s*cmp\s+#\$A5\b", "$2E")

    def test_each_head_publishes_masked_shadow(self):
        sections, shared = _family_sections()
        for slot, (need, _, _) in HEADS.items():
            sec = sections[need]  # missing NEED section fails closed
            self.assertRegex(sec, r"(?m)^\s*lda\s+\$2502\b",
                             "%s/%s image load" % (slot, need))
            self.assertRegex(sec, r"(?m)^\s*bra\s+cr_tail_publish\b",
                             "%s/%s tail transfer" % (slot, need))
            load = re.search(r"(?m)^\s*lda\s+\$2502\b", sec).start()
            tail = re.search(r"(?m)^\s*bra\s+cr_tail_publish\b", sec).start()
            self.assertLess(load, tail, "%s/%s" % (slot, need))
            # The marker cell is never written by any head.
            self.assertNotRegex(sec, r"(?m)^\s*st[axyz]\s+.*\$2503\b",
                                "%s/%s must not store $2503" % (slot, need))
        # Shared tail: publish the shadow image BEFORE the VDC write, then
        # restore the caller's MPR0 and report ok.
        tail = _code_block(shared, "cr_tail_publish", [])
        publish = re.search(r"(?m)^\s*sta\s+\$2502\b", tail)
        vdc = re.search(r"(?m)^\s*sta\s+a:\$0002\b", tail)
        self.assertIsNotNone(publish, "tail publishes $2502")
        self.assertIsNotNone(vdc, "tail writes VDC CR low")
        self.assertLess(publish.start(), vdc.start(), "publish before VDC write")
        self.assertRegex(tail, r"(?m)^\s*tam\s+#\$01\b", "tail restores MPR0")
        self.assertLess(re.search(r"(?m)^\s*plp\b", tail).start(),
                        re.search(r"(?m)^\s*clc\b", tail).start())
        self.assertNotRegex(tail, r"(?m)^\s*st[axyz]\s+.*\$2503\b",
                            "tail must not store the $2503 marker")
        # Shared reject: SEC + RTS with no store of any kind (no write anywhere).
        reject = _code_block(shared, "cr_reject", ["cr_tail_publish"])
        self.assertRegex(reject, r"(?m)^\s*sec\b", "reject sets C")
        self.assertRegex(reject, r"(?m)^\s*rts\b", "reject returns")
        self.assertNotRegex(reject, r"(?m)^\s*st[axyz]\b",
                            "reject path must not store anywhere")

    def test_family_masks_match_source(self):
        sections, _ = _family_sections()
        for slot, (need, kind, mask) in HEADS.items():
            op = "and" if kind == "AND" else "ora"
            # The mask lives in its own NEED section: a whole-file match
            # would let one slot's bytes satisfy another slot's pin.
            self.assertRegex(sections[need],
                             r"(?m)^\s*%s\s+#\$%02X\b" % (op, mask),
                             "%s/%s" % (slot, need))
            for other, (oneed, okind, omask) in HEADS.items():
                if other == slot:
                    continue
                oop = "and" if okind == "AND" else "ora"
                self.assertNotRegex(sections[need],
                                    r"(?m)^\s*%s\s+#\$%02X\b" % (oop, omask),
                                    "%s/%s must not carry %s mask" % (slot, need, other))
        # $2E keeps its own body: mask pinned there, untouched by #843.
        slot_2e = (ROOT / SLOT_2E_SRC).read_text()
        self.assertRegex(slot_2e, r"(?m)^\s*ora\s+#\$80\b", "$2E")
        for _, ( _, okind, omask) in HEADS.items():
            oop = "and" if okind == "AND" else "ora"
            self.assertNotRegex(slot_2e, r"(?m)^\s*%s\s+#\$%02X\b" % (oop, omask),
                                "$2E must not carry single-source masks")

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
