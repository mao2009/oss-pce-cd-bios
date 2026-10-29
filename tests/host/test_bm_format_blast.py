"""Blast-radius model (Issue #303): non-executing, synthetic images only. Model: tools/bm_format_blast.py."""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bm_format_blast as blast  # noqa: E402
import bm_format_model as bm  # noqa: E402

MODEL_PATH = Path(__file__).resolve().parents[2] / "tools" / "bm_format_blast.py"


def formatted(size=bm.MIN_CAPACITY):
    image = bytearray(size)
    image[:len(bm.SIGNATURE)] = bm.SIGNATURE
    return bytes(image)


class BlastRadiusTest(unittest.TestCase):
    def test_blank_image_changes_only_signature_bytes(self):
        r = blast.blast_radius(bytes(bm.MIN_CAPACITY), allow_destructive=True)
        self.assertEqual(r["model_status"], bm.OK)
        # SIGNATURE has two trailing zero bytes, so they do not change on a blank image.
        self.assertEqual(r["changed_ranges"], [(0, 6)])
        self.assertFalse(r["user_data_at_risk"])

    def test_dirty_invalid_image_reports_destroyed_data(self):
        r = blast.blast_radius(b"\xAA" * bm.MIN_CAPACITY, allow_destructive=True)
        self.assertEqual(r["model_status"], bm.OK)
        self.assertEqual(r["changed_ranges"], [(0, bm.MIN_CAPACITY)])
        self.assertEqual(r["nonzero_destroyed_count"], bm.MIN_CAPACITY)
        self.assertTrue(r["user_data_at_risk"])

    def test_partial_change_reports_disjoint_ranges(self):
        image = bytearray(bm.MIN_CAPACITY)
        image[0] = 0xAA
        image[20] = 0x55
        r = blast.blast_radius(bytes(image), allow_destructive=True)
        self.assertEqual(r["changed_ranges"], [(0, 6), (20, 21)])
        # Only bytes 0 and 20 held non-zero data; bytes 1..5 were zero and merely gain the signature.
        self.assertEqual(r["nonzero_destroyed_count"], 2)

    def test_valid_signature_changes_nothing(self):
        r = blast.blast_radius(formatted(), allow_destructive=True)
        self.assertEqual(r["model_status"], bm.ALREADY_FORMATTED)
        self.assertEqual(r["changed_ranges"], [])
        self.assertFalse(r["user_data_at_risk"])

    def test_unconfirmed_and_short_images_change_nothing(self):
        dirty = b"\xAA" * bm.MIN_CAPACITY
        r = blast.blast_radius(dirty, allow_destructive=False)
        self.assertEqual((r["model_status"], r["changed_ranges"]), (bm.REFUSED_UNCONFIRMED, []))
        r = blast.blast_radius(b"\xAA" * (bm.MIN_CAPACITY - 1), allow_destructive=True)
        self.assertEqual((r["model_status"], r["changed_ranges"]), (bm.INSUFFICIENT_CAPACITY, []))

    def test_output_never_claims_bios_or_approval(self):
        r = blast.blast_radius(b"\xAA" * bm.MIN_CAPACITY, allow_destructive=True)
        self.assertEqual(r["verdict"], blast.NOT_EXECUTED)
        self.assertEqual(r["bios_write_set"], "UNKNOWN")
        self.assertIs(r["release_eligible"], False)

    def test_input_buffer_is_not_mutated(self):
        image = bytearray(b"\xAA" * bm.MIN_CAPACITY)
        blast.blast_radius(image, allow_destructive=True)
        self.assertEqual(bytes(image), b"\xAA" * bm.MIN_CAPACITY)

    def test_non_bytes_input_is_rejected(self):
        with self.assertRaises(TypeError):
            blast.blast_radius("SYNBM", allow_destructive=True)

    def test_model_imports_only_the_format_model(self):
        tree = ast.parse(MODEL_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertEqual(imported, {"bm_format_model"})


if __name__ == "__main__":
    unittest.main()
