# Test-staging wrappers, Phase 2 design + prototype (Issue #852)

> **Scope:** design + prototype + host unit tests only. No change to
> `Makefile` target bodies, `.github/workflows`, Required Checks, test
> bodies, `CLAUDE.md`, or `AGENTS.md`. This branch adds exactly two files:
> `tools/test_staging_select.py` (prototype selector) and
> `tests/host/test_test_staging_select.py` (its unit tests).
> This document is the `test-staging-wrapper-design-852.md` deliverable.
>
> **Depends-on Task-A:** the change->test correspondence map (Phase 1,
> §3 of the #850 survey) is owned by Task-A. Its canonical format is NOT
> defined here. The prototype reads maps only through the `MapLoader`
> interface and ships a provisional envelope for prototype tests; any map
> whose format tag is not recognised falls back to L3 fail-closed.
>
> **Evidence boundary:** prototype + synthetic-fixture unit-test evidence
> only. No `./rom` access (`rom/` is absent in this worktree), no commercial
> media, no real-core runs, no Required-CI run. L0-L2 outputs below are
> PARTIAL by construction and never MERGE_READY evidence.

Related: #850 (survey/design, §§3-5), #851 (survey doc), #852 (this Phase 2
acceptance), #848 (CI sharding, unchanged), #849 (toolchain setup, unchanged).

## 1. What the survey fixes (§§3-5 of test-staging-survey-850)

- §3.1 adopts方式 A (static correspondence table) as the selection source
  of truth; §3.2 sketches the initial table; §3.3 lists the fail-closed
  L3-escalation conditions.
- §4 proposes five thin wrappers (`test-quick/focused/impact/full/extended`)
  that call **existing commands only** with `API=`/`BASE=` arguments.
- §5 restricts AI agents to mechanical table lookup; MERGE_READY needs
  local L3 **and** all 6 Required Checks.
- Issue #852 (independent review 6089974846 MEDIUM 1 / FOLLOW_UP) moves the
  uncommitted-input handling into Phase 2 acceptance: committed, staged,
  unstaged, untracked, delete/rename, BASE-fetch failure, diff failure,
  map missing/inconsistency, and input-identity for evidence reuse.

This document turns those decisions into a buildable contract: wrapper
definitions (§2), the selector specification (§3), the map-loader interface
and Task-A dependency (§4), the #852 acceptance matrix (§5), the evidence
and reuse rules (§6), and the validation record (§7).

## 2. Wrapper definitions (existing-command callers only)

Names follow the existing `make <verb>-<target>` convention. Each wrapper
takes at most `API=` (which API/module is in focus) and `BASE=` (default
`origin/main`, resolved via merge-base exactly like `tools/check.py`'s local
path). Wrappers add no verification logic, no thresholds, no new gates.

| Wrapper | Level | Existing commands it calls |
| --- | --- | --- |
| `make test-quick` | L0 (~30 s) | `make check` (contracts + syntax + whitespace + diff audit, ~2.5-5 s) plus the changed host modules as `python3 -m unittest tests.host.test_<name>` |
| `make test-focused API=<name>` | L1 (~2 min) | host pattern `python3 -m unittest discover -s tests/host -p 'test_<api>*.py' -v` (same pattern family as `dev-test`), `make dev-rom`, the 1-2 matching `tests/integration/geargrafx_dev_<api>.py` runs (same scripts `dev-integration` calls) |
| `make test-impact BASE=<base>` | L2 (~5 min) | selector output: related host modules + the related `integration` sub-targets (e.g. `st-boundary`, `st-bypass`) + `make check`; heavy sweeps excluded by the map |
| `make test-full` | L3 (full regression) | unconditional existing sequence: `make setup` -> `make build` -> `make test` -> `make test-real-core` -> `make check` -> release verification (`make build MODE=release` + `cmp` + `tools/build.py --verify`) -> `rom_provenance` reproduce set -> `dev-rom`/`dev-test`/`dev-integration` (includes `dev-bcdbin-sweep`/`dev-sweep`, §1.3(f) of the survey) + `dev-release-gate-blocked` -> applicable core-harness/BCDBIN gates; Required CI (6 checks) still mandatory and separate |
| `make test-extended` | L4 (manual opt-in) | long sweeps explicitly NOT run by CI (`dev-mul16s-sweep` and future opt-ins); never a Required-Check substitute; `dev-bcdbin-sweep`/`dev-sweep` are L3, not L4 |

