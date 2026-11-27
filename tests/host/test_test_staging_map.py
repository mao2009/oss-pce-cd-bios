"""Validation tests for the Phase 1 staging map v0 (Issue #850, Phase 1).

The machine-readable map lives at tools/test_staging_map.json and is
documented in docs/test-staging-map-v0-850.md. These tests pin the map
schema, reference integrity, duplicate-freedom, fail-closed coverage
(every tracked file classifies; unknown classifies L3) and full 81-slot
coverage. They also pin the safety invariants the map must not change
(6 Required Checks, JSR 0/81, VERIFIED 0/81, release_eligible=false,
docs-only fast path disabled).

Local commercial media (./rom/) is never accessed: the media test below
SKIPs whether rom/ is absent or present (SKIP, never PASS).
"""
import fnmatch
import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = ROOT / "tools" / "test_staging_map.json"

sys.path.insert(0, str(ROOT / "tools"))

LEVELS = {"L0", "L1", "L2", "L3", "L4"}
CI_CLASSES = {"required-ci", "ci-nonrequired", "ci-outside-optin", "push-only"}
CATEGORIES = {
    "dev-api-impl", "host", "dev-diagnostic", "real-core",
    "direct-integration", "static", "mutation", "safety-gate",
    "shared-code", "build-infra", "ci-workflow", "docs",
    "fixture", "selection-logic",
}

REQUIRED_CHECKS = {
    "bios-dev", "verify", "guard-bios-dev",
    "guard-verify", "guard-core-harness", "guard-bcdbin-core",
}

DEV_OVERRIDE_SLOTS = {
    0x1E, 0x21, 0x22, 0x23, 0x2E, 0x3A, 0x3C, 0x3D, 0x3E,
    0x3F, 0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x4F,
}

SKIP_DIRS = {".git", "build", "intermediate", "__pycache__"}
SKIP_PREFIXES = (".intake", ".trusted-main")


def load_map():
    assert MAP_PATH.is_file(), (
        "staging map missing: tools/test_staging_map.json (Phase 1 v0 artifact)")
    with open(MAP_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def makefile_targets():
    targets = set()
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*:[^=]*$", line)
        if m:
            targets.add(m.group(1))
    return targets


def repo_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS
                       and not d.startswith(SKIP_PREFIXES)
                       and not d.startswith(".intake")
                       and d != ".trusted-main"]
        for fn in filenames:
            if fn.endswith((".pyc", ".pyo")):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
            out.append(rel)
    return sorted(out)


def entry_matches(entry, rel):
    kind = entry["match_type"]
    pat = entry["match"]
    if kind == "exact":
        return rel == pat
    if kind == "exact-list":
        return rel in pat
    if kind == "glob":
        return fnmatch.fnmatch(rel, pat)
    raise AssertionError(f"unknown match_type: {kind}")


def classify(entries, rel):
    for entry in entries:
        if entry_matches(entry, rel):
            return entry
    return None


class MapSchemaTest(unittest.TestCase):
    def test_map_loads_with_top_level_keys(self):
        m = load_map()
        for key in ("schema_version", "entries", "api_slots",
                    "make_transitive", "static_units_defined",
                    "required_checks", "safety_invariants", "fallback"):
            self.assertIn(key, m, f"missing top-level key: {key}")
        self.assertEqual(m["schema_version"], 1)

    def test_entry_schema(self):
        m = load_map()
        seen = set()
        for e in m["entries"]:
            for key in ("id", "match_type", "match", "level", "category",
                        "ci_class", "required_ci", "tests", "reason"):
                self.assertIn(key, e, f"entry {e.get('id')}: missing {key}")
            self.assertNotIn(e["id"], seen, f"duplicate entry id: {e['id']}")
            seen.add(e["id"])
            self.assertIn(e["match_type"], ("exact", "exact-list", "glob"))
            self.assertIn(e["level"], LEVELS, f"entry {e['id']}: bad level")
            self.assertIn(e["category"], CATEGORIES, f"entry {e['id']}: bad category")
            self.assertIn(e["ci_class"], CI_CLASSES, f"entry {e['id']}: bad ci_class")
            self.assertIsInstance(e["required_ci"], bool)
            self.assertTrue(e["reason"].strip(), f"entry {e['id']}: empty reason")
            t = e["tests"]
            for key in ("host", "integration_scripts", "dev_targets", "static_units"):
                self.assertIn(key, t, f"entry {e['id']}: tests missing {key}")
                self.assertEqual(len(t[key]), len(set(t[key])),
                                 f"entry {e['id']}: duplicate in tests.{key}")
            for u in t["static_units"]:
                self.assertIn(u, m["static_units_defined"],
                              f"entry {e['id']}: unknown static unit {u}")

    def test_fallback_is_last_and_fail_closed(self):
        m = load_map()
        fb = m["fallback"]
        self.assertEqual(fb["match"], "**")
        self.assertEqual(fb["level"], "L3")
        self.assertEqual(m["entries"][-1]["id"], fb["id"])


