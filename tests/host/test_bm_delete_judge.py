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


class BmDeleteStructureGateTest(unittest.TestCase):
    """Issue #38 Wave2: structure-gate regression pins (Wave1 RED rows T1-T4, T6, T8-T11, T18).

    Un-snapshotable images return (CORRUPTED, empty-image convention); snapshot-able abnormal
    images return (CORRUPTED, byte-identical snapshot). CORRUPTED wins over REFUSED on
    doubly-bad calls. Gate checks are explicit (no try/except) and never call mock_delete.
    Synthetic images only; no I/O.
    """

    EMPTY = {"entries": [], "formatted": False, "capacity": 0, "end": 0}

    def test_T1_none_image_is_corrupted_empty(self):
        outcome, out = bj.judge(None, N_A)
        self.assertEqual(outcome, bj.CORRUPTED)
        self.assertEqual(out, self.EMPTY)

    def test_T2_non_dict_images_are_corrupted_empty(self):
        for bad in ("notadict", 1234, ["entries"], ("entries",), b"bytes"):
            with self.subTest(bad=type(bad).__name__):
                outcome, out = bj.judge(bad, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)

    def test_T3_missing_keys_are_corrupted_empty(self):
        base = bj._snapshot(_image([(N_A, b"a")]))
        for key in ("entries", "formatted", "capacity", "end"):
            with self.subTest(missing=key):
                broken = {k: v for k, v in base.items() if k != key}
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)

    def test_T4_entries_not_list_is_corrupted_empty(self):
        base = bj._snapshot(_image([(N_A, b"a")]))
        for bad in (None, "entries", {"name": N_A}, 18, ({"name": N_A, "data": b"a"},)):
            with self.subTest(bad=type(bad).__name__):
                broken = dict(base, entries=bad)
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)

    def test_T6_non_dict_entry_is_corrupted_empty(self):
        base = bj._snapshot(_image([(N_A, b"a")]))
        for bad in ("entry", None, 18, [N_A], (N_A, b"a")):
            with self.subTest(bad=repr(bad)[:20]):
                broken = dict(base, entries=[bad])
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)

    def test_T8_entry_missing_name_is_corrupted_empty(self):
        broken = {"entries": [{"data": b"a"}], "formatted": True, "capacity": 2048, "end": 19}
        outcome, out = bj.judge(broken, N_A)
        self.assertEqual(outcome, bj.CORRUPTED)
        self.assertEqual(out, self.EMPTY)

    def test_T9_entry_missing_data_is_corrupted_empty(self):
        broken = {"entries": [{"name": N_A}], "formatted": True, "capacity": 2048, "end": 19}
        outcome, out = bj.judge(broken, N_A)
        self.assertEqual(outcome, bj.CORRUPTED)
        self.assertEqual(out, self.EMPTY)

    def test_T10_non_bytes_name_is_corrupted_empty(self):
        for bad in ("short", None, 1234, ["x"], {"n": 1}):
            with self.subTest(bad=type(bad).__name__):
                broken = {"entries": [{"name": bad, "data": b"a"}],
                          "formatted": True, "capacity": 2048, "end": 19}
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)

    def test_T11_non_bytes_data_is_corrupted_empty(self):
        for bad in ("a", None, 1234, ["x"]):
            with self.subTest(bad=type(bad).__name__):
                broken = {"entries": [{"name": N_A, "data": bad}],
                          "formatted": True, "capacity": 2048, "end": 19}
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)

    def test_mutable_scalars_are_corrupted_empty(self):
        """Wave1 allowlist: formatted/capacity/end holding list/dict/bytearray are un-snapshotable."""
        base = bj._snapshot(_image([(N_A, b"a")]))
        cases = [
            ("formatted", [True]),
            ("formatted", {"formatted": True}),
            ("formatted", bytearray(b"\x01")),
            ("capacity", [2048]),
            ("capacity", {"capacity": 2048}),
            ("capacity", bytearray(b"\x00\x08")),
            ("end", [19]),
            ("end", {"end": 19}),
            ("end", bytearray(b"\x13")),
        ]
        self.assertEqual(len(cases), 9)
        for key, bad_scalar in cases:
            with self.subTest(scalar=key, kind=type(bad_scalar).__name__):
                broken = dict(base, **{key: bad_scalar})
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)

    def test_mutable_scalar_outputs_are_frozen_and_fresh(self):
        import copy
        base = bj._snapshot(_image([(N_A, b"a")]))
        mutables = [
            dict(base, formatted=[True]),
            dict(base, capacity={"capacity": 2048}),
            dict(base, end=bytearray(b"\x13")),
        ]
        for broken in mutables:
            with self.subTest(broken=repr(broken)[:60]):
                frozen = copy.deepcopy(broken)
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)
                # Output mutation must not affect the input (deepcopy-frozen).
                out["entries"].append({"name": N_A, "data": b"x"})
                out["capacity"] = 9999
                self.assertEqual(broken, frozen)
                # Repeated returns are equal but never shared.
                _, first = bj.judge(broken, N_A)
                _, second = bj.judge(broken, N_A)
                self.assertEqual(first, second)
                self.assertIsNot(first, second)

    def test_mutable_scalar_precedence_over_refused(self):
        base = bj._snapshot(_image([(N_A, b"a")]))
        broken_scalars = [
            dict(base, formatted=[True]),
            dict(base, capacity={"capacity": 2048}),
            dict(base, end=bytearray(b"\x13")),
        ]
        for broken in broken_scalars:
            for bad_name in (b"", b"short", "FILE_A", None, 1234):
                with self.subTest(bad_name=repr(bad_name)[:20]):
                    outcome, out = bj.judge(broken, bad_name)
                    self.assertEqual(outcome, bj.CORRUPTED)
                    self.assertEqual(out, self.EMPTY)

    def test_mutable_scalars_never_call_mock_delete(self):
        import copy
        from unittest import mock as utm
        base = bj._snapshot(_image([(N_A, b"a")]))
        abnormal = [
            dict(base, formatted=[True]),
            dict(base, formatted={"formatted": True}),
            dict(base, formatted=bytearray(b"\x01")),
            dict(base, capacity=[2048]),
            dict(base, capacity={"capacity": 2048}),
            dict(base, capacity=bytearray(b"\x00\x08")),
            dict(base, end=[19]),
            dict(base, end={"end": 19}),
            dict(base, end=bytearray(b"\x13")),
        ]
        with utm.patch.object(bj, "mock_delete", side_effect=AssertionError("must not call")) as spy:
            for broken in abnormal:
                frozen = copy.deepcopy(broken)
                for name in (N_A, b"short", None):
                    outcome, out = bj.judge(broken, name)
                    self.assertEqual(outcome, bj.CORRUPTED)
                    self.assertEqual(out, self.EMPTY)
                self.assertEqual(broken, frozen)
            self.assertEqual(spy.call_count, 0)

    def test_subclass_scalars_are_corrupted_empty(self):
        """Wave2 strict gate: int/str/bytes subclasses w/ mutable attrs on all 3 fields fail closed."""
        class MyInt(int):
            pass
        class MyStr(str):
            pass
        class MyBytes(bytes):
            pass
        base = bj._snapshot(_image([(N_A, b"a")]))

        def with_attr(cls, v):
            obj = cls(v)
            obj.extra = [1, 2, 3]
            return obj
        cases = [
            ("formatted", with_attr(MyInt, 1)),
            ("formatted", with_attr(MyStr, "yes")),
            ("formatted", with_attr(MyBytes, b"yes")),
            ("capacity", with_attr(MyInt, 2048)),
            ("capacity", with_attr(MyStr, "2048")),
            ("capacity", with_attr(MyBytes, b"2048")),
            ("end", with_attr(MyInt, 19)),
            ("end", with_attr(MyStr, "19")),
            ("end", with_attr(MyBytes, b"19")),
        ]
        self.assertEqual(len(cases), 9)
        for key, bad in cases:
            with self.subTest(scalar=key, kind=type(bad).__name__):
                broken = dict(base, **{key: bad})
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)

    def test_subclass_containers_are_corrupted_empty(self):
        """Wave2 strict gate: dict/list/entry/bytes-subclass containers fail closed (no aliasing)."""
        class MyDict(dict):
            pass
        class MyList(list):
            pass
        class MyBytes(bytes):
            pass
        class MyBA(bytearray):
            pass
        base = bj._snapshot(_image([(N_A, b"a")]))
        dimg = MyDict(base)
        lentries = dict(base, entries=MyList(base["entries"]))
        dentry = dict(base, entries=[MyDict(base["entries"][0])])
        bname = dict(base, entries=[{"name": MyBytes(N_A), "data": b"a"}])
        bdata = dict(base, entries=[{"name": N_A, "data": MyBytes(b"a")}])
        baname = dict(base, entries=[{"name": MyBA(N_A), "data": b"a"}])
        cases = [dimg, lentries, dentry, bname, bdata, baname]
        self.assertEqual(len(cases), 6)
        for broken in cases:
            with self.subTest(broken=repr(broken)[:60]):
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)

    def test_exact_builtin_gate_acceptance(self):
        """Exact bool/int/str/bytes pass the gate; subclasses and disallowed builtins do not."""
        class MyInt(int):
            pass
        base = bj._snapshot(_image([(N_A, b"a")]))
        self.assertTrue(bj._is_snapshotable(base))
        for broken in (dict(base, formatted=1), dict(base, formatted="yes"),
                        dict(base, capacity="2048"), dict(base, end="19")):
            with self.subTest(broken=(broken["formatted"], broken["capacity"], broken["end"])):
                self.assertTrue(bj._is_snapshotable(broken))
        for bad in ([True], {"a": 1}, bytearray(b"\x01"), {1, 2}, (1,), 1.5, None):
            with self.subTest(bad=type(bad).__name__):
                self.assertFalse(bj._is_snapshotable(dict(base, formatted=bad)))
                self.assertFalse(bj._is_snapshotable(dict(base, capacity=bad)))
                self.assertFalse(bj._is_snapshotable(dict(base, end=bad)))
        self.assertFalse(bj._is_snapshotable(dict(base, formatted=MyInt(1))))
        self.assertFalse(bj._is_snapshotable(dict(base, capacity=MyInt(2048))))
        self.assertFalse(bj._is_snapshotable(dict(base, end=MyInt(19))))

    def test_evil_special_methods_never_raise(self):
        """Subclass special-method hole: EvilInt/EvilStr as any scalar -> CORRUPTED+EMPTY, no raise."""
        class EvilInt(int):
            def __eq__(self, other):
                raise RuntimeError("EvilInt __eq__")
            def __ne__(self, other):
                raise RuntimeError("EvilInt __ne__")
            def __lt__(self, other):
                raise RuntimeError("EvilInt __lt__")
            def __le__(self, other):
                raise RuntimeError("EvilInt __le__")
            def __gt__(self, other):
                raise RuntimeError("EvilInt __gt__")
            def __ge__(self, other):
                raise RuntimeError("EvilInt __ge__")
            __hash__ = int.__hash__
        class EvilStr(str):
            def __eq__(self, other):
                raise RuntimeError("EvilStr __eq__")
            def __ne__(self, other):
                raise RuntimeError("EvilStr __ne__")
            def __lt__(self, other):
                raise RuntimeError("EvilStr __lt__")
            def __le__(self, other):
                raise RuntimeError("EvilStr __le__")
            def __gt__(self, other):
                raise RuntimeError("EvilStr __gt__")
            def __ge__(self, other):
                raise RuntimeError("EvilStr __ge__")
            def __len__(self):
                raise RuntimeError("EvilStr __len__")
            __hash__ = str.__hash__
        base = bj._snapshot(_image([(N_A, b"a")]))
        evils = [EvilInt(1), EvilInt(2048), EvilInt(19),
                 EvilStr("yes"), EvilStr("2048"), EvilStr("19")]
        fields = ["formatted", "capacity", "end"]
        self.assertEqual(len(evils), 6)
        for field in fields:
            for idx, evil in enumerate(evils):
                with self.subTest(field=field, evil="%s#%d" % (type(evil).__name__, idx)):
                    broken = dict(base, **{field: evil})
                    for name in (N_A, b"short", None):
                        outcome, out = bj.judge(broken, name)  # must not raise
                        self.assertEqual(outcome, bj.CORRUPTED)
                        self.assertEqual(out, self.EMPTY)
                    self.assertIs(broken[field], evil)

    def test_subclass_outputs_frozen_fresh_cross_return(self):
        import copy
        class MyInt(int):
            pass
        class MyStr(str):
            pass
        class MyBytes(bytes):
            pass
        base = bj._snapshot(_image([(N_A, b"a")]))
        brokens = [
            dict(base, formatted=MyInt(1)),
            dict(base, capacity=MyStr("2048")),
            dict(base, end=MyBytes(b"19")),
        ]
        for broken in brokens:
            with self.subTest(broken=repr(broken)[:60]):
                frozen = copy.deepcopy(broken)
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, self.EMPTY)
                out["entries"].append({"name": N_A, "data": b"x"})
                out["capacity"] = 9999
                self.assertEqual(broken, frozen)
                _, first = bj.judge(broken, N_A)
                _, second = bj.judge(broken, N_A)
                self.assertEqual(first, second)
                self.assertIsNot(first, second)

    def test_subclass_precedence_over_refused_and_spy(self):
        import copy
        from unittest import mock as utm
        class MyInt(int):
            pass
        class EvilInt(int):
            def __eq__(self, other):
                raise RuntimeError("EvilInt __eq__")
            def __ne__(self, other):
                raise RuntimeError("EvilInt __ne__")
            def __lt__(self, other):
                raise RuntimeError("EvilInt __lt__")
            def __le__(self, other):
                raise RuntimeError("EvilInt __le__")
            def __gt__(self, other):
                raise RuntimeError("EvilInt __gt__")
            def __ge__(self, other):
                raise RuntimeError("EvilInt __ge__")
            __hash__ = int.__hash__
        base = bj._snapshot(_image([(N_A, b"a")]))
        evil = EvilInt(2048)
        brokens = [
            dict(base, formatted=MyInt(1)),
            dict(base, capacity=MyInt(2048)),
            dict(base, end=MyInt(19)),
            dict(base, capacity=evil),
        ]
        with utm.patch.object(bj, "mock_delete", side_effect=AssertionError("must not call")) as spy:
            for broken in brokens:
                if any(type(v).__name__ == "EvilInt" for v in broken.values()):
                    marker = broken["capacity"]
                else:
                    marker = None
                frozen = None
                if marker is None:
                    frozen = copy.deepcopy(broken)
                for bad_name in (b"", b"short", "FILE_A", None, 1234):
                    outcome, out = bj.judge(broken, bad_name)  # must not raise
                    self.assertEqual(outcome, bj.CORRUPTED)
                    self.assertEqual(out, self.EMPTY)
                if marker is not None:
                    self.assertIs(broken["capacity"], evil)
                else:
                    self.assertEqual(broken, frozen)
            self.assertEqual(spy.call_count, 0)

    def test_T18_scalar_insanity_is_corrupted_snapshot(self):
        base = bj._snapshot(_image([(N_A, b"a")]))
        cases = []
        cases.append(dict(base, formatted=1))
        cases.append(dict(base, formatted="yes"))
        cases.append(dict(base, capacity="2048"))
        cases.append(dict(base, capacity=0))
        cases.append(dict(base, capacity=-8))
        cases.append(dict(base, end="19"))
        cases.append(dict(base, end=-1))
        cases.append(dict(base, end=base["capacity"] + 1))
        for broken in cases:
            with self.subTest(broken=(broken["formatted"], broken["capacity"], broken["end"])):
                outcome, out = bj.judge(broken, N_A)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertTrue(bj._images_equal(out, broken))

    def test_doubly_bad_corrupted_wins_over_refused(self):
        # Un-snapshotable image + bad name: CORRUPTED (empty convention), never REFUSED, never raises.
        for bad_name in (b"", b"short", "FILE_A", None, 1234):
            with self.subTest(bad_name=repr(bad_name)[:20]):
                self.assertEqual(bj.judge(None, bad_name), (bj.CORRUPTED, self.EMPTY))
        # Snapshot-able corrupt image + bad name: CORRUPTED with byte-identical snapshot.
        base = bj._snapshot(_image([(N_A, b"a")]))
        broken = dict(base, end=base["end"] + 1)
        for bad_name in (b"", b"short", None):
            with self.subTest(bad_name=repr(bad_name)[:20]):
                outcome, out = bj.judge(broken, bad_name)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertTrue(bj._images_equal(out, broken))

    def test_gate_results_are_fresh_objects_never_aliased(self):
        _, first = bj.judge(None, N_A)
        _, second = bj.judge(None, N_A)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        base = bj._snapshot(_image([(N_A, b"a")]))
        broken = dict(base, end=base["end"] + 1)
        _, snap = bj.judge(broken, N_A)
        self.assertIsNot(snap, broken)
        self.assertIsNot(snap["entries"], broken["entries"])

    def test_corrupt_inputs_are_not_mutated(self):
        import copy
        corrupt = [
            None,
            "notadict",
            {"entries": "nope", "formatted": True, "capacity": 2048, "end": 0},
            {"entries": [[N_A]], "formatted": True, "capacity": 2048, "end": 0},
            {"entries": [{"name": "str-name", "data": b"a"}],
             "formatted": True, "capacity": 2048, "end": 19},
            dict(bj._snapshot(_image([(N_A, b"a")])), end=999),
            dict(bj._snapshot(_image([(N_A, b"a")])), formatted=1),
        ]
        for bad_image in corrupt:
            with self.subTest(bad_image=repr(bad_image)[:40]):
                frozen = copy.deepcopy(bad_image)
                bj.judge(bad_image, N_A)
                bj.judge(bad_image, b"short")
                self.assertEqual(bad_image, frozen)

    def test_mock_delete_never_called_on_abnormal(self):
        import copy
        from unittest import mock as utm
        base = bj._snapshot(_image([(N_A, b"a")]))
        abnormal = [
            (None, N_A),
            ("notadict", N_A),
            (dict(base, entries="nope"), N_A),
            ({"entries": [{"name": "s", "data": b"a"}],
              "formatted": True, "capacity": 2048, "end": 19}, N_A),
            (dict(base, end=base["end"] + 1), N_A),  # audit-level corrupt
            (dict(base, formatted=1), N_A),  # scalar-level corrupt
            (base, b"short"),  # REFUSED path
            (base, None),  # REFUSED path
            (dict(base, formatted=False), N_A),  # NOT_FORMATTED path
            (base, b"\x00\x00NOPE      "),  # NOT_FOUND path
        ]
        with utm.patch.object(bj, "mock_delete", side_effect=AssertionError("must not call")) as spy:
            for bad_image, name in abnormal:
                frozen = copy.deepcopy(bad_image)
                bj.judge(bad_image, name)
                self.assertEqual(bad_image, frozen)
            self.assertEqual(spy.call_count, 0)


