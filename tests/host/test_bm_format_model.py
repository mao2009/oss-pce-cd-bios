"""BM_FORMAT safety model (Issue #303): core-free checks on synthetic images only.

Model: tools/bm_format_model.py. Signature and capacity are project-internal fixture values,
not the System Card backup-RAM format; the BIOS API stays BLOCKED_ABI (#302).
"""
import ast
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bm_format_model as bm  # noqa: E402

MODEL_PATH = Path(__file__).resolve().parents[2] / "tools" / "bm_format_model.py"

# The model currently imports nothing. Extend deliberately if that ever changes.
ALLOWED_IMPORTS = frozenset()
BANNED_NAMES = {"__import__", "importlib", "open", "os", "io", "pathlib", "tempfile",
                "subprocess", "socket"}


def formatted(size=bm.MIN_CAPACITY):
    image = bytearray(size)
    image[:len(bm.SIGNATURE)] = bm.SIGNATURE
    return bytes(image)


def blank(size=bm.MIN_CAPACITY):
    return bytes(size)


class PositiveTest(unittest.TestCase):
    def test_blank_image_is_formatted_and_keeps_size(self):
        status, out = bm.format_image(blank(256), allow_destructive=True)
        self.assertEqual(status, bm.OK)
        self.assertEqual(len(out), 256)
        self.assertEqual(out[:len(bm.SIGNATURE)], bm.SIGNATURE)
        self.assertEqual(bm.classify(out), bm.VALID)

    def test_invalid_signature_is_recovered_by_format(self):
        corrupt = bytearray(formatted())
        corrupt[0] ^= 0xFF
        self.assertEqual(bm.classify(bytes(corrupt)), bm.INVALID)
        status, out = bm.format_image(bytes(corrupt), allow_destructive=True)
        self.assertEqual(status, bm.OK)
        self.assertEqual(bm.classify(out), bm.VALID)

    def test_formatted_result_zeroes_previous_body(self):
        dirty = bytearray(b"\xAA" * bm.MIN_CAPACITY)
        status, out = bm.format_image(bytes(dirty), allow_destructive=True)
        self.assertEqual(status, bm.OK)
        self.assertEqual(out[bm.HEADER_LEN - 8:bm.HEADER_LEN], bytes(8))
        self.assertEqual(out[bm.HEADER_LEN:], bytes(bm.MIN_CAPACITY - bm.HEADER_LEN))


class NegativeTest(unittest.TestCase):
    def test_unconfirmed_request_refuses_and_keeps_image(self):
        for flag in (False, None, 1, "yes"):
            with self.subTest(flag=flag):
                image = blank()
                status, out = bm.format_image(image, allow_destructive=flag)
                self.assertEqual(status, bm.REFUSED_UNCONFIRMED)
                self.assertEqual(out, image)

    def test_default_is_fail_closed(self):
        image = blank()
        self.assertEqual(bm.format_image(image), (bm.REFUSED_UNCONFIRMED, image))

    def test_double_initialization_is_refused_and_keeps_data(self):
        image = bytearray(formatted())
        image[bm.HEADER_LEN] = 0x5A  # user payload byte
        image = bytes(image)
        status, out = bm.format_image(image, allow_destructive=True)
        self.assertEqual(status, bm.ALREADY_FORMATTED)
        self.assertEqual(out, image)

    def test_unconfirmed_on_well_formed_image_refuses(self):
        image = formatted()
        status, out = bm.format_image(image)
        self.assertEqual(status, bm.REFUSED_UNCONFIRMED)
        self.assertEqual(out, image)

    def test_unconfirmed_on_short_image_refuses_not_capacity(self):
        image = blank(bm.MIN_CAPACITY - 1)
        status, out = bm.format_image(image, allow_destructive=False)
        self.assertEqual(status, bm.REFUSED_UNCONFIRMED)
        self.assertEqual(out, image)

    def test_confirmed_on_formatted_image_is_double_init_not_capacity(self):
        image = formatted()
        status, out = bm.format_image(image, allow_destructive=True)
        self.assertEqual(status, bm.ALREADY_FORMATTED)
        self.assertEqual(out, image)

    def test_short_formatted_image_is_capacity_not_double_init(self):
        image = formatted(bm.MIN_CAPACITY - 1)
        status, out = bm.format_image(image, allow_destructive=True)
        self.assertEqual(status, bm.INSUFFICIENT_CAPACITY)
        self.assertEqual(out, image)

    def test_insufficient_capacity_keeps_image(self):
        image = blank(bm.MIN_CAPACITY - 1)
        status, out = bm.format_image(image, allow_destructive=True)
        self.assertEqual(status, bm.INSUFFICIENT_CAPACITY)
        self.assertEqual(out, image)

    def test_non_bytes_input_is_rejected(self):
        with self.assertRaises(TypeError):
            bm.format_image([0] * bm.MIN_CAPACITY, allow_destructive=True)
        with self.assertRaises(TypeError):
            bm.format_image("not-bytes", allow_destructive=True)


class BoundaryTest(unittest.TestCase):
    def test_capacity_boundary(self):
        self.assertEqual(bm.format_image(blank(bm.MIN_CAPACITY - 1), allow_destructive=True)[0],
                         bm.INSUFFICIENT_CAPACITY)
        self.assertEqual(bm.format_image(blank(bm.MIN_CAPACITY), allow_destructive=True)[0], bm.OK)
        self.assertEqual(bm.format_image(blank(bm.MIN_CAPACITY + 1), allow_destructive=True)[0], bm.OK)

    def test_empty_image_is_insufficient(self):
        self.assertEqual(bm.format_image(b"", allow_destructive=True)[0], bm.INSUFFICIENT_CAPACITY)

    def test_signature_off_by_one_byte_is_invalid(self):
        for index in range(len(bm.SIGNATURE)):
            with self.subTest(index=index):
                image = bytearray(formatted())
                image[index] ^= 0x01
                self.assertEqual(bm.classify(bytes(image)), bm.INVALID)
                self.assertEqual(bm.format_image(bytes(image), allow_destructive=True)[0], bm.OK)

    def test_short_image_classifies_as_capacity_not_signature(self):
        self.assertEqual(bm.classify(formatted(bm.MIN_CAPACITY - 1)), bm.SHORT)


class TempFileTest(unittest.TestCase):
    """Only a temporary file is touched; the model itself never opens files."""

    def test_temp_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic_bm.bin"
            path.write_bytes(blank(128))
            status, out = bm.format_image(path.read_bytes(), allow_destructive=True)
            self.assertEqual(status, bm.OK)
            path.write_bytes(out)
            self.assertEqual(bm.classify(path.read_bytes()), bm.VALID)
            again, unchanged = bm.format_image(path.read_bytes(), allow_destructive=True)
            self.assertEqual(again, bm.ALREADY_FORMATTED)
            self.assertEqual(unchanged, out)

    def test_model_imports_are_allowlisted(self):
        tree = ast.parse(MODEL_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add("." * node.level + (node.module or "").split(".")[0])
        self.assertLessEqual(imported, ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS)

    def test_model_has_no_io_or_process_names(self):
        tree = ast.parse(MODEL_PATH.read_text(encoding="utf-8"))
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
        self.assertFalse(used & BANNED_NAMES, used & BANNED_NAMES)

if __name__ == "__main__":
    unittest.main()
