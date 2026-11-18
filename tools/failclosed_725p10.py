#!/usr/bin/env python3
"""Fail-closed P10 verification for Issue #725 (supplement to tools/failclosed_gate.py).

Verifies that #473 validation progress never promotes unvalidated work to a
release decision. This module adds only the vectors that the base gate does
not already assert explicitly, and never duplicates an existing check:

* P10-A (task item 4): DEV_EXPERIMENTAL success exists (DEV overrides present
  with PROJECT_SPEC_ONLY + host/real-core evidence) yet genuine ABI stays
  0/81, JSR stays 0/81, release stays refused. The base gate checks the zeros;
  this check additionally asserts the DEV-success precondition and the
  cross-ledger independence (DEV count > 0 while VERIFIED == 0).
* P10-B (task items 5,6,7): synthetic PASS, host PASS and docs-only merges
  never promote. Proven by runtime input-closure audit (every content-read
  and existence-check performed by the verdict loads is recorded at call
  time, catching direct, dynamically built and helper-indirected paths on
  every occurrence) plus an extended marker refusal list and a
  synthetic-evidence approval that still yields no execution.
* P10-C (task item 8): unverified conditions stay UNKNOWN/BLOCKED. Asserts
  the ledgers the base gate does not validate: hardware observations validate
  (no OBSERVED), c3 production allowlist stays UNDECIDED/DESIGN_ONLY, and
  api_contracts mapping/implementation/provenance/confidence stay at their
  unpromoted vocabularies. Accepts optional injected ledger data so tests can
  drive the production predicate with mutants (default loads the specs).

Host-only. Reads no BIOS, no ROM, no emulator, no private ./rom/ input.
Pure stdlib plus the committed ledger validators. Exit 0 means every P10
invariant held; exit 1 means at least one failed. Any crash is a failure,
never a pass. This tool never lifts a safety gate: it has no unlock input,
no write path to any ledger, and no ALLOWED return path.
"""
from __future__ import annotations

import argparse
import builtins
import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from abi1_reference_review import load as abi1_load  # noqa: E402
from dev_implementations import (  # noqa: E402
    load_ledger as dev_load_ledger,
    load_slots as dev_load_slots,
    summary as dev_summary,
)
from reference_jsr_gate import (  # noqa: E402
    HAZARDS,
    is_execution_permitted,
    load as jsr_load,
    resolve as jsr_resolve,
)

import build_rom  # noqa: E402
from build_rom import ReleaseBlocked  # noqa: E402
import hw_observation_ledger  # noqa: E402
# Pre-imported so the audit window records verdict data reads only: these
# modules are otherwise lazily imported inside the verdict path, and the
# import machinery itself stats tools/ on first load.
import release_makefile_guard  # noqa: E402,F401
from failclosed_gate import run_all as base_run_all  # noqa: E402

# Gate input closure (runtime-audited, see check_synthetic_host_docs_non_promotion).
# The safety verdict's data reads must stay within the allowlisted inputs;
# docs/, build/ outputs, rom/ inputs and test-result artefacts must never be
# read as verdict data. Spec JSON ledgers, the Makefile and src/ override
# listings are the full data set. tools/*.py sources are code (filtered from
# the audit as .py), not data.
ALLOWLISTED_INPUT_PREFIXES = ("spec/", "Makefile", "src/")
# Provenance-pointer existence checks that fail closed on absence. The
# hardware ledger records its source reference as a docs/ path and the
# validator stats it for existence; absence fails closed (never promotes).
ALLOWLISTED_STAT_EXCEPTIONS = ("docs/cd-reset-contract.md",)
FORBIDDEN_INPUT_PATTERNS = ("docs/", "build/", "./rom/", "rom/", ".log",
                            "test-results", "test_results", "junit")
# Exact path components that mark a forbidden input. Component-exact (not
# substring) so tools/build_rom.py (component "build_rom.py") is not
# flagged while "build/" outputs, "docs/" files and "rom/" inputs are.
FORBIDDEN_PATH_COMPONENTS = ("docs", "build", "rom", "test-results",
                             "test_results", "junit")


def _fail(message: str) -> str:
    return message


