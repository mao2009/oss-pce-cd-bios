# Phase ABI-1: reference System Card BIOS static ABI review and safety pre-review

Status: **Phase 0 to Phase 3 complete, Phase 4 not run.**
Scope: the seven priority API slots of the **reference (pure) Super System Card 3.0
BIOS** — `$1E`, `$3C`, `$3D`, `$3F`, `$40`, `$41`, `$42`.

This document is the human readable companion of
[`spec/abi1_reference_review.json`](../spec/abi1_reference_review.json), which is
the machine readable ledger. The ledger is validated by
[`tools/abi1_reference_review.py`](../tools/abi1_reference_review.py) and by
[`tests/host/test_abi1_reference_review.py`](../tests/host/test_abi1_reference_review.py).

**What this document is not.** It is not a compatibility claim, not a BIOS release
claim and not an ABI verification. `release_eligible` stays `false`, the reference
ABI axis stays `UNVERIFIED`, and no slot in this document is `VERIFIED`. Every
observation here was produced locally, from a private image that is never
committed, never uploaded and never printed.

---

## 1. Evidence classes: two axes, counted apart

The review keeps a **static review** axis and a **verification** axis apart.
Conflating them is what would over-claim, so the ledger stores them in
different fields and the validator refuses a ledger that mixes them.

### 1.1 The static review axis (evidence classes of the eleven items)

| class | meaning |
| --- | --- |
| `OBSERVED_STATIC` | read out of the private image by the local static audit; reproducible by the owner who holds the image. It is an observation **of the image**, not of a call |
| `SOURCE_FACT` | taken from a public caller or emulator source, with the source named |
| `HYPOTHESIS` | plausible, not established; must not be used as an expectation |
| `UNKNOWN` | not established; deliberately left unset |

### 1.2 The verification axis (the ABI state of a slot)

| state | meaning | reachable in this phase |
| --- | --- | --- |
| `NOT_RUN` | no reference callee was executed for this slot | yes, for all seven |
| `BLOCKED` | the callee was not executed because an execution gate or a safety condition blocks it | yes |
| `OBSERVED` | a **reproducible** real core observation exists for the slot | no |
| `PARTIAL` | a **reproducible** real core observation exists, but it covers only part of the contract | no |
| `VERIFIED` | an owner approved promotion of a reproducible observation | no, and not representable today |

Two rules follow, and the validator enforces both:

* a static review never reaches `OBSERVED`, `PARTIAL` or `VERIFIED`. The seven
  reviewed slots are a **static review of seven APIs**, not a partial
  verification of seven APIs;
* a **prior local only** observation (a measurement that exists but cannot be
  reproduced through the committed tooling) is recorded on its own field and
  never counts as a reproducible observation. Slot `$1E` is the only slot with
  such an observation.

An item with no evidence stays `UNKNOWN`. A static audit reports the hazard opcodes found over
the explored code, which is not an upper bound: a clean profile is necessary for a safe
execution, never sufficient on its own. The findings are complete for the
explored code only when the walk resolved every path; when it could not (an
unknown page bank, an undecodable byte, an exhausted budget) the findings are
partial, and the ledger records that in `unresolved` and `budget_exhausted`. The validator
refuses a safe verdict for such a profile, so the seven records below cannot
hide a truncation.

---

## 2. Phase 0 — foundation