class MapRefsTest(unittest.TestCase):
    def test_exact_paths_exist(self):
        m = load_map()
        for e in m["entries"]:
            if e["match_type"] == "exact":
                self.assertTrue((ROOT / e["match"]).exists(),
                                f"entry {e['id']}: missing path {e['match']}")
            elif e["match_type"] == "exact-list":
                self.assertTrue(len(e["match"]) > 0,
                                f"entry {e['id']}: empty exact-list")
                self.assertEqual(len(e["match"]), len(set(e["match"])),
                                  f"entry {e['id']}: dup in exact-list")
                for p in e["match"]:
                    self.assertTrue((ROOT / p).is_file(),
                                    f"entry {e['id']}: missing path {p}")

    def test_globs_match_at_least_one_file(self):
        m = load_map()
        files = repo_files()
        for e in m["entries"]:
            if e["match_type"] == "glob" and e["match"] != "**":
                hits = [f for f in files if fnmatch.fnmatch(f, e["match"])]
                self.assertTrue(hits, f"entry {e['id']}: glob matches nothing: {e['match']}")

    def test_host_refs_exist(self):
        m = load_map()
        host_files = {f for f in repo_files() if f.startswith("tests/host/")}
        for e in m["entries"]:
            for h in e["tests"]["host"]:
                if h.startswith("tests.host."):
                    mod = h[len("tests.host."):]
                    self.assertIn(f"tests/host/{mod}.py", host_files,
                                  f"entry {e['id']}: unknown host module {h}")
                elif "*" in h:
                    self.assertIn("/", h,
                                  f"entry {e['id']}: host patterns use slash form: {h}")
                    hits = [f for f in host_files if fnmatch.fnmatch(f, h)]
                    self.assertTrue(hits, f"entry {e['id']}: host pattern matches nothing: {h}")
                elif h.startswith("tests/host/") and h.endswith(".py"):
                    self.assertIn(h, host_files,
                                  f"entry {e['id']}: unknown host file {h}")
                else:
                    self.fail(f"entry {e['id']}: bad host ref format: {h}")

    def test_integration_scripts_exist(self):
        m = load_map()
        for e in m["entries"]:
            for s in e["tests"]["integration_scripts"]:
                self.assertTrue((ROOT / s).is_file(),
                                f"entry {e['id']}: missing integration script {s}")

    def test_dev_targets_exist_in_makefile(self):
        m = load_map()
        targets = makefile_targets()
        for e in m["entries"]:
            for t in e["tests"]["dev_targets"]:
                self.assertIn(t, targets, f"entry {e['id']}: unknown Makefile target {t}")

    def test_dev_targets_have_transitive_rows(self):
        m = load_map()
        trans = m["make_transitive"]
        targets = makefile_targets()
        for e in m["entries"]:
            for t in e["tests"]["dev_targets"]:
                self.assertIn(t, trans, f"entry {e['id']}: target {t} lacks transitive row")
        for name in trans:
            self.assertIn(name, targets, f"transitive row for unknown Makefile target: {name}")


