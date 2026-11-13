"""Host tests for Issue #789 matched-selection verification (pure, no core).

Verifies tools/g5_progress_model.verify_matched_selection: A/B/C/D must share
the same synthetic track/INDEX01 LBA/payload/selection record. Mismatches are
BLOCKED, never silent passes. Existing per-variant checks (D embedded vs
executed, wiring, fake-RTS/real gates) are reused, not duplicated here.
"""
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import g5_progress_model as M  # noqa: E402


def _d_manifest(lba=265, track=1, code=b"\x01\x02\x03"):
    return {
        "rom_name": "experimental-dev-only-not-compatible-syscard-sized.pce",
        "rom_sha256": "a" * 64,
        "rom_bytes": M.ROM_BYTES,
        "pinned_cc65": M.PINNED_CC65,
        "pinned_geargrafx": M.PINNED_GEARGRAFX,
        "release_eligible": False,
        "dev_callable": False,
        "branch": "agent-d/matched-selection-789",
        "head": "b" * 40,
        "labels": {"api_unimplemented": 0xE245},
        "symbols": {"api_unimplemented": 0xE245},
        "selection_contract": {"lba": lba, "track": track,
                               "media_identity": {"cue": "u.cue"}},
        "payload_sha256": hashlib.sha256(bytes(code)).hexdigest(),
        "payload_bytes": len(code),
        "cd_base_proto": {"slot": "$02", "entry": "$E006", "proto_entry": "$F000"},
        "experimental_slot_33": {"table_entry": "0xE099", "body": "$F100"},
        "cd_read_link": {"slot": "$03", "entry": "$E009", "table_entry": "0xE009",
                         "link_entry": "$F200"},
    }


def _executed(lba=265, track=1):
    return {"lba": lba, "track": track, "identity": {"cue": "u.cue"}}


def _abc(lba=265, track=1):
    return [{"variant": "A", "lba": lba, "track": track},
            {"variant": "B", "lba": lba, "track": track},
            {"variant": "C", "lba": lba, "track": track}]


class MatchedSelectionTests(unittest.TestCase):
    def test_all_matched_passes(self):
        code = b"\x01\x02\x03"
        M.verify_matched_selection(d_manifest=_d_manifest(code=code),
                                   abc_sels=_abc(), unified_code=code,
                                   executed=_executed())

    def test_selection_lba_mismatch_blocked(self):
        code = b"\x01\x02\x03"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(lba=266, code=code),
                                       abc_sels=_abc(), unified_code=code,
                                       executed=_executed())

    def test_payload_mismatch_blocked(self):
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(code=b"\x01\x02\x03"),
                                       abc_sels=_abc(), unified_code=b"\x09\x09",
                                       executed=_executed())

    def test_embedded_media_identity_mismatch_blocked(self):
        code = b"\x01\x02\x03"
        man = _d_manifest(code=code)
        man["selection_contract"]["media_identity"] = {"cue": "other.cue"}
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=man, abc_sels=_abc(),
                                       unified_code=code, executed=_executed())

    def test_abc_lba_mismatch_blocked(self):
        code = b"\x01\x02\x03"
        bad = _abc()
        bad[1] = {"variant": "B", "lba": 0, "track": 1}
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(code=code),
                                       abc_sels=bad, unified_code=code,
                                       executed=_executed())

    def test_unmeasured_executed_blocked(self):
        code = b"\x01\x02\x03"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(code=code),
                                       abc_sels=_abc(), unified_code=code,
                                       executed=None)


