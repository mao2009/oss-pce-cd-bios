#!/usr/bin/env python3
"""Deterministic CI sharding for the host/integration suites with fail-closed aggregation.

Not a product/test change: the same tests run, only distributed. Every rule is
designed so a silent test drop, duplication, unknown ID, empty shard, SHA
mismatch, failure, cancellation, missing artifact, import failure, unlisted
SKIP, manifest tamper, missing aggregate dependency, unclassified newcomer, or
missing direct-integration unit is RED (never PASS).

Usage:
  ci_sharding.py list-suites            # planned sets from the manifest + live discover
  ci_sharding.py check-manifest         # manifest self-consistency vs live discover (fail-closed)
  ci_sharding.py run-shard --shard ID --evidence-out PATH
  ci_sharding.py run-host --evidence-out PATH
  ci_sharding.py run-direct [--only UNIT ...] --evidence-out PATH
  ci_sharding.py aggregate --evidence DIR [--sha SHA]

The manifest below is the single source of truth for shard membership. A test
module discovered live but absent from every shard is an UNCLASSIFIED newcomer
and fails aggregation (add it to a shard in the same PR that adds the test).
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MANIFEST_VERSION = 1

# Integration unittest shards: explicit module lists, balanced by measured
# module wall time on pinned runners (run 45941187061, ~982s total). Module
# granularity preserves setUpModule/setUpClass semantics and in-module order.
# Rebalance only on measured data; keep this list sorted for reviewability.
INTEGRATION_SHARDS = {
    "rc-0": [
        "test_g5_cdread_commercial",
        "test_g5_2base",
        "test_g5_memopen_4a",
        "test_apicall_capacity",
        "test_apicall_vdc_snapshot",
        "test_ex_bcdbin_real_core",
        "test_g5_expD_next_probe",
    ],
    "rc-1": [
        "test_g5_satclr_36",
        "test_g5_irqon_2c",
        "test_dev_g5_profile",
        "test_g5_progress",
        "test_matched_selection_realcore",
        "test_unified_link_realcore",
        "test_unified_realcore",
        "test_run_input_guest_real_core",
        "test_mpr1_tam_irq",
    ],
    "rc-2": [
        "test_g5_dotmod_24",
        "test_g5_scrmod_25",
        "test_g5_integrated_chain",
        "test_geargrafx_frame_tracer_synthetic_cd",
        "test_cd_base_proto_v1",
        "test_cd_read_link_v1",
        "test_debugger_setup",
        "test_getver_observation_real_core",
        "test_irq_366_entry_execbp",
    ],
    "rc-3": [
        "test_g5_rcroff_2b",
        "test_g5_sprdma_35",
        "test_g5_chain_7api_24_36",
        "test_ipl_loader_g5_4a_793",
        "test_ipl_loader_g5_800",
        "test_build_core",
        "test_ex_dspoff_real_core",
    ],
}

# `make integration` direct-script units, in Makefile order. The runner invokes
# `make <unit>` so the Makefile stays the source of truth for WHAT each unit
# does; this list pins WHICH units must run (a dropped Makefile line is RED).
STATIC_UNITS = [
    "static:check",
    "static:release",
    "static:repro",
]

STATIC_MAKE_TARGETS = {
    # Each unit maps to an ordered list of argv commands (verbatim copies of
    # the current verify-full static steps; the Makefile stays the
    # implementation of what each command does).
    "static:check": [["make", "check"]],
    "static:release": [
        ["make", "build", "MODE=release"],
        ["cmp", "build/debug/smoke-test-not-bios.pce",
         "build/release/smoke-test-not-bios.pce"],
        [sys.executable, "tools/build.py", "--verify",
         "build/debug/smoke-test-not-bios.pce"],
    ],
    "static:repro": [
        [sys.executable, "tools/rom_provenance.py", "--reproduce", "smoke",
         "--mode", "debug"],
        ["cmp", "build/debug/smoke-test-not-bios.pce",
         "build/rom-provenance/a/smoke-test-not-bios.pce"],
        [sys.executable, "tools/rom_provenance.py", "--rom",
         "build/debug/smoke-test-not-bios.pce", "--manifest",
         "build/debug/manifest.json"],
    ],
}

DIRECT_UNITS = [
    "direct:smoke",
    "direct:reference-guard",
    "direct:reference-guard-issue173",
    "direct:mmio1804-trap",
    "direct:mmio1804-preblock",
    "direct:irq-register",
    "direct:st-bypass",
    "direct:bcdbin",
    "direct:block-write-guard",
    "direct:block-transfer-matrix",
    "direct:block-mid-refusal",
    "direct:stack-access-guard",
    "direct:stack-methods",
    "direct:stack-methods-compare",
    "direct:stack-s5",
    "direct:stack-s6",
    "direct:stack-irq-timing",
    "direct:stack-mpr-buffer",
    "direct:caller-stack-contract",
    "direct:st-boundary",
    "direct:block-preblock",
]

DIRECT_MAKE_TARGETS = {
    "direct:smoke": None,  # custom argv, handled below
    "direct:reference-guard": ["geargrafx_reference_guard.py"],
    "direct:reference-guard-issue173": ["geargrafx_reference_guard_issue173.py"],
    "direct:mmio1804-trap": ["geargrafx_mmio1804_trap.py"],
    "direct:mmio1804-preblock": ["geargrafx_mmio1804_preblock.py"],
    "direct:irq-register": ["geargrafx_irq_register.py"],
    "direct:st-bypass": ["geargrafx_st_bypass.py"],
    "direct:bcdbin": ["geargrafx_bcdbin.py"],
    "direct:block-write-guard": ["block-write-guard"],
    "direct:block-transfer-matrix": ["block-transfer-matrix"],
    "direct:block-mid-refusal": ["block-mid-refusal"],
    "direct:stack-access-guard": ["stack-access-guard"],
    "direct:stack-methods": ["stack-methods"],
    "direct:stack-methods-compare": ["stack-methods-compare"],
    "direct:stack-s5": ["stack-s5"],
    "direct:stack-s6": ["stack-s6"],
    "direct:stack-irq-timing": ["stack-irq-timing"],
    "direct:stack-mpr-buffer": ["stack-mpr-buffer"],
    "direct:caller-stack-contract": ["caller-stack-contract"],
    "direct:st-boundary": ["st-boundary"],
    "direct:block-preblock": ["block-preblock"],
}

# Skips that are allowed to appear in evidence. `scope` is "always" (reason
# must match exactly as prefix) or "conditional" (reason must match AND the
# recorded gate state must show the gate tripped).
ALLOWED_SKIPS = [
    {
        "id": "test_ex_dspoff_real_core.ExperimentalSlot33RealCpuTests."
              "test_two_call_flow_reaches_slot_33_after_slot_02_scaffolding",
        "reason_prefix": "superseded by the union real-$02 SEQ",
        "scope": "always",
    },
]

# Whole-class conditional skips: the class's tests are planned but may all be
# absent from executed when the gate trips. The pseudo `setUpClass ... skipped`
# line counts as skipped, NOT as Ran (verified against CPython unittest).
ALLOWED_CLASS_SKIPS = [
    {
        "class": "test_api_744_ipl_sel_boundary.BuilderRomBoundaryTest",
        "reason_prefix": "needs make dev-rom output",
        "gate": "missing build/debug/bios-dev/dev-only-not-compatible-syscard3.pce",
    },
]

EVIDENCE_SCHEMA = 1


def _tool_pins():
    try:
        import json as _json
        with open(os.path.join(ROOT, "tools", "dependencies.json"), encoding="utf-8") as fh:
            deps = _json.load(fh)
        return {
            "cc65": deps.get("cc65", {}).get("revision", "UNKNOWN"),
            "geargrafx": deps.get("geargrafx", {}).get("revision", "UNKNOWN"),
        }
    except OSError:
        return {"cc65": "UNKNOWN", "geargrafx": "UNKNOWN"}


def discover_ids(start_dir, pattern):
    # Isolated subprocess per suite: importing both tests/host and
    # tests/integration in one process collides on identical top-level module
    # names, so discovery runs in a fresh interpreter with cwd=ROOT (exactly
    # like `make test` / `make integration-test` invoke it).
    code = (
        "import json, unittest\n"
        "def _walk(s):\n"
        "    for t in s:\n"
        "        if isinstance(t, unittest.TestSuite):\n"
        "            yield from _walk(t)\n"
        "        else:\n"
        "            yield t.id()\n"
        "loader = unittest.TestLoader()\n"
        "suite = loader.discover(%r, pattern=%r)\n"
        "print(json.dumps(list(_walk(suite))))\n"
        % (start_dir, pattern)
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"discovery failed for {start_dir}: {proc.stderr[-2000:]}")
    import json as _json
    return _json.loads(proc.stdout)
    ids = []

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                ids.append(t.id())
    walk(suite)
    return ids


def manifest_modules():
    mods = []
    for shard in sorted(INTEGRATION_SHARDS):
        mods.extend(INTEGRATION_SHARDS[shard])
    return mods


def check_manifest(live_modules=None):
    """Manifest self-consistency vs live discover. Returns a list of problems."""
    problems = []
    if live_modules is None:
        live_modules = sorted({i.split(".")[0] for i in discover_ids("tests/integration", "test_*.py")})
    listed = manifest_modules()
    if len(listed) != len(set(listed)):
        problems.append("manifest lists duplicate modules")
    for mod in live_modules:
        if mod not in listed:
            problems.append(f"unclassified test module (add to a shard in the same PR): {mod}")
    for mod in listed:
        if mod not in live_modules:
            problems.append(f"manifest lists unknown test module: {mod}")
    counts = {}
    for shard, mods in INTEGRATION_SHARDS.items():
        if not mods:
            problems.append(f"empty shard: {shard}")
        counts[shard] = len(mods)
    for unit in DIRECT_UNITS:
        if unit not in DIRECT_MAKE_TARGETS:
            problems.append(f"direct unit without runner mapping: {unit}")
    for unit in DIRECT_MAKE_TARGETS:
        if unit not in DIRECT_UNITS:
            problems.append(f"runner mapping without manifest unit: {unit}")
    for unit in STATIC_UNITS:
        if unit not in STATIC_MAKE_TARGETS:
            problems.append(f"static unit without runner mapping: {unit}")
    for unit in STATIC_MAKE_TARGETS:
        if unit not in STATIC_UNITS:
            problems.append(f"runner mapping without manifest unit: {unit}")
    return problems


class _Collector(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.records = []
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.monotonic()
        super().startTest(test)

    def _finish(self, test, result, reason=""):
        started = self.started.pop(test.id(), None)
        duration = (time.monotonic() - started) if started is not None else 0.0
        self.records.append({"id": test.id(), "result": result, "reason": reason,
                             "duration_s": round(duration, 3)})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._finish(test, "ok")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._finish(test, "fail")

    def addError(self, test, err):
        super().addError(test, err)
        self._finish(test, "error")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._finish(test, "skipped", str(reason))

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._finish(test, "expected-failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._finish(test, "unexpected-success")


def _gate_states():
    return {
        "dev_rom_present": os.path.isfile(os.path.join(
            ROOT, "build", "debug", "bios-dev", "dev-only-not-compatible-syscard3.pce")),
    }


def _base_evidence(kind, shard_id):
    return {
        "schema": EVIDENCE_SCHEMA,
        "manifest_version": MANIFEST_VERSION,
        "sha": os.environ.get("GITHUB_SHA", "local"),
        "kind": kind,
        "shard_id": shard_id,
        "toolchain": _tool_pins(),
        "gate_states": _gate_states(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _require_cc65():
    # Same contract as `make test` / `make integration-test`: a missing cc65
    # is a FAILURE, never a SKIP (see tools/environment.py requires_cc65).
    os.environ["OSS_PCE_REQUIRE_CC65"] = "1"


def run_shard(shard_id, evidence_out):
    _require_cc65()
    if shard_id not in INTEGRATION_SHARDS:
        raise ValueError(f"unknown shard: {shard_id} (expected one of {sorted(INTEGRATION_SHARDS)})")
    modules = INTEGRATION_SHARDS[shard_id]
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for mod in modules:
        suite.addTests(loader.loadTestsFromName(mod))
    planned = []

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                planned.append(t.id())
    walk(suite)
    collector = _Collector()
    started = time.monotonic()
    suite.run(collector)
    duration = time.monotonic() - started
    evidence = _base_evidence("integration-unittest", shard_id)
    evidence.update({
        "planned_ids": sorted(planned),
        "executed": sorted(collector.records, key=lambda r: r["id"]),
        "failures": len(collector.failures),
        "errors": len(collector.errors),
        "duration_s": round(duration, 3),
    })
    with open(evidence_out, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=2, sort_keys=True)
    return collector


def run_host(evidence_out):
    _require_cc65()
    loader = unittest.TestLoader()
    suite = loader.discover("tests/host")
    planned = []

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                planned.append(t.id())
    walk(suite)
    collector = _Collector()
    started = time.monotonic()
    suite.run(collector)
    duration = time.monotonic() - started
    evidence = _base_evidence("host-unittest", "host")
    evidence.update({
        "planned_ids": sorted(planned),
        "executed": sorted(collector.records, key=lambda r: r["id"]),
        "failures": len(collector.failures),
        "errors": len(collector.errors),
        "duration_s": round(duration, 3),
    })
    with open(evidence_out, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=2, sort_keys=True)
    return collector


def _run_make_unit(unit):
    """Run one direct-integration unit. Returns (exit_code, duration_s, note)."""
    started = time.monotonic()
    if unit == "direct:smoke":
        cmd = [sys.executable, "tests/integration/geargrafx_smoke.py",
               "--rom", "build/debug/smoke-test-not-bios.pce",
               "--output", "build/debug/geargrafx-evidence.json"]
    else:
        target = DIRECT_MAKE_TARGETS[unit]
        if target is None:
            raise ValueError(f"no runner mapping for {unit}")
        if len(target) == 1 and target[0].endswith(".py"):
            cmd = [sys.executable, os.path.join("tests", "integration", target[0])]
        else:
            cmd = ["make"] + target
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800)
    duration = time.monotonic() - started
    note = ""
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-2000:]
        note = tail[-500:]
    return proc.returncode, round(duration, 3), note


def _run_unit_list(units, planned, runner, kind, shard_id, evidence_out):
    unknown = [u for u in units if u not in planned]
    if unknown:
        raise ValueError(f"unknown {kind} units: {unknown}")
    records = []
    for unit in units:
        code, duration, note = runner(unit)
        records.append({"unit": unit, "exit_code": code, "duration_s": duration,
                        "note": note})
    evidence = _base_evidence(kind, shard_id)
    evidence.update({
        "planned_units": list(planned),
        "executed": records,
    })
    with open(evidence_out, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=2, sort_keys=True)
    return records


def run_direct(only, evidence_out):
    return _run_unit_list([u for u in DIRECT_UNITS if not only or u in only],
                          DIRECT_UNITS, _run_make_unit, "integration-direct",
                          "direct", evidence_out)


def _run_static_unit(unit):
    started = time.monotonic()
    last_note = ""
    for argv in STATIC_MAKE_TARGETS[unit]:
        proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                              timeout=1800)
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr)[-2000:]
            return proc.returncode, round(time.monotonic() - started, 3), tail[-500:]
        last_note = (proc.stdout + proc.stderr)[-200:]
    return 0, round(time.monotonic() - started, 3), last_note[-500:]


def run_static(only, evidence_out):
    return _run_unit_list([u for u in STATIC_UNITS if not only or u in only],
                          STATIC_UNITS, _run_static_unit, "static",
                          "static", evidence_out)


def _match_allowed_skip(test_id, reason):
    for rule in ALLOWED_SKIPS:
        if (test_id == rule["id"] or test_id.startswith(rule["id"])) and \
                reason.startswith(rule["reason_prefix"]):
            return rule
    return None


def _pseudo_class_skip_record(records):
    """Pseudo `setUpClass (module.Class) ... skipped` records.

    Verified against CPython unittest: a setUpClass SkipTest produces ONE
    pseudo record whose id is literally `setUpClass (module.Class)`; it is
    counted in `skipped` but NOT in `testsRun`. Real test IDs of the class
    never appear as executed."""
    out = []
    for r in records:
        rid = r.get("id", "")
        if rid.startswith("setUpClass (") and rid.endswith(")"):
            out.append((rid[len("setUpClass ("):-1], r.get("reason", "")))
    return out


def _match_allowed_class_skip(class_name, reason, gate_states):
    for rule in ALLOWED_CLASS_SKIPS:
        if class_name == rule["class"] and reason.startswith(rule["reason_prefix"]):
            if rule["gate"].startswith("missing "):
                gate_file = rule["gate"].split(None, 1)[1]
                if gate_file.endswith("dev-only-not-compatible-syscard3.pce"):
                    gate_tripped = not gate_states.get("dev_rom_present", True)
                else:
                    gate_tripped = False
                if gate_tripped:
                    return rule
    return None


def aggregate(evidence_list, live_integration_modules=None, live_host_ids=None):
    """Fail-closed aggregation. Returns a list of problems (empty == GREEN)."""
    problems = []
    if not evidence_list:
        problems.append("no shard evidence provided")
        return problems
    shas = {e.get("sha") for e in evidence_list}
    if len(shas) != 1:
        problems.append(f"evidence spans multiple SHAs: {sorted(shas)}")
    kinds = {}
    for e in evidence_list:
        kinds.setdefault(e.get("kind"), []).append(e.get("shard_id"))
    # Expected shard set: 1 host + direct + N integration shards.
    expected_shards = {"host", "direct", "static"} | set(INTEGRATION_SHARDS)
    seen = set()
    for e in evidence_list:
        seen.add(e.get("shard_id"))
    for missing in sorted(expected_shards - seen):
        problems.append(f"missing shard evidence: {missing}")
    seen_counts = {}
    for e in evidence_list:
        seen_counts[e.get("shard_id")] = seen_counts.get(e.get("shard_id"), 0) + 1
    for sid in sorted(seen_counts):
        if seen_counts[sid] > 1:
            problems.append(f"duplicate shard evidence: {sid}")
    for e in evidence_list:
        if e.get("shard_id") not in expected_shards:
            problems.append(f"unknown shard evidence: {e.get('shard_id')}")
        if e.get("schema") != EVIDENCE_SCHEMA or e.get("manifest_version") != MANIFEST_VERSION:
            problems.append(f"shard {e.get('shard_id')}: schema/manifest version mismatch")
        if e.get("kind") in ("integration-unittest", "host-unittest") and \
                not e.get("executed"):
            problems.append(f"shard {e.get('shard_id')}: empty shard (zero executed tests)")
        if e.get("failures") or e.get("errors"):
            problems.append(f"shard {e.get('shard_id')}: reports failures={e.get('failures')} "
                            f"errors={e.get('errors')}")
    # Per-shard planned-vs-executed with allowlisted skips only.
    for e in evidence_list:
        kind, sid = e.get("kind"), e.get("shard_id")
        if kind in ("integration-direct", "static"):
            planned = list(DIRECT_UNITS if kind == "integration-direct"
                           else STATIC_UNITS)
            label = "direct" if kind == "integration-direct" else "static"
            executed = [r["unit"] for r in e.get("executed", [])]
            for u in executed:
                if u not in planned:
                    problems.append(f"{label}: unknown unit executed: {u}")
            for u in planned:
                if u not in executed:
                    problems.append(f"{label}: planned unit missing: {u}")
            for r in e.get("executed", []):
                if r.get("exit_code") != 0:
                    problems.append(f"{label}: unit {r['unit']} exit_code={r['exit_code']}")
            continue
        if kind not in ("integration-unittest", "host-unittest", "static"):
            problems.append(f"shard {sid}: unknown kind {kind!r}")
            continue
        planned = set(e.get("planned_ids", []))
        executed_ids = [r["id"] for r in e.get("executed", [])]
        seen_ids = set(executed_ids)
        if len(executed_ids) != len(seen_ids):
            problems.append(f"shard {sid}: duplicate executed test IDs")
        for rid in executed_ids:
            if rid.startswith("setUpClass ("):
                continue  # pseudo class-skip record; handled in the gate check below
            if rid not in planned:
                problems.append(f"shard {sid}: executed ID not in planned set: {rid}")
        results = {r["id"]: r for r in e.get("executed", [])}
        pseudos = _pseudo_class_skip_record(e.get("executed", []))
        pseudo_by_class = {}
        for class_name, reason in pseudos:
            rule = _match_allowed_class_skip(class_name, reason, e.get("gate_states", {}))
            if rule is None:
                problems.append(f"shard {sid}: unlisted class-level SKIP {class_name}: {reason!r}")
            else:
                pseudo_by_class[class_name] = rule
        for pid in sorted(planned - seen_ids):
            pid_class = ".".join(pid.split(".")[:2])
            if pid_class in pseudo_by_class:
                continue  # whole class skipped via allowlisted gate; pseudo record is the evidence
            problems.append(f"shard {sid}: planned ID missing from executed: {pid}")
        for rid, r in results.items():
            if rid.startswith("setUpClass ("):
                continue  # pseudo class-skip record; gated in _pseudo_class_skip_record above
            if r["result"] in ("fail", "error", "unexpected-success"):
                problems.append(f"shard {sid}: {rid} result={r['result']}")
            elif r["result"] == "skipped":
                if _match_allowed_skip(rid, r.get("reason", "")):
                    continue
                problems.append(f"shard {sid}: unlisted SKIP {rid}: {r.get('reason', '')!r}")
            elif r["result"] not in ("ok", "expected-failure"):
                problems.append(f"shard {sid}: {rid} unknown result {r['result']!r}")
    # Newcomer gate: live discover must equal the manifest union exactly.
    if live_integration_modules is None:
        live_integration_modules = sorted(
            {i.split(".")[0] for i in discover_ids("tests/integration", "test_*.py")})
    manifest_union = sorted(set(manifest_modules()))
    for mod in live_integration_modules:
        if mod not in manifest_union:
            problems.append(f"unclassified test module (add to a shard in the same PR): {mod}")
    for mod in manifest_union:
        if mod not in live_integration_modules:
            problems.append(f"manifest lists unknown test module: {mod}")
    # Host gate: the host shard must plan the full live host set (minus
    # allowlisted conditional skips, verified separately). A host runner may
    # not silently narrow its own planned set.
    if live_host_ids is None:
        live_host_ids = discover_ids("tests/host", "test_*.py")
    for e in evidence_list:
        if e.get("shard_id") == "host" and e.get("kind") == "host-unittest":
            for hid in live_host_ids:
                if hid not in e.get("planned_ids", []):
                    problems.append(f"host shard omits live test ID: {hid}")
            for pid in e.get("planned_ids", []):
                if pid not in live_host_ids:
                    problems.append(f"host shard plans unknown test ID: {pid}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-suites")
    p = sub.add_parser("check-manifest")
    p = sub.add_parser("run-shard")
    p.add_argument("--shard", required=True)
    p.add_argument("--evidence-out", required=True)
    p = sub.add_parser("run-host")
    p.add_argument("--evidence-out", required=True)
    p = sub.add_parser("run-direct")
    p.add_argument("--only", nargs="*", default=None)
    p.add_argument("--evidence-out", required=True)
    p = sub.add_parser("run-static")
    p.add_argument("--only", nargs="*", default=None)
    p.add_argument("--evidence-out", required=True)
    p = sub.add_parser("aggregate")
    p.add_argument("--evidence", nargs="+", required=True)
    args = parser.parse_args(argv)
    if args.command == "list-suites":
        print(json.dumps({
            "integration_shards": {k: list(v) for k, v in INTEGRATION_SHARDS.items()},
            "direct_units": list(DIRECT_UNITS),
            "allowed_skips": ALLOWED_SKIPS,
            "allowed_class_skips": ALLOWED_CLASS_SKIPS,
        }, indent=2, sort_keys=True))
        return 0
    if args.command == "check-manifest":
        problems = check_manifest()
        for problem in problems:
            print(f"MANIFEST-REJECTED {problem}")
        return 1 if problems else 0
    if args.command == "run-shard":
        collector = run_shard(args.shard, args.evidence_out)
        print(f"shard {args.shard}: Ran {collector.testsRun} "
              f"failures={len(collector.failures)} errors={len(collector.errors)}")
        return 0 if collector.wasSuccessful() else 1
    if args.command == "run-host":
        collector = run_host(args.evidence_out)
        print(f"host: Ran {collector.testsRun} failures={len(collector.failures)} "
              f"errors={len(collector.errors)}")
        return 0 if collector.wasSuccessful() else 1
    if args.command == "run-direct":
        records = run_direct(args.only, args.evidence_out)
        bad = [r for r in records if r["exit_code"] != 0]
        print(f"direct: {len(records)} units, failures={len(bad)}")
        return 1 if bad else 0
    if args.command == "run-static":
        records = run_static(args.only, args.evidence_out)
        bad = [r for r in records if r["exit_code"] != 0]
        print(f"static: {len(records)} units, failures={len(bad)}")
        return 1 if bad else 0
    if args.command == "aggregate":
        evidences = []
        for path in args.evidence:
            with open(path, encoding="utf-8") as fh:
                evidences.append(json.load(fh))
        problems = aggregate(evidences)
        for problem in problems:
            print(f"AGGREGATE-REJECTED {problem}")
        print("AGGREGATE-GREEN all shards reconciled" if not problems else
              f"AGGREGATE-RED {len(problems)} problem(s)")
        return 1 if problems else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
