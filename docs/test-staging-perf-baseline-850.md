# Phase3 perf baseline finalization (Issue #850, Task C recovery)

> **Scope:** measurement + docs only. No Makefile / CI / Required-Check / test-body
> changes. No `./rom` access (`ls rom/` → no such directory, re-verified this
> session). No command run exceeded 60 s in this session; heavy reruns were
> explicitly NOT performed. All heavy timings below are **already-collected**
> values from the prior measurement session log
> (`/tmp/pi-subagents-uid-1000/async-subagent-runs/db132a92-fa54-42cc-9183-63e4b65ee60a/output-0.log`),
> reused verbatim — not re-measured, not extrapolated.
> **Base:** `2399a50b`. **Branch:** `agent/test-staging-perf-20261128`.
> **Worktree:** `/tmp/pce-staging-c-perf` (this worktree only).
> The project is not yet a compatible System Card BIOS; nothing in this
> baseline implies otherwise.

## 1. Environment (this session's light verification)

| Item | Value |
| --- | --- |
| Python | 3.12.3 (`python3 --version`) |
| CPU | 8 vCPU (`nproc` = 8) |
| `TOOLS_DIR` | `/tmp/oss-pce-cd-bios-tools-1000`, pinned tools **cached** (actionlint v1.7.7, cc65 `71746c8`, Geargrafx `b49ae82a` + debugger build) |
| `JOBS` | unset (empty; Makefile default) |
| Base / branch | `2399a50b` / `agent/test-staging-perf-20261128` |
| `git status` | clean before and after (only this new untracked doc added) |
| `git diff --check` | clean (exit 0) |

Cache-state caveat: every LOCAL timing below assumes the warm-cache state
above. Cold `make setup` (fresh toolchain fetch/build) was **not** measured
here and belongs to #849. Do not compare these numbers against cold-CI
runners.

## 2. Measurement table (already-collected; NOT re-run this session)

Source: prior-session log `output-0.log` lines 2241–2333 (section headers
`=== ... ===` + `elapsed=` + `Ran N tests in ...` lines). `elapsed=` is
**wall time** of the timed wrapper; `cpu=` is the wrapper's process-CPU
estimate (≈100 % ⇒ single-core saturated ⇒ CPU time ≈ wall time for that
unit). unittest `Ran … in …` is in-process test time (excludes discovery /
interpreter startup). None of these is runner-billable time (see §3).

| # | Unit (log section) | Wall (`elapsed=`) | CPU | unittest / result |
| --- | --- | --- | --- | --- |
| M1 | `python3 tools/contracts.py` | **0.05 s** | 98 % | `PASS: 81 candidate contracts consistent; BIOS verification NOT_RUN` |
| M2 | `python3 -m unittest tests.host.test_api_contracts -v` | **0.09 s** | 78 % | `Ran 13 tests in 0.001s`, OK |
| M3 | `make dev-rom` | **0.90 s** | 103 % | DEV ROM generated (256 KiB, 81 slots), not boot/retail verified |
| M4 | `python3 tools/check.py` | **2.59 s** | 100 % | 4× `PASS` guard/syntax lines |
| M5 | `make check` (contracts + check.py + c3 + verify_build_diff) | **2.78 s** | 100 % | exit 0; `BLOCKED`/`NOT_STATIC` tri-state lines are expected output, not failures; `PASS 724 build-diff audit` |
| M6 | single DEV API `MA_MUL8U` (18 cases, real Geargrafx DEV ROM) | **4.84 s** | 100 % | `PASS … 18 cases …; no-CLD mutant rejected 3/3` |
| M7 | host pattern `test_api*` | **5.80 s** | 104 % | OK (count line in rerun: 313 tests, see M9) |
| M8 | host pattern `test_ma*` | **30.12 s** | 99 % | `Ran 142 tests in 30.010s`, OK |
| M9 | rerun: 313 tests | **4.555 s** (unittest) | — | OK (corresponds to the `test_api*` pattern rerun) |
| M10 | rerun: 142 tests | **31.181 s** (unittest) | — | OK (corresponds to the `test_ma*` pattern rerun) |
| M11 | `tests/integration/test_g5_2base.py` alone | **124.82 s** (`elapsed=`); **124.716 s** unittest | — | `Ran 27 tests`, OK |
| M12 | G5 subset (16 files: `test_dev_g5_profile`, `test_g5_2base`, `test_g5_cdread_commercial`, `test_g5_chain_7api_24_36`, `test_g5_dotmod_24`, `test_g5_expD_next_probe`, `test_g5_integrated_chain`, `test_g5_irqon_2c`, `test_g5_memopen_4a`, `test_g5_progress`, `test_g5_rcroff_2b`, `test_g5_satclr_36`, `test_g5_scrmod_25`, `test_g5_sprdma_35`, `test_ipl_loader_g5_4a_793`, `test_ipl_loader_g5_800`) | **TIMEOUT after 1500 s** (killed, exit 143) | — | not OK — timeout cause recorded (§6) |
| M13 | `block-transfer-matrix` (direct script) | **aborted by operator** (`Command aborted`) | — | unmeasured in this baseline; survey reference 40.0 s / 1017 applied + detected retained as reference only |

