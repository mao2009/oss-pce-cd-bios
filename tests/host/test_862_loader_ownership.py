"""Issue #862 host fixture: PUBLIC loader vs shadow ownership/lifetime (G5 P1 Agent B).

Follow-up of #844 / docs/844-crshadow-survey.md (survey-only, L1-L3) and the
L5 run recorded in docs/862-loader-ownership-record.md. Synthetic only, no
emulator, no BIOS, no ROM, no reference JSR, and no ./rom/ content is read at
any point.

Scope (#862 = L5 + ownership; #861 owns the L4 desync RED run):
  * staging geometry: BUF $2200-$29FF, $5A fill via STA (ptr),Y (8 pages),
    then READ(6) sector transfer into the same BUF (indirect range writer);
  * shadow cells $2502/$2503/$2504 = BUF offsets $0302/$0303/$0304
    (arithmetic, SOURCE_CONFIRMED);
  * INFO / pre-check / BODY sequencing with last-sector-wins at IPLJMP;
  * loader-ownership-period decision: the loader owns BUF during staging, so
    the shadow is INVALID across any staging cycle (machine-checkable via
    shadow_valid() and sync_caller_ok() below);
  * marker-$A5-alone-is-not-proof (gate necessity vs sync sufficiency);
  * mitigation comparison lives in docs/862-loader-ownership-record.md; this
    file pins the code facts it rests on, including the forbidden absences
    (no loader store to $2502-$2504, no RAM move, no marker change, no
    auto-seed).

Evidence classes (same legend as #844): SOURCE_CONFIRMED (read from tracked
source/arithmetic), SYNTHETIC_REPRODUCED (host model here; real-core L5 run in
the companion integration module), HYPOTHESIS, UNKNOWN, UNOBSERVED.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOADER_SRC = ROOT / "tests/integration/ipl_loader_rom.s"
RECORD_DOC = ROOT / "docs/862-loader-ownership-record.md"

# ---- Address arithmetic (SOURCE_CONFIRMED pins) ----
BUF = 0x2200
SECTOR = 2048
BUF_END = BUF + SECTOR - 1  # $29FF
SHADOW_LO = 0x2502
MARKER = 0x2503
SHADOW_RSVD = 0x2504
IPLSTA_LO = 0x3000
FILL = 0x5A
SEEDED_MARKER = 0xA5
WORK_RAM_BANK = 0xF8  # MPR1 value mapping $2000-$3FFF to work RAM

# ---- Ownership/lifetime decision record (machine-checkable) ----
OWNER_STAGING = "loader"       # owns BUF $2200-$29FF during staging
OWNER_SHADOW_SYNC = "caller"   # owns $2502 image only inside a synced call window
INVALIDATING_EVENTS = ("staging_cycle", "family_publish", "reset_or_warm_handoff")
# Validity: a seeding store makes the shadow VALID; any invalidating event
# makes it INVALID. There is no other transition (no auto-seed, no timeout).
VALID = "VALID"
INVALID = "INVALID"


def shadow_valid(state, event):
    """Ownership-lifetime judge for the $2502/$2503 cells.

    state: VALID or INVALID. event: "seed" | one of INVALIDATING_EVENTS.
    Unknown events fail closed (ValueError), never guess VALID.
    """
    if event == "seed":
        return VALID
    if event in INVALIDATING_EVENTS:
        return INVALID
    raise ValueError("unknown lifetime event: %r" % (event,))


def sync_caller_ok(shadow, marker, real_cr, mpr1):
    """Machine-checkable synchronized-caller preconditions for a CR-family call.

    ALL must hold: marker == $A5 (gate necessity), shadow == real CR at entry
    (sync sufficiency), MPR1 == $F8 (work-RAM bank so $2502 is the owned cell).
    Returns True only when the ok path would publish the caller's true intent.
    """
    return marker == SEEDED_MARKER and shadow == real_cr and mpr1 == WORK_RAM_BANK


def stage_track(fill_value, sectors):
    """Host model of BUF offsets $0302-$0304 across one staging run.

    fill_value: byte written by the $5A pre-fill (SOURCE_CONFIRMED $5A).
    sectors: list of 2048-byte sectors in transfer order
             (INFO, pre-check, BODY[0..n-1]). Each transfer fully overwrites
             BUF, so the last sector wins. Returns the final triple.
    """
    triple = (fill_value, fill_value, fill_value)
    for sector in sectors:
        if len(sector) != SECTOR:
            raise ValueError("sector must be %d bytes" % SECTOR)
        triple = (sector[0x0302], sector[0x0303], sector[0x0304])
    return triple


def _loader_text():
    return LOADER_SRC.read_text()


class LoaderGeometryTests(unittest.TestCase):
    def test_buf_range_is_one_staging_sector(self):
        text = _loader_text()
        self.assertRegex(text, r"(?m)^BUF\s*=\s*\$2200\b")
        self.assertRegex(text, r"(?m)^\s*SECTOR\s*=\s*2048\b")
        self.assertEqual(BUF_END, 0x29FF)
        self.assertEqual(SECTOR, 2048)

    def test_shadow_cells_are_buf_offsets_0302_0304(self):
        for addr, want in ((SHADOW_LO, 0x0302), (MARKER, 0x0303),
                           (SHADOW_RSVD, 0x0304)):
            self.assertEqual(addr - BUF, want)
            self.assertTrue(BUF <= addr <= BUF_END)

    def test_shadow_logically_below_iplsta(self):
        for addr in (SHADOW_LO, MARKER, SHADOW_RSVD):
            self.assertLess(addr, IPLSTA_LO)


class StagingWriterTests(unittest.TestCase):
    """The loader is an indirect range writer over the shadow cells."""

    def test_prefill_is_5a_via_indirect_ptr_y_8_pages(self):
        text = _loader_text()
        # SOURCE_CONFIRMED: read_sector fills BUF with $5A before every transfer.
        self.assertIn("lda #$5A", text)
        self.assertIn("sta (ptr),y", text)
        self.assertRegex(text, r"(?m)^\s*ldx\s+#8\b")

    def test_sector_transfer_targets_buf_via_ptr(self):
        text = _loader_text()
        # read_sector resets ptr to BUF, then the DATA_IN loop stores via (ptr).
        self.assertRegex(text, r"(?m)^\s*sta\s+\(ptr\)\s*$")

    def test_staging_call_order_is_info_precheck_body(self):
        text = _loader_text()
        # reset: INFO read -> validate -> probe_last -> copy_body (SOURCE_CONFIRMED order).
        reset = text[text.index("\nreset:"):text.index("\nvalidate_info:")]
        order = [m.start() for m in
                 re.finditer(r"(?m)^\s*jsr\s+(read_sector|validate_info|probe_last|copy_body|verify_body|launch)\b",
                             reset)]
        names = [re.match(r"\s*jsr\s+(\w+)", reset[pos:]).group(1)
                 for pos in order]
        self.assertEqual(names, ["read_sector", "validate_info", "probe_last",
                                "copy_body", "verify_body", "launch"])

    def test_precheck_reads_final_body_sector(self):
        text = _loader_text()
        # probe_last: LBA = body + nsec - 1 (final body sector, SOURCE_CONFIRMED).
        probe = text[text.index("\nprobe_last:"):text.index("\ncopy_body:")]
        self.assertRegex(probe, r"(?m)adc\s+nsec\b")
        self.assertRegex(probe, r"(?m)sbc\s+#1\b")
        self.assertRegex(probe, r"(?m)^\s*jsr\s+read_sector\b")

    def test_body_loop_reads_every_sector_in_order(self):
        text = _loader_text()
        # copy_body: per-sector LBA = body + idx, then read_sector (SOURCE_CONFIRMED).
        body = text[text.index("\ncopy_body:"):text.index("\nverify_body:")]
        self.assertRegex(body, r"(?m)adc\s+idx\b")
        self.assertRegex(body, r"(?m)^\s*jsr\s+read_sector\b")
        self.assertRegex(body, r"(?m)inc\s+idx\b")

    def test_loader_never_stores_shadow_cells_directly(self):
        text = _loader_text()
        # The loader writes the shadow cells ONLY via the BUF range writer
        # (fill + sector transfer). No direct store to $2502/$2503/$2504 may
        # exist, including inside TEST-ONLY snapshot code.
        for cell in ("$2502", "$2503", "$2504"):
            for match in re.finditer(r"(?m)^\s*st[axyz]\s+.*\%s\b" % re.escape(cell), text):
                self.fail("loader must not store %s directly: %s"
                          % (cell, match.group(0).strip()))

    def test_l5_snapshot_code_is_ifdef_contained(self):
        text = _loader_text()
        # Every L5_ snapshot token must live inside an IPL_L5_SNAPSHOT region;
        # production builds (define off) carry zero snapshot bytes.
        lines = text.splitlines()
        depth = 0
        inside = False
        guarded = 0
        total = 0
        for line in lines:
            if re.match(r"\s*\.ifdef\s+IPL_L5_SNAPSHOT\s*$", line):
                depth += 1
                inside = True
                continue
            if re.match(r"\s*\.endif\b", line) and depth:
                depth -= 1
                inside = depth > 0
                continue
            if "L5_" in line:
                total += 1
                if inside:
                    guarded += 1
        self.assertGreater(total, 0, "expected L5 snapshot code in the loader")
        self.assertEqual(guarded, total,
                         "all L5_ lines must be inside #ifdef IPL_L5_SNAPSHOT")


class BufTrackingModelTests(unittest.TestCase):
    def _sector(self, triple, fill=0x00):
        sector = bytearray([fill]) * SECTOR
        sector[0x0302:0x0305] = bytes(triple)
        return bytes(sector)

    def test_fill_then_single_sector_last_wins(self):
        final = stage_track(FILL, [self._sector((0xC0, 0xC1, 0xC2))])
        self.assertEqual(final, (0xC0, 0xC1, 0xC2))

    def test_info_precheck_body_sequence_last_body_wins(self):
        info = self._sector((0xD0, 0xD1, 0xD2))
        pre = self._sector((0xC4, 0xC5, 0xC6))     # probe_last: final body sector
        bodies = [self._sector((0xC0 + i, 0xC1 + i, 0xC2 + i)) for i in range(5)]
        self.assertEqual(bodies[-1][0x0302:0x0305], bytes((0xC4, 0xC5, 0xC6)))
        final = stage_track(FILL, [info, pre] + bodies)
        self.assertEqual(final, (0xC4, 0xC5, 0xC6))
        # The pre-check snapshot already equals the final body sector's triple:
        # probe_last and the last copy iteration read the same LBA.
        self.assertEqual((pre[0x0302], pre[0x0303], pre[0x0304]), final)

    def test_fill_state_is_not_a_valid_shadow(self):
        # Pre-first-READ(6) the marker offset holds $5A fill: gate rejects.
        # (SYNTHETIC_REPRODUCED in the #844 fixture for every family slot.)
        self.assertNotEqual(FILL, SEEDED_MARKER)

    def test_bad_sector_size_fails_closed(self):
        with self.assertRaises(ValueError):
            stage_track(FILL, [b"\x00" * 16])


class SyncPreconditionTests(unittest.TestCase):
    def test_sync_call_satisfies_all_preconditions(self):
        self.assertTrue(sync_caller_ok(shadow=0x04, marker=0xA5, real_cr=0x04,
                                       mpr1=0xF8))

    def test_stale_shadow_fails_despite_a5_marker(self):
        # Marker-$A5-alone-is-not-proof: stale shadow + residue $A5 fails sync.
        self.assertFalse(sync_caller_ok(shadow=0xFF, marker=0xA5, real_cr=0x00,
                                        mpr1=0xF8))

    def test_unseeded_marker_fails(self):
        self.assertFalse(sync_caller_ok(shadow=0x04, marker=0x5A, real_cr=0x04,
                                        mpr1=0xF8))

    def test_wrong_mpr_bank_fails(self):
        # $2502 outside work RAM is not the owned cell (out of contract).
        self.assertFalse(sync_caller_ok(shadow=0x04, marker=0xA5, real_cr=0x04,
                                        mpr1=0x80))

    def test_every_single_condition_is_necessary(self):
        base = dict(shadow=0x04, marker=0xA5, real_cr=0x04, mpr1=0xF8)
        for key, bad in (("shadow", 0xFF), ("marker", 0x00),
                         ("real_cr", 0x00), ("mpr1", 0x00)):
            case = dict(base)
            case[key] = bad
            self.assertFalse(sync_caller_ok(**case), key)


class OwnershipLifetimeTests(unittest.TestCase):
    def test_seed_makes_shadow_valid(self):
        self.assertEqual(shadow_valid(INVALID, "seed"), VALID)

    def test_staging_cycle_invalidates(self):
        # Loader-ownership-period: any staging cycle (fill + transfers)
        # overwrites the cells, so a previously seeded shadow is INVALID.
        self.assertEqual(shadow_valid(VALID, "staging_cycle"), INVALID)

    def test_family_publish_and_reset_invalidate(self):
        self.assertEqual(shadow_valid(VALID, "family_publish"), INVALID)
        self.assertEqual(shadow_valid(VALID, "reset_or_warm_handoff"), INVALID)

    def test_seed_after_staging_restores_validity(self):
        self.assertEqual(shadow_valid(shadow_valid(VALID, "staging_cycle"),
                                      "seed"), VALID)

    def test_unknown_event_fails_closed(self):
        with self.assertRaises(ValueError):
            shadow_valid(VALID, "timeout")
        with self.assertRaises(ValueError):
            shadow_valid(VALID, "")

    def test_owners_are_explicit(self):
        self.assertEqual(OWNER_STAGING, "loader")
        self.assertEqual(OWNER_SHADOW_SYNC, "caller")


class MitigationRecordTests(unittest.TestCase):
    """The decision record must name every compared option and the verdict."""

    def test_record_exists_and_covers_all_options(self):
        text = RECORD_DOC.read_text()
        for option in ("loader-ownership-period", "shadow-ownership",
                       "marker-validity", "reject-if-unsync", "RAM-relocation"):
            self.assertIn(option, text, option)
        for verdict in ("adopted", "FOLLOW_UP", "rejected"):
            self.assertIn(verdict, text, verdict)

    def test_record_forbids_groundless_changes(self):
        text = RECORD_DOC.read_text()
        for phrase in ("no RAM move", "no marker change", "no auto-seed"):
            self.assertIn(phrase, text, phrase)


if __name__ == "__main__":
    unittest.main()
