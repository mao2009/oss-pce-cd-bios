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
import hashlib
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
        "test_bm_delete_blocker",
        "test_g5_expg_843",
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

# The smoke unit writes this file (same path the serial CI used); run-direct
# mirrors it next to its own evidence so the aggregate job can hand it off
# byte-identical under the historical path (MAJOR 3: handoff parity).
SMOKE_EVIDENCE_SRC = os.path.join("build", "debug", "geargrafx-evidence.json")
SMOKE_EVIDENCE_NAME = "geargrafx-evidence.json"

# The ROM the static shard verifies (release-compare, build --verify,
# same-input reproduce, rom_provenance --rom/--manifest all run against this
# file). The static evidence records its digest (MAJOR 1: handoff parity) so
# the aggregator can bind the verified tree to the published handoff tree.
STATIC_VERIFIED_ROM = os.path.join("build", "debug", "smoke-test-not-bios.pce")

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
    },
    {
        # Env-gated: commercial-CUE tests run only with a local CUE path.
        # Allowed only when the gate is tripped (env unset); the gate state
        # is recorded per-shard evidence (B4 root cause: countTestCases vs
        # testsRun accounting for conditional skips).
        "id": "test_cd_boot_loader_layout.GameCueTests.",
        "reason_prefix": "set OSS_PCE_GAME_CUE to the local game CUE path",
        "gate": "unset OSS_PCE_GAME_CUE",
    },
    {
        # Env-gated: synthetic G5 measurement needs the dev-rom build output.
        # Allowed only when the recorded gate shows it absent; any other
        # sub-reason (e.g. core absent) is NOT covered and stays RED.
        "id": "test_g5_progress.G5SyntheticMeasurementTests."
              "test_synthetic_abc_differential",
        "reason_prefix": "SKIP real-core G5 synthetic (not attempted): DEV ROM absent",
        "gate": "missing build/debug/bios-dev/dev-only-not-compatible-syscard3.pce",
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


def parse_makefile_direct_units(makefile_text, mode="debug"):
    """Parse the `integration` target into ordered unit descriptors.

    Returns [("script", argv...), ("make", target), ...] with $(PYTHON)
    unchanged and $(MODE) substituted by `mode`. Only the `integration`
    target and the sub-make targets it invokes are read; anything else in
    the Makefile is out of scope.
    """
    lines = makefile_text.splitlines()
    start = next(i for i, l in enumerate(lines) if re.match(r"^integration\s*:", l))
    body = []
    for l in lines[start + 1:]:
        if not l.startswith("\t"):
            break
        body.append(l.strip())
    units = []
    targets = {}
    current = None
    for l in lines:
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*:[^=]*$", l)
        if m:
            current = m.group(1)
            targets[current] = []
        elif current is not None and l.startswith("\t"):
            targets[current].append(l.strip())
    for entry in body:
        m = re.match(r"^\$\(MAKE\)\s+([A-Za-z0-9_.-]+)\s*$", entry)
        if m:
            units.append(("make", m.group(1)))
            continue
        m = re.match(r"^\$\(PYTHON\)\s+(\S+)(.*)$", entry)
        if m:
            script, rest = m.group(1), m.group(2).strip()
            token_re = re.compile(r'"([^"]*)"|(\S+)')
            args = [a or b for a, b in token_re.findall(rest)]
            argv = [sys.executable,
                    script.replace("$(MODE)", mode).replace("$(ROOT)", ROOT)]
            argv.extend(a.replace("$(MODE)", mode).replace("$(ROOT)", ROOT) for a in args)
            units.append(("script", argv))
            continue
        units.append(("unknown", entry))
    return units, targets


def expected_direct_units(mode="debug"):
    """Canonical unit list derived from DIRECT_UNITS + the runner mapping
    (single source of truth for WHAT must run; the Makefile stays the source
    of truth for HOW each unit runs, verified equal below)."""
    out = []
    for u in DIRECT_UNITS:
        if u == "direct:smoke":
            out.append(("script",
                        [sys.executable, "tests/integration/geargrafx_smoke.py",
                         "--rom", f"build/{mode}/smoke-test-not-bios.pce",
                         "--output", f"build/{mode}/geargrafx-evidence.json"]))
            continue
        entry = DIRECT_MAKE_TARGETS.get(u)
        if entry is None or len(entry) != 1:
            raise ValueError(f"runner mapping must map {u} to exactly one command")
        target = entry[0]
        if target.endswith(".py"):
            out.append(("script", [sys.executable, "tests/integration/" + target]))
        else:
            out.append(("make", target))
    return out


def check_makefile_parity(makefile_text=None, mode="debug"):
    """The Makefile `integration` recipe and DIRECT_UNITS must describe the
    same ordered unit set (MEDIUM: single source of truth by verification).
    A Makefile-only addition/removal/reorder/arg-change, or a manifest-only
    entry, is RED. Sub-make bodies are executed verbatim via `make <target>`,
    so only their names are pinned here; direct scripts are pinned with full
    argv (script + args + MODE)."""
    if makefile_text is None:
        with open(os.path.join(ROOT, "Makefile"), encoding="utf-8") as fh:
            makefile_text = fh.read()
    problems = []
    try:
        units, targets = parse_makefile_direct_units(makefile_text, mode=mode)
    except (StopIteration, ValueError) as error:
        return [f"integration target unparsable: {error}"]
    try:
        expected = expected_direct_units(mode=mode)
    except Exception as error:
        return [f"manifest unit mapping broken: {error}"]
    if units != expected:
        problems.append("Makefile integration recipe != manifest DIRECT_UNITS mapping "
                        f"(makefile={units!r} manifest={expected!r})")
    for kind, name in units:
        if kind == "unknown":
            problems.append(f"unrecognized integration recipe line: {name!r}")
        elif kind == "make" and name not in targets:
            problems.append(f"integration sub-make target missing from Makefile: {name}")
    return problems


def check_manifest(live_modules=None):
    """Manifest self-consistency vs live discover AND vs the Makefile
    integration recipe (MEDIUM: single source of truth by verification)."""
    problems = []
    if live_modules is None:
        live_modules = sorted({i.split(".")[0] for i in discover_ids("tests/integration", "test_*.py")})
    listed = manifest_modules()
    if len(listed) != len(set(listed)):
        problems.append("manifest lists duplicate modules")
    seen_mods = {}
    for shard, mods in INTEGRATION_SHARDS.items():
        for mod in mods:
            if mod in seen_mods:
                problems.append(f"manifest module {mod} in two shards "
                                f"({seen_mods[mod]} and {shard}); cross-shard duplicates "
                                "would be unrepresentable")
            seen_mods[mod] = shard
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
    problems.extend(check_makefile_parity())
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
        "env_oss_pce_game_cue_set": bool(os.environ.get("OSS_PCE_GAME_CUE")),
    }