| item | value |
| --- | --- |
| main HEAD at the start of the phase | `28176c9` (`Merge pull request #468 from mao2009/feat/dev-mul16u-sideeffects`) |
| open pull requests | `#470` only (Draft, `feat/367-transferstart-push`) |
| reference BIOS image | `rom/syscard3.pce`, 262 656 bytes, SHA-256 `df4f75feebb95e53dfef72dea0787df743e3474ba046ff5a4cb88e34dda93ff1` |
| image layout | 512 byte header, 256 KiB body, 32 banks of 8 KiB |
| pinned emulator | Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3` (GPL-3.0, never bundled) |
| pinned decoder | cc65 `71746c829e77f74c2b601a7d16821170c2610df3` `da65`, HuC6280 CPU description (zlib) |
| claim ledger | `spec/api_contracts.json`, 81 slots, all `verification: NOT_RUN` |
| DEV ledger | `spec/dev_implementations.json`, 7 implemented slots, 0 `DEV_CALLABLE`, 0 `VERIFIED` |
| reference JSR execution gate | `tools/reference_jsr_gate.py`, closed for **81/81** slots (the tool's own count: the ledger holds one row and the other 80 slots have no record at all) |

The image hash is recorded because it was actually measured locally. The image
itself, its bytes and any derived code stay on the owner's machine.

### 2.1 The reference JSR execution gate is closed

`tools/reference_jsr_gate.py::resolve()` cannot return `ALLOWED` by design: it
refuses when the ledger has no reviewed record, when any hazard control is not
`CONTROLLED` and when no independent approval is supplied, and
`require_allowed()` reads the committed ledger only. `validate_ledger()` even
requires every ledger row to restate `execution: "BLOCKED"`.

Consequence for this phase: **the committed runner cannot execute a reference
JSR for any slot**, including `$1E`. A local real-core observation for `$1E`
(recorded as local only; its values are withdrawn from public documents) was taken before
`tools/reference_jsr_gate.py` was wired into the runner (PR #372), and it is
therefore **not reproducible through the committed tooling today**. The gate is
not touched by this phase, and no gate is bypassed by it.

### 2.2 What `#470` leaves open for this phase

`#470` (Draft) measures the refusal path of the block transfer `TransferStart`
push and shows that a refused push is **not** a safe termination: the
`TransferEnd` pop then reads three never written slots, which on a `$FF` stack
page are I/O registers, so A/X/Y are overwritten and the VDC status register is
cleared. The gate stays `BLOCKED`.

For this phase the relevant consequence is precise: a callee that uses a block
transfer would inherit that unresolved hazard, because the refusal path is not a
safe stop. The static audit therefore counts block transfer opcodes
(`TII`/`TDD`/`TIA`/`TAI`/`TIN`) as a first class hazard. **None** of the seven
reviewed slots contains one, so `#470` does not block any of them; it blocks
the *general* case and is recorded as such.

### 2.3 A correction to an existing published statement

`docs/reference-bios-test-plan.md` describes the core's zero page and stack
placement as "a core deviation" because "real hardware keeps the stack in page
0". That is **wrong**, and the safety reasoning of this phase depends on it.

The HuC6280 manual places the zero page at `$2000`: the high order byte of a
zero page address "always contains the logical address 20", and the stack lives
at `$2100`, both independent of the MPRs. The pinned core's non-`GG_TESTING`
build defines `ZERO_PAGE_ADDR = 0x2000` and `STACK_ADDR = 0x2100`
(`huc6280.h`), so it is **faithful to the hardware**, not a deviation. The
observable consequence is the opposite of what the old text implies: a zero page
operand of the reference BIOS lands in page 1 on the core *and* on hardware, so
the guarded probe's page 1 allowlist covers it correctly. The paragraph in
`docs/reference-bios-test-plan.md` is corrected in this pull request.

---

## 3. Phase 1 — why this order

The order below is the **investigation** order, not an execution permission
list. It is the order in which the static review was carried out and the order
in which the remaining work should continue.

| priority | API | slot | reason |
| --- | --- | --- | --- |
| 1 | `EX_GETVER` | `$1E` | the only slot with any prior reference-side observation; the cheapest way to re-establish a measurement chain end to end |
| 2 | `EX_BINBCD` | `$3C` | a value contract that the project already has a DEV-side model for, so a reference measurement would be directly comparable |
| 3 | `EX_BCDBIN` | `$3D` | same, and the inverse conversion, so the pair constrains each other |
| 4 | `MA_MUL8U` | `$3F` | a pure register contract with a DEV-side model; establishes whether the arithmetic family shares one dispatcher |
| 5 | `MA_MUL8S` | `$40` | the signed variant of the previous one |
| 6 | `MA_MUL16U` | `$41` | a wider contract, likely to expose the same dispatcher with a different payload |
| 7 | `MA_DIV16S` | `$42` | the most complex of the four, and the one with the deepest error contract (divide by zero, overflow) |

The order was **not** chosen from the DEV ROM's slot numbers. The static audit
of the reference image confirmed the jump table independently, and the
prioritisation follows the reference-side evidence, not the DEV side.

---

## 4. Phase 2 — static ABI investigation

### 4.1 Method

1. The private image is read locally only. The tool refuses to run when
   `GITHUB_ACTIONS=true`, so no private image can ever be read in CI.
