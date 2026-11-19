# $25 EX_SCRMOD contract research (Issue #47, G5 P0 task)

Status: **reference contract UNKNOWN; no independent implementation; no verification.**
**NOT a compatible System Card BIOS. NOT genuine ABI. NOT DEV_CALLABLE.
`release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81 (unchanged by this document).
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, or saves
appear here. `./rom/` was not accessed in any form for this document
(no existence check, listing, or read).

Branch basis: `95148a37` (merge of PR #824, origin/main at document start).
This document changes no ledger, no source, no gate, and no ROM bytes.

## 1. Purpose and scope (目的と調査対象)

G5 commercial-CD boot path (parent #635, integration #802, blocker
measurement #635, experimental $4A wiring #824) reached the sanitized
observation:

`$02 -> $33 -> $4A -> $23 -> $25 (unimplemented halt)`

after the $4A experimental preservation body started returning.
The next blocker is slot `$25` (EX_SCRMOD candidate). Genuine `$4A` semantics
stay UNKNOWN/BLOCKED (docs/84-4a-contract.md); the `$23 -> $25` advance was
first seen under a SIMULATED $4A diagnostic (#803) and then under the real
$4A experimental return (#824). In both cases the `$25` halt itself is the
observed fact; everything downstream of genuine `$4A` remains unproven.

This document investigates the `$25` (EX_SCRMOD candidate) contract so that
an independent implementation can be scoped with evidence, and so that
what must NOT be implemented can be stated with grounds. It does NOT
complete the specification by guessing. Completion is explicitly out of
scope: the goal is to make "what can be implemented and why the rest cannot"
auditable.

Related: #47 (EX_SCRMOD), #802 (G5 integrated boot path), #635 (commercial
blocker measurement), #824 ($4A EX_MEMOPEN experimental implementation),
#45 ($23 EX_SCRSIZ), #46 ($24 EX_DOTMOD). Tracker #3 (inventory),
prerequisites #1 (ROM map), #2, #6 (Geargrafx harness), #8 (provenance).

Required outcome (task): purpose/scope, references/provenance, ABI contract
table, VDC side effects and preservation conditions, comparison with `$23`,
synthetic positive/negative/boundary plan (self-made data only), competing
implementation policies, open issues, and acceptance criteria for the next
implementation task.

Safety: no main direct commit, independent worktree only, no interference
with other sessions, no force-push/amend/destructive reset, no `./rom/`
access, no commercial-media disclosure, 0 reference-BIOS JSR, no promotion
of genuine ABI verification status, `release_eligible=false` kept,
no implementation/merge/Issue-close in this document.

## 2. References and provenance (参照資料とprovenance)

All external items are REFERENCE_ONLY / PENDING unless stated otherwise.
No file or fragment from them enters the firmware. No source text is
reproduced; only fact citations (URL, version, date, what was claimed).
Third-party code is never translated line-by-line to bypass licensing
(docs/provenance.md).

| ID | Source (pinned where applicable) | What it establishes about $25 | Disposition |
|----|----------------------------------|-------------------------------|-------------|
| S1 | Zeograd System Card notes, `https://www.zeograd.com/download/pce_bios.html` (System Card 3.0 scope; page states part of the content is reverse-engineering; re-fetched 2026-11-19 for this survey, fact citation only) | Headings `EX_SCRSIZ, Function #$23`, `EX_DOTMOD, Function #$24`, `EX_SCRMOD, Function #$25` with NO `IN`/`OUT` block (bare headings, unlike `$26` EX_IMODE which documents `reg A` values `$00-$03`). Register table on the same page: `$20F3` = current VDC register selected, `$20F7` = value of VDC reg #5 (Control Register), `$20F8-$20FF` = `al/ah/bl/bh/cl/ch/dl/dh` (BIOS work-RAM slots, NOT eight HuC6280 registers), plus `$2214/$2215`, `$2227-$2232`, `$2243`. Display-family one-liners exist only for `$2A-$33` (RCRON/RCROFF/IRQON/IRQOFF/BGON/BGOFF/SPRON/SPROFF/DSPON/DSPOFF), none for `$23/$24/$25` | REFERENCE_ONLY, PENDING. Name + slot labels only; zero call contract. The absence of an `IN`/`OUT` block is recorded as absence, not as proof of no inputs |
| S2 | Stum generation table, `https://www.stum.de/2025/pcenginebiosoffsets/` (2025-07-28; CC BY-NC-SA 4.0 display, fact citation only; re-checked 2026-11-19, no table reproduced) | Row `ex_scrmod`: JSR `$E06F`; per-generation bodies `1.0=$E333`, `2.0/2.1/3.0=$E29D`, `TG2.0/TG3.0=$E2B6` (addresses cited as facts only, never used as implementation or entry addresses). Header is Method / JSR / 1.0 / 2.0 / 2.1 / 3.0 / TG2.0 / TG3.0. The JSR column is the stable jump-table entry; body addresses move by version. General BIOS discipline on the same page: startup mapping assumptions (MPR#7=`$00`, MPR#0=`$FF`, MPR#1=`$F8` assumed/required; JSR-via-table discipline) | REFERENCE_ONLY, PENDING. Mapping + per-generation body grouping only; no inputs/outputs/side effects. Non-commercial condition means no adoption path via this source |
| S3 | pce-devel/huc `include/hucc/pcengine.inc` (caller-side header; BSL-1.0; PENDING; pinned hash recorded in `docs/reuse/external-verification-log-p7.md:55` as `b6a25d33…`, verified 2026-11-18 per docs/84-4a-contract.md S1) | Caller-side equate pattern `ex_* = $00:E0xx, FUNC` inside the CDROM conditional block (line 548 for `$4A`; same block holds the `$23-$25` family per `spec/api_contracts.json` mapping sources). Equate existence is a caller fact | REFERENCE_ONLY, PENDING. Name-to-entry equate only; no callee behavior. Nothing copied. The equate proves the SDK spells the name, not what the BIOS does |
| S4 | mckayemu/hugo `bios.c` (HLE emulator source; GPL-2.0; PENDING; REFERENCE_ONLY candidate per `docs/reuse-inventory.md`; pinned hash `db3f227e…` per same verification log) | HLE selector-name label pattern (`KEY_BIOS 0x4A` observed for `$4A`; same selector table holds the EX display family). Selector only | REFERENCE_ONLY, PENDING. Name only; explicitly not entry proof; nothing copied. An HLE selector is not proof of jump-table entry or behavior |
| S5 | NEC HuC6270 manual (public hardware fact) + pinned Geargrafx notes (`huc6270.h` `k_register_mask[5] = 0x1FFF`, `RenderLine` `latched_cr & 0x80/0x40`; HuDK `vdc_crl`/`vdc_crh` RAM-copy practice; vdcdox names VRR the only valid data-port read) + in-tree citations (`src/overrides/slot_2E.s:4-7`, `src/overrides/slot_23.s:9`) | CR R05 bit 7 = BB (background enable), bit 6 = SB (sprite enable); CR high bits 13-15 unwritable; MWR R09 writable mask `0x00FF` (high byte not stored); VDC ports `ST0=$0000` (AR select), `ST1=$0002` (data low), `ST2=$0003` (data high); AR is the selected-register latch; port RMW is unreliable on the pinned core (stale `m_read_buffer`) so the DEV family owns a RAM shadow instead of reading the port | SOURCE_SUPPORTED as hardware facts only. They justify DEV masks but say nothing about the `$25` BIOS routine |
| S6 | In-tree DEV precedents: `src/overrides/slot_23.s` (EX_SCRSIZ DEV, Issue #45) + `src/overrides/slot_2E.s` (EX_BGON DEV, Issue #56) + `src/ex_dspoff.s` + harnesses `tests/integration/geargrafx_dev_scrsiz.py`, `tests/integration/geargrafx_dev_cr.py`, `tests/host/test_api_scrsiz_dev.py`, `tests/host/test_api_cr_dev.py` | Project-own DEV contracts only (see section 5). Reusable as engineering discipline (MPR0=`$FF` window with SEI, AR handling, stack depth, shadow ownership, C=0 ok / C=1 reject convention), NOT as reference evidence | PROJECT_SPEC_ONLY for their own slots. Reuse as template is allowed; reuse as `$25` ABI proof is forbidden |
| S7 | Sanitized G5 observations already on GitHub (Owner-permitted, read-only, structural only; this document adds no new measurement) | (a) Standard G5 image (PR #801 wiring, `$4A` stub): CUE[0] `$02->$33->$4A` stub halt; CUE[1] `$1E->$4A` stub halt (docs/g5-commercial-reobservation-2026-11-18.md; docs/g5-evidence-2026-11-18.md). (b) SIMULATED `$4A` diagnostic (PR #803, 5-byte RTS+NOP patch): CUE[0] advanced conditionally to `$23->$25` (SIMULATED_NEXT_API); CUE[1] to `$02->$02->$21->$1C`. (c) `$4A` experimental preservation body (PR #824 merged): CUE[0] `$02->$33->$4A->$23->$25` halt (was `$4A` stub halt); CUE[1] `$1E->$4A->$02->$02->$21->$1C` halt. `$4A` real return observed on both (JSR MATCH, cold/warm identical). Next blockers recorded as `$25` EX_SCRMOD (#47) and `$1C` BM_DELETE (#38), both not implemented | OBSERVED (call-state interface only: API order, stub-halt slot, cold/warm match). No memory/sector/content data. Genuine `$4A`/`$23`/`$25` semantics stay UNKNOWN/BLOCKED; SIMULATED returns are never genuine |
| S8 | Project ledgers: `spec/api_slots.json` (id 37 = `EX_SCRMOD`), `spec/api_contracts.json` (slot 37: `candidate_name EX_SCRMOD`, `candidate_entry 0xE06F`, `mapping_status HYPOTHESIS`, all 11 behavioral fields UNKNOWN), `spec/dev_implementations.json` (id 37: `state NOT_READY`, `abi_contract UNKNOWN`, `implementation null`), `tools/build_rom.py` (81-slot JSR table at `$E000+3*slot`, stub `LDX #slot; JMP api_unimplemented`) | Current ledger state: mapping is hypothesis, behavior is zero, no DEV override for `$25`, table mechanics verified by `make dev-test` layout checks | Project record, unchanged by this document. The table mechanics (`$E06F = $E000+3*$25`) are project-verified layout facts, not reference-ABI proof |

License/provenance gate: S1-S4 are PENDING and stay REFERENCE_ONLY.
No file or fragment from them enters the firmware. Any future `$25`
implementation must be original code written from interface observations
(S7) plus project-own DEV discipline (S6), never a translation of
third-party or reference code. `tools/provenance_guard.py` scope would
cover any new `src/overrides/slot_25.s` + host test + this doc.

Generation note (S2): for `$25`, bodies group as `1.0` vs `2.0/2.1/3.0`
vs `TG` — i.e. 2.x and 3.0 share the same body address (`$E29D`).
That address sameness does NOT prove behavioral sameness and does NOT
prove what the behavior is. The `$4A` slot by contrast has a different
body per generation (`1.0=$FCCA`, `2.x=$E175`, `3.0=$FE92`; see
docs/84-4a-contract.md S2). The two slots therefore have different
generation-difference structures; neither structure yields a call contract.

## 3. ABI contract table (ABI契約表)

Classification used in this section (task taxonomy):

- `SOURCE_CONFIRMED`: pinned independent source explicitly states the fact
  and no conflict exists; or project-own fact measured by the pinned harness.
  A caller-side fact (equate exists) is SOURCE_CONFIRMED only as a caller
  fact, never as a callee claim.
- `INFERRED`: strict logical consequence of SOURCE_CONFIRMED facts
  (e.g. address arithmetic), with the inference stated.
- `HYPOTHESIS`: single-source claim or label-only agreement without behavior;
  usable as a research pointer, never as implementation grounds.
- `UNKNOWN`: no trustworthy source; must stay unimplemented.

Every row keeps the reference callee and the project DEV layer separate.
"Reference" means the genuine System Card 3.0 callee behind `$E06F`.
"DEV" means any future project-own routine (currently nonexistent for `$25`).

| # | Item | Verdict | Grounds |
|---|------|---------|---------|
| 3-1 | Slot number `$25` (37 decimal) | SOURCE_CONFIRMED (ledger fact) | S8: `api_slots.json` id 37, `api_contracts.json` slot 37, `api-inventory.md:62` all agree on the slot index. Ledger existence is confirmed; callee behavior is not |
| 3-2 | Guest jump entry `$E06F` (`$E000 + 3*$25`) | HYPOTHESIS for the reference mapping; INFERRED arithmetic; SOURCE_CONFIRMED for project table mechanics | S1+S2 agree on the label `$E06F` (HYPOTHESIS as a BIOS claim). Arithmetic `$E000+3*0x25=$E06F` is INFERRED and trivially checkable. Project table mechanics are SOURCE_CONFIRMED: `tools/build_rom.py:render_table` emits `JMP` at `$E000+3*i` and `make dev-test` verifies the 81-slot layout; but that proves what OUR ROM does, not what the reference does |
| 3-3 | API identity `EX_SCRMOD` (vs aliases) | HYPOTHESIS | S1+S2+S3 agree on the spelling `EX_SCRMOD` only. No conflict is recorded for `$25` (unlike `$0D`/`$4A`/`$4C`/`$50` in docs/api-inventory.md Known conflicts). Single-system agreement on a name is still HYPOTHESIS for behavior; no promotion without provenance review (#8) |
| 3-4 | Generation differences (1.0 vs 2.x vs 3.0 vs TG) | Names/bodies HYPOTHESIS; behavior UNKNOWN; "2.x == 3.0" sameness INFERRED only as address equality | S2 address grouping (S8 ledger cites S2 as mapping source). No source describes what any generation body does. Address equality (`$E29D` for 2.x and 3.0) is INFERRED from the cited table; behavioral equality from it would be a guess and is forbidden |
| 3-5 | Inputs: A/X/Y registers | UNKNOWN (genuine); no DEV contract exists | S1 has no `IN` block for `$23/$24/$25` (absence recorded, not evidence). S2/S3/S4 name no registers. S7 commercial probe is observation-only by design (entry regs for `$4A` were recorded in docs/84-4a-contract.md S7, but no `$25` entry regs were measured because execution halts AT `$25` without entering a body). Caller work-RAM `$20F8-$20FF` (`al..dh`) are caller variables (S1 register table), not callee inputs: caller writes do not prove callee reads |
| 3-6 | Inputs: BIOS work RAM `al/ah/bl/bh/cl/ch/dl/dh` (`$20F8-$20FF`) | UNKNOWN | S1 defines the addresses (SOURCE_CONFIRMED as address definitions). No source states the `$25` callee reads/writes any of them. HuC caller wrappers (S3 block) are not recorded for `$25`; even where wrappers exist for other slots (e.g. CD_READ `_cl/_ch/_dl`), the wrapper write is SOURCE_CONFIRMED as a caller fact and UNKNOWN as a callee read (see docs/cd-base-abi-ledger.md pattern) |
| 3-7 | Return values (A/X/Y, work RAM, error codes) | UNKNOWN | No source defines any `$25` return or error path. S7 halt classification is `API_UNIMPLEMENTED`, not a return code. Future DEV C=0/C=1 conventions (S6) are project-own and must not be presented as genuine |
| 3-8 | Clobbered / preserved registers | UNKNOWN | No source. S6 DEV discipline (A/X/Y/MPR preserved on both paths; N/Z/V/D/I/T preserved via PLP, C is the result bit) is PROJECT_SPEC_ONLY for slots `$23`/`$2E` and is not transferable to `$25` as ABI |
| 3-9 | MPR / bank preconditions and preservation | Precondition UNKNOWN per-slot; general discipline HYPOTHESIS; entry OBSERVED only for neighbors | S2 general discipline (MPR0=`$FF`, MPR1=`$F8`, MPR7=`$00` at startup) is HYPOTHESIS as a general claim. S7: MPR1=`$F8` at neighboring `$4A` entries (OBSERVED). What genuine `$25` requires/preserves beyond that: UNKNOWN. MPR1=`$F8` work-RAM mapping is required by every DEV shadow design (S6) but that is a DEV precondition, not a reference fact |
| 3-10 | Stack and return PC mechanics | Mechanics SOURCE_CONFIRMED (project table); `$25` return semantics UNKNOWN | S8 + S7 probe classifier pattern: table JSR pushes return PC, SP delta 2, `stack_check=MATCH` when a body returns via RTS (observed for `$02`/`$33`/`$4A`-experimental). That mechanics layer is SOURCE_CONFIRMED for OUR table. Whether genuine `$25` returns at all, with what SP discipline, is UNKNOWN (current `$25` entry is `LDX #$25; JMP api_unimplemented` → `BRA -2`, never returns) |
| 3-11 | BIOS work-RAM write range (shadows, `$20F3`/`$20F7`, `$2502` family) | UNKNOWN (genuine); DEV shadows are PROJECT_SPEC_ONLY | S1's `$20F3` vs `$20F7` conflict (register table vs per-function one-liners for `$2A-$33`) leaves even the reference shadow address unresolved for the NEIGHBOR family (docs/61-dspoff-contract.md S1). For `$25` there is not even a one-liner. The DEV `$2502`/`$2503`/`$2504` shadow (S6) is project-own for the CR family; the DEV `$23` body writes NO work RAM. Neither may be claimed for `$25` |
| 3-12 | Flags (Carry/Zero etc.) | UNKNOWN (genuine) | No source. S7 `$4A` return-Carry sensitivity (forcing C=0 at `$4A` return diverted CUE[0] from `$23` to `$05`; see docs/84-4a-contract.md S8) constrains the CALLER's branch on `$4A`, not `$25` flags. The pinned-core P-latch note (HuC6280 `SetP` masks with `$EF`; PHP/PLP restores every latch bit) is a SOURCE_CONFIRMED platform fact for future preservation code, not a `$25` contract |
| 3-13 | Screen mode and VDC timing values | UNKNOWN | The Japanese focus line in docs/api-inventory.md:62 ("画面モード設定・VDCタイミング値を調べる") is a research pointer, not a contract. S1 gives no values; S2 gives only body addresses; S5 gives hardware register facts without BIOS mapping. Which VDC register(s) (`MWR R09`? `CR R05`? `VCE`? timing registers?), which bits, and when they take effect (immediate vs next horizontal/vertical period vs VBlank wait): all UNKNOWN |
| 3-14 | IRQ / events / PSG / input side effects | UNKNOWN | No source. S6 DEV contracts explicitly exclude zero-page/VRAM/joypad/CD/ADPCM/BURAM access; that exclusion is a DEV property, not a reference measurement |
| 3-15 | Error / unsupported / boundary behavior | UNKNOWN | No source defines any `$25` error return, refusal, or boundary. Future DEV reject paths (C=1, no VDC/RAM write outside transient stack slots) would be project-own conventions |
| 3-16 | Synchronization / timing (VBlank wait, cycles) | UNKNOWN | No source. S6 DEV bodies perform no VBlank wait by contract; that is a stated DEV limitation, not a reference timing claim |

Summary: the only SOURCE_CONFIRMED facts are (a) public hardware register
facts (S5), (b) project table/ledger mechanics (S8), and (c) the sanitized
halt order (S7). The name/entry mapping is HYPOTHESIS. Every behavioral
field of the genuine `$25` callee is UNKNOWN. No row is promoted.

## 4. VDC side effects and preservation conditions (VDC副作用と保存条件)

Genuine `$25` VDC behavior is UNKNOWN (3-13). This section states what is
known about the hardware and about the neighboring DEV implementations so
that a future `$25` design does not silently inherit the wrong register,
the wrong shadow, or the wrong timing.

### 4.1 Hardware facts (SOURCE_CONFIRMED, not BIOS behavior)

- VDC register select/write ports via the MPR0=`$FF` window: `ST0=$0000`
  (AR select), `ST1=$0002` (data low), `ST2=$0003` (data high); absolute
  `a:` form is required (never zero page); `MPR0=$FF` with `SEI`, restored
  before RTS (S5, S6 discipline).
- `MWR` is VDC register `$09`; writable mask `0x00FF` — high byte is not
  stored (S5; `src/overrides/slot_23.s:9`). DEV `$23` therefore writes
  low=`A`, high=`$00`, leaves `AR=9`.
- `CR` is VDC register `$05`; writable mask `0x1FFF` (high bits 13-15
  unwritable); bit 7 = BB, bit 6 = SB (S5; `src/overrides/slot_2E.s:4-7`).
  DEV `$2E`/`$33` family owns CR-low in work RAM `$2502` (+ `$A5` marker
  `$2503`, reserved `$2504`) because a port read-modify-write would destroy
  other CR bits on the pinned core (stale `m_read_buffer`).
- New CR values are effective from the next horizontal display period
  (S6 convention, hardware timing); MWR effect timing for DEV `$23` is
  stated only as "real VDC MWR state change" with no VBlank wait performed
  (`src/overrides/slot_23.s:13`).

None of the above selects the `$25` target register. In particular:

- `$25` is NOT known to be MWR, CR, VCE, or a timing register. The names
  suggest `$23`=size, `$24`=dot clock, `$25`=screen mode, `$26`=auto-increment
  (`IN A=$00-$03`), `$27`=vertical, `$28`=horizontal — but the suggestion
  comes from English names only. Names are not inputs.
- `$25` is NOT known to share the `$2502` CR shadow, the `$20F3`/`$20F7`
  reference shadow (itself conflicted, see docs/61-dspoff-contract.md S1),
  or the `$20F8-$20FF` caller variables. Assuming any of them as the `$25`
  operand would be a guess.
- VBlank-wait behavior is NOT known. DEV `$23` and the CR family perform
  no VBlank wait by contract; whether the genuine `$25` waits, polls,
  or takes effect immediately is UNKNOWN.

### 4.2 Preservation conditions any future DEV `$25` must meet

These are project-own engineering requirements (S6 template), not
reference claims. Any future `$25` body — experimental image only unless
a layout change is separately approved — must at minimum:

- Preserve A/X/Y/MPRs; preserve N/Z/V/D/I/T via PLP restore with C as the
  only result bit (same convention as `$23`/`$2E`/`ex_dspoff.s`).
- Use a single straight-line MPR0=`$FF` window with SEI; validate BEFORE
  the window; restore MPR0 before RTS; never remap MPR1 (stack page
  `$2100-$21FF`); assume NMI absent (S6).
- Leave AR at the documented register for the chosen target (e.g. 9 for
  MWR, 5 for CR) — but the target itself must first be justified; AR value
  without target justification is meaningless.
- Bound the stack: max pushes below JSR stated explicitly (3 for the CR
  family, 5 for `$23`); caller SP minimum stated (`>= $05` for the CR
  family); `$04` and below out of contract; pushes balanced before RTS;
  transient stack bytes `$21(SP-k)..$21(SP)` are the ONLY allowed RAM
  writes on a reject path.
- Own its work-RAM shadow explicitly (address, marker, reserved bytes,
  single-writer rule, desync out-of-contract rule, no-direct-write
  discipline with caller re-seed responsibility) OR state "no work-RAM
  writes" with real-core RAM-diff proof (as `$23` does). Silence on this
  point is a defect.
- State VDC effect timing explicitly (immediate / next horizontal period /
  VBlank-gated) and mark it DEV-measured vs UNKNOWN-reference.

Until the genuine target register is known, the only preservation that
can be claimed for `$25` is "the current stub preserves nothing and never
returns" (fail-closed `BRA -2`). Any returning body must prove the above
on the pinned core with mutants (see section 6).

## 5. Comparison with `$23` EX_SCRSIZ (`$23`との比較)

`$23` is the closest implemented neighbor (DEV_EXPERIMENTAL MWR writer)
and the immediate predecessor in the observed `$23->$25` halt order. It is
a discipline template, never ABI evidence.

| Aspect | `$23` EX_SCRSIZ (Issue #45) | `$25` EX_SCRMOD (Issue #47) | What transfers |
|--------|-----------------------------|-----------------------------|----------------|
| Slot / entry | `$23` / `$E069` (`$E000+3*$23`), HYPOTHESIS mapping | `$25` / `$E06F` (`$E000+3*$25`), HYPOTHESIS mapping | Table mechanics identical (S8). No behavior follows |
| Zeograd | Bare heading, no `IN`/`OUT` (same as `$24`/`$25`) | Bare heading, no `IN`/`OUT` (same as `$23`/`$24`) | Equal ignorance: neither has a public call contract |
| Stum bodies | `1.0=$E2FC`, `2.x/3.0=$E267`, `TG=$E280` | `1.0=$E333`, `2.x/3.0=$E29D`, `TG=$E2B6` | Different bodies; 2.x==3.0 grouping holds for both but proves nothing behavioral. `$25` bodies are larger-distance from `$23` bodies; no cross-slot inference allowed |
| DEV status | IMPLEMENTED (project-own): `src/overrides/slot_23.s`, `spec/dev_implementations.json` id 35 `PROJECT_SPEC_ONLY`, 18/81 DEV count includes it | NOT IMPLEMENTED: no `src/overrides/slot_25.s`, id 37 `UNKNOWN`/`null`/`NOT_READY`, 63 stubs include it | `$23` DEV existence does not advance `$25` by one step |
| DEV VDC target | MWR R09 low=`A`, high=`$00`, `AR` left at 9, no work-RAM writes, no VBlank wait | UNKNOWN target, UNKNOWN shadow, UNKNOWN timing | The MWR choice for `$23` is itself DEV-only (genuine `$23` ABI is UNKNOWN/UNVERIFIED). Copying "MWR low from A" to `$25` would duplicate an unproven choice onto a different slot |
| DEV inputs | `A` = MWR low (DEV-defined); X preserved/unused, Y preserved | No DEV inputs defined; A/X/Y all UNKNOWN | X-preserved/unused is a `$23`-DEV property, not a `$25` fact |
| Preservation | A/X/Y/MPR0 preserved; PLP restore; max 5 pushes; caller MPR0 saved/restored; `MPR1=$F8` assumed | Must be defined from scratch if ever implemented; current stub preserves nothing (fail-closed) | Window discipline (SEI + MPR0=`$FF` + absolute form + restore) and stack-bounding method transfer as engineering practice; register roles do not |
| Harness | `geargrafx_dev_scrsiz.py` (6 cases: `00/11/31/5A/FF/34` with X variants; asserts VDC R9, AR=9, A/X/Y, SP, MPRs) + `test_api_scrsiz_dev.py` (static: exports, DEV markers, `sta a:$0000/$0002`, `stz a:$0003`, `lda #$09`, TMA/TAM, PLY/PLX) | No harness; plan in section 6 proposes a parallel skeleton (case IDs T01-Txx) reusing the same probe shape but with TARGET TBD | Probe shape (compile-probe → synthetic CUE → `RESULT` JSON → assert PC/halt, VDC state, regs, SP, MPRs) transfers; expected VDC values do NOT (no `$25` oracle exists) |
| G5 role | `$23` returns in the observed chain (project experimental implementation, not genuine ABI) | `$25` is the halt AFTER `$23` returns; `$25` genuine contract UNKNOWN/BLOCKED | `$23` returning does not imply `$25` inputs; the `$23->$25` order is an observed call sequence, not a data-flow proof |
| Forbidden reuse | — | Do NOT cite `$23` DEV MWR behavior, `$23` host/real-core PASS, or `$23` static checks as `$25` evidence. Do NOT wire `$25` to the `$23` body or share its shadow. Do NOT claim "screen size vs screen mode differ only by register" | Recorded here so review can check it |

Bottom line: `$23` gives the project a worked example of how to specify,
implement, and test a VDC-touching DEV override WITHOUT claiming reference
compatibility. `$25` must repeat that full cycle from zero — it cannot
shortcut by inheriting `$23`'s register, values, or PASS.

## 6. Synthetic test plan, self-made data only (synthetic正例・負例・境界例)

Scope: host + pinned Geargrafx real core (`b49ae82a`, cc65 `71746c8`
per `tools/dependencies.json`), synthetic media generated by
`tools/make_cd_image.py` (deterministic `OSSPCE-CD-TEST-V1` payload stream)
or bare synthetic CUEs as in `geargrafx_dev_scrsiz.py` (`d.bin` zeros +
minimal CUE). No commercial media, no reference BIOS, no second emulator
required (cross-check when feasible per Issue template, SKIP otherwise).
All verdicts are PROJECT_SPEC_ONLY or SIMULATED; genuine PASS is impossible
until the reference contract is known. `./rom/` is not used.

### 6.1 What is tested at each stage

Stage G0 (already true, regression-locked): `$25` table entry is the
fail-closed stub. Any guest `JSR $E06F` must NOT return (halt at
`api_unimplemented` `BRA -2` with `X=$25` marker). This is the ONLY
`$25` behavior the project may currently claim.

Stage G1 (this document proposes, does NOT implement): host-side contract
shape + probe skeleton for a FUTURE experimental `$25` body, mirroring
the `$23`/`$2E` harnesses. G1 tests the harness and the DEV discipline,
never the genuine ABI.

Stage G2 (BLOCKED): real VDC-meaningful `$25` processing. Entry to G2
requires the genuine target register, input encoding, and shadow/timing
to be resolved from provenance-approved evidence (#8). G2 is NOT reached
by this document.

### 6.2 Case table (IDs T01-T14; T = test, all synthetic)

Conventions: caller sets MPR1=`$F8` (work-RAM window), MPR0 initial=`$F8`
(so a missing restore is detectable, cf. scrsiz probe), MPR7=0, SP=`$FF`
unless varied, `Y` sentinel `$A5` where applicable. Each real-core case
asserts: halt PC reached, VDC state (`vdc_r9`/`vdc_ar` or CR as applicable
— TBD for `$25`, hence G1 asserts mechanics + invariance, not values),
`A/X/Y` preservation or DEV-defined result, `SP` balanced, MPRs preserved,
RAM diff limited to allowed sets. Cold+warm identical required.

| ID | Class | Setup (synthetic) | Action | Expect (DEV-discipline, NOT genuine) | Guards against |
|----|-------|-------------------|--------|--------------------------------------|----------------|
| T01 | 正例 (stub) | Any synthetic ROM with the 81-slot table | `JSR $E06F` from synthetic guest | No return; PC stays at `api_unimplemented` halt; `X=$25`; cold/warm identical | Claiming `$25` returns; table miswire (`JMP` target wrong) |
| T02 | 正例 (mechanics) | Experimental-image-only future body (NOT wired here): table `JMP` points at body | `JSR $E06F` then halt-loop | Return to caller+2 via RTS; `stack_check=MATCH`, SP delta 0, cold/warm identical | Non-returning table; SP leak; warm-only return |
| T03 | 正例 (preserve) | Future body, `A=$00/$FF`, `X=$12/$34`, `Y=$A5`, P with C set/clear | `JSR $E06F` | A/X/Y/MPRs preserved or DEV-documented result; N/Z/V/D/I/T preserved except documented C; AR left at documented register | Register clobber; flag leak; AR drift |
| T04 | 正例 (window) | Future body, MPR0=`$F8` entry | `JSR $E06F` | SEI during window; MPR0=`$FF` at `$0000/$0002` access; MPR0 restored; MPR1 never remapped | IRQ-inside-window corruption; MPR0 leak; MPR1 remap breaking stack (`$21xx`) |
| T05 | 正例 (RAM scope) | Future body, shadow seeded per its spec (or "no work-RAM" declared) | `JSR $E06F` | RAM diff ⊆ {transient `$21(SP-k)..$21(SP)`, declared shadow/sentinel, STORE record}; no zero-page/VRAM/CD/BURAM | Silent work-RAM/VRAM smash; shadow over-write |
| T06 | 負例 (bad table) | Mutant: table entry not `JMP` to body (rerouted to stub/next slot) | `JSR $E06F` | RED: halt-class mismatch / `return_matches_caller=false` | Table-reroute going unnoticed (cf. $4A 21-byte structural pin) |
| T07 | 負例 (forced-CLC/SEC) | Mutant: body forces C=0 or C=1 regardless of path | `JSR $E06F` (seeded + unseeded) | RED: ok-path vs reject-path C inverted; harness distinguishes | Result-flag fabrication (cf. $4A forced-CLC/SEC mutants) |
| T08 | 負例 (unseeded bypass) | Mutant: unseeded shadow takes ok path (writes VDC anyway) | `JSR $E06F` unseeded | RED: VDC/shadow invariance violated | Missing initialization gate (cf. `AND #$FF`/`$7F`/`$00` family for CR) |
| T09 | 負例 (mask mutants) | Mutants: wrong VDC mask (e.g. `AND #$FF` keeps all, `AND #$00` clears kept bits, high-byte rewrite) | `JSR $E06F` seeded | RED: VDC low/high mismatch vs DEV oracle | Wrong-bit preservation (cf. ex_dspoff 6-mutant set) |
| T10 | 負例 (stack smash) | Mutant: body writes outside `$21(SP-k)..$21(SP)` (e.g. `$2100` page smash) | `JSR $E06F` | RED: RAM diff shows out-of-scope write | Out-of-contract RAM write |
| T11 | 境界例 | `A=$00` / `$FF`, `X=$00` / `$80` (`$80` is the SETWRT reject boundary pattern; for `$25` it is a preservation probe, not a semantic boundary) | `JSR $E06F` | Preserved (or DEV-reject C=1 with no VDC/RAM write); no crash/timeout | Byte-boundary clobber; sign-bit mishandling |
| T12 | 境界例 | `SP=$05` (min-in-contract) vs `SP=$04` (out-of-contract) | `JSR $E06F` | `$05`: GREEN with balance; `$04`: out-of-contract, never GREEN (harness refuses or documents) | Stack-depth overrun (cf. CR family `$05` rule, `$23` 5-byte rule) |
| T13 | 境界例 | MPR1 != `$F8` (e.g. `$FF` I/O page, `$00` wrong bank) | `JSR $E06F` | BLOCKED/refused: no blind execution; if executed, must not corrupt I/O (gate BEFORE window) | Non-RAM-mapped shadow access (cf. $4A MPR1 gate) |
| T14 | 境界例 | Cold vs warm; `P` with C=0 vs C=1 entry; `Y` sentinel variations | `JSR $E06F` ×2 resets | Identical verdicts; entry-C does not leak into result except via documented path | Reset-dependent state; flag-latch confusion (B-bit note: plain PHP/PLP restores every latch bit on the pinned core) |

Oracle discipline (borrowed from #824): each negative/boundary mutant must
name its failure mode (which assert fails) — a crash or timeout alone is
not a RED proof. The $4A program used 9 body/table mutants with per-mutant
failure modes plus a 21-byte structural pin; a future `$25` program must
provide at least the T06-T10 mutant set with the same strictness before any
DEV claim.

What is NOT tested: genuine `$25` input encoding (UNKNOWN, so no oracle),
commercial-disc continuation past `$25` (SIMULATED scaffolding at best, never
G5_OBSERVED), second-emulator cross-check (SKIP until feasible), hardware
(NOT_TESTED).

## 7. Implementation policy comparison (実装方針の比較)

| Policy | What it does | G5 effect | Safety / cost | Verdict |
|--------|--------------|-----------|---------------|---------|
| A. Keep fail-closed stub (current) | `$E06F` stays `LDX #$25; JMP api_unimplemented` (`BRA -2`). No `src/overrides/slot_25.s`; ledgers unchanged; counts stay 18/81 DEV + 63 stubs (with `$4A`-experimental counted separately) | G5 halts at `$25` with class `API_UNIMPLEMENTED`. No pathfinding past `$25`, but no fabrication | Zero VDC/RAM risk; zero layout churn; fully reviewable. Cost: G5 stays blocked at `$25` until genuine contract is known | **REQUIRED baseline. This document implements A (by changing nothing).** Any other policy must keep A as the normal-ROM behavior |
| B. Experimental-image-only returning scaffold (SIMULATED, Owner-permitted only) | Route `$E06F` to a minimal returning body ONLY in an experimental image (BOOT-region inclusion like `experimental_slot_33`, or `cd_25="experimental"` opt-in like `cd_4a`; normal DEV ROM byte-identical). Minimal variants: (B1) pure preservation (save/restore A/X/Y/P/MPR, RTS, NO VDC touch); (B2) preservation + documented-no-write + C convention (project-experimental, NOT genuine) | Lets the synthetic G5 chain observe the NEXT API after `$25` (pathfinding), exactly as the #803 SIMULATED `$4A` RTS diagnostic and the #824 preservation body did for `$4A->$23->$25`. Result class is SIMULATED_NEXT_API, never genuine progress | Small ROM churn (BOOT region, no OVERRIDES ceiling hit if placed like `$33`-experimental); but EVERY observation past `$25` is conditional on a fabricated return and must be labeled SIMULATED. Risk: readers mistake scaffolding for implementation. Mitigation: manifest `system_card_abi: NO_CLAIM`, `release_eligible: false`, separate builder flag, default `stub` | **CONDITIONAL, pathfinding only.** Justified ONLY as a diagnostic to identify the next blocker (like S8 for `$4A`), with Owner permission, in an experimental image, default `stub`. Must never enter the normal ROM, never be called DEV_CALLABLE, never be cited as `$25` semantics |
| C. DEV VDC-touching override in the normal ROM (e.g. guess MWR/CR/VCE write from A) | Add `src/overrides/slot_25.s` wired via `overrides()` + ledger flip to `PROJECT_SPEC_ONLY`, with real VDC I/O | Would advance G5 past `$25` on a guessed register — indistinguishable from progress while potentially programming the WRONG VDC state (wrong screen mode/timing), desyncing the CR/MWR shadow family, and misleading title-screen assessment | Breaks the OVERRIDES `$E800` ceiling risk (cf. docs/61-dspoff-contract.md section 4: 5 bytes below overlay; any new override needs an approved relocation); creates a wrong-but-deterministic VDC state; violates "don't invent arguments" (Issue template); promotes an UNKNOWN to PROJECT_SPEC_ONLY without grounds | **FORBIDDEN.** No guessed VDC target, no guessed input encoding, no guessed shadow, no guessed timing. BLOCKED until section 9 criteria are met |

The `$4A` precedent (#824) does NOT transfer to `$25` as justification for
C. `$4A`'s preservation-only body was scoped to (i) OBSERVED entry regs on
two CUEs, (ii) a 12-run return-shape matrix proving Carry-sensitivity for
CUE[0] (S8), and (iii) an explicit "no other memory/bus effects" proof with
mutants. `$25` has no entry-reg observation (execution stops AT `$25`) and
no return-shape matrix. B1 without that grounding is scaffolding (B), not
processing. C without a VDC oracle is fabrication.

## 8. Open issues (未解決事項)

- U1. Genuine `$25` input encoding (A/X/Y vs `$20F8-$20FF` vs stack): UNKNOWN.
  Blocks every DEV input design. Depends on #1/#2/#8 (ROM map, provenance,
  caller evidence). No HuC `$25` wrapper catalogued; even if found, wrapper
  writes prove caller behavior only.
- U2. Genuine `$25` VDC target register(s) and bit assignments: UNKNOWN.
  Blocks every VDC-touching body. S1 silent; S2 address-only; S5 hardware-only.
  MWR-vs-CR-vs-VCE-vs-timing must not be guessed from names.
- U3. Genuine `$25` work-RAM footprint (`$20F3`/`$20F7` vs `$2502` family vs
  `$20F8-$20FF` vs none): UNKNOWN. The neighbor-family shadow conflict
  (`$20F3` vs `$20F7` on the same Zeograd page) is itself unresolved; `$25`
  inherits that uncertainty plus its own silence.
- U4. Genuine `$25` MPR/stack/flag/return contract: UNKNOWN. General MPR
  discipline is HYPOTHESIS; per-slot gate is unmeasured. P-latch B-bit note
  is a platform fact awaiting a contract to apply it to.
- U5. Genuine `$25` timing (immediate / next-line / VBlank-gated) and error
  paths: UNKNOWN. No source; no measurement path without a body.
- U6. Generation applicability (does the 2.x==3.0 body sameness mean the
  research target 3.0 inherits 2.x behavior?): BLOCKED. Needs primary-source
  confirmation + #8 approval. Until then, 3.0-only scope with no cross-version
  claim.
- U7. Relation to `$24` DOTMOD / `$26` IMODE / `$27` VMODE / `$28` HMODE:
  UNKNOWN. No source orders or separates them. Co-testing `$23-$28` as a
  "video group" is a harness convenience, not an ABI claim.
- U8. ROM-layout ceiling for any future normal-ROM override: measured for
  `$33` (OVERRIDES ends `$E7FB`, 5 bytes below `$E800` probe overlay;
  `slot_2E.s` 44 bytes is the size class). A `slot_25.s` of similar size
  would need the same approved relocation that `$33` lacked. Experimental
  BOOT-region placement (like `experimental_slot_33`) avoids the ceiling
  but keeps the body out of the normal ROM by design.
- U9. `$25` entry-register observation: NOT_RUN. The current stub never enters
  a body, so no `$25` entry snapshot (A/X/Y/P, MPRs, SP) exists even at the
  S7 level. A B-policy scaffold would produce the first such snapshot, but
  labeled SIMULATED-entry (conditional on the fabricated `$25` return's
  predecessors, not on genuine `$25` inputs).
- U10. Second-emulator / hardware cross-check: SKIP. Pinned Geargrafx is the
  only gate; vdcdox/HuDK practices are cited as practice, not as second-core
  evidence.

Each UNKNOWN above is a reason to NOT implement, not a gap to fill by
inference. The ledger stays `mapping_status: HYPOTHESIS`, all behavioral
fields UNKNOWN, `implementation: null`, `NOT_READY`, approval null.

## 9. Acceptance criteria for the next implementation task (次の実装タスクの受入条件)

A future task that moves `$25` beyond documentation (whether B-scaffold or,
much later, a VDC-meaningful DEV body) must meet ALL of the following.
Failure on any line is BLOCKED, never PASS-by-assumption.

1. Provenance review first (#8): new sources pinned (repo/URL, commit/tag,
   path, hash, author, per-file license, obligations); S1-S4 stay
   REFERENCE_ONLY unless a human APPROVES otherwise; no `vendor/` payload;
   `tools/provenance_guard.py` GREEN.
2. Contract delta documented: which U-item (U1-U10) is newly resolved, by
   which source, with SOURCE_CONFIRMED/INFERRED/HYPOTHESIS/UNKNOWN restated.
   HYPOTHESIS never grounds VDC writes.
3. Independent code only: original HuC6280 in `src/overrides/slot_25.s`
   (normal ROM) or BOOT-region experimental include (scaffold); no reference
   bytes; no third-party translation; MIT header per repo LICENSE.
4. Narrow scope + default-safe wiring: normal ROM default keeps the stub
   byte-identical (opt-in `cd_25="experimental"` or separate experimental
   builder like `build_ipl_loader_g5_4a.py` / `experimental_slot_33`);
   release and normal DEV paths untouched; manifest records
   `system_card_abi: NO_CLAIM`, `dev_callable: false`,
   `release_eligible: false`, `private_media_used: false`.
5. Synthetic fixtures + mutants (section 6): T01 stub RED→GREEN, T02-T05
   positive GREEN cold+warm, T06-T10 mutants all RED with named failure
   modes, T11-T14 boundaries documented (in-contract GREEN, out-of-contract
   refused). ROM/fixture hashes, emulator version (`b49ae82a`), cc65
   version (`71746c8`), regs/events recorded per run.
6. Real Geargrafx core only: `make test` (host), `make check`, `make
   dev-test` / `make integration-test` scoped suites, plus `git diff --check`
   clean. Mock success or ROM-load is never behavioral success. Commercial
   media runs (if Owner-permitted) follow the #802 opt-in path with
   sanitized structural output only; `./rom/` handling per AGENTS.md and
   CONTRIBUTING.md; presence of media never auto-creates a test.
7. Ledger honesty: `spec/api_contracts.json` slot 37 stays UNKNOWN/HYPOTHESIS
   unless the new evidence genuinely resolves it; `spec/dev_implementations.json`
   id 37 flips to `PROJECT_SPEC_ONLY` only for a VDC-meaningful DEV body with
   real-core proof, never for a B-scaffold; reference JSR gate stays 0/81;
   VERIFIED stays 0/81; DEV_CALLABLE stays 0 without owner approval;
   `release_eligible=false` stays; G5_OBSERVED stays false (title screen only).
8. Independent review + CI convergence: CRITICAL/MAJOR/MEDIUM 0 (CodeRabbit
   findings addressed in-PR); Required CI GREEN; no merge without Owner
   approval; Draft until then. No force-push/amend/reset; normal commits only.
   External-review limits are recorded as BLOCKED, not as approval.

## 10. Reuse, safety, and reproduction

- Strategy per docs/provenance.md + CONTRIBUTING.md (reuse first): S5
  hardware facts + S6 in-tree DEV discipline are the only reusable behavior
  references; S1-S4 are fact citations (no copy/translation); any future
  `$25` body is ORIGINAL project-own code, MIT, no reference bytes.
- This document adds no source, no fixture, no binary, no `vendor/` content,
  no banned extensions, no staged `./rom/` content. Gates and counts are
  untouched: reference JSR 0/81, VERIFIED 0/81, DEV_CALLABLE 0,
  `release_eligible=false`, G5_OBSERVED=false, DEV 18/81 + 63 stubs (plus
  the separately-counted `$4A`-experimental wiring).
- Residual risks: (1) S1's `$20F3` vs `$20F7` conflict is unresolved even for
  neighbors — `$25` shadow handling stays UNKNOWN; (2) any B-scaffold will be
  mistaken for implementation unless labeled SIMULATED in every table, log,
  and Issue comment; (3) OVERRIDES ceiling still blocks any future `$25`
  normal-ROM override until a layout change is approved separately.
- Reproduce the claims here: `make setup` (Linux x86_64, pinned tools),
  `make test`, `make check`, `git diff --check`; confirm
  `spec/api_contracts.json` slot 37 UNKNOWN, `spec/dev_implementations.json`
  id 37 `null`/`NOT_READY`, no `src/overrides/slot_25.s`, and the sanitized
  `$02->$33->$4A->$23->$25` order only from the linked GitHub observations
  (no local media read).

---

*Provenance of this document: original research synthesis, MIT (repository
LICENSE). No proprietary firmware, game, or reference-BIOS content was used.
Reviewers: check that no row above promotes HYPOTHESIS to fact and that no
sentence implies System Card compatibility from diagnostic progress.*