class _BadName(bytes):
    """bytes subclass with valid 12-byte payload; exact-type gate must refuse."""
    pass


class _BadBA(bytearray):
    """bytearray subclass with valid 12-byte payload; exact-type gate must refuse."""
    pass


class _RedirectName:
    """Non-bytes redirect: bytes() would copy a valid name, len() claims 12; gate must refuse before any copy."""
    def __bytes__(self):
        return N_A
    def __len__(self):
        return 12


def _fresh_exact_gate_bad_names():
    """Fresh BadName/RedirectName/BadBA/RedirectBA table; new objects per call (no aliasing)."""
    return [
        ("BadName", _BadName(N_A)),
        ("RedirectName", _RedirectName()),
        ("BadBA", _BadBA(N_A)),
        ("RedirectBA", memoryview(N_A)),
    ]


class BmDeleteExactNameGateTest(unittest.TestCase):
    """Issue #38 Wave2: exact-type name gate (type(name) in (bytes, bytearray) before len).

    Table BadName/RedirectName/BadBA/RedirectBA x judge REFUSED-identical +
    validate False + mock_delete ValueError + spy non-invocation + input-unmutated +
    cross-return fresh + exact-match control + order preservation. CORRUPTED-wins holds
    for doubly-bad (bad name + bad image). No generic bytes(name) copying: redirect
    names whose bytes()/buffer would equal a stored entry are still REFUSED. Synthetic only.
    """

    def test_exact_name_gate_validate_is_false(self):
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(name=label):
                self.assertFalse(bj.validate_input(bad))

    def test_exact_name_gate_judge_refused_identical(self):
        img = _image([(N_A, b"a"), (N_B, b"bb")])
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(name=label):
                outcome, out = bj.judge(img, bad)
                self.assertEqual(outcome, bj.REFUSED)
                self.assertTrue(bj._images_equal(out, img))

    def test_exact_name_gate_mock_delete_raises(self):
        img = _image([(N_A, b"a")])
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(name=label):
                with self.assertRaises(ValueError):
                    bj.mock_delete(img, bad)

    def test_exact_name_gate_make_image_rejects(self):
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(name=label):
                with self.assertRaises(ValueError):
                    bj.make_image(entries=[(bad, b"a")])

    def test_exact_name_gate_never_calls_mock_delete(self):
        import copy
        from unittest import mock as utm
        img = _image([(N_A, b"a")])
        with utm.patch.object(bj, "mock_delete", side_effect=AssertionError("must not call")) as spy:
            for label, bad in _fresh_exact_gate_bad_names():
                with self.subTest(name=label):
                    frozen = copy.deepcopy(img)
                    outcome, out = bj.judge(img, bad)
                    self.assertEqual(outcome, bj.REFUSED)
                    self.assertTrue(bj._images_equal(out, img))
                    self.assertEqual(img, frozen)
            self.assertEqual(spy.call_count, 0)

    def test_exact_name_gate_inputs_unmutated(self):
        import copy
        img = _image([(N_A, b"a"), (N_B, b"bb")])
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(name=label):
                frozen = copy.deepcopy(img)
                bj.judge(img, bad)
                bj.validate_input(bad)
                with self.assertRaises(ValueError):
                    bj.mock_delete(img, bad)
                self.assertEqual(img, frozen)
                # Redirect payload itself is not normalized by the judge.
                if isinstance(bad, (bytes, bytearray, memoryview)):
                    self.assertEqual(bytes(bad), N_A)
                else:
                    self.assertEqual(bytes(bad), N_A)

    def test_exact_name_gate_cross_return_fresh(self):
        img = _image([(N_A, b"a")])
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(name=label):
                _, first = bj.judge(img, bad)
                _, second = bj.judge(img, bad)
                self.assertEqual(first, second)
                self.assertIsNot(first, second)
                self.assertIsNot(first["entries"], second["entries"])
                self.assertTrue(bj._images_equal(first, img))
                self.assertTrue(bj._images_equal(second, img))

    def test_exact_name_gate_corrupted_wins_over_refused(self):
        base = bj._snapshot(_image([(N_A, b"a")]))
        broken = dict(base, end=base["end"] + 1)
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(name=label):
                outcome, out = bj.judge(broken, bad)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertTrue(bj._images_equal(out, broken))
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(name="un-snapshotable+" + label):
                outcome, out = bj.judge(None, bad)
                self.assertEqual(outcome, bj.CORRUPTED)
                self.assertEqual(out, {"entries": [], "formatted": False, "capacity": 0, "end": 0})

    def test_exact_name_gate_no_generic_copying(self):
        # bytes(RedirectName) and bytes(RedirectBA) both equal the stored entry,
        # yet the judge must REFUSE instead of converting them into a lookup key.
        img = _image([(N_A, b"a")])
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(name=label):
                self.assertEqual(bytes(bad), N_A)
                outcome, _ = bj.judge(img, bad)
                self.assertEqual(outcome, bj.REFUSED)

    def test_exact_name_gate_exact_match_control_and_order(self):
        # Exact bytes and exact bytearray still hit; subclasses/redirects never do.
        img = _image([(N_A, b"a"), (N_B, b"bb"), (N_C, b"ccc")])
        self.assertTrue(bj.validate_input(bytearray(N_A)))
        self.assertEqual(bj.judge(img, bytearray(N_A))[0], bj.OK)
        for label, bad in _fresh_exact_gate_bad_names():
            with self.subTest(control=label):
                self.assertNotEqual(bj.judge(img, bad)[0], bj.OK)
        # Order preservation with exact names: head/middle/tail deletes keep survivors in order.
        _, head = bj.judge(img, N_A)
        self.assertEqual([bytes(e["name"]) for e in head["entries"]], [N_B, N_C])
        _, mid = bj.judge(img, N_B)
        self.assertEqual([bytes(e["name"]) for e in mid["entries"]], [N_A, N_C])
        _, tail = bj.judge(img, N_C)
        self.assertEqual([bytes(e["name"]) for e in tail["entries"]], [N_A, N_B])
        self.assertEqual([bytes(e["data"]) for e in mid["entries"]], [b"a", b"ccc"])


