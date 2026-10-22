#!/usr/bin/env python3
"""Host tests for the local-only reference ROM static auditor.

The auditor never runs in CI, because CI has no private BIOS image. These
tests therefore drive it with a **synthetic** image that the test itself
builds, so the decoder, the jump table parser and the hazard classifier are
covered without any private input.
"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import reference_rom_static_audit as audit  # noqa: E402
from environment import requires_cc65  # noqa: E402


def synthetic_image(callee=b"\xa2\x03\xa0\x00\x60"):
    """A headerless 8 KiB bank with an 81 entry JMP table and one tiny callee.

    Slot 0x1E points at the callee, the other slots point at distinct stubs.
    The callee is the project-own EX_GETVER shape (load X, load Y, return):
    it is a test fixture written for this repository, not reference code.
    """
    bank = bytearray(b"\xff" * audit.BANK_BYTES)
    base = 0xE000 - 0xE000  # the table starts at the first byte of the bank
    for index in range(audit.ENTRY_COUNT):
        offset = base + index * audit.ENTRY_STRIDE
        if index == 0x1E:
            target = 0xF000
        else:
            target = 0xF100 + index * 3
        bank[offset] = audit.JMP_ABS
        bank[offset + 1] = target & 0xFF
        bank[offset + 2] = target >> 8
    bank[0xF000 - 0xE000:0xF000 - 0xE000 + len(callee)] = callee
    for index in range(audit.ENTRY_COUNT):
        if index == 0x1E:
            continue
        offset = 0xF100 + index * 3 - 0xE000
        bank[offset] = 0x60  # RTS
    return bytes(bank)


@requires_cc65
class SyntheticImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.image = self.directory / "synthetic.pce"
        self.image.write_bytes(synthetic_image())

    def test_jump_table_is_parsed_as_three_byte_jump_entries(self):
        result = audit.audit_image(self.image, [0x1E], synthetic=True)
        table = result["jump_table"]
        self.assertEqual(table["entry_count"], 81)
        self.assertEqual(table["entry_stride_bytes"], 3)
        self.assertTrue(table["all_entries_are_jmp_abs"])
        self.assertTrue(table["all_targets_distinct"])
        self.assertEqual(result["apis"][0]["candidate_entry"], "0xE05A")

    def test_a_side_effect_free_callee_reports_no_hazard(self):
        result = audit.audit_image(self.image, [0x1E], synthetic=True)
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["returns_observed"], ["rts"])
        self.assertEqual(profile["instructions_decoded"], 3)
        self.assertEqual([value for value in profile["hazard_counts"].values() if value], [])
        self.assertEqual(profile["store_target_classes"], {})
        self.assertEqual(profile["unresolved"], [])

    def test_a_block_transfer_is_reported_as_a_hazard(self):
        image = self.directory / "block.pce"
        # TII $204D,$205F,$0003 followed by RTS: a block transfer in the callee.
        image.write_bytes(synthetic_image(b"\x73\x4d\x20\x5f\x20\x03\x00\x60"))
        result = audit.audit_image(image, [0x1E], synthetic=True)
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["hazard_counts"]["BLOCK_TRANSFER"], 1)
        self.assertEqual(profile["hazard_counts"]["OTHER_STORE"], 1)

    def test_a_zero_page_store_is_reported_in_page_one(self):
        image = self.directory / "zp.pce"
        # STX $EC then RTS: the HuC6280 zero page lives at $2000.
        image.write_bytes(synthetic_image(b"\x86\xec\x60"))
        result = audit.audit_image(image, [0x1E], synthetic=True)
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["hazard_counts"]["PAGE1_STORE"], 1)
        self.assertEqual(profile["store_target_classes"], {"PAGE1_ZERO_PAGE": 1})
        self.assertEqual(profile["zero_page_operand_stores"], 1)

    def test_an_interrupt_enable_is_reported(self):
        image = self.directory / "cli.pce"
        image.write_bytes(synthetic_image(b"\x58\x60"))
        result = audit.audit_image(image, [0x1E], synthetic=True)
        self.assertEqual(result["apis"][0]["callee"]["hazard_counts"]["IRQ_ENABLE"], 1)

    def test_a_mpr_switch_is_reported(self):
        image = self.directory / "tam.pce"
        # LDA #$03 / TAM #$40 (MPR6) then RTS.
        image.write_bytes(synthetic_image(b"\xa9\x03\x53\x40\x60"))
        result = audit.audit_image(image, [0x1E], synthetic=True)
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["hazard_counts"]["MPR_WRITE"], 1)

    def test_a_stale_accumulator_value_never_survives_an_accumulator_write(self):
        # A "known A" that survives a CLA or a PLA would make a later TAM
        # resolve a cross page callee in the wrong bank, so the profile would
        # describe code the callee never runs.
        for clobber in (b"\x62", b"\x68"):
            image = self.directory / f"clobber{clobber.hex()}.pce"
            # LDA #$05 / <clobber> / TAM #$40 / JSR $CF00 / RTS
            image.write_bytes(synthetic_image(b"\xa9\x05" + clobber
                                              + b"\x53\x40\x20\x00\xcf\x60"))
            result = audit.audit_image(image, [0x1E], synthetic=True)
            profile = result["apis"][0]["callee"]
            self.assertEqual(profile["unresolved"], ["page_bank_unknown"],
                             f"opcode {clobber.hex()} left a stale accumulator value")
            self.assertEqual(profile["hazard_counts"]["MPR_WRITE"], 1)

    def test_a_self_loop_is_reported_as_a_non_returning_path(self):
        # One path returns, the other spins forever: the profile must not look
        # clean just because some path returned.
        image = self.directory / "loop.pce"
        image.write_bytes(synthetic_image(b"\xc9\x00\xf0\x01\x60\x80\xfe"))
        result = audit.audit_image(image, [0x1E], synthetic=True)
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["returns_observed"], ["rts"])
        self.assertEqual(profile["non_returning_states"], 1)
        self.assertEqual(profile["hazard_counts"]["NO_RETURN"], 1)

    def test_a_loop_with_a_returning_exit_is_not_a_proof_that_every_path_returns(self):
        # BNE to itself, or fall through to RTS: some path returns, another spins forever.
        # The existence check reports 0 here by design (limitation pin). The
        # separate all-paths measure (#485) must flag the spinning state.
        image = self.directory / "mixed_loop.pce"
        image.write_bytes(synthetic_image(b"\xd0\xfe\x60"))
        result = audit.audit_image(image, [0x1E], synthetic=True)
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["returns_observed"], ["rts"])
        self.assertEqual(profile["non_returning_states"], 0)
        self.assertEqual(profile["states_without_guaranteed_return"], 1)

    def test_all_paths_measure_is_zero_only_when_every_path_returns(self):
        cases = {
            "plain": (b"\xa2\x03\x60", 0),                      # LDX / RTS
            "diamond": (b"\xc9\x00\xf0\x01\xea\x60", 0),        # CMP/BEQ/NOP/RTS
            "spin_arm": (b"\xc9\x00\xf0\x01\x60\x80\xfe", 3),   # CMP, BEQ, spin
            "bare_spin": (b"\x80\xfe", 1),
            "unresolved_jump": (b"\x6c\x00\x20", 1),            # JMP ($2000)
        }
        for name, (code, expected) in cases.items():
            image = self.directory / f"{name}.pce"
            image.write_bytes(synthetic_image(code))
            profile = audit.audit_image(image, [0x1E], synthetic=True)["apis"][0]["callee"]
            self.assertEqual(profile["states_without_guaranteed_return"], expected, name)

    def test_unknown_bank_is_not_treated_as_a_guaranteed_return(self):
        # LDA #$05 / CLA (A becomes unknown) / TAM #$40 (MPR6 unmapped) /
        # JSR $CF00 (bank unknown) / RTS
        image = self.directory / "unknown_bank.pce"
        image.write_bytes(synthetic_image(b"\xa9\x05\x62\x53\x40\x20\x00\xcf\x60"))
        profile = audit.audit_image(image, [0x1E], synthetic=True)["apis"][0]["callee"]
        self.assertEqual(profile["unresolved"], ["page_bank_unknown"])
        self.assertGreater(profile["states_without_guaranteed_return"], 0)

    def test_a_budget_cut_never_looks_guaranteed(self):
        # The default callee (LDX / LDY / RTS) returns, but a 2 state budget
        # cuts the walk before the RTS: the cut tail must not count as safe.
        with mock.patch.object(audit.Auditor, "MAX_STATES", 2):
            profile = audit.audit_image(self.image, [0x1E], synthetic=True)["apis"][0]["callee"]
        self.assertTrue(profile["budget_exhausted"])
        self.assertGreater(profile["states_without_guaranteed_return"], 0)

    def test_any_successor_rule_is_distinguished_from_all_successors(self):
        # Mutation guard: weakening "all successors" to "any successor" turns
        # 2 into 1 here, because arm c spins forever while arm b returns.
        successors = {"a": ["b", "c"], "b": [], "c": ["c"]}
        visited = {"a", "b", "c"}
        self.assertEqual(
            audit.Auditor._states_without_guaranteed_return(visited, successors, {"b"}), 2)
        self.assertEqual(
            audit.Auditor._states_without_return(visited, successors, {"b"}), 1)

    def test_the_tam_mask_selects_the_page_the_core_selects(self):
        # Memory::SetMprTAM maps mask bit i to MPR[i], so "tam #$40" is MPR6 and
        # the call target in $C000-$DFFF must be read from bank 3.
        image = self.directory / "tam.pce"
        body = bytearray(synthetic_image(b"\xa9\x03\x53\x40\x20\x00\xcf\x60"))
        body.extend(b"\xff" * 3 * audit.BANK_BYTES)
        # bank 3 at $CF00 holds two RTS, bank 0 two LDA: the instruction count
        # tells which bank the walk followed.
        body[3 * audit.BANK_BYTES + 0x0F00:3 * audit.BANK_BYTES + 0x0F02] = b"\x60\x60"
        body[0x0F00:0x0F02] = b"\xa9\x01"
        image.write_bytes(bytes(body))
        result = audit.audit_image(image, [0x1E], synthetic=True)
        profile = result["apis"][0]["callee"]
        # Bank 3 holds two RTS, bank 0 an LDA: the instruction count tells
        # which bank the walk followed. With the wrong mask mapping the page
        # bank stays unknown, or bank 0 is read instead.
        self.assertEqual(profile["instructions_decoded"], 5)
        self.assertEqual(profile["unresolved"], [])

    def test_the_projection_declares_that_it_is_not_a_permission(self):
        result = audit.audit_image(self.image, [0x1E], synthetic=True)
        self.assertEqual(result["scope"], "STATIC_PROFILE_ONLY")
        self.assertEqual(result["execution_permission"], "NOT_PRODUCED_BY_THIS_TOOL")

    def test_the_projection_never_carries_a_permission(self):
        result = audit.audit_image(self.image, [0x1E], synthetic=True)
        for path, value in audit_strings(result):
            self.assertNotIn("ALLOWED", value.upper(), f"{path} carries a permission")

    def test_the_projection_never_carries_bytes_or_addresses(self):
        result = audit.audit_image(self.image, [0x1E], synthetic=True)
        text = repr(result)
        self.assertNotIn("0x4c", text.lower())
        self.assertNotIn("synthetic", text.lower())
        # No internal address may appear: the only addresses in the projection
        # are the public jump table entries.
        for path, value in audit_strings(result):
            for match in re.finditer(r"(?:0x|\$)([0-9A-Fa-f]{4})", value):
                self.assertIn(match.group(1).upper(), {"E000", "E05A"},
                              f"{path} carries an address that is not a public entry")


def audit_strings(node, path=""):
    """Every string value of a projection, with its path."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from audit_strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from audit_strings(value, f"{path}[{index}]")


