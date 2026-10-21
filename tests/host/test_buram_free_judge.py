"""Issue #306: BM_FREE free-space judge HOST MODEL (tools/buram_free_judge.py). Synthetic images only.

Not a BIOS routine and not a real backup-RAM check: no file is opened, no save is read or written. Checks: formatted
images count free blocks exactly (positive), unformatted / invalid headers / wrong size are rejected (negative),
capacity boundaries, every checksummed single-bit flip is rejected (no false positive), and the module has no I/O.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import buram_free_judge as bj  # noqa: E402


FORBIDDEN_MODULES = {'os', 'io', 'pathlib', 'shutil', 'tempfile', 'subprocess', 'socket', 'importlib', 'builtins'}
FORBIDDEN_ATTRS = {'read_bytes', 'write_bytes', 'read_text', 'write_text'}


def _patched(image, index, value):
    data = bytearray(image)
    data[index] = value
    return bytes(data)


class BuramFreeJudgeTest(unittest.TestCase):
    # positive
    def test_fresh_formatted_image_has_all_blocks_free(self):
        verdict, free, _ = bj.judge_free(bj.make_image())
        self.assertEqual((verdict, free), (bj.OK, bj.NDATA))

    def test_free_count_follows_allocation_table(self):
        verdict, free, _ = bj.judge_free(bj.make_image(used={0, 4, 9}))
        self.assertEqual((verdict, free), (bj.OK, bj.NDATA - 3))

    def test_bytearray_input_is_accepted_and_not_modified(self):
        image = bytearray(bj.make_image(used={2}))
        before = bytes(image)
        self.assertEqual(bj.judge_free(image)[0], bj.OK)
        self.assertEqual(bytes(image), before)

    # boundaries
    def test_capacity_boundaries(self):
        self.assertEqual(bj.NDATA * bj.BLOCK, 1920)
        self.assertEqual(bj.judge_free(bj.make_image(used=range(bj.NDATA)))[:2], (bj.OK, 0))  # full, still valid
        self.assertEqual(bj.judge_free(bj.make_image(used={0}))[:2], (bj.OK, bj.NDATA - 1))
        self.assertEqual(bj.judge_free(bj.make_image(used={bj.NDATA - 1}))[:2], (bj.OK, bj.NDATA - 1))

    def test_last_block_index_is_bounded(self):
        with self.assertRaises(ValueError):
            bj.make_image(used={bj.NDATA})
        with self.assertRaises(ValueError):
            bj.make_image(used={-1})

    def test_wrong_size_is_bad_size_not_free_space(self):
        image = bj.make_image()
        for size in (0, bj.SIZE - 1, bj.SIZE + 1):
            with self.subTest(size=size):
                verdict, free, _ = bj.judge_free((image + bytes(size))[:size])
                self.assertEqual((verdict, free), (bj.BAD_SIZE, None))

    # negative: unformatted
    def test_erased_and_blank_images_are_unformatted(self):
        for image in (bj.ERASED, bj.BLANK):
            with self.subTest(image=image[:1]):
                verdict, free, _ = bj.judge_free(image)
                self.assertEqual((verdict, free), (bj.UNFORMATTED, None))

    # negative: invalid header
    def test_magic_mismatch_is_invalid_header(self):
        image = bj.make_image()
        self.assertEqual(bj.judge_free(_patched(image, 0, ord('X')))[0], bj.INVALID_HEADER)

    def test_nonuniform_image_without_magic_is_invalid_not_unformatted(self):
        image = bytearray(bj.SIZE)
        image[100] = 0x5A
        self.assertEqual(bj.judge_free(bytes(image))[0], bj.INVALID_HEADER)

    def test_wrong_version_is_invalid_header(self):
        self.assertEqual(bj.judge_free(_patched(bj.make_image(), 4, 2))[0], bj.INVALID_HEADER)

    def test_checksum_mismatch_is_invalid_header(self):
        image = bj.make_image()
        self.assertEqual(bj.judge_free(_patched(image, 15, (image[15] + 1) & 0xFF))[0], bj.INVALID_HEADER)

    def test_table_value_out_of_range_is_invalid_header(self):
        # Even with a recomputed checksum, a table byte other than 0x00/0x01 must not be counted as free space.
        data = bytearray(bj.make_image())
        data[bj.TABLE + 3] = 0x02
        data[15] = bj._checksum(data)
        verdict, free, _ = bj.judge_free(bytes(data))
        self.assertEqual((verdict, free), (bj.INVALID_HEADER, None))

    # regression: no false positive on any single-bit corruption of the checksummed region
    def test_every_single_bit_flip_is_rejected(self):
        self.assertEqual(bj.mutation_self_check(bj.make_image(used={1, 7})), (bj.TABLE + bj.NDATA) * 8)

    def test_reserved_nonzero_byte_with_valid_checksum_is_invalid_header(self):
        # Reserved bytes [5:15] must be zero; a recomputed checksum must not let a non-zero reserved byte through.
        data = bytearray(bj.make_image())
        data[9] = 0x01
        data[15] = bj._checksum(data)
        self.assertEqual(bj.judge_free(bytes(data))[:2], (bj.INVALID_HEADER, None))

    @unittest.expectedFailure
    def test_unchecked_byte_outside_model_is_documented_gap(self):
        # KNOWN GAP (documented, not a feature): byte 31 lies outside the checksummed table in this model, so a flip
        # there is still accepted as OK. This test asserts the CORRECT behaviour (rejection) and is marked
        # expectedFailure, so the suite passes today. If a future fix rejects it, this becomes an UNEXPECTED SUCCESS,
        # which fails the run on purpose: the gap is closed, so remove the expectedFailure decorator and update this
        # test (and the model docs) instead of leaving it as a known-gap pin.
        image = bj.make_image()
        self.assertNotEqual(bj.judge_free(_patched(image, 31, 0xAA))[0], bj.OK)

    # isolation: the judge does no file I/O and imports no file-access modules
    def test_module_has_no_file_io(self):
        tree = ast.parse((ROOT / 'tools' / 'buram_free_judge.py').read_text(encoding='utf-8'))
        imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse(imported & FORBIDDEN_MODULES, imported)
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn('open', calls)
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse(attrs & FORBIDDEN_ATTRS, attrs & FORBIDDEN_ATTRS)


if __name__ == '__main__':
    unittest.main()