2. The 81 entry jump table at `$E000` is parsed: every entry must be a 3 byte
   `JMP abs`, and every target must be distinct.
3. For each requested slot, the entry target is followed with a worklist walk
   over `(pc, MPR map, A value, return stack)` states. The MPR map is modelled
   concretely: a `TAM` whose source value is known updates the affected page,
   every other `TAM` marks the affected page unknown. The accumulator value is
   modelled the same way: only an immediate load into `A` makes it known, and
   every other opcode that could write `A` makes it unknown. A stale value
   would make a later `TAM` resolve a cross page callee in the wrong bank, so
   the model errs towards "unknown" everywhere.
4. Instructions are decoded with the **pinned cc65 `da65`** (HuC6280 CPU
   description) — the same disassembler the repository already uses for the DEV
   ROM — so no hand written opcode table can become a verification blind spot.
5. Every instruction is classified into a closed hazard vocabulary. The output
   is a fixed shape projection: counts, booleans and a closed vocabulary. No
   ROM byte, no disassembly text and no internal routine address is emitted.

The tool is [`tools/reference_rom_static_audit.py`](../tools/reference_rom_static_audit.py);
its host tests build a synthetic image and never touch a private input
([`tests/host/test_reference_rom_static_audit.py`](../tests/host/test_reference_rom_static_audit.py)).

Two local hygiene notes. `da65` needs a file, so the auditor writes a sixteen
byte window of the private image into a temporary directory for every decode;
those bytes never reach the repository, but an aborted run can leave them in the
system temporary directory. And the CI refusal lives in the library entry point
as well as in the CLI, so a future tool that imports the module cannot read a
private image in CI either.

### 4.2 The jump table is confirmed against the image

| property | result |
| --- | --- |
| first entry | `$E000` |
| entry count | 81 |
| entry stride | 3 bytes, every entry a `JMP abs` |
| targets | 81 distinct target addresses |
| conclusion | for this image the candidate entry addresses in `spec/api_contracts.json` are no longer only a public-header hypothesis |

The internal target addresses are deliberately **not** published, and the
validator rejects any four digit address in the ledger that is not a public
entry address or a documented CPU constant.

### 4.3 The eleven static items

The full table for every slot is in the ledger (`static_items`). The summary:

| item | `$1E` | `$3C` | `$3D` | `$3F` | `$40` | `$41` | `$42` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| entry address and basis | OBSERVED | OBSERVED | OBSERVED | OBSERVED | OBSERVED | OBSERVED | OBSERVED |
| caller supplied registers | OBSERVED (none) | OBSERVED (A) | OBSERVED (A) | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| caller preconditions | OBSERVED (none) | OBSERVED (A ≤ 99) | OBSERVED (two BCD digits) | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| expected return value | OBSERVED (X, Y = image version bytes) | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| possibly changed registers | OBSERVED (X, Y) | OBSERVED (A, X) | OBSERVED (A, X) | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| CPU flag handling | OBSERVED (N, Z) | OBSERVED (C carries the compare) | OBSERVED (C carries the digit test) | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| stack usage | OBSERVED (balanced, 2 byte JSR) | OBSERVED (balanced) | OBSERVED (balanced) | OBSERVED (≥ 2 byte JSR) | OBSERVED | OBSERVED | OBSERVED |
| MPR change | OBSERVED (none) | OBSERVED (none) | OBSERVED (none) | OBSERVED (switch + restore) | OBSERVED | OBSERVED | OBSERVED |
| RAM / I/O / VDC / CD side effects | OBSERVED (none) | OBSERVED (1 zero page store) | OBSERVED (2 zero page stores) | OBSERVED (page 1 only) | OBSERVED (page 1 only) | OBSERVED (page 1 only) | OBSERVED (page 1 only) |
| interrupt or timing dependence | OBSERVED (the callee does not change the interrupt state) | OBSERVED (same) | OBSERVED (same) | OBSERVED (same) | OBSERVED (same) | OBSERVED (same) | OBSERVED (same) |
| executable / BLOCKED conditions | OBSERVED | OBSERVED | OBSERVED | OBSERVED | OBSERVED | OBSERVED | OBSERVED |

`UNKNOWN` is not a gap in the review; it is the result. The value contract of
`$3C` to `$42` is **not** established by a static audit, and this document does
not pretend otherwise.

### 4.4 What the static audit found for each slot

