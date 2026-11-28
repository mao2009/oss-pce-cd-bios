# G5 next-path candidate selection survey (Issue #802, parent #635) — Agent B verdict: BLOCKED, zero selection

Survey-only deliverable. No API implementation, no behavior change, no `./rom/`
access of any form (no existence check, listing, or read; all commercial claims
below cite already-published sanitized structural records only).

- Base: `origin/main` `4827598d` (PR #877 merge).
- Prior design: Wave1 survey (read-only, no writes) — this document persists its
  verdict with in-tree verification at base.
- Safety gates (unchanged): JSR 0/81, VERIFIED 0/81, `release_eligible=false`,
  `G5_OBSERVED=false`, `$1C` `BLOCKED_ABI`.
- Scope boundary: CR-shadow/loader/BOOT staging belongs to Agent A
  (#843 BOOT commonization, #844 BUF-overlap/marker-validity P1). This survey
  selects nothing in that scope.
- Interpretation rule: NEXT arrival ≠ functional success (reject path possible;
  shadow `$2503` never read commercially — INFERRED only). No title-failure
  cause is asserted: per-API necessity and the genuine blocker (including
  whether display debt causes title non-arrival) remain UNKNOWN; only
  debt-residence (B1 = no display init, OBSERVED CR=0) is SOURCE_CONFIRMED.

## 1. Prior verdict kept: display-4 all-BLOCKED (PR #877)

`docs/g5-display-next-functional-selection.md` verdict is kept unchanged:
**$25 (Issue #47) / $24 (#46) / $36 (#64) / $35 (#63) — D1 BLOCKED and
D2-functional BLOCKED, B1 preservation scaffolds maintained** (STA 0,
verified in-tree). Re-check at base confirms the grounds still hold:
`spec/api_contracts.json` mapping all HYPOTHESIS for the four slots, the
reference-JSR gate rejects all (no genuine-ABI oracle), and no new public
functional descriptions exist. No new grounds exist, so the BLOCKED verdicts
are **not lifted**.

## 2. State of the board (verified at base `4827598d`)

- **Implemented D2 bodies**: `$2C` (PR #840), `$2B` (PR #841). `$33` EX_DSPOFF
  + `$4A` preservation + `$2E`/`$23`/`$21` DEV precedents in-tree.
  Single source: `src/ex_cr_family.s` (`NEED_CR33`/`NEED_CR2C`/`NEED_CR2B`;
  `$2C` = ORA `#$08` bit-3 set, `$2B` = AND `#$FB` bit-2 clear).
- **Wiring**: `tools/build_ipl_loader_g5.py` wires only $02/$03/$33
  (`experimental_slots: $02, $03, $33`). Full ExpG combination lives in
  `tools/build_experimental_bios_boot.py`
  (`unified_api_windows_4a_25_24_36_35_2c_2b`).
- **Arrival records (sanitized, published)**: CUE[0] ExpF
  `$02→$33→$4A→$23→$25→$24→$36→$35→$2C→$2B` stub halt (10 calls, cold/warm
  identical, CR=0 maintained); CUE[1] `$1E→$4A→$02→$02→$21→$1C` halt
  (6 calls). **NEXT after `$2B`: UNKNOWN** (`docs/53` U8 — stub halt, nothing
  observed past `$2B`).

## 3. Ranked candidates table

Scoring: ◎ strong / ○ partial / △ hint-only / × absent. ABI-uncertainty × =
fully UNKNOWN (blocks D1); synth-verifiability = synthetic oracle availability.

| Rank | Candidate (existing Issue) | G5 contrib | Public grounding | Indep. evidence | ABI uncert. | Synth verif. | BOOT budget | Impl size | Regr. risk | Verdict |
|------|---------------------------|------------|------------------|-----------------|-------------|--------------|-------------|-----------|------------|---------|
| 1 | **$2D EX_IRQOFF** (#55) — near-miss, **NOT selected** | × (commercially unreachable; no NEXT advance, no ExpF comparison possible) | ◎ (IN N/A + bit3-clear + `$2C` pair + no-setup, same 5-point shape) | ◎ (pinned CR bit/mask/hazard + family window) | × genuine (OUT N/A; I/IDR/SR/handler/timing UNKNOWN per docs/55 §6.4 E1) | ◎ (same oracle shape as `$2C`) | △ (U12 overflow pressure; #843 open) | Small (44B-class) | **High here**: 3rd writer to shared `$2502` during open P1 #844 false-ok investigation | **NOT SELECTED**: Agent A scope (CR-shadow) + zero G5 contribution + active P1 risk. Reuse #55. |
| 2–5 | **$25/#47, $24/#46, $36/#64, $35/#63** functional | ○ (upstream debt, B1 done) | × bare / △ one-liner (#63) | △ (HW positions only, no BIOS link) | × (inputs/outputs/flags/targets/timing all UNKNOWN) | ○ (B1 only; no functional oracle) | ○ (B1-scale) | N/A (target unknown) | Mid–High (VDC/DMA corruption) | **BLOCKED** — #877 kept. Reuse #47/#46/#64/#63. |
| 6 | **NEXT after `$2B`** (whatever follows the `$2B` stub halt) | ◎ in principle (is the actual blocker) | × (nothing observed past `$2B`) | × | × (callee unknown because unreached) | × (no entry state to build oracle from) | ? | ? | ? | **BLOCKED** — missing evidence: one arrival record (slot/entry/call-state). Reuse #53 (U8) + #802. |
| 7 | **$2A EX_RCRON** (#52) | × (unreachable; `$2B` pair side never arrived) | △ (name-adjacent hint only; no selection doc) | △ (bit-2 assignment is HW fact, not BIOS link) | × | ○ (shape only) | △ | Small | High (same P1 scope as $2D) | **BLOCKED** — no `$2A`-specific contract doc; CR-scope. Reuse #52. |
| 8 | **$1C BM_DELETE** (#38, CUE[1] blocker) | ◎ (CUE[1] first blocker) | × (caller source has selector name only, no callee body) | × | × (all contract fields UNKNOWN) | × (destructive: no synthetic DMA/BRAM oracle may authorize a delete) | — | — | **Prohibitive** (data-destructive) | **BLOCKED_ABI** — gate unchanged. Reuse #38. |
| 9 | **CD path** (CD_BASE/READ/EXEC #147/#149/#735/#243/…) | ○ (loader-adjacent) | × | × (reference-JSR gate BLOCKED) | × | △ (host/diagnostic only) | — | — | High | **BLOCKED** — no verified ABI. Reuse existing CD issues. |
| 10 | **Input** $21/#43 (DEV done), $22/#44 | × (no G5 delta: `$21` already returns on CUE[1]) | × (`$22` genuine UNKNOWN) | × | × | — | — | — | Low but pointless | **BLOCKED** — no advancing delta. Reuse #43/#44. |
| 11 | **Memory/IRQ others** ($4A/#84 done-preservation; handlers/vectors; display $26–$32 etc.) | ×–○ | × (bare headings) | ×–△ | × | ×–○ | — | — | Mid–High | **BLOCKED** — same bare-heading rationale as display-4. Reuse existing per-API issues. |

## 4. Selection: BLOCKED — no implementable candidate

The highest-grounded candidate ($2D) is disqualified for Agent B on three
independent grounds: (a) **scope** — third CR-family writer on shared `$2502`
while P1 #844 (BUF-overlap/marker false-ok) is open under Agent A;
(b) **contribution** — commercially unreachable, advances no NEXT arrival;
(c) **budget/risk** — BOOT overflow pressure (#843 open) plus P1 regression
risk. All other candidates lack the public + independent grounds for even a
limited D2 scope. **At most one was to be picked; zero meet the bar, so none
is picked.** No new Issue is created.

## 5. Unblock conditions (concrete missing evidence)

- NEXT-after-`$2B`: one sanitized arrival record (slot/entry/table-JSR/MPR1/SP/
  cold-warm) via the existing opt-in commercial path — needs Owner permission,
  not decided here.
- `$2D`: Agent A P1 closure (#843 BOOT fit + #844 ownership/validity
  resolution) **plus** a commercial arrival (currently none) to give it G5
  meaning; #55 holds the contract delta checklist.
- Display-4: two-system public input/value/target agreement (per #877 §6) +
  pinned-core correspondence; agreement-alone never suffices.
- `$1C`: genuine contract + non-destructive oracle — `BLOCKED_ABI` gate stays
  until then; destructive execution is never the verification method.

## 6. Verification method (conditional — applies to whichever candidate unblocks first)

Synthetic real-core only (pinned toolchain): seeded ok-path oracle + unseeded
fail-closed (C=1, zero VDC/RAM writes) + mask/bit-preservation mutants +
wrong-target mutants RED + window/MPR/AR/SP + RAM/VDC-diff + chain cold/warm +
64-variant BOOT-span regression + `make check`/`make test`/Required CI +
independent review with 0 CRITICAL/MAJOR. Commercial runs remain
structural-observation only (NEXT arrival ≠ success); gates (JSR 0/81,
VERIFIED 0/81, `release_eligible=false`, `G5_OBSERVED=false`, `$1C`
`BLOCKED_ABI`) unchanged.

## 7. Issue reuse (no new Issue)

Display → #47/#46/#64/#63; CR pair → #55 (#52 for `$2A`); NEXT-after-`$2B` →
#53 (U8) under #802; CUE[1] → #38; shadow/loader/BOOT → Agent A #843/#844
(Agent B does not touch).
