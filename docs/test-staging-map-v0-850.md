# Test staging map v0 (Phase 1, Issue #850 follow-up)

> **Scope:** docs + machine-readable table + validation tests only. No
> change to `Makefile`, `.github/workflows/*`, Required Checks, test
> bodies, or pins. Base: `origin/main` `2399a50b` (Merge PR #851).
> Branch: `agent/test-staging-map-v0-20261128`.
> **Evidence boundary:** PASS below applies only to the listed checks.
> **Local media:** no commercial or reference-BIOS media was accessed.
> Non-contact holds independent of media presence: enumeration is
> tracked-files-only (`git ls-files -z`, so untracked `./rom/` content
> is never scanned) plus a synthetic-fixture non-contact test; nothing
> asserts on `./rom/` existence, listing, or bytes. (History, not proof:
> an early draft noted `ls rom/` failing; current evidence does not rely
> on that.) The media test PASSES (never SKIP); no real-media test is
> attempted.

Related: #850 (survey/design, `docs/test-staging-survey-850.md` §§1–3),
#853 (ledger gaps corrected here, §5), #848 (CI sharding, integrated),
#852 (Phase 2 input handling, out of scope).

## 1. Artifacts (added only)

| File | Role |
| --- | --- |
| `tools/test_staging_map.json` | Machine-readable map (this doc is its human companion) |
| `docs/test-staging-map-v0-850.md` | This file |
| `tests/host/test_test_staging_map.py` | Validation tests (schema, refs, no-dup, no-unclassified-leak, 81-slot coverage, safety invariants) |

Read the map with `python3 -c "import json;
m=json.load(open('tools/test_staging_map.json'))"`. Evaluation order is
array order, first match wins; the last entry (`fallback-unclassified`,
`glob **`) classifies everything else as **L3** (unknown is never
L0/L1/L2 — fail-closed).

## 2. Schema

Top level: `schema_version`, `levels` (L0–L4), `ci_classes`,
`required_checks` (6 names), `safety_invariants`, `ci_facts`,
`static_units_defined`, `make_transitive`, `entries`, `fallback`,
`api_slots` (81 rows).

Each `entries[]` row: `id`, `match_type` (`exact`|`exact-list`|`glob`),
`match`, `level`, `category` (`host`|`real-core`|`dev-diagnostic`|
`direct-integration`|`static`|`mutation`|`safety-gate`|`shared-code`|
`build-infra`|`ci-workflow`|`docs`|`fixture`|`selection-logic`|
`dev-api-impl`), `tests` (`host` exact modules `tests.host.*` or
slash-form `tests/host/*.py` patterns, `integration_scripts`,
`dev_targets`, `static_units`), `reason`, `required_ci` (boolean),
`ci_class` (`required-ci`|`ci-nonrequired`|`ci-outside-optin`|
`push-only`). Selected rows carry `shards` (module→rc-N) or `units`.

Each `api_slots[]` row: `slot` (0–80, the identifier — candidate
names are NOT duplicated here but resolve via `spec/api_slots.json`,
the single source, so not-wired negative tests never trip on the
map), `dev_implemented` (exactly the 18 override slots),
`dev_source`, `level`, `tests`, `reason` (slot-number form, e.g.
`$07`).

## 3. Classification summary

- **L1 (25 rows):** 18 override slots
  (`src/overrides/slot_{1E,21,22,23,2E,3A,3C,3D,3E,3F,
  40,41,42,43,44,45,46,4F}.s`) + 5 math sources
  (`src/math/ma_{mul8u,mul8s,mul16u,div16s,div16u}.s`) + 2 host patterns
  (`tests/host/test_api*.py`, `tests/host/test_ma*.py`; identical to the
  `dev-test` selection run by required `bios-dev-full`).
- **L2 (8 rows):** host halves of the `bios-dev-full` diagnostic pairs
  (`test_startup`, `test_reset_entry`, `test_cd_media_state`,
  `test_cdimage`, `test_cdread`, `test_cdload_exec`, `test_cdreset_bus`,
  `test_cdda_verify`).
- **L3 (default):** everything else — shared/probe/linked sources,
  future `slot_*.s`, spec ledgers, tools, `Makefile`, workflows, docs,
  host/integration support files, the 63 unimplemented API slots
  (contract-table coverage only), and all CI-executed scripts.
- **Fixtures row:** `tests-fixtures` covers `tests/fixtures/*.s`
  (assembled/probe sources, L3); data fixtures (`*.json`/`*.cfg`/`*.tsv`)
  fall through to the fail-closed fallback (also L3), so the map never
  names a guarded fixture and never trips not-wired negative tests.
- **L4 (1 row):** 16 scripts with no workflow coverage
  (`api-unimplemented`, `api-entry`, `slot02-return`, `public-dev-gap`,
  `irq-postaccept-boundary`, `routine-real-core` set, `cdra-compare`,
  `cd-base`, `cdda-scenarios`, `cdsubq`, `cd-stat-observation`) plus the
  `dev-mul16s-sweep --full` grid (the only sweep outside required CI).

## 4. CI classes (entry-level `required_ci` + `ci_class`)

- **Required-CI** (`required_ci:true`): backs a Required Check —
  `ci.yml` (`host-tests`, `integration-direct`, `rc-0..3`,
  `static-release`, `verify-full`+`verify`+`guard-verify`),
  `api-source.yml` (`bios-dev-full`+`bios-dev`+`guard-bios-dev`),
  `core-bcdbin.yml` (`bcdbin-core`+guard), `core-harness.yml`
  (`core-harness`+guard only).
- **CI-nonRequired** (`required_ci:false`): runs in CI every PR but backs
  no Required Check — the 6 non-guard `core-harness.yml` jobs (§5),
  `pages.yml`; `nightly.yml` is **push-only** (schedule/dispatch on
  `main`, no PR trigger).
- **CI-outside-optin** (`required_ci:false`): make target exists, no
  workflow invokes it — `api-unimplemented-integration`,
  `api-entry-integration`, `slot02-return-integration`,
  `public-dev-gap-integration`, `dev-mul16s-sweep`, `routine-real-core`,
  `irq-postaccept-boundary`, `dev-cd-base/cd-stat-observation`,
  `dev-cdda-scenarios`, `dev-cdsubq`, `dev-cdra-compare`, `dev-g5*`.
  Exclusion mechanism is the pinned guard allowlist or an explicit
  "Not run by CI" Makefile comment (needs a main-only change to connect).
- `make_transitive` records, per target, the ordered expansion and the
  `ci_jobs` (job + class) that execute it, so transitive calls
  (`test-real-core`→`build`+`integration`+`integration-test`;
  `integration`→21 units; `dev-test`→`dev-rom`+`test_api*`+`test_ma*`+
  `dev-integration`; `dev-integration`→`dev-rom`+18 scripts+
  `dev-bcdbin-sweep`+`dev-sweep`) are enumerated, not implied.

## 5. Corrections applied here (Issue #853 ledger gaps in survey #850)

1. **`core-harness.yml` job count:** 8 jobs total — 7 test-executing
   (`core-harness` 30 min + `dev-boot-internal`, `experimental-bios-boot`,
   `dev-ipl-boot`, `ipl-loader`, `synthetic-track-select` 30 min each +
   `dev-bios-ipl-loader` 45 min) + 1 guard (`guard-core-harness`
   10 min). Only `core-harness`/`guard-core-harness` back Required
   Checks; the other 6 are CI-nonRequired. The survey listed 5.
2. **`tests/host/test_ci_sharding.py` exists** (post-#848): the sharding
   manifest + fail-closed aggregation has host-side negative tests; the
   map's `tools-selection-logic` row pins it.
3. **Serial vs parallel build:** pre-#848 serial CI ran one
   `setup`→`build`; post-#848 each of the 8 parallel jobs runs its own
   serial `setup`+`build` (redundant builds) and only the tests are
   sharded — a wall-time win, not a CPU win (`ci_facts.build_model`).
4. **Host 9m59s double-run:** `host-tests` runs `make test` (≈9m59s in run
   `45990514643`) **and** `ci_sharding.py run-host` (a second full host
   run emitting the evidence manifest). The map records both.
5. **Wall vs CPU/runner separation:** CI times are runner wall-clock
   (`ubuntu-24.04`, cache/toolchain dependent); local timings are not
   comparable (`ci_facts.wall_vs_cpu`).
6. **`integration` unit count:** 21 units (Makefile recipe =
   `DIRECT_UNITS`, parity-checked), not 22.

## 6. Safety invariants (pinned by validation tests, unchanged)

6 Required Checks (`bios-dev`, `verify`, `guard-bios-dev`,
`guard-verify`, `guard-core-harness`, `guard-bcdbin-core`); reference-JSR
ALLOWED 0/81 (runner NONE); ABI VERIFIED 0/81; all 81
`api_contracts.json` entries `NOT_STARTED`/`NOT_RUN`;
`dev_implementations.json` all `NOT_READY`, `release_eligible=false`;
`make release` BLOCKED (exit 1), dev release gate exit 3 + `BLOCKED:`
only; docs-only fast path disabled; no `./rom` access.

## 7. Validation evidence

- RED: with the map absent, `python3 -m unittest
  tests.host.test_test_staging_map` → 17 failures (0 errors, 0 skipped;
  only the 3 map-independent tests pass: the 2 ledger/gate invariant
  tests plus the synthetic enumeration-hygiene test).
- GREEN: with the map present → `Ran 20 tests ... OK` in
  ~0.1–0.2 s local; the `./rom` media tests PASS presence-independently
  (never SKIP; tracked-files-only enumeration hygiene + synthetic
  fixtures only, no `./rom/` existence, listing, or byte asserts).
- `git diff --check`: clean (new files only, no whitespace errors).
- `make check`: PASS (unchanged static gate; see run log for timing).
- `git status --short`: only the 3 added files (plus the branch itself).

## 8. Draft PR text (do NOT merge/close; review only)

> Title: `Phase 1 staging map v0 (docs + JSON + validation tests; no
> CI/Makefile/test-body changes)`
>
> Body: Adds the machine-readable test-staging map v0
> (`tools/test_staging_map.json`, 65 entries + 81 API-slot rows + 55
> transitive Makefile rows), its human companion
> (`docs/test-staging-map-v0-850.md`), and fail-closed validation tests
> (`tests/host/test_test_staging_map.py`, 20 tests). Applies the Issue
> #853 corrections (§5). Safety invariants unchanged (6 Required, JSR
> 0/81, VERIFIED 0/81, `release_eligible=false`, docs-only disabled, no
> `./rom` access). Evidence: new-test RED→GREEN, `git diff --check`
> clean, `make check` PASS.
>
> Labels: `documentation`, `phase-1`. Reviewers: Owner (merge approval
> required before any Phase 2 work).

---

*Provenance: original analysis (MIT). No proprietary content. No
`./rom` access (presence-independent: tracked-files-only enumeration,
no existence, listing, or byte reads). No emulator runs; timings are
host-side file checks only.*
