# Issue #721: TransferEnd state-reset order + no-resume contract (ledger)

Status: **verification ledger only (doc + host test)**. No production
mechanism is implemented, enabled, or proposed for adoption here.
`#173` / `#367` / `#459` stay BLOCKED. `release_eligible=false`,
ABI VERIFIED 0/81, DEV_CALLABLE 0, reference JSR 0/81 are unchanged.
This project is not a compatible System Card BIOS.

Scope (plan 6): resolve the known TransferEnd state-reset order
contradiction between #701 and #499 against the pinned source and the
existing fixture, and pin the stop/no-resume contract with the
retention axes compared. New real-core runs are NOT made here
(NOT_RUN); real-core columns cite the existing #499 driver runs on the
pinned core. Synthetic fixtures only; `./rom/`, commercial BIOS/ROM
bytes, and reference JSR are not used.

## 1. Pinned source fact (order)

Pinned Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`,
`src/huc6280_opcodes_inline.h`:

- `OPCodes_TransferStart` (lines 668-683): line 670 sets
  `m_transfer_state = 2` **before** the three `StackPush8` calls
  (lines 672-674, Y/A/X). Fetch16 operands and counter setup follow
  (lines 676-679).
- `OPCodes_TransferEnd` (lines 685-693): line 687 sets
  `m_transfer_state = 0` **before** the three `StackPop8` calls
  (lines 688-690, X/A/Y). PC+6 and cycles follow (lines 691-692).

So the reset-then-access order holds for **both** instructions:
state first, stack accesses second.

## 2. Contradiction resolution (#701 vs #499)

| Source | Statement | Verdict |
|---|---|---|
| `docs/473-production-safety-mechanism-design.md` §2 (PR #701) | "`OPCodes_TransferEnd` (685-690): pops X, A, Y via `StackPop8` x3, then applies PC +6 and resets state." | **INACCURATE**: the reset (line 687) precedes the pops (lines 688-690), not the reverse. |
| `tests/integration/geargrafx_stack_access_guard.py` `ROUTES["tend"]["armed"]` (#499 driver) | Comment "TransferEnd sets the transfer state to 0 BEFORE its first pop (pinned source)"; `refusal_transfer_state=0`, `instruction_transfer_state=1`, `stop_transfer_state=0`. | **MATCHES** the pinned source (line 687 before lines 688-690). |
| `tests/integration/geargrafx_stack_access_guard.py` `ROUTES["tstart"]["armed"]` (#499 driver) | Comment "TransferStart sets the transfer state to 2 BEFORE its first push"; `refusal_transfer_state=2`, `stop_transfer_state=2`. | **MATCHES** the pinned source (line 670 before lines 672-674). |
| `docs/473-selection-supplement-s7s8.md` §2 | Records the above as an unresolved known difference and asserts only "TransferEnd includes a state transition with the body already written". | Consistent placeholder; superseded by §1 of this ledger for the order fact. The #701 doc text itself is **not** edited here (out of scope). |

Real-core measurement anchor (existing #499 runs, not rerun here):
the `tend-armed` row passes `side_effect_check` only with
`refusal.transfer_state == 0` while `refusal_instruction_start`
carries state 1, and stops with state 0. A pop-after-reset core
would present refusal state 1 instead and be rejected by that check.
The `tstart-armed` row analogously requires refusal state 2.
These expectations are pinned by `test_transfer_state_facts_are_pinned`
in `tests/host/test_stack_access_guard.py` and re-pinned in §5 below.

## 3. Retention axes: what is compared, per stop

Axes: PC, S, P, A, X, Y, MPR0-7, `m_mpr_buffer` (#654),
VDC (AR/R0/R1/R7/SR/read_buffer), WRAM/stack image, transfer
counters (source/dest/length/count), destination byte, IRR/IRQ,
`instructions_after_refusal` / halted / latched.

| Axis | `tstart-armed` (MPR1=$FF, refused push) | `tend-armed` (MPR1=$FF, refused pop) | `allowed` (MPR1=$F8) / `unguarded` control |
|---|---|---|---|
| PC | Compared; stop at guest start (`0x1E00`); `changed={"transfer_state"}` only besides PC bookkeeping | Compared; stop `0x1E07`; `changed={pc, transfer_state, a, x, y}` | `allowed`: exact PC/S/window/dst asserted; `unguarded`: ordered I/O accesses asserted |
| S | Unchanged at refusal and stop (checked three ways) | Unchanged at refusal and stop (patch refuses **before** `S++`; the only pre-check `S++` is mutant `mut-popinc`, RED) | Window/`stack_bytes_changed` asserted |
| P/A/X/Y | Unchanged (only `transfer_state` dirty) | A/X/Y dirty (refused pops yield 0 and the instruction still runs to its end); P unchanged | Asserted per route table |
| MPR0-7 | Full 8-bank snapshot compared; only MPR1 varies in these runs (WRAM `$F8` vs I/O `$FF`) | Same; `irqtam` additionally records `stop_mpr1=$FF` | `stop MPR1` checked (`$F8` allowed) |
| `m_mpr_buffer` (#654) | Probe exposes `GG_VerifyMprBuffer()` and the `mb_*` routes log it, but the #499 `tstart`/`tend` armed checks do **not** assert it | Same | **BLOCKED**: not part of this contract; buffer retention across a TransferEnd stop is UNMEASURED here |
| VDC | `ar/r0/r1/r7/sr/read_buffer` before==after; `vdc_ar` at guard call equals pre-instruction value (no earlier write) | Same checks; `unguarded` control shows the pops read VDC (`$2102/$2103/$2104`) and clear status | `allowed`: no I/O access at all |
| WRAM/stack image | 5-byte window `$0100-$0104` + `stack_bytes_changed` counter compared (0 on armed); **not** a full-WRAM image | Same; `tend-armed` additionally asserts window `[0,0,0x34,0x12,0x07]`, `stack_bytes_changed=3` (probe-side WRAM bookkeeping of the allowed setup), `dst=$5A` | `allowed`: exact window asserted |
| Transfer counters | **BLOCKED**: the patch exposes `GG_VerifyTransferSource/Dest/Length/Count` and the probe snapshots them at refusal (`g_t_at_refusal`), but the `tstart`/`tend` armed classifier does not assert them | Same | UNMEASURED for this contract |
| Destination byte | `dst` unchanged asserted | `dst=$5A` asserted (body already executed before TransferEnd) | `allowed` asserts `dst` |
| IRR/IRQ | `irr_before==irr_after` required (fail-closed; device-originated drift rejects the run, not attributed to the guard) | Same | Same |
| No-resume | `halted && latched && refused`, `instructions_after_refusal==0`, `io_after_refusal==0` | Same | n/a |

Unobserved is BLOCKED, never PASS: full WRAM image beyond the
5-byte window, `m_mpr_buffer` retention, transfer-counter values at
the stop, cycle counts, MMIO outside the CPU `Memory::Read/Write`
path (ST0/ST1/ST2 `WriteFromCPU`, device DMA), NMI (absent on the
pinned core), libretro build, real hardware.

## 4. Dirty-stop summary (TransferStart / TransferEnd)

- `tstart` refused push: state dirty **before** the first stack
  access (`transfer_state` already 2). Verdict BLOCKED: side effect
  prevented, stop state not consistent (`changed={"transfer_state"}`).
- `tend` refused pop: state already 0 at the first pop (line 687),
  while the instruction started with state 1; A/X/Y dirty (pops
  yield 0 and the instruction runs on); body already written
  (`dst=$5A`, #470 O4). Verdict BLOCKED
  (`changed={pc, transfer_state, a, x, y}`).
- Clean stops are only the plain single-byte push routes
  (`pha/php/phx/phy/phasame/pharestore`, verdict PASS =
  `REGISTERS_UNCHANGED_PC_ADVANCED`); every other route is BLOCKED
  by construction. PASS never claims a restored pre-instruction
  state (the refused push is dropped; re-running would need a PC
  rewind to the instruction start).

## 5. No-resume contract

After any latched refusal the guest is **never resumed** (kill
semantics only; no resume, no retry, no pop suppression):

- The probe breaks out of the run loop on `gg_verify_stack_refused`
  (and halted/latched flags are asserted on every armed row).
- `instructions_after_refusal != 0`, `halted == false`,
  `latched == false`, or any `io_reads/io_writes/io_after_refusal`
  rejects the row (side-effect axis, observation-only: config
  fields `guarded`/`mutation` are not read).
- Resume-permitting mutants are RED: `mut-nolatch` (no latch, no
  stop: guest resumed), `mut-ignore` (refusal computed but access
  performed), `mut-nostop` (latch kept, stop removed), plus the
  falsified `resumed` / `not_halted` / `not_latched` rows and every
  `unguarded` control fed to the armed classifier.
- Host pinning: `tests/host/test_721_transferend_noresume.py`
  (`NoResumeContractTests`, `FalsifiedRowTests`).

## 6. Byte side effect ≠ full CPU state

A stop with zero byte side effects is **not** evidence of full CPU
state retention:

- `phasame` / `pharestore`: a final-state-only classifier accepts
  the `unguarded` row (same-value write / write-then-restore leave
  no final trace), so the event log is required to reject it
  (`state_only_classifier_accepts` vs `check_armed`).
- `tend`/`tstart`: side effect PREVENTED on the armed runs, yet the
  state verdict stays BLOCKED (dirty `transfer_state`, and A/X/Y
  for `tend`). No route is promoted to consistent-stop on byte
  silence alone.

## 7. Gates and provenance

- Gates unchanged: `#173` / `#367` / `#459` BLOCKED;
  `release_eligible=false`; ABI VERIFIED 0/81; DEV_CALLABLE 0;
  reference JSR 0/81.
- Provenance: pinned core `b49ae82` per `tools/dependencies.json`
  (GPL-3.0, verification-only patch, never distributed);
  synthetic BIOS/CD only. No `./rom/` input, no commercial bytes,
  no reference JSR. Real-core evidence cited here is the existing
  #499 driver (`make stack-access-guard`); this ledger adds no new
  real-core run.
- Review: independent review of this ledger + host test before
  merge; CI (`make host-test` subset + `make check` whitespace)
  must pass. Unmeasured stays BLOCKED.