def _write_evidence(evidence_out, evidence):
    parent = os.path.dirname(os.path.abspath(evidence_out))
    os.makedirs(parent, exist_ok=True)
    with open(evidence_out, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=2, sort_keys=True)


def _base_evidence(kind, shard_id):
    return {
        "schema": EVIDENCE_SCHEMA,
        "manifest_version": MANIFEST_VERSION,
        "sha": os.environ.get("GITHUB_SHA", "local"),
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        "kind": kind,
        "shard_id": shard_id,
        "toolchain": _tool_pins(),
        "gate_states": {},
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _require_cc65():
    # Same contract as `make test` / `make integration-test`: a missing cc65
    # is a FAILURE, never a SKIP (see tools/environment.py requires_cc65).
    os.environ["OSS_PCE_REQUIRE_CC65"] = "1"


def run_shard(shard_id, evidence_out):
    _require_cc65()
    # Gate snapshot at START (closest feasible proxy for skip time; see Phase F).
    # A second snapshot at the end must match on every gate an allowlisted
    # skip relies on, or the run is rejected (no silent mid-run changes).
    gates_start = _gate_states()
    if shard_id not in INTEGRATION_SHARDS:
        raise ValueError(f"unknown shard: {shard_id} (expected one of {sorted(INTEGRATION_SHARDS)})")
    modules = INTEGRATION_SHARDS[shard_id]
    # Mirror `unittest discover -s tests/integration`: plain module names need
    # the tests directory itself on sys.path (there is no package __init__).
    idir = os.path.join(ROOT, "tests", "integration")
    sys.path.insert(0, idir)
    try:
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for mod in modules:
            suite.addTests(loader.loadTestsFromName(mod))
    finally:
        try:
            sys.path.remove(idir)
        except ValueError:
            pass
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
        "gate_states": gates_start,
        "gate_states_end": _gate_states(),
    })
    _write_evidence(evidence_out, evidence)
    return collector


