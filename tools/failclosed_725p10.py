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
  never promote. Supported by a runtime read audit: the content-reads and
  existence-checks actually observed, via the monitored APIs, on the
  executed verdict-load path are recorded at call time (catching direct,
  dynamically built and helper-indirected paths on every occurrence) and
  must stay within the allowlist — plus an extended marker refusal list
  and a synthetic-evidence approval that still yields no execution. The
  audit observes only the monitored entry points on the executed path: it
  does not cover preopened-FD reads, mmap, subprocess reads, directory
  enumeration, unexecuted future code paths, or OS-level traffic outside
  the monitored APIs, so it demonstrates observed-access closure on the
  current execution path, not a universal proof for all future verdict
  code.
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
# listings are the full data set. tools/*.py sources are code (restricted
# exemption in _is_known_module_source), not data.
#
# Prefixes are directory prefixes WITH trailing slash only, so "speculative"
# can never match "spec/". The Makefile is an exact file allowlist entry:
# "Makefile.approval.json" must never match a bare "Makefile" prefix.
ALLOWLISTED_INPUT_PREFIXES = ("spec/", "src/")
ALLOWLISTED_INPUT_FILES = frozenset({"Makefile"})
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
# Genuine cache/module file names. Anything else under __pycache__ (or a
# non-module name) is data, never trusted code.
_VALID_PYC_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.cpython-\d+\.pyc")
_VALID_MOD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.(py|pyc)")


def _fail(message: str) -> str:
    return message


def _safe_failure(context: str, exc: BaseException) -> str:
    """Fail-closed finding that never leaks paths or values from exc.

    Only the exception type and a fixed context label are reported.
    """
    return f"{context} ({type(exc).__name__}; details withheld)"


def check_dev_success_without_promotion() -> list[str]:
    """DEV_EXPERIMENTAL success must not become genuine ABI (task item 4)."""
    problems: list[str] = []
    try:
        ledger = dev_load_ledger()
        slots = dev_load_slots()
    except Exception as exc:  # noqa: BLE001 -- any load failure fails closed
        return [_safe_failure("dev ledger unreadable", exc)]
    try:
        chosen = set(build_rom.overrides())
    except Exception as exc:  # noqa: BLE001
        return [_safe_failure("dev overrides unreadable", exc)]
    # Precondition: DEV success exists on the committed tree (overrides
    # present with DEV evidence). Without this the non-promotion claim would
    # be vacuous. The exact count is informational; the invariant is that
    # whatever is present never counts as genuine ABI.
    if not chosen:
        problems.append("DEV success precondition: no DEV overrides present")
    try:
        summ = dev_summary(ledger, chosen)
    except Exception as exc:  # noqa: BLE001
        return problems + [_safe_failure("dev summary failed", exc)]
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
    """Legacy suffix-only code guess (kept for reference; NOT used as an exemption).

    Kept so the old, too-broad rule stays visible in history. The classifier
    below no longer trusts suffixes: see _is_known_module_source().
    """
    normalized = path_str.replace("\\", "/")
    if "__pycache__" in normalized.split("/"):
        return True
    return normalized.endswith(".py") or normalized.endswith(".pyc")


def _canonical(path_str: str) -> str | None:
    """Canonical absolute path with symlinks resolved, or None on failure.

    A resolution failure is fail-closed downstream: callers treat None as
    outside the allowlist (content reads) rather than silently allowing it.
    """
    try:
        return os.path.realpath(os.fspath(path_str))
    except (OSError, ValueError, TypeError):
        return None


def _is_known_module_source(canonical_repo_rel: str | None) -> bool:
    """Restricted code exemption: canonical known gate-module sources only.

    A path is exempted as code only when it canonically resolves inside the
    tools/ module tree with a Python source form (.py, .pyc, or a
    __pycache__ entry). Suffix alone never exempts: docs/policy.py or
    rom/input.py are data reads and stay forbidden (checked before this).
    """
    if canonical_repo_rel is None:
        return False
    parts = canonical_repo_rel.split("/")
    if not parts or parts[0] != "tools":
        return False
    if "__pycache__" in parts:
        # Only genuine cache entries: <mod>.cpython-<ver>.pyc directly under
        # __pycache__. Arbitrary descendants (e.g. approval.json) are data.
        rest = parts[parts.index("__pycache__") + 1:]
        return (len(rest) == 1
                and _VALID_PYC_RE.fullmatch(rest[0]) is not None)
    # Only direct module files: <mod>.py / <mod>.pyc directly under tools/.
    # Nested paths are not known module sources.
    return len(parts) == 2 and _VALID_MOD_RE.fullmatch(parts[1]) is not None


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


