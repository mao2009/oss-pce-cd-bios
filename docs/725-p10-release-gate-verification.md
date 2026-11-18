# Issue #725 P10 verification: release gate fail-closed maintenance

Status: **verification only**. No production implementation, no production
guard enabled, no safety gate lifted. `release_eligible=false`,
ABI VERIFIED 0/81, DEV_CALLABLE 0, reference JSR permitted 0/81.
#173 / #459 / #367 stay BLOCKED. `make release` stays BLOCKED.
This project is not a compatible System Card BIOS.

Parent: #473. Design input: PR #714 (design only). Base gate: PR #769
(`tools/failclosed_gate.py`, `tests/host/test_725_failclosed.py`,
`docs/725-failclosed-invariants.md`). This P10 supplement adds only the
vectors the base gate does not assert explicitly; it never duplicates an
existing check and never touches #806 files. Pinned Geargrafx `b49ae82` is
recorded for context; this verification uses no emulator, no BIOS, no ROM,
and no private `./rom/` input.

## 1. What was verified (task items 1-9)

| # | Task item | Base gate (PR #769) | P10 supplement (this issue) | Today |
|---|---|---|---|---|
| 1 | `release_eligible=false` maintained | `check_spec_release_eligible`, `check_dev_callable_zero`, `check_abi_verified_zero`, `check_reference_jsr_closed` | re-verified via `run_all` on both gates | false in `dev_implementations.json`, `abi1_reference_review.json`, `hardware_observations.json`, every `reference_jsr_gate` slot |
| 2 | genuine ABI VERIFIED 0/81 | `check_abi_verified_zero` (81 slots NOT_RUN, summary 0) | `p10-dev-success-without-promotion` cross-checks DEV count > 0 while VERIFIED 0 | 0/81, `reproducible_real_core_observations` 0 |
| 3 | reference JSR 0/81 | `check_reference_jsr_closed` (81/81 closed, `jsr_count` 0, `cdseek` BLOCKED) | re-verified despite DEV success | 81/81 closed, `execution_permitted` false, `jsr_count` 0 |
| 4 | DEV_EXPERIMENTAL success never becomes genuine ABI | `check_release_gate_refuses` (hypothetical-full 81/81 still refuses), `dev_validate` refuses VERIFIED | NEW `check_dev_success_without_promotion`: asserts 18 DEV overrides present with PROJECT_SPEC_ONLY/UNKNOWN yet `abi_verified` 0, `DEV_CALLABLE` 0, `reference_abi_verified` 0, JSR closed, full-override release still refused | 18 DEV overrides (incl. EX_JOYSNS/EX_SCRSIZ DEV_EXPERIMENTAL), 0 VERIFIED, 0 DEV_CALLABLE |
| 5 | synthetic PASS never means release allowed | `check_promotion_negatives` (exact `"ALLOWED"` match, well-formed approval at most MATERIALS_COMPLETE) | NEW extended markers (`SYNTHETIC PASS`, `STATIC_OK`, `PROJECT_SPEC_ONLY`, …) + synthetic-evidence approval still non-ALLOWED | all refused |
| 6 | host PASS alone never unlocks | `test_gate_has_no_unlock_path`, `test_release_gate_module_never_passes` | NEW runtime read audit (observed via monitored APIs on the executed path): canonically-resolved data reads stay within `spec/`+`Makefile`+`src/`; no test-result reads observed | gate has no `write_text`, no `return "ALLOWED"` |
| 7 | docs-only update never changes safety judgment | CI docs-only fast path disabled (full CI for every change) | NEW runtime read audit (observed-access closure on the current path, not a universal proof): executed verdict loads show no `docs/` content read via monitored APIs; docs prose claiming ALLOWED still yields BLOCKED | observed reads contain no `docs/` content |
| 8 | unverified stays UNKNOWN/BLOCKED | `ALLOWED_CONTRACT_STATUSES` + `verification NOT_RUN` + `execution BLOCKED` | NEW `check_unverified_persistence`: hardware ledger validates (no OBSERVED, `callee_abi` UNVERIFIED, BLOCKED/NOT_RUN), c3 allowlist UNDECIDED/DESIGN_ONLY, api_contracts mapping HYPOTHESIS / impl NOT_STARTED / provenance PENDING / confidence UNKNOWN-or-SOURCE_SUPPORTED / verification NOT_RUN | all persist |
| 9 | negatives/mutations detect intentional destruction | Makefile evaluation-time / `.EXTRA_PREREQS` / dynamic-name mutants, ALLOWED ledger, `require_allowed` | NEW 28 RED mutants (see §4: ledger/marker + audit + Makefile-boundary + bytes-path + symlink/external + sensitivity), all detected fail-closed | RED detected, committed tree GREEN |