class GuardTests(unittest.TestCase):
    def test_the_cli_refuses_to_run_in_ci(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        image = directory / "synthetic.pce"
        image.write_bytes(synthetic_image())
        output = directory / "out.json"
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with mock.patch("sys.stderr", new_callable=__import__("io").StringIO) as err:
                code = audit.main(["--rom", str(image), "--output", str(output)])
        self.assertEqual(code, 3)
        self.assertIn("never read in CI", err.getvalue())
        self.assertFalse(output.exists())

    def test_the_library_entry_point_also_refuses_to_run_in_ci(self):
        # Without the synthetic declaration the library entry point refuses in
        # CI exactly like the CLI does.
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        image = directory / "synthetic.pce"
        image.write_bytes(synthetic_image())
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(audit.AuditError):
                audit.audit_image(image, [0x1E])

    def test_an_image_inside_the_repository_is_never_a_synthetic_fixture(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(audit.AuditError):
                audit.audit_image(ROOT / "spec" / "api_contracts.json", [0x1E], synthetic=True)


    def test_a_partial_bank_image_is_refused(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        image = directory / "short.pce"
        image.write_bytes(b"\x00" * 1234)
        with self.assertRaises(audit.AuditError):
            audit.audit_image(image, [0x1E], synthetic=True)

    def test_a_slot_outside_the_table_is_refused(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        image = directory / "synthetic.pce"
        image.write_bytes(synthetic_image())
        with self.assertRaises(audit.AuditError):
            audit.audit_image(image, [0x99], synthetic=True)


if __name__ == "__main__":
    unittest.main()
