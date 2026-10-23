"""Issue #306: read-only BM_FREE fixture and judge checks (tools/bm_free_fixture.py). Synthetic images only.

Expected values are hand-computed and written as literal bytes. No test calls the module's image builder or its
checksum helper, so changing the checksum formula or the layout in tools/buram_free_judge.py fails these tests.

Synthetic layout (copied as literals from the #396 model docstring, not imported):
  [0:4] b"SBR1", [4] version 1, [5:15] reserved zero, [15] checksum, [16:31] table (15 blocks, 0x00 free / 0x01 used),
  SIZE 2048, BLOCK 128. Hand rule for the checksum of a valid image: (0x53 + 0x42 + 0x52 + 0x31 + 0x01 + used) & 0xFF,
  so the header part is 0x119 = 0x19, and the checksum is 0x19 + (number of used blocks), low byte.

Positive: normal header. Negative: uninitialized, invalid, size, typed input. Boundary: capacity, table values.
False positives: every single-bit flip of the checksummed header and table is rejected. Read-only: writes raise,
the judge never mutates a caller's bytearray, and no file or OS write is attempted while judging.
"""
import ast
import builtins
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import bm_free_fixture as bf  # noqa: E402
import buram_free_judge as bj  # noqa: E402

FORBIDDEN_MODULES = {'os', 'io', 'pathlib', 'shutil', 'tempfile', 'subprocess', 'socket', 'importlib', 'builtins'}

SIZE, TABLE, NDATA, BLOCK = 2048, 16, 15, 128


def _table(used=()):
    return bytes(1 if i in used else 0 for i in range(NDATA))


def _image(table, checksum):
    """Literal layout: 4 magic + 1 version + 10 reserved + 1 checksum = 16 header bytes, 15 table bytes, padding."""
    return b'SBR1' + b'\x01' + bytes(10) + bytes([checksum]) + bytes(table) + bytes(SIZE - TABLE - NDATA)


ALL_FREE = _image(_table(), 0x19)                 # 0x19 + 0 used
BLOCK0_USED = _image(_table({0}), 0x1A)           # 0x19 + 1 used
USED_1_2_14 = _image(_table({1, 2, 14}), 0x1C)    # 0x19 + 3 used
ALL_USED = _image(_table(range(NDATA)), 0x28)     # 0x19 + 15 used
TABLE_VALUE_2 = _image(bytes([2]) + bytes(14), 0x1B)  # table value 0x02 with checksum 0x19 + 2 fixed by hand


