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


def _ok_record(i, mod="test_mod"):
    return {"id": f"{mod}.Class.test_{i:02d}", "result": "ok", "reason": "",
            "duration_s": 0.1}


_DEFAULT_MOD = {"rc-0": "test_m_a", "rc-1": "test_m_b",
                "rc-2": "test_m_c", "rc-3": "test_m_d",
                "host": "test_m_host"}


def _evidence(shard_id="rc-0", kind="integration-unittest", n=3, sha="abc123",
              run_id="run-1", attempt="1", mod=None):
    mod = mod or _DEFAULT_MOD.get(shard_id, "test_mod")
    planned = [f"{mod}.Class.test_{i:02d}" for i in range(n)]
    return {
        "schema": 1, "manifest_version": 1, "sha": sha, "kind": kind,
        "shard_id": shard_id, "run_id": run_id, "run_attempt": attempt,
        "toolchain": {"cc65": "x", "geargrafx": "y"},
        "gate_states": {"dev_rom_present": True,
                         "env_oss_pce_game_cue_set": False},
        "started": "2026-11-27T00:00:00Z",
        "planned_ids": list(planned),
        "executed": [_ok_record(i, mod) for i in range(n)],
        "failures": 0, "errors": 0, "duration_s": 1.0,
    }


def _static_evidence(sha="abc123", run_id="run-1", attempt="1"):
    return {
        "schema": 1, "manifest_version": 1, "sha": sha, "kind": "static",
        "shard_id": "static", "run_id": run_id, "run_attempt": attempt,
        "toolchain": {"cc65": "x", "geargrafx": "y"},
        "gate_states": {},
        "started": "2026-11-27T00:00:00Z",
        "planned_units": list(shard.STATIC_UNITS),
        "executed": [{"unit": u, "exit_code": 0, "duration_s": 0.1, "note": ""}
                     for u in shard.STATIC_UNITS],
        "failures": 0, "errors": 0,
    }


def _direct_evidence(units=("direct:smoke",), sha="abc123",
                     run_id="run-1", attempt="1"):
    return {
        "schema": 1, "manifest_version": 1, "sha": sha, "kind": "integration-direct",
        "shard_id": "direct", "run_id": run_id, "run_attempt": attempt,
        "toolchain": {"cc65": "x", "geargrafx": "y"},
        "gate_states": {},
        "started": "2026-11-27T00:00:00Z",
        "planned_units": list(shard.DIRECT_UNITS),
        "executed": [{"unit": u, "exit_code": 0, "duration_s": 0.1, "note": ""}
                     for u in units],
        "failures": 0, "errors": 0,
    }


def _full_green_set():
    out = [_evidence(sid, n=2) for sid in sorted(shard.INTEGRATION_SHARDS)]
    out[0]["planned_ids"] = out[0]["planned_ids"][:0]
    out[0]["executed"] = []
    return out


