# Issue #716 (P01 of #473): A+D+T production wiring position and halt semantics — design/audit only

Status: **design/audit only**. No production wiring. No method finalisation. No production guard is enabled.
#173 / #367 / #459 remain BLOCKED. `release_eligible=false`, ABI VERIFIED 0/81, DEV_CALLABLE 0,
referenced JSR permission 0/81 are unchanged. The project is not a compatible System Card BIOS.

- Issue: #716 (parent #473, plan item P01). Basis: PR #714 / `docs/473-selection-supplement-s7s8.md` §7–8 (plan 1).
- This document does **not** re-implement the verification of #647 (D), #648 (T) or #654 (buffer).
  It records **where** A, D, T would have to be connected in a future production core/harness, in what
  order relative to instruction fetch / IRQ / TransferStart/End, and **what a halt means** at each point.
- **Drafting this document is not production-implementation approval.** Until explicit owner approval,
  work stays limited to design, measurement and Draft PRs. A+D+T is a verification-planning candidate;
  the production method is UNDECIDED.

## 1. Sources and evidence classes

| Claim | Class |
|---|---|
| TransferStart sets `m_transfer_state = 2` then pushes Y/A/X; TransferEnd pops X/A/Y then PC+6; push = write-then-S--, pop = S++-then-read; `RunInstruction` order fetch → `CheckIRQs()` → opcode → `HandleIRQ()` when state is 0 | Pinned-source reading (`b49ae82`), restated from `docs/473-selection-supplement-s7s8.md` §2 (no new source copy in this document) |
| A alone UNSAFE on IRQ-in-TAM; A+D / A+E SAFE_IN_SCOPE (TAM-before vs TAM-after stop); T pre-start refusals can be SAFE_IN_SCOPE, mid-transfer refusals BLOCKED | Verification-only measurement on pinned Geargrafx `b49ae82` with synthetic BIOS/CD and verification-only patch (#647/#648, recorded in `docs/647-*` / `docs/648-*`; results there are expectations until their CI `verify` passes — this document adds no new measured values) |
| TransferEnd state-reset order (pop-before-reset vs pop-after-reset) | **Known discrepancy, unresolved** (`docs/473-selection-supplement-s7s8.md` §2: PR #701 §2 vs #499 driver comment). This document does not resolve it; §5 lists it as a precondition |
| True free-run, step/RunToVBlank, libretro (#103) enforceability | Audit below (§4); unmeasured positions are marked UNKNOWN/BLOCKED, never PASS |
| GPL/MIT boundary | Audit of `docs/173-safety-monitor-limits.md` (§6); no GPL source is copied here |

Synthetic fixtures only. No private `./rom/` input is used. No commercial ROM/BIOS bytes are published.
No reference JSR is executed. Geargrafx observations are not proof of hardware behaviour (real hardware: #655 UNKNOWN).

## 2. Candidate wiring positions (design, not wired)

"Refuse" / "stop" below means: the host stops calling `RunInstruction` and the guest is never resumed
(kill semantics, §4). It never means suppressing a pop, rolling back bytes, or resuming the guest.
No position below exists in `src/`; all hooks named are the existing verification-only
probe/patch positions in `tests/integration/`, cited as **precedent locations only**.

### A — MPR1=$F8 allowlist at the instruction-start boundary

- Candidate position: **before the opcode fetch of each instruction** (the existing verification-only
  `gg_verify_pre_instruction` probe-side hook used by #645; A1 PARTIAL in S7/S8 §3).
- Check: MPR1 must equal `$F8` (WRAM, #497). Anything else is fail-closed refusal. Reset-exit MPR1 is
  UNDETERMINED: instruction 0 must be preceded by a recorded `DIRECT_SETMPR $F8`, otherwise BLOCKED.
- Covers: JSR/PHA/5-transfer entry checks measured SAFE_IN_SCOPE (#645).
- Does **not** cover: the pending IRQ inside the TAM's own `RunInstruction` (TAM maps MPR1 to $FF,
  then `HandleIRQ()` pushes to `$21xx` in the same call — #498 `n_irq_tam` UNSAFE). This hole is why D exists.
- Does **not** cover: MPR0 code-fetch hole, `m_mpr_buffer` aliasing, ST0/ST1/ST2 `WriteFromCPU`
  bypass (#173 BLOCKED), device-originated DMA (#367 W17), or src/dst bytes outside WRAM (T's scope).

### D — decode-time TAM refusal (before TAM executes)

- Candidate position: **after decoding the pending instruction as TAM, before executing it**
  (probe-side; needs a side-effect-free opcode/operand peek — peekability is core-specific, #647).
- Check: refuse a TAM whose operand has bit 1 set while A != `$F8`; refuse any opcode that cannot be
  peeked (fail closed). TMA reads are not refused by D (TMA #$02 then TAM #$02 is a positive case, #647).
- Stop state (inherited, not re-measured): PC/MPR/`m_mpr_buffer` unchanged at the stop — the TAM never
  ran, so the buffer stays at its start value (#654: `$02`→`$02`). This cleanliness is the recorded
  reason D is preferred over E for further verification; it is **not** a production adoption decision.
- Precedent pattern: #712 ST entry-PC 0-execution refusal with no-entry-check mutation RED
  ("refuse at the instruction boundary with zero execution") — cited as a design precedent only,
  not as transfer-safety evidence.

### T — pre-start transfer refusal (before TransferStart executes)

- Candidate position: **at the instruction start of TII/TIN/TDD/TIA/TAI with transfer state 0,
  before the opcode fetch** (probe method `AT` = A+T, #648).
- Check: peek the six operands without a bus read; compute every source and destination address of the
  whole transfer (TDD decrements both, TIN fixes destination, TIA alternates destination, TAI alternates
  source, length 0 = 65536); refuse the whole instruction before the fetch if any byte lies in a page
  not mapped to WRAM `$F8`, or any destination byte is one of the instruction's own seven bytes
  (each byte step re-runs the opcode — self-overwrite); unpeekable opcodes refused (fail closed).
  The TransferStart stack precondition (MPR1 = `$F8`) remains A's check.
- Stop state (inherited): nothing has changed yet at a pre-start refusal, so it can be SAFE_IN_SCOPE.
- **Mid-transfer refusal is not a safe path**: a stop inside the transfer (state 2 or 1) is BLOCKED
  (bytes already written are counted, not undone; guest must not resume — `mut-tresume` shows the rest
  of the transfer reaching the hardware page). The mid-transfer opcode/MPR-unchanged check exists only
  as a conservative refusal-with-no-resume, never as a resume path.

### C — kill-only final backstop (no pop suppression, no rollback, no resume)

- Candidate position (verification-patch precedent only): **immediately before each stack access
  side effect** in the four aggregated functions `StackPush8/16` / `StackPop8/16` (#499: 19 routes
  stopped; state BLOCKED). This document wires nothing there.
- Semantics: **kill only**. After a C stop the guest is never resumed. Pop suppression (B′), rollback
  of partial writes, and state-restore-and-resume are explicitly **not adopted** (S0 item 3; S7/S8 §7).
- State after a C stop is BLOCKED (PC/S/P/MPR/VDC/WRAM deltas vs the refused point are recorded for
  audit, never treated as a resumable snapshot). C says nothing about ST/DMA/device paths.

### E — retained alternative, not the candidate

A+E (IRQ deferral at TAM) is retained as the documented alternative (#647: stop at `$1E02` after the TAM,
MPR1=`$FF` left visible, +1 instruction latency, starvation on non-peekable opcodes unmeasured).
This document does not wire E and does not decide D-vs-E for production.

## 3. Order relative to fetch / IRQ / TransferStart/End

Pinned-core order (restated from S7/S8 §2; line references there):

```
RunInstruction:
  1. fetch opcode                      (PC advances here)
  2. CheckIRQs()                       (pending fixed from pre-instruction I/IRR)
  3. execute opcode:
       TAM          -> SetMprTAM (MPR + buffer change)
       TransferStart-> m_transfer_state = 2, then StackPush8 x3 (Y/A/X)
       TransferEnd  -> StackPop8 x3 (X/A/Y), PC+6, state reset (order disputed, §5)
       byte steps   -> per-byte reads/writes (state 2/1; IRQ not taken while state != 0, #653)
  4. HandleIRQ() if pending and state == 0 -> StackPush16/StackPush8 to STACK_ADDR|S (MPR1-resolved)
```

Candidate checks in this order (all pre-side-effect; any refusal halts with no resume):

1. **A** (instruction-start boundary, before step 1): MPR1 must already be `$F8`.
2. **D** (decode-time, between steps 1 and 3 for TAM): TAM with forbidden mapping refused before step 3.
3. **T** (pre-start, between steps 1 and 3 for transfers at state 0): whole-transfer range refused before step 3.
4. **C** (kill-only, immediately before any stack write/read in step 3 or 4): last backstop, state BLOCKED.

What this order achieves (design intent, verification pending): A handles the steady state; D closes the
same-`RunInstruction` IRQ-in-TAM hole that A cannot see; T closes the src/dst-range hole that MPR1 checks
cannot see (MPR1=`$F8` with an I/O destination is A's documented allowlist leak, S7/S8 §3 A7); C converts
any residual stack access into a non-resumable halt instead of a silent side effect.

## 4. Halt semantics and enforceability audit

Halt = the host stops calling `RunInstruction`; the guest is never resumed. The stopped-state deltas
(PC, A/X/Y, S, P, MPR0–7, VDC, WRAM, destination bytes, IRQ pending, `m_mpr_buffer`, transfer counters)
are recorded for audit. Raw stack bytes are not published (#709 observer precedent: MPR1-gated
`stack_check`, IRQ/JSR distinction).

| Execution mode | Can the halt be enforced? | Basis |
|---|---|---|
| Debugger step / `RunToVBlank` harness (single-instruction advance, pre/post-instruction stop) | Candidate enforcement point (verification precedent: #499 no-stepping post-instruction stop, #645 pre-instruction, #712 step counting, #366 post-accept boundary). Production wiring UNDECIDED | Measured on pinned debugger build only |
| True free-run (no per-instruction stop mechanism) | **UNKNOWN** — unmeasured; never PASS | S7/S8 §3 A8 / §5 C6; #653 premise |
| libretro shared core (#103) | **BLOCKED for enforcement** — no single-instruction stop API for this probe | `docs/366-irq-postaccept-boundary.md` boundary section |
| Real hardware (#655) | **UNKNOWN** | Geargrafx observations are not hardware proof |

Per-method stopped-state contract (inherited from #647/#648; no new values measured here;
a uniform stopped state is NOT required across methods):

| Method | Reference point | Expected stopped state | Verdict |
|---|---|---|---|
| A+D (TAM refused) | TAM execution start | PC/MPR/buffer/A/X/Y/S/P at run start; IRQ still pending, not entered; transfer state 0; nothing ran after | SAFE_IN_SCOPE (verification scope) |
| A+E (deferred) | Next instruction start | TAM-applied MPR1=`$FF` visible; +1 instruction latency tolerated; IRQ still pending | SAFE_IN_SCOPE (verification scope), retained alternative |
| T pre-start | Transfer instruction start | Nothing changed; PC/MPR at refusal start | Can be SAFE_IN_SCOPE |
| T mid-transfer | Inside transfer | Partial execution done; written bytes counted, not undone | BLOCKED, no resume |
| C (any) | Immediately before side effect | Side effect stopped; residual PC/S/P/MPR/VDC/WRAM state may differ from pre-refusal | BLOCKED, kill only |

## 5. Responsibility boundaries and open preconditions

| Boundary | Owner |
|---|---|
| Refusal checks (A/D/T) and kill backstop (C) as verification-only probe/patch logic | `tests/integration/` verification patches and probes (distribution-prohibited, §6) |
| Future production core/harness wiring, if ever approved | UNDECIDED — no position in `src/`, no accessor added, no guard enabled by this document |
| Production adoption, gate release (`#173`/`#367`/`#459`, `release_eligible`, ABI/JSR/DEV_CALLABLE) | Owner decision only; this document recommends nothing for production |

Open preconditions (each BLOCKED/UNKNOWN until its own Issue/PR resolves it; none resolved here):

1. TransferEnd state-reset order discrepancy (S7/S8 §2) — must be settled against pinned source before any production design can fix pop-side expectations.
2. S0 item 4 (C stop-point definition: pop S++-before-read ordering) — still undefined.
3. Side-effect-free peek availability on any future production core (core-specific; unpeekable = fail-closed over-refusal).
4. True free-run enforcement — UNKNOWN; #712 entry-boundary 0-execution is precedent, not transfer evidence.
5. libretro (#103) single-step API — absent (BLOCKED for enforcement).
6. ST0/ST1/ST2 `WriteFromCPU` bypass — BLOCKED (#173); device DMA — unmeasured (#367 W17).
7. IRQ newly asserted *inside* TAM execution — NOT_MEASURED on pinned core (no clock point in TAM, #653).
8. E starvation on non-peekable opcodes — unmeasured (#647).
9. MPR0 code-fetch hole and MPR1-outside-S1-scope banks — outside A/C coverage (S7/S8 §6 table).
10. Real-hardware equivalence — UNKNOWN (#655).

## 6. GPL/MIT boundary audit (`docs/173-safety-monitor-limits.md`)

- Pinned Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3` is GPL-3.0; this repository is MIT.
  No GPL source is copied into this repository by this document (no code excerpts added; line citations
  above are restatements of the existing design docs, not new source reproduction).
- Existing verification hooks live only in `tests/integration/` patches/probes and build only into local,
  undistributed probe binaries (per #173: link/run locally, never commit artifacts, never distribute,
  never publish as CI artifacts). This document adds no patch and changes no build rule.
- GPL-licence conformance of the link/run flow is unconfirmed and is not decided here; continued local
  verification use is not stated as conformance.
- Consequence for any future production wiring: it must be original code that passes the provenance gate
  (`docs/provenance.md`: pinned upstream revision, per-file licence, APPROVED human disposition before
  merge). Verification-only patch code must never flow into a distributable product or production BIOS.
  This precondition is recorded here and enforced at review time, not implemented here.

## 7. Minimal-change, maintenance and withdrawal notes

- Minimal configuration this design assumes (verification scope): pinned debugger-build core `b49ae82`,
  synthetic HuC6280 fixtures (MPR1=`$FF` negatives, MPR1=`$F8` WRAM positives, IRQ-in-TAM, src/dst
  crossing, self-overwrite, buffer TMA cases), per-instruction advance with pre-instruction / decode-time /
  pre-start / kill hooks in the verification probe. Changing any element (core revision, harness without
  per-instruction stop, libretro target, real hardware) re-opens §4–§5.
- Maintenance: the wiring positions in §2–§3 are design records. If the pinned source references drift
  (TransferStart/End, `StackPush/Pop`, `RunInstruction`, `SetMprTAM`/buffer), the affected section must be
  re-audited in its own Draft PR; this document must not silently track source changes.
- Withdrawal: the A+D+T candidacy recorded here is withdrawn (in a follow-up doc PR, gates staying BLOCKED)
  if any of: side-effect-free peek proves unimplementable without bus side effects; free-run enforcement
  proves impossible where required; ST/DMA paths invalidate the CPU-path-only scope; or the TransferEnd
  order discrepancy resolves against the §3 ordering. Withdrawal needs no production change because none
  was made.

## 8. Verification and review (doc-only)

- This document performs no measurement: every behavioural expectation is inherited from the cited
  `docs/473-*` / `docs/647-*` / `docs/648-*` / `docs/653-*` / `docs/654-*` / `docs/367-*` / `docs/497-*` /
  `docs/498-*` / `docs/366-*` records. New safety claims require new measured evidence; none is made here.
- Host/docs check: `python3 -m unittest tests.host.test_public_docs_current -v` (public-count staleness
  gate; this document adds no counts) plus `make check`-adjacent doc lint only. Real-core gates are
  NOT_RUN for this doc PR (nothing to execute).
- Independent review is requested on the Draft PR with focus on: (a) no `src/`/build-rule/spec-gate change
  in the diff, (b) no method-adoption or gate-release wording, (c) UNKNOWN/BLOCKED labels where §4–§5
  require them, (d) GPL boundary (§6) intact, (e) no commercial/`./rom/`/reference-JSR content.
- 1 Issue = 1 Draft PR. CI result and the reason for each NOT_RUN item are recorded on the PR.

## 9. Non-goals (restated from #716)

Production adoption of any method, API JSR解禁, reference ROM/BIOS execution, distribution of verification
patches, and closing #473. None is done or proposed here.

## 10. Owner decision (options only — no recommendation for production)

1. Accept A+D+T wiring/halting positions in §2–§4 as the verification-planning reference and approve only
   the child-Issue split (P02 peek/accessor, P03 fixtures, P06 halt-state/no-resume contract, P12 integration
   preconditions) as Draft-PR-tracked work. Gates stay BLOCKED.
2. Keep #473 design-only without this reference (rely on S7/S8 + PR #701 alone) and wait for #367 evidence.
3. Defer (leave this document as a retained candidate) and prioritise E-side starvation measurement first.

MERGE_RECOMMENDED: **yes** (this document + docs-check only; doc-only).
CLOSE_RECOMMENDED: **no** (#716 stays open for its acceptance checklist; #473 stays open; no gate change).
