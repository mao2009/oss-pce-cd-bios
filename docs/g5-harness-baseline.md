# G5 harness baseline (Agent C) — initial report

Date (UTC): 2026-11-12. HEAD: `0c7157e7403abc2398e4bc66f69d26e28181aa2a`
Branch: `agent-c/g5-harness-g5` (from `origin/main`).
Agent: C. Parent tracker: #635.

Pins: Geargrafx `b49ae82a` (`b49ae82a012eade566d72e9d61c9d95a5caa4da3`),
cc65 `71746c8` (`71746c829e77f74c2b601a7d16821170c2610df3`).

Safety: `reference_bios_jsr=0`, `abi_verified=0`, `dev_callable=false`,
`release_eligible=false`, `compatible_system_card_bios=false`.
Gates #173/#367/#459 unchanged. No experimental code in normal/release.
Geargrafx observations are not hardware proof.

## What ran

- Host model tests: `tests/host/test_g5_harness.py` — **PASS 26/26**
  (variant specs, return kinds, all 9 verdict paths incl. G5_OBSERVED gating,
  manifest pos/neg, sanitization pos/neg, progress diff, safety pins).
- Integration static: `tests/integration/test_g5_progress.py` static cases —
  **PASS 3/3** (variant listing, harness imports, skeleton pins).
- Real-core synthetic A/B/C differential — **BLOCKED/SKIP (not attempted)**:
  `cc65 unavailable (missing tool ca65; run make setup)` on this Windows
  worker host; pinned Geargrafx sources and DEV ROM build inputs also absent.
  No real-core evidence exists from this run; nothing is reported as PASS.
- Commercial media — **NOT_RUN**: `./rom/` absent in this worktree and no
  CUE candidates resolvable here; read-only discipline respected; no private
  bytes/paths/hashes touched, logged, or published.
- Related existing suite spot-check: `tests/host/test_api_commercial_media`
  shows pre-existing Windows-only failures (symlink privilege `WinError 1314`
  and POSIX permission cases) — untouched by this change, not a regression.

## Expected A vs B vs C differential (instrument design, not yet executed here)

Synthetic SEQ23 3-API chain (JSR slot2 → JSR slot51 → JSR slot3):

| Variant | Return kind | Expected APIs | Expected stop |
|---------|-------------|---------------|---------------|
| A (normal PUBLIC) | NONE | 1 (`[(2,JSR)]`) | `API_ENTRY_CONFIRMED`, stub halt X=2 |
| B ($02 fake RTS) | FAKE_RTS | 2 (`[(2,JSR),(51,JSR)]`) | `SIMULATED_NEXT_API`, next blocker $33 |
| C ($02+$33 fake RTS) | FAKE_RTS | 3 (`[(2,JSR),(51,JSR),(3,JSR)]`) | `SIMULATED_NEXT_API`, next blocker $03 |
| D (experimental REAL) | REAL_SERVICE | per manifest | `REAL_SERVICE_RETURN` or `BLOCKED` (labels missing) |

Each fake return advances exactly one more API; the whole-image diff is
exactly the patched 5-byte windows (asserted by the reused builder).
First stop cause on variant A (synthetic): stub halt at the first table
entry (slot $02, X=2). Single next implementation: none — measurement only;
the harness identifies the next blocker but implements nothing.

## G5 contribution

This harness proves nothing about game boot yet — it is the measurement
instrument itself. What it contributes: automatic per-variant boot-progress
metrics (call order, real-vs-fake distinction, return evidence, PC/SP/MPR,
insn counts, IPL transfer, cold/warm delta, stop cause, baseline progress,
title evidence) with fail-closed verdicts where screen change alone is never
title and `G5_OBSERVED` requires observed + confirmed title evidence with a
stated basis. Boot-failure classification counts as success.

## Remaining blocker

Pinned Linux real-core execution environment (`make setup`, `make dev-rom`,
`tools/g5_harness.py --mode debug --variants A B C`) plus optional local
`./rom/*.cue` for the opt-in commercial arm. No code blocker in the harness
model itself.
