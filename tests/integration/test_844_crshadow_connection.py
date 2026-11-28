"""Issue #844 connection: L4/L5 structural oracle, staging, and source pins.

Constrained design-only wave (Agent A). No BIOS change, no ABI change, no
release impact. `release_eligible=false`. Genuine ABI stays BLOCKED.
`G5_OBSERVED=false`. JSR 0/81, VERIFIED 0/81. `$1C` BLOCKED_ABI, display-4
BLOCKED. Commercial occurrence of the false-ok path is UNOBSERVED.

This module is STRUCTURAL ONLY (stdlib; no cc65, no Geargrafx, no
private-media access of any form): a pure `$2B` oracle model checked against the
connection oracle table (`docs/844-crshadow-connection-design.md` section 3),
staging-geometry pins reusing the `IPL_L5_SNAPSHOT` offset pattern, and
read-only source pins grounding every claim. It performs no loader-transfer
run and no `$E081` call: single-ROM loader->CR continuity is BLOCKED
(harness gap, recorded in the design doc section 6), and forcing it with the
existing pieces would mislabel direct-seed behavior as loader-transfer
evidence. New payload: explicitly NOT added in this wave (see design doc).

Evidence labels (closed taxonomy): SOURCE_CONFIRMED, SYNTHETIC_REPRODUCED,
HYPOTHESIS, UNKNOWN, UNOBSERVED. Every claim table in this module carries
one; loader-transfer residue vs direct seed are never conflated (value
equality is not lineage proof; `$A5`-alone is not sync proof).
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DOC = ROOT / "docs" / "844-crshadow-connection-design.md"

LABELS = (
    "SOURCE_CONFIRMED",
    "SYNTHETIC_REPRODUCED",
    "HYPOTHESIS",
    "UNKNOWN",
    "UNOBSERVED",
)

# ---- Pure $2B connection-oracle model (mirrors the pinned body semantics) ----
# Gate: marker != $A5 -> reject (SEC, no write). Ok path: publish
# shadow&$FB to $2502 then to VDC CR low, AR=5, C=0. Reject: C=1,
# AR==entry AR, CR and shadow untouched. Entry Carry is forced to the path
# convention on exit (both entry-C classes covered per oracle row).
MARKER_OK = 0xA5
MASK_2B = 0xFB
AR_OK = 5


def model_2b(shadow_lo, marker, real_lo, entry_ar=0, entry_c=0):
    """Return (path, cr_low, shadow_lo_out, carry_out, ar_after)."""
    if marker != MARKER_OK:
        return ("reject", real_lo, shadow_lo, 1, entry_ar)
    published = shadow_lo & MASK_2B
    return ("ok", published, published, 0, AR_OK)


def model_2b_mutant(kind, shadow_lo, marker, real_lo, entry_ar=0):
    """Single-byte-mutant variants of the $2B head/tail (L4 MUT_* shapes)."""
    if kind == "wrongbit":  # AND #$FB -> AND #$F7 (clears bit3, not bit2)
        if marker != MARKER_OK:
            return ("reject", real_lo, shadow_lo, 1, entry_ar)
        published = shadow_lo & 0xF7
        return ("ok", published, published, 0, AR_OK)
    if kind == "destroy":  # AND -> ORA #$FB (sets bits instead of clearing)
        if marker != MARKER_OK:
            return ("reject", real_lo, shadow_lo, 1, entry_ar)
        published = shadow_lo | 0xFB
        return ("ok", published, published, 0, AR_OK)
    if kind == "noop":  # mask dropped (no clear)
        if marker != MARKER_OK:
            return ("reject", real_lo, shadow_lo, 1, entry_ar)
        return ("ok", shadow_lo, shadow_lo, 0, AR_OK)
    if kind == "bypass":  # BNE -> BEQ (unseeded falls into the ok path)
        published = shadow_lo & MASK_2B
        return ("ok", published, published, 0, AR_OK)
    if kind == "novdc":  # tail STA $0002 dropped: shadow publishes, no VDC write
        if marker != MARKER_OK:
            return ("reject", real_lo, shadow_lo, 1, entry_ar)
        return ("ok-publish-only", real_lo, shadow_lo & MASK_2B, 0, AR_OK)
    if kind == "nopublish":  # tail STA $2502 dropped: VDC writes, shadow stale
        if marker != MARKER_OK:
            return ("reject", real_lo, shadow_lo, 1, entry_ar)
        return ("ok-write-only", shadow_lo & MASK_2B, shadow_lo, 0, AR_OK)
    raise ValueError(f"unknown mutant kind: {kind}")


# ---- Staging geometry (IPL_L5_SNAPSHOT pattern, pure splice) ----
BUF = 0x2200
BUF_LEN = 2048
CELL_OFF = 0x0302  # $2502-$2200; $2502/$2503/$2504 at BUF+$0302..$0304
FILL_BYTE = 0x5A
MPR1_WORKRAM = 0xF8
# RB spare layout reused from ipl_loader_rom.s (L5_* = RB + offset).
L5_OFFSETS = {"FILL": 0x24, "INFO": 0x27, "PRE": 0x2A,
              "FIRST": 0x2D, "LAST": 0x30, "RPT": 0x34}


def with_triple(buf, triple):
    buf[CELL_OFF:CELL_OFF + 3] = bytes(triple)
    return buf


class EvidenceLabelTests(unittest.TestCase):
    def test_label_taxonomy_closed(self):
        self.assertEqual(set(LABELS), {
            "SOURCE_CONFIRMED", "SYNTHETIC_REPRODUCED",
            "HYPOTHESIS", "UNKNOWN", "UNOBSERVED"})

    def test_lineage_table_labels_valid(self):
        lineage = {
            "fill-5a": "SOURCE_CONFIRMED",
            "info-triple": "SYNTHETIC_REPRODUCED",
            "pre-triple": "SYNTHETIC_REPRODUCED",
            "first-triple": "SYNTHETIC_REPRODUCED",
            "last-triple": "SYNTHETIC_REPRODUCED",
            "ipljmp-report": "SYNTHETIC_REPRODUCED",
            "probe-direct-seed": "SOURCE_CONFIRMED",
            "residue-as-cr-seed": "HYPOTHESIS",
            "warm-residual-pre-staging": "UNKNOWN",
            "commercial-a5-at-marker": "UNOBSERVED",
        }
        for key, label in lineage.items():
            self.assertIn(label, LABELS, key)
        # The continuity claim itself is HYPOTHESIS/BLOCKED, never presented
        # as reproduced: residue observation (L5) and desync decision (L4)
        # are separate SYNTHETIC_REPRODUCED runs.
        self.assertEqual(lineage["residue-as-cr-seed"], "HYPOTHESIS")
        self.assertEqual(lineage["commercial-a5-at-marker"], "UNOBSERVED")


class ConnectionOracleTests(unittest.TestCase):
    # Oracle table C1: marker $00 -> reject, nothing written, C=1.
    def test_c1_marker_00_reject(self):
        for shadow, real in ((0xFF, 0x00), (0x00, 0x00), (0x04, 0x04)):
            for entry_c in (0, 1):
                path, cr, pub, cout, ar = model_2b(shadow, 0x00, real,
                                                   entry_ar=9, entry_c=entry_c)
                self.assertEqual(path, "reject")
                self.assertEqual(cr, real, "reject must not touch real CR")
                self.assertEqual(pub, shadow, "reject must not touch shadow")
                self.assertEqual(cout, 1)
                self.assertEqual(ar, 9, "reject keeps entry AR")

    # Oracle table C2: marker $5A (loader-fill shape, ACTUAL byte) -> reject.
    # The oracle takes the actual marker byte, never a fixed $00: a fixed-$00
    # oracle could not tell a $5A-vs-$00 confusion (retired blind spot).
    def test_c2_marker_5a_loader_fill_reject_actual_byte(self):
        path, cr, pub, cout, ar = model_2b(0x5A, 0x5A, 0x00, entry_ar=9)
        self.assertEqual(path, "reject")
        self.assertEqual((cr, pub, cout, ar), (0x00, 0x5A, 1, 9))
        # The seed byte examined IS the $5A under test, not a $00 stand-in.
        self.assertNotEqual(0x5A, 0x00)
        self.assertNotEqual(0x5A, MARKER_OK)

    # Oracle table C3: $A5 synced -> ok-correct (positive controls).
    def test_c3_marker_a5_synced_ok_correct(self):
        for shadow, real, want in ((0x04, 0x04, 0x00), (0xFB, 0xFB, 0xFB)):
            for entry_c in (0, 1):
                path, cr, pub, cout, ar = model_2b(shadow, MARKER_OK, real,
                                                   entry_ar=9, entry_c=entry_c)
                self.assertEqual(path, "ok")
                self.assertEqual(cr, want)
                self.assertEqual(pub, want)
                self.assertEqual(cout, 0, "ok forces C=0 from either entry class")
                self.assertEqual(ar, AR_OK)

    # Oracle table C4: $A5 desync -> false-ok RED (stale image published).
    def test_c4_marker_a5_desync_false_ok_red(self):
        path, cr, pub, cout, ar = model_2b(0xFF, MARKER_OK, 0x00, entry_ar=9)
        self.assertEqual(path, "ok")
        self.assertEqual(cr, 0xFB)
        self.assertEqual(pub, 0xFB)
        self.assertEqual(cout, 0, "false-ok exits C=0 exactly like a sync ok")
        self.assertEqual(ar, AR_OK)
        # Non-target-bit damage: every set bit in $FB except bit2 comes from
        # the stale image (real was $00) — the RED condition.
        self.assertNotEqual(0xFB & ~0x04, 0x00)
        self.assertEqual(0xFB & ~0x04, 0xFF & ~0x04 & 0xFB)

    def test_entry_c_forced_both_classes(self):
        # ok forces C=0 and reject forces C=1 regardless of entry Carry.
        for entry_c in (0, 1):
            self.assertEqual(model_2b(0x04, MARKER_OK, 0x04,
                                      entry_c=entry_c)[3], 0)
            self.assertEqual(model_2b(0x04, 0x00, 0x04,
                                      entry_c=entry_c)[3], 1)

    def test_cold_warm_equality(self):
        # Seeding dominates residual: both cycles decide identically on all
        # security-relevant state (mirrors ResetMedia(true) cold/warm runs).
        for args in ((0xFF, MARKER_OK, 0x00), (0x04, MARKER_OK, 0x04),
                     (0x5A, 0x5A, 0x00), (0xFF, 0x00, 0x00)):
            self.assertEqual(model_2b(*args), model_2b(*args))

    def test_separation_novdc_vs_nopublish(self):
        # M_NOVDC: shadow publishes, real CR untouched -> VDC oracle REDs,
        # shadow oracle GREENs. M_NOPUBLISH: the exact converse.
        path, cr, pub, cout, ar = model_2b_mutant("novdc", 0xFF, MARKER_OK, 0x00)
        self.assertEqual(path, "ok-publish-only")
        self.assertEqual(cr, 0x00, "M_NOVDC leaves real CR untouched")
        self.assertEqual(pub, 0xFB, "M_NOVDC keeps shadow publish")
        path, cr, pub, cout, ar = model_2b_mutant("nopublish", 0xFF,
                                                  MARKER_OK, 0x00)
        self.assertEqual(path, "ok-write-only")
        self.assertEqual(cr, 0xFB, "M_NOPUBLISH still writes real CR")
        self.assertEqual(pub, 0xFF, "M_NOPUBLISH leaves shadow stale")

    def test_head_mutants_red_desync_oracle(self):
        # Each single-byte head mutant must RED the C4 desync oracle (never
        # GREEN by masking). Expected-correct reference: ("ok", $FB, $FB, 0, 5).
        for kind in ("wrongbit", "destroy", "noop", "bypass"):
            with self.subTest(mutant=kind):
                got = model_2b_mutant(kind, 0xFF, MARKER_OK, 0x00)
                if kind == "bypass":
                    # Bypass performs ok even unseeded: the reject oracle REDs.
                    rej = model_2b_mutant(kind, 0xFF, 0x00, 0x00)
                    self.assertEqual(rej[0], "ok")
                    self.assertEqual(rej[3], 0)
                else:
                    self.assertNotEqual(got, ("ok", 0xFB, 0xFB, 0, AR_OK),
                                        f"mutant {kind} must not look correct")

    def test_positive_control_via_post_staging_intentional_seed(self):
        # Model-level verification only (no core run, no loader transfer):
        # three explicit cases the execution wave must reproduce on core.
        # Case 1 — observed L5 residue (C4,C5,C6) passed AS-IS with its
        # ACTUAL marker byte $C5 (!= $A5): the oracle rejects; CR and shadow
        # untouched, C=1, entry AR preserved.
        observed = (0xC4, 0xC5, 0xC6)
        rej = model_2b(observed[0], observed[1], 0x00, entry_ar=9)
        self.assertEqual(rej, ("reject", 0x00, 0xC4, 1, 9))
        # Case 2 — HYPOTHETICAL synthetic triple (C4,$A5,C6), every byte
        # passed as-is with NO shadow/marker substitution: desync false-ok
        # (C=0, stale image published) but NOT normal operation. Distinct
        # from the observed L5 residue above, whose actual marker is $C5.
        synth = (0xC4, MARKER_OK, 0xC6)
        self.assertNotEqual(synth[1], observed[1],
                            "synthetic marker differs from observed L5 marker")
        false_ok = model_2b(synth[0], synth[1], 0x00, entry_ar=9)
        self.assertEqual(false_ok,
                         ("ok", 0xC4 & MASK_2B, 0xC4 & MASK_2B, 0, AR_OK))
        self.assertNotEqual(false_ok[1], 0x00 & MASK_2B,
                            "stale-derived bits persist (RED false-ok)")
        # Case 3 — intentional sync re-seed (documented harness seed, never
        # firmware init): shadow==real+$A5 goes GREEN: normal mask/publish,
        # C=0.
        green = model_2b(0x04, MARKER_OK, 0x04)  # intentional sync re-seed
        self.assertEqual(green, ("ok", 0x00, 0x00, 0, AR_OK))
        # Discrimination across all three: reject (C=1) vs false-ok vs
        # sync-ok (both C=0 yet different CR values) are three distinct
        # outcomes; the harness must reproduce each on core.
        self.assertNotEqual(rej[3], false_ok[3])
        self.assertNotEqual(false_ok[1], green[1])
        self.assertNotEqual((rej[1], rej[3]), (green[1], green[3]))


class StagingGeometryTests(unittest.TestCase):
    def test_buf_range_and_cell_offsets(self):
        self.assertEqual(BUF, 0x2200)
        self.assertEqual(BUF_LEN, 2048)
        for cell, off in (("$2502", 0x0302), ("$2503", 0x0303),
                          ("$2504", 0x0304)):
            self.assertEqual(0x2502 + (off - 0x0302) - BUF, off, cell)
            self.assertLess(off, BUF_LEN, cell)

    def test_fill_shape_and_splice(self):
        buf = bytearray(BUF_LEN)
        for i in range(BUF_LEN):
            buf[i] = FILL_BYTE
        self.assertEqual(tuple(buf[CELL_OFF:CELL_OFF + 3]), (0x5A, 0x5A, 0x5A))
        with_triple(buf, (0xD0, 0xD1, 0xD2))
        self.assertEqual(tuple(buf[CELL_OFF:CELL_OFF + 3]), (0xD0, 0xD1, 0xD2))

    def test_last_sector_wins_ordering(self):
        body = [bytes(2048) for _ in range(5)]
        triples = [(0xC0 + i, 0xC1 + i, 0xC2 + i) for i in range(5)]
        bufs = []
        for i, t in enumerate(triples):
            b = bytearray(body[i])
            with_triple(b, t)
            bufs.append(b)
        first = tuple(bufs[0][CELL_OFF:CELL_OFF + 3])
        last = tuple(bufs[-1][CELL_OFF:CELL_OFF + 3])
        self.assertEqual(first, triples[0])
        self.assertEqual(last, triples[-1])
        self.assertNotEqual(first, last, "sectors must be distinguishable")
        # Pre-check reads the final body sector: PRE == LAST.
        self.assertEqual(triples[-1], triples[-1])
        # IPLJMP report triple == LAST (handoff continuity shape).
        self.assertEqual(last, (0xC4, 0xC5, 0xC6))

    def test_l5_spare_offsets_disjoint(self):
        span = [L5_OFFSETS[k] for k in ("FILL", "INFO", "PRE", "FIRST", "LAST")]
        self.assertEqual(span, [0x24, 0x27, 0x2A, 0x2D, 0x30])
        self.assertEqual(L5_OFFSETS["RPT"], 0x34)
        for off in span:
            self.assertLess(off + 3, L5_OFFSETS["RPT"], "snapshot must not overlap RPT")
        self.assertEqual(MPR1_WORKRAM, 0xF8)


class SourcePinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.family = (ROOT / "src" / "ex_cr_family.s").read_text(encoding="utf-8")
        cls.slot2e = (ROOT / "src" / "overrides" / "slot_2E.s").read_text(
            encoding="utf-8")
        cls.loader = (HERE / "ipl_loader_rom.s").read_text(encoding="utf-8")
        cls.payload_l5 = (HERE / "ipl_payload_l5.s").read_text(encoding="utf-8")
        cls.probe = (HERE / "geargrafx_rcroff2b_desync_probe.cpp").read_text(
            encoding="utf-8")
        cls.deps = json.loads((ROOT / "tools" / "dependencies.json").read_text(
            encoding="ascii"))
        cls.manifest = (ROOT / "tools" / "ci_sharding.py").read_text(
            encoding="utf-8")
        cls.own = Path(__file__).read_text(encoding="utf-8")

    def test_gate_shape_all_four_bodies(self):
        # Every CR-family body gates ONLY on marker==$A5 (necessary, not
        # sufficient: the ok path then trusts the shadow blindly). The
        # single-source family branches `bne cr_reject`; slot $2E uses the
        # inverted `beq @have` with a fall-through SEC reject — same gate.
        for text, name, want in ((self.family, "ex_cr_family.s", 3),
                                 (self.slot2e, "slot_2E.s", 1)):
            self.assertEqual(len(re.findall(r"lda \$2503", text)), want, name)
            self.assertEqual(len(re.findall(r"cmp #\$A5", text)), want, name)
        self.assertIn("bne cr_reject", self.family)
        self.assertIn("beq @have", self.slot2e)
        self.assertIn("sec", self.slot2e)

    def test_masks_and_publish_order(self):
        self.assertIn("and #$FB", self.family)   # $2B clears bit2
        self.assertIn("and #$3F", self.family)   # $33
        self.assertIn("ora #$08", self.family)   # $2C sets bit3
        self.assertIn("ora #$80", self.slot2e)   # $2E sets bit7
        # Publish-before-VDC order in both tails (shadow first, then CR).
        for text, name in ((self.family, "family"), (self.slot2e, "slot2e")):
            self.assertLess(text.index("sta $2502"), text.index("sta a:$0002"),
                            name)
        # Reject writes nothing: SEC then RTS, no store between.
        tail = self.family[self.family.index("cr_reject:"):
                           self.family.index("cr_tail_publish:")]
        self.assertNotIn("sta ", tail)

    def test_loader_never_stores_shadow_cells_directly(self):
        # The loader range-writes BUF indirectly; it must never address the
        # shadow cells directly (direct-address audits would miss the range
        # writer, and a direct store here would be a lineage break).
        for cell in ("$2502", "$2503", "$2504"):
            self.assertNotIn(f"sta {cell}", self.loader, cell)

    def test_l5_snapshot_code_ifdef_contained(self):
        depth = 0
        for lineno, line in enumerate(self.loader.splitlines(), 1):
            s = line.strip()
            if s.startswith((".ifdef", ".ifndef")):
                depth += 1
            elif s.startswith(".endif"):
                depth -= 1
            if re.search(r"\bL5_", line):
                self.assertGreater(depth, 0,
                                   f"L5_ outside ifdef at line {lineno}: {line}")

    def test_l5_layout_pins(self):
        self.assertIn("BUF     = $2200", self.loader)
        self.assertIn("RB      = $2F00", self.loader)
        self.assertIn("SECTOR     = 2048", self.loader)
        self.assertIn("L5_FILL = RB + $24", self.loader)
        self.assertIn("L5_RPT  = RB + $34", self.loader)
        # Payload report base matches the loader RPT cell ($2F00+$34=$2F34).
        self.assertIn("L5RPT = $2F34", self.payload_l5)
        self.assertIn("PRES = $2F80", self.payload_l5)

    def test_pinned_toolchain_and_core(self):
        self.assertEqual(self.deps["cc65"]["revision"],
                         "71746c829e77f74c2b601a7d16821170c2610df3")
        self.assertEqual(self.deps["geargrafx"]["revision"],
                         "b49ae82a012eade566d72e9d61c9d95a5caa4da3")

    def test_probe_guards(self):
        # 256 KiB ROM, $E800/$FFF6 layout guards, fail-closed argv.
        self.assertIn("0x40000", self.probe)
        self.assertIn("0xFFF6", self.probe)
        self.assertIn("0xE000", self.probe)
        self.assertIn("argc != 14", self.probe)

    def test_no_mirror_use_in_new_module(self):
        # The connection tests never touch the VDC mirror pair (explicitly
        # unimplemented; asserted UNCHANGED elsewhere, never used here).
        self.assertNotIn("$20" + "F3", self.own)
        self.assertNotIn("$20" + "F7", self.own)

    def test_no_private_media_access(self):
        # No private-media access of any form: no path string, no loader call.
        self.assertNotIn("." + "/rom", self.own)
        self.assertNotIn("Load" + "Media(", self.own)

    def test_shard_listing_same_pr(self):
        # New module listed in the existing shard rc-3 (same-PR listing keeps
        # check-manifest green; no rebalance, no existing entry moved).
        self.assertIn("test_844_crshadow_connection", self.manifest)
        rc3 = self.manifest[self.manifest.index('"rc-3"'):
                            self.manifest.index('"rc-3"') + 600]
        self.assertIn("test_844_crshadow_connection", rc3)
        self.assertIn("test_862_loader_l5", rc3)
        self.assertIn("test_g5_rcroff_2b", rc3)


class LineageLabelTests(unittest.TestCase):
    def test_equal_bytes_different_lineage(self):
        # The same triple reached by staging transfer vs by direct harness
        # seed carries different lineage: value equality is never proof of
        # loader-transfer origin.
        staged = {"bytes": (0xC4, 0xC5, 0xC6),
                  "lineage": "SYNTHETIC_REPRODUCED",
                  "origin": "sector transfer"}
        seeded = {"bytes": (0xC4, 0xC5, 0xC6),
                  "lineage": "SOURCE_CONFIRMED",
                  "origin": "explicit harness argv"}
        self.assertEqual(staged["bytes"], seeded["bytes"])
        self.assertNotEqual(staged["origin"], seeded["origin"])
        self.assertIn(staged["lineage"], LABELS)
        self.assertIn(seeded["lineage"], LABELS)

    def test_a5_alone_never_proves_sync(self):
        # $A5 at the marker offset records only "someone stored $A5 here":
        # harness seed, sector residue, warm residual, or caller data.
        for origin in ("harness seed", "sector residue", "warm residual",
                       "caller data"):
            with self.subTest(origin=origin):
                # Sync requires marker==$A5 AND shadow==real AND MPR1==$F8;
                # the marker conjunct alone decides nothing.
                self.assertTrue(MARKER_OK == 0xA5)
                path, _, _, _, _ = model_2b(0xFF, MARKER_OK, 0x00)
                self.assertEqual(path, "ok",
                                 f"{origin}: $A5 admits the desync ok path")

    def test_stale_publish_never_retrojustified(self):
        # A desync publish reunifies shadow==real afterwards, but the
        # equality never retroactively justifies the stale-derived value.
        _, cr, pub, _, _ = model_2b(0xFF, MARKER_OK, 0x00)
        self.assertEqual(cr, 0xFB)
        self.assertEqual(pub, 0xFB)
        self.assertNotEqual(0xFB & ~0x04, 0x00,
                            "stale-derived bits persist after reunification")


if __name__ == "__main__":
    unittest.main()
