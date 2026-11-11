#!/usr/bin/env python3
"""Fail-closed release invariant gate (Issue #725).

Mechanically verifies that #473 validation progress never lifts the safety
boundary of #173/#367/#459 or the ``make release`` boundary:

* ``make release`` stays BLOCKED (static Makefile contract; the recipe itself
  is never executed by this gate).
* ``release_eligible`` stays false in every spec ledger that carries it.
* Owner-approved DEV_CALLABLE stays 0.
* Reference ABI VERIFIED stays 0/81.
* Reference JSR permitted stays 0/81 (``jsr_count`` stays 0).
* Synthetic PASS, doc-only merges and host-test PASS are never promotion
  reasons (negative checks below).
* Undecidable release paths fail closed (malformed ledgers are refused;
  no code path in this repository returns ``ALLOWED``).

Host-only. Reads no BIOS, no ROM, no emulator, no private ``./rom/`` input.
Pure stdlib. Exit 0 means every invariant held; exit 1 means at least one
failed. Any crash is a failure, never a pass. This gate never lifts a safety
gate: it has no unlock input and no write path to any ledger.
"""
from __future__ import annotations

import argparse
import json
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
    validate as dev_validate,
)
from reference_jsr_gate import (  # noqa: E402
    HAZARDS as JSR_HAZARDS,
    is_execution_permitted,
    load as jsr_load,
    resolve as jsr_resolve,
)

import build_rom  # noqa: E402
from build_rom import ReleaseBlocked  # noqa: E402
import cdseek_safety_gate  # noqa: E402

SPEC_RELEASE_ELIGIBLE_FILES = (
    "spec/dev_implementations.json",
    "spec/abi1_reference_review.json",
    "spec/hardware_observations.json",
)

# Contract field statuses that describe callee ABI behaviour. Only UNKNOWN /
# HYPOTHESIS / BLOCKED may appear today; OBSERVED / VERIFIED would be a
# promotion and must fail this gate.
ALLOWED_CONTRACT_STATUSES = frozenset({"UNKNOWN", "HYPOTHESIS", "BLOCKED"})


def _fail(message: str) -> str:
    return message


def check_makefile_release() -> list[str]:
    """Static contract of the ``release:`` target (never executed here).

    Whitelist: the only safe shape is exactly the two-line BLOCKED stub::

        release:
        \t@echo 'BLOCKED: ...' >&2
        \t@exit 1

    Any other shape fails closed, including the known bypass classes:

    * ``-@exit 1`` / ``-`` prefix (make ignore-error neutralises ``exit 1``);
    * conditional/chained success (``|| true``, ``&&``, ``; exit 0``,
      ``; true``, trailing ``exit 0``);
    * preceding build commands or extra recipe lines (only 2 lines allowed);
    * extra dangerous statements (build/release promotion, shell
      substitution, redirection beyond ``>&2``).
    """
    problems: list[str] = []
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^release:\n((?:\t.*\n?)+)", text, re.M)
    if match is None:
        return ["Makefile has no 'release:' target"]
    recipe = match.group(1)
    lines = recipe.splitlines()
    # Whitelist: exactly the echo-BLOCKED + exit-1 stub, nothing more.
    if len(lines) != 2:
        problems.append(
            "Makefile release recipe must be exactly 2 lines "
            f"(@echo BLOCKED-stub + @exit 1), found {len(lines)}"
        )
    for index, line in enumerate(lines, start=1):
        if not line.startswith("\t"):
            problems.append(f"Makefile release line {index}: must start with TAB")
            continue
        body = line[1:]
        if body.startswith("-"):
            problems.append(
                f"Makefile release line {index}: '-' (ignore-error) prefix forbidden "
                "(would neutralise 'exit 1'; '-@exit 1' refused)"
            )
        if not body.startswith("@"):
            problems.append(
                f"Makefile release line {index}: must use '@' prefix "
                f"(got {body[:12]!r}; conditional/silent bypass refused)"
            )
    if lines:
        body1 = lines[0][1:] if lines[0].startswith("\t") else lines[0]
        if not re.fullmatch(r"@echo\s+'BLOCKED:.*'\s+>&2\s*", body1):
            problems.append(
                "Makefile release line 1 must be \"@echo 'BLOCKED: ...' >&2\" "
                f"(got {body1!r}; preceding build commands refused)"
            )
    if len(lines) >= 2:
        body2 = lines[1][1:] if lines[1].startswith("\t") else lines[1]
        if body2.strip() != "@exit 1":
            if "exit 1" not in body2:
                problems.append("Makefile release recipe must end with '@exit 1'")
            else:
                problems.append(
                    f"Makefile release line 2 must be exactly '@exit 1' "
                    f"(got {body2!r}; conditional/chained success refused)"
                )
        if ";" in body2 or "||" in body2 or "&&" in body2 or "|" in body2:
            problems.append(
                "Makefile release line 2: shell chaining "
                f"(';'/||/&&/|) forbidden (got {body2!r})"
            )
    if "BLOCKED" not in recipe:
        problems.append("Makefile release recipe lacks BLOCKED")
    # Unconditional success anywhere is a promotion bypass.
    if re.search(r"exit\s+0\b", recipe):
        problems.append("Makefile release recipe must not contain 'exit 0' "
                        "(conditional success refused)")
    # Shell metacharacters outside the quoted BLOCKED message, excluding the
    # one allowed '>&2' redirection. The ';' inside the quoted message is
    # stripped with the quotes, so this does not false-positive on legit.
    code_outside_quotes = re.sub(r"'[^']*'", "''", recipe)
    code_no_redir = code_outside_quotes.replace(">&2", "")
    for token in (";", "||", "&&", "|", "`", "$(", "${", ">", "<", "#"):
        if token in code_no_redir:
            problems.append(
                f"Makefile release recipe: {token!r} outside quoted message forbidden "
                "(chaining/substitution/redirection refused)"
            )
            break
    if re.search(r"\btrue\b", code_no_redir) or re.search(r"\bfalse\b", code_no_redir):
        problems.append(
            "Makefile release recipe must not contain 'true'/'false' "
            "(success-masking refused)"
        )
    for forbidden in ("build_rom.py", "--mode release", "release_eligible=true",
                      "ALLOWED", "release_eligible = True", "build_rom",
                      "$(PYTHON)", "${", "rm ", "rm\t", "cp ", "mv ",
                      "chmod", "chown", "curl", "wget", "python"):
        if forbidden in recipe:
            problems.append(f"Makefile release recipe must not contain {forbidden!r} "
                            "(preceding build / dangerous statement refused)")
            break
    return problems


