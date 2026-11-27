# Review-finding taxonomy → reusable regression kinds (Issue #865, Task D Round 1)

> **Round scope: survey + root-cause classification + design ONLY. No implementation.**
> Authority: this worktree only (`/tmp/pce-staging-d-generalize`,
> branch `agent/test-generalize-regression-20261128`, base HEAD `83cf842c`).
> PR branches `#856` / `#857` / `#858` were **read-only** (gh API + local
> fetched refs, `--stat` only); nothing was pushed there, merged, closed, or
> copied as canonical. No `./rom` access. No command exceeded 60 s; only
> host-unit self-checks and metadata reads were run.
> The project is **not yet a compatible System Card BIOS**; nothing here
> implies otherwise.

## 1. Sources surveyed

### 1.1 The 8 source findings (verbatim-review basis)

| Fid | Source PR / review-ID | HEAD at discovery | Severity | One-line finding |
| --- | --- | --- | --- | --- |
| F-856-1 | #856 / `6094124245` §1 | `2ab14d08` / main `d3908245` | MAJOR / MERGE_BLOCKER | `repo_files()` (`os.walk(ROOT)`) enumerates `./rom` when present; `test_no_rom_access` asserts absence → env-dependent PASS/FAIL |
| F-856-2 | #856 / `6094124245` §2 | `2ab14d08` | MAJOR / MERGE_BLOCKER | `host-api-pattern` (L1) precedes `host-guard-contracts` (L3) under first-match-wins → `test_api_commercial_media.py`, `test_api_halt_classify.py`, `test_api_release_gate.py` misclassified L1 |
| F-857-1 | #857 / `6094124363` §1 | `7038bde0` / main `d3908245` | MAJOR / MERGE_BLOCKER | L3 path returns `selected=<deduped subset>`, `partial=False`, `unselected=[]` → partial L3 misdisplayed as full; fallback itself omits `release-verify` from the local-L3 meaning |
| F-857-2 | #857 / `6094124363` §2 | `7038bde0` | MAJOR / MERGE_BLOCKER | `evidence_fingerprint()` hashes only `origin:status:path`; same-HEAD content changes reuse stale results; map/API/level/env conditions uncovered; `unknown`/`manual` HEAD not refused |
| F-857-3 | #857 / `6094124363` §3 | `7038bde0` | MAJOR / MERGE_BLOCKER | CLI defaults to `ProvisionalJsonMapLoader`; `--map` canonical never engages (`canonical loader` not started) → production path untested, silent L3 fallback hides it |
| F-857-4 | #857 / `6094124363` §4 | `7038bde0` | MAJOR / MERGE_BLOCKER | `CanonicalJsonMapLoader.load()` aliases collide: `str(number)` vs bare hex `f"{number:X}"`/`f"{number:02X}"` (`"10"` = decimal-10 vs hex-0x10); slot `level` from `apis` not enforced for `--api` minimum |
| F-858-1 | #858 / `6094124535` §1 | `56ad5ad6` / main `d3908245` | MEDIUM / MERGE_BLOCKER | "runner-minutes are strictly larger" / "necessarily rose" unproven from parallelization alone; wall vs CPU vs runner-usage vs billing conflated |
| F-858-2 | #858 / `6094124535` §2 | `56ad5ad6` | MEDIUM / MERGE_BLOCKER | SHA/run/attempt binding claimed but comparison rows lack concrete SHAs + attempt numbers; §6 single-file 125 s does not establish the 1500 s timeout root cause |

### 1.2 Re-reviews (repeated-review cost evidence)

| Re-review | Verdict | What it proves |
| --- | --- | --- |
| #856 / `6094302201` | CHANGES_REQUESTED, findings unmodified | F-856-1/2 survived to re-review HEAD unchanged |
| #857 / `6094302428` | CHANGES_REQUESTED, 4 findings unmodified | F-857-1..4 survived to re-review HEAD unchanged |
| #858 / `6094302641` | CHANGES_REQUESTED, 2 findings unmodified | F-858-1/2 survived to re-review HEAD unchanged |