def _is_failclosed_marker(path_str: str) -> bool:
    """True for audit-internal fail-closed placeholders (never excused)."""
    return path_str.startswith("<") and path_str.endswith(">")


def _is_ancestor_of_root(canonical: str) -> bool:
    """True only for ancestor directories of ROOT hit by resolve (excused).

    pathlib ``resolve()``/``realpath()`` stats ancestor directories via
    ``os.stat``/``os.lstat`` (e.g. ``/tmp`` when ROOT is
    ``/tmp/<worktree>``). Those ancestor stats are OS/resolve noise, not
    verdict inputs: their existence is trivially true and carries no
    attacker-controlled signal. Only exact ancestor directories are
    excused (directory-boundary prefix match, ``/`` handled explicitly);
    every other outside-ROOT existence check — including sibling temp
    files, stdlib paths and explicit ``/tmp``-style verdict probes that
    are not ancestors — stays fail-closed. Markers (``<...>``) are never
    ancestors.
    """
    try:
        if not isinstance(canonical, str):
            return False
        if _is_failclosed_marker(canonical):
            return False
        root_resolved = os.path.realpath(str(ROOT.resolve()))
        canon = os.path.realpath(canonical)
        if canon == "/":
            return True
        if root_resolved == canon:
            return True
        return root_resolved.startswith(canon.rstrip("/") + "/")
    except (OSError, ValueError, TypeError):
        return False


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
    # While set, wrappers skip recording: used while resolving the effective
    # path itself, so the audit never records its own canonicalization I/O.
    quiet = False

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

    def _effective_at_record(text: str) -> str:
        # Call-time canonical path: absolute + symlink-resolved while the
        # reading CWD still applies, so a later chdir cannot re-target the
        # record. Internal stat calls run under quiet and leave no trace.
        nonlocal quiet
        prev = quiet
        quiet = True
        try:
            return os.path.realpath(os.path.abspath(text))
        except (OSError, ValueError, TypeError):
            return "<unresolvable-path>"
        finally:
            quiet = prev

    def _record(kind: str, value) -> None:
        # bytes paths (accepted by os.open/builtins.open/io.open) are decoded
        # here so they can never bypass the audit. Undecodable or otherwise
        # unusable values become an explicit placeholder that downstream
        # classification refuses (fail closed, never silently allowed).
        if quiet:
            return
        if isinstance(value, (bytes, bytearray)):
            try:
                value = os.fsdecode(value)
            except (UnicodeDecodeError, ValueError):
                value = "<undecodable-bytes-path>"
        if not isinstance(value, (str, os.PathLike)):
            return
        try:
            text = os.fspath(value)
        except (TypeError, ValueError):
            text = "<unrecordable-path>"
        if not isinstance(text, str):
            text = "<unrecordable-path>"
        records.append((kind, _effective_at_record(text)))

    def _read_text(self, *args, **kwargs):
        _record("read", str(self))
        return orig_read_text(self, *args, **kwargs)

    def _read_bytes(self, *args, **kwargs):
        _record("read", str(self))
        return orig_read_bytes(self, *args, **kwargs)

    def _path_open(self, *args, **kwargs):
        _record("read", str(self))
        return orig_path_open(self, *args, **kwargs)

    def _opener_present(args, kwargs):
        # opener is the 8th parameter of both builtins.open and io.open
        # (file, mode, buffering, encoding, errors, newline, closefd,
        # opener), verified against the runtime signatures; it may arrive
        # by keyword or by position. opener=None is the normal path.
        if kwargs.get("opener", None) is not None:
            return True
        return len(args) > 6 and args[6] is not None

    def _builtin_open(file, *args, **kwargs):
        # A custom opener can redirect the read independently of the path
        # string; it cannot be resolved safely here, so fail closed.
        # An int fd is likewise unresolvable to a path: fail closed.
        if _opener_present(args, kwargs):
            records.append(("read", "<custom-opener>"))
        if isinstance(file, int):
            records.append(("read", "<int-fd-read>"))
            return orig_builtin_open(file, *args, **kwargs)
        _record("read", file)
        return orig_builtin_open(file, *args, **kwargs)

    def _io_open(file, *args, **kwargs):
        if _opener_present(args, kwargs):
            records.append(("read", "<custom-opener>"))
        if isinstance(file, int):
            records.append(("read", "<int-fd-read>"))
            return orig_io_open(file, *args, **kwargs)
        _record("read", file)
        return orig_io_open(file, *args, **kwargs)

    def _os_open(path, *args, **kwargs):
        # dir_fd redirects relative paths to another directory; the recorded
        # path string alone cannot identify the real target (FD lifetime,
        # reuse and platform differences make safe resolution unreliable
        # here), so any non-default dir_fd is fail-closed. An int fd is
        # likewise unresolvable to a path: fail closed with a fixed marker
        # (no fd number or path leaks). The call itself still delegates
        # unchanged: behavior is observed, never altered, and no extra file
        # is opened for classification.
        if kwargs.get("dir_fd", None) is not None:
            records.append(("read", "<dir_fd-relative-path>"))
        if isinstance(path, int):
            records.append(("read", "<int-fd-read>"))
        else:
            _record("read", path)
        return orig_os_open(path, *args, **kwargs)

    def _dir_fd_present(kwargs):
        # os.stat/os.lstat take dir_fd keyword-only; a non-default dir_fd
        # redirects relative paths to another directory, so the recorded
        # string alone cannot identify the real target: fail closed.
        # follow_symlinks and other flags pass through unchanged.
        return kwargs.get("dir_fd", None) is not None

    def _os_stat(path, *args, **kwargs):
        # An int fd (os.stat(fd) is valid on Linux/Python) is unresolvable
        # to a repo path: fail closed with a fixed marker that carries no
        # fd number and no absolute path. The call still delegates unchanged
        # (return value, errors and dir_fd/follow_symlinks handling
        # preserved); the marker is recorded before delegation so even a
        # raising call (e.g. unsupported fd on some platforms) is flagged.
        if _dir_fd_present(kwargs):
            records.append(("stat", "<dir_fd-relative-stat>"))
        if isinstance(path, int):
            records.append(("stat", "<int-fd-stat>"))
        else:
            _record("stat", path)
        return orig_os_stat(path, *args, **kwargs)

    def _os_lstat(path, *args, **kwargs):
        # os.lstat(int) raises TypeError on CPython (fd has no symlink),
        # but an attempted fd call must still be audited, not silently
        # ignored: record the same fixed marker before delegating so the
        # attempt is fail-closed even when the caller catches the error.
        if _dir_fd_present(kwargs):
            records.append(("stat", "<dir_fd-relative-stat>"))
        if isinstance(path, int):
            records.append(("stat", "<int-fd-stat>"))
        else:
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

    Order matters: fail-closed markers (``<...>``) are refused FIRST,
    CWD-independently, so a marker can never be laundered through a
    canonical path that happens to fall inside ROOT. Forbidden components
    are checked SECOND on both the raw and the canonical
    (symlink-resolved) path, so docs/policy.py or a symlink landing under
    docs//rom//build/ can never be laundered through a code exemption.
    The code exemption is restricted to canonical known module sources
    (tools/ .py/.pyc/__pycache__). Content reads outside the canonical
    repository allowlist are refused (fail closed); outside-ROOT existence
    checks are fail-closed except for the narrow resolve-noise excuse
    (ancestor directories of ROOT hit by ``pathlib.resolve``/``realpath``
    via ``os.stat``/``os.lstat``). Stat hits on ALLOWLISTED_STAT_EXCEPTIONS
    (docs provenance pointers that fail closed on absence) are explicitly
    excused. No absolute filesystem path, fd number or exception detail
    ever appears in a message: outside-ROOT hits use fixed strings with
    only the component or marker name.
    """
    forbidden: list[str] = []
    non_allowlisted: list[str] = []
    for kind, path_str in records:
        # 0. Fail-closed markers FIRST (CWD-independent, never excused).
        # Covers <custom-opener>, <int-fd-read>, <int-fd-stat>,
        # <dir_fd-relative-path>, <dir_fd-relative-stat>,
        # <unresolvable-path>, <undecodable-bytes-path>,
        # <unrecordable-path>. The marker name itself is safe (no path,
        # no fd number); the message keeps the marker substring so
        # detector-sensitivity tests ("int-fd", "dir_fd",
        # "custom-opener") keep working.
        if _is_failclosed_marker(path_str):
            non_allowlisted.append(f"{kind} fail-closed marker {path_str!r} (refused)")
            continue
        canonical = _canonical(path_str)
        # 1. Forbidden FIRST: raw path and symlink-resolved target alike.
        # A symlink under an allowlisted prefix that lands outside (or under
        # a forbidden component) is caught here via its canonical path.
        hit = _is_forbidden_recorded_path(path_str)
        if hit is None and canonical is not None:
            hit = _is_forbidden_recorded_path(canonical)
        canon_rel = _repo_relative(canonical) if canonical is not None else None
        if hit is not None:
            # Excuse the known fail-closed provenance-pointer stat and the
            # parent-directory stats that resolving it implies (pathlib
            # resolve stats ancestors via os.stat); a content read of any
            # docs/ path would still be forbidden, since the validator only
            # stats existence (absence fails closed, never promotes).
            if kind == "stat" and canon_rel in ALLOWLISTED_STAT_EXCEPTIONS:
                continue
            if kind == "stat" and canon_rel is not None and any(
                exc == canon_rel or exc.startswith(canon_rel + "/")
                for exc in ALLOWLISTED_STAT_EXCEPTIONS
            ):
                continue
            if canon_rel is not None:
                forbidden.append(f"{kind} forbidden input {hit!r} via {canon_rel}")
            else:
                forbidden.append(f"{kind} forbidden input {hit!r} outside repository")
            continue
        # 2. Restricted code exemption: canonical known module sources only.
        # Suffix alone never exempts (docs/policy.py stays forbidden above).
        if _is_known_module_source(canon_rel):
            continue
        # 3. Outside-ROOT: content reads refused; existence checks are
        # fail-closed except for narrow resolve-noise ancestors.
        # Previously all outside-ROOT stat was ignored (false GREEN for
        # verdict-relevant external existence checks). Now only ancestor
        # directories of ROOT (hit by pathlib resolve/realpath via
        # os.stat/os.lstat) are excused; every other external stat —
        # sibling temp files, stdlib paths, explicit /tmp probes — is
        # refused with a fixed message (no path leak). Resolution failures
        # (canonical is None) are also refused.
        if canon_rel is None:
            if kind == "read":
                non_allowlisted.append(f"{kind} content read outside repository allowlist")
            else:
                if canonical is not None and _is_ancestor_of_root(canonical):
                    continue
                non_allowlisted.append(
                    f"{kind} external existence check outside repository allowlist (refused)"
                )
            continue
        if canon_rel == ".":
            # The repo root itself carries no verdict data: validators resolve
            # paths through ROOT (pathlib resolve stats it) and its existence
            # is trivially true. A content read of a directory would fail
            # anyway, so only stat is excused here.
            if kind == "stat":
                continue
            non_allowlisted.append(f"{kind} repo-root read (refused)")
            continue
        allowed = canon_rel in ALLOWLISTED_INPUT_FILES or canon_rel.startswith(
            ALLOWLISTED_INPUT_PREFIXES)
        if not allowed:
            non_allowlisted.append(f"{kind} non-allowlisted input {canon_rel!r}")
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

    Input closure is supported by a runtime read audit: the content-reads
    and existence-checks actually observed, via the monitored entry points
    (Path.read_text/read_bytes/open, builtins.open, io.open, os.open/stat/
    lstat, Path.exists/is_file/stat), on the executed verdict loads are
    recorded at call time (catching direct, dynamically built and
    helper-indirected paths on every occurrence) and must contain no
    forbidden docs//build//rom//test-result input while every
    canonically-resolved data read stays allowlisted and every
    outside-ROOT existence check is fail-closed except narrow
    resolve-noise ancestors. Reads are recorded
    with their call-time canonical location, so a later chdir cannot
    re-target them; non-default os.stat/os.lstat dir_fd, custom openers
    (keyword or positional), int-fd opens/stats and unresolvable paths are
    fail-closed markers, never silently allowed. Scope: the audit
    covers only the executed path through the monitored APIs — preopened-FD
    reads, mmap, subprocess reads, directory enumeration, unexecuted future
    code paths and OS traffic outside the monitored set are not observed,
    so the claim is observed-access closure on the current execution path,
    not a universal proof. One fail-closed docs provenance-pointer stat
    (ALLOWLISTED_STAT_EXCEPTIONS) is excused because absence fails closed
    and content is never read as verdict data.
    """
    problems: list[str] = []
    # Runtime input closure over the actual verdict loads.
    try:
        records = _audit_verdict_reads(_run_verdict_loads_for_audit)
    except Exception as exc:  # noqa: BLE001 -- any audit failure fails closed
        return [_safe_failure("input-closure audit crashed", exc)]
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
        problems.append(_safe_failure("hardware_observations refused", exc))
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
            failures = [_safe_failure(f"{name} failed", exc)]
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
        print(_safe_failure("FAIL: P10 invariant gate crashed", exc), file=sys.stderr)
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
