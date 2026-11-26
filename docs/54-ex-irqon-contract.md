# $2C EX_IRQON contract research (Issue #54, G5 P0 task)

Status: **reference contract UNKNOWN; no independent functional implementation; no verification.**
**NOT a compatible System Card BIOS. NOT genuine ABI. NOT DEV_CALLABLE.
`release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81 (unchanged by this document).
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, or saves
appear here. `./rom/` was not accessed in any form for this document
(no existence check, listing, or read).

Branch basis: `b5b79d38` (origin/main at task start; PR #834 `$35` scaffold
integrated). This document changes no ledger, no source, no gate, and no ROM
bytes. The companion code change in the same task (APICALL capacity option in
`geargrafx_api_entry_probe.cpp` + `geargrafx_api_entry.py` + regression suite
`tests/integration/test_apicall_capacity.py`) is an observation-mechanism
improvement, not a `$2C` implementation: it adds no BIOS body, no wiring, and
no release-eligible behavior.

## 1. Purpose and scope (目的と調査対象)

G5 commercial-CD boot path (parent #635, integration #802) reached `$35` with
the preservation-only scaffold (ExpE, Issue #802 comment `7178913072`):

CUE[0] ExpE: `$02 -> $33 -> $4A -> $23 -> $25 -> $24 -> $36 -> $35 -> $2C (unimplemented halt)`
(X=`$2C`=44, entry `$E084`, 9 APICALLs, cold/warm identical, 80M budget).

CUE[1] ExpE: `$1E -> $4A -> $02 -> $02 -> $21 -> $1C` (unchanged, `$1C` BLOCKED_ABI).

`$2C` (EX_IRQON candidate) is therefore the next genuine boot blocker on the
observed commercial path. This document investigates the `$2C` contract so that
an implementation (functional D1 or limited experimental D2) can be scoped with
evidence. It does NOT complete the specification by guessing. In particular,
the name "IRQON" MUST NOT be taken to authorize a CLI, an interrupt-mask
write, or a VDC register write in any implementation.

Related: #54 (`$2C` EX_IRQON), #55 (`$2D` EX_IRQOFF, the disable-side pair),
#802 (G5 path), #635 (blocker measurement), #63 (`$35`), #64 (`$36`),
#46 (`$24`), #47 (`$25`), #84 (`$4A`), #38 (`$1C` BLOCKED). Tracker #3,
prerequisites #1, #2, #6, #8.

Required outcome: purpose/scope, references/provenance, ABI contract table,
IRQ-contract analysis (CPU I / controller mask / VDC / BIOS state kept
separate), E1/E2/E3 selection with rationale, display-init debt evaluation,
open issues, acceptance criteria.

Safety: no main direct commit, independent worktree only, no interference
with other sessions, no force-push/amend/destructive reset, no `./rom/`
access for this document, no commercial-media disclosure, 0 reference-BIOS
JSR, no VERIFIED promotion, `release_eligible=false` kept.

## 2. References and provenance (参照資料とprovenance)

All external items are REFERENCE_ONLY / PENDING unless stated otherwise.
No file or fragment from them enters the firmware. No source text is
reproduced; only fact citations (URL, version, date, what was claimed).

| ID | Source (pinned where applicable) | What it establishes about $2C | Disposition |
|----|----------------------------------|-------------------------------|-------------|
| S1 | Zeograd System Card notes, `https://www.zeograd.com/download/pce_bios.html` (System Card 3.0 scope; reverse-engineering noted; re-fetched 2026-11-27 for this task, 19537 bytes; one IRQON mention located, VDC/IRQ/register hints present) | `EX_IRQON`, Function `#$2C` with a short VDC/IRQ-related one-liner. No input/output register contract, no flags, no timing, no error contract in the cited entry | REFERENCE_ONLY, PENDING. Name + slot + target hint only; zero call contract. N/A-style absence recorded as absence, never as no-side-effect proof |
| S2 | Stum generation table, `https://www.stum.de/2025/pcenginebiosoffsets/` (2025-07-28; CC BY-NC-SA 4.0 display, fact citation only; local `/tmp/stum-63.html` 24169-byte copy from the `$35` task re-checked: single `irqon` row locator + single `E084` occurrence) | Row `ex_irqon`: JSR `$E084`; per-generation bodies differ (same family pattern as `$35`: JSR stable, bodies move). Sibling row `ex_irqoff` adjacent | REFERENCE_ONLY, PENDING. Mapping + grouping only. Single-behavior assumption FORBIDDEN |
| S3 | pce-devel/huc `include/hucc/pcengine.inc` @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` (BSL-1.0; pin re-verified 2026-11-27: 18844 bytes, SHA-256 `b6a25d33…` matches; single `ex_irqon` equate, line 515) + `hucc-systemcard.asm` @ same pin (no C-callable wrapper for the IRQ family observed) | Caller-side equate only. Absence of wrapper recorded as absence | REFERENCE_ONLY, PENDING. Name-to-entry only. Nothing copied |
| S4 | mckayemu/hugo `bios.c` @ `64ed226da228873ee4f069e8973a4` (GPL-2.0) | Re-verification INCONCLUSIVE in this task (raw fetch path unresolved; no new claim made). Prior `$35`-task pattern (selector define + debug name, no `handle_bios()` body) is NOT transferred to `$2C` without its own check | NOT re-verified here; REJECTED for ROM inclusion regardless. Nothing copied |
| S5a | Pinned Geargrafx `b49ae82a` (`tools/dependencies.json`; GPL-3.0, test-only) — `src/huc6280.h` (CPU state: `IDR`/`IRR` pointers, `AssertIRQ1/2`) + VDC docs `skills/.../huc6270_vdc.md` (R05 CR bits 0–3 = IE interrupt enables; SR reports interrupt conditions and reading SR clears status bits except BSY; R05/R06 register map; DCR DSC/DVC bits) | Hardware map AS IMPLEMENTED by the pinned core. Gives locations/encodings for CPU-side mask/pending (IDR/IRR) and VDC-side enables/status (CR IE, SR) | REFERENCE_ONLY, PENDING for license. Hardware facts only — NOT `$2C` BIOS facts. No code copied. Hardware capability MUST NOT be mistaken for the BIOS calling convention |
| S5b | In-tree measured I/O practice (MIT): `src/overrides/slot_23.s` (VDC window), `src/ex_dspoff.s`, harnesses `geargrafx_dev_scrsiz.py`, `geargrafx_dev_cr.py` | VDC window discipline (MPR0 `$FF` + `SEI` + absolute + restore; AR left; max-push). NO in-tree IRQ-enabling BIOS body exists (every experimental body assumes IRQs masked/absent and performs no I/O control) | PROJECT_SPEC_ONLY. Discipline reusable IF I/O ever justified. No IRQ precedent exists in-tree to reuse |
| S5c | NEC HuC6280/HuC6270 manuals + PCE IRQ-management public notes (UNPINNED — no title/edition/section/page/URL in-tree; not re-fetched) | Claimed register/sequencing notes (I flag, IDR/IRR bits, VDC IE sources, VBlank/raster/timer paths). In-tree repetitions are NOT independent confirmations | HYPOTHESIS. Downgraded: MUST NOT ground implementation until pinned |
| S6 | In-tree DEV precedents: `$23` DEV, `$2E` CR-family, `ex_dspoff.s`, `$4A` preservation, `$25` B1, `$24`/`$36`/`$35` B1-analogous + harnesses | Project-own contracts only. Reusable as discipline (stack accounting, no-RMW shadow practice, MPR1/`SEI`/absolute window, RAM-diff scoping), NEVER as `$2C` proof. CR-family CLI/SEI-adjacent behavior belongs to CR/B2 only, never assumed for `$2C` | PROJECT_SPEC_ONLY. `$35` B1-analogous skeleton reusable as NO-I/O template ONLY; its SEMANTICS (or absence) never grounds `$2C` |
| S7 | Sanitized G5 observations (Owner-permitted, structural only; Phase C of this task re-observed with raised APICALL capacity 32) | CUE[0] ExpE `$02->$33->$4A->$23->$25->$24->$36->$35->$2C` halt (9 APICALLs, X=`$2C`=44, entry `$E084`, all table JSR delta 2, in_table, MPR1 `$F8`, SP balanced, stack MATCH, cold/warm identical, 80M budget, no truncation, dropped 0). `$2C` entry call-state OBSERVED once (A=7/X=0/Y=0/P=`$06`/I=1, IDR=0, IRR=0; see §3). CUE[1] ExpE unchanged `$1E->$4A->$02->$02->$21->$1C` halt | OBSERVED (call-state only, single game, single callsite). No memory/sector/content data. Genuine `$2C` (and everything upstream genuine) UNKNOWN/BLOCKED |
| S8 | Ledgers: `spec/api_slots.json` id 44 = `EX_IRQON`, `spec/api_contracts.json` slot 44 (`EX_IRQON`, `0xE084`, HYPOTHESIS, all UNKNOWN), `spec/dev_implementations.json` id 44 (`NOT_STARTED`), `tools/build_rom.py` 81-slot JSR table (`$E000+3*slot`, stub `LDX #slot; JMP api_unimplemented`) | Current state: hypothesis, zero behavior, no DEV override, table mechanics project-verified | Project record, unchanged. `$E084 = $E000+3*$2C` is layout fact, not ABI proof |