def check_spec_release_eligible() -> list[str]:
    problems: list[str] = []
    for rel in SPEC_RELEASE_ELIGIBLE_FILES:
        path = ROOT / rel
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{rel}: unreadable ({type(exc).__name__})")
            continue
        if payload.get("release_eligible") is not False:
            problems.append(f"{rel}: release_eligible must stay false")
    # The per-slot JSR ledger restates BLOCKED with release_eligible=false.
    try:
        ledger, _ = jsr_load()
    except Exception as exc:  # noqa: BLE001 -- any load failure is a gate failure
        return problems + [f"spec/reference_jsr_gate.json: unreadable ({type(exc).__name__})"]
    for key, entry in ledger.get("slots", {}).items():
        if entry.get("release_eligible") is not False:
            problems.append(f"reference_jsr_gate slot {key}: release_eligible must stay false")
        if entry.get("execution") != "BLOCKED":
            problems.append(f"reference_jsr_gate slot {key}: execution must stay BLOCKED")
    return problems


def check_dev_callable_zero() -> list[str]:
    problems: list[str] = []
    try:
        ledger = dev_load_ledger()
        slots = dev_load_slots()
    except Exception as exc:  # noqa: BLE001
        return [f"dev ledger unreadable ({type(exc).__name__}: {exc})"]
    try:
        from dev_implementations import overrides as dev_overrides
        chosen = set(dev_overrides())
    except Exception as exc:  # noqa: BLE001
        return [f"dev overrides unreadable ({type(exc).__name__}: {exc})"]
    problems.extend(f"dev ledger: {p}" for p in dev_validate(ledger, slots, chosen))
    try:
        summ = dev_summary(ledger, chosen)
    except Exception as exc:  # noqa: BLE001
        return problems + [f"dev summary failed ({type(exc).__name__}: {exc})"]
    if summ.get("release_eligible") is not False:
        problems.append("dev summary: release_eligible must stay false")
    if summ.get("dev_callable_approved") != 0:
        problems.append(f"dev summary: DEV_CALLABLE must stay 0 (got {summ.get('dev_callable_approved')})")
    if summ.get("owner_approved") != 0:
        problems.append(f"dev summary: owner approval must stay 0 (got {summ.get('owner_approved')})")
    if summ.get("abi_verified") != 0:
        problems.append(f"dev summary: ABI VERIFIED must stay 0 (got {summ.get('abi_verified')})")
    return problems