| slot | API | callee instructions | return | hazards found |
| --- | --- | --- | --- | --- |
| `$1E` | `EX_GETVER` | 3 | `RTS` | none |
| `$3C` | `EX_BINBCD` | 19 | `RTS` | 1 store to the zero page |
| `$3D` | `EX_BCDBIN` | 23 | `RTS` | 2 stores to the zero page |
| `$3F` | `MA_MUL8U` | 36 | `RTS` | 1 cross page call, 1 MPR read, 2 MPR writes, 4 page 1 stores (3 of them zero page) |
| `$40` | `MA_MUL8S` | 62 | `RTS` | 1 cross page call, 1 MPR read, 2 MPR writes, 6 page 1 stores (5 of them zero page) |
| `$41` | `MA_MUL16U` | 43 | `RTS` | 1 cross page call, 1 MPR read, 2 MPR writes, 15 page 1 stores (14 of them zero page) |
| `$42` | `MA_DIV16S` | 89 | `RTS` | 1 cross page call, 1 MPR read, 2 MPR writes, 23 page 1 stores (22 of them zero page) |

Not found in any of the seven: a block transfer, a VDC direct write (`ST0`,
`ST1`, `ST2`), an interrupt enable, an indirect jump, an unresolved branch
target, a budget exhaustion, or a **non returning path**. The last one is a
property the auditor can establish: it keeps the successor graph and computes,
for every reachable state, whether a return is still reachable, so a callee
with a self loop is reported instead of looking clean because some other path
returned (`non_returning_states` is 0 for all seven).

Two structural facts matter for the rest of the phase:

* The four arithmetic slots are **dispatchers**. Each one switches a page bank
  register to a fixed BIOS bank, calls the operation in that bank, and restores
  the saved value before returning. The bank switch is visible to a runtime
  monitor, and the guarded probe refuses it.
* The two BCD slots and the arithmetic slots use the CPU zero page, which the
  HuC6280 places at `$2000`. Those stores are inside page 1, so they are inside
  the probe's RAM write allowlist, but they are **real side effects**: they
  clobber the System Card zero page area, and they will show up in a RAM
  difference.

---

## 5. Phase 3 — safety pre-review

The verdict is about the *reference* callee, under the existing guarded probe
(`tests/integration/geargrafx_reference_jsr.cpp`) and the pinned core.

### 5.0 A static verdict is not an execution permission

`EXECUTION_SAFE` means **the static analysis found no dangerous path in the explored code**. It does
not mean that the call may be made. The two are separate fields, and the
validator keeps them separate:

| field | values | meaning |
| --- | --- | --- |
| `safety.static_verdict` | `EXECUTION_SAFE`, `EXECUTION_SAFE_WITH_OBSERVABLE_ZERO_PAGE_WRITE`, `EXECUTION_BLOCKED` | what the static profile of the callee shows |
| `safety.scope` | `STATIC_PROFILE_ONLY` | the verdict is about a static profile and nothing else |
| `safety.execution_permission` | `BLOCKED` (the only representable value) | whether a reference JSR may be executed |
| `safety.approval_requirement` | a fixed marker | a separate, independent safety approval procedure is required first, and it is not implemented |

So every one of the seven records is a static `EXECUTION_SAFE` (or
`EXECUTION_BLOCKED`) **and** an execution permission of `BLOCKED`. The static
auditor's own output carries the same two markers (`scope` and
`execution_permission: NOT_PRODUCED_BY_THIS_TOOL`), so its projection cannot be
fed into the execution gate as a permission either.

The repository's reference JSR execution gate
(`tools/reference_jsr_gate.py`) is a **separate** mechanism. It reads
`spec/reference_jsr_gate.json` and `spec/api_contracts.json`, it does not read
this ledger, and it refuses all 81 slots. The host test suite checks that
refusal, and that the gate source does not mention the Phase ABI-1 fields.

A permission to execute a reference BIOS therefore needs a **separate,
independent safety approval procedure**, which does not exist yet. This phase
does not implement it, and it does not weaken the existing gate.