Proposed recipe shape (illustrative; **Makefile wiring is deferred** to
Phase 3 after the Task-A map lands and Owner approval — this branch does
not edit the `Makefile`):

```make
BASE ?= origin/main
test-impact:
	$(PYTHON) tools/test_staging_select.py --base "$(BASE)"
	# run the printed existing commands; the printout is the audit record
test-focused:
	@test -n "$(API)" || { echo 'API=<name> is required' >&2; exit 2; }
	$(PYTHON) tools/test_staging_select.py --base "$(BASE)" --api "$(API)"
```

Every command the selector prints exports `OSS_PCE_REQUIRE_CC65=1`
(`tools/environment.py` gate), so a missing cc65 FAILs instead of
SKIPping — a missing tool is never PASS.

## 3. Selector specification (`tools/test_staging_select.py`)

The selector prints a plan and runs nothing. Pipeline:

1. **Collect** via `collect_changes(repo, base)`: shallow-history refusal
   (same rule as `check.py`), local `BASE` resolution, `merge-base`,
   committed diff (`merge-base..HEAD`), staged diff (`--cached`),
   unstaged diff, untracked scan (`status --porcelain -z`). All diffs use
   `--no-renames`, so a rename always arrives as delete+add (§5, row 5).
   `--fetch` performs an explicit fetch first; the default is read-only
   (no network).
2. **Load** the map through `MapLoader` (§4).
3. **Select** via `select()`: every tracked change must match at least one
   rule; the level is the maximum matched level (fail-closed upward).
   `--api NAME` additionally requires the `apis` table entry and treats
   out-of-scope files as unclassified (-> L3).
4. **Render** via `render()`: level, fallback yes/no + reason, PARTIAL
   marker for L0-L2, LOCAL-L3 vs Required-CI block for L3, SKIP-policy
   line, SELECTED (with full argv + env), UNSELECTED (remainder of the L3
   sequence for auditability), per-file REASONS, evidence fingerprint,
   reuse rule. `--json` prints the same structure as JSON.

Fail-closed core (any of these selects L3, exit 0, reason printed):

- BASE unresolvable (ref absent) / shallow history / fetch failure /
  merge-base or any diff failure;
- map missing, unreadable, wrong format tag, bad level, bad command;
- unclassified file, untracked file, unknown `--api` value;
- empty change set or empty L0-L2 command set (a 0-test run is never PASS);
- any map rule at L3 (shared code, ABI, guard/tooling changes).

L4 is manual-only (`--level L4`); auto-selection never yields L4.

## 4. Map-loader interface and Task-A dependency

```python
class MapLoader:
    def load(self, path): ...  # -> (rules, apis) or raise MapError
```

`ProvisionalJsonMapLoader` implements this interface for the prototype-test
envelope `test-staging-impact-map/provisional-0` with three command kinds:

- `{"make": "<target>"}` — target must be in `KNOWN_MAKE_TARGETS`
  (existing targets only; anything else is inconsistent -> L3);
- `{"unittest": "<pattern>", "suite": "host"|"integration"}` — existing
  discover suites only;
- `{"script": "tests/...py"}` — must be an existing repository script
  (path-escape or missing file is inconsistent -> L3).