## 2. What ran (and what did not run)

Ran (host-only, synthetic fixtures only, no cc65 required for this gate):

* `python3 tools/failclosed_gate.py --check` → exit 0, 8/8 PASS.
* `python3 tools/failclosed_725p10.py --check` → exit 0, 3/3 PASS.
* `python3 -m unittest discover -s tests/host -p 'test_725*'` → all PASS
  (base 23 + P10 22).
* `python3 tools/reference_jsr_gate.py --check` → exit 3 (BLOCKED convention),
  `BLOCKED: reference JSR execution gate closed for 81/81 slots; jsr_count=0`.
* `python3 tools/build_rom.py --release-gate` → exit 3,
  `BLOCKED: 63 unimplemented API slots: …` (18 DEV overrides present, 65 stubs).
* `python3 tools/abi1_reference_review.py --check` → exit 0 (18 DEV, 0 VERIFIED).
* `python3 tools/hw_observation_ledger.py` (via P10-C) → PASS (1 BLOCKED entry,
  `callee_abi` UNVERIFIED).
* `make release` (only after the static contract proved the recipe is the
  harmless `echo BLOCKED + exit 1` stub) → non-zero with BLOCKED on stderr.
  The gate itself never executes this recipe.

Did not run (explicitly NOT_RUN / BLOCKED, not evidence):

* No Geargrafx core, no guest execution, no reference JSR call.
* No commercial disc, no System Card boot, no callable System Card BIOS API.
* No original homebrew-disc boot, no retail-game compatibility.
* No private `./rom/` input (absent or not applicable; public tests must pass
  without it; task prohibits commercial/reference access so `./rom/` was not
  listed or opened).

Evidence boundaries per `docs/testing.md` and `AGENTS.md` are kept: host-model
checks, synthetic-disc real-core diagnostics, local commercial-disc structural
observations, callable BIOS API execution, homebrew boot, and game
compatibility are distinct categories. This issue claims only host-model
static verification; every other category is NOT_RUN here.

## 3. Added negatives (no duplication of PR #769)

New tool `tools/failclosed_725p10.py` (host-only, stdlib, fail-closed, no
ledger writes, no unlock input):

* `p10-dev-success-without-promotion`: DEV overrides non-empty yet
  `abi_verified` 0, `DEV_CALLABLE` 0, owner 0, `reference_abi_verified` 0,
  every override-present slot NOT_READY with PROJECT_SPEC_ONLY/UNKNOWN, JSR
  closed, hypothetical-full release still refused.
* `p10-synthetic-host-docs-non-promotion`: runtime read audit (observed-access closure on the executed path, not a universal proof) —
  every content-read and existence-check performed by the verdict loads is
  recorded at call time (direct, dynamically built and helper-indirected
  paths, every occurrence), and no forbidden `docs/`/`build/`/`rom/`/log/
  test-result input may appear while every repo-relative data read stays
  within `spec/`+`Makefile`+`src/`; extended marker refusal;
  synthetic-evidence approval still at most MATERIALS_COMPLETE with
  `execution_permitted` false and `jsr_count` 0. Scope: `.py` code loads are
  filtered (code, not data); one fail-closed docs provenance-pointer stat
  (`docs/cd-reset-contract.md`, absence fails closed) is excused.