def check_abi_verified_zero() -> list[str]:
    problems: list[str] = []
    try:
        document, contracts, dev_ledger = abi1_load()
    except Exception as exc:  # noqa: BLE001
        return [f"abi1 ledger invalid ({type(exc).__name__})"]
    summary = document.get("summary", {})
    if summary.get("reference_abi_verified") != 0:
        problems.append("abi1 summary: reference_abi_verified must stay 0")
    if summary.get("owner_approved_dev_callable") != 0:
        problems.append("abi1 summary: owner_approved_dev_callable must stay 0")
    if summary.get("reproducible_real_core_observations") != 0:
        problems.append("abi1 summary: reproducible_real_core_observations must stay 0")
    if document.get("release_eligible") is not False:
        problems.append("abi1 review: release_eligible must stay false")
    # Full contract ledger: 81 slots, every verification NOT_RUN, no
    # OBSERVED/VERIFIED callee-behaviour claim.
    apis = contracts.get("apis", [])
    if len(apis) != 81:
        problems.append(f"api_contracts: expected 81 slots, found {len(apis)}")
        return problems
    for index, api in enumerate(apis):
        verification = api.get("verification", {})
        if verification != {"status": "NOT_RUN", "executed_callee": False, "evidence": []}:
            problems.append(f"api_contracts slot {index:02X}: verification must stay NOT_RUN")
        for field, claim in (api.get("contract") or {}).items():
            status = claim.get("status") if isinstance(claim, dict) else None
            if status not in ALLOWED_CONTRACT_STATUSES:
                problems.append(f"api_contracts slot {index:02X} field {field}: "
                                f"status {status!r} is a promotion (refused)")
    _ = dev_ledger  # cross-checked inside abi1_load().validate; kept explicit.
    return problems


def check_reference_jsr_closed() -> list[str]:
    problems: list[str] = []
    try:
        ledger, contracts = jsr_load()
    except Exception as exc:  # noqa: BLE001
        return [f"reference_jsr_gate ledger invalid ({type(exc).__name__})"]
    total = len(contracts.get("apis", []))
    if total != 81:
        return [f"reference_jsr_gate: expected 81 contract slots, found {total}"]
    closed = 0
    for slot in range(total):
        try:
            record = jsr_resolve(slot, ledger, contracts)
        except Exception as exc:  # noqa: BLE001 -- resolve must not raise on the committed ledger
            problems.append(f"reference_jsr_gate slot {slot:02X}: resolve raised {type(exc).__name__}")
            continue
        if is_execution_permitted(record.get("execution")):
            problems.append(f"reference_jsr_gate slot {slot:02X}: execution permitted (refused)")
        else:
            closed += 1
        if record.get("execution_permitted") is not False:
            problems.append(f"reference_jsr_gate slot {slot:02X}: execution_permitted must stay False")
        if record.get("jsr_count") != 0:
            problems.append(f"reference_jsr_gate slot {slot:02X}: jsr_count must stay 0")
        if record.get("release_eligible") is not False:
            problems.append(f"reference_jsr_gate slot {slot:02X}: release_eligible must stay false")
    if closed != total:
        problems.append(f"reference_jsr_gate: closed {closed}/{total}, expected 81/81")
    # The CD_SEEK pre-execution gate stays BLOCKED with zero executions.
    try:
        decision = cdseek_safety_gate.decide()
    except Exception as exc:  # noqa: BLE001
        return problems + [f"cdseek_safety_gate undecidable ({type(exc).__name__})"]
    if decision.get("verdict") != "BLOCKED":
        problems.append("cdseek_safety_gate: verdict must stay BLOCKED")
    if decision.get("jsr_count") != 0:
        problems.append("cdseek_safety_gate: jsr_count must stay 0")
    return problems


def check_release_gate_refuses() -> list[str]:
    problems: list[str] = []
    try:
        current = set(build_rom.overrides())
    except Exception as exc:  # noqa: BLE001
        return [f"build_rom overrides unreadable ({type(exc).__name__}: {exc})"]
    for label, overridden in (("current", current), ("hypothetical-full", set(range(81)))):
        try:
            build_rom.release_gate(overridden)
        except ReleaseBlocked as exc:
            if not str(exc).startswith("BLOCKED:"):
                problems.append(f"release_gate({label}): refusal must start with 'BLOCKED:'")
        except Exception as exc:  # noqa: BLE001 -- any other exception is not the designed refusal
            problems.append(f"release_gate({label}): wrong exception {type(exc).__name__}")
        else:
            # FORBIDDEN: the gate must never pass. A fall-through return would
            # be a lifted safety gate.
            problems.append(f"release_gate({label}): gate passed (FORBIDDEN: gate must refuse)")
    return problems


def _well_formed_approval() -> dict:
    return {
        "reviewer": "synthetic-negative-test",
        "pr": 1,
        "abi_status": "REVIEWED",
        "hazard_controls": {hazard: "CONTROLLED" for hazard in JSR_HAZARDS},
        "callee_run_evidence": ["synthetic-host-only-not-a-run"],
    }