| slot | safety verdict | why |
| --- | --- | --- |
| `$1E` | `EXECUTION_SAFE` | the callee has no store, no MPR write, no VDC direct write, no block transfer and no interrupt enable, and it returns with `RTS`; its two reads stay inside the API bank window, so the memory mapped I/O page is unreachable |
| `$3C` | `EXECUTION_SAFE_WITH_OBSERVABLE_ZERO_PAGE_WRITE` | one store, into the zero page, therefore inside the allowlist; it is still a side effect that clobbers the System Card zero page area |
| `$3D` | `EXECUTION_SAFE_WITH_OBSERVABLE_ZERO_PAGE_WRITE` | two such stores |
| `$3F` | `EXECUTION_BLOCKED` | the dispatcher switches a page bank register at run time, which the probe's dynamic MPR detection refuses before the next instruction runs |
| `$40` | `EXECUTION_BLOCKED` | same |
| `$41` | `EXECUTION_BLOCKED` | same |
| `$42` | `EXECUTION_BLOCKED` | same |

### 5.1 Residual risks that apply to every verdict

* A static profile reports the findings over the explored code, not a runtime
  observation; it is not an upper bound on hazards. The walk is limited to 20 000 states and 24 call
  levels; none of the seven walks hit that limit, and no walk ended in an
  unresolved target.
* The arithmetic dispatchers switch to a bank that the static model resolves
  from the `TAM` operand. The reachable code *inside* that bank is followed,
  but data dependent targets inside the operation are not enumerated.
* The pinned core implements **no** NMI source, so a non maskable interrupt
  cannot be taken during a call in this environment. That is a property of the
  measurement environment, not of the hardware.
* None of the seven callees changes the interrupt disable state, so the
  interrupt behaviour during a call is the caller's. The guarantee "no
  maskable interrupt is taken" only holds for a caller that enters with the
  interrupt disable flag set, which is how the guarded probe enters the
  callee. A caller that enters with interrupts enabled does not get it, and
  the ledger records that as a caller precondition rather than as a property
  of the callee.

### 5.2 What the probe can and cannot contain

| side effect | probe mechanism | verdict |
| --- | --- | --- |
| store outside the allowlist | `Memory::GetMemoryMapWrite()` denial **and** a CPU address write breakpoint | prevented and detected |
| store into the I/O page | no MPR may hold `$FF`, and the run is rejected if one appears | unreachable |
| MPR change at run time | every MPR is compared after each step | rejected, fail closed |
| VDC direct write (`ST0`/`ST1`/`ST2`) | **not** intercepted: these bypass `Memory::Write()` | not contained; therefore a callee that uses them would be blocked |
| block transfer | the byte writes are guarded, but the `#470` refusal path is not a safe termination | not contained; therefore a callee that uses one would be blocked |
| interrupt taken during the call | the probe does not stop on an interrupt | not contained; therefore a callee that enables interrupts would be blocked |
| instruction budget | one instruction at a time | a backstop, never the safety argument |

---

## 6. Phase 4 — real core observation: not run

**No reference BIOS API was executed in this phase.** The reasons, in order:

1. The repository's reference JSR execution gate is closed for all 81 slots
   (§2.1). Bypassing it is not acceptable and was not done.
2. The parent tracker `#173` is still `BLOCKED` and keeps the in-core safety
   boundary open.
3. For `$3F` to `$42` the safety verdict is `EXECUTION_BLOCKED` in any case, so
   the call would be refused by the probe itself.

### 6.1 Re-evaluation of the existing `$1E` observation

The prior local observation (values withdrawn from public documents, owner decision
2026-10-28) is not compared here, so this document asserts no agreement with the static audit. The static
audit of the image shows a three instruction callee that loads two bytes from the
BIOS image's own version area and returns.

What the prior observation does **not** establish, and what this phase therefore
still lacks:

* it covers **one** input vector, one call shape and one environment;
* it was taken before the execution gate was wired into the runner, so it is
  **not reproducible** through the committed tooling;
* it is local only evidence with no attestation, and the producer is not
  authenticated, so the repository's evidence rules refuse to promote it;
* the negative and mutation coverage that a `VERIFIED` claim would need is
  missing.

Consequently `$1E` records `observation.prior_local_only: PRESENT` with the
source document, and its ABI state stays `NOT_RUN` on the verification axis:
a past local only observation is not a reproducible measurement and does not
promote the slot. The slot is **not** `OBSERVED` and **not** `VERIFIED`.

### 6.2 What a `VERIFIED` claim would require

The acceptance conditions are defined here, before any promotion is attempted:

1. the reference JSR execution gate can express a reviewed permission for the
   slot, and the owner records it;