* `p10-unverified-persistence`: `hw_observation_ledger.validate_ledger`
  passes, `callee_abi` UNVERIFIED, entries BLOCKED/NOT_RUN with
  NOT_MEASURED trust; c3 status DESIGN_ONLY with allowlist UNDECIDED and
  null approval/regions; api_contracts 81 slots with mapping HYPOTHESIS,
  implementation NOT_STARTED, provenance PENDING, confidence
  UNKNOWN/SOURCE_SUPPORTED, verification NOT_RUN, contract statuses
  UNKNOWN/HYPOTHESIS/BLOCKED.

New tests `tests/host/test_725_p10_failclosed.py` (48 tests, host-only):

* GREEN on the committed tree for all three P10 checks plus base-gate
  regression and CLI `--check` (exit 0).
* RED mutants (in-memory copies or temp-dir dummy files; nothing written to
  the repository, no Makefile executed, never `./rom/`): DEV VERIFIED,
  DEV_CALLABLE without approval, abi1 OBSERVED, hypothetical-full release
  refusal, hardware OBSERVED, C3 APPROVED via the production checker,
  mapping VERIFIED via the production checker — each refused fail-closed.
* Detector load-bearing: explicitly injected normal data is GREEN while the
  same production function on mutated data is RED with slot-specific
  messages (a neutered checker would FAIL these tests).
* Read-audit tests: committed verdict reads are closed as observed via
  the monitored APIs; forbidden `.py` mutants (`docs/policy.py`,
  `rom/input.py`, `docs/__pycache__/*.pyc`, dynamic `.py`, symlink whose
  target sits under `docs/`), external-JSON mutants on all four read APIs,
  a symlink escape (`spec/` link pointing outside), a non-allowlisted repo
  JSON real read, resolve-failure and no-absolute-path-in-messages checks,
  plus detector-disabled sensitivity and restore-on-exception proofs.
  Earlier mutants (direct/dynamic/helper/`build`/2nd-occurrence/`getattr`-
  evasion) retained.
* Docs prose claiming ALLOWED cannot change the verdict because the verdict
  never content-reads docs (audited).
* No-unlock-path test: new tool contains no `return "ALLOWED"`, no
  `release_eligible True`, no `write_text`, no emulator/toolchain imports.

## 4. RED → GREEN

Each mutant below is RED (the tampered input is refused fail-closed); the
committed tree is GREEN (all gates pass). Mutants are never committed.