Survey-era references reused unchanged (not this-baseline measured):
host full LOCAL `make test` 309.7 s (5939 tests); direct-script smoke 0.27 s,
st-bypass 4.0 s, st-boundary 4.7 s; `make build` (incremental, cached) 0.07 s.

## 3. Wall vs CPU vs runner-usage vs billing (do not conflate)

- **Wall time** = elapsed time a developer waits. Local units above use the
  timed wrapper's `elapsed=`. CI run-wall below is
  (`run updated_at` − `run_started_at`/first-job-start) as reported by the
  Runs/Jobs API; the ≈29 m / ≈12.5 m / ≈14.5 m figures are **reused as
  reported** (reference), not re-derived here.
- **CPU time** = wall × `cpu%`. Local light units are ≈1 core
  (`cpu=` ≈ 100 % ⇒ CPU time ≈ wall time for that unit). `cpu=104 %` (M7)
  reflects brief multi-process overlap (ROM rebuilds in tmp dirs), not a
  parallel speedup. **CI CPU time is unmeasured** (no per-step CPU % in the
  Jobs API) — never quote it.
- **Runner-usage (observed, this session)** = Σ per-job
  (`completed_at` − `started_at`) from `gh api .../actions/runs/<id>/jobs`,
  computed this session (method recorded; raw timestamps in §4). This is a
  usage approximation, **not billing**:
  - serial `45941187061`: Σ ≈ **1775 s (≈29.6 min)** — dominated by the
    single `verify-full` job (1752 s); scope 6 s + verify 5 s +
    guard-verify 12 s.
  - parallel-init `45990514643`: Σ ≈ **2785 s (≈46.4 min)** (host-tests
    599 s; integration-direct 498 s; shards 241/348/455/413 s;
    static-release 104 s; verify-full 107 s; scope 5 s; guard-verify 11 s;
    verify 4 s).
  - safety-fixed `46011185730`: Σ ≈ **2461 s (≈41.0 min)** (host-tests
    505 s; integration-direct 517 s; shards 238/238/332/387 s;
    static-release 102 s; verify-full 106 s; scope 11 s; provenance 12 s;
    guard-verify 10 s; verify 3 s).
- **Billing is unknown / unmeasured.** GitHub bills with per-job rounding,
  per-OS multipliers, and minimums that this Σ does not model; no billing
  API was queried. Do not present Σ job-wall as an invoice figure.
- **Reading (observed instance only, not a law):** in *these three runs*,
  summed job-wall was higher under parallelism (29.6 → 46.4 / 41.0 min)
  while run-wall was lower (≈29 m → ≈12.5 m / ≈14.5 m). Parallelism does
  not *necessarily* raise total compute in general — that depends on setup
  duplication, caching, and scheduling. The wall reduction below is a
  latency finding on an identical test set (no test omitted); it is not a
  compute saving and must never be described as a test-set reduction.

## 4. CI comparison: old-serial vs parallel-init vs safety-fixed