def check_dev_success_without_promotion() -> list[str]:
    """DEV_EXPERIMENTAL success must not become genuine ABI (task item 4)."""
    problems: list[str] = []
    try:
        ledger = dev_load_ledger()
        slots = dev_load_slots()
    except Exception as exc:  # noqa: BLE001 -- any load failure fails closed
        return [f"dev ledger unreadable ({type(exc).__name__}: {exc})"]
    try:
        chosen = set(build_rom.overrides())
    except Exception as exc:  # noqa: BLE001
        return [f"dev overrides unreadable ({type(exc).__name__}: {exc})"]
    # Precondition: DEV success exists on the committed tree (overrides
    # present with DEV evidence). Without this the non-promotion claim would
    # be vacuous. The exact count is informational; the invariant is that
    # whatever is present never counts as genuine ABI.
    if not chosen:
        problems.append("DEV success precondition: no DEV overrides present")
    try:
        summ = dev_summary(ledger, chosen)
    except Exception as exc:  # noqa: BLE001
        return problems + [f"dev summary failed ({type(exc).__name__}: {exc})"]
    if summ.get("abi_verified") != 0:
        problems.append(f"DEV ledger: abi_verified must stay 0 (got {summ.get('abi_verified')})")
    if summ.get("dev_callable_approved") != 0:
        problems.append("DEV ledger: DEV_CALLABLE must stay 0 despite DEV implementations")
    if summ.get("owner_approved") != 0:
        problems.append("DEV ledger: owner approval must stay 0")
    if summ.get("release_eligible") is not False:
        problems.append("DEV ledger: release_eligible must stay false")
    # Every override-present slot must still be NOT_READY (never DEV_CALLABLE)
    # with a non-VERIFIED DEV contract (PROJECT_SPEC_ONLY or UNKNOWN).
    by_id = {e.get("id"): e for e in ledger.get("slots", []) if isinstance(e, dict)}
    for slot in sorted(chosen):
        entry = by_id.get(slot)
        if entry is None:
            problems.append(f"dev slot {slot:02X}: override present but no ledger row")
            continue
        if entry.get("state") == "DEV_CALLABLE":
            problems.append(f"dev slot {slot:02X}: DEV implementation must not be DEV_CALLABLE")
        abi = (entry.get("abi_contract") or {}).get("status") if isinstance(entry.get("abi_contract"), dict) else None
        if abi == "VERIFIED":
            problems.append(f"dev slot {slot:02X}: DEV_EXPERIMENTAL must never be VERIFIED")
        if abi not in ("PROJECT_SPEC_ONLY", "UNKNOWN"):
            problems.append(f"dev slot {slot:02X}: DEV contract status {abi!r} is unexpected")
    # Cross-ledger independence: DEV count and genuine ABI count are separate
    # axes. The committed tree has DEV implementations yet 0 genuine VERIFIED.
    try:
        document, _, _ = abi1_load()
    except Exception as exc:  # noqa: BLE001
        return problems + [f"abi1 ledger invalid ({type(exc).__name__})"]
    abi_summary = document.get("summary", {})
    dev_implemented = abi_summary.get("dev_rom_implemented_apis")
    if not isinstance(dev_implemented, int) or dev_implemented <= 0:
        problems.append("abi1 summary: dev_rom_implemented_apis must be > 0 (DEV success exists)")
    if abi_summary.get("reference_abi_verified") != 0:
        problems.append("abi1 summary: reference_abi_verified must stay 0 despite DEV success")
    if document.get("release_eligible") is not False:
        problems.append("abi1 review: release_eligible must stay false")
    # JSR stays closed despite DEV success; release still refuses even with
    # every slot overridden (override presence never proves implementation).
    try:
        jsr_ledger, contracts = jsr_load()
    except Exception as exc:  # noqa: BLE001
        return problems + [f"reference_jsr_gate ledger invalid ({type(exc).__name__})"]
    for slot in range(len(contracts.get("apis", []))):
        try:
            record = jsr_resolve(slot, jsr_ledger, contracts)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"jsr slot {slot:02X}: resolve raised {type(exc).__name__}")
            continue
        if is_execution_permitted(record.get("execution")):
            problems.append(f"jsr slot {slot:02X}: execution permitted despite DEV success (refused)")
        if record.get("jsr_count") != 0:
            problems.append(f"jsr slot {slot:02X}: jsr_count must stay 0")
    try:
        build_rom.release_gate(set(range(81)))
    except ReleaseBlocked as exc:
        if not str(exc).startswith("BLOCKED:"):
            problems.append("release_gate(hypothetical-full): refusal must start with 'BLOCKED:'")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"release_gate(hypothetical-full): wrong exception {type(exc).__name__}")
    else:
        problems.append("release_gate(hypothetical-full): gate passed (FORBIDDEN)")
    return problems


