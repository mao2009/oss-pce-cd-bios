"""Negative tests for the CI sharding manifest + fail-closed aggregation (Issue #846).

Synthetic evidence fixtures only (no emulator, no builds); each anomaly must
RED the aggregator, and only the exact anomaly may RED. The manifest
self-consistency test pins the CURRENT tree: an unclassified module fails
until it is added to a shard in the same PR that adds the test.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools")]

import ci_sharding as shard


def _ok_record(i):
    return {"id": f"test_mod.Class.test_{i:02d}", "result": "ok", "reason": "",
            "duration_s": 0.1}


def _evidence(shard_id="rc-0", kind="integration-unittest", n=3, sha="abc123"):
    planned = [f"test_mod.Class.test_{i:02d}" for i in range(n)]
    return {
        "schema": 1, "manifest_version": 1, "sha": sha, "kind": kind,
        "shard_id": shard_id,
        "toolchain": {"cc65": "x", "geargrafx": "y"},
        "gate_states": {"dev_rom_present": True},
        "started": "2026-11-27T00:00:00Z",
        "planned_ids": list(planned),
        "executed": [_ok_record(i) for i in range(n)],
        "failures": 0, "errors": 0, "duration_s": 1.0,
    }


def _static_evidence(sha="abc123"):
    return {
        "schema": 1, "manifest_version": 1, "sha": sha, "kind": "static",
        "shard_id": "static",
        "toolchain": {"cc65": "x", "geargrafx": "y"},
        "gate_states": {},
        "started": "2026-11-27T00:00:00Z",
        "planned_units": list(shard.STATIC_UNITS),
        "executed": [{"unit": u, "exit_code": 0, "duration_s": 0.1, "note": ""}
                     for u in shard.STATIC_UNITS],
    }


def _direct_evidence(units=("direct:smoke",), sha="abc123"):
    return {
        "schema": 1, "manifest_version": 1, "sha": sha, "kind": "integration-direct",
        "shard_id": "direct",
        "toolchain": {"cc65": "x", "geargrafx": "y"},
        "gate_states": {},
        "started": "2026-11-27T00:00:00Z",
        "planned_units": list(shard.DIRECT_UNITS),
        "executed": [{"unit": u, "exit_code": 0, "duration_s": 0.1, "note": ""}
                     for u in units],
    }


def _full_green_set():
    out = [_evidence(sid, n=2) for sid in sorted(shard.INTEGRATION_SHARDS)]
    out[0]["planned_ids"] = out[0]["planned_ids"][:0]
    out[0]["executed"] = []
    return out


class AggregateNegativeTests(unittest.TestCase):
    def _green(self):
        evs = [_evidence("rc-0", n=2), _evidence("rc-1", n=1),
                 _evidence("rc-2", n=1), _evidence("rc-3", n=1)]
        host = _evidence("host", kind="host-unittest", n=2)
        direct = _direct_evidence(units=tuple(shard.DIRECT_UNITS))
        static = _static_evidence()
        return evs + [host, direct, static]

    def _host_live(self, evs):
        for e in evs:
            if e["shard_id"] == "host":
                return list(e["planned_ids"])
        return []

    def _problems(self, evs):
        problems = shard.aggregate(evs, live_integration_modules=[],
                                   live_host_ids=self._host_live(evs))
        return [p for p in problems if "unclassified" not in p
                and "unknown test module" not in p]

    def test_positive_control_green(self):
        self.assertEqual([], self._problems(self._green()))

    def test_01_missing_shard_red(self):
        evs = self._green()[1:]
        problems = self._problems(evs)
        self.assertTrue(any("missing shard" in p for p in problems), problems)

    def test_02_missing_test_id_red(self):
        evs = self._green()
        evs[0]["executed"] = evs[0]["executed"][:1]
        problems = self._problems(evs)
        self.assertTrue(any("missing from executed" in p for p in problems), problems)

    def test_03_duplicate_test_id_red(self):
        evs = self._green()
        evs[0]["executed"] = evs[0]["executed"] + [evs[0]["executed"][0]]
        problems = self._problems(evs)
        self.assertTrue(any("duplicate" in p for p in problems), problems)

    def test_04_unknown_test_id_red(self):
        evs = self._green()
        rec = _ok_record(0)
        rec["id"] = "test_mod.Class.test_unlisted"
        evs[0]["executed"].append(rec)
        problems = self._problems(evs)
        self.assertTrue(any("not in planned" in p for p in problems), problems)

    def test_05_empty_shard_red(self):
        evs = self._green()
        evs[1]["executed"] = []
        evs[1]["planned_ids"] = []
        problems = self._problems(evs)
        self.assertTrue(any("empty shard" in p or "missing shard" in p or
                            "planned ID missing" in p for p in problems), problems)

    def test_06_sha_mismatch_red(self):
        evs = self._green()
        evs[1]["sha"] = "different"
        problems = self._problems(evs)
        self.assertTrue(any("multiple SHAs" in p for p in problems), problems)

    def test_07_shard_failure_red(self):
        evs = self._green()
        evs[0]["failures"] = 1
        evs[0]["executed"][0]["result"] = "fail"
        problems = self._problems(evs)
        self.assertTrue(any("failures=1" in p or "result=fail" in p for p in problems),
                        problems)

    def test_08_shard_cancellation_red(self):
        # A cancelled shard provides NO evidence file at all.
        evs = [e for e in self._green() if e["shard_id"] != "rc-0"]
        problems = self._problems(evs)
        self.assertTrue(any("missing shard evidence: rc-0" in p for p in problems),
                        problems)

    def test_09_artifact_missing_red(self):
        problems = shard.aggregate([], live_integration_modules=[])
        self.assertTrue(any("no shard evidence" in p for p in problems), problems)

    def test_10_import_failure_red(self):
        evs = self._green()
        rec = _ok_record(0)
        rec["id"] = "unittest.loader._FailedTest.test_x"
        evs[0]["executed"].append(rec)
        problems = self._problems(evs)
        self.assertTrue(any("not in planned" in p or "unknown" in p.lower()
                            for p in problems), problems)

    def test_11_unlisted_skip_red(self):
        evs = self._green()
        evs[0]["executed"][0]["result"] = "skipped"
        evs[0]["executed"][0]["reason"] = "some new reason"
        problems = self._problems(evs)
        self.assertTrue(any("unlisted SKIP" in p for p in problems), problems)

    def test_12_manifest_tamper_red(self):
        evs = self._green()
        evs[0]["manifest_version"] = 999
        problems = self._problems(evs)
        self.assertTrue(any("version mismatch" in p for p in problems), problems)

    def test_13_missing_aggregate_dependency_red(self):
        # The aggregate gate itself must require every shard: dropping rc-1
        # from the evidence set is RED even though rc-0 alone is internally
        # consistent (mirrors a missing `needs:` edge).
        evs = [e for e in self._green() if e["shard_id"] != "rc-1"]
        problems = self._problems(evs)
        self.assertTrue(any("missing shard evidence: rc-1" in p for p in problems),
                        problems)

    def test_14_unclassified_newcomer_red(self):
        evs = self._green()
        problems = shard.aggregate(evs, live_integration_modules=["test_brand_new_mod"])
        self.assertTrue(any("unclassified test module" in p for p in problems), problems)

    def test_15_direct_unit_missing_red(self):
        evs = self._green()
        for e in evs:
            if e["shard_id"] == "direct":
                e["executed"] = e["executed"][:1]
        problems = self._problems(evs)
        self.assertTrue(any("planned unit missing" in p for p in problems), problems)

    def test_16_static_unit_missing_red(self):
        evs = self._green()
        for e in evs:
            if e["shard_id"] == "static":
                e["executed"] = e["executed"][:1]
        problems = self._problems(evs)
        self.assertTrue(any("planned unit missing" in p for p in problems), problems)

    def test_17_static_unknown_unit_red(self):
        evs = self._green()
        for e in evs:
            if e["shard_id"] == "static":
                e["executed"].append({"unit": "static:bogus", "exit_code": 0,
                                        "duration_s": 0.1, "note": ""})
        problems = self._problems(evs)
        self.assertTrue(any("unknown unit executed" in p for p in problems), problems)

    def test_18_duplicate_evidence_red(self):
        evs = self._green()
        evs = evs + [dict(evs[0])]
        problems = self._problems(evs)
        self.assertTrue(any("duplicate shard evidence" in p for p in problems), problems)

    def test_listed_skip_green_with_reason(self):  # noqa tolerance
        evs = self._green()
        rec = dict(evs[0]["executed"][0])
        rec["id"] = ("test_ex_dspoff_real_core.ExperimentalSlot33RealCpuTests."
                     "test_two_call_flow_reaches_slot_33_after_slot_02_scaffolding")
        rec["result"] = "skipped"
        rec["reason"] = "superseded by the union real-$02 SEQ; extra"
        evs[0]["planned_ids"] = [rec["id"]] + evs[0]["planned_ids"][1:]
        evs[0]["executed"] = [rec] + evs[0]["executed"][1:]
        problems = self._problems(evs)
        self.assertEqual([], [p for p in problems if "unclassified" not in p
                              and "unknown test module" not in p])


class ManifestSelfCheckTests(unittest.TestCase):
    def test_manifest_covers_live_tree_exactly(self):
        problems = shard.check_manifest()
        self.assertEqual([], problems)

    def test_no_empty_shards(self):
        for sid, mods in shard.INTEGRATION_SHARDS.items():
            self.assertTrue(mods, f"empty shard {sid}")

    def test_list_suites_runs(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(0, shard.main(["list-suites"]))
        doc = __import__("json").loads(buf.getvalue())
        self.assertEqual(sorted(shard.INTEGRATION_SHARDS), sorted(doc["integration_shards"]))


if __name__ == "__main__":
    unittest.main()


class RequireCc65ContractTests(unittest.TestCase):
    def test_runners_force_required_cc65(self):
        import os
        old = os.environ.get("OSS_PCE_REQUIRE_CC65")
        try:
            if "OSS_PCE_REQUIRE_CC65" in os.environ:
                del os.environ["OSS_PCE_REQUIRE_CC65"]
            shard._require_cc65()
            self.assertEqual(os.environ.get("OSS_PCE_REQUIRE_CC65"), "1")
        finally:
            if old is None:
                os.environ.pop("OSS_PCE_REQUIRE_CC65", None)
            else:
                os.environ["OSS_PCE_REQUIRE_CC65"] = old

class EvidenceWriterTests(unittest.TestCase):
    def test_write_evidence_creates_missing_dirs(self):
        import tempfile, os
        base = tempfile.mkdtemp(prefix="ci-shard-ev-")
        target = os.path.join(base, "nope", "nested", "evidence.json")
        shard._write_evidence(target, {"a": 1})
        with open(target, encoding="utf-8") as fh:
            import json
            self.assertEqual(json.load(fh), {"a": 1})

class ShardLoaderTests(unittest.TestCase):
    def test_shard_modules_import_cleanly(self):
        import unittest
        import sys
        idir = str(ROOT / "tests" / "integration")
        sys.path.insert(0, idir)
        try:
            loader = unittest.TestLoader()
            for sid, mods in shard.INTEGRATION_SHARDS.items():
                with self.subTest(shard=sid):
                    suite = unittest.TestSuite()
                    for mod in mods:
                        suite.addTests(loader.loadTestsFromName(mod))
                    bad = [t.id() for t in _iter(suite)
                           if "FailedTest" in type(t).__name__]
                    self.assertEqual([], bad, f"shard {sid} has unloadable modules")
                    self.assertGreater(suite.countTestCases(), 0,
                                       f"shard {sid} is empty")
        finally:
            sys.path.remove(idir)


def _iter(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _iter(t)
        else:
            yield t