def check_promotion_negatives() -> list[str]:
    """Synthetic PASS / doc-only / host PASS must never promote."""
    problems: list[str] = []
    # Only the exact string "ALLOWED" permits; every look-alike fails closed.
    for candidate in (None, "", "allowed", " ALLOWED ", "MATERIALS_COMPLETE",
                      "BLOCKED", "PASS", 0, False, b"ALLOWED"):
        if is_execution_permitted(candidate):
            problems.append(f"is_execution_permitted({candidate!r}) must stay False")
    if not is_execution_permitted("ALLOWED"):
        # The predicate itself is exact-match by design; if it rejects the
        # literal, the fail-closed definition drifted.
        problems.append('is_execution_permitted("ALLOWED") definition drifted')
    try:
        ledger, contracts = jsr_load()
    except Exception as exc:  # noqa: BLE001
        return [f"promotion negatives: ledger unreadable ({type(exc).__name__})"]
    slot = 1  # the one reviewed slot; unreviewed slots stay BLOCKED regardless.
    try:
        # A fully CONTROLLED, well-formed approval still grants no execution:
        # at most MATERIALS_COMPLETE, never ALLOWED, permission stays False.
        record = jsr_resolve(slot, ledger, contracts, approval=_well_formed_approval())
    except Exception as exc:  # noqa: BLE001
        return [f"promotion negatives: resolve raised {type(exc).__name__}"]
    if is_execution_permitted(record.get("execution")):
        problems.append("promotion negatives: well-formed approval must never yield ALLOWED")
    if record.get("execution") not in ("BLOCKED", "MATERIALS_COMPLETE"):
        problems.append(f"promotion negatives: unexpected execution {record.get('execution')!r}")
    if record.get("execution_permitted") is not False:
        problems.append("promotion negatives: execution_permitted must stay False")
    if record.get("jsr_count") != 0:
        problems.append("promotion negatives: jsr_count must stay 0")
    # Malformed / missing approval stays BLOCKED (fail closed).
    for label, approval in (("none", None), ("malformed", {"reviewer": ""}),
                            ("host-pass-only", {"reviewer": "x", "pr": 1})):
        try:
            rec = jsr_resolve(slot, ledger, contracts, approval=approval)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"promotion negatives: approval {label} raised {type(exc).__name__}")
            continue
        if rec.get("execution") != "BLOCKED":
            problems.append(f"promotion negatives: approval {label} must stay BLOCKED")
    return problems


def check_undecidable_fail_closed() -> list[str]:
    """Unknown / malformed inputs are refused, never promoted."""
    problems: list[str] = []
    try:
        ledger, contracts = jsr_load()
    except Exception as exc:  # noqa: BLE001
        return [f"undecidable: ledger unreadable ({type(exc).__name__})"]
    # A ledger that claims ALLOWED must be refused by validation.
    tampered = json.loads(json.dumps(ledger))
    tampered["slots"]["1"]["execution"] = "ALLOWED"
    try:
        from reference_jsr_gate import validate_ledger
        validate_ledger(tampered, contracts)
    except ValueError:
        pass
    except Exception as exc:  # noqa: BLE001
        problems.append(f"undecidable: wrong exception for ALLOWED ledger ({type(exc).__name__})")
    else:
        problems.append("undecidable: ALLOWED ledger was accepted (FORBIDDEN)")
    # require_allowed() has no allow path on the committed ledger.
    try:
        from reference_jsr_gate import require_allowed, GateRefused
        try:
            require_allowed("slot-1e")
        except GateRefused:
            pass
        else:
            problems.append("undecidable: require_allowed passed (FORBIDDEN)")
        try:
            require_allowed("slot-zz")
        except GateRefused:
            pass
        else:
            problems.append("undecidable: require_allowed accepted a malformed target")
    except ImportError as exc:
        problems.append(f"undecidable: gate import failed ({exc})")
    return problems


CHECKS = (
    ("makefile-release-stays-blocked", check_makefile_release),
    ("spec-release-eligible-false", check_spec_release_eligible),
    ("dev-callable-zero", check_dev_callable_zero),
    ("abi-verified-zero", check_abi_verified_zero),
    ("reference-jsr-closed", check_reference_jsr_closed),
    ("release-gate-refuses", check_release_gate_refuses),
    ("promotion-negatives", check_promotion_negatives),
    ("undecidable-fail-closed", check_undecidable_fail_closed),
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
    parser.add_argument("--check", action="store_true", help="verify every fail-closed invariant")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("--check is required")
    try:
        ok, results = run_all()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: invariant gate crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
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