2. the safety verdict above is exercised at run time, and the in-core guard
   negative and positive tests still pass;
3. at least three input vectors, cold and warm, with the full register, flag,
   `SP`, `MPR`, RAM difference and I/O access set recorded;
4. at least one negative case per observation class: a wrong entry, a forbidden
   store, a tampered fixture, a budget exhaustion;
5. a mutant of the observer that must not produce the same verdict;
6. a second, independent emulator or real hardware run;
7. the producer authentication that `#171` requires before any trusted
   `VERIFIED` promotion.

---

## 7. Phase 5 — verification infrastructure

### 7.1 Reused, not rebuilt

| need | existing mechanism | used here |
| --- | --- | --- |
| instruction decoding | pinned cc65 `da65` (HuC6280) | yes, the static audit reuses it |
| per slot claim ledger | `spec/api_contracts.json` | yes, the validator cross-checks every entry address |
| observation record shape | `oss-pce-cd-bios/reference-observation/v1` | unchanged |
| JSR observation shape | `oss-pce-cd-bios/jsr-observation/v1` | unchanged |
| in-core guard | `tests/integration/geargrafx_reference_jsr.cpp` | unchanged |
| execution gate | `tools/reference_jsr_gate.py` | unchanged, still closed |
| evidence classes | `docs/abi-evidence-classification.md` | the three axes are kept apart |

### 7.2 Added in this phase

* `tools/reference_rom_static_audit.py` — the local-only static auditor. It
  refuses to run in CI, never prints a byte, and emits a fixed shape
  projection. Its host tests use a synthetic image only.
* `spec/abi1_reference_review.json` — the machine readable review ledger.
* `tools/abi1_reference_review.py` — the ledger validator. It refuses a
  `VERIFIED` claim, a `release_eligible` claim, a drift against the claim
  ledger, a missing static item, an unknown vocabulary value, a decoded
  instruction in the published text and any non public address in it.
* `tests/host/test_abi1_reference_review.py` and
  `tests/host/test_reference_rom_static_audit.py` — the positive and the
  negative coverage of both.

### 7.3 Still missing

* A JSONL evidence writer. The repository has a single record shape
  (`reference-observation/v1`) but no stream format, and no field by field
  comparator between a DEV observation and a reference observation. Both are
  needed before a multi case sweep can be run, and neither is needed before
  the execution gate is opened, so neither is built here.
* An `MPR` delta rule for the probe. The arithmetic dispatchers would need it
  (§5). It must be proved safe on its own, with the `#470` style refusal path
  in mind, and it is a separate piece of work with its own review.

---

## 8. Reference ABI versus DEV ABI

| aspect | reference BIOS | this project's DEV ROM |
| --- | --- | --- |
| `$1E` result | `X` and `Y` carry the version bytes **read from the BIOS image** | `X` and `Y` are **hard coded** to 3 and 0 |
| `$1E` mechanism | two absolute loads from the image, then `RTS` | two immediate loads, then `RTS` |
| `$1E` observable outcome for the measured input | withdrawn from public documents (owner decision, 2026-10-28) | `X = 3`, `Y = 0`, `A` preserved (DEV-defined, not a reference value) |
| `$3C` to `$42` value contract | **not established** | a project-own specification exists (`docs/ex-binbcd-internal.md`, `docs/ex-bcdbin.md`, `docs/ma-mul8s.md`) and is explicitly *not* the reference contract |
| side effects | the reference BCD and arithmetic paths write the zero page and, for the arithmetic slots, switch a page bank | the DEV overrides stay in the stack page and the zero page, and never change an MPR |

The observable outcome of `$1E` on the reference side is not restated here (the
observed values are withdrawn from public documents, owner decision 2026-10-28). The
DEV implementation is not the same implementation: the reference value depends on the
image, the DEV value does not, and no agreement between the two is asserted as a contract. A DEV
observation must never be used as the expected value of a reference observation
(`docs/abi-evidence-classification.md`, axis separation).

---

## 9. Metrics

Every number below is **derived from the ledger** by
`tools/abi1_reference_review.py::compute_metrics()`, and the ledger stores the
same numbers in its `summary` block, which the validator recomputes and
compares. A hand written or stale aggregate fails the validation.

