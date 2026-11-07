"""Host checks for the Issue #242 synthetic ROM-to-RAM handoff judge (no emulator; real-core runs are in the integration test)."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/integration"))
sys.path.insert(0, str(ROOT / "tools"))
from geargrafx_handoff import (  # noqa: E402
    Blocked, HandoffRejected, RAM_ENTRY, check_handoff_contract, expected_payload, expected_ram_writes,
    fixture_static_violations, judge, mutate, run_negatives)

GOOD_MPR = [0xFF, 0xF8, 1, 2, 31, 3, 4, 0]
LABELS = {"handoff_jump": 0xF0A0, "handoff_done": 0xF0B0}
PAYLOAD = expected_payload(LABELS["handoff_done"])  # compared as bytes; never executed here


def _row(cycle, phase, pc, sp=0xFF, mpr=GOOD_MPR):
    # Guest-driven registers of a complete handoff: A is the last copied byte until the payload's final LDA,
    # X is the copy-loop exit value. Y/P/MPR2-6 are synthetic residues, preserved within the cycle only.
    a = 0x5A if phase == "handoff_done" else PAYLOAD[-1]
    return {"cycle": cycle, "phase": phase, "pc": pc, "sp": sp, "mpr": mpr,
            "a": a, "x": len(PAYLOAD), "y": 182, "p": 0x07}


def _full_ram(marker=b"\xa5\x5a", extra=None):
    """8 KiB work RAM image after a complete handoff; `extra` maps work RAM offsets to unexpected bytes."""
    image = bytearray(0x2000)
    image[0x200:0x200 + len(PAYLOAD)] = PAYLOAD
    image[0x220:0x222] = marker
    for offset, value in (extra or {}).items():
        image[offset] = value
    return bytes(image)


def _records(entry_pc=RAM_ENTRY, marker=b"\xa5\x5a", mpr=GOOD_MPR, extra=None):
    records = {"TRACE": [], "OBS": []}
    end = _full_ram(marker, extra)
    records["OBS"].append({"phase": "loaded", "pc": LABELS["handoff_jump"], "sp": 0xFF, "vdc_ar": 0, "vdc_r0": 0,
                           "stack_01f8_hex": "0000000000000000", "work_ram_hex": bytes(0x2000).hex()})
    for cycle in ("cold", "warm"):
        records["TRACE"] += [_row(cycle, "handoff_jump", LABELS["handoff_jump"]),
                             _row(cycle, "handoff_entry", entry_pc, mpr=mpr),
                             _row(cycle, "handoff_done", LABELS["handoff_done"])]
        dump = bytearray(64)
        dump[:len(PAYLOAD)] = PAYLOAD
        dump[0x20:0x22] = marker
        records["OBS"].append({"phase": cycle, "pc": LABELS["handoff_done"], "sp": 0xFF, "vdc_ar": 0, "vdc_r0": 0,
                               "stack_01f8_hex": "0000000000000000",
                               "work_ram_0200_hex": bytes(dump).hex(), "work_ram_hex": end.hex()})
    records["OBS"].append({"phase": "nonreturning", "pc": LABELS["handoff_done"], "sp": 0xFF,
                           "vdc_ar": 0, "vdc_r0": 0, "stack_01f8_hex": "0000000000000000",
                           "work_ram_hex": end.hex()})
    return records


class HandoffContractTests(unittest.TestCase):
    def test_accepts_work_ram_entry(self):
        check_handoff_contract(RAM_ENTRY, 16, 0xFF, GOOD_MPR)

    def test_rejects_entry_outside_work_ram_window(self):
        for entry, size in ((0xF000, 16), (0x1FF8, 16), (0x3FF8, 16)):
            with self.subTest(entry=hex(entry)), self.assertRaises(ValueError):
                check_handoff_contract(entry, size, 0xFF, GOOD_MPR)

    def test_rejects_bad_bank_or_stack(self):
        bad = [[0xFF, 0x00] + GOOD_MPR[2:], [0xFF, 0xF8, 1, 2, 31, 3, 4, 1], [0x00, 0xF8] + GOOD_MPR[2:]]
        for mpr in bad:
            with self.subTest(mpr=mpr), self.assertRaises(ValueError):
                check_handoff_contract(RAM_ENTRY, 16, 0xFF, mpr)
        with self.assertRaises(ValueError):
            check_handoff_contract(RAM_ENTRY, 16, 0xFE, GOOD_MPR)


class HandoffJudgeTests(unittest.TestCase):
    def test_accepts_complete_handoff(self):
        judge(_records(), LABELS, PAYLOAD)

    def test_rejects_missing_entry_or_marker(self):
        records = _records()
        records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_entry" or r["cycle"] != "warm"]
        with self.assertRaises(ValueError):
            judge(records, LABELS, PAYLOAD)
        with self.assertRaises(ValueError):
            judge(_records(marker=b"\x00\x00"), LABELS, PAYLOAD)

    def test_rejects_entry_pc_and_bank_mismatch(self):
        with self.assertRaises(ValueError):
            judge(_records(entry_pc=RAM_ENTRY + 1), LABELS, PAYLOAD)
        with self.assertRaises(ValueError):
            judge(_records(mpr=[0xFF, 0x00] + GOOD_MPR[2:]), LABELS, PAYLOAD)


class HandoffRamSideEffectTests(unittest.TestCase):
    """Full work RAM (cold cycle): only the typed payload copy and marker may differ from 'loaded'."""

    def _reason(self, records):
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        return cm.exception.reason

    def test_accepts_only_typed_writes(self):
        self.assertEqual(len(expected_ram_writes(PAYLOAD)), len(PAYLOAD) + 2)
        judge(_records(), LABELS, PAYLOAD)

    def test_rejects_unexpected_write_outside_payload_and_marker(self):
        for offset in (0x000, 0x1FF, 0x20D, 0x222, 0x1FFF):
            with self.subTest(offset=hex(offset)):
                self.assertEqual(self._reason(_records(extra={offset: 0x77})), "ram_side_effect")

    def test_rejects_wrong_marker_byte_in_full_image(self):
        self.assertEqual(self._reason(_records(extra={0x221: 0x00})), "ram_expected")

    def test_rejects_halt_that_is_not_stable(self):
        records = _records()
        for row in records["OBS"]:
            if row["phase"] == "nonreturning":
                row["work_ram_hex"] = _full_ram(extra={0x300: 0x01}).hex()
        self.assertEqual(self._reason(records), "ram_unstable")

    def test_rejects_missing_full_image(self):
        records = _records()
        records["OBS"] = [r for r in records["OBS"] if r["phase"] != "loaded"]
        self.assertEqual(self._reason(records), "obs")

    def test_extra_copy_reason_precedes_register_check(self):
        # The real extra-copy mutation also leaves A=$FF (fill byte) and X=14; the judge must still
        # report ram_side_effect, because the RAM check runs before register preservation.
        records = _records(extra={0x20D: 0xFF})
        for row in records["TRACE"]:
            if row["phase"] in ("handoff_jump", "handoff_entry"):
                row["a"] = 0xFF
            row["x"] = len(PAYLOAD) + 1
        self.assertEqual(self._reason(records), "ram_side_effect")

    def test_rejects_warm_image_differing_from_cold(self):
        records = _records()
        for row in records["OBS"]:
            if row["phase"] == "warm":
                row["work_ram_hex"] = _full_ram(extra={0x300: 0x01}).hex()
        self.assertEqual(self._reason(records), "ram_warm")

    def test_rejects_missing_warm_full_image(self):
        records = _records()
        for row in records["OBS"]:
            if row["phase"] == "warm":
                del row["work_ram_hex"]
        self.assertEqual(self._reason(records), "obs")


class HandoffRegisterTests(unittest.TestCase):
    """Guest-driven A/X, preserved Y/MPR2-6/SP and the I flag; absolute residues are not asserted."""

    def _reason(self, records):
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        return cm.exception.reason

    def _mutate(self, cycle, phase, **changes):
        records = _records()
        for row in records["TRACE"]:
            if row["cycle"] == cycle and row["phase"] == phase:
                row.update(changes)
        return records

    def test_accepts_different_power_on_residues_per_cycle(self):
        # debug/release and cold/warm residues differ on the pinned core; only within-cycle preservation is judged.
        records = _records()
        for row in records["TRACE"]:
            if row["cycle"] == "warm":
                row["y"] = 99
                row["p"] = 0x47  # different V residue, I still set
                row["mpr"] = [0xFF, 0xF8, 9, 8, 7, 6, 5, 0]
        judge(records, LABELS, PAYLOAD)

    def test_rejects_wrong_a_or_x(self):
        self.assertEqual(self._reason(self._mutate("cold", "handoff_jump", a=0x00)), "regs")
        self.assertEqual(self._reason(self._mutate("cold", "handoff_entry", a=0x00)), "regs")
        self.assertEqual(self._reason(self._mutate("warm", "handoff_done", a=0x00)), "regs")
        self.assertEqual(self._reason(self._mutate("cold", "handoff_jump", x=0)), "regs")
        self.assertEqual(self._reason(self._mutate("cold", "handoff_entry", x=0)), "regs")
        self.assertEqual(self._reason(self._mutate("cold", "handoff_done", x=len(PAYLOAD) + 1)), "regs")

    def test_rejects_missing_register_field(self):
        records = _records()
        del records["TRACE"][0]["a"]
        self.assertEqual(self._reason(records), "obs")

    def test_rejects_y_or_mpr2_6_not_preserved(self):
        self.assertEqual(self._reason(self._mutate("cold", "handoff_entry", y=0)), "regs")
        self.assertEqual(self._reason(self._mutate("warm", "handoff_done", y=0)), "regs")
        records = _records()
        for row in records["TRACE"]:
            if row["cycle"] == "warm" and row["phase"] == "handoff_done":
                row["mpr"] = row["mpr"][:3] + [99] + row["mpr"][4:]
        self.assertEqual(self._reason(records), "regs")

    def test_rejects_sp_or_interrupt_flag(self):
        # SP at jump/entry is already covered by the earlier jump/contract checks; at done it is a regs failure.
        self.assertEqual(self._reason(self._mutate("cold", "handoff_jump", sp=0xFE)), "jump")
        self.assertEqual(self._reason(self._mutate("cold", "handoff_entry", sp=0xFE)), "sp")
        self.assertEqual(self._reason(self._mutate("cold", "handoff_done", sp=0xFE)), "regs")
        self.assertEqual(self._reason(self._mutate("warm", "handoff_jump", p=0x03)), "regs")
        self.assertEqual(self._reason(self._mutate("warm", "handoff_entry", p=0x03)), "regs")
        self.assertEqual(self._reason(self._mutate("warm", "handoff_done", p=0x03)), "regs")


class HandoffMmioSampledTests(unittest.TestCase):
    def _reason(self, records):
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        return cm.exception.reason

    def test_accepts_unchanged_sampled_vdc(self):
        judge(_records(), LABELS, PAYLOAD)

    def test_rejects_changed_sampled_vdc(self):
        for phase, field in (("cold", "vdc_r0"), ("warm", "vdc_ar"), ("nonreturning", "vdc_r0")):
            with self.subTest(phase=phase, field=field):
                records = _records()
                for row in records["OBS"]:
                    if row["phase"] == phase:
                        row[field] = 1
                self.assertEqual(self._reason(records), "mmio_sampled")

    def test_rejects_missing_sampled_vdc(self):
        records = _records()
        del records["OBS"][0]["vdc_ar"]
        self.assertEqual(self._reason(records), "obs")


class HandoffStackTests(unittest.TestCase):
    def _reason(self, records):
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        return cm.exception.reason

    def test_accepts_unchanged_stack_page(self):
        judge(_records(), LABELS, PAYLOAD)

    def test_rejects_stack_push_bytes(self):
        # An interrupt dispatch push leaves PC/P bytes at $21FD-$21FF even if the handler returned.
        records = _records()
        for row in records["OBS"]:
            if row["phase"] == "cold":
                row["stack_01f8_hex"] = "0000000000f01d20"
        self.assertEqual(self._reason(records), "stack")

    def test_rejects_missing_stack_observation(self):
        records = _records()
        del records["OBS"][0]["stack_01f8_hex"]
        self.assertEqual(self._reason(records), "obs")

    def test_rejects_nonreturning_pc_moved(self):
        records = _records()
        for row in records["OBS"]:
            if row["phase"] == "nonreturning":
                row["pc"] = LABELS["handoff_jump"]
        self.assertEqual(self._reason(records), "end")


class FixtureStaticTests(unittest.TestCase):
    """Operand-level reachability: the reviewed fixture has no violations, and each source-level mutation
    below is rejected by fixture_static_violations() itself (no assembly, no execution, no build failure)."""

    def _source(self):
        return (ROOT / "tests/fixtures/handoff-probe.s").read_text(encoding="ascii")

    def test_reviewed_fixture_has_no_violations(self):
        self.assertEqual(fixture_static_violations(self._source()), [])

    def _mutated(self, old, new):
        source = self._source()
        self.assertEqual(source.count(old), 1, f"mutation anchor {old!r} must occur exactly once")
        return source.replace(old, new, 1)

    def test_source_mutations_are_rejected_by_the_intended_check(self):
        # (name, old, new, substring that the intended violation must contain)
        cases = [
            ("payload_store_to_hardware_page", "sta $2220", "sta $0000", "sta operand"),
            ("copy_store_indirect", "sta $2200,x", "sta ($2200),x", "sta operand"),
            ("stx_store", "sta $2221", "stx $2221", "mnemonic"),
            ("st0_vdc", "sta $2220", "st0 $2220", "mnemonic"),
            ("st1_vdc", "sta $2221", "st1 $2221", "mnemonic"),
            ("st2_vdc", "sta $2220", "st2 $2220", "mnemonic"),
            ("block_transfer_tii", "    cld", "    cld\n    tii", "mnemonic"),
            ("block_transfer_tdd", "    cld", "    cld\n    tdd", "mnemonic"),
            ("tam_to_mpr2", "tam #$02", "tam #$03", "tam operand"),
            ("jump_to_hardware_page", "jmp $2200", "jmp $0000", "jmp operand"),
            ("branch_to_ram", "bra handoff_done", "bra $2200", "bra operand"),
            ("indirect_lda", "lda handoff_payload_rom,x", "lda ($10),y", "lda operand"),
            ("cli_unmasking_irq", "    cld", "    cld\n    cli", "mnemonic"),
            ("jsr_stack_write", "    cld", "    cld\n    jsr handoff_done", "mnemonic"),
            ("sei_removed", "    sei", "    nop", "sei"),
            ("vector_to_hardware_page", ".word handoff_trap        ; IRQ2 / BRK", ".word $0000", "vector target"),
            ("byte_directive_injection", "    cld", "    cld\n    .byte $FF", "directive"),
            ("res_directive_injection", "    cld", "    cld\n    .res 1", "directive"),
        ]
        for name, old, new, needle in cases:
            with self.subTest(mutation=name):
                violations = fixture_static_violations(self._mutated(old, new))
                self.assertTrue(violations, f"{name}: mutation was not detected")
                self.assertTrue(any(needle in v for v in violations),
                                f"{name}: violations {violations} do not mention {needle!r}")

    def test_comment_and_whitespace_changes_are_accepted(self):
        source = self._source()
        variants = {
            "comment_text": source.replace("; MPR0 = hardware I/O bank", "; changed comment"),
            "indentation_and_blank_lines": source.replace("    sei", "        sei  \n"),
            "label_on_instruction_line": source.replace("handoff_reset:\n    sei", "handoff_reset: sei"),
            "operand_spacing": source.replace("sta $2200,x", "sta $2200, x"),
        }
        for name, variant in variants.items():
            with self.subTest(variant=name):
                self.assertEqual(fixture_static_violations(variant), [])

    def _move(self, source, needle, before):
        lines = source.splitlines()
        index = next(i for i, line in enumerate(lines) if needle in line)
        moved = lines.pop(index)
        target = next(i for i, line in enumerate(lines) if before in line)
        lines.insert(target, moved)
        return "\n".join(lines) + "\n"

    def _insert_before(self, source, needle, new_line):
        lines = source.splitlines()
        target = next(i for i, line in enumerate(lines) if needle in line)
        lines.insert(target, new_line)
        return "\n".join(lines) + "\n"

    def _delete(self, source, needle):
        lines = source.splitlines()
        index = next(i for i, line in enumerate(lines) if needle in line)
        del lines[index]
        return "\n".join(lines) + "\n"

    def test_order_and_placement_mutations_are_rejected_by_structure(self):
        # Every mutation below uses only allowed mnemonics/operands, so the per-instruction allow-list alone
        # would accept it; the normalized sequence comparison must reject it.
        source = self._source()
        word_lines = [line for line in source.splitlines() if ".word" in line]
        swapped = source.splitlines()
        word_indexes = [i for i, line in enumerate(swapped) if ".word" in line]
        swapped[word_indexes[-1]], swapped[word_indexes[-2]] = swapped[word_indexes[-2]], swapped[word_indexes[-1]]
        mutations = {
            "sta_2220_before_tam_02": self._move(source, "sta $2220", "tam #$02"),
            "extra_lda_between_f8_and_tam_02": self._insert_before(source, "tam #$02", "    lda #$FF"),
            "tam_02_after_copy": self._move(source, "tam #$02", "jmp $2200"),
            "sta_2200_before_tam_02": self._move(source, "sta $2200,x", "tam #$02"),
            "copy_label_moved": self._move(source, "handoff_copy:", "ldx #$FF"),
            "payload_label_moved": self._move(source, "handoff_payload_rom:", "handoff_done:"),
            "word_outside_vectors": self._move(source, ".word handoff_reset", '.segment "VECTORS"'),
            "vectors_segment_renamed": source.replace('.segment "VECTORS"', '.segment "BOOT"'),
            "segment_name_with_space": source.replace('.segment "BOOT"', '.segment "B OOT"'),
            "setcpu_with_space": source.replace('.setcpu "huc6280"', '.setcpu "huc 6280"'),
            "vectors_segment_deleted": self._delete(source, '.segment "VECTORS"'),
            "vector_order_swapped": "\n".join(swapped) + "\n",
            "vector_count_reduced": self._delete(source, word_lines[0]),
            "extra_inx_in_copy_loop": self._insert_before(source, "cpx #13", "    inx"),
        }
        for name, mutated in mutations.items():
            with self.subTest(mutation=name):
                violations = fixture_static_violations(mutated)
                self.assertTrue(violations, f"{name}: mutation was not detected")
                self.assertTrue(any("structure differs" in v or "structure length" in v for v in violations),
                                f"{name}: violations {violations} are not structure violations")


class HandoffReasonTests(unittest.TestCase):
    """Each negative is rejected for its own reason key; entry and MPR are checked before phase order."""

    def test_mpr1_rom_bank_is_rejected_for_mpr_not_phase_order(self):
        records = _records(mpr=[0xFF, 0x00] + GOOD_MPR[2:])
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        self.assertEqual(cm.exception.reason, "mpr")

    def test_missing_entry_is_rejected_for_entry_not_phase_order(self):
        records = _records()
        records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_entry"]
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        self.assertEqual(cm.exception.reason, "entry")

    def test_mpr_is_checked_before_phase_order(self):
        records = _records(mpr=[0xFF, 0x00] + GOOD_MPR[2:])
        records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_done"]
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        self.assertEqual(cm.exception.reason, "mpr")


def _mutant_rom():
    """Synthetic 8 KiB bank holding the bytes the negatives patch: MPR1 immediate and the JMP $2200 operand."""
    labels = {"handoff_bank": 0xE011, "handoff_jump": LABELS["handoff_jump"], "handoff_done": LABELS["handoff_done"],
              "handoff_payload_rom": 0xE070, "handoff_payload_end_rom": 0xE080}
    rom = bytearray(0x2000)
    rom[0x11 - 1] = 0xF8
    rom[labels["handoff_jump"] - 0xE000:labels["handoff_jump"] - 0xE000 + 3] = bytes([0x4C, 0x00, 0x22])
    rom[0x100:0x103] = b"\xe0\x0d\xd0"  # cpx #13 / bne handoff_copy anchor for the extra-copy negative
    rom[0x70:0x75] = bytes.fromhex("a9a58d2022")  # lda #$A5 / sta $2220 anchor for the marker negative
    rom[0x80] = 0xFF                    # fill byte after the payload (ld65 fill in the real bank)
    return bytes(rom), labels


class NegativeMutationTests(unittest.TestCase):
    def test_extra_copy_byte_patches_only_the_loop_bound(self):
        rom, labels = _mutant_rom()
        mutated = mutate(rom, labels, "extra_copy_byte")
        changed = [i for i, (a, b) in enumerate(zip(rom, mutated)) if a != b]
        self.assertEqual(changed, [0x101])
        self.assertEqual(mutated[0x101], 0x0E)

    def test_marker_outside_ram_patches_only_the_sta_operand(self):
        rom, labels = _mutant_rom()
        mutated = mutate(rom, labels, "marker_outside_ram")
        changed = [i for i, (a, b) in enumerate(zip(rom, mutated)) if a != b]
        self.assertEqual(changed, [0x73, 0x74])  # $2220 -> $0000, opcode $8D untouched
        self.assertEqual(mutated[0x73:0x75], b"\x00\x00")

    def test_extra_copy_byte_refuses_a_non_fill_byte_after_the_payload(self):
        rom, labels = _mutant_rom()
        rom = bytearray(rom)
        rom[0x80] = 0x00
        with self.assertRaises(ValueError):
            mutate(bytes(rom), labels, "extra_copy_byte")


class NegativeRunnerTests(unittest.TestCase):
    def test_probe_timeout_is_blocked_not_rejected(self):
        rom, labels = _mutant_rom()

        def run(path):
            raise subprocess.TimeoutExpired(cmd="probe", timeout=30)

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(Blocked):
            run_negatives(rom, labels, PAYLOAD, Path(tmp), run)

    def test_probe_crash_is_blocked_not_rejected(self):
        rom, labels = _mutant_rom()

        def run(path):
            raise subprocess.CalledProcessError(returncode=3, cmd="probe")

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(Blocked):
            run_negatives(rom, labels, PAYLOAD, Path(tmp), run)

    def test_wrong_reason_for_a_negative_fails(self):
        rom, labels = _mutant_rom()

        def run(path):
            records = _records()  # complete handoff except the phase list: rejected for "phase", not "mpr"
            records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_done"]
            return records

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError) as cm:
            run_negatives(rom, labels, PAYLOAD, Path(tmp), run)
        self.assertNotIsInstance(cm.exception, Blocked)

    def test_expected_reasons_are_recorded(self):
        rom, labels = _mutant_rom()

        def run(path):
            if path.name.startswith("mpr1_rom_bank"):
                return _records(mpr=[0xFF, 0x00] + GOOD_MPR[2:])
            if path.name.startswith("extra_copy_byte"):
                # Mirror the real mutation: the extra $FF byte also leaves A=$FF and X=14.
                records = _records(extra={0x20D: 0xFF})
                for row in records["TRACE"]:
                    if row["phase"] in ("handoff_jump", "handoff_entry"):
                        row["a"] = 0xFF
                    row["x"] = len(PAYLOAD) + 1
                return records
            if path.name.startswith("marker_outside_ram"):
                # Mirror the real mutation faithfully: payload bytes in RAM are a9 a5 8d 00 00 ... (STA $0000),
                # and $2220 was never written (only $2221 got $5A). The judge rejects on the payload dump.
                records = _records()
                mutated_payload = PAYLOAD[:3] + b"\x00\x00" + PAYLOAD[5:]
                for row in records["OBS"]:
                    if "work_ram_0200_hex" in row:
                        dump = bytearray.fromhex(row["work_ram_0200_hex"])
                        dump[:len(mutated_payload)] = mutated_payload
                        dump[0x20] = 0x00
                        row["work_ram_0200_hex"] = bytes(dump).hex()
                return records
            records = _records()
            records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_entry"]
            return records

        with tempfile.TemporaryDirectory() as tmp:
            negatives = run_negatives(rom, labels, PAYLOAD, Path(tmp), run)
        self.assertEqual({k: v["reason"] for k, v in negatives.items()},
                         {"mpr1_rom_bank": "mpr", "entry_misaligned": "entry", "extra_copy_byte": "ram_side_effect",
                          "marker_outside_ram": "payload"})


if __name__ == "__main__":
    unittest.main()