def run_host(evidence_out):
    _require_cc65()
    gates_start = _gate_states()
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
        "gate_states": gates_start,
        "gate_states_end": _gate_states(),
    })
    _write_evidence(evidence_out, evidence)
    return collector


def _run_make_unit(unit, mode="debug"):
    """Run one direct-integration unit. Returns (exit_code, duration_s, note)."""
    started = time.monotonic()
    if unit == "direct:smoke":
        cmd = [sys.executable, "tests/integration/geargrafx_smoke.py",
               "--rom", f"build/{mode}/smoke-test-not-bios.pce",
               "--output", os.path.join("build", mode, SMOKE_EVIDENCE_NAME)]
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


def _mirror_smoke_evidence(evidence_out):
    """Copy the smoke evidence next to the direct evidence file (same artifact)."""
    dest_dir = os.path.dirname(os.path.abspath(evidence_out))
    src = os.path.join(ROOT, SMOKE_EVIDENCE_SRC)
    if os.path.isfile(src):
        import shutil
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copyfile(src, os.path.join(dest_dir, SMOKE_EVIDENCE_NAME))
        return True
    return False


def _verified_rom_digest():
    """SHA-256 of the exact ROM the static units verify (MAJOR 1).

    Returns the hex digest, or "" when the file is absent (the aggregator
    REDs a missing digest; absence is never silently accepted)."""
    path = os.path.join(ROOT, STATIC_VERIFIED_ROM)
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


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
        "failures": sum(1 for r in records if r["exit_code"] != 0),
        "errors": 0,
    })
    if kind == "integration-direct" and set(units) == set(planned):
        # Mirror the smoke evidence only for full direct runs; partial
        # (--only) runs cannot claim the handoff file.
        evidence["smoke_evidence_mirrored"] = _mirror_smoke_evidence(evidence_out)
    if kind == "static" and set(units) == set(planned):
        # Record the digest of the exact ROM the static units verified, but
        # only for full runs where every unit passed; partial or failed runs
        # cannot claim a verified ROM (the aggregator REDs them anyway).
        if all(r["exit_code"] == 0 for r in records):
            evidence["verified_rom_sha256"] = _verified_rom_digest()
        else:
            evidence["verified_rom_sha256"] = ""
    _write_evidence(evidence_out, evidence)
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


def _gate_tripped(gate, gate_states):
    if gate.startswith("missing "):
        marker = gate.split(None, 1)[1]
        if marker.endswith("dev-only-not-compatible-syscard3.pce"):
            return not gate_states.get("dev_rom_present", True)
        return False
    if gate.startswith("unset "):
        var = gate.split(None, 1)[1]
        return not gate_states.get("env_" + var.lower().replace("-", "_") + "_set", True)
    return False


def _match_allowed_skip(test_id, reason, gate_states):
    for rule in ALLOWED_SKIPS:
        if not (test_id == rule["id"] or test_id.startswith(rule["id"])):
            continue
        if not reason.startswith(rule["reason_prefix"]):
            continue
        gate = rule.get("gate")
        if gate is None:
            return rule
        if _gate_tripped(gate, gate_states):
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


def _is_nonempty_str(value):
    return isinstance(value, str) and len(value) > 0


