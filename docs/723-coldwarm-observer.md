# Issue #723: cold/warm observer + IRQ/JSR distinction (ledger)

Status: **verification ledger only (doc + host test)**. No production
mechanism is implemented, enabled, or proposed for adoption here.
`#173` / `#367` / `#459` stay BLOCKED. `release_eligible=false`,
ABI VERIFIED 0/81, DEV_CALLABLE 0, reference JSR 0/81 are unchanged.
This project is not a compatible System Card BIOS.

Scope (plan 8): follow the `#709` MPR1-gated `stack_check` observer
precedent (`docs/635-api-entry-observation.md`) to record the A+D+T
premises and stop state separately for cold and warm. No new API
reference permission and no JSR execution are included. New real-core
runs are NOT made here (NOT_RUN); real-core citations reuse existing
runs on the pinned core. Synthetic fixtures and host checks only;
`./rom/`, commercial BIOS/ROM bytes, and reference JSR are not used
and not published.

Stop-state contract reuse (no duplication): the per-stop retention
axes, the TransferStart/TransferEnd dirty-stop positions, and the
kill-only no-resume contract are pinned by Issue #721
(`docs/721-transferend-noresume.md` + `tests/host/test_721_*`,
PR #770). This ledger does **not** re-pin, re-table, or re-judge
those axes. This ledger pins only the cold/warm observer shape:
separate initial-state evidence per cycle and the IRQ-vs-JSR entry
distinction with fail-closed handling.

## 1. Pinned precedents reused (no new facts asserted)

- `#497` contract (`docs/497-mpr1-stack-contract.md`,
  `tools/mpr1_stack_contract.py`): MPR1=`$F8` only PASS; reset value
  UNDETERMINED (`RESET_MPR1 is None`) and never a contract input;
  harness must record `DIRECT_SETMPR $F8` at instruction index 0 before
  the first guest instruction; TAM vs `DIRECT_SETMPR` are distinct
  paths (`check_mpr1_write`); same-instruction IRQ overlap is
  `UNMEASURED`, unobserved IRQ state (`IRQ_UNKNOWN=None`) is BLOCKED,
  never PASS (`judge_irq_window`).
- `#709` / `#635` observer (`docs/635-api-entry-observation.md`,
  `tests/integration/geargrafx_api_entry.py`,
  `tests/integration/geargrafx_api_entry_probe.cpp`): probe records
  caller/entry/slot/SP-delta/entry-regs/return booleans only;
  `classify_apicall` distinguishes `JSR` (in-table, delta 2) /
  `JMP` (in-table, delta 0) / `INTERRUPT` (in-table, delta 3) /
  `DIRECT` (non-table stub match) / `IRQ` (non-table vector or
  delta 3) / `OTHER*`; `jsr_confirmed` additionally requires table +
  delta 2 + internal `stack_check=="MATCH"` +
  `return_matches_caller is True` + `mpr[1]==$F8`; commercial mode is
  fail-closed at the C++ boundary (no RAM dumps, no raw stack values;
  `assert_commercial_clean` rejects `*_hex` / `stack_b0` / `stack_b1` /
  `stack_word`).
- `#721` ledger (PR #770): TransferEnd reset-then-access order
  (line 687 before pops at 688-690, pinned Geargrafx `b49ae82`
  `src/huc6280_opcodes_inline.h`), retention axes per stop, dirty
  stops (`tstart` loses only `transfer_state`; `tend` loses
  PC/state/A/X/Y), no-resume kill semantics. Cited, not repeated.
- Pinned Geargrafx `b49ae82a012ede566d72e9d61c9d95a5caa4da3` per
  `tools/dependencies.json` (GPL-3.0, verification-only patch, never
  distributed); synthetic HuCARD/CD only.

## 2. Cold/warm initial-state evidence (separate per cycle)

Each cycle (`cold`, `warm`) carries its own independent evidence
triple, judged by `check_initial_state` (`#497` §2). The reset value
is recorded for information only and never changes a verdict.

| Cycle | Measured reset | Recorded init value | Instruction index | Verdict |
|---|---|---|---|---|
| cold | UNDETERMINED (any byte incl. `$F8`, or unreadable) | `$F8` | 0 | PASS (reset not trusted) |
| warm | UNDETERMINED (any byte incl. `$F8`, or unreadable) | `$F8` | 0 | PASS (reset not trusted) |

Fail-closed rules (from `#497`, re-checked here, not widened):

- No recorded init in a cycle -> BLOCKED (even if that cycle's
  measured reset happens to read `$F8`).
- Recorded init at index != 0 -> FAIL (even with `$F8`; mid-run
  `DIRECT_SETMPR` is `check_mpr1_write(DIRECT_SETMPR, $F8, >=1)==FAIL`).
- Recorded init value != `$F8` -> FAIL (readable contradiction).
- Unreadable value/index type (`str`/`None`/`bool`/list) -> BLOCKED
  (evidence cannot be read).
- `RESET` path is never a contract input
  (`check_mpr1_write(RESET, *, *)==BLOCKED`); TAM is judged by the
  bank table at the instruction boundary only.

Cold and warm must **not** share one record: a single `DIRECT_SETMPR`
entry reused for both cycles is BLOCKED (each cycle needs its own
index-0 record). A cold PASS never certifies a warm run and vice
versa.

## 3. IRQ vs JSR stack-entry distinction

The observer classifies every recorded stack entry with
`classify_apicall(call, stub_addr)` and confirms JSR returns only with
`jsr_confirmed(call)`. `stub_addr` is the explicit verified loader
BIOS address for the executed ROM (omission raises; `None` never
yields `DIRECT`).

| Entry shape | Classification | JSR-confirmed? | Safety use |
|---|---|---|---|
| in-table, `sp_delta==2`, `stack_check=="MATCH"`, `return_matches_caller is True`, `mpr[1]==$F8` | `JSR` | yes | JSR return evidence only |
| in-table, `sp_delta==0` | `JMP` | no | Not a return; last-call only per `#635` |
| in-table, `sp_delta==3` | `INTERRUPT` | no | IRQ-entry class, not JSR evidence |
| non-table, entry == stub | `DIRECT` | no | Direct reach, not a table call |
| non-table, vector (`entry>=0xFFF0`) or `sp_delta==3` | `IRQ` | no | IRQ-entry class, not JSR evidence |
| otherwise | `OTHER` / `OTHER_DIRECT` | no | Not evidence for either |

Non-transfer rule: **a JSR success is never IRQ safety evidence and
an IRQ-entry observation is never JSR safety evidence.** The host
suite pins a negative for each direction: a `jsr_confirmed` row fed
to the IRQ-entry check does not pass it, and an `IRQ` row fed to
`jsr_confirmed` returns `False`. Mismatched `MPR1` (`mpr[1]!=$F8`)
forces `jsr_confirmed is False` (UNKNOWN, not confirmed).

## 4. Fail-closed rules (mismatch / unmeasured / tracking leak)

| Condition | Verdict | Reason |
|---|---|---|
| `mpr[1]!=$F8` on a JSR confirm check | not confirmed (`False`) | MPR1-gated `stack_check` precedent |
| `mpr1_changed` with observed assert/pending | `UNMEASURED` | Same-instruction IRQ hole (`#497` §4) |
| Any `IRQ_UNKNOWN` in a judged window | `BLOCKED` | Missing evidence, never a negative |
| `RUN` count != 2 or cold/warm run missing | `FAIL` | Cycles incomplete (`classify_commercial` shape) |
| `apicall_truncated is True` in any run | `INCOMPLETE`/`BLOCKED` | Tracking leak; never promoted |
| Budget / unconfirmed halt in any run | `BUDGET` | Budget is not a halt |
| Wrong-type inputs | `BLOCKED` | Cannot be read |
| Readable contradiction | `FAIL` | Contract violation |

A cycle pair passes the observer only when **both** cycles carry
index-0 `$F8` init records, every judged window is observed (no
`IRQ_UNKNOWN`), no window is `UNMEASURED`, every entry is classified
without `OTHER*` ambiguity for the claimed method, and no truncation
or budget flag is set. Anything else stays fail-closed; unobserved
stays BLOCKED, never PASS.

## 5. Public boundary (#596): no raw stack bytes

Following the `#709` / `#635` commercial boundary, this ledger and
its host test publish **no** raw stack bytes and no private guest
output:

- Observer records carry booleans/deltas/PCs/slot only
  (`stack_check`, `return_matches_caller`, `sp_delta`, `entry_pc`,
  `caller_pc`, `slot`, `in_table`); never `stack_b0`/`stack_b1`/
  `stack_word`, never `*_hex` RAM dumps.
- Logs and artifacts assert the same audit as
  `assert_commercial_clean`: any row containing a `*_hex` key or a
  `stack_b0`/`stack_b1`/`stack_word` key is rejected fail-closed.
- `./rom/` inputs, commercial CUE/BIN contents, names, hashes, and
  absolute paths are out of scope and never appear here (synthetic
  only). The host test is runnable without `./rom/` and without the
  pinned core.

## 6. Cold/warm difference and observation-limits table

Real-core evidence is NOT_RUN in this ledger (cites existing `#499`
driver / `#635` cold+warm synthetic controls where noted; no rerun).
`MEASURED` = host-pinned synthetic expectation; `NOT_RUN` = no
real-core run here; `BLOCKED` = unmeasured, never PASS.

| Item | Cold | Warm | Limit |
|---|---|---|---|
| Initial state (`DIRECT_SETMPR $F8` at index 0) | MEASURED (host; separate record required) | MEASURED (host; separate record required) | Real reset value NOT_RUN here; `#497` treats it as UNDETERMINED by contract |
| MPR1 gate (`$F8` only) | MEASURED (`check_stack_bank` full 0..255) | MEASURED (same) | Bank-type reality beyond `$F8`/`$FF` NOT_RUN; `$F7` stays denied unverified |
| IRQ-vs-JSR classification | MEASURED (host `classify_apicall`/`jsr_confirmed` matrix) | MEASURED (same matrix) | IRQ injection experiment NOT_RUN here (`#635` notes it as NOT_RUN); vector/delta classification only, no opcode-restore claim |
| Same-instruction IRQ hole | MEASURED as `UNMEASURED`/`BLOCKED` (host) | MEASURED as `UNMEASURED`/`BLOCKED` (host) | TAM-inside assert NOT_MEASURED (`#653` clock-point limit retained) |
| Stop-state retention axes | Reused from `#721` (NOT_RUN here) | Reused from `#721` (NOT_RUN here) | Full WRAM image / `mpr_buffer` / transfer counters BLOCKED per `#721` §3 |
| Transfer-body / VDC / device DMA / ST bypass | NOT_RUN | NOT_RUN | BLOCKED per `#367`/`#173`; outside this observer |
| Real hardware | NOT_RUN | NOT_RUN | Geargrafx observations are not hardware proof |

## 7. Gates and provenance

- Gates unchanged: `#173` / `#367` / `#459` BLOCKED;
  `release_eligible=false`; ABI VERIFIED 0/81; DEV_CALLABLE 0;
  reference JSR 0/81.
- Provenance: pinned core `b49ae82` per `tools/dependencies.json`
  (GPL-3.0, verification-only patch, never distributed); synthetic
  BIOS/CD only. No `./rom/` input, no commercial bytes, no reference
  JSR. Real-core evidence cited here is the existing `#499` driver
  and the `#635` cold+warm synthetic controls; this ledger adds no
  new real-core run.
- Review: independent review of this ledger + host test before
  merge; CI (`make host-test` subset + `make check` whitespace)
  must pass. Unmeasured stays BLOCKED.
