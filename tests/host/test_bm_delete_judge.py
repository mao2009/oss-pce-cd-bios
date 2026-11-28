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


if __name__ == "__main__":
    unittest.main()