Gate: S1-S3 REFERENCE_ONLY/PENDING; S4 not re-verified here; S5a
test-dependency/REFERENCE_ONLY; S5b PROJECT_SPEC_ONLY; S5c HYPOTHESIS; no
file/fragment enters firmware. (`src/overrides/slot_44.s` is MA_SQRT for
*hex* slot `$44` = decimal 68 and is unrelated to decimal-44 `$2C`.)

## 3. ABI contract table

| # | Item | Genuine `$2C` | DEV `$2C` | Disposition |
|---|------|---------------|-----------|-------------|
| 1 | Name/slot/entry | `EX_IRQON` / `$2C` / `$E084` candidate | none (stub halts) | S1/S2/S3 name+entry HYPOTHESIS as callee; S8 HYPOTHESIS. SOURCE_CONFIRMED only as caller equate (S3 line 515). `$E084` layout project-verified, not ABI proof |
| 2 | Generations | Bodies differ per generation (S2 pattern); JSR stable | N/A | Single-behavior assumption FORBIDDEN |
| 3 | Pair `$2D` EX_IRQOFF | Relation UNKNOWN (adjacent names do NOT prove symmetric contracts, shared flags, or call order) | none | S2 adjacency is grouping, never behavior. #55 stays independent |
| 4-6 | Inputs A/X/Y / work-RAM | UNKNOWN (S1 gives no register contract) | none | S7 single-callsite entry values (A=7/X=0/Y=0, see §4) are OBSERVED call-state, never an input contract |
| 7 | CPU I flag | UNKNOWN (genuine effect on I unproven: CLI vs preserve vs conditional all ungrounded) | n/a (no body) | S7 OBSERVED I=1 at entry on one path is not a precondition and not a postcondition. §5 keeps I / IDR / VDC-IE strictly separate |
| 8 | IRQ controller (IDR/IRR) | UNKNOWN (which sources, mask vs clear, pending handling all unproven) | n/a | S7 OBSERVED IDR=0/IRR=0 once is not a contract. Hardware locations per S5a |
| 9 | VDC enables/status (CR IE, SR) | UNKNOWN (which IE bits, value domain, SR-clear side effects, timing all unproven) | n/a | S5a gives register locations + SR read-clear fact; S1 hint alone never authorizes a store. Any future VDC write needs public BIOS-contract link `$2C`-to-register |
| 10 | MPR precondition | UNKNOWN (genuine); OBSERVED `$F8` on one path is not a contract | n/a | Table mechanics project-verified (S8) |
| 11 | Stack/return | UNKNOWN | (no scaffold selected; see §6) | JSR `$E084` tail discipline would apply to any future D2 only |
| 12 | Preserved/clobbered (A/X/Y/P) | UNKNOWN | n/a | B1 latch-exact practice (S6) reusable as technique only, never as `$2C` proof |
| 13 | Flags (Carry/Z/N/V) | UNKNOWN | n/a | CR/B2 conventions never assumed for `$2C` |
| 14 | IRQ timing/pending/handler/VBlank | UNKNOWN (sync vs async, wait vs enable-and-return, handler setup all UNPROVEN) | n/a | Natural time-passage IRQ events vs implementation side effects must be distinguished in any future test (§G of the task) |
| 15 | Errors/rejects | UNKNOWN | n/a | No public error codes |
| 16 | Call order (`…->$35->$2C`) | UNKNOWN genuine; OBSERVED order on one commercial path only | N/A | One-game order under experimental (SIMULATED) upstream returns. Genuine ordering UNKNOWN |