| metric | value | where it comes from |
| --- | --- | --- |
| reference ROM static review done | **7** | the seven per API records with a static profile |
| reference ROM real core observation (past, local only) | **1** | slot `$1E`, `observation.prior_local_only` |
| reproducible reference ROM real core observation in this phase | **0** | `observation.reproducible_real_core` |
| reference ABI `VERIFIED` | **0** | the ABI state axis |
| reference ABI `PARTIAL`/`OBSERVED` (reproducible) | **0** | the ABI state axis |
| reference ABI `NOT_RUN` | **7** of the seven reviewed, 74 of the remaining | the ABI state axis |
| DEV ROM implemented APIs | **8** | `spec/dev_implementations.json` |
| owner approved `DEV_CALLABLE` | **0** | `spec/dev_implementations.json` |
| reference JSR execution permission | **BLOCKED** for all seven | `safety.execution_permission` |
| `release_eligible` | **false** | the ledger and the source ledgers |

---

## 10. Unblock conditions and the next APIs

### 10.1 To lift the execution block for `$1E`

0. a **separate, independent safety approval procedure** is implemented and
   carried out. It does not exist yet, this phase does not implement it, and
   the static review in this ledger is not such a procedure: the static
   `EXECUTION_SAFE` verdict of §5 is a statement about the callee's side
   effect profile and nothing else.
1. the owner records a reviewed gate entry for slot `$1E` in
   `spec/reference_jsr_gate.json`, and the gate module is changed so that a
   reviewed state can actually be expressed (today `resolve()` cannot return
   `ALLOWED`, and `validate_ledger()` refuses any row that does not restate
   `BLOCKED`);
2. the in-core guard negative and positive tests still pass unchanged;
3. the observation is repeated over several input vectors, cold and warm, with
   the full observation set of §6.2;
4. the negative and mutant coverage of §6.2 exists;
5. the result stays `LOCAL_ONLY_NOT_VERIFIED` until `#171` is settled.

### 10.2 To lift the execution block for `$3C` and `$3D`

Everything from §10.1, plus:

* the caller contract is specified and reviewed: which register carries the
  input, what the output is, what the error signalling is;
* the zero page write is accepted as an expected, bounded side effect, and the
  probe's allowlist keeps it inside page 1;
* the observation records the zero page difference, so the side effect is
  visible rather than hidden.

### 10.3 To lift the execution block for `$3F` to `$42`

Everything from §10.2, plus:

* the probe gains a reviewed `MPR` delta rule that accepts a switch to a
  read only BIOS bank and its restoration, and still rejects a switch to a
  writable bank;
* the switch is proved safe against the `#470` refusal path, which is a
  separate open item;
* the ledger validator is amended in the same change: as it stands, any
  callee that writes an MPR at run time is forced to `EXECUTION_BLOCKED`, so
  the rule and the probe have to be lifted together, and the lift has to be
  reviewed on its own.

### 10.4 The next APIs to review

`$3E` (`EX_RND`), then the remaining slots in ascending slot order, with the
same static method. The static method is cheap and does not need the execution
gate, so the remaining 74 slots can be statically triaged first and the
measurement effort can be spent on the slots whose static profile is clean.

---

## 11. Independent review

An independent review (a separate read-only session, ChatGPT) examined this
change against the six hard constraints. It confirmed the ledger reproduces the
auditor's projection and that no path leaks a byte, a disassembly line or an
internal address, and it found six substantive defects. All of them are fixed
in this pull request:

| finding | fix |
| --- | --- |
| the accumulator model missed two opcodes that write `A`, so a stale value could make a later `TAM` resolve a cross page callee in the wrong bank | the accumulator model was rewritten: only an immediate load into `A` makes the value known, everything else makes it unknown, and a regression test with a synthetic callee pins the behaviour |
| the "no return" hazard only recorded that *some* path returned, so a callee with a self loop looked clean | the auditor now keeps the successor graph and computes, for every reachable state, whether a return is still reachable; the ledger records `non_returning_states` and the validator refuses a safe verdict when it is not 0 |
| the validator accepted a ledger whose static profile was not clean over the explored code | `unresolved` must be empty and `non_returning_states` must be 0 for any verdict other than `EXECUTION_BLOCKED`, and a runtime MPR switch, a block transfer, a VDC direct write or an interrupt enable now force `EXECUTION_BLOCKED` |
| the validator accepted a ledger that claimed a fresh execution | the plain `OBSERVED_LOCAL_ONLY` form was removed from the vocabularies; only the `..._PRIOR` form is allowed, and it has to name a repository file that holds the earlier observation |
| the CI refusal and the `TAM` mask mapping, the two properties the code calls critical, were untested | both now have a test; the mask test distinguishes the two candidate banks, and the CI refusal is checked in the CLI *and* in the library entry point |
| the published-text guard missed the memory writing opcodes, was case sensitive, and its test was satisfied by the address rule instead | the mnemonic list now covers every operand carrying opcode of the HuC6280 table, the match is case insensitive, and the test uses a payload that only the instruction rule can catch |

