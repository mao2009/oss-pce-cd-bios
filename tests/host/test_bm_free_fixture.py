"""Issue #306: read-only BM_FREE fixture and judge checks (tools/bm_free_fixture.py). Synthetic images only.

Positive: normal header. Negative: uninitialized and invalid headers. Boundary: capacity, size, table values.
False positives: every single-bit flip of the checksummed header and table is rejected. Read-only: writes raise,
the judge never mutates its input, and no file or OS write is attempted while judging.
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


class BmFreeFixtureTest(unittest.TestCase):
    def test_scenarios_match_expected_verdict(self):
        for name, (image, expected) in bf.scenarios().items():
            with self.subTest(name=name):
                self.assertEqual(bj.judge_free(bytes(image))[0], expected)

    # positive
    def test_normal_header_counts_all_free_blocks(self):
        verdict, free, _ = bj.judge_free(bytes(bf.ReadOnlyImage(bj.make_image())))
        self.assertEqual((verdict, free), (bj.OK, bj.NDATA))

    def test_normal_header_counts_used_blocks_out(self):
        verdict, free, _ = bj.judge_free(bytes(bf.ReadOnlyImage(bj.make_image(used={1, 2, 14}))))
        self.assertEqual((verdict, free), (bj.OK, bj.NDATA - 3))

    # negative: uninitialized
    def test_erased_and_blank_images_are_unformatted_with_no_free_count(self):
        for raw in (bj.ERASED, bj.BLANK):
            verdict, free, _ = bj.judge_free(bytes(bf.ReadOnlyImage(raw)))
            self.assertEqual((verdict, free), (bj.UNFORMATTED, None))

    # negative: invalid
    def test_invalid_headers_are_rejected_without_a_free_count(self):
        fresh = bj.make_image()
        garbage = bytes((i * 37) % 256 for i in range(bj.SIZE))
        for name, raw in (
            ('magic', b'XXXX' + fresh[4:]),
            ('garbage', garbage),
            ('reserved byte', fresh[:5] + b'\x01' + fresh[6:]),
            ('checksum', fresh[:15] + bytes([fresh[15] ^ 0x80]) + fresh[16:]),
        ):
            with self.subTest(name=name):
                verdict, free, _ = bj.judge_free(bytes(bf.ReadOnlyImage(raw)))
                self.assertEqual((verdict, free), (bj.INVALID_HEADER, None))

    # boundary
    def test_capacity_boundaries_zero_one_and_all_blocks(self):
        self.assertEqual(bj.judge_free(bj.make_image())[1], bj.NDATA)
        self.assertEqual(bj.judge_free(bj.make_image(used={0})), (bj.OK, bj.NDATA - 1, mock.ANY))
        self.assertEqual(bj.judge_free(bj.make_image(used=range(bj.NDATA)))[:2], (bj.OK, 0))
        self.assertEqual(bj.NDATA * bj.BLOCK, 1920)

    def test_block_index_outside_capacity_is_refused_by_the_builder(self):
        with self.assertRaises(ValueError):
            bj.make_image(used={bj.NDATA})

    def test_size_boundaries_are_rejected_not_counted(self):
        fresh = bj.make_image()
        for raw in (b'', fresh[:-1], fresh + b'\x00'):
            verdict, free, _ = bj.judge_free(bytes(bf.ReadOnlyImage(raw)))
            self.assertEqual((verdict, free), (bj.BAD_SIZE, None))

    def test_table_byte_boundary_0x01_ok_and_0x02_rejected(self):
        fresh = bytearray(bj.make_image())
        fresh[bj.TABLE] = 0x01
        fresh[15] = bj._checksum(fresh)
        self.assertEqual(bj.judge_free(bytes(fresh))[0], bj.OK)
        fresh[bj.TABLE] = 0x02
        fresh[15] = bj._checksum(fresh)
        self.assertEqual(bj.judge_free(bytes(fresh))[0], bj.INVALID_HEADER)

    # false positives
    def test_no_single_bit_flip_in_header_or_table_is_accepted(self):
        self.assertEqual(bj.mutation_self_check(bj.make_image(used={3})), (bj.TABLE + bj.NDATA) * 8)

    def test_bytes_and_bytearray_views_agree(self):
        raw = bj.make_image(used={5})
        self.assertEqual(bj.judge_free(raw), bj.judge_free(bytearray(raw)))

    # read-only
    def test_fixture_refuses_every_write(self):
        image = bf.ReadOnlyImage(bj.make_image())
        with self.assertRaises(PermissionError):
            image[0] = 0
        with self.assertRaises(PermissionError):
            del image[0]
        self.assertFalse(hasattr(image, '__dict__'))

    def test_fixture_copies_caller_buffer(self):
        source = bytearray(bj.make_image())
        image = bf.ReadOnlyImage(source)
        source[bj.TABLE] = 0x01
        self.assertEqual(bytes(image), bj.make_image())

    def test_judging_never_mutates_the_fixture_and_attempts_no_write(self):
        images = [image for image, _ in bf.scenarios().values()]
        before = [bytes(image) for image in images]
        attempts = []

        def refuse(*args, **kwargs):
            attempts.append(args)
            raise AssertionError(f"I/O attempted during judging: {args}")

        with mock.patch.object(builtins, 'open', refuse), mock.patch.object(io, 'open', refuse), \
                mock.patch.object(os, 'open', refuse), mock.patch.object(os, 'write', refuse):
            for image in images:
                bj.judge_free(bytes(image))
        self.assertEqual(attempts, [])
        self.assertEqual([bytes(image) for image in images], before)

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
