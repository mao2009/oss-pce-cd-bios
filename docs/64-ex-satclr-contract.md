# $36 EX_SATCLR contract research (Issue #64, G5 P0 task)

Status: **reference contract UNKNOWN; no independent implementation; no verification.**
**NOT a compatible System Card BIOS. NOT genuine ABI. NOT DEV_CALLABLE.
`release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81 (unchanged by this document).
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, or saves
appear here. `./rom/` was not accessed in any form for this document
(no existence check, listing, or read).

Branch basis: `0a935398` (origin/main at task start, PR #828 merged, PR #829
$24 scaffold OPEN/unmerged and NOT used as a basis here per the task §3
dependency rule). This document changes no ledger, no source, no gate, and
no ROM bytes.

Dependency note (task §3): PR #829 (`$24` B1-analogous, HEAD `00eaf858`,
MERGE_READY/CI-pass/independent-review-passed, Owner approval pending) is
OPEN at task start. This document is based on latest main `0a935398`
WITHOUT #829 changes. `$24`-linkage verification (if any) is performed in
an explicitly separated dependency environment and is NEVER claimed as
integrated-main commercial success until the dependency resolves. The #829
branch/worktree is not modified and its changes are not duplicated here.

Integration update (PR #830 merge `e5bf19b9`): PR #829 MERGED into main `66ff2e64`; this branch now builds on integrated main with both `cd_24` and `cd_36`. The pre-merge `0a935398` basis and observations above remain as historical evidence; normative statements below describe the integrated tree.

## 1. Purpose and scope (目的と調査対象)

G5 commercial-CD boot path (parent #635, integration #802) reached the
sanitized observation in a separated dependency environment (main + #829
`$24` experimental + local `$36` probe, NOT integrated main):

CUE[0] ExpC: `$02 -> $33 -> $4A -> $23 -> $25 -> $24 -> $36 (unimplemented halt)`
(Issue #802 comment `7121732277`, PR #829 ExpC runs).

On pre-merge main `0a935398` (without #829), CUE[0] ExpB halts at
`$24` and `$36` is NOT reached; CUE[1] stays at
`$1E -> $4A -> $02 -> $02 -> $21 -> $1C` (Issue #38, BLOCKED_ABI, out of
scope). `$4A`/`$25` semantics stay UNKNOWN/BLOCKED; their experimental
returns are never genuine. `$24` experimental (PR #829, MERGED into main `66ff2e64`) is also SIMULATED,
and MUST NOT be mistaken for genuine capability.

This document investigates the `$36` (EX_SATCLR candidate) contract so that
an independent implementation can be scoped with evidence, and so that
what must NOT be implemented can be stated with grounds. It does NOT
complete the specification by guessing. In particular, the name SATCLR
MUST NOT be taken to mean "zero-fill VRAM/SATB", and "sprite hide" MUST NOT
be equated with "zero-fill". Which of WRAM software SAT / VRAM SAT /
VDC-internal SATB (if any) is the target — or whether several are operated —
is UNKNOWN until grounded.

Related: #64 (EX_SATCLR), #802 (G5 path), #635 (blocker measurement),
#829 (`$24` scaffold, MERGED into main `66ff2e64`), #46 (`$24`), #45 (`$23`),
#47 (`$25`), #84 (`$4A`), #38 (`$1C` BLOCKED). Tracker #3,
prerequisites #1, #2, #6, #8.

Required outcome: purpose/scope, references/provenance, ABI contract
table (SAT-target focus), WRAM/VRAM/VDC/SATB operation contract,
relationship with the existing display-init path
(`$33->$4A->$23->$25->$24->$36`), sprite-primitive verification plan
(self-made data only), D1/D2/D3 policy, open issues, acceptance criteria.

Safety: no main direct commit, independent worktree only (this worktree),
no interference with #829 branch/worktree or other sessions, no
force-push/amend/destructive reset, no `./rom/` access for this document,
no commercial-media disclosure, 0 reference-BIOS JSR, no VERIFIED
promotion, `release_eligible=false` kept, no implementation/merge/Issue-close
in this document.

## 2. References and provenance (参照資料とprovenance)

All external items are REFERENCE_ONLY / PENDING unless stated otherwise.
No file or fragment from them enters the firmware. No source text is
reproduced; only fact citations (URL, version, date, what was claimed).

| ID | Source (pinned where applicable) | What it establishes about $36 | Disposition |
|----|----------------------------------|-------------------------------|-------------|
| S1 | Zeograd System Card notes, `https://www.zeograd.com/download/pce_bios.html` (System Card 3.0 scope; reverse-engineering noted; re-fetched 2026-11-24 for the $24 task via curl, fact citation only; $36 section re-checked here from the same local copy `/tmp/pce_bios.html` 19170 bytes) | `EX_SATCLR, Function #$36` bare heading with NO `IN`/`OUT` block (like `$23/$24/$25`; unlike `$26` EX_IMODE with A-values, `$35` EX_SPRDMA with "$2214/5 to VDC reg #19" one-liner, `$38-$3A` with A/X bytes). Neighbor `$35` SPRDMA one-liner: "Copies the word form $2214/5 to VDC reg #19 (Sprite Attribute Table)". Display-family one-liners exist for `$2A-$33` only. Register table: `$20F3` current VDC reg, `$20F7` CR value, `$20F8-$20FF` BIOS work-RAM (`al..dh`), `$2214/$2215`, `$2227-$2232`, `$2243` | REFERENCE_ONLY, PENDING. Name + slot only; zero call contract. Bare heading recorded as absence, never as no-input proof. `$35` one-liner is about `$35`, never `$36` evidence |
| S2 | Stum generation table, `https://www.stum.de/2025/pcenginebiosoffsets/` (2025-07-28; CC BY-NC-SA 4.0 display, fact citation only; re-checked 2026-11-24, no table reproduced; local `/tmp/stum.html` 24169 bytes) | Row `ex_satclr`: JSR `$E0A2`; per-generation bodies `1.0=$E671`, `2.0/2.1/3.0=$E5DA`, `TG2.0/TG3.0=$E5F3`. Header Method/JSR/1.0/2.0/2.1/3.0/TG. JSR stable, bodies move. Same startup-mapping discipline note as for `$24` | REFERENCE_ONLY, PENDING. Mapping + grouping only. Non-commercial condition, no adoption path |
| S3 | pce-devel/huc `include/hucc/pcengine.inc` @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` (BSL-1.0; pin re-verified 2026-11-24: 18844 bytes, SHA-256 `b6a25d33…` matches, L525 `ex_satclr = $00:E0A2, FUNC` in CDROM block L464-552) | Caller-side equate only. `hucc-systemcard.asm` @ same pin (20230 bytes, SHA matches) grep for satclr/sprdma/sprput/sat yields zero hits: no C-callable wrapper for the sprite family (same as display family) | REFERENCE_ONLY, PENDING. Name-to-entry only. Nothing copied. Absence of wrapper recorded as absence |
| S4 | mckayemu/hugo `bios.c` @ `64ed226da2288738781743eeef228c069e8973a4` (GPL-2.0; pin re-verified 2026-11-24: 29130 bytes, SHA-256 `db3f227e…` matches) | Selector `#define EX_SATCLR 0x36` L98, debug name `case EX_SATCLR: return "EX_SATCLR"` L266-267, NO `case EX_SATCLR` body in `handle_bios()` (falls to `default:` restore). Same pattern as `$24/$25` | REFERENCE_ONLY, PENDING. Name only. REJECTED for ROM inclusion. Nothing copied |
| S5a | Pinned Geargrafx `b49ae82a` (`tools/dependencies.json`; GPL-3.0, test-only) — `src/huc6270.h` + `skills/.../huc6270_vdc.md` (VDC ports, R00-R13 map; R0F DCR/OCCR block-transfer control, R10 SOUR, R11 DESR, R12 LENR, R13 DVSSR VRAM–SATB source; R02 VRR/VWR shared index; R05 CR; status SR) + `src/huc6260.h` + `skills/.../huc6260_vce.md` (VCE `$0400` CR, `$0402` CTA, `$0404` CTW/CTR) | Hardware map AS IMPLEMENTED by the pinned core. SATB DMA registers + VRAM access path locations. Pin per `dependencies.json`; no line numbers pinned here | REFERENCE_ONLY, PENDING for license. Hardware facts only — NOT `$36` BIOS facts. No code copied. Hardware capability MUST NOT be mistaken for the BIOS calling convention |
| S5b | In-tree measured I/O practice (MIT): `src/overrides/slot_23.s` (VDC window), `src/ex_dspoff.s`, harnesses `geargrafx_dev_scrsiz.py`, `geargrafx_dev_cr.py` | VDC window discipline (MPR0 `$FF` + `SEI` + absolute + restore; AR left; max-push). NO in-tree DEV/VDC/VRAM SAT precedent (no BIOS body in this repo touches VRAM SAT, SATB DMA, or VCE palette for sprites) | PROJECT_SPEC_ONLY. Discipline reusable IF I/O ever justified. No SAT primitive exists in-tree to reuse |
| S5c | NEC HuC6270/HuC6260 manuals + PCE sprite-management public notes (UNPINNED — no title/edition/section/page/URL in-tree; not re-fetched) | Claimed hardware/usage notes (SAT layout, hidden-sprite encodings, DMA sequencing, timing). In-tree repetitions are NOT independent confirmations | HYPOTHESIS. Downgraded: no reconstructible citation. MUST NOT ground implementation until pinned |
| S6 | In-tree DEV precedents: `$23` DEV, `$2E` CR-family, `ex_dspoff.s`, `$4A` preservation, `$25` B1, `$24` B1-analogous (PR #829 MERGED into main `66ff2e64`; in-tree) + harnesses | Project-own contracts only. Reusable as discipline (stack accounting, no-RMW shadow practice, MPR1/`SEI`/absolute window, RAM-diff scoping), NEVER as `$36` proof. Carry-convention belongs to CR/B2 only, never assumed for `$36` | PROJECT_SPEC_ONLY. `$24` B1-analogous skeleton reusable as NO-I/O template ONLY; its SEMANTICS (or absence) never grounds `$36` |
| S7 | Sanitized G5 observations (Owner-permitted, structural only; this document adds no new measurement) | Main `0a935398`: CUE[0] ExpB `$02->$33->$4A->$23->$25->$24` halt (ExpB = `$4A`+`$25` exp, `$24` stub); CUE[1] `$1E->$4A->$02->$02->$21->$1C` halt. Dependency env (main + #829 `$24` exp, Issue #802 comment `7121732277`): CUE[0] ExpC `$02->$33->$4A->$23->$25->$24->$36` halt; CUE[1] unchanged `$1C`. `$36` entry observed ONLY as table JSR (`$E0A2`, delta 2, in_table, MPR1 `$F8`, SP balanced, stack MATCH, cold/warm identical). Upstream experimental returns never genuine; `$36` downstream of genuine `$4A` unproven | OBSERVED (call-state only). No memory/sector/content data. Genuine `$36` (and everything upstream genuine) UNKNOWN/BLOCKED |
| S8 | Ledgers: `spec/api_slots.json` id 54 = `EX_SATCLR`, `spec/api_contracts.json` slot 54 (`EX_SATCLR`, `0xE0A2`, HYPOTHESIS, all 11 UNKNOWN), `spec/dev_implementations.json` id 54 (`NOT_READY`, `null`), `tools/build_rom.py` 81-slot JSR table (`$E000+3*slot`, stub `LDX #slot; JMP api_unimplemented`) | Current state: hypothesis, zero behavior, no DEV override, table mechanics project-verified | Project record, unchanged. `$E0A2 = $E000+3*$36` is layout fact, not ABI proof |

Gate: S1-S4 PENDING/REFERENCE_ONLY; S5a test-dependency/REFERENCE_ONLY;
S5b PROJECT_SPEC_ONLY; S5c HYPOTHESIS; no file/fragment enters firmware.

## 3. ABI contract table (SAT-target focus)

| # | Item | Genuine `$36` | DEV `$36` | Disposition |
|---|------|---------------|-----------|-------------|
| 1 | Name/slot/entry | `EX_SATCLR` / `$36` / `$E0A2` candidate | none (stub halts) | S1/S2/S3/S4 name+entry HYPOTHESIS as callee; S8 HYPOTHESIS. SOURCE_CONFIRMED only as caller equate (S3 L525). `$E0A2` layout project-verified, not ABI proof |
| 2 | Generations | Bodies differ (S2: 1.0 `$E671`, 2.x `$E5DA`, TG `$E5F3`); JSR stable | N/A | Single-behavior assumption FORBIDDEN |
| 3-5 | Inputs A/X/Y | UNKNOWN | none | S1 bare heading (absence, not evidence). S2/S3/S4 name no regs. S7 probe is observation-only; dependency-env `$36` entry regs were recorded locally in private evidence but are NOT published and do NOT define an ABI (single-game single-callsite) |
| 6 | Work-RAM (`$20F3/$20F7/$2214/$2215/$2227-$2232/$2243`) | UNKNOWN | none | S1 lists addresses without direction for `$36`. Caller writes never prove callee contract. `$35` one-liner ("word form $2214/5 to VDC #19") is about `$35`, NEVER `$36` evidence. `$36` shadow UNKNOWN |
| 7 | MPR precondition | UNKNOWN (genuine); OBSERVED `$F8` on the single dependency path is NOT a contract | DEV discipline (if ever): MPR1 `$F8` caller precondition | S7 one-path observation never defines ABI. Synthetic harness validates MPR1 BEFORE JSR (T13-analog); NO in-body gate (same rationale as B1) |
| 8 | Stack/return | UNKNOWN | Scaffold-analogous (if D2): JSR `$E0A2` tail-JMP, RTS to caller+3, SP balanced (JSR 2 + max 4 transient, caller minimum `$06`) | Table mechanics project-verified (S8). Genuine return semantics UNKNOWN |
| 9 | Preserved/clobbered | UNKNOWN | Scaffold-analogous: A/X/Y/P exact incl Carry in the verified T=0 domain (T=1 excluded, see §7 U6), MPRs unchanged, AR unchanged | S6: `$23` preserves; `$25`/`$24` B1 preserve. Genuine `$36` preservation UNKNOWN. Any future body states SCAFFOLD contract, never genuine |
| 10 | SAT target: WRAM software SAT vs VRAM SAT vs VDC-internal SATB | UNKNOWN — the central question. Name alone proves nothing | Scaffold-analogous: NONE (no WRAM/VRAM/VDC/MMIO writes) | S5a gives hardware locations; S5b gives window discipline IF justified. B1-analogous performs zero I/O, so target questions are NOT_APPLICABLE to the scaffold. "Zero-fill" and "sprite hide" MUST NOT be equated without grounds. Which (if any) of the three stores is touched is UNPROVEN |
| 11 | VDC side effects (register select, MAWR/MARR/VRR/VWR, DCR/SOUR/DESR/LENR/DVSSR, SR) | UNKNOWN | Scaffold-analogous: NONE | Same as #10. Any future VDC write needs pinned-hardware (S5a) PLUS public BIOS-contract link `$36`-to-register — absent today |
| 12 | Shadow/AR/state | UNKNOWN | Scaffold-analogous: AR unchanged, no shadow, no state | S1 `$20F3`/`$20F7` caller-visible, not proven `$36` shadow |
| 13 | Flags (Carry/Z/N/V) | UNKNOWN | Scaffold-analogous: Carry preserved exactly (no CLC/SEC), other P latch-exact except T (pinned-core T-consumption, same as B1) | CR/B2 conventions never assumed for `$36`. Single-game entry P NOT published/contract |
| 14 | IRQ/VBlank/timing/DMA-wait | UNKNOWN | Scaffold-analogous: NMI/IRQ assumed absent across the short window; no wait/enable | S7 IDR/IRR unchanged on one path is not a contract. DMA-wait semantics UNKNOWN |
| 15 | Errors/rejects | UNKNOWN | Scaffold-analogous: NO refusal path | No public error codes. Future I/O body MIGHT define fail-closed rejects only with public grounds |
| 16 | Call-order (`$33->$4A->$23->$25->$24->$36`) | UNKNOWN genuine; OBSERVED order on CUE[0] dependency path only | N/A | S7 one-game order under experimental returns. Genuine ordering UNKNOWN. See §4 |

Verdict: genuine `$36` UNKNOWN in every behavioral field. D1 functional
(B1-analogous NO-I/O scaffold aside, ANY WRAM/VRAM/VDC write) is BLOCKED
without a public BIOS-contract source for target/range/value/flags/shadow.
D2 preservation-only scaffold is the ONLY implementable scope without
guessing, and ONLY for pathfinding (SIMULATED), never as genuine progress.

## 4. VDC/VRAM/SATB operation contract + relationship with the display-init path

### 4.1 Hardware facts (NOT BIOS behavior)

| Claim | Source | Use |
|-------|--------|-----|
| VDC ports/ST0/ST1/ST2, R00-R13 map; R0F DCR block-transfer control, R10 SOUR, R11 DESR, R12 LENR, R13 DVSSR VRAM–SATB source; R02 VRR/VWR shared; status SR | S5a (pinned core) | Location/encoding IF a future D1 body ever touches VDC/VRAM/SATB. Does NOT prove `$36` touches any of them |
| VCE `$0400`/`$0402`/`$0404` palette ports | S5a | Unrelated to SAT clearing except that sprite colors live in table RAM; MUST NOT be conflated with `$36` target |
| MPR0 `$FF` + `SEI` + absolute + restore; AR left; max-push | S5b | Discipline IF I/O ever justified. D2 uses NO window |
| SAT/SATB/DMA sequencing, hidden-sprite encodings, timing | S5c HYPOTHESIS | MUST NOT ground anything until pinned |

### 4.2 Preservation conditions (D2-analogous / future VDC)

- D2-analogous (IF selected): preservation only (A/X/Y/P incl Carry in the verified T=0 domain, MPRs unchanged, SP balanced, AR unchanged, no WRAM/VRAM/VDC/VCE/MMIO writes outside transient stack, no TAM/TMA/TXS/SEI requirement, no shadow, no mask, no refusal, SIMULATED). Rationale: genuine target UNKNOWN, so ANY clearing/writing would be guesswork (including "clear VRAM SAT", "zero SATB", "hide sprites", "wait DMA"). Applicable: T01-T03, T05-T07, T10-T14 (see §5 plan; design doc §3); T04/T08/T09 NOT_APPLICABLE.
- Future D1: needs (a) pinned hardware (S5a already) PLUS (b) public BIOS-contract for `$36` target/range/value/flags/shadow/timing (absent) PLUS (c) independent oracle (absent) PLUS (d) legal review (pending). Until (b)-(d), D1 BLOCKED. `$35` SPRDMA behavior ("$2214/5 to #19") MUST NOT be copied as `$36` behavior (different slot, no link).

### 4.3 Relationship with `$33->$4A->$23->$25->$24->$36` (§7 task)

- `$33` DSPOFF (DEV) + `$23` MWR DEV + `$25`/`$24` B1 no-ops + `$36` halt. Order OBSERVED on one dependency path, NOT genuine dependence.
- `$25`/`$24` no-ops have NO side effects by design, so `$36` entry well-formedness (MPR1/SP/IRQ) does NOT depend on their side effects — it depends on caller discipline (satisfied). `$36` halt is unimplemented stub, NOT a consequence of upstream no-ops.
- Upstream display-init debt (`$25` + `$24` no-ops, plus `$23` DEV-only MWR) ACCUMULATES and MUST be tracked, never repaired by `$36` guessing. Missing preconditions (if any) must be recorded and linked to their own Issues (`#45/#47/#46`), never silently compensated inside `$36`.
- VDC/VRAM/SATB responsibility split UNKNOWN. MUST NOT assert `$36` owns any store.

## 5. Sprite-primitive verification plan (self-made data only)

Scope: host + pinned Geargrafx real core (`b49ae82a`, cc65 `71746c8`
per `tools/dependencies.json`; re-checked at implementation time).
All fixtures project-authored HuC6280; no commercial/game/reference bytes.

- Mechanics: PUBLIC IPL boot (2-base builder, commercial FIRST default; on integrated main: `cd_4a`+`cd_25`+`cd_24`+`cd_36` independent opt-ins, default all `stub`) -> JSR `$E0A2` (real table entry) -> preservation -> RTS -> halt. Stub default (`cd_36="stub"`) keeps `$E0A2` on fallback and traps X=`$36`.
- Preservation: C=0/C=1 (mutants RED on mismatched class only), A!=P, boundaries; MPRs/SP exact; SP `$FF`/`$06` balanced guest-executed with SP restored, `SP=$00/$05` probe-argument refusal verified (no guest execution, NOT_RUN/OUT_OF_CONTRACT for guest), below-minimum guest execution unrun; MPR1 `$FF` refused BEFORE JSR (never reaches `$36`). Probe refusal is harness discipline, never body refusal; genuine error contract UNKNOWN.
- No-target invariance (D2-analogous): dedicated probe asserts AR unchanged, all 20 VDC regs unchanged, MPRs/IDR/IRR unchanged, WRAM canary + full-buffer equality + per-instruction write history (exactly 6 transient stack bytes). VCE/palette explicitly OUT of `$36` scope (no claim). Any WRAM/VRAM/VDC write mutant RED; write-then-restore RED on history. Same-value-store invisibility documented (value-diff tracer limitation, same as B1).
- Chain (main-tree): synthetic `$33->$4A->$23->$25->$24->$36` through real entries on the integrated tree (the `$02` leg is PUBLIC boot; `$24` leg from merged #829). Pre-merge main-tree chain skipped `$24` (stub then; historical).
- Refusal-path absence: no refusal (T04/T08/T09 NOT_APPLICABLE), routing, 9-byte pin, buffer negatives.
- Primitives (task §8: WRAM SAT ops, VRAM writes, VDC select/addr/data, SATB DMA, sprite attrs, display enables, non-target preservation, shadow, IRQ, cold/warm): verified ONLY as isolated hardware exercises IF the investigation justifies them, NEVER as `$36` contract. No bulk generic-primitive addition for unclear future use.

Oracles are grounded in S5a (hardware locations) + S5b (window discipline, negative reference) + S6 (skeleton discipline). Implementation bytes NEVER ground their own oracles. Final-value equality NEVER substitutes for write-history (restore mutants).

## 6. D1/D2/D3 policy

| Policy | Content | Cost | Verdict |
|--------|---------|------|---------|
| D1 functional (genuine or limited) | Independent HuC6280 body with real WRAM/VRAM/VDC ops + shadow + validation + fail-closed, grounded in public contract + pinned hardware + independent oracle | Needs (b)-(d) above; none exist. ANY clearing/writing today (including "hide all sprites", "zero SAT", "wait SATB DMA") would be name-based guessing | BLOCKED. No public source defines `$36` target/range/value/flags/shadow/timing. Single-game entry values (NOT published) cannot define an ABI. Do NOT implement |
| D2 preservation-only (SIMULATED, opt-in) | Route `$E0A2` to a returning body ONLY in experimental images (BOOT, `cd_36="experimental"`; normal DEV byte-identical). Body: save/restore, RTS, NO writes, AR unchanged, MPRs unchanged, no work-RAM writes, SP balanced, Carry preserved (verified T=0 domain; T=1 excluded per §7 U6), no code. SIMULATED_NEXT_API, never genuine | No memory/VDC risk beyond transient stack; debt +1 no-op (`$25`+`$24`+`$36` = three no-ops on integrated main). Risk: mistaken for sprite fix; debt must be tracked. Mitigations: NO_CLAIM manifest, `release_eligible=false`, separate flag, default `stub`, every observation SIMULATED | CONDITIONAL, pathfinding only. Requires §8 acceptance (synthetic GREEN on the integrated tree; commercial stays NOT_RUN — NEVER claimed as commercial success). Its value is synthetic isolation + next-blocker identification. DO NOT select D2 for mere API-count increase — compare diagnostic value (next-blocker identification in dependency env) vs debt (+1 no-op). If the comparison fails, choose D3 |
| D3 BLOCKED | Record UNKNOWN, missing info, verification method, next steps | Zero code risk; zero boot progress in this task | FALLBACK if D2 debt/value comparison fails, if synthetic GREEN cannot be met, or if review/CI cannot converge. Prefer D3 over a debt-unjustified D2 |

`$35` SPRDMA ("$2214/5 to #19") does NOT transfer to `$36` as justification.
`$25`/`$24` preservation skeletons transfer ONLY as NO-I/O template/oracle
discipline, never as `$36` semantics.

## 7. Open issues

- U1. Genuine `$36` target/range/value/flags/shadow/timing: UNKNOWN (all §3). No public IN/OUT. Single-game values NOT ABI.
- U2. WRAM-SAT vs VRAM-SAT vs SATB linkage: HYPOTHESIS at best (name only). `$35` one-liner never grounds `$36`.
- U3. Generations: S2 bodies differ; single-behavior forbidden; commercial generations UNKNOWN (and stays UNKNOWN publicly).
- U4. Genuine ordering/dependence on `$23/$25/$24`: UNKNOWN. Observed order is NOT dependence (§4.3).
- U5. Display/sprite-init debt: `$25`+`$24`(+`$36` if D2) no-ops + `$23` DEV-only MWR = real sprite/display init entirely undone. Tracked, never presented as sprite correctness.
- U6. T-flag domain: same pinned-core T-consumption as B1; T=1 entry excluded.
- U7. Relation to `$35` SPRDMA / `$37` SPRPUT / `$34` DMAMOD: UNKNOWN (Zeograd documents `$35` target but nothing links it to `$36`).
- U8. Next blocker after `$36`: UNKNOWN until dependency-env ExpD re-observation (Phase H). CUE[1] `$1C` remains BLOCKED_ABI regardless.
- U9. Dependency: #829 MERGED (main `66ff2e64`). Main-tree `$36` work MUST NOT claim commercial results (commercial NOT_RUN). Pre-merge main-tree ExpD (with `$24` stub) halted at `$24` by construction (historical).

## 8. Acceptance criteria

D2 (IF selected after debt/value comparison) is accepted ONLY if ALL hold:

- (a) Contract doc (this file) + B1-analogous-equivalent design doc merged (or same-PR docs) with S1-S8, §3 UNKNOWN, §4 NO-I/O + non-dependence + debt, §5 plan, §6 D2-conditional, §7 U1-U9.
- (b) `src/ex_satclr.s` 9-byte preservation skeleton (PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/RTS), BOOT, `ex_satclr_entry`, NOT genuine/DEV_CALLABLE/SIMULATED, MPR1 `$F8` precondition, no I/O, Carry preserved, latch-exact, stack/NMI notes, ORIGINAL MIT.
- (c) Wiring behind `cd_36="experimental"` (default `stub` byte-identical): `build_experimental_bios_boot.py` (`_redirect_slot36_to_body` alongside `_redirect_slot24_to_body`, `unified_api_windows_36` lattice with `_4a_36`/`_25_36`/`_4a_25_36` supersets plus `_24`-containing supersets up to maximum `_4a_25_24_36` — 16 variants on integrated main), `build_ipl_loader_g5_2base.py` (`cd_36` param, incremental labels/sources/links, patch+window checks per combination, manifest `cd_36` + slots + sources, link_labels), normal DEV untouched, 256KiB + `$FFF6` + BOOT-span enforced, unallowed diffs refused, NO #829 code duplicated (formal merge `e5bf19b9` integrates origin/main; `_24_*` combinations in-tree).
- (d) Host builder tests + real-core suite (§5 T01-T14 analog) ALL GREEN on pinned core, cold/warm identical every run for the API-entry runs and the write-history tracer (cold+warm asserted), with VDC snapshot probe single-cycle (cold-boot pass; warm VDC NOT_RUN) + write-history probes (VCE/palette explicitly out of scope).
- (e) `make test`, `make check` (EXIT 0), `git diff --check`, RED→GREEN, mutants (target/range/off-by-one/value/WRAM-VRAM-confusion/non-target-write/bad-VDC-select/missing-side-effect/unneeded-MMIO/restore-masking/SATB-misop/shadow/save/SP/MPR/flag/RTS/success/wiring/boundary/default-mix-in ALL RED or N/A-with-reason), ALLOW/DENY, default identity, DEV invariance, capacity/boundary, Required CI GREEN, independent review 0/0/0, CodeRabbit `@coderabbitai review` with zero unresolved CRITICAL/MAJOR/MEDIUM.
- (f) Commercial (task §13 variant table, cold/warm, sanitized): synthetic ExpD `$02->$33->$4A->$23->$25->$24->$36->NEXT` (or next stub halt with slot/classification) on the integrated tree + CUE[1] `$1C` regression check; commercial runs NOT_RUN on all variants. Commercial success NEVER claimed. G5_OBSERVED stays false unless title evidence (not expected).
- (g) Safety gates unchanged (JSR 0/81, VERIFIED 0/81, DEV_CALLABLE false, release_eligible false, fail-closed, DEV invariance, SIMULATED vs genuine vs synthetic vs real-core vs commercial strictly distinguished). Frame/display: §12 items reported as OBSERVED/UNKNOWN/NOT_RUN; non-black/VBlank/frame NEVER promoted to title.

D1 acceptance is NOT defined here (BLOCKED; would need a contract revision with (b)-(d) PLUS public-contract grounds for every target/range/value/flag/write).

## 9. Reuse, safety, reproduction

- Reuse: `$25`/`$24` NO-I/O skeleton + wiring + oracles (discipline only); `$23` window discipline as negative reference; `$35` one-liner NEVER reused as `$36` behavior. No third-party code copied (all PENDING/REJECTED stay REFERENCE_ONLY).
- Safety: no `./rom/` access for this document; no commercial bytes; no reference BIOS execution/JSR/copy; no ledger/gate/ROM changes by this document.
- Reproduction: Zeograd/Stum/HuC/Hugo re-checks per §2 (local `/tmp` scratch from the $24 task, never committed/published); Geargrafx pin per `tools/dependencies.json` (`b49ae82a`/`71746c8`); G5 observations per Issue #802 comments `7119970902` (main) and `7121732277` (dependency env, NOT main success).

NOT a compatible System Card BIOS. Geargrafx observations are not hardware proof.