Verdict: genuine `$2C` UNKNOWN in every behavioral field. The observed
entry values (§4) describe one callsite, not a contract.

## 4. Observed `$2C` call-state (Phase C, OBSERVED — not a contract)

CUE[0] ExpE with APICALL capacity 32 (both cycles identical, no truncation,
dropped 0). All 9 calls: table JSR (delta 2), in_table, MPR1 `$F8`, SP
balanced, stack MATCH, `return_matches_caller` true at entry-observation
time (stacked-return bytes read before the callee runs — same documented
limitation as the `$35` suite, not an RTS landing proof):

- `$02` `$E006`, `$33` `$E099`, `$4A` `$E0DE`, `$23` `$E069`, `$25` `$E06F`, `$24` `$E06C`, `$36` `$E0A2`, `$35` `$E09F`, then
- **`$2C` `$E084`**: caller A=7, X=0, Y=0, P=`$06` (I flag SET, Z set), MPRs unchanged (`$F8…`), IDR=0, IRR=0, halt after: `api_unimplemented` X=`$2C`, halt confirmed, cold/warm identical.

Discipline notes: entry P/I/IDR/IRR are guest-observed facts of one call.
They MUST NOT be promoted to preconditions ("game always calls with I=1"),
postconditions, or an implementation checklist. VDC registers are NOT
observable through the entry probe (mechanism limitation, recorded — not
worked around by guessing).