All three re-reviews record **4 workflow successes + Required 6 jobs success
at the reviewed HEAD while MERGE_BLOCKERs were present** → cross-cutting
kind X-1 (CI-green-but-undetected, §3).

### 1.3 Closely-related priors surveyed (bounded: 8 + priors, no full history)

| Prior | Relation to this taxonomy |
| --- | --- |
| PR #815 + review #821 (`5958259975`): cwd-scope defect in destructive-guard chain rule, fixed in-PR | Same KIND family as K-2/K-5: a scope/wiring defect only review caught; precedent for "review-found scoping defect → in-PR fix → regression lock" |
| PR #814 (+ #820, Issue #637): stdin-provenance guard (bare-interp + piped stdin DENY; "first wins over `-c`" precedence pin) | Precedent for K-4 (provenance/identity must be explicit; ambiguous inputs DENY) and K-2 (precedence explicitly pinned and locked by tests) |
| Issue #725 + `docs/725-failclosed-invariants.md` + `tests/host/test_725_failclosed.py` + `test_725_p10_failclosed.py` | Precedent for K-3: fail-closed invariants verified mechanically (host-only `unittest` + gate script), `release_eligible=false` held |
| `tests/host/test_artifact_guard_scope_r10/r12/r14.py`, `test_artifact_guard_repo.py` | Precedent for K-1 (scope/exclusion tests) and K-5 (repo-coupled wiring tests: real workflows must invoke the trusted-main copy) |
| `tests/host/test_api_commercial_media.py` (Issue #635) | Pattern to **reuse** for K-1 tests: synthetic `rom/` built only in `tmp` dirs, secret sentinel names prove sanitized errors, never real media |
| `tests/host/test_696_default_image_hash.py` | Pattern to reuse for K-4: pinned-hash regression style |
| `tests/host/test_api_744_ipl_sel_boundary.py`, `test_365_st_boundary_record.py`, `test_744_ipl_21bit_lba.py` | Pattern to reuse for K-6: boundary/mutant-record test style |
| PR #848 (CI parallel sharding + fail-closed aggregate) + `tests/host/test_ci_sharding.py` | Adjacent to K-7: sharding changed CI timing shape; measurement claims about it need the §3-evidence discipline of D-7 |
| `docs/test-staging-survey-850.md` (PR #851, merged) | Parent survey; this taxonomy generalizes its follow-on review findings, not its content |

### 1.4 Follow-up status observed (read-only, `--stat` only — NOT verified fixes)

- PR #856 branch has a post-review commit `3dad5dad` ("resolve Wave1 review
  blockers (media-safe enumeration, safety-rule ordering)") touching exactly
  the two reviewed files. Whether F-856-1/2 are truly fixed needs **re-review
  in that PR** — Task D must not duplicate or assume it.
- PR #858 branch has a post-review commit `0dc05b2a` referencing review
  `6094124533` (a review ID **outside** the 8 tasked findings — flagged for
  the parent, not surveyed here) touching the reviewed doc.
- PR #857 branch is unchanged since reviewed HEAD `7038bde0`; F-857-1..4 are
  presumed still open in that PR.

## 2. Reusable problem KINDs

### K-1 — Forbidden-area contact via enumeration or presence-assert (D-1)

- **Sources:** F-856-1 (#856 / `6094124245`, HEAD `2ab14d08`, MAJOR/MERGE_BLOCKER).
- **Root cause:** test helper walks the live tree (`os.walk(ROOT)`) with an
  exclusion list that omits the forbidden dir; a second test asserts the
  forbidden dir's *absence*, turning a legitimate environment into a failure.
- **Why existing tests missed it:** CI uses clean checkouts where `./rom`
  never exists, so GREEN proves nothing about non-contact (X-1). No test
  feeds a synthetic forbidden dir into the enumerator.
- **Other places this kind could occur:** any `os.walk`/`glob`/`iterdir`
  helper over the repo (artifact guard scope tests, provenance collectors,
  selector `host pattern` listing in `_canonical_host_command`, future
  staging-map validators); any "absence assert" on an opt-in-local path.
- **Mechanical detectability:** high — construct a tmp repo containing a
  `rom/`-named dir (or run enumerator against a fixture tree) and assert it
  is never listed; run the suite with and without the dir and require
  identical verdicts.
- **Best test method:** host-only `unittest`, synthetic fixture tree
  (reuse `test_api_commercial_media.py::make_rom` style: tmp dirs only,
  never `./rom`); RED = enumerator lists forbidden dir / verdict flips with
  dir presence; GREEN = tracked-files-only enumeration (`git ls-files`-style
  or explicit `rom/` exclusion) + no absence-assert.
- **Prevention acceptance criteria:** (a) no staging/selection test
  enumerates the live tree without a forbidden-dir exclusion; (b) no test
  asserts absence of an opt-in-local path; (c) media presence/absence never
  changes PASS/FAIL (only SKIP-policy-neutral or identical verdict).
- **Corresponding test:** NEW (no existing test covers enumerator exclusion;
  see reuse map §4). Follow-up: child draft CD-1.
- **If unhandled:** every future map/selector test re-risks touching private
  media on dev machines and env-dependent CI flips. Reason it may stay open:
  in-PR fix (`3dad5dad`) may already cover instance; the KIND-level test still
  warrants a home independent of that PR.

### K-2 — General rule shadows safety rule under order-dependent matching (D-2)

- **Sources:** F-856-2 (#856 / `6094124245`, HEAD `2ab14d08`, MAJOR/MERGE_BLOCKER).
- **Root cause:** first-match-wins evaluation + general pattern listed before
  exact safety rule → safety classification silently unreachable for 3 guard
  files.
- **Why existing tests missed it:** tests validated schema/coverage counts,
  not *which rule fired per safety-critical path*; CI GREEN with wrong L1s.
- **Other places:** any ordered rule list (destructive-guard chain rules —
  cf. #815 where chain inspection was deliberately placed *after* existing
  checks to preserve reason pins; stdin-guard "first wins" pins — cf. #814;
  future map `entries`, Makefile target routing, artifact-guard allowlists).
- **Mechanical detectability:** high — for each safety-critical path, assert
  the winning rule ID/level; mutation: swap two rules and require the test to
  go RED.
- **Best test method:** host-only `unittest` over the canonical map JSON:
  pin `test_api_commercial_media.py`, `test_api_halt_classify.py`,
  `test_api_release_gate.py` → L3 via the exact safety rule; plus an
  order-mutation test (swap general/safety entries → must fail).
- **Prevention acceptance criteria:** (a) exact safety rules precede general
  patterns (or equivalent priority, not bare order); (b) winning-rule
  regression pins for every safety-critical path; (c) any rule reorder that
  changes a safety verdict fails tests.
- **Corresponding test:** NEW (map file itself is new in #856; precedence-pin
  style reused from #814-guard tests). Follow-up: child draft CD-2.

### K-3 — Fail-closed display/execution gap: partial presented as complete (D-3)

- **Sources:** F-857-1 (#857 / `6094124363`, HEAD `7038bde0`, MAJOR/MERGE_BLOCKER).
- **Root cause:** L3 fallback path reuses the `Selection` shape with
  `selected=<subset>`, `partial=False`, `unselected=[]`; a subset success
  reads as full-L3 success. The fallback's own command set is not the single
  mandatory full sequence (missing `release-verify` semantics).
- **Why existing tests missed it:** unit tests covered the prototype's happy
  paths, not the invariant "L3 ⇒ one fixed complete sequence"; render text
  even disclaims the distinction, but no test enforces it on the object.
- **Other places:** any fallback/degraded path that reuses the success shape
  (L4 manual path, `GitError`/`MapError` → L3 paths, CI fail-closed aggregate
  in #848, release-gate BLOCKED vs PASS reporting).
- **Mechanical detectability:** high — assert object-level invariants:
  `level==L3 ⇒ fallback==True, partial==False` only with
  `selected == FULL_L3_SEQUENCE` rendered and `unselected==[]` meaning
  "none remainder", never a subset; feed unknown/empty/mixed inputs and
  require exactly the full sequence.
- **Best test method:** host-only `unittest` with synthetic `ChangedInputs`
  (unknown path, empty set, mixed L1+L3, zero-command map entry) → assert
  full-sequence selection; RED→GREEN on the subset-display mutant.
- **Prevention acceptance criteria:** (a) L3 always emits the single
  mandatory complete sequence; (b) no subset may carry `partial=False` +
  empty `unselected`; (c) 0-command classification is L3-never-PASS (already
  partially handled — lock it).
- **Corresponding test:** NEW (selector is new in #857; invariant style
  reused from `test_725_failclosed.py`). Follow-up: child draft CD-3.

### K-4 — Evidence identity too weak to refuse stale reuse (D-4)

- **Sources:** F-857-2 (#857 / `6094124363`, HEAD `7038bde0`, MAJOR/MERGE_BLOCKER).
- **Root cause:** `inputs_sha256` covers `origin:status:path` only — content,
  map/API/level conditions, and effective environment are outside the digest;
  `unknown`/`manual` HEADs are matchable, so refusal never triggers.
- **Why existing tests missed it:** reuse logic tested equality, not
  *discriminating power* (no differing-content/same-path case); toolchain
  `unknown` refusal was added but content-blindness was not probed.
- **Other places:** any cache/fingerprint/reuse gate (intermediate fixture
  artifacts + handoff manifests in #848, `test_696` hash pins if extended to
  mutable inputs, future staging-evidence store).
- **Mechanical detectability:** high — mutation: same status/path, different
  bytes → fingerprints must differ; `unknown`/`manual` stored-vs-current must
  refuse; drop one condition (map path, api, mode, env) → must refuse.
- **Best test method:** host-only `unittest` on synthetic repos (tmp dirs):
  RED→GREEN pairs for content-change, condition-change, unknown-HEAD; reuse
  hash-pin style from `test_696_default_image_hash.py`.
- **Prevention acceptance criteria:** (a) digest covers content + full
  selection conditions + effective env; (b) unidentifiable inputs
  (`unknown`/`manual`/missing pins) refuse reuse fail-closed; (c) each
  covered dimension has a mutant test proving refusal.
- **Corresponding test:** NEW. Follow-up: child draft CD-4.

### K-5 — Production entrypoint not connected; prototype path masks it (D-5)

- **Sources:** F-857-3 (#857 / `6094124363`, HEAD `7038bde0`, MAJOR/MERGE_BLOCKER).
- **Root cause:** `run_selector`/`main` default to `ProvisionalJsonMapLoader`;
  passing `--map` with the canonical file still routes through the
  provisional loader, so the canonical path is never exercised and L3
  fallback hides the misrouting.
- **Why existing tests missed it:** tests targeted the prototype loader +
  CLI arg parsing, never "default CLI against the canonical map end-to-end";
  fallback-to-L3 made the wrong path look safe.
- **Mechanical detectability:** high — invoke `main()`/ `run_selector()`
  with default loader + canonical map and assert the active loader is
  canonical and the match mode is `first-match`; assert provisional loader
  rejects the canonical tag.
- **Best test method:** host-only `unittest` CLI end-to-end with synthetic
  map fixtures (reuse repo-coupled wiring style from
  `test_artifact_guard_repo.py`: assert the *real* entrypoint reaches the
  *real* implementation); provisional isolated to explicit test-only flag.
- **Prevention acceptance criteria:** (a) default CLI connects to the
  canonical loader; (b) provisional loader is test-only and explicitly
  opted; (c) loader mismatch surfaces as an error, never silent L3.
- **Corresponding test:** NEW. Follow-up: child draft CD-5.

### K-6 — Ambiguous identifier spellings + unenforced level constraints (D-6)

- **Sources:** F-857-4 (#857 / `6094124363`, HEAD `7038bde0`, MAJOR/MERGE_BLOCKER).
- **Root cause:** `setdefault` alias table admits bare-hex spellings that
  collide with decimal (`"10"`, `"20"`); `--api` floors at L1 without
  enforcing the canonical slot's own level.
- **Why existing tests missed it:** alias tests (if any) used unambiguous
  spellings; no collision-pair (`"10"` decimal-16 vs `0x10`) or unknown-form
  case; slot-level enforcement untested.
- **Other places:** any multi-spelling identifier (API `$NN` vs decimal vs
  `0xNN` across `tools/api_*`, slot ledgers, CALLER_LEDGER refs; issue #744
  21-bit LBA boundary handling shows the same family — one value, several
  spellings, boundary must be exact).
- **Mechanical detectability:** high — collision-pair table test
  (`"10"`→decimal-16 only, bare hex rejected, `$/0x` explicit); unknown-form
  → L3; per-slot `--api` run asserts slot level, not just ≥L1.
- **Best test method:** host-only `unittest` parameterized over
  spellings × APIs (reuse boundary-mutant style from
  `test_api_744_ipl_sel_boundary.py` / `test_744_ipl_21bit_lba.py`).
- **Prevention acceptance criteria:** (a) exactly one spelling family per
  numeric domain (bare hex banned); (b) ambiguous/unknown → L3; (c) slot
  level enforced on the `--api` path.
- **Corresponding test:** NEW. Follow-up: child draft CD-6.

### K-7 — Measurement claim exceeds collected evidence (D-7)

- **Sources:** F-858-1, F-858-2 (#858 / `6094124535`, HEAD `56ad5ad6`, MEDIUM/MERGE_BLOCKER).
- **Root cause:** causal language ("strictly larger", "necessarily rose")
  from parallelization alone; binding claims (SHA/run/attempt) without the
  concrete identifiers in the comparison rows; single-point timing (125 s
  file) used toward a 1500 s timeout root cause.
- **Why existing tests missed it:** docs-only artifact — no test *can* catch
  prose overreach; no doc-schema lint requires SHAs/attempts or
  estimate-vs-fact separation.
- **Other places:** any perf/baseline/evidence doc (§-style claims in
  `docs/g5-evidence*`, `frame-tracer-measurements.md`, future #849 cold-cache
  numbers, CI wall/CPU/runner-usage reporting after #848).
- **Mechanical detectability:** partial — a lightweight host lint can require
  comparison tables to carry SHA + run-id + attempt fields and ban a small
  denylist of unqualified causal phrases unless adjacent to the supporting
  numbers; root-cause *validity* itself stays human review.
- **Best test method:** (a) immediate: doc correction in-PR (observed started
  in `0dc05b2a` — re-review there); (b) preventive: tiny docs-lint host test
  on measurement docs (field-presence, not prose judgment) — child draft CD-7
  (optional, lowest priority; human review remains the gate).
- **Prevention acceptance criteria:** (a) every comparison row carries
  retrievable SHA/run/attempt or is labeled reference/unconfirmed;
  (b) wall/CPU/runner-usage/billing never interchanged; (c) estimates labeled
  estimates.
- **Corresponding test:** NEW lint (optional) — no existing test covers it.
  Follow-up: child draft CD-7.

### X-1 — Cross-cutting: CI-green-but-undetected (all MAJOR findings)

Every MAJOR finding above coexisted with green CI (4 workflows + Required 6
jobs at the reviewed HEADs). The invariant: **CI success is not evidence a
review finding is absent.** Each child test below therefore includes a
"would-have-been-RED at discovery HEAD" demonstration (run the new test
against the discovery-HEAD logic/fixture) as its detection proof, per the
RED→GREEN / mutation requirement of Issue #865.

## 3. D-1..D-7 coverage map

| Task-D item | KIND | Findings | Severity max | Test home (new, independent of #856–#858) |
| --- | --- | --- | --- | --- |
| D-1 forbidden-area non-contact | K-1 | F-856-1 | MAJOR/BLOCKER | CD-1 |
| D-2 rule-shadowing priority | K-2 | F-856-2 | MAJOR/BLOCKER | CD-2 |
| D-3 fail-closed completeness | K-3 | F-857-1 | MAJOR/BLOCKER | CD-3 |
| D-4 evidence identity | K-4 | F-857-2 | MAJOR/BLOCKER | CD-4 |
| D-5 entrypoint connection | K-5 | F-857-3 | MAJOR/BLOCKER | CD-5 |
| D-6 identifier/boundary coverage | K-6 | F-857-4 | MAJOR/BLOCKER | CD-6 |
| D-7 measurement-evidence consistency | K-7 | F-858-1, F-858-2 | MEDIUM/BLOCKER | CD-7 (lint, optional) |
| (cross-cutting) | X-1 | all re-reviews | process | RED-at-discovery proof required in each CD |

Priority: CD-1, CD-2 first (safety-gate/ALLOW-DENY adjacent, D-1/D-2 are the
only kinds where a live-tree test run itself could touch forbidden media);
CD-3, CD-4 next (fail-closed integrity of any future selector reuse);
CD-5, CD-6 after (wiring/aliases, smaller blast radius); CD-7 last
(docs-lint, human review remains primary).

## 4. Existing-test reuse map (reuse — no duplicates)

| Reusable existing asset | Reuse for | What to copy (pattern, not code) |
| --- | --- | --- |
| `tests/host/test_api_commercial_media.py` (`make_rom` tmp fixtures, sentinel names, sanitized-error asserts) | CD-1 fixtures | synthetic forbidden-dir fixtures; never touch `./rom` |
| Stdin-guard tests from PR #814 (precedence pins, "first wins" locks, reason-pin preservation) | CD-2 style | winning-rule pins + reorder-mutant must-go-RED |
| `tests/host/test_725_failclosed.py`, `test_725_p10_failclosed.py`, `tools/failclosed_gate.py --check` pattern | CD-3 style | object-level invariant asserts, host-only `unittest` |
| `tests/host/test_696_default_image_hash.py` | CD-4 style | hash-pin + mutant discrimination |
| `tests/host/test_artifact_guard_repo.py` (repo-coupled wiring: real workflows → trusted-main copy) | CD-5 style | real-entrypoint → real-implementation wiring asserts |
| `tests/host/test_api_744_ipl_sel_boundary.py`, `test_744_ipl_21bit_lba.py`, `test_365_st_boundary_record.py` | CD-6 style | collision-pair / boundary-mutant tables |
| `tests/host/test_ci_sharding.py` | CD-7 context | what sharding does/does not change (claim boundary) |

Deliberately **not** reused/duplicated: the in-PR tests
(`test_test_staging_map.py`, `test_test_staging_select.py`) belong to
#856/#857; Task-D tests must live in new files with new names and must not
import from those PR-only modules, so Task D stays independent and
mergeable regardless of those PRs' fate.

## 5. Child-issue drafts (DRAFTS ONLY — do NOT create yet)

### CD-1 (D-1): media-safe enumeration + env-independent verdicts

> Title: `test(#865/D-1): forbidden-area non-contact regression (enumerator exclusion + env-independent verdicts)`
> Scope: NEW host-only `unittest` file (name TBD, not the #856 filename):
> synthetic fixture tree containing a `rom/`-named dir; assert no staging
> enumerator lists it; assert identical verdicts with/without the dir; assert
> no absence-assert on opt-in-local paths. RED→GREEN proof against F-856-1
> logic. Safety: tmp fixtures only, never `./rom`; no Required/CI changes.
> Accept: K-1 criteria (a)–(c).

### CD-2 (D-2): safety-rule precedence regression

> Title: `test(#865/D-2): classification rule-shadowing regression (safety-rule priority pins)`
> Scope: NEW host-only test over canonical-map fixtures: exact safety rules
> precede general patterns; winning-rule pins for the 3 guard files
> (`test_api_commercial_media.py`, `test_api_halt_classify.py`,
> `test_api_release_gate.py` → L3 via safety rule); reorder-mutant goes RED.
> Accept: K-2 criteria (a)–(c).

### CD-3 (D-3): L3 fail-closed completeness invariants

> Title: `test(#865/D-3): selector L3 fail-closed completeness (single full sequence, no subset display)`
> Scope: NEW host-only test with synthetic `ChangedInputs`: unknown path /
> empty set / mixed L1+L3 / zero-command entry → exactly the single mandatory
> full local-L3 sequence (incl. `release-verify` semantics); subset with
> `partial=False` + empty `unselected` forbidden. RED→GREEN vs F-857-1.
> Accept: K-3 criteria (a)–(c).

### CD-4 (D-4): evidence-fingerprint discrimination + refusal

> Title: `test(#865/D-4): evidence identity regression (content-aware fingerprint, fail-closed refusal)`
> Scope: NEW host-only test on synthetic repos: same status/path + different
> bytes → different digest; dropped condition (map/API/level/env) → refuse;
> `unknown`/`manual` HEAD → refuse. RED→GREEN vs F-857-2. Accept: K-4 (a)–(c).

### CD-5 (D-5): canonical entrypoint connection

> Title: `test(#865/D-5): CLI→canonical-loader connection regression (provisional isolated)`
> Scope: NEW host-only CLI end-to-end test: default invocation engages the
> canonical loader (`first-match`); provisional loader rejects canonical tag
> and is test-only explicit; loader mismatch errors, never silent L3.
> RED→GREEN vs F-857-3. Accept: K-5 (a)–(c).

### CD-6 (D-6): identifier-spelling disambiguation + slot-level enforcement

> Title: `test(#865/D-6): API identifier alias + slot-level regression (bare-hex ban, collision pairs)`
> Scope: NEW host-only parameterized test: `"10"`→decimal-16 only, bare hex
> rejected, `$/0x` explicit, unknown→L3; per-slot `--api` run enforces the
> slot's own level. RED→GREEN vs F-857-4. Accept: K-6 (a)–(c).

### CD-7 (D-7): measurement-doc evidence lint (optional, lowest priority)

> Title: `test(#865/D-7): measurement-doc evidence-field lint (SHA/run/attempt presence, estimate labels)`
> Scope: NEW tiny host lint over measurement docs: comparison rows carry
> SHA+run+attempt or `unconfirmed`/`reference` label; denylist of unqualified
> causal phrases without adjacent numbers. Human review stays the gate; this
> only prevents field omission. Doc-correction itself stays in #858
> (re-review `0dc05b2a`). Accept: K-7 (a)–(c).

## 6. Urgent safety report

**No NEW critical safety problem discovered.** No CRITICAL finding beyond the
already-reported MAJOR/MERGE_BLOCKERs (which remain tracked in their own PRs
and are restated, not re-decided, here). Specifically:

- No ABI/ALLOW-DENY/release-gate change is proposed or required by this doc.
- No `./rom` was accessed; no commercial bytes/hashes/names are recorded.
- The in-PR fix commits observed (`3dad5dad` on #856, `0dc05b2a` on #858)
  are **not** validated here — each needs its own PR re-review before merge.
- One out-of-scope observation for the parent: PR #858's fix commit references
  review `6094124533`, which is outside the 8 tasked findings — the parent may
  want to confirm that review is tracked elsewhere. Not a blocker for Task D.
- PR #857 (4 MAJOR/MERGE_BLOCKERs) is unchanged since reviewed HEAD
  `7038bde0`; its blockers stand until fixed in that PR.

## 7. Residual notes

- Full-history automation is explicitly out of scope (per #865); the prior
  survey above is bounded and documented in §1.3.
- Detection proofs (RED at discovery HEAD / mutation) belong to the child
  implementations, not this doc — this round only classifies and designs.
- If #856/#857/#858 land their in-PR fixes first, the child issues remain
  valid: they lock the KINDs in Task-D-owned files independent of those PRs.