All three are full-scope runs of the **same test set** under the same
Required names; differences are orchestration, not coverage. SHA/run/attempt
binding is enforced by `verify-full` (#848 mechanism): an old HEAD's result
must never be claimed for a new HEAD.

Evidence status used below: **measured** = observed this session via
read-only `gh api` (value + method recorded); **reused** = already-collected
or as-reported value retained verbatim as reference; **estimated** =
arithmetic/inference over measured values; **unconfirmed** = cited without
in-worktree provenance; **unmeasured** = no data (never quote as a number).
Run-wall figures (≈29 m / ≈12.5 m / ≈14.5 m) are **reused as reported**;
SHA / attempt / event / branch / workflow / job timestamps are **measured**
this session; percentages are **estimated**; billing/CI-CPU are
**unmeasured**; the 23 m 30 s serial variant is **unconfirmed**.

| Run | Wall (reused as reported) | SHA / run / attempt binding (measured this session) |
| --- | --- | --- |
| old serial baseline | **≈29 m 09 s** run-wall (reused as reported; dispersion noted: 23 m 30 s variant reported elsewhere — **unconfirmed**, no run id/SHA in this worktree). Reused breakdown: setup ≈1 m 20 s; host ≈3 m 51 s (5935 tests); real-core ≈23 m 50 s (715 unittest = 982.107 s + direct-script group) | **measured** via Runs API: run `45941187061` (run_number 2528), `run_attempt: 2`, HEAD SHA `145b182e808e49dc50ee922062186618ce8bd249`, branch `agent/g5-rcroff-2b-20261127`, event `pull_request`, workflow `.github/workflows/ci.yml`, conclusion `success`. Command/conditions: CI workflow dispatch (not a single shell command); pre-#848 serial `verify-full` body; measured job timestamps: `verify-full` 05:14:53→05:44:05Z (1752 s), scope/verify/guard-verify ≈6/5/12 s; Σ job-wall ≈1775 s (method §3) |
| parallel-init (#848) | **≈12 m 33 s** run-wall (reused as reported: host 9 m 59 s / integration-direct 8 m 18 s / shards 4–7 m / static-release 1 m 44 s / verify-full aggregation 1 m 47 s). ≈57 % wall reduction (**estimated**), identical test set and Required names | **measured** via Runs API: run `45990514643` (run_number 2547), `run_attempt: 1`, HEAD SHA `fb1699c36e9995dbf27540ebab1cf44e209498cd`, branch `agent/846-ci-parallel-sharding`, event `pull_request`, workflow `.github/workflows/ci.yml`, conclusion `success`. Command/conditions: CI workflow dispatch; #848 sharded body; measured job timestamps 11:26:39→11:39:09Z (see §3 per-job list); Σ job-wall ≈2785 s (method §3) |
| safety-fixed | **≈14 m 32 s** run-wall (reused as reported) | **measured** via Runs API: run `46011185730` (run_number 2550), `run_attempt: 1`, HEAD SHA `2392014f9cac9d00da449633a139470f168fd288`, branch `agent/846-ci-parallel-sharding`, event `pull_request`, workflow `.github/workflows/ci.yml`, conclusion `success`. Command/conditions: CI workflow dispatch; safety-fixed sharded body + `provenance` job. Per-job breakdown **measured** this session (Jobs API, previously recorded as unavailable): scope 11 s; provenance 12 s; host-tests 505 s (13:58:16→14:06:41Z); integration-direct 517 s (13:58:36→14:07:13Z); real-core-shards 387/238/332/238 s; static-release 102 s; verify-full 106 s; guard-verify 10 s; verify 3 s; Σ job-wall ≈2461 s (method §3) |

Reading (wall finding retained; compute claim corrected): #848 parallelism cut run-wall ≈57 % (**estimated**: 29 m → 12.5 m) with zero test
omission; the safety-fixed run is ≈2 m longer wall than parallel-init (≈14.5 m
vs ≈12.5 m) while remaining ≈50 % below serial (**estimated**). In *these
three runs* summed job-wall was higher under parallelism (29.6 → 46.4 /
41.0 min, **measured** Σ per §3 method) while run-wall fell — an observed
latency-vs-usage tradeoff in this instance, not proof that parallelism must
raise compute. Billing remains **unmeasured** (§3); wall savings are latency
savings, not compute savings and not a test-set reduction.

## 5. Repro steps (each < 60 s; heavy units intentionally excluded)

```sh
cd /tmp/pce-staging-c-perf            # this worktree only; base 2399a50b
git branch --show-current             # expect agent/test-staging-perf-20261128
export TOOLS_DIR=/tmp/oss-pce-cd-bios-tools-1000   # warm cache assumed; see §1
python3 tools/contracts.py            # expect PASS 81 contracts, ~0.05–0.11 s
python3 -m unittest tests.host.test_api_contracts  # expect OK 13 tests, ~0.1 s
make dev-rom                          # expect DEV ROM generated, ~0.9–1.3 s
make check                            # expect exit 0, ~2.8–4.2 s
git diff --check                      # expect clean, instant
```

This session's light re-verification (GREEN, no heavy rerun):
contracts.py 0.108 s PASS; `test_api_contracts` 0.103 s OK (13 tests);
`make dev-rom` 1.266 s PASS; `make check` 4.137 s exit 0
(`BLOCKED`/`NOT_STATIC` tri-state lines present as expected);
`git diff --check` clean.

## 6. Timeout cause (recorded, not retried)

- **Measured facts:** the 16-file G5 subset (M12) hit the 1500 s command
  timeout and was killed (exit 143) — **measured** (this-baseline wrapper
  outcome); `test_g5_2base.py` **alone** costs ≈125 s wall (M11: 124.82 s
  `elapsed=` / 124.716 s unittest, 27 tests OK) — **measured**.
- **Estimate (not a proven root cause):** the subset composition was *likely*
  too heavy for interactive budgets, with `test_g5_2base.py`-class files as
  the plausible dominant cost — **estimated**. No per-file timing inside the
  timed-out subset run was recorded, so the 125 s single-file number does
  **not** prove the 1500 s timeout's composition-level cause; the
  contribution breakdown of the killed run is **unmeasured**. The subset was
  abandoned without workaround (a scheduling decision), which the estimate
  supports but does not compel.
- `block-transfer-matrix` (M13) was operator-aborted before producing a
  this-baseline number (survey-era 40.0 s retained as **reused** reference
  only) — **unmeasured** this baseline.
- Consequence for staging (selection, not omission): L2 (≤5 m) must never
  blindly include the G5 group or the heavy mutation matrix; those units
  stay L3. L3 still runs everything (§7).

## 7. L0/L1/L2 feasibility WITHOUT omitting tests

No test is omitted from the full gate at any stage: L0–L2 are ordered
subsets for fast feedback; L3 always runs the current full set (local L3 +
all 6 Required Checks). Feasibility below is arithmetic over measured units
only.

- **L0 (≤30 s): FEASIBLE.** Worst-case full light path M5 (`make check`
  2.78–4.14 s) + M3 (`dev-rom` 0.90–1.27 s) + one host file M2 (0.09–0.10 s)
  ≈ **< 6 s wall**, ~5× inside budget. File-scoped subsets (Q1–Q5 per survey
  §2) execute fewer tests and are therefore cheaper by construction.
- **L1 (≤2 m = 120 s): FEASIBLE for 1–2 target APIs.** Single-API example:
  M3 (0.9 s) + M6 (`MA_MUL8U` 4.84 s) + M9 (`test_api*` 4.6–5.8 s) ≈ **≈12 s
  wall**. Combined host patterns M7+M8 ≈ 36 s wall still leave 84 s headroom
  for 1–2 dev-integration scripts. Caveat: `test_ma*` alone is 30–31 s (M8,
  M10) — an L1 bundling many `test_ma*`-class patterns plus several
  real-core scripts approaches but does not breach 120 s on measured numbers.
- **L2 (≤5 m = 300 s): FEASIBLE only with heavy-unit exclusion (selection,
  not omission).** Light L2 composition fits easily: `test_ma*` 30–31 s +
  st-boundary 4.7 s + st-bypass 4.0 s + `make check` 2.8–4.2 s ≈ **≈42 s**.
  But a single `test_g5_2base` (124.8 s) consumes ≈42 % of the budget, and
  the full G5 subset exceeds 1500 s (M12). Therefore: L2 may include at most
  one G5-class file alongside light units, or — the recommended default —
  keeps G5-full and `block-transfer-matrix` in L3. L3 coverage is unchanged
  either way; nothing is omitted, only scheduled later.

## 8. Unmeasured items (SKIP / BLOCKED — not PASS)

| Item | Status | Reason |
| --- | --- | --- |
| host full LOCAL (`make test` full) | **SKIP** | not run in this baseline (exceeds 60 s command budget); survey 309.7 s cited as reference only |
| real-core full LOCAL (`make test-real-core`) | **SKIP** | not run; CI serial reference ≈23 m 50 s only |
| `make setup` fresh (uncached toolchain fetch/build) | **SKIP** | warm-cache only here; #849 scope |
| combined `make dev-test` recipe as one unit (dev-rom + `test_api*` + `test_ma*` verbose) | **SKIP** | patterns measured separately (M7–M10, sum ≈36 s wall); combined single-run number not recorded — do not quote 36 s as measured |
| L2 subset composition (G5 + direct scripts as one staged run) | **BLOCKED** | G5 subset timed out at 1500 s (M12); no composed L2 wall exists |
| shared-code path / docs-only path timing | **SKIP** | not run; docs-only fast path remains disabled (survey §1.2), so there is no fast path to time |
| `block-transfer-matrix` this-baseline | **ABORTED** (unmeasured) | operator-aborted (M13); survey 40.0 s is reference only |
| commercial-disc / retail-game compatibility | out of scope | no `./rom` access; synthetic diagnostics only (`docs/testing.md` boundary) |

## 9. #848 host double-run investigation (read-only; no deletion)

Read scope only: `ci.yml` L76–78 and `tools/ci_sharding.py` L545–570.
No file was modified.

- `ci.yml` L76 (`make test`) runs the full host suite as the `host-tests`
  job body; L78 (`python3 tools/ci_sharding.py run-host --evidence-out …`)
  immediately re-runs the full host suite for evidence.
- `tools/ci_sharding.py` `run_host()` (L545–570): `loader.discover("tests/host")`,
  walks the suite to record `planned_ids`, then `suite.run(collector)` —
  a second **full execution**, not a replay of `make test` results — and
  writes `planned_ids` / `executed` / `failures` / `errors` / `duration_s` /
  gate states to evidence.
- Finding: structurally **two full host executions in one job** — an
  independent optimization candidate (evidence could in principle be derived
  from a single execution). Quantification is **not** claimed here: the
  parallel-init host job total (9 m 59 s) bundles both executions and the
  split was not measured. Any change would touch CI/test-evidence paths and
  is out of scope for this task (Makefile/CI/test bodies frozen; Required
  names unchanged).

## 10. Validation this session

- `python3 tools/contracts.py` → PASS (0.108 s)
- `python3 -m unittest tests.host.test_api_contracts` → OK, 13 tests (0.103 s)
- `make dev-rom` → PASS (1.266 s)
- `make check` → exit 0 (4.137 s; `BLOCKED`/`NOT_STATIC` lines expected tri-state output)
- `git diff --check` → clean
- No Makefile / CI / test-body edits; `git status` shows only this new doc as untracked.

## 11. Draft PR (do NOT merge/close; Depends-on Task-A map; no Phase 4)

- **Title:** `docs(#850): Phase3 perf baseline finalization (Task C recovery, light-only)`
- **Base:** `main` ← **Head:** `agent/test-staging-perf-20261128` (base `2399a50b`)
- **Scope:** adds `docs/test-staging-perf-baseline-850.md` only. No build, CI,
  Required-Check, test-body, or tooling changes.
- **Depends-on (Task-A map):** none required. This docs-only PR has **no
  technical dependency** on #856 or Task-A mapping work; it is independently
  reviewable and mergeable on its own evidence. Any sequencing after Task-A
  mapping for issue-number / milestone linkage is administrative convenience
  only, not a prerequisite — this PR makes no product, architecture, or
  scope decision and proposes no Phase 4 implementation.
- **Do NOT merge or close** per task authority; review only.
- **Evidence:** §2 table (already-collected M1–M13) + §5 light GREEN reruns +
  `make check` exit 0 + `git diff --check` clean.
- **Residual risks:** LOCAL numbers are warm-cache, single-host (8 vCPU,
  Python 3.12.3) — not transferable to cold CI runners; `safety-fixed`
  per-job breakdown unavailable in-worktree; combined `dev-test` and composed
  L2 walls remain unmeasured (marked SKIP/BLOCKED, §8); G5-full /
  matrix heaviness bounds L2 composition (§6–§7).