| Mutant (never committed) | Expected | Observed |
|---|---|---|
| DEV ledger slot 33 `abi_contract.status=VERIFIED` | `dev_validate` reports VERIFIED refused | RED detected (`test_dev_verified_mutant_is_refused_red`) |
| DEV ledger slot 33 `state=DEV_CALLABLE` without approval | `dev_validate` reports missing evidence/approval | RED detected |
| abi1 ledger slot `slot-1e` `abi_state=OBSERVED` | `abi1_reference_review.validate` raises `Invalid` | RED detected |
| `build_rom.release_gate(set(range(81)))` (hypothetical-full) | `ReleaseBlocked("BLOCKED: …")` | RED detected (gate refuses) |
| hardware ledger entry `status=OBSERVED` with fake runs | `hw_observation_ledger.validate_ledger` raises `ValueError` (unauthenticated principal) | RED detected |
| c3 `production_allowlist.status=APPROVED` | production `check_unverified_persistence(c3_raw=…)` reports `c3 production_allowlist: status must stay UNDECIDED` | RED detected |
| `docs/policy.py`, `rom/input.py`, `docs/__pycache__/policy.pyc` content reads (temp dummies) | runtime audit flags forbidden `'docs'`/`'rom'` (old code-first order missed: `([],[])`) | RED detected |
| symlink `link.py` → canonical `docs/secret.py` | forbidden via canonical path | RED detected |
| `/tmp/approval.json` content read via `Path.read_text`/`builtins.open`/`io.open`/`os.open` | `content read outside repository allowlist` | RED detected (4 APIs) |
| `spec/evil.json` symlink → outside secret (patched-ROOT classifier) | escape refused (outside allowlist) | RED detected |
| repo `CONTRIBUTING.md` real content read | `non-allowlisted input 'CONTRIBUTING.md'` | RED detected |
| unresolvable path `\x00` content read | refused, never silently allowed | RED detected |
| api_contracts slot 05 `mapping_status=VERIFIED` | production `check_unverified_persistence(contracts_raw=…)` reports `slot 05: mapping_status must stay HYPOTHESIS` | RED detected |
| `Makefile.approval.json` / `.backup` / `.policy` / `Makefile/approval.json` (old startswith allowed) | `non-allowlisted input 'Makefile.…'` (exact-file allowlist only) | RED detected |
| bytes-path `os.open` / `builtins.open` / `io.open` on temp approval.json (old `_record` dropped bytes) | recorded + `content read outside repository allowlist` | RED detected (3 APIs) |
| undecodable bytes path `\xff\xfe\x00bad` | recorded as placeholder, refused | RED detected |
| direct/dynamic/helper/`build/`/2nd-occurrence/`getattr`-evading forbidden reads (temp dummies) | runtime audit flags the forbidden component | RED detected (6 mutants) |
| neutered detector (always-[]) | RED mutant tests FAIL | load-bearing proven (sensitivity test) |
| `is_execution_permitted("SYNTHETIC PASS" / "host PASS" / "DOC_ONLY" / …)` | `False` for every look-alike | RED detected (all refused) |
| Makefile `release:` stub broken (covered by base gate) | `release_makefile_guard` reports reasons | RED detected (base gate, not duplicated here) |
| Committed tree: base 8/8 + P10 3/3 + host 71 tests (P10 48 + base 23) | exit 0, `release_eligible=false`, JSR 0/81 | GREEN |

## 5. Future unlock procedure (independent review + Owner re-approval required)

No unlock is approved by this issue. If a future release is ever proposed,
all of the following are required, in a separate Issue and separate Draft PR
(1 Issue = 1 Draft PR), before any gate text may change:

1. An independent review by a reviewer who did not author the change,
   recorded with reviewer identity, PR reference, hazard-control evidence,
   and callee-run evidence — and even then the current
   `tools/reference_jsr_gate.py` can only reach MATERIALS_COMPLETE, never
   ALLOWED, because no reference-JSR runner exists.
2. Explicit Owner re-approval of the release, recorded in the ledger approval
   fields (`approved_by`, `review_ref`) and in the PR.
3. A reproducible real-core observation plus an implemented independent
   certification method for `OBSERVED` / `PARTIAL`, and a defined promotion
   path for `VERIFIED` — none of which exists today.
4. Closure or explicit re-scoping of #173 / #367 / #459 with the same
   independent review and Owner approval; a passing synthetic suite, a
   doc-only merge, or a passing host suite is never sufficient.
5. The mechanical gates in this issue and PR #769 must be updated in the same
   PR to expect the newly approved state, and must keep failing closed on
   everything else.

Until every step above is completed and merged, `release_eligible` stays
false, `make release` stays BLOCKED, and any PR that lifts the gate without
them must be refused.

## 6. Provenance

New files added by this P10 verification: `tools/failclosed_725p10.py`,
`tests/host/test_725_p10_failclosed.py`, `docs/725-p10-release-gate-verification.md`.
No OSS reuse, no upstream pin, no proprietary bytes. Synthetic fixtures only.
No `./rom/` input used or required. #806 files untouched.