class DTrackTests(unittest.TestCase):
    """MINOR #790: D embedded track must equal the executed track."""

    def test_mismatch_blocked(self):
        code = b"\x01\x02\x03"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(track=2, code=code),
                                       abc_sels=_abc(), unified_code=code,
                                       executed=_executed())

    def test_missing_blocked(self):
        code = b"\x01\x02\x03"
        man = _d_manifest(code=code)
        del man["selection_contract"]["track"]
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=man, abc_sels=_abc(),
                                       unified_code=code, executed=_executed())

    def test_string_blocked(self):
        code = b"\x01\x02\x03"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(track="1", code=code),
                                       abc_sels=_abc(), unified_code=code,
                                       executed=_executed())

    def test_bool_blocked(self):
        code = b"\x01\x02\x03"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(track=True, code=code),
                                       abc_sels=_abc(), unified_code=code,
                                       executed=_executed())

    def test_match_passes(self):
        code = b"\x01\x02\x03"
        M.verify_matched_selection(d_manifest=_d_manifest(track=1, code=code),
                                   abc_sels=_abc(track=1), unified_code=code,
                                   executed=_executed(track=1))

    def test_fake_rts_misclassification_is_not_a_pass(self):
        # A/B/C fake RTS must never be classified as real: the model gates
        # already refuse manifest-alone real claims; matched-selection only
        # checks inputs, never execution. A fake return observed in probe
        # records must still go through derive_real_service_proof (which needs
        # measured gates), not through this input check.
        proof = M.build_real_service_proof(arrived_at_callee=False,
                                           post_return_position_observed=False,
                                           sp_restored=False, mpr_consistent=False,
                                           order_matches_expected=False)
        ok, _ = M.validate_real_service_proof(proof)
        self.assertFalse(ok)

    def test_no_transfer_false_pass_needs_ram_proof(self):
        # A missing 2048B copy must fail the transfer assertion: the transfer
        # proof lives in the real-core buffer/PHASE comparison (sector after
        # boot differs from boot), never in the input check alone.
        from tools import make_cd_image as _mci  # noqa
        self.assertNotEqual(_mci.sector_payload(265), _mci.sector_payload(266))

    def test_wiring_mismatch_still_blocked(self):
        # Manifest/ROM wiring mismatch stays BLOCKED via the existing slot
        # verifier (reused, not duplicated): wrong table dest refuses.
        import g5_progress_model as MM
        rom = bytearray(MM.ROM_BYTES)
        rom[0x02 * 3:0x02 * 3 + 3] = bytes((0x4C, 0x00, 0xF0))
        man = _d_manifest()
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            MM.verify_d_rom_slot_targets(bytes(rom), man)


def _ipl_manifest_a(sel=265, rom_bytes=b"ROM-A-BYTES"):
    import hashlib as _hl
    return {"rom_sha256": _hl.sha256(bytes(rom_bytes)).hexdigest(),
            "private_media_used": False, "ipl_sel_lba": sel}


def _ipl_manifest_bc(sel=265, rom_bytes=b"ROM-BC-BYTES"):
    import hashlib as _hl
    return {"rom_sha256": _hl.sha256(bytes(rom_bytes)).hexdigest(),
            "private_media_used": False,
            "ipl_sel": "$%04X (synthetic)" % sel}