class _EvilStrKey(str):
    """Stored-key evil: same value/hash as the required key; any membership compare raises."""
    def __eq__(self, other):
        raise RuntimeError("_EvilStrKey __eq__ must never run")
    def __ne__(self, other):
        raise RuntimeError("_EvilStrKey __ne__ must never run")
    __hash__ = str.__hash__


class _SpyStrKey(str):
    """Stored-key spy: counts hash/eq/bytes invocations; the gate must call none of them."""
    def __new__(cls, value):
        obj = str.__new__(cls, value)
        obj.hash_calls = 0
        obj.eq_calls = 0
        obj.bytes_calls = 0
        return obj
    def __hash__(self):
        self.hash_calls += 1
        return str.__hash__(self)
    def __eq__(self, other):
        self.eq_calls += 1
        return str.__eq__(self, other)
    def __ne__(self, other):
        self.eq_calls += 1
        return str.__ne__(self, other)
    def __bytes__(self):
        self.bytes_calls += 1
        return str.__bytes__(self) if hasattr(str, "__bytes__") else bytes(str(self), "utf-8")


class _SameHashKey:
    """Non-str stored key colliding with hash(target); any membership compare raises."""
    def __init__(self, target):
        self._target = target
        self.hash_calls = 0
        self.eq_calls = 0
    def __hash__(self):
        self.hash_calls += 1
        return hash(self._target)
    def __eq__(self, other):
        self.eq_calls += 1
        raise RuntimeError("_SameHashKey __eq__ must never run")
    def __ne__(self, other):
        self.eq_calls += 1
        raise RuntimeError("_SameHashKey __ne__ must never run")