## 5. IRQ-contract analysis (kept separate, none grounded for `$2C`)

- HuC6280 CPU I flag (P bit 2; SEI/CLI/BRK family): hardware fact (S5c HYPOTHESIS until pinned). Whether genuine `$2C` executes CLI, preserves I, or conditions on I is UNKNOWN. The name alone MUST NOT authorize CLI.
- IRQ controller mask (IDR) / pending (IRR): hardware locations per S5a. Observed 0/0 once (§4) is not a mask contract. Which sources `$2C` touches (IRQ2/IRQ1/TIRQ) is UNKNOWN.
- VDC interrupt enables (CR/R05 IE bits 0–3): locations per S5a. Which bits, what values, and with what handshake is UNKNOWN. S1 hint alone is insufficient (no value/timing/flag/shadow).
- VDC status (SR) including its read-to-clear side effect (except BSY): hardware fact per S5a. Any future implementation must prove it does not unintentionally clear status; any future test must not perform gratuitous SR reads.
- VBlank / raster / timer / sprite-collision sources: UNLINKED to `$2C` (names of sources are hardware facts; the BIOS-to-source link is UNKNOWN).
- BIOS-side IRQ management (work-RAM flags, vector/handler installation, enable-vs-occurrence timing): UNKNOWN in every field; no in-tree precedent (S5b: no IRQ-enabling body exists).
- Stack/MPR/return-boundary effects of enabling interrupts mid-path: UNKNOWN; any future D1 must test handler-transition/return, pending-at-entry, and boundary abnormal cases (task §G).

## 6. E1/E2/E3 selection

- E1 functional: **BLOCKED**. S1 one-liner + S5a hardware locations do NOT constitute a BIOS calling convention for any of: I-flag action, IDR mask, VDC IE target/range/value, SR discipline, handler setup, timing, errors. No D1 body, no VDC write, no CLI/SEI, no mask change in this task.
- E2 preservation-only scaffold: **NOT selected in this task** (deferred, not rejected in principle). Reasons:
  1. Semantic inversion is load-bearing here, unlike the display no-ops: a preservation scaffold leaves I=1/VDC-IE/IDR untouched, i.e. it *withholds the service the name promises*. If the game VBlank-waits after `$2C`, the result is a budget-exhaustion hang, not a clean NEXT halt — attribution is weaker than the `$35` case (where the game demonstrably proceeded).
  2. Display-init debt (`$25`/`$24`/`$36`/`$35` no-ops) compounds the confound: VDC may be unconfigured, so IRQ behavior is doubly ungrounded; a `$2C` no-op cannot disentangle "game waits for IRQ" from "display never initialized".
  3. A responsible E2 needs its own design doc (preservation-vs-enable contract, hang-vs-NEXT interpretation rules), 64-variant wiring, dedicated suites, chain updates, ExpF commercial run, and review — a full PR-sized follow-up, not a same-PR addition. Mechanical B1 application without that work is prohibited by the task and is not done here.