class MapCoverageTest(unittest.TestCase):
    def test_no_unclassified_leak(self):
        m = load_map()
        entries = m["entries"]
        missing = [f for f in repo_files() if classify(entries, f) is None]
        self.assertEqual(missing, [], f"unclassified files (fail-closed leak): {missing[:20]}")

    def test_every_file_first_match_is_deterministic(self):
        m = load_map()
        for f in repo_files():
            hits = [e["id"] for e in m["entries"] if entry_matches(e, f)]
            self.assertTrue(hits, f"no match (should be caught by fallback): {f}")

    def test_new_artifacts_are_covered(self):
        m = load_map()
        for f in ("tools/test_staging_map.json",
                  "docs/test-staging-map-v0-850.md",
                  "tests/host/test_test_staging_map.py"):
            e = classify(m["entries"], f)
            self.assertIsNotNone(e, f"new artifact uncovered: {f}")
            self.assertEqual(e["level"], "L3", f"new artifact {f} must be L3 (selection logic)")

    def test_81_api_slots_covered(self):
        m = load_map()
        slots = m["api_slots"]
        self.assertEqual(len(slots), 81)
        ids = [s["slot"] for s in slots]
        self.assertEqual(sorted(ids), list(range(81)))
        spec = json.loads((ROOT / "spec" / "api_slots.json").read_text(encoding="utf-8"))
        names = {s["id"]: s["candidate_name"] for s in spec["slots"]}
        dev = set()
        for s in slots:
            self.assertEqual(s["name"], names[s["slot"]],
                             f"slot {s['slot']}: name mismatch vs spec/api_slots.json")
            self.assertIn(s["level"], LEVELS)
            if s["dev_implemented"]:
                dev.add(s["slot"])
                self.assertIsNotNone(s["dev_source"], f"slot {s['slot']}: no dev_source")
                self.assertTrue((ROOT / s["dev_source"]).is_file(),
                                f"slot {s['slot']}: missing dev_source {s['dev_source']}")
        self.assertEqual(dev, DEV_OVERRIDE_SLOTS,
                         f"DEV slot set must be exactly the 18 overrides: extra={dev - DEV_OVERRIDE_SLOTS} "
                         f"missing={DEV_OVERRIDE_SLOTS - dev}")


class MapSafetyTest(unittest.TestCase):
    def test_required_checks_pinned(self):
        m = load_map()
        self.assertEqual(set(m["required_checks"]), REQUIRED_CHECKS)
        inv = m["safety_invariants"]
        self.assertEqual(inv["required_check_count"], 6)
        self.assertFalse(inv["docs_only_fast_path_enabled"])
        self.assertFalse(inv["release_eligible"])
        self.assertEqual(inv["jsr_allowed"], "0/81")
        self.assertEqual(inv["abi_verified"], "0/81")

    def test_spec_ledgers_unchanged(self):
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        self.assertEqual(len(contracts["apis"]), 81)
        for a in contracts["apis"]:
            self.assertEqual(a.get("implementation_status"), "NOT_STARTED",
                             f"slot {a.get('slot')}: implementation changed")
            self.assertEqual(a.get("verification", {}).get("status"), "NOT_RUN",
                             f"slot {a.get('slot')}: verification changed")
        impl = json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))
        self.assertFalse(impl["release_eligible"])
        for s in impl["slots"]:
            self.assertEqual(s.get("state"), "NOT_READY",
                             f"slot {s.get('id')}: DEV state changed")

    def test_jsr_gate_allows_nothing(self):
        import reference_jsr_gate as gate
        ledger = json.loads((ROOT / "spec" / "reference_jsr_gate.json").read_text(encoding="utf-8"))
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        for slot in (0x00, 0x1E, 0x3F, 0x4F, 0x50):
            rec = gate.resolve(slot, ledger, contracts)
            self.assertNotEqual(rec["execution"], "ALLOWED",
                                f"slot {slot:02X}: gate must not allow")
            self.assertFalse(rec["execution_permitted"])
            self.assertEqual(rec["jsr_count"], 0)
            self.assertFalse(rec["release_eligible"])

    def test_no_rom_access(self):
        rom = ROOT / "rom"
        if rom.exists():
            self.skipTest("SKIP: ./rom present; commercial media is never read by map tests")
        self.skipTest("SKIP: ./rom absent; no real-media test attempted (synthetic fixtures only)")


if __name__ == "__main__":
    unittest.main()