Rules carry `pattern` (fnmatch), `level` (L0-L3), `commands`, `reason`
(audit text), and an optional `api` tag. Depends-on Task-A: the canonical
map format, the v0 content (§3.2 of the survey), and ownership/review live
in Task-A. Until then any non-provisional `format` tag raises
`MapError` -> L3 (covered by `test_unknown_format_tag_falls_back_to_l3`).
Nothing in the selector hardcodes Task-A's schema: `test_map_loader_is_an_
interface` proves a third-party loader plugs in through the interface.

### 4a. Canonical loader (interim, pre-#856-merge)

`CanonicalJsonMapLoader` (same file, `match_mode = "first-match"`)
validates the Task-A v0 envelope strictly and converts it to selector
rules:

- identity: `name == "test-staging-map-v0"`, `schema_version == 1`, any
  `format` key rejected (provisional envelope is never canonical), 40-hex
  `base_commit` fingerprint, Required Checks exactly the pinned 6;
- entries: `exact` / `exact-list` (split per path) / `glob` with non-empty
  `match`, level in L0-L4, known `ci_class`, bool `required_ci`, full
  `tests` object, non-empty reason; unique ids; `fallback` is the last
  entry with `match == "**"` at L3;
- `api_slots`: exactly 81 rows covering slots 0-80 once, unique names,
  `dev_source` path or null; slot-number identifiers (`63`, `0x3F`,
  `$3F`, …) resolve to the slot name via `resolve_api()`;
- commands: `dev_targets` must exist in the live `Makefile`, `host` refs
  (`tests.host.MOD` or `tests/host/PATTERN.py`) must resolve, `script`
  entries must be existing in-repo `tests/integration/*.py`, `static_units`
  must be declared (contributing `make check`); anything else raises
  `MapError` -> L3.
- classification is first-match-wins in entry order; L4 entries escalate
  to L3 running the full local-L3 sequence (L4 opt-in commands are
  validated but discarded — never auto-selected); L3 entries with empty
  tests likewise run the full local-L3 sequence.

Dependency pin (interim): read-only fetch of
`origin/agent/test-staging-map-v0-20261128` at `cf428568` — recorded as
`CANONICAL_SOURCE_HEAD` and pinned by
`test_dependency_head_pin_is_recorded`. The loader was verified against
that revision's schema (232 rules / 81 slots load from the fetched
revision read-only). This is **not** final verification: after Task-A
(#856) integrates, the pin must advance to the merged HEAD and the
`CanonicalLoaderTests` (14 tests) plus `make check` / `make test` re-run.

## 5. Issue #852 acceptance matrix

| #852 condition | Prototype behaviour | Covering test |
| --- | --- | --- |
| committed changes | `merge-base..HEAD --no-renames` captured | `test_committed_change_is_captured` |
| staged changes | `--cached --no-renames` captured | `test_staged_change_is_captured` |
| unstaged changes | working-tree diff captured | `test_unstaged_change_is_captured` |
| untracked files | captured; unregistered by definition -> L3 | `test_untracked_change_is_captured`, `test_untracked_file_forces_l3_despite_covering_map` |
| delete / rename | `--no-renames` yields delete+add; `R`/`C` statuses rejected by assertion | `test_rename_is_delete_plus_add` |
| BASE fetch failure | `--fetch` failure (`fetch` kind) -> L3 | `test_fetch_failure_falls_back_to_l3` |
| BASE unresolvable / shallow | `base` / `shallow` kind -> L3 | `test_unknown_base_falls_back_to_l3`, `test_shallow_history_falls_back_to_l3` |
| diff failure | `diff` kind -> L3 | `test_diff_failure_falls_back_to_l3` |
| map missing / inconsistent | `map-missing-or-inconsistent` -> L3 (missing file, no `--map`, bad JSON, unknown format tag, bad level, unknown target, missing script) | 7 `MapTests` cases |
| input identity for reuse | fingerprint + strict `evidence_reusable` (unknown toolchain never reuses) | 4 `EvidenceTests` cases |
| cc65 gate preserved | every emitted command env is exactly `{OSS_PCE_REQUIRE_CC65: "1"}`; L3 keeps `make test` / `make test-real-core` | `test_require_cc65_preserved_on_every_command`, `test_l3_selection_keeps_required_gate_commands` |
| missing-tool / 0-test / unintended SKIP never PASS | empty change set -> L3; empty selection -> L3; SKIP-policy printed on every report | `test_empty_change_set_selects_l3_never_passes_empty`, `test_skip_policy_is_always_printed` |
| explicit selected / unselected / reason / fallback | `render()` sections asserted verbatim | `test_l1_output_marks_partial_and_lists_unselected` |
| L0-L2 PARTIAL | PARTIAL marker + never-MERGE_READY wording | same as above |
| local-L3 vs Required-CI | L3 block names all 6 checks and the CI-only trust boundaries | `test_l3_output_distinguishes_local_l3_from_required_ci` |
| reuse needs same HEAD/input/env/toolchain/conditions | per-field mutation refuses reuse; HEAD advance changes fingerprint | `test_any_field_mismatch_refuses_reuse`, `test_fingerprint_changes_with_head` |
| `--api` scoping | known API -> L1 subset; unknown API -> L3; out-of-scope file -> L3 | 3 `ApiTests` cases |

## 6. Evidence, reuse, and non-goals

- **Reuse rule:** a prior result may stand in for re-execution only when
  `evidence_reusable(stored, current)` holds: identical HEAD, base,
  inputs hash, Python version, platform, toolchain pins (from
  `tools/dependencies.json`), MODE, and env flags — the same principle as
  #848's run/attempt binding. Unknown toolchains never reuse. An old HEAD
  result is never claimed for a new HEAD.
- **PARTIAL:** L0-L2 reports state their level is not full regression and
  is never MERGE_READY evidence alone. MERGE_READY still needs local L3
  success **and** all 6 Required Checks (`bios-dev`, `verify`,
  `guard-bios-dev`, `guard-verify`, `guard-core-harness`,
  `guard-bcdbin-core`); Required CI is not reproducible locally (trusted
  guard, artifact handoff) and is never skipped or substituted.
- **Non-goals (unchanged):** no Makefile/workflow/Required-Check edits, no
  test-body edits, no new verification logic, no docs-only fast path, no
  commercial-media or `./rom` access, no network fetch by default.

## 7. Validation record

Prototype: `tools/test_staging_select.py` (~470 lines, stdlib only).
Tests: `tests/host/test_test_staging_select.py`, 47 tests, all synthetic
(temp git repos + injected failing runners; no cc65, no core, no network).

| Command | Result | Timing |
| --- | --- | --- |
| `python3 -m unittest tests.host.test_test_staging_select` | OK (47 tests) | ~2.5 s |
| `OSS_PCE_REQUIRE_CC65=1 python3 -m unittest tests.host.test_test_staging_select` (required gate) | OK (47 tests) | ~2.5 s |
| RED proof, mutation A (drop `OSS_PCE_REQUIRE_CC65=1` from emitted env) | FAIL (3 failures), restored -> OK | — |
| RED proof, mutation B (untracked no longer forces L3) | FAIL (1 failure), restored -> OK | — |
| `make check` (contracts + check + c3 + verify_build_diff) | PASS | ~4.8 s |

Interim canonical-loader record (pre-#856-merge, same commands plus): 14
`CanonicalLoaderTests` OK on this branch; the fetched Task-A revision
(`cf428568`, read-only) loads as 232 rules / 81 api slots; slot-number
spellings (`63`, `0x3F`, `$3F`, …) resolve to one subset.

Residual risks: (1) the provisional envelope is throwaway — Task-A must
define the canonical format and a loader for it; (2) wrapper `Makefile`
recipes in §2 are proposed text, not wired targets; (3) timings are
worktree-local (8 vCPU, warm cache) and only cover the selector unit
tests, not real-core subsets — L1/L2 subset timings stay open until
Phase 3 measures representative patterns.

*Provenance: original analysis (MIT). No proprietary content. No `./rom`
access. Pinned tools reused read-only for `make check`.*