class IplSelFromBuiltRomTests(unittest.TestCase):
    """MEDIUM #790: A/B/C SEL from ROM-bound manifests, never test constants.

    RED baseline (old test-constant path): passing {"lba": SEL} constants to
    verify_matched_selection accepts a swapped/mistbuilt ROM silently.
    GREEN: read_ipl_sel_from_built_rom verifies rom_sha256 (catches
    post-build byte tampering) and parses the builder-recorded SEL (catches
    a rebuild/swap with a different SEL); redacted/private/malformed are
    BLOCKED. Track is NOT ROM-embedded (see helper doc): A/B/C track is
    INFERRED post-run from successful IPL boot on the executed track, never
    OBSERVED pre-run from ROM bytes.
    """

    def test_a_positive(self):
        rom = b"ROM-A-BYTES"
        self.assertEqual(M.read_ipl_sel_from_built_rom(rom, _ipl_manifest_a(265, rom), "A"), 265)

    def test_a_lba_tamper_blocked(self):
        # A rebuilt/swapped with LBA 0 while executed is 265 -> BLOCKED at
        # the matched-selection gate (not silently passed via constants).
        rom = b"ROM-A-BYTES"
        sel = M.read_ipl_sel_from_built_rom(rom, _ipl_manifest_a(0, rom), "A")
        code = b"\x01\x02\x03"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(code=code),
                                       abc_sels=[{"variant": "A", "lba": sel, "track": 1},
                                                 {"variant": "B", "lba": 265, "track": 1},
                                                 {"variant": "C", "lba": 265, "track": 1}],
                                       unified_code=code, executed=_executed())

    def test_b_lba_tamper_blocked(self):
        rom = b"ROM-B-BYTES"
        sel = M.read_ipl_sel_from_built_rom(rom, _ipl_manifest_bc(0, rom), "B")
        code = b"\x01\x02\x03"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(code=code),
                                       abc_sels=[{"variant": "A", "lba": 265, "track": 1},
                                                 {"variant": "B", "lba": sel, "track": 1},
                                                 {"variant": "C", "lba": 265, "track": 1}],
                                       unified_code=code, executed=_executed())

    def test_c_lba_tamper_blocked(self):
        rom = b"ROM-C-BYTES"
        sel = M.read_ipl_sel_from_built_rom(rom, _ipl_manifest_bc(0, rom), "C")
        code = b"\x01\x02\x03"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(code=code),
                                       abc_sels=[{"variant": "A", "lba": 265, "track": 1},
                                                 {"variant": "B", "lba": 265, "track": 1},
                                                 {"variant": "C", "lba": sel, "track": 1}],
                                       unified_code=code, executed=_executed())

    def test_executed_mismatch_blocked(self):
        rom = b"ROM-A-BYTES"
        sel = M.read_ipl_sel_from_built_rom(rom, _ipl_manifest_a(265, rom), "A")
        code = b"\x01\x02\x03"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(code=code),
                                       abc_sels=[{"variant": "A", "lba": sel, "track": 1},
                                                 {"variant": "B", "lba": 265, "track": 1},
                                                 {"variant": "C", "lba": 265, "track": 1}],
                                       unified_code=code,
                                       executed=_executed(lba=0))

    def test_d_embedded_identity_mismatch_blocked(self):
        code = b"\x01\x02\x03"
        man = _d_manifest(code=code)
        man["selection_contract"]["media_identity"] = {"cue": "other.cue"}
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=man, abc_sels=_abc(),
                                       unified_code=code, executed=_executed())

    def test_payload_mismatch_via_helper_path_blocked(self):
        rom = b"ROM-A-BYTES"
        sel = M.read_ipl_sel_from_built_rom(rom, _ipl_manifest_a(265, rom), "A")
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.verify_matched_selection(d_manifest=_d_manifest(code=b"\x01\x02\x03"),
                                       abc_sels=[{"variant": "A", "lba": sel, "track": 1},
                                                 {"variant": "B", "lba": 265, "track": 1},
                                                 {"variant": "C", "lba": 265, "track": 1}],
                                       unified_code=b"\x09\x09", executed=_executed())

    def test_all_match_positive_via_helper(self):
        rom_a, rom_b, rom_c = b"ROM-A-BYTES", b"ROM-B-BYTES", b"ROM-C-BYTES"
        code = b"\x01\x02\x03"
        abc = [{"variant": "A", "lba": M.read_ipl_sel_from_built_rom(rom_a, _ipl_manifest_a(265, rom_a), "A"), "track": 1},
               {"variant": "B", "lba": M.read_ipl_sel_from_built_rom(rom_b, _ipl_manifest_bc(265, rom_b), "B"), "track": 1},
               {"variant": "C", "lba": M.read_ipl_sel_from_built_rom(rom_c, _ipl_manifest_bc(265, rom_c), "C"), "track": 1}]
        M.verify_matched_selection(d_manifest=_d_manifest(code=code),
                                   abc_sels=abc, unified_code=code,
                                   executed=_executed())

    def test_rom_hash_mismatch_blocked(self):
        rom = b"ROM-A-BYTES"
        man = _ipl_manifest_a(265, b"OTHER-BYTES")
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.read_ipl_sel_from_built_rom(rom, man, "A")

    def test_redacted_private_blocked(self):
        import hashlib as _hl
        rom = b"ROM-A-BYTES"
        man = {"rom_sha256": _hl.sha256(rom).hexdigest(), "private_media_used": True,
               "ipl_sel_lba": 265}
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.read_ipl_sel_from_built_rom(rom, man, "A")
        man_bc = {"rom_sha256": _hl.sha256(rom).hexdigest(), "private_media_used": False,
                  "ipl_sel": "redacted (private media)"}
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            M.read_ipl_sel_from_built_rom(rom, man_bc, "B")


if __name__ == "__main__":
    unittest.main()