The review also corrected wording: "81 distinct internal routines" became "81
distinct target addresses", the interrupt item now states that the callee does
not change the interrupt disable state (the behaviour is the caller's) instead
of claiming that no interrupt can be taken, and the block move classification
now follows the pinned core, where all five block moves write their
destination.

A **second** independent pass reviewed the fixes themselves. It confirmed that
all six findings are closed, that the committed ledger reproduces a fresh audit
field for field, and that the new accumulator model is neither unsound nor
over-conservative. It found four further defects, which are fixed here as
well: the published store counts of `$40`, `$41` and `$42` had been edited to
the wrong values (they are the hazard counts 6, 15 and 23; the zero page
counts 5, 14 and 22 are a subset), the published-text guard still missed the
`ADC`, `RMB*`, `SMB*`, `BBR*` and `BBS*` opcodes, the `prior_evidence` check
accepted a path outside the repository, and the forcing list of the validator
did not cover a data dependent or a non page 1 store. The mnemonic list is now
compared against the pinned cc65 opcode table by a host test, so the claim
"every operand carrying opcode" is machine checked rather than asserted.

## 12. Verification of the two review fixes

The two findings of the conditional review are separated mechanically, and the
separation is checked by the host suite. The RED -> GREEN record below runs
the **pre-fix** validator and the **post-fix** validator against the same
defect; the pre-fix side is the reviewed commit `2f1c703`.

| defect | old (`2f1c703`) | new |
| --- | --- | --- |
| seven static reviews carried as `PARTIAL`/`OBSERVED` | ACCEPTED | REJECTED |
| the prior local only observation carried as a dynamic `OBSERVED` | ACCEPTED | REJECTED |
| the execution permission may say `NOT_RUN` instead of `BLOCKED` | ACCEPTED | REJECTED |
| an API outside the phase scope | ACCEPTED | REJECTED |
| the aggregate is derived from the ledger | NOT DERIVED | DERIVED |
| `release_eligible` turned on | REJECTED | REJECTED |
| the prior evidence pointing outside the repository | REJECTED | REJECTED |
| the static profile declares that it is not a permission | NO MARKER | MARKED |

The reference JSR gate refusal is verified by the host suite as well:

```
$ python3 tools/reference_jsr_gate.py --check
BLOCKED: reference JSR execution gate closed for 81/81 slots; jsr_count=0
$ echo $?
3
```

The gate reads `spec/reference_jsr_gate.json` and `spec/api_contracts.json`
only. It does not read `spec/abi1_reference_review.json`, and a host test
asserts that the gate source mentions neither the Phase ABI-1 ledger nor its
`static_verdict` and `execution_permission` fields, so the new safety field
cannot become a gate input.

## 13. Sources

| source | use | licence / conditions |
| --- | --- | --- |
| `rom/syscard3.pce` (private, local only) | the static audit input | never committed, never uploaded, never printed |
| Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3` | the pinned core, its memory map, its `TAM`/`BSR`/`SAX` semantics | GPL-3.0, not bundled |
| cc65 `71746c829e77f74c2b601a7d16821170c2610df3` | the pinned `da65` HuC6280 decoder | zlib, already a pinned dependency |
| Hudson HuC6280 software manual | the zero page at `$2000`, the stack at `$2100`, `SAX`/`SAY`/`SXY` as register swaps, `BSR` as an 8 bit relative branch | public documentation |
| `huc:include/hucc/pcengine.inc` (`54f5c736…`) | the public System Card function equates used as the entry address hypothesis | public caller source, `PENDING` provenance |
| `huc:include/hucc/core.inc` (`54f5c736…`) | the public caller convention: the API slot is passed in `Y`, and the System Card zero page variables live at `$2000` | public caller source, `PENDING` provenance |