def _shape_problems(e):
    """Per-evidence schema/type/value contract (MAJOR 2). Missing, mistyped,
    or empty identity/toolchain fields are RED even before cross-evidence
    comparison, so a uniformly wrong/forged set cannot pass."""
    problems = []
    sid = e.get("shard_id", "?")
    if not isinstance(e, dict):
        return [f"evidence entry is not a mapping: {type(e).__name__}"]
    if e.get("schema") != EVIDENCE_SCHEMA or not isinstance(e.get("schema"), int):
        problems.append(f"shard {sid}: bad schema (want int {EVIDENCE_SCHEMA})")
    if e.get("manifest_version") != MANIFEST_VERSION or \
            not isinstance(e.get("manifest_version"), int):
        problems.append(f"shard {sid}: bad manifest version (want int {MANIFEST_VERSION})")
    if e.get("kind") not in ("integration-unittest", "host-unittest",
                                "integration-direct", "static"):
        problems.append(f"shard {sid}: unknown kind {e.get('kind')!r}")
    for field in ("sha", "run_id", "run_attempt"):
        if not _is_nonempty_str(e.get(field)):
            problems.append(f"shard {sid}: missing or empty {field}")
    tools = e.get("toolchain")
    if not isinstance(tools, dict):
        problems.append(f"shard {sid}: missing toolchain record")
    else:
        for tool in ("cc65", "geargrafx"):
            if not _is_nonempty_str(tools.get(tool)):
                problems.append(f"shard {sid}: missing toolchain pin for {tool}")
    gates = e.get("gate_states")
    if not isinstance(gates, dict):
        problems.append(f"shard {sid}: missing gate_states record")
    kind = e.get("kind")
    if kind in ("integration-unittest", "host-unittest"):
        planned = e.get("planned_ids")
        if not isinstance(planned, list) or not planned or \
                any(not isinstance(i, str) or not i for i in planned):
            problems.append(f"shard {sid}: planned_ids must be a non-empty list of IDs")
        if not isinstance(e.get("executed"), list):
            problems.append(f"shard {sid}: executed must be a list")
        else:
            for r in e.get("executed"):
                if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r.get("id"):
                    problems.append(f"shard {sid}: malformed executed record: {r!r:.120}")
                    break
                if r.get("result") not in ("ok", "skipped", "fail", "error",
                                            "expected-failure", "unexpected-success") \
                        and not str(r.get("id", "")).startswith("setUpClass ("):
                    problems.append(f"shard {sid}: unknown result {r.get('result')!r} "
                                    f"for {r.get('id')}")
    elif kind in ("integration-direct", "static"):
        planned = e.get("planned_units")
        if not isinstance(planned, list) or not planned or \
                any(not isinstance(u, str) or not u for u in planned):
            problems.append(f"shard {sid}: planned_units must be a non-empty list of units")
        if not isinstance(e.get("executed"), list):
            problems.append(f"shard {sid}: executed must be a list")
    if kind == "static":
        # MAJOR 1: static evidence must name the digest of the exact ROM its
        # units verified. A missing or malformed digest cannot be bound to
        # the handoff ROM, so it is RED here before any cross-file check.
        digest = e.get("verified_rom_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            problems.append(f"shard {sid}: missing or malformed verified_rom_sha256 "
                            "(want 64 lowercase hex of the static-verified ROM)")
    if not isinstance(e.get("failures"), int) or not isinstance(e.get("errors"), int):
        problems.append(f"shard {sid}: failures/errors must be ints")
    return problems


def _check_identity(evidence_list, identity):
    """Bind evidence to the expected run identity (MAJOR 2). `identity` maps
    sha/run_id/run_attempt/toolchain pins; missing/empty expected values are
    RED (fail-closed, never silently accepted)."""
    problems = []
    for field in ("sha", "run_id", "run_attempt"):
        want = identity.get(field) if identity else None
        if not _is_nonempty_str(want):
            problems.append(f"identity has no expected {field} (refusing to aggregate blind)")
            continue
        for e in evidence_list:
            got = e.get(field)
            if got != want:
                problems.append(f"shard {e.get('shard_id')}: {field} mismatch "
                                f"(evidence {got!r} vs expected {want!r})")
    pins = (identity or {}).get("toolchain", {})
    for tool in ("cc65", "geargrafx"):
        want = pins.get(tool) if isinstance(pins, dict) else None
        if not _is_nonempty_str(want):
            problems.append(f"identity has no expected toolchain pin for {tool}")
            continue
        for e in evidence_list:
            tools = e.get("toolchain")
            got = tools.get(tool) if isinstance(tools, dict) else None
            if got != want:
                problems.append(f"shard {e.get('shard_id')}: toolchain pin mismatch for {tool} "
                                f"(evidence {got!r} vs expected {want!r}); pins prove consistency "
                                "with the pinned toolchain, not binary authenticity")
    return problems