- E3: **selected**. No implementation; contract research + observation-mechanism improvement + re-observation evidence are the completed deliverables. BLOCKED is reported as a grounded conclusion, not a failure.

## 7. Display-init debt evaluation (task §I)

- Public-spec confirmed dependencies: NONE beyond slot/entry names (S1–S3 give no cross-API dependence).
- Synthetic-proven dependencies: NONE (no-op bodies have no side effects by construction; `$35` return through the synthetic chain is mechanics, not display progress).
- Commercially observed stops: `$4A`→`$25`→`$24`→`$36`→`$35`→`$2C` sequence on CUE[0] (each step advanced by the corresponding scaffold's return). CUE[1] stops at `$1C` regardless.
- Guesses (explicitly NOT claims): that upstream no-ops leave VDC unconfigured; that the game may VBlank-wait after `$2C`; that `$2D` mirrors `$2C`. All UNSUPPORTED — recorded as open, never used.
- Open: whether genuine display-init (`$25`/`$24`/`$36`/`$35` real services) is prerequisite to meaningful `$2C` behavior. Priority proposal: (1) `$2C`/`$2D` pair contract research (public sources + second-game cross-check); (2) VDC-state observation capability for commercial runs (mechanism work, no ABI guessing); (3) only then revisit E2 with hang-interpretation rules. No API is re-implemented by this document.

## 8. Open issues

- U1. Genuine `$2C` inputs/outputs/flags/I-flag action: UNKNOWN.
- U2. IDR mask vs IRR pending handling: UNKNOWN.
- U3. VDC CR IE target bits/values/handshake: UNKNOWN.
- U4. SR read-clear discipline for `$2C`: UNKNOWN.
- U5. Handler/vector/BIOS-flag setup: UNKNOWN.
- U6. Timing (sync/async, wait vs fire-and-continue, VBlank alignment): UNKNOWN.
- U7. Relation to `$2D` EX_IRQOFF and `$2A`/`$2B` RCR pair: UNKNOWN (names adjacent, contracts unlinked).
- U8. NEXT after `$2C`: UNKNOWN (stub halt; no execution past `$2C` observed).
- U9. Second-core cross-check: NOT_RUN (Geargrafx-only).
- U10. VDC-state commercial observability: NO MECHANISM (entry probe limitation, §4).

## 9. Acceptance criteria

- (a) This contract doc merged (or same-PR doc) with S1–S8, §3 UNKNOWN table, §4 OBSERVED-only call-state with non-promotion rule, §5 separation analysis, §6 E3 selection, §7 debt evaluation, §8 U1–U10.
- (b) APICALL capacity option: bounded 1..64, default 8 unchanged; `apicall_dropped` additive count with truncated⟺dropped>0 invariant; invalid inputs fail-closed (C++ exit 2, Python ValueError); no guest writes; commercial output stays RAM-clean; existing host source-property tests GREEN.
- (c) Regression suite `tests/integration/test_apicall_capacity.py` ALL GREEN on pinned core (under/exact/over × default/raised caps, boundary 64, invalid refusals incl. direct-C++ invocation, commercial-clean, cold/warm identical order every run), RED→GREEN recorded, M1 (off-by-one) + M2 (dropped-count) mutants RED.
- (d) Phase C re-observation on the same HEAD: ExpD repro (8 calls, X=`$35`, no trunc) + ExpE cap-32 (9 calls through `$2C`, X=`$2C` halt confirmed, trunc False, dropped 0, cold/warm identical) + CUE[1] ExpE `$1C` regression; media invariant pre/post identical; BIOS image hashes recorded privately only.
- (e) `make test`, `make check` EXIT 0, `git diff --check`, default-path behavior unchanged (6-arg invocation preserved), Required CI GREEN, independent review with zero unresolved CRITICAL/MAJOR/MEDIUM, no merge without Owner approval.
- (f) Safety gates unchanged (JSR 0/81, VERIFIED 0/81, `release_eligible=false`, `$2C`/`$1C`/`$35` BLOCKED_ABI, DEV invariance, SIMULATED vs genuine vs synthetic vs real-core vs commercial strictly distinguished). G5_OBSERVED stays false.

NOT a compatible System Card BIOS. Geargrafx observations are not hardware proof.
