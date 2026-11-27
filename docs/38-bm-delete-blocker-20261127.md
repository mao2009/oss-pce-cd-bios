# Agent C: $1C BM_DELETE boot-blocker re-confirmation (Issue #38, independent commercial-CD path)

- Task: G5 Agent C, Issue #38 `$1C` BM_DELETE boot blocker (independent commercial-CD path).
- Base: `origin/main` `2399a50b`. Branch: `agent/38-1c-blocker-20261127`.
  Worktree: `/tmp/g5-c-38-1c-blocker` (no shared-wiring edits; A/B worktrees untouched).
- Verdict: **`BLOCKED_ABI` — no function implemented.** Genuine `$E054` contract remains UNKNOWN.
  Gates unchanged: reference JSR 0/81, genuine ABI VERIFIED 0/81, `release_eligible=false`,
  `G5_OBSERVED=false`.
- Full caller-side contract stays in `docs/38-bm-delete-contract.md` (PR #826, merged). This note
  adds: provenance re-fetch (Zeograd now readable), latest-observation blocker check (sanitized
  structure only), host mock fixtures, and a synthetic real-core stub-halt proof.
- No commercial media was accessed for this task (no `./rom/` existence check, listing, or read).
  No reference BIOS was executed, disassembled, or copied. No System Card bytes appear here.

## 1. Provenance re-review (no speculation)

Fixed local copies (scratch only, uncommitted): Zeograd `pce_bios.html` 19170 bytes,
SHA-256 `6966984b…fd2cab6f` (HTTP 200, 2026-11-27 fetch); Stum offsets page 24169 bytes,
SHA-256 `81a4c1c1…e196121c0` (HTTP 200, 2026-11-27 fetch). Hash prefixes match the
`docs/g5-display-functional-selection.md` S1/S2 re-fetch of the same day.

| Source | $1C finding | Disposition |
| --- | --- | --- |
| Zeograd `pce_bios.html` | `BM_DELETE, Function #$1C` is a **bare heading: no IN list, no OUT list**, same as `BM_READ`/`BM_WRITE`/`BM_FILES`. The only BM-adjacent functional text is the `HUBM` note (A=`$FF` when the string is missing) and the `BM_FREE` IN/OUT (`N/A` / `CX: amount of free space`). | REFERENCE_ONLY. Bare heading confirms the name/slot but contributes **zero** input/output evidence. PENDING |
| Stum offsets table | Row `bm_delete / $E054` with body addresses `1.0=$F951 / 2.0=$FA10 / 2.1=$FA1A / 3.0=$FA1A / TG2.0=$FA33 / TG3.0=$FA33`. JSR column is `$E054` on all generations. | REFERENCE_ONLY. Name/entry correspondence only. Body differences forbid assuming one behavior. PENDING |
| HuC/HuCC caller side (S1–S5 of PR #826) | Unchanged since PR #826: name/entry equates, `_bm_delete.1` wrapper (name pointer in `_ax` only), `BM_OK / BM_NOT_FOUND / BM_NOT_FORMATTED($FF)` caller constants. | REFERENCE_ONLY (CALLER). No new fetch; hashes already pinned in `spec/api_contracts.json` |
| Hugo `bios.c` HLE | `BM_DELETE 0x1C` selector/``debug name only; no `handle_bios()` case (per PR #826 S7). | REFERENCE_ONLY. Absence is not behavior. REJECTED for ROM use (GPL) |
| Prior PR #826 | `BLOCKED_ABI` contract doc, docs-only, `NOT_RUN` for execution. | Superseded in execution coverage by §4–§5 below; contract conclusion unchanged |

New vs PR #826: Zeograd was HTTP 403 then (`NOT_RUN`); it is now read and confirms the bare
heading, i.e. it **strengthens the negative result** (no public IN/OUT exists for `$1C`), it does
not add any positive contract fact.

## 2. $1C is still the blocker on the independent path (latest observations, structure only)

Sanitized chain facts already published on Issue #802 / `docs/g5-evidence-2026-11-18.md` /
`docs/g5-display-functional-selection.md` (no new commercial run was made or needed here):

- CUE[1]-family path: `$1E → $4A → $02 → $02 → $21 → $1C`, halt with X=`$1C`, cold/warm identical.
  Reported stable through ExpE VDC re-observation (PR #839) and ExpF (PR #840): CUE[1] **6 calls,
  `$1C` halt regression unchanged**, including after the `$2C` D2-functional experiment that moved
  CUE[0] forward to `$2B`.
- The `$1C` arrival everywhere stands **under experimental/SIMULATED `$4A` returns**, never under a
  genuine `$4A` ABI (still UNKNOWN/BLOCKED). Arrival order is OBSERVED; callee input/output/return
  evidence for `$1C` is absent (UNKNOWN).
- Therefore `$1C` remains the first blocker on the independent commercial-CD path, and its genuine
  contract remains the gate for any delete implementation.

## 3. Contract findings (caller-side only; callee side stays UNKNOWN)

Authoritative table: `docs/38-bm-delete-contract.md` §§3–5 (22 CONFIRMED-by-source caller facts,
7 HYPOTHESIS, 19 UNKNOWN). Agent C delta:

| Item | Caller-side fact (CONFIRMED-by-source) | Callee (BIOS `$E054`) status |
| --- | --- | --- |
| Entry / name | `bm_delete = $00:E054` (HuCC equate); Stum JSR column `$E054` all generations | HYPOTHESIS (mapping unproven) |
| Args | Name pointer in `_ax` (`$F8:20F8`) only; 12-byte convention (2 uniqueness + 10 ASCII blank-padded). Zeograd adds nothing (bare heading) | UNKNOWN (reads? MPR visibility? length validation?) |
| Returns | Caller stores A into `__bm_error`, expects `0/1/$FF` (`BM_OK / BM_NOT_FOUND / BM_NOT_FORMATTED`) | UNKNOWN (genuine set unconfirmed; `2/3/4/5` codes UNKNOWN) |
| Saved regs | Caller sets nothing but `_ax`; reads A; zeroes Y; touches no MPR/`sei/cli` | UNKNOWN (A/X/Y/P preservation, SP discipline) |
| RAM | `_ax` at `$20F8`; `__bm_error` 1 byte; `_bx/_cx/_dx` unused by this wrapper | UNKNOWN (`$20F8–$20FF`, `$2260` use, BRAM-window writes) |
| HW side effects | None in the caller wrapper | UNKNOWN (MMIO `$1803/$1807`, MPR4=`$F7`, IRQ, timing, compaction/fragmentation) |
| Errors | Caller comment lists 3 outcomes; siblings show comment inconsistencies (I-1) | UNKNOWN (all failure semantics, atomicity, re-entry) |

Bare-RTS vs real-function discipline: a bare RTS at `$E054` would return to the caller and let a
payload continue (marker written, `cd_halt_ok`). That is **forward motion, not function evidence**,
and this task does not add any such stub. §5 proves the current entry halts instead.

## 4. Why BLOCKED_ABI (explicit missing-evidence list)

No delete implementation is grounded because every load-bearing callee fact is missing an
independent second source:

1. Genuine argument reading (which bytes of the 12, MPR visibility, validation) — no source.
2. Genuine return placement and value set (A? flags? which codes incl. checksum/dir/empty/full?) — caller comment only.
3. Register/stack/MPR preservation contract — no source.
4. Work-RAM footprint (`$20F8–$20FF`, `$2260`, `__bm_error` analogue) — no source.
5. BRAM access path (window mapping, unlock/lock order, MPR4 handling, IRQ discipline) — no source.
6. Delete semantics (which bytes erased, compaction vs freelist, fragmentation, checksum/header update, free-space accounting) — HuC direct implementation exists but is route B, not BIOS evidence.
7. Generation behavior (Stum body addresses differ per generation) — unconfirmed for 3.0-only use.
8. Failure atomicity (not-found/unformatted/corrupt/empty/invalid input leave BRAM identical) — no source.
9. JSR execution permission (`spec/reference_jsr_gate.json` has no `$E054` entry) — absent.
10. A non-destructive verification plan that overcomes the `docs/173` MMIO pre-block impossibility for a destructive op — none approved.

Any one of 1–10 alone blocks implementation; all ten are open.

## 5. What was built instead (isolated additions, no shared wiring)

New files only (no edits to builders, tables, ROM sources, ledgers, or `Makefile`):

- `tools/bm_delete_judge.py` — in-memory PROJECT-INVENTED mock judge (pure stdlib, no imports, no I/O):
  `MockStatus {OK, NOT_FOUND, NOT_FORMATTED}` vs `HarnessOutcome {REFUSED, CORRUPTED}` layers,
  `validate_input` (12-byte bytes) → `audit_image` (CLEAN invariants incl. `end == mock_used`,
  no duplicates, 12-byte names) → `mock_delete` (single-entry removal, compaction by order,
  failure-identical images). `mock_add`/`mock_free` are preparation/arithmetic helpers, not BIOS claims.
- `tools/bm_delete_fixture.py` — 25 READ-ONLY scenarios (5 positive incl. head/middle/tail/empty-data,
  11 negative incl. unformatted/missing/bad-input/4 corruptions, 9 boundary incl. 11/12/13-byte,
  NUL, case, uniqueness-prefix, full-image tail) with hand-written expectations.
- `tests/host/test_bm_delete_judge.py` — 20 host tests (order/bytes preservation, capacity reuse,
  non-mutation, double-delete, input/audit refusals, corruption set, exact-match boundaries,
  96 name-bit-flip rejections, import/I-O allowlist).
- `tests/host/test_bm_delete_fixture.py` — 7 host tests (scenario verdicts, survivor order, full-image
  free, failure identity, re-delete, literal 12/11/13-byte checks, read-only + allowlist).
- `tests/integration/test_bm_delete_blocker.py` — 10 tests: 6 synthetic real-core (§6) + 4 ledger guards.

## 6. Real-core verification (pinned core, synthetic media only)

Method: normal DEV ROM (`build_rom.py`, debug) → PUBLIC IPL loader BIOS
(`build_ipl_loader_bios`, `IPL_SEL=$0109`) → synthetic one-sector payload
(`tests/integration/ipl_api_call_payload.s`, `API_ENTRY=$E054`) JSRs the **real table entry**,
then writes marker `$AB` at `$2F80` and spins. Probe: `geargrafx_cdread_probe.cpp`, budget 20M,
cold+warm. Classification: `api_halt_classify.classify` (stub address from the built image labels).

Result (2026-11-27 run, this worktree): **10/10 GREEN (cold+warm each)** —

- `$E054` JSR traps at `api_unimplemented` with `halt_confirmed`, X=`$1C`, PC == stub address.
- Post-call marker NOT written (`$2F80 != $AB`): the call did not return — a bare RTS would have
  written it and reached `cd_halt_ok`, which the suite asserts against.
- Cold/warm agree on reason/halt/X/PC/marker byte.
- Version pins asserted in-test: cc65 revision `71746c8…`, Geargrafx `b49ae82a…`
  (from `tools/dependencies.json`); BIOS image SHA-256 logged per run and cross-checked against
  the probe record (`rom_sha256` equality). Synthetic cue only; cue bytes never published.
- Harness self-check: the same runs classified with `expected_slot=$1D` yield `API_SLOT_MISMATCH`.
- Ledger guards (host-only): no `src/overrides/slot_1C.s`; `dev_implementations.json` id 28
  `NOT_READY`/implementation null; `api_contracts.json` slot 28 `HYPOTHESIS` + all 10 contract
  fields UNKNOWN; `reference_jsr_gate.json` has no slot 28 / no `$E054`, all entries
  `release_eligible=false`.

What this proves: the `$1C` boot blocker is **present and fail-closed** on current main wiring —
an unimplemented-stub halt, not a return. What it does not prove: any genuine `$1C` behavior
(no delete executed, no BRAM touched, no ABI verified).

## 7. Validation performed here

- `python3 tools/bm_delete_judge.py` → fresh OK, 96 name bit flips rejected; missing/unformatted paths OK.
- `python3 tools/bm_delete_fixture.py` → 25/25 scenarios GREEN.
- `python3 -m unittest tests.host.test_bm_delete_judge tests.host.test_bm_delete_fixture` → 27 tests OK.
- `OSS_PCE_REQUIRE_CC65=1 python3 -m unittest tests.integration.test_bm_delete_blocker` → 10 tests OK
  (pinned cc65 `71746c8`, pinned Geargrafx `b49ae82a`, debug build).
- `make check` and `make test` / required CI: run before PR update (see PR report).

## 8. Boot-path contribution and follow-ups

- Contribution: on the independent (CUE[1]-family) commercial path, the next required work is still
  the genuine `$1C` contract (missing-evidence list §4), not loader or harness work — the halt is
  reproduced synthetically at the real table entry with cold/warm agreement.
- Explicit non-duplication: no SIMULATED `$4A`/`$1C` RTS experiment was added or re-run; PR #803's
  `SIMULATED_NEXT_API` chains are cited only as what this task is not.
- Follow-ups for the owner: (a) second independent callee-side source for `$E054` (any of §4.1–8);
  (b) `$E054` JSR-gate human decision + non-destructive verification plan for a destructive op;
  (c) BURAM-family sequencing (`$18/$19/$1A/$1B/$1D` share the format/name/format-state questions).