def _check_smoke_evidence(direct_evidence_path, rom_path, expected_pins):
    """Validate the transferred smoke evidence next to the direct evidence file
    (MAJOR 3): presence, schema, PASS status, scope, pinned core revision, and
    ROM-SHA identity against the handed-off ROM bytes. No re-generation, no
    substitution: a fabricated PASS file fails schema/pin/digest binding."""
    problems = []
    base = os.path.dirname(os.path.abspath(direct_evidence_path))
    smoke_path = os.path.join(base, SMOKE_EVIDENCE_NAME)
    if not os.path.isfile(smoke_path):
        return [f"direct: missing transferred smoke evidence {SMOKE_EVIDENCE_NAME!r} "
                f"next to {direct_evidence_path!r}"]
    try:
        with open(smoke_path, encoding="utf-8") as fh:
            smoke = json.load(fh)
    except (OSError, ValueError) as error:
        return [f"direct: smoke evidence unreadable: {error}"]
    if not isinstance(smoke, dict):
        return ["direct: smoke evidence is not a mapping"]
    if smoke.get("schema_version") != 1 or not isinstance(smoke.get("schema_version"), int):
        problems.append("direct: smoke evidence has bad schema_version (want int 1)")
    if smoke.get("status") != "PASS":
        problems.append(f"direct: smoke evidence status is not PASS: {smoke.get('status')!r}")
    if smoke.get("scope") != "hucard-smoke-only":
        problems.append(f"direct: smoke evidence scope is not hucard-smoke-only: {smoke.get('scope')!r}")
    want_core = (expected_pins or {}).get("geargrafx")
    if not _is_nonempty_str(want_core):
        problems.append("direct: no expected Geargrafx pin to bind the smoke evidence")
    elif smoke.get("geargrafx_revision") != want_core:
        problems.append(f"direct: smoke evidence core mismatch "
                        f"(evidence {smoke.get('geargrafx_revision')!r} vs expected {want_core!r})")
    if rom_path is None:
        problems.append("direct: no handoff ROM given for smoke digest binding")
    else:
        try:
            with open(rom_path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError as error:
            problems.append(f"direct: handoff ROM unreadable: {error}")
            digest = None
        if digest is not None and smoke.get("rom_sha256") != digest:
            problems.append("direct: smoke evidence ROM digest does not match the handed-off ROM "
                            f"(evidence {str(smoke.get('rom_sha256'))[:16]}.. vs file {digest[:16]}..)")
    return problems


def _live_ids_by_module():
    """Independent live discover, grouped by module (MAJOR 1)."""
    return {mod: sorted(ids) for mod, ids in _group_by_module(
        discover_ids("tests/integration", "test_*.py")).items()}


def _group_by_module(ids):
    groups = {}
    for i in ids:
        groups.setdefault(i.split(".")[0], []).append(i)
    return groups


def _default_identity():
    """Expected run identity for aggregation (MAJOR 2).

    In CI this binds evidence to the ambient run (GITHUB_* env) plus the
    actually-checked-out commit and the pinned toolchain file — never to the
    evidence's own claims alone. Locally (no CI env) the SHA falls back to
    the current checkout; an explicit `identity` argument overrides everything
    (used by tests). Missing/empty fields are RED, never defaulted away."""
    import subprocess as _sp
    try:
        head = _sp.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                       text=True, timeout=60)
        checkout_sha = head.stdout.strip() if head.returncode == 0 else ""
    except Exception:
        checkout_sha = ""
    return {
        "sha": os.environ.get("GITHUB_SHA", "") or checkout_sha,
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "toolchain": _tool_pins(),
    }


def _expected_ids_for_shard(shard_id, shards, live_by_module, live_host_ids):
    """Authoritative expected test IDs for one shard: independent live IDs
    partitioned by the manifest mapping (never the shard's own planned list).
    A shard that plans a subset of its expected set — even with
    executed == planned — is RED (MAJOR 1: coordinated omission)."""
    if shard_id == "host":
        return sorted(live_host_ids)
    return sorted(i for mod in shards.get(shard_id, []) for i in live_by_module.get(mod, []))


