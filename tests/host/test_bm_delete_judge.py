"""Issue #38: BM_DELETE delete-judge HOST MODEL tests (tools/bm_delete_judge.py). Synthetic images only.

Not a BIOS routine and not a real backup-RAM check: no file is opened, no save is read or written. Checks:
formatted images delete exactly the named entry with survivors byte-identical and in order (positive),
missing/unformatted/wrong input are refused without mutation (negative), capacity and 12-byte name
boundaries, every name single-bit flip of a one-entry image is rejected (no false positive), and the
module has no I/O.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import bm_delete_judge as bj  # noqa: E402


FORBIDDEN_MODULES = {'os', 'io', 'pathlib', 'shutil', 'tempfile', 'subprocess', 'socket', 'importlib', 'builtins'}
FORBIDDEN_ATTRS = {'read_bytes', 'write_bytes', 'read_text', 'write_text', 'open'}
# Exact top-level import allowlist: the judge imports nothing (pure stdlib, no imports at all).
ALLOWED_IMPORTS = {'bm_delete_judge': set()}

N_A = b"\x00\x00FILE_A    "
N_B = b"\x00\x00FILE_B    "
N_C = b"\x00\x00FILE_C    "


def _image(entries):
    return bj.make_image(entries=entries)


class BmDeleteJudgeTest(unittest.TestCase):
    # positive
    def test_delete_sole_entry_leaves_empty_image(self):
        status, out = bj.mock_delete(_image([(N_A, b"hello")]), N_A)
        self.assertEqual(status, bj.OK)
        self.assertEqual(out["entries"], [])
        self.assertEqual(out["end"], 0)
        self.assertEqual(bj.mock_free(out), out["capacity"])

    def test_delete_head_preserves_order_and_bytes(self):
        img = _image([(N_A, b"a"), (N_B, b"bb"), (N_C, b"ccc")])
        status, out = bj.mock_delete(img, N_A)
        self.assertEqual(status, bj.OK)
        self.assertEqual([bytes(e["name"]) for e in out["entries"]], [N_B, N_C])
        self.assertEqual([bytes(e["data"]) for e in out["entries"]], [b"bb", b"ccc"])

    def test_delete_middle_preserves_neighbors(self):
        img = _image([(N_A, b"a"), (N_B, b"bb"), (N_C, b"ccc")])
        status, out = bj.mock_delete(img, N_B)
        self.assertEqual(status, bj.OK)
        self.assertEqual([bytes(e["name"]) for e in out["entries"]], [N_A, N_C])
        self.assertEqual([bytes(e["data"]) for e in out["entries"]], [b"a", b"ccc"])

    def test_delete_tail_keeps_prefix(self):
        img = _image([(N_A, b"a"), (N_B, b"bb"), (N_C, b"ccc")])
        status, out = bj.mock_delete(img, N_C)
        self.assertEqual(status, bj.OK)
        self.assertEqual([bytes(e["name"]) for e in out["entries"]], [N_A, N_B])

    def test_deleted_capacity_is_reusable_by_mock_add(self):
        img = _image([(N_A, b"a"), (N_B, b"bb")])
        free_before = bj.mock_free(img)
        _, deleted = bj.mock_delete(img, N_A)
        self.assertGreater(bj.mock_free(deleted), free_before)
        restored = bj.mock_add(deleted, N_A, b"a")
        self.assertEqual([bytes(e["name"]) for e in restored["entries"]], [N_B, N_A])
        self.assertEqual(bytes(restored["entries"][1]["data"]), b"a")
        self.assertEqual(restored["end"], bj.mock_used(restored["entries"]))
        self.assertEqual(len(restored["entries"]), 2)

    def test_input_image_is_never_mutated(self):
        img = _image([(N_A, b"a"), (N_B, b"bb")])
        before = bj._snapshot(img)
        bj.judge(img, N_A)
        bj.judge(img, b"\x00\x00NOPE      ")
        self.assertTrue(bj._images_equal(img, before))

    def test_bytearray_inputs_are_accepted(self):
        img = bj.make_image(entries=[(bytearray(N_A), bytearray(b"a"))])
        status, _ = bj.mock_delete(img, bytearray(N_A))
        self.assertEqual(status, bj.OK)

    # negative: not found / unformatted keep the image byte-identical
    def test_missing_name_returns_not_found_identical(self):
        img = _image([(N_A, b"a")])
        status, out = bj.mock_delete(img, b"\x00\x00NOPE      ")
        self.assertEqual(status, bj.NOT_FOUND)
        self.assertTrue(bj._images_equal(out, img))

    def test_unformatted_image_returns_not_formatted_identical(self):
        img = _image([(N_A, b"a")])
        img["formatted"] = False
        img["end"] = bj.mock_used(img["entries"])
        outcome, out = bj.judge(img, N_A)
        self.assertEqual(outcome, bj.NOT_FORMATTED)
        self.assertTrue(bj._images_equal(out, img))

    def test_empty_formatted_image_is_not_found(self):
        outcome, out = bj.judge(_image([]), N_A)
        self.assertEqual(outcome, bj.NOT_FOUND)
        self.assertEqual(out["entries"], [])

    def test_double_delete_second_is_not_found_identical(self):
        img = _image([(N_A, b"a")])
        first, once = bj.judge(img, N_A)
        self.assertEqual(first, bj.OK)
        second, twice = bj.judge(once, N_A)
        self.assertEqual(second, bj.NOT_FOUND)
        self.assertTrue(bj._images_equal(twice, once))

    # negative: input layer
    def test_bad_name_lengths_are_refused_without_delete(self):
        img = _image([(N_A, b"a")])
        for bad in (b"", b"short", b"\x00\x00PAD      ", b"\x00\x00PAD        ", b"this name is far too long"):
            with self.subTest(bad=bad):
                outcome, out = bj.judge(img, bad)
                self.assertEqual(outcome, bj.REFUSED)
                self.assertTrue(bj._images_equal(out, img))

    def test_non_bytes_names_are_refused(self):
        img = _image([(N_A, b"a")])
        for bad in ("FILE_A", None, 1234, ["x"]):
            with self.subTest(bad=bad):
                self.assertEqual(bj.judge(img, bad)[0], bj.REFUSED)

    def test_mock_delete_refuses_bad_input_and_corrupt_images_directly(self):
        img = _image([(N_A, b"a")])
        with self.assertRaises(ValueError):
            bj.mock_delete(img, b"short")
        broken = bj._snapshot(img)
        broken["end"] = broken["end"] + 1
        with self.assertRaises(ValueError):
            bj.mock_delete(broken, N_A)

    # negative: audit layer
    def test_corrupt_images_are_corrupted_without_delete(self):
        base = _image([(N_A, b"a"), (N_B, b"bb")])
        cases = []
        over_end = bj._snapshot(base)
        over_end["end"] = over_end["end"] + 1
        cases.append(over_end)
        dup = {"entries": [{"name": N_A, "data": b"a"}, {"name": N_A, "data": b"b"}],
               "formatted": True, "capacity": 2048, "end": 18 + 1 + 18 + 1}
        cases.append(dup)
        bad_len = {"entries": [{"name": b"short", "data": b"a"}],
                   "formatted": True, "capacity": 2048, "end": 18 + 1}
        cases.append(bad_len)
        over_cap = {"entries": [{"name": N_A, "data": b"a"}],
                    "formatted": True, "capacity": 2048, "end": 2049}
        cases.append(over_cap)
        for corrupt in cases:
            with self.subTest(corrupt=corrupt["end"]):
                outcome, out = bj.judge(corrupt, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertTrue(bj._images_equal(out, corrupt))

    # boundaries
    def test_capacity_boundaries(self):
        self.assertEqual(bj.MOCK_ENTRY_OVERHEAD, 18)
        tiny = bj.make_image(entries=[(N_A, b"")], capacity=18)
        self.assertEqual(bj.mock_free(tiny), 0)
        status, out = bj.mock_delete(tiny, N_A)
        self.assertEqual((status, bj.mock_free(out)), (bj.OK, 18))
        with self.assertRaises(ValueError):
            bj.make_image(entries=[(N_A, b"a")], capacity=18)
        with self.assertRaises(ValueError):
            bj.make_image(entries=[(N_A, b"a"), (N_A, b"b")])

    def test_name_matching_is_exact_12_bytes(self):
        img = bj.make_image(entries=[(b"\x00\x00FOO       ", b"upper"),
                                     (b"\x00\x00foo       ", b"lower"),
                                     (b"\x01\x02FILE_A    ", b"nz")])
        self.assertEqual(bj.judge(img, b"\x00\x00FOO       ")[0], bj.OK)
        self.assertEqual(bj.judge(img, b"\x00\x00foo       ")[0], bj.OK)
        self.assertEqual(bj.judge(img, b"\x00\x00FOO       ".lower())[0], bj.OK)
        self.assertEqual(bj.judge(img, b"\x00\x00FILE_A    ")[0], bj.NOT_FOUND)
        self.assertEqual(bj.judge(img, b"\x01\x02FILE_A    ")[0], bj.OK)

    # no false positives
    def test_every_name_single_bit_flip_is_rejected(self):
        img = _image([(N_A, b"hello")])
        self.assertEqual(bj.mutation_self_check(img, N_A), 96)

    # no I/O: the judge module imports nothing and touches no file APIs
    def test_module_has_no_imports_and_no_file_io(self):
        src = (ROOT / "tools" / "bm_delete_judge.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        self.assertEqual(imports, ALLOWED_IMPORTS["bm_delete_judge"])
        for attr in FORBIDDEN_ATTRS:
            self.assertNotIn(attr, src)
        for mod in FORBIDDEN_MODULES:
            self.assertNotIn(f"import {mod}", src)


if __name__ == "__main__":
    unittest.main()
