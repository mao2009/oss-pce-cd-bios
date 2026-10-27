"""Static guards for the DEV-only reset and CD-boot initialisation facts (Issue #551, wave26 w03).

Pure text checks: no cc65, no ROM build, no reference BIOS. They pin what the design doc
docs/dev-cd-boot-init-design.md claims about the existing sources, so a change to those sources
fails here until the doc is updated with it.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN_DOC = ROOT / "docs" / "dev-cd-boot-init-design.md"


def code_lines(text):
    """Source lines with comments stripped, blank lines dropped, labels kept as their own entries."""
    out = []
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if line:
            out.append(line)
    return out


def section(lines, start, stop=None):
    """Lines from the first entry equal to `start` up to (not including) the first entry equal to `stop`."""
    i = lines.index(start)
    if stop is None:
        return lines[i + 1:]
    j = lines.index(stop, i + 1)
    return lines[i + 1:j]


def check_media_gate(lines):
    """The DEV CD-boot path may reach `jmp cd_entry` only after cd_media_state returned $01."""
    jsr = lines.index("jsr cd_media_state")
    cmp_ = lines.index("cmp #$01", jsr)
    bne = lines.index("bne dev_media_halt", cmp_)
    jmp = lines.index("jmp cd_entry", bne)
    if not (jsr < cmp_ < bne < jmp):
        raise ValueError("media gate order")


def check_prefix(lines):
    """Before the media probe: MPR0=$FF, MPR1=$F8 and SP=$FF, each in that order, and nothing else touches MPR."""
    body = section(lines, "dev_boot_entry:", "jsr cd_media_state")
    expected = ["cld", "lda #$ff", "tam #$01", "lda #$f8", "tam #$02", "ldx #$ff", "txs"]
    if [s.lower() for s in body] != expected:
        raise ValueError(f"prefix {body}")


class ResetStopLoopTests(unittest.TestCase):
    def test_default_reset_is_stop_loop_in_boot_s(self):
        lines = code_lines((ROOT / "src/boot.s").read_text(encoding="utf-8"))
        start = lines.index("reset_not_implemented:")
        self.assertEqual(lines[start + 1:start + 5], ["sei", "ldx #$FF", "@loop:", "bra @loop"])

    def test_default_reset_vector_targets_that_label(self):
        text = (ROOT / "src/boot.s").read_text(encoding="utf-8")
        self.assertEqual(text.count(".word reset_not_implemented"), 5)

    def test_build_verifier_pins_the_stop_loop_bytes(self):
        text = (ROOT / "tools/build_rom.py").read_text(encoding="utf-8")
        self.assertIn("bytes.fromhex('78 a2 ff 80 fe')", text)


class DevBootPrefixTests(unittest.TestCase):
    def setUp(self):
        self.lines = code_lines((ROOT / "src/dev_boot_internal.s").read_text(encoding="utf-8"))

    def test_prefix_sets_mpr0_mpr1_and_sp_before_media_probe(self):
        check_prefix(self.lines)

    def test_media_gate_precedes_cd_entry(self):
        check_media_gate(self.lines)

    def test_reset_entry_uses_the_same_mpr0_mpr1_values(self):
        reset = code_lines((ROOT / "src/reset_entry.s").read_text(encoding="utf-8"))
        body = section(reset, "reset_entry:", "reset_mpr_ready:")
        self.assertIn("tam #$01", body)
        self.assertIn("tam #$02", body)
        self.assertEqual([s.lower() for s in body if s.lower().startswith("lda #")], ["lda #$ff", "lda #$f8"])

    def test_media_gate_order_check_rejects_a_reordered_gate(self):
        mutated = list(self.lines)
        mutated.remove("jmp cd_entry")
        mutated.insert(mutated.index("jsr cd_media_state"), "jmp cd_entry")
        with self.assertRaises(ValueError):
            check_media_gate(mutated)

    def test_prefix_check_rejects_a_missing_mpr1_write(self):
        mutated = [s for s in self.lines if s != "tam #$02"]
        with self.assertRaises(ValueError):
            check_prefix(mutated)


class MediaResultAllocationTests(unittest.TestCase):
    def test_ms_res_block_is_disjoint_from_the_other_dev_boot_blocks(self):
        from tools.build_dev_boot_internal import MS_RES
        blocks = {  # (start, length) from the allocation table in src/dev_boot_internal.s
            "cd_load RES": (0x2300, 0x20), "staging BUF": (0x2800, 0x800), "payload EXEC": (0x3000, 0x100),
            "marker": (0x3F00, 0x07), "RETVEC": (0x3F10, 0x02), "EXECVEC": (0x3F12, 0x02),
            "cd_media_state MS_RES": (MS_RES, 0x07),
        }
        self.assertEqual(MS_RES, 0x3F40)
        spans = sorted(blocks.values())
        for (a, alen), (b, _) in zip(spans, spans[1:]):
            self.assertLessEqual(a + alen, b, f"overlap at ${a:04X}")


class DesignDocTests(unittest.TestCase):
    def test_design_doc_cites_the_reset_loop_location(self):
        self.assertTrue(DESIGN_DOC.is_file(), "docs/dev-cd-boot-init-design.md is missing")
        text = DESIGN_DOC.read_text(encoding="utf-8")
        boot = (ROOT / "src/boot.s").read_text(encoding="utf-8").splitlines()
        loop_line = next(i for i, line in enumerate(boot, 1) if line.strip().startswith("@loop:"))
        self.assertIn(f"src/boot.s:{loop_line}", text)
        self.assertIn("#551", text)


if __name__ == "__main__":
    unittest.main()