def _check_static_handoff(evidence_list, static_rom_path):
    """Bind the static-verified ROM to the handoff ROM (MAJOR 1).

    The static shard verifies one exact tree (release-compare, build
    --verify, same-input reproduce, rom_provenance --rom/--manifest); the
    handoff publishes another. The static evidence carries the verified
    digest; it must equal the digest of the handoff ROM file. A divergence
    (stale/partial rebuild, runner-specific bytes, substituted file) is RED.
    The aggregator never re-verifies ROM bytes itself; CI additionally runs
    build --verify and rom_provenance --rom/--manifest on the handoff pair."""
    problems = []
    static = [e for e in evidence_list if e.get("kind") == "static"]
    if len(static) != 1:
        return [f"static handoff binding needs exactly one static evidence "
                f"(found {len(static)})" ]
    want = static[0].get("verified_rom_sha256")
    if not isinstance(want, str) or not re.fullmatch(r"[0-9a-f]{64}", want):
        return ["static handoff binding needs a well-formed verified_rom_sha256 "
                "in the static evidence"]
    if not os.path.isfile(static_rom_path):
        return [f"static handoff ROM missing: {static_rom_path}"]
    with open(static_rom_path, "rb") as fh:
        got = hashlib.sha256(fh.read()).hexdigest()
    if got != want:
        problems.append("static-verified ROM digest does not match the handoff ROM "
                        f"(static {want[:16]}... vs handoff {got[:16]}...); the published "
                        "tree is not the verified tree")
    return problems