class AggregateNegativeTests(unittest.TestCase):
    def _run_env(self):
        # Deterministic run binding: the aggregator compares evidence run IDs
        # against its own GITHUB_RUN_ID (fail-closed against cross-run mixing).
        # Pin both sides to the fixture values so the unit tests do not depend
        # on the ambient CI environment.
        return {"GITHUB_RUN_ID": "run-1", "GITHUB_RUN_ATTEMPT": "1"}

    def _aggregate(self, evs, **kw):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, self._run_env(), clear=False):
            return shard.aggregate(evs, **kw)

    def _green(self):
        mods = {"rc-0": "test_m_a", "rc-1": "test_m_b",
                "rc-2": "test_m_c", "rc-3": "test_m_d"}
        evs = []
        for sid, mod in mods.items():
            evs.append(_evidence(sid, n=2, mod=mod))
        host = _evidence("host", kind="host-unittest", n=2)
        for e in evs + [host]:
            e.setdefault("run_id", "run-1")
            e.setdefault("run_attempt", "1")
        direct = _direct_evidence(units=tuple(shard.DIRECT_UNITS))
        static = _static_evidence()
        return evs + [host, direct, static]

    def _host_live(self, evs):
        for e in evs:
            if e["shard_id"] == "host":
                return list(e["planned_ids"])
        return []

    def _synthetic_manifest(self):
        return {"rc-0": ["test_m_a"], "rc-1": ["test_m_b"],
                "rc-2": ["test_m_c"], "rc-3": ["test_m_d"]}

    def _synthetic_live_ids(self, evs):
        by_mod = {}
        for e in evs:
            if e.get("kind") != "integration-unittest":
                continue
            mod = e["planned_ids"][0].split(".")[0] if e.get("planned_ids") else None
            if mod:
                by_mod.setdefault(mod, []).extend(i for i in e.get("planned_ids", []))
        return {m: sorted(set(v)) for m, v in by_mod.items()}

    def _identity(self):
        return {"sha": "abc123", "run_id": "run-1", "run_attempt": "1",
                "toolchain": {"cc65": "x", "geargrafx": "y"}}

    def _problems(self, evs, **kw):
        kw.setdefault("shards", self._synthetic_manifest())
        kw.setdefault("live_integration_ids", self._synthetic_live_ids(evs))
        kw.setdefault("live_host_ids", self._host_live(evs))
        kw.setdefault("identity", self._identity())
        problems = shard.aggregate(evs, **kw)
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
        self.assertTrue(any("not in expected set" in p for p in problems), problems)

    def test_05_empty_shard_red(self):
        evs = self._green()
        evs[1]["executed"] = []
        problems = self._problems(evs)
        self.assertTrue(any("empty shard" in p or "missing shard" in p or
                            "planned ID missing" in p or "missing from executed" in p
                            for p in problems), problems)
        evs = self._green()
        evs[1]["planned_ids"] = []
        problems = self._problems(evs)
        self.assertTrue(any("non-empty list" in p for p in problems), problems)

    def test_06_sha_mismatch_red(self):
        evs = self._green()
        evs[1]["sha"] = "different"
        problems = self._problems(evs)
        self.assertTrue(any("sha mismatch" in p for p in problems), problems)

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
        problems = self._problems([])
        self.assertTrue(any("no shard evidence" in p for p in problems), problems)

    def test_10_import_failure_red(self):
        evs = self._green()
        rec = _ok_record(0)
        rec["id"] = "unittest.loader._FailedTest.test_x"
        evs[0]["executed"].append(rec)
        problems = self._problems(evs)
        self.assertTrue(any("not in expected set" in p or "unknown" in p.lower()
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
        self.assertTrue(any("version mismatch" in p or "bad manifest version" in p for p in problems), problems)

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
        live = dict(self._synthetic_live_ids(evs))
        live["test_brand_new_mod"] = ["test_brand_new_mod.Class.test_00"]
        problems = shard.aggregate(evs, shards=self._synthetic_manifest(),
                                   live_integration_ids=live,
                                   live_host_ids=self._host_live(evs),
                                   identity=self._identity())
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

    def test_listed_skip_green_with_reason(self):
        # Listed (non-gated) reason: the skipped test must still belong to the
        # shard's expected set; the allowlist only waives the SKIP itself.
        mod = "test_ex_dspoff_real_core"
        shards = {"rc-0": ["test_m_a"], "rc-1": ["test_m_b"],
                  "rc-2": ["test_m_c", mod], "rc-3": ["test_m_d"]}
        live = {m: [f"{m}.Class.test_{i:02d}" for i in range(2)]
                for mods in shards.values() for m in mods}
        tid = (mod + ".ExperimentalSlot33RealCpuTests."
               "test_two_call_flow_reaches_slot_33_after_slot_02_scaffolding")
        live[mod] = sorted(set(live[mod]) | {tid})
        evs = []
        for sid, mods in shards.items():
            planned = sorted({i for m in mods for i in live[m]})
            recs = []
            for pid in planned:
                if pid == tid:
                    recs.append({"id": pid, "result": "skipped",
                                 "reason": "superseded by the union real-$02 SEQ; extra",
                                 "duration_s": 0.1})
                else:
                    recs.append({"id": pid, "result": "ok", "reason": "",
                                 "duration_s": 0.1})
            evs.append({"schema": 1, "manifest_version": 1, "sha": "abc123",
                         "kind": "integration-unittest", "shard_id": sid,
                         "run_id": "run-1", "run_attempt": "1",
                         "toolchain": {"cc65": "x", "geargrafx": "y"},
                         "gate_states": {"dev_rom_present": True,
                                          "env_oss_pce_game_cue_set": False},
                         "started": "2026-11-27T00:00:00Z",
                         "planned_ids": planned, "executed": recs,
                         "failures": 0, "errors": 0, "duration_s": 1.0})
        host = _evidence("host", kind="host-unittest", n=2)
        host.update({"run_id": "run-1", "run_attempt": "1",
                     "gate_states": {"dev_rom_present": True,
                                      "env_oss_pce_game_cue_set": False}})
        direct = _direct_evidence(units=tuple(shard.DIRECT_UNITS))
        direct.update({"run_id": "run-1", "run_attempt": "1",
                       "gate_states": {"dev_rom_present": True,
                                        "env_oss_pce_game_cue_set": False}})
        static = _static_evidence()
        static.update({"run_id": "run-1", "run_attempt": "1",
                       "gate_states": {"dev_rom_present": True,
                                        "env_oss_pce_game_cue_set": False}})
        evs = evs + [host, direct, static]
        problems = shard.aggregate(evs, shards=shards, live_integration_ids=live,
                                   live_host_ids=[r["id"] for r in host["executed"]],
                                   identity=self._identity())
        self.assertEqual([], [p for p in problems if "unclassified" not in p
                              and "unknown test module" not in p], problems)


    def _gated_skip_case(self, test_id, reason, gates, target_shard="rc-1", mod=None,
                           whole_class=False):
        # Synthetic world where the target shard owns the skipped test's
        # module, so the skip is a legitimate member of the expected set
        # (mirrors the real manifest owning test_g5_progress in rc-1; host
        # tests live outside the integration manifest entirely).
        mod = mod or test_id.split(".")[0]
        if target_shard == "host":
            shards = {"rc-0": ["test_m_a"], "rc-1": ["test_m_b"],
                      "rc-2": ["test_m_c"], "rc-3": ["test_m_d"]}
            live = {m: [f"{m}.Class.test_{i:02d}" for i in range(2)]
                    for m in ["test_m_a", "test_m_b", "test_m_c", "test_m_d"]}
            live[mod] = sorted(set(live.get(mod, [])) | {test_id})
        else:
            shards = {"rc-0": ["test_m_a"], "rc-1": ["test_m_b"],
                      "rc-2": ["test_m_c"], "rc-3": ["test_m_d"]}
            shards[target_shard] = sorted(set(shards[target_shard] + [mod]))
            live = {m: [f"{m}.Class.test_{i:02d}" for i in range(2)]
                    for mods in shards.values() for m in mods}
            live.setdefault(mod, []).append(test_id)
            live = {m: sorted(set(v)) for m, v in live.items()}
        evs = []
        for sid, mods in shards.items():
            planned = sorted({i for m in mods for i in live.get(m, [])})
            recs = [dict(id=pid, result="ok", reason="", duration_s=0.1)
                    for pid in planned]
            evs.append({"schema": 1, "manifest_version": 1, "sha": "abc123",
                        "kind": "integration-unittest", "shard_id": sid,
                        "run_id": "run-1", "run_attempt": "1",
                        "toolchain": {"cc65": "x", "geargrafx": "y"},
                        "gate_states": dict(gates),
                        "started": "2026-11-27T00:00:00Z",
                        "planned_ids": list(planned),
                        "executed": list(recs),
                        "failures": 0, "errors": 0, "duration_s": 1.0})
        host = _evidence("host", kind="host-unittest", n=2)
        host.update({"run_id": "run-1", "run_attempt": "1",
                     "gate_states": dict(gates)})
        direct = _direct_evidence(units=tuple(shard.DIRECT_UNITS))
        direct.update({"run_id": "run-1", "run_attempt": "1",
                       "gate_states": dict(gates)})
        static = _static_evidence()
        static.update({"run_id": "run-1", "run_attempt": "1",
                       "gate_states": dict(gates)})
        evs = evs + [host, direct, static]
        # Mark one planned ID in the target shard (or host) as the gated
        # skip under test. Planned stays the FULL expected set (MAJOR 1: a
        # subset plan would itself be RED); the skip is proven by the
        # matching allowlisted gate + pseudo record, never by narrowing planned.
        if target_shard == "host":
            for e in evs:
                if e["shard_id"] == "host":
                    e["planned_ids"] = [test_id] + e["planned_ids"][1:]
                    e["executed"] = [dict(id=test_id, result="ok", reason="",
                                          duration_s=0.1)] + e["executed"][1:]
        tgt = next(e for e in evs if e["shard_id"] == target_shard)
        assert test_id in tgt["planned_ids"], test_id
        for r in tgt["executed"]:
            if r["id"] == test_id:
                r["result"] = "skipped"
                r["reason"] = reason
                break
        else:
            raise AssertionError(f"skip target not in planned: {test_id}")
        if whole_class:
            # Whole-class-style gate demonstration (e.g. a setUpClass-level
            # skip): the pseudo record is the evidence; the skipped test stays
            # in planned (unexecuted). Only used where an allowlisted
            # whole-class rule exists for the class.
            tgt["executed"] = [r for r in tgt["executed"] if r["id"] != test_id]
            tgt["executed"].append({"id": f"setUpClass ({test_id.rsplit('.', 1)[0]})",
                                    "result": "skipped", "reason": reason, "duration_s": 0.0})
        live_host = [r["id"] for r in host["executed"]]
        # Only manifest-owned modules participate in the live set: host-side
        # modules (e.g. the GameCue test, a host test) are covered by the
        # host gate, not the integration newcomer gate.
        owned = {m for mods in shards.values() for m in mods}
        live = {m: v for m, v in live.items() if m in owned}
        return shard.aggregate(evs, shards=shards, live_integration_ids=live,
                               live_host_ids=live_host, identity=self._identity())
    def test_gate_gamecue_tripped_green(self):
        problems = self._gated_skip_case(
            "test_cd_boot_loader_layout.GameCueTests.test_x",
            "set OSS_PCE_GAME_CUE to the local game CUE path",
            {"dev_rom_present": True, "env_oss_pce_game_cue_set": False},
            target_shard="host")
        self.assertEqual([], problems, problems)

    def test_gate_gamecue_env_set_red(self):
        problems = self._gated_skip_case(
            "test_cd_boot_loader_layout.GameCueTests.test_x",
            "set OSS_PCE_GAME_CUE to the local game CUE path",
            {"dev_rom_present": True, "env_oss_pce_game_cue_set": True},
            target_shard="host")
        self.assertTrue(any("unlisted SKIP" in p for p in problems), problems)

    def test_gate_gamecue_wrong_reason_red(self):
        problems = self._gated_skip_case(
            "test_cd_boot_loader_layout.GameCueTests.test_x",
            "some other reason",
            {"dev_rom_present": True, "env_oss_pce_game_cue_set": False},
            target_shard="host")
        self.assertTrue(any("unlisted SKIP" in p for p in problems), problems)

    def test_gate_g5progress_tripped_green(self):
        problems = self._gated_skip_case(
            "test_g5_progress.G5SyntheticMeasurementTests."
            "test_synthetic_abc_differential",
            "SKIP real-core G5 synthetic (not attempted): DEV ROM absent",
            {"dev_rom_present": False, "env_oss_pce_game_cue_set": False})
        self.assertEqual([], problems, problems)

    def test_gate_g5progress_present_red(self):
        problems = self._gated_skip_case(
            "test_g5_progress.G5SyntheticMeasurementTests."
            "test_synthetic_abc_differential",
            "SKIP real-core G5 synthetic (not attempted): DEV ROM absent",
            {"dev_rom_present": True, "env_oss_pce_game_cue_set": False})
        self.assertTrue(any("unlisted SKIP" in p for p in problems), problems)


class ClassSkipEndToEndTests(unittest.TestCase):
    """Aggregator-level coverage of the pseudo class-skip path, mirroring the
    real CI case (whole BuilderRomBoundaryTest class skipped when the dev-rom
    build output is absent)."""

    def _host_world(self, gate_states, reason):
        host = _evidence("host", kind="host-unittest", n=1)
        host["planned_ids"] = ["test_api_744_ipl_sel_boundary.BuilderRomBoundaryTest",
                               "test_m_host.Class.test_01"]
        host["executed"] = [
            {"id": "setUpClass (test_api_744_ipl_sel_boundary.BuilderRomBoundaryTest)",
             "result": "skipped", "reason": reason, "duration_s": 0.0},
            {"id": "test_m_host.Class.test_01", "result": "ok", "reason": "",
             "duration_s": 0.1},
        ]
        host["gate_states"] = dict(gate_states)
        host.update({"run_id": "run-1", "run_attempt": "1"})
        evs = [_evidence("rc-0", n=2, mod="test_m_a"), _evidence("rc-1", n=2, mod="test_m_b"),
               _evidence("rc-2", n=2, mod="test_m_c"), _evidence("rc-3", n=2, mod="test_m_d")]
        for e in evs:
            e.update({"run_id": "run-1", "run_attempt": "1",
                      "gate_states": dict(gate_states)})
        direct = _direct_evidence(units=tuple(shard.DIRECT_UNITS))
        direct.update({"run_id": "run-1", "run_attempt": "1",
                       "gate_states": dict(gate_states)})
        static = _static_evidence()
        static.update({"run_id": "run-1", "run_attempt": "1",
                       "gate_states": dict(gate_states)})
        return evs + [host, direct, static]

    def _agg(self, evs, live=None):
        shards = {"rc-%d" % i: ["test_m_%s" % chr(97 + i)] for i in range(4)}
        if live is None:
            live = {}
            for sid, mods in shards.items():
                for m in mods:
                    live.setdefault(m, []).extend(
                        f"{m}.Class.test_{i:02d}" for i in range(2))
            for e in evs:
                if e.get("kind") != "integration-unittest":
                    continue
                for pid in e.get("planned_ids", []):
                    live.setdefault(pid.split(".")[0], [])
                    if pid not in live[pid.split(".")[0]]:
                        live[pid.split(".")[0]].append(pid)
            live = {m: sorted(set(v)) for m, v in live.items()}
        return shard.aggregate(evs, shards=shards, live_integration_ids=live,
                               live_host_ids=[r["id"] for r in evs[4]["executed"]
                                              if not r["id"].startswith("setUpClass (")] +
                                              ["test_api_744_ipl_sel_boundary.BuilderRomBoundaryTest"],
                               identity={"sha": "abc123", "run_id": "run-1", "run_attempt": "1",
                                         "toolchain": {"cc65": "x", "geargrafx": "y"}})

    def test_class_skip_gate_tripped_green(self):
        evs = self._host_world({"dev_rom_present": False, "env_oss_pce_game_cue_set": False},
                               "needs make dev-rom output (CI build step)")
        problems = self._agg(evs)
        self.assertEqual([], [p for p in problems
                              if "unclassified" not in p and "unknown test module" not in p],
                         problems)

    def test_class_skip_gate_present_red(self):
        evs = self._host_world({"dev_rom_present": True, "env_oss_pce_game_cue_set": False},
                               "needs make dev-rom output (CI build step)")
        problems = self._agg(evs)
        self.assertTrue(any("class-level SKIP" in p for p in problems), problems)

    def test_class_skip_wrong_reason_red(self):
        evs = self._host_world({"dev_rom_present": False, "env_oss_pce_game_cue_set": False},
                               "some other reason")
        problems = self._agg(evs)
        self.assertTrue(any("class-level SKIP" in p for p in problems), problems)

    def test_pseudo_helpers(self):
        recs = [{"id": "setUpClass (mod.ClassName)", "result": "skipped", "reason": "r"}]
        self.assertEqual([("mod.ClassName", "r")], shard._pseudo_class_skip_record(recs))
        self.assertEqual([], shard._pseudo_class_skip_record(
            [{"id": "mod.Class.test_x", "result": "ok", "reason": ""}]))


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

class RunIdentityNegativeTests(unittest.TestCase):
    def _base(self):
        evs = [_evidence("rc-0", n=2), _evidence("rc-1", n=1),
               _evidence("rc-2", n=1), _evidence("rc-3", n=1)]
        host = _evidence("host", kind="host-unittest", n=2)
        for e in evs + [host]:
            e.setdefault("run_id", "run-1")
            e.setdefault("run_attempt", "1")
        direct = _direct_evidence(units=tuple(shard.DIRECT_UNITS))
        static = _static_evidence()
        return evs + [host, direct, static]

    def _aggregate_here(self, evs, **kw):
        by_mod = {}
        for e in evs:
            if e.get("kind") != "integration-unittest":
                continue
            mod = _DEFAULT_MOD.get(e["shard_id"], "test_mod")
            by_mod.setdefault(mod, []).extend(
                r["id"] for r in e.get("executed", [])
                if not r["id"].startswith("setUpClass ("))
            for pid in e.get("planned_ids", []):
                if pid.startswith(mod + "."):
                    by_mod[mod].append(pid)
        by_mod = {m: sorted(set(v)) for m, v in by_mod.items()}
        shards = {sid: [m] for sid, m in _DEFAULT_MOD.items() if sid != "host"}
        kw.setdefault("shards", shards)
        kw.setdefault("live_integration_ids", by_mod)
        host_ids = []
        for e in evs:
            if e.get("shard_id") == "host":
                host_ids = list(e.get("planned_ids", []))
        kw.setdefault("live_host_ids", host_ids)
        kw.setdefault("identity", {"sha": "abc123", "run_id": "run-1", "run_attempt": "1",
                                     "toolchain": {"cc65": "x", "geargrafx": "y"}})
        return shard.aggregate(evs, **kw)

    def test_mixed_run_ids_red(self):
        evs = self._base()
        evs[0]["run_id"] = "run-other"
        problems = self._aggregate_here(evs)
        self.assertTrue(any("run_id mismatch" in p for p in problems), problems)

    def test_mixed_attempts_red(self):
        evs = self._base()
        evs[1]["run_attempt"] = "2"
        problems = self._aggregate_here(evs)
        self.assertTrue(any("attempt" in p for p in problems), problems)