def _swap_top_key(base, target, new_key):
    """Return a fresh image dict with the `target` top-level key replaced by `new_key`."""
    out = {}
    for k, v in base.items():
        out[new_key if k == target else k] = v
    return out


def _swap_entry_key(entry, target, new_key):
    """Return a fresh entry dict with the `target` entry key replaced by `new_key`."""
    out = {}
    for k, v in entry.items():
        out[new_key if k == target else k] = v
    return out


class BmDeleteBadKeyGateTest(unittest.TestCase):
    """Issue #38 Wave2 blocker: stored-key iteration before membership (top + entry level).

    type(key) is str runs before any membership/subscript on that level, so evil or
    colliding stored keys never reach __hash__/__eq__ and the gate never raises:
    CORRUPTED + fresh EMPTY wins (even over REFUSED on doubly-bad calls), mock_delete
    is never called, inputs are never mutated. Exact-str keys pass normally and extra
    exact-str keys are kept (accepted). Synthetic images only; no I/O.
    """

    EMPTY = {"entries": [], "formatted": False, "capacity": 0, "end": 0}
    TOP_KEYS = ("entries", "formatted", "capacity", "end")
    ENTRY_KEYS = ("name", "data")

    def _base(self):
        return bj._snapshot(_image([(N_A, b"a")]))

    def _assert_fresh_empty(self, out):
        self.assertEqual(out, self.EMPTY)
        self.assertIsNot(out, self.EMPTY)

    def _assert_identity_keys(self, broken, keys_before, vals_before):
        after_keys = list(broken.keys())
        after_vals = list(broken.values())
        self.assertEqual(len(after_keys), len(keys_before))
        for a, b in zip(after_keys, keys_before):
            self.assertIs(a, b)
        self.assertEqual(len(after_vals), len(vals_before))
        for a, b in zip(after_vals, vals_before):
            self.assertIs(a, b)

    def test_top_level_bad_keys_corrupted_empty(self):
        # 4-row table: each required top-level key replaced by an evil same-value str key.
        for target in self.TOP_KEYS:
            with self.subTest(key=target):
                base = self._base()
                broken = _swap_top_key(base, target, _EvilStrKey(target))
                keys_before = list(broken.keys())
                vals_before = list(broken.values())
                outcome, out = bj.judge(broken, N_A)  # must not raise
                self.assertEqual(outcome, bj.CORRUPTED)
                self._assert_fresh_empty(out)
                self.assertFalse(bj._is_snapshotable(broken))
                self._assert_identity_keys(broken, keys_before, vals_before)

    def test_entry_internal_bad_keys_corrupted_empty(self):
        # 2-row table: each required entry key replaced by an evil same-value str key.
        for target in self.ENTRY_KEYS:
            with self.subTest(key=target):
                base = self._base()
                evil_entry = _swap_entry_key(base["entries"][0], target, _EvilStrKey(target))
                broken = dict(base, entries=[evil_entry])
                entry_keys_before = list(broken["entries"][0].keys())
                outcome, out = bj.judge(broken, N_A)  # must not raise
                self.assertEqual(outcome, bj.CORRUPTED)
                self._assert_fresh_empty(out)
                self.assertFalse(bj._is_snapshotable(broken))
                after_keys = list(broken["entries"][0].keys())
                self.assertEqual(len(after_keys), len(entry_keys_before))
                for a, b in zip(after_keys, entry_keys_before):
                    self.assertIs(a, b)

    def test_colliding_same_hash_key_corrupted_empty(self):
        # Non-str key with hash("entries"): old membership compare would raise; gate rejects first.
        base = self._base()
        colliding = _SameHashKey("entries")
        broken = _swap_top_key(base, "entries", colliding)
        keys_before = list(broken.keys())
        outcome, out = bj.judge(broken, N_A)  # must not raise
        self.assertEqual(outcome, bj.CORRUPTED)
        self._assert_fresh_empty(out)
        self.assertFalse(bj._is_snapshotable(broken))
        after_keys = list(broken.keys())
        self.assertEqual(len(after_keys), len(keys_before))
        for a, b in zip(after_keys, keys_before):
            self.assertIs(a, b)

    def test_non_invocation_spy_hash_eq_bytes_never_called(self):
        # Counting str-subclass keys: reset after construction, then gate must call nothing.
        from unittest import mock as utm
        with utm.patch.object(bj, "mock_delete", side_effect=AssertionError("must not call")) as spy:
            for target in self.TOP_KEYS + ("extra",):
                with self.subTest(key=target):
                    base = self._base()
                    if target == "extra":
                        probe = _SpyStrKey("note")
                        broken = dict(base)
                        broken[probe] = "x"
                    else:
                        probe = _SpyStrKey(target)
                        broken = _swap_top_key(base, target, probe)
                    probe.hash_calls = 0
                    probe.eq_calls = 0
                    probe.bytes_calls = 0
                    outcome, out = bj.judge(broken, N_A)  # must not raise
                    self.assertEqual(probe.hash_calls, 0)
                    self.assertEqual(probe.eq_calls, 0)
                    self.assertEqual(probe.bytes_calls, 0)
                    if target == "extra":
                        # Extra exact-value key is still a str subclass: rejected, never compared.
                        self.assertEqual(outcome, bj.CORRUPTED)
                        self._assert_fresh_empty(out)
                    else:
                        self.assertEqual(outcome, bj.CORRUPTED)
                        self._assert_fresh_empty(out)
            self.assertEqual(spy.call_count, 0)

    def test_doubly_bad_key_corrupted_wins_over_refused(self):
        base = self._base()
        broken_top = _swap_top_key(base, "entries", _EvilStrKey("entries"))
        evil_entry = _swap_entry_key(base["entries"][0], "name", _EvilStrKey("name"))
        broken_entry = dict(base, entries=[evil_entry])
        colliding = _swap_top_key(base, "capacity", _SameHashKey("capacity"))
        for label, broken in (("top", broken_top), ("entry", broken_entry), ("colliding", colliding)):
            for bad_name in (b"", b"short", "FILE_A", None, 1234):
                with self.subTest(image=label, bad_name=repr(bad_name)[:20]):
                    outcome, out = bj.judge(broken, bad_name)  # must not raise
                    self.assertEqual(outcome, bj.CORRUPTED)
                    self._assert_fresh_empty(out)

    def test_bad_key_never_calls_mock_delete(self):
        import copy
        from unittest import mock as utm
        base = self._base()
        clean_entry = copy.deepcopy(base["entries"][0])
        abnormals = [
            _swap_top_key(base, "entries", _EvilStrKey("entries")),
            _swap_top_key(base, "formatted", _EvilStrKey("formatted")),
            _swap_top_key(base, "capacity", _EvilStrKey("capacity")),
            _swap_top_key(base, "end", _EvilStrKey("end")),
            dict(base, entries=[_swap_entry_key(clean_entry, "name", _EvilStrKey("name"))]),
            dict(base, entries=[_swap_entry_key(clean_entry, "data", _EvilStrKey("data"))]),
            _swap_top_key(base, "entries", _SameHashKey("entries")),
        ]
        self.assertEqual(len(abnormals), 7)
        with utm.patch.object(bj, "mock_delete", side_effect=AssertionError("must not call")) as spy:
            for idx, broken in enumerate(abnormals):
                with self.subTest(case=idx):
                    outcome, out = bj.judge(broken, N_A)  # must not raise
                    self.assertEqual(outcome, bj.CORRUPTED)
                    self._assert_fresh_empty(out)
            self.assertEqual(spy.call_count, 0)

    def test_cross_return_fresh_and_no_aliasing(self):
        base = self._base()
        broken = _swap_top_key(base, "end", _EvilStrKey("end"))
        _, first = bj.judge(broken, N_A)
        _, second = bj.judge(broken, N_A)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertEqual(first, self.EMPTY)
        # Mutating one return must not affect the next.
        first["entries"].append({"name": N_A, "data": b"x"})
        first["capacity"] = 9999
        _, third = bj.judge(broken, N_A)
        self.assertEqual(third, self.EMPTY)

    def test_exact_str_acceptance_and_extra_key_kept(self):
        base = self._base()
        self.assertTrue(bj._is_snapshotable(base))
        self.assertEqual(bj.judge(base, N_A)[0], bj.OK)
        # Extra exact-str key is kept (accepted): gate passes, delete proceeds normally.
        extra = dict(base, note="x")
        self.assertTrue(bj._is_snapshotable(extra))
        outcome, out = bj.judge(extra, N_A)
        self.assertEqual(outcome, bj.OK)
        self.assertEqual(out["entries"], [])


if __name__ == "__main__":
    unittest.main()