def _is_forbidden_recorded_path(path_str: str) -> str | None:
    """Return the forbidden component if a recorded read touches one, else None.

    Component-exact matching (not substring): tools/build_rom.py is code, not
    a build/ output. Both repo-relative reads (docs/x) and temp-dummy reads
    (/tmp/.../docs/mutant.txt) are flagged by component, so dynamically built
    paths and helper-indirected reads are caught regardless of how the string
    was assembled. .log files are flagged by suffix.
    """
    normalized = path_str.replace("\\", "/")
    components = [c for c in normalized.split("/") if c]
    for component in components:
        if component in FORBIDDEN_PATH_COMPONENTS:
            return component
    if normalized.lower().endswith(".log"):
        return ".log"
    return None


def _is_code_path(path_str: str) -> bool:
    """Code loads (.py/.pyc/__pycache__) are not verdict data; ignore them."""
    normalized = path_str.replace("\\", "/")
    if "__pycache__" in normalized.split("/"):
        return True
    return normalized.endswith(".py") or normalized.endswith(".pyc")


def _repo_relative(path_str: str) -> str | None:
    """Repo-relative posix path if the read is under ROOT, else None."""
    try:
        candidate = Path(path_str)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        resolved = candidate.resolve()
        root_resolved = ROOT.resolve()
        if resolved == root_resolved:
            return "."
        try:
            return resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            return None
    except (OSError, ValueError):
        return None


def _audit_verdict_reads(loader) -> list[tuple[str, str]]:
    """Record content-read and existence-check paths during loader().

    Patches the common read entry points for the audit window only and
    restores them afterwards. Catches direct reads, dynamically assembled
    paths and helper-indirected reads alike, on every occurrence (not just
    the first), because detection happens at call time, not by text search.
    Only reads performed by loader() are recorded: imports are already done
    before the window, so test-runner/import/stdlib traffic is excluded.
    """
    records: list[tuple[str, str]] = []

    orig_read_text = Path.read_text
    orig_read_bytes = Path.read_bytes
    orig_path_open = Path.open
    orig_builtin_open = builtins.open
    orig_io_open = io.open
    orig_os_open = os.open
    orig_os_stat = os.stat
    orig_os_lstat = os.lstat
    orig_exists = Path.exists
    orig_is_file = Path.is_file
    orig_stat = Path.stat

    def _record(kind: str, value) -> None:
        if isinstance(value, (str, os.PathLike)):
            try:
                records.append((kind, os.fspath(value)))
            except (TypeError, ValueError):
                pass

    def _read_text(self, *args, **kwargs):
        _record("read", str(self))
        return orig_read_text(self, *args, **kwargs)

    def _read_bytes(self, *args, **kwargs):
        _record("read", str(self))
        return orig_read_bytes(self, *args, **kwargs)

    def _path_open(self, *args, **kwargs):
        _record("read", str(self))
        return orig_path_open(self, *args, **kwargs)

    def _builtin_open(file, *args, **kwargs):
        _record("read", file)
        return orig_builtin_open(file, *args, **kwargs)

    def _io_open(file, *args, **kwargs):
        _record("read", file)
        return orig_io_open(file, *args, **kwargs)

    def _os_open(path, *args, **kwargs):
        _record("read", path)
        return orig_os_open(path, *args, **kwargs)

    def _os_stat(path, *args, **kwargs):
        _record("stat", path)
        return orig_os_stat(path, *args, **kwargs)

    def _os_lstat(path, *args, **kwargs):
        _record("stat", path)
        return orig_os_lstat(path, *args, **kwargs)

    def _exists(self, *args, **kwargs):
        _record("stat", str(self))
        return orig_exists(self, *args, **kwargs)

    def _is_file(self, *args, **kwargs):
        _record("stat", str(self))
        return orig_is_file(self, *args, **kwargs)

    def _stat(self, *args, **kwargs):
        _record("stat", str(self))
        return orig_stat(self, *args, **kwargs)

    Path.read_text = _read_text  # type: ignore[method-assign]
    Path.read_bytes = _read_bytes  # type: ignore[method-assign]
    Path.open = _path_open  # type: ignore[method-assign]
    builtins.open = _builtin_open  # type: ignore[assignment]
    io.open = _io_open  # type: ignore[assignment]
    os.open = _os_open  # type: ignore[assignment]
    os.stat = _os_stat  # type: ignore[assignment]
    os.lstat = _os_lstat  # type: ignore[assignment]
    Path.exists = _exists  # type: ignore[method-assign]
    Path.is_file = _is_file  # type: ignore[method-assign]
    Path.stat = _stat  # type: ignore[method-assign]
    try:
        loader()
    finally:
        Path.read_text = orig_read_text  # type: ignore[method-assign]
        Path.read_bytes = orig_read_bytes  # type: ignore[method-assign]
        Path.open = orig_path_open  # type: ignore[method-assign]
        builtins.open = orig_builtin_open  # type: ignore[assignment]
        io.open = orig_io_open  # type: ignore[assignment]
        os.open = orig_os_open  # type: ignore[assignment]
        os.stat = orig_os_stat  # type: ignore[assignment]
        os.lstat = orig_os_lstat  # type: ignore[assignment]
        Path.exists = orig_exists  # type: ignore[method-assign]
        Path.is_file = orig_is_file  # type: ignore[method-assign]
        Path.stat = orig_stat  # type: ignore[method-assign]
    return records


