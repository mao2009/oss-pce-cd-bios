"""Issue #38: read-only BM_DELETE fixture and verdict checks (tools/bm_delete_fixture.py).

Synthetic images only. Expected values are hand-written 12-byte names in this file and in
tools/bm_delete_fixture.py; the fixture never derives an expectation from the judge. Read-only:
writes raise, the judge never mutates a caller's image, and no file or OS write is attempted.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import bm_delete_fixture as bf  # noqa: E402
import bm_delete_judge as bj  # noqa: E402

FORBIDDEN_MODULES = {'os', 'io', 'pathlib', 'shutil', 'tempfile', 'subprocess', 'socket', 'importlib', 'builtins'}
# Exact top-level import allowlist per host module. The fixture imports only the judge.
ALLOWED_IMPORTS = {'bm_delete_fixture': {'bm_delete_judge'}, 'bm_delete_judge': set()}


class BmDeleteFixtureTest(unittest.TestCase):
    def test_scenarios_match_expected_outcome(self):
        for name, (fixture, target, expected) in bf.scenarios().items():
            with self.subTest(name=name):
                self.assertEqual(bj.judge(fixture.to_image(), target)[0], expected)

    # positive
    def test_head_middle_tail_deletes_keep_survivors_in_order(self):
        two = None
        for name, (fixture, target, expected) in bf.scenarios().items():
            if name == "positive: delete middle entry":
                two = (fixture, target)
        self.assertIsNotNone(two)
        fixture, target = two
        status, out = bj.mock_delete(fixture.to_image(), target)
        self.assertEqual(status, bj.OK)
        self.assertEqual([bytes(e["name"]) for e in out["entries"]],
                         [b"\x00\x00FILE_A    ", b"\x00\x00FILE_C    "])
        self.assertEqual([bytes(e["data"]) for e in out["entries"]], [b"a", b"ccc"])

    def test_full_image_tail_delete_frees_space(self):
        for name, (fixture, target, expected) in bf.scenarios().items():
            if name == "boundary: delete tail of full image":
                status, out = bj.mock_delete(fixture.to_image(), target)
                self.assertEqual(status, bj.OK)
                self.assertGreater(bj.mock_free(out), 0)

    # negative: failures are byte-identical
    def test_failures_keep_image_identical(self):
        for name, (fixture, target, expected) in bf.scenarios().items():
            if expected in (bj.NOT_FOUND, bj.NOT_FORMATTED, bj.REFUSED, bj.CORRUPTED):
                with self.subTest(name=name):
                    before = fixture.to_image()
                    outcome, out = bj.judge(before, target)
                    self.assertEqual(outcome, expected)
                    self.assertTrue(bj._images_equal(out, before))

    def test_second_delete_of_same_target_is_not_found(self):
        one = None
        for name, (fixture, target, expected) in bf.scenarios().items():
            if name == "positive: delete sole entry":
                one = (fixture, target)
        self.assertIsNotNone(one)
        fixture, target = one
        first, once = bj.judge(fixture.to_image(), target)
        self.assertEqual(first, bj.OK)
        self.assertEqual(bj.judge(once, target)[0], bj.NOT_FOUND)

    # boundary spot checks with literal names (hand-computed here, not derived from the fixture)
    def test_literal_12_byte_names_delete_and_11_13_are_refused(self):
        padded = {"entries": [{"name": b"\x00\x00PAD       ", "data": b"d"}],
                  "formatted": True, "capacity": 2048, "end": 19}
        self.assertEqual(bj.judge(padded, b"\x00\x00PAD       ")[0], bj.OK)
        self.assertEqual(bj.judge(padded, b"\x00\x00PAD      ")[0], bj.REFUSED)
        self.assertEqual(bj.judge(padded, b"\x00\x00PAD        ")[0], bj.REFUSED)

    # read-only + no I/O
    def test_fixture_images_are_read_only(self):
        fixture = next(iter(bf.scenarios().values()))[0]
        with self.assertRaises(PermissionError):
            fixture["x"] = 1
        with self.assertRaises(PermissionError):
            del fixture["x"]

    def test_modules_have_no_io_imports(self):
        for module, allowed in ALLOWED_IMPORTS.items():
            src = (ROOT / "tools" / f"{module}.py").read_text(encoding="utf-8")
            tree = ast.parse(src)
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module.split(".")[0])
            self.assertEqual(imports, allowed, module)
            for mod in FORBIDDEN_MODULES:
                self.assertNotIn(f"import {mod}", src)


if __name__ == "__main__":
    unittest.main()