def aggregate(evidence_list, live_integration_ids=None, live_host_ids=None,
              shards=None, identity=None, smoke_evidence_path=None, smoke_rom_path=None,
              static_rom_path=None):
    """Fail-closed aggregation. Returns a list of problems (empty == GREEN).

    - `live_integration_ids`: authoritative live ID set as {module: [ids]} (or
      a flat list, grouped internally). Defaults to an independent live
      discover. `live_host_ids`: authoritative live host IDs (defaults to live
      discover). Unclassified live IDs and manifest entries without live IDs
      are RED.
    - `shards`: manifest mapping under test (defaults to INTEGRATION_SHARDS).
      Lets unit tests use a synthetic manifest instead of the real one.
    - `identity`: expected {sha, run_id, run_attempt, toolchain{cc65,geargrafx}}
      (MAJOR 2). Defaults to the ambient environment + checkout + pinned file.
      Missing/empty expected values are RED.
    - `smoke_*`: transferred smoke evidence + handoff ROM (MAJOR 3); when
      given, both are validated (presence, schema, PASS, scope, pinned core,
      ROM digest identity).
    - `static_rom_path`: handoff ROM file (MAJOR 1); when given, its digest
      must equal the static evidence's verified_rom_sha256."""
    problems = []
    if not evidence_list:
        problems.append("no shard evidence provided")
        return problems
    if shards is None:
        shards = INTEGRATION_SHARDS
    for e in evidence_list:
        problems.extend(_shape_problems(e))
    if live_integration_ids is None:
        try:
            live_integration_ids = _live_ids_by_module()
        except Exception as error:
            problems.append(f"live discover failed: {error}")
            live_integration_ids = {}
    live_by_module = live_integration_ids if isinstance(live_integration_ids, dict) \
        else _group_by_module(live_integration_ids)
    if live_host_ids is None:
        try:
            live_host_ids = discover_ids("tests/host", "test_*.py")
        except Exception as error:
            problems.append(f"live host discover failed: {error}")
            live_host_ids = []
    # Identity binding (MAJOR 2).
    if identity is None:
        identity = _default_identity()
        checkout_sha = ""
        try:
            import subprocess as _sp
            head = _sp.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                           text=True, timeout=60)
            checkout_sha = head.stdout.strip() if head.returncode == 0 else ""
        except Exception:
            checkout_sha = ""
        if checkout_sha and identity.get("sha") and checkout_sha != identity["sha"]:
            problems.append("aggregator checkout SHA does not match GITHUB_SHA "
                            f"(checkout {checkout_sha[:12]} vs env {identity['sha'][:12]})")
    problems.extend(_check_identity(evidence_list, identity))
    expected_pins = (identity or {}).get("toolchain") or {}
    expected_shards = {"host", "direct", "static"} | set(shards)
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
        want_kind = {"host": "host-unittest", "direct": "integration-direct",
                     "static": "static"}.get(e.get("shard_id"), "integration-unittest")
        if e.get("kind") != want_kind:
            problems.append(f"shard {e.get('shard_id')}: kind {e.get('kind')!r} does not match shard "
                            f"(want {want_kind!r}; a swapped kind hides the wrong test set)")
        if e.get("failures") or e.get("errors"):
            problems.append(f"shard {e.get('shard_id')}: reports failures={e.get('failures')} "
                            f"errors={e.get('errors')}")
    # Newcomer gate: live modules must equal the manifest modules exactly.
    live_modules = sorted(live_by_module)
    manifest_union = sorted(set(m for mods in shards.values() for m in mods))
    for mod in live_modules:
        if mod not in manifest_union:
            problems.append(f"unclassified test module (add to a shard in the same PR): {mod}")
    for mod in manifest_union:
        if mod not in live_by_module:
            problems.append(f"manifest lists unknown test module: {mod}")
    # Per-shard planned-vs-EXPECTED with allowlisted skips only.
    # Gated skips used below are collected so the gate-stability check can
    # verify their gate did not change mid-run (Phase F).
    gated_shards = set()
    by_id = {e.get("shard_id"): e for e in evidence_list}
    expected_by_shard = {sid: _expected_ids_for_shard(sid, shards, live_by_module, live_host_ids)
                         for sid in expected_shards}
    for sid in sorted(expected_shards):
        e = by_id.get(sid)
        if e is None:
            continue  # already reported as missing above
        kind = e.get("kind")
        if kind in ("integration-direct", "static"):
            planned_manifest = list(DIRECT_UNITS if kind == "integration-direct" else STATIC_UNITS)
            if list(e.get("planned_units", [])) != planned_manifest:
                problems.append(f"shard {sid}: planned units do not match the manifest "
                                f"(tampered or stale plan)")
                continue
            label = "direct" if kind == "integration-direct" else "static"
            executed = [r["unit"] for r in e.get("executed", [])]
            if len(executed) != len(set(executed)):
                problems.append(f"{label}: duplicate executed units")
            for u in executed:
                if u not in planned_manifest:
                    problems.append(f"{label}: unknown unit executed: {u}")
            for u in planned_manifest:
                if u not in executed:
                    problems.append(f"{label}: planned unit missing: {u}")
            for r in e.get("executed", []):
                if r.get("exit_code") != 0:
                    problems.append(f"{label}: unit {r['unit']} exit_code={r['exit_code']}")
            continue
        if kind not in ("integration-unittest", "host-unittest"):
            problems.append(f"shard {sid}: unknown kind {kind!r}")
            continue
        expected = expected_by_shard[sid]
        planned = list(e.get("planned_ids", []))
        if sorted(planned) != sorted(expected) or len(planned) != len(expected):
            problems.append(f"shard {sid}: planned IDs do not match the live manifest mapping "
                            f"(planned {len(planned)}, expected {len(expected)}; tampered plan or "
                            "coordinated omission)")
            continue
        executed_ids = [r["id"] for r in e.get("executed", [])]
        seen_ids = set(executed_ids)
        if len(executed_ids) != len(seen_ids):
            problems.append(f"shard {sid}: duplicate executed test IDs")
        for rid in executed_ids:
            if rid.startswith("setUpClass ("):
                continue  # pseudo class-skip record; handled in the gate check below
            if rid not in expected:
                problems.append(f"shard {sid}: executed ID not in expected set: {rid}")
        results = {r["id"]: r for r in e.get("executed", [])}
        pseudos = _pseudo_class_skip_record(e.get("executed", []))
        pseudo_by_class = {}
        for class_name, reason in pseudos:
            rule = _match_allowed_class_skip(class_name, reason, e.get("gate_states", {}))
            if rule is None:
                problems.append(f"shard {sid}: unlisted class-level SKIP {class_name}: {reason!r}")
            else:
                pseudo_by_class[class_name] = rule
                if rule.get("gate"):
                    gated_shards.add(sid)
        for pid in sorted(set(expected) - seen_ids):
            pid_class = ".".join(pid.split(".")[:2])
            if pid_class in pseudo_by_class:
                continue  # whole class skipped via allowlisted gate; pseudo record is the evidence
            problems.append(f"shard {sid}: expected ID missing from executed: {pid}")
        for rid, r in results.items():
            if rid.startswith("setUpClass ("):
                continue  # pseudo class-skip record; gated above
            if rid not in expected:
                continue  # already reported as not-in-expected above
            if r["result"] in ("fail", "error", "unexpected-success"):
                problems.append(f"shard {sid}: {rid} result={r['result']}")
            elif r["result"] == "skipped":
                rule = _match_allowed_skip(rid, r.get("reason", ""), e.get("gate_states", {}))
                if rule is not None:
                    if rule.get("gate"):
                        gated_shards.add(sid)
                    continue
                problems.append(f"shard {sid}: unlisted SKIP {rid}: {r.get('reason', '')!r}")
            elif r["result"] not in ("ok", "expected-failure"):
                problems.append(f"shard {sid}: {rid} unknown result {r['result']!r}")
    # Host gate: the host shard must plan the full live host set (minus
    # allowlisted conditional skips, verified per-record above). A host runner
    # may not silently narrow its own planned set.
    for e in evidence_list:
        if e.get("shard_id") == "host" and e.get("kind") == "host-unittest":
            for hid in live_host_ids:
                if hid not in e.get("planned_ids", []):
                    problems.append(f"host shard omits live test ID: {hid}")
            for pid in e.get("planned_ids", []):
                if pid not in live_host_ids:
                    problems.append(f"host shard plans unknown test ID: {pid}")
    # Gate-stability gate (Phase F): a gated skip is only valid if its gate
    # did not change between run start and run end. Otherwise the skip-time
    # state is unprovable and the evidence is rejected (fail-closed; re-run
    # with a pinned environment instead of silently accepting).
    for sid in sorted(gated_shards):
        e = by_id.get(sid, {})
        if e.get("gate_states", {}) != e.get("gate_states_end", {}):
            problems.append(f"shard {sid}: gate changed during run; gated skip "
                            "timing unverifiable (record start/end gate states and re-run)")
    # Union gate: every live ID executed-or-allowlisted-skipped somewhere,
    # nothing else. Cross-shard executed duplicates are RED (misrouting or
    # double-run); unexecuted leftovers without an allowlisted gate are RED.
    seen_union = {}
    for e in evidence_list:
        kind = e.get("kind")
        if kind in ("integration-unittest", "host-unittest"):
            for r in e.get("executed", []):
                rid = r.get("id")
                if isinstance(rid, str) and not rid.startswith("setUpClass ("):
                    seen_union.setdefault(rid, []).append(e.get("shard_id"))
    for rid, sids in sorted(seen_union.items()):
        if len(sids) > 1:
            problems.append(f"executed test ID in multiple shards {sids}: {rid}")
    live_union = set()
    for ids in live_by_module.values():
        live_union.update(ids)
    live_union.update(live_host_ids)
    # Smoke handoff binding (MAJOR 3): when paths are given, validate the
    # transferred smoke evidence and its digest identity with the handed-off
    # ROM. The aggregator never re-generates evidence.
    if smoke_evidence_path is not None or smoke_rom_path is not None:
        if not (smoke_evidence_path and smoke_rom_path):
            problems.append("smoke binding needs both --smoke-evidence and --smoke-rom")
        else:
            problems.extend(_check_smoke_evidence(smoke_evidence_path, smoke_rom_path,
                                                  expected_pins))
    # Static handoff binding (MAJOR 1): the published tree must be the
    # verified tree. Enforced only when the handoff ROM path is given; CI
    # always gives it (pinned by the workflow contract test).
    if static_rom_path is not None:
        problems.extend(_check_static_handoff(evidence_list, static_rom_path))
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
    p.add_argument("--smoke-evidence", default=None)
    p.add_argument("--smoke-rom", default=None)
    p.add_argument("--static-rom", default=None)
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
        problems = aggregate(evidences, smoke_evidence_path=args.smoke_evidence,
                             smoke_rom_path=args.smoke_rom,
                             static_rom_path=args.static_rom)
        for problem in problems:
            print(f"AGGREGATE-REJECTED {problem}")
        print("AGGREGATE-GREEN all shards reconciled" if not problems else
              f"AGGREGATE-RED {len(problems)} problem(s)")
        return 1 if problems else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