class BmFreeFixtureTest(unittest.TestCase):
    def test_hand_layout_literals_are_consistent(self):
        self.assertEqual(len(ALL_FREE), SIZE)
        self.assertEqual((sum(b'SBR1') + 1) & 0xFF, 0x19)
        self.assertEqual(NDATA * BLOCK, 1920)

    def test_scenarios_match_expected_verdict(self):
        for name, (image, expected) in bf.scenarios().items():
            with self.subTest(name=name):
                self.assertEqual(bj.judge_free(bytearray(bytes(image)))[0], expected)

    # positive
    def test_all_free_fixed_bytes_count_15(self):
        self.assertEqual(bj.judge_free(bytearray(ALL_FREE)), (bj.OK, 15, mock.ANY))

    def test_block0_used_fixed_bytes_count_14(self):
        self.assertEqual(bj.judge_free(bytearray(BLOCK0_USED))[:2], (bj.OK, 14))

    def test_used_blocks_1_2_14_fixed_bytes_count_12(self):
        self.assertEqual(bj.judge_free(bytearray(USED_1_2_14))[:2], (bj.OK, 12))

    def test_all_used_fixed_bytes_count_0(self):
        self.assertEqual(bj.judge_free(bytearray(ALL_USED))[:2], (bj.OK, 0))

    # negative: uninitialized
    def test_erased_and_blank_images_are_unformatted_with_no_free_count(self):
        for raw in (bj.ERASED, bj.BLANK):
            verdict, free, _ = bj.judge_free(bytearray(raw))
            self.assertEqual((verdict, free), (bj.UNFORMATTED, None))

    # negative: invalid
    def test_invalid_headers_are_rejected_without_a_free_count(self):
        garbage = bytes((i * 37) % 256 for i in range(SIZE))
        for name, raw in (
            ('magic', b'XXXX' + ALL_FREE[4:]),
            ('garbage', garbage),
            ('reserved byte', ALL_FREE[:5] + b'\x01' + ALL_FREE[6:]),
            ('checksum', ALL_FREE[:15] + bytes([0x99]) + ALL_FREE[16:]),
            ('table value 0x02 (checksum 0x1B)', TABLE_VALUE_2),
        ):
            with self.subTest(name=name):
                verdict, free, _ = bj.judge_free(bytearray(raw))
                self.assertEqual((verdict, free), (bj.INVALID_HEADER, None))

    # boundary
    def test_capacity_boundaries_zero_one_and_all_blocks(self):
        self.assertEqual(bj.judge_free(bytearray(ALL_FREE))[1], NDATA)
        self.assertEqual(bj.judge_free(bytearray(BLOCK0_USED))[1], NDATA - 1)
        self.assertEqual(bj.judge_free(bytearray(ALL_USED))[1], 0)

    def test_block_index_outside_capacity_is_refused_by_the_builder(self):
        with self.assertRaises(ValueError):
            bj.make_image(used={NDATA})

    def test_size_boundaries_are_rejected_not_counted(self):
        for raw in (b'', ALL_FREE[:-1], ALL_FREE + b'\x00'):
            verdict, free, _ = bj.judge_free(bytearray(raw))
            self.assertEqual((verdict, free), (bj.BAD_SIZE, None))

    def test_table_byte_boundary_0x01_ok_and_0x02_rejected(self):
        self.assertEqual(bj.judge_free(bytearray(BLOCK0_USED))[:2], (bj.OK, 14))
        self.assertEqual(bj.judge_free(bytearray(TABLE_VALUE_2))[0], bj.INVALID_HEADER)

    # false positives
    def test_no_single_bit_flip_in_header_or_table_is_accepted(self):
        self.assertEqual(bj.judge_free(bytearray(USED_1_2_14))[0], bj.OK)
        self.assertEqual(bj.mutation_self_check(USED_1_2_14), (TABLE + NDATA) * 8)

    def test_bytes_and_bytearray_views_agree(self):
        self.assertEqual(bj.judge_free(ALL_FREE), bj.judge_free(bytearray(ALL_FREE)))

    # negative: typed input and out-of-range reads are refused
    def test_judge_refuses_str_and_fixture_objects(self):
        with self.assertRaises(TypeError):
            bj.judge_free("str")
        with self.assertRaises(TypeError):
            bj.judge_free(bf.ReadOnlyImage(ALL_FREE))
        with self.assertRaises(TypeError):
            bj.judge_free(123)

    def test_fixture_refuses_non_bytes_construction(self):
        for bad in (123, "str", None):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                bf.ReadOnlyImage(bad)

    def test_fixture_read_out_of_range_is_refused(self):
        image = bf.ReadOnlyImage(ALL_FREE)
        self.assertEqual(image.read(0, 4), b'SBR1')
        self.assertEqual(image.read(SIZE - 1, 1), ALL_FREE[-1:])
        for offset, length in ((SIZE, 1), (SIZE - 1, 2), (-1, 1), (0, -1)):
            with self.subTest(offset=offset, length=length), self.assertRaises(IndexError):
                image.read(offset, length)

    # read-only
    def test_fixture_refuses_every_write(self):
        image = bf.ReadOnlyImage(ALL_FREE)
        with self.assertRaises(PermissionError):
            image[0] = 0
        with self.assertRaises(PermissionError):
            del image[0]
        self.assertFalse(hasattr(image, '__dict__'))

    def test_fixture_copies_caller_buffer(self):
        source = bytearray(ALL_FREE)
        image = bf.ReadOnlyImage(source)
        source[TABLE] = 0x01
        self.assertEqual(bytes(image), ALL_FREE)

    def test_judge_does_not_mutate_a_bytearray_input(self):
        image = bytearray(BLOCK0_USED)
        saved = bytes(image)
        verdict, free, _ = bj.judge_free(image)
        self.assertEqual((verdict, free), (bj.OK, 14))
        self.assertEqual(bytes(image), saved)

    def test_judging_never_mutates_the_fixture_and_attempts_no_write(self):
        raws = [bytes(image) for image, _ in bf.scenarios().values()]
        before = [bytes(raw) for raw in raws]
        attempts = []

        def refuse(*args, **kwargs):
            attempts.append(args)
            raise AssertionError(f"I/O attempted during judging: {args}")

        with mock.patch.object(builtins, 'open', refuse), mock.patch.object(io, 'open', refuse), \
                mock.patch.object(os, 'open', refuse), mock.patch.object(os, 'write', refuse):
            for raw in raws:
                buf = bytearray(raw)
                bj.judge_free(buf)
                self.assertEqual(bytes(buf), raw)
        self.assertEqual(attempts, [])
        self.assertEqual(raws, before)

    def test_fixture_module_has_no_io_imports(self):
        tree = ast.parse((ROOT / 'tools' / 'bm_free_fixture.py').read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        self.assertEqual(imported & FORBIDDEN_MODULES, set())


if __name__ == '__main__':
    unittest.main()