def _classify_verdict_reads(records: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """Split audit records into (forbidden, non-allowlisted) hits.

    Code loads (.py) are filtered. Temp scaffolding outside ROOT is allowed
    unless it carries a forbidden component (mutant dummies live there).
    Repo-relative data reads must be within ALLOWLISTED_INPUT_PREFIXES;
    stat hits on ALLOWLISTED_STAT_EXCEPTIONS (docs provenance pointers that
    fail closed on absence) are explicitly excused.
    """
    forbidden: list[str] = []
    non_allowlisted: list[str] = []
    for kind, path_str in records:
        if _is_code_path(path_str):
            continue
        hit = _is_forbidden_recorded_path(path_str)
        repo_rel = _repo_relative(path_str)
        if hit is not None:
            # Excuse the known fail-closed provenance-pointer stat and the
            # parent-directory stats that resolving it implies (pathlib
            # resolve stats ancestors via os.stat); a content read of any
            # docs/ path would still be forbidden, since the validator only
            # stats existence (absence fails closed, never promotes).
            if kind == "stat" and repo_rel in ALLOWLISTED_STAT_EXCEPTIONS:
                continue
            if kind == "stat" and repo_rel is not None and any(
                exc == repo_rel or exc.startswith(repo_rel + "/")
                for exc in ALLOWLISTED_STAT_EXCEPTIONS
            ):
                continue
            forbidden.append(f"{kind} forbidden input {hit!r} via {repo_rel or path_str}")
            continue
        if repo_rel is None:
            continue  # outside ROOT without forbidden components: scaffolding
        if repo_rel == ".":
            # The repo root itself carries no verdict data: validators resolve
            # paths through ROOT (pathlib resolve stats it) and its existence
            # is trivially true. A content read of a directory would fail
            # anyway, so only stat is excused here.
            if kind == "stat":
                continue
            non_allowlisted.append(f"{kind} repo-root read (refused)")
            continue
        if not (repo_rel == "Makefile" or repo_rel.startswith(ALLOWLISTED_INPUT_PREFIXES)):
            non_allowlisted.append(f"{kind} non-allowlisted input {repo_rel!r}")
    return forbidden, non_allowlisted


def _run_verdict_loads_for_audit() -> None:
    """Execute every verdict-critical data load the gates depend on.

    Covers the base gate and both P10 sibling checks so the audited set is
    the actual verdict inputs, not a copy. Read-only; never writes ledgers.
    Imports stay outside the audit window (see top-level pre-imports) so
    import-machinery directory stats are never recorded as verdict reads.
    """
    base_run_all()
    check_dev_success_without_promotion()
    check_unverified_persistence()
    # The synthetic-approval branch of this check also loads the JSR ledger.
    ledger, contracts = jsr_load()
    jsr_resolve(1, ledger, contracts)


def check_synthetic_host_docs_non_promotion() -> list[str]:
    """Synthetic PASS, host PASS and docs-only merges never promote (items 5,6,7).

    Input closure is proven by runtime audit: every content-read and
    existence-check performed by the verdict loads is recorded at call time
    (catching direct, dynamically built and helper-indirected paths on every
    occurrence) and must contain no forbidden docs//build//rom//test-result
    input while every repo-relative data read stays allowlisted.
    Scope: .py code loads are filtered (code, not data); one fail-closed
    docs provenance-pointer stat (ALLOWLISTED_STAT_EXCEPTIONS) is excused
    because absence fails closed and content is never read as verdict data.
    """
    problems: list[str] = []
    # Runtime input closure over the actual verdict loads.
    try:
        records = _audit_verdict_reads(_run_verdict_loads_for_audit)
    except Exception as exc:  # noqa: BLE001 -- any audit failure fails closed
        return [f"input-closure audit crashed ({type(exc).__name__}: {exc})"]
    forbidden, non_allowlisted = _classify_verdict_reads(records)
    for item in forbidden:
        problems.append(f"input closure: {item}")
    for item in non_allowlisted:
        problems.append(f"input closure: {item}")
    # Extended marker refusal: no look-alike PASS string ever permits.
    for candidate in (None, "", "PASS", "SYNTHETIC PASS", "synthetic pass",
                      "host PASS", "HOST PASS", "doc-only merge", "DOC_ONLY",
                      "docs-only", "CI PASS", "TEST PASS", "GREEN",
                      "MATERIALS_COMPLETE", "allowed", " ALLOWED ",
                      "PROJECT_SPEC_ONLY", "STATIC_OK", 0, False, b"ALLOWED"):
        if is_execution_permitted(candidate):
            problems.append(f"marker {candidate!r} must never permit execution")
    if not is_execution_permitted("ALLOWED"):
        problems.append('is_execution_permitted("ALLOWED") definition drifted')
    # A synthetic-evidence approval still grants no execution.
    try:
        ledger, contracts = jsr_load()
    except Exception as exc:  # noqa: BLE001
        return problems + [f"promotion negatives: ledger unreadable ({type(exc).__name__})"]
    synthetic_approval = {
        "reviewer": "synthetic-p10-negative",
        "pr": 725,
        "abi_status": "REVIEWED",
        "hazard_controls": {hazard: "CONTROLLED" for hazard in HAZARDS},
        "callee_run_evidence": ["synthetic-host-only-not-a-run", "SYNTHETIC PASS"],
    }
    try:
        record = jsr_resolve(1, ledger, contracts, approval=synthetic_approval)
    except Exception as exc:  # noqa: BLE001
        return problems + [f"synthetic approval: resolve raised {type(exc).__name__}"]
    if is_execution_permitted(record.get("execution")):
        problems.append("synthetic approval must never yield ALLOWED")
    if record.get("execution") not in ("BLOCKED", "MATERIALS_COMPLETE"):
        problems.append(f"synthetic approval: unexpected execution {record.get('execution')!r}")
    if record.get("execution_permitted") is not False or record.get("jsr_count") != 0:
        problems.append("synthetic approval: permission/count must stay False/0")
    return problems


def check_unverified_persistence(hardware_raw=None, c3_raw=None, contracts_raw=None) -> list[str]:
    """Unverified conditions stay UNKNOWN/BLOCKED (task item 8).

    Optional injected ledger data exists so tests can drive the production
    predicate with mutants. Default (None) loads the committed specs, so the
    production CLI input path is unchanged.
    """
    problems: list[str] = []
    # Hardware observations: the base gate checks release_eligible only; here
    # the full validator must pass, so no OBSERVED promotion can slip through.
    try:
        raw = hardware_raw if hardware_raw is not None else json.loads(
            (ROOT / "spec" / "hardware_observations.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"hardware_observations unreadable ({type(exc).__name__})"]
    try:
        hw_observation_ledger.validate_ledger(raw)
    except ValueError as exc:
        problems.append(f"hardware_observations: {exc}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"hardware_observations validator crashed ({type(exc).__name__})")
    if raw.get("release_eligible") is not False:
        problems.append("hardware_observations: release_eligible must stay false")
    if raw.get("callee_abi") != "UNVERIFIED":
        problems.append("hardware_observations: callee_abi must stay UNVERIFIED")
    for entry in raw.get("entries", []) if isinstance(raw.get("entries"), list) else []:
        if not isinstance(entry, dict):
            problems.append("hardware_observations: entry must be an object")
            continue
        if entry.get("status") not in ("BLOCKED", "NOT_RUN"):
            problems.append(f"hardware_observations {entry.get('field')!r}: status must stay BLOCKED/NOT_RUN")
        if entry.get("trust_level") != "NOT_MEASURED":
            problems.append(f"hardware_observations {entry.get('field')!r}: trust must stay NOT_MEASURED")
    # C3 static contract: production allowlist stays UNDECIDED, design only.
    try:
        c3 = c3_raw if c3_raw is not None else json.loads(
            (ROOT / "spec" / "c3_static_contracts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return problems + [f"c3_static_contracts unreadable ({type(exc).__name__})"]
    if c3.get("status") != "VERIFICATION_DESIGN_ONLY":
        problems.append("c3_static_contracts: status must stay VERIFICATION_DESIGN_ONLY")
    allow = c3.get("production_allowlist") if isinstance(c3.get("production_allowlist"), dict) else {}
    if allow.get("status") != "UNDECIDED":
        problems.append("c3 production_allowlist: status must stay UNDECIDED")
    if allow.get("approved_by") is not None or allow.get("review_ref") is not None:
        problems.append("c3 production_allowlist: approval must stay null")
    for key in ("mpr1_stack_banks", "transfer_regions", "mmio_devices"):
        if allow.get(key) is not None:
            problems.append(f"c3 production_allowlist: {key} must stay null")
    # api_contracts: mapping/implementation/provenance/confidence stay
    # unpromoted; verification stays NOT_RUN (contract statuses are already
    # pinned by the base gate and re-asserted here for persistence).
    try:
        contracts = contracts_raw if contracts_raw is not None else json.loads(
            (ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return problems + [f"api_contracts unreadable ({type(exc).__name__})"]
    apis = contracts.get("apis", [])
    if not isinstance(apis, list) or len(apis) != 81:
        return problems + [f"api_contracts: expected 81 slots, found {len(apis) if isinstance(apis, list) else type(apis).__name__}"]
    allowed_contract = frozenset({"UNKNOWN", "HYPOTHESIS", "BLOCKED"})
    for index, api in enumerate(apis):
        if not isinstance(api, dict):
            problems.append(f"api_contracts slot {index:02X}: entry must be an object")
            continue
        if api.get("mapping_status") != "HYPOTHESIS":
            problems.append(f"api_contracts slot {index:02X}: mapping_status must stay HYPOTHESIS")
        if api.get("implementation_status") != "NOT_STARTED":
            problems.append(f"api_contracts slot {index:02X}: implementation_status must stay NOT_STARTED")
        if api.get("provenance_status") != "PENDING":
            problems.append(f"api_contracts slot {index:02X}: provenance_status must stay PENDING")
        if api.get("confidence") not in ("UNKNOWN", "SOURCE_SUPPORTED"):
            problems.append(f"api_contracts slot {index:02X}: confidence must stay UNKNOWN/SOURCE_SUPPORTED")
        verification = api.get("verification", {})
        if verification != {"status": "NOT_RUN", "executed_callee": False, "evidence": []}:
            problems.append(f"api_contracts slot {index:02X}: verification must stay NOT_RUN")
        for field, claim in (api.get("contract") or {}).items():
            status = claim.get("status") if isinstance(claim, dict) else None
            if status not in allowed_contract:
                problems.append(f"api_contracts slot {index:02X} field {field}: status {status!r} is a promotion (refused)")
    return problems


CHECKS = (
    ("p10-dev-success-without-promotion", check_dev_success_without_promotion),
    ("p10-synthetic-host-docs-non-promotion", check_synthetic_host_docs_non_promotion),
    ("p10-unverified-persistence", check_unverified_persistence),
)


def run_all() -> tuple[bool, dict]:
    results: dict = {}
    ok = True
    for name, func in CHECKS:
        try:
            failures = func()
        except Exception as exc:  # noqa: BLE001 -- any crash fails closed
            failures = [f"{name} crashed: {type(exc).__name__}: {exc}"]
        results[name] = failures
        if failures:
            ok = False
    return ok, results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify every P10 invariant")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("--check is required")
    try:
        ok, results = run_all()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: P10 invariant gate crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for name, failures in results.items():
        if not failures:
            print(f"PASS {name}")
        else:
            for failure in failures:
                print(f"FAIL {name}: {failure}", file=sys.stderr)
    print(json.dumps({"ok": ok,
                      "failed": sorted(name for name, failures in results.items() if failures),
                      "release_eligible": False,
                      "reference_jsr_permitted": 0,
                      "jsr_count": 0}, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
