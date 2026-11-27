# $35 EX_SPRDMA contract research (Issue #63, G5 P0 task)

Status: **reference contract UNKNOWN; no independent functional implementation; no verification.**
**NOT a compatible System Card BIOS. NOT genuine ABI. NOT DEV_CALLABLE.
`release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81 (unchanged by this document).
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, or saves
appear here. `./rom/` was not accessed in any form for this document
(no existence check, listing, or read).

Branch basis: `65b0cb56` (origin/main at task start, PR #832 merged, integrated
main with `cd_4a`+`cd_25`+`cd_24`+`cd_36`). This document changes no ledger,
no source, no gate, and no ROM bytes.

## 1. Purpose and scope (目的と調査対象)

G5 commercial-CD boot path (parent #635, integration #802) reached the
sanitized observation on integrated main (Issue #802 comment `7165514587`):

CUE[0] ExpD: `$02 -> $33 -> $4A -> $23 -> $25 -> $24 -> $36 -> $35 (unimplemented halt)`
(X=`$35`=53, entry `$E09F`, 8 APICALLs, cold/warm identical).

CUE[1] ExpD: `$1E -> $4A -> $02 -> $02 -> $21 -> $1C` (unchanged, `$1C` BLOCKED_ABI).

`$4A`/`$25`/`$24`/`$36` experimental returns are never genuine. `$35`
genuine semantics stay UNKNOWN/BLOCKED.

This document investigates the `$35` (EX_SPRDMA candidate) contract so that
an independent implementation can be scoped with evidence. It does NOT
complete the specification by guessing. In particular, the Zeograd one-liner
"Copies the word form $2214/5 to VDC reg #19" MUST NOT be taken as a full
calling convention (no input/output registers, no flags, no timing, no error
contract, no shadow discipline).

Related: #63 (`$35` EX_SPRDMA), #802 (G5 path), #635 (blocker measurement),
#64 (`$36`), #46 (`$24`), #47 (`$25`), #84 (`$4A`), #38 (`$1C` BLOCKED). Tracker #3,
prerequisites #1, #2, #6, #8.

Required outcome: purpose/scope, references/provenance, ABI contract table,
WRAM/VDC/SATB DMA operation contract, relationship with the display-init path
(`$33->$4A->$23->$25->$24->$36->$35`), sprite-primitive verification plan
(self-made data only), D1/D2 policy, open issues, acceptance criteria.

Safety: no main direct commit, independent worktree only, no interference
with other sessions, no force-push/amend/destructive reset, no `./rom/`
access for this document, no commercial-media disclosure, 0 reference-BIOS
JSR, no VERIFIED promotion, `release_eligible=false` kept, no
implementation/merge/Issue-close in this document.

## 2. References and provenance (参照資料とprovenance)

All external items are REFERENCE_ONLY / PENDING unless stated otherwise.
No file or fragment from them enters the firmware. No source text is
reproduced; only fact citations (URL, version, date, what was claimed).

| ID | Source (pinned where applicable) | What it establishes about $35 | Disposition |
|----|----------------------------------|-------------------------------|-------------|
| S1 | Zeograd System Card notes, `https://www.zeograd.com/download/pce_bios.html` (System Card 3.0 scope; reverse-engineering noted; re-fetched 2026-11-25 for this task, fact citation only; local `/tmp/pce_bios-63.html` 19170 bytes, SHA-256 `6966984b…` matches the $36-task copy) | `EX_SPRDMA, Function #$35` with `IN (N/A)` / `OUT (N/A)` + one-liner "Copies the word form $2214/5 to VDC reg #19 (Sprite Attribute Table)". Neighbor `$34` EX_DMAMOD one-liner ("sets VDC reg #15 and updates $2243") is about `$34`, never `$35` evidence. `$36` is a bare heading (no IN/OUT). Display-family one-liners exist for `$2A-$33` only | REFERENCE_ONLY, PENDING. Name + slot + target hint only; zero call contract. N/A recorded as absence of register contract, never as no-side-effect proof |
| S2 | Stum generation table, `https://www.stum.de/2025/pcenginebiosoffsets/` (2025-07-28; CC BY-NC-SA 4.0 display, fact citation only; re-checked 2026-11-25, no table reproduced; local `/tmp/stum-63.html` 24169 bytes) | Row `ex_sprdma`: JSR `$E09F`; per-generation bodies `1.0=$E4A2`, `2.0/2.1/3.0=$E40B`, `TG2.0/TG3.0=$E424`. Header Method/JSR/1.0/2.0/2.1/3.0/TG. JSR stable, bodies move | REFERENCE_ONLY, PENDING. Mapping + grouping only. Single-behavior assumption FORBIDDEN |
| S3 | pce-devel/huc `include/hucc/pcengine.inc` @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` (BSL-1.0; pin re-verified 2026-11-25: 18844 bytes, SHA-256 `b6a25d33…` matches, L524 `ex_sprdma = $00:E09F, FUNC` in CDROM block) + `hucc-systemcard.asm` @ same pin (no C-callable wrapper for the sprite family) | Caller-side equate only. Absence of wrapper recorded as absence | REFERENCE_ONLY, PENDING. Name-to-entry only. Nothing copied |
| S4 | mckayemu/hugo `bios.c` @ `64ed226da228873ee4f069e8973a4` (GPL-2.0; pin re-verified 2026-11-25: 29130 bytes, SHA-256 `db3f227e…` matches) | Selector `#define EX_SPRDMA 0x35` L97, debug name `case EX_SPRDMA: return "EX_SPRDMA"` L264-265, NO `case EX_SPRDMA` body in `handle_bios()` (falls to `default:`). Same pattern as `$24`/`$25`/`$36` | REFERENCE_ONLY, PENDING. Name only. REJECTED for ROM inclusion. Nothing copied |
| S5a | Pinned Geargrafx `b49ae82a` (`tools/dependencies.json`; GPL-3.0, test-only) — `src/huc6270.h` + `skills/.../huc6270_vdc.md` (VDC ports, R00-R13 map; R0F DCR block-transfer control, R10 SOUR, R11 DESR, R12 LENR, R13 DVSSR VRAM–SATB source; R02 VRR/VWR shared index; R05 CR; status SR) + `src/huc6260.h` + VCE docs | Hardware map AS IMPLEMENTED by the pinned core. R13 (decimal 19) = DVSSR = VRAM–SATB Transfer Source Address Register (16-bit). VDC register numbers `0x00-0x13` valid | REFERENCE_ONLY, PENDING for license. Hardware facts only — NOT `$35` BIOS facts. No code copied. Hardware capability MUST NOT be mistaken for the BIOS calling convention |
| S5b | In-tree measured I/O practice (MIT): `src/overrides/slot_23.s` (VDC window), `src/ex_dspoff.s`, harnesses `geargrafx_dev_scrsiz.py`, `geargrafx_dev_cr.py` | VDC window discipline (MPR0 `$FF` + `SEI` + absolute + restore; AR left; max-push). NO in-tree DEV/VDC/VRAM SAT precedent (no BIOS body in this repo touches VRAM SAT, SATB DMA, or VCE palette for sprites) | PROJECT_SPEC_ONLY. Discipline reusable IF I/O ever justified. No SAT primitive exists in-tree to reuse |
| S5c | NEC HuC6270/HuC6260 manuals + PCE sprite-management public notes (UNPINNED — no title/edition/section/page/URL in-tree; not re-fetched) | Claimed hardware/usage notes (SAT layout, hidden-sprite encodings, DMA sequencing, timing). In-tree repetitions are NOT independent confirmations | HYPOTHESIS. Downgraded: no reconstructible citation. MUST NOT ground implementation until pinned |
| S6 | In-tree DEV precedents: `$23` DEV, `$2E` CR-family, `ex_dspoff.s`, `$4A` preservation, `$25` B1, `$24` B1-analogous, `$36` B1-analogous + harnesses | Project-own contracts only. Reusable as discipline (stack accounting, no-RMW shadow practice, MPR1/`SEI`/absolute window, RAM-diff scoping), NEVER as `$35` proof. Carry-convention belongs to CR/B2 only, never assumed for `$35` | PROJECT_SPEC_ONLY. `$36` B1-analogous skeleton reusable as NO-I/O template ONLY; its SEMANTICS (or absence) never grounds `$35` |
| S7 | Sanitized G5 observations (Owner-permitted, structural only; this document adds no new measurement) | Integrated main `65b0cb56`: CUE[0] ExpD `$02->$33->$4A->$23->$25->$24->$36->$35` halt (8 APICALLs, X=`$35`=53, entry `$E09F`, all table JSR delta 2, in_table, MPR1 `$F8`, SP balanced, stack MATCH, cold/warm identical, budget-80M halt confirmed, no truncation). CUE[1] ExpD unchanged `$1E->$4A->$02->$02->$21->$1C` halt. `$35` entry observed ONLY as table JSR. Upstream experimental returns never genuine; `$35` downstream of genuine `$4A` unproven | OBSERVED (call-state only). No memory/sector/content data. Genuine `$35` (and everything upstream genuine) UNKNOWN/BLOCKED |
| S8 | Ledgers: `spec/api_slots.json` id 53 = `EX_SPRDMA`, `spec/api_contracts.json` slot 53 (`EX_SPRDMA`, `0xE09F`, HYPOTHESIS, all UNKNOWN), `spec/dev_implementations.json` id 53 (`NOT_READY`, `null`), `tools/build_rom.py` 81-slot JSR table (`$E000+3*slot`, stub `LDX #slot; JMP api_unimplemented`) | Current state: hypothesis, zero behavior, no DEV override, table mechanics project-verified | Project record, unchanged. `$E09F = $E000+3*$35` is layout fact, not ABI proof |

Gate: S1-S4 PENDING/REFERENCE_ONLY; S5a test-dependency/REFERENCE_ONLY;
S5b PROJECT_SPEC_ONLY; S5c HYPOTHESIS; no file/fragment enters firmware.

## 3. ABI contract table

| # | Item | Genuine `$35` | DEV `$35` | Disposition |
|---|------|---------------|-----------|-------------|
| 1 | Name/slot/entry | `EX_SPRDMA` / `$35` / `$E09F` candidate | none (stub halts) | S1/S2/S3/S4 name+entry HYPOTHESIS as callee; S8 HYPOTHESIS. SOURCE_CONFIRMED only as caller equate (S3 L524). `$E09F` layout project-verified, not ABI proof |
| 2 | Generations | Bodies differ (S2: 1.0 `$E4A2`, 2.x `$E40B`, TG `$E424`); JSR stable | N/A | Single-behavior assumption FORBIDDEN |
| 3-5 | Inputs A/X/Y | UNKNOWN (S1 IN N/A = absence of register contract, not proof of no inputs) | none | S1 N/A recorded as absence. S2/S3/S4 name no regs. S7 probe is observation-only; integrated-main ExpD `$35` entry regs were recorded locally in private evidence but are NOT published and do NOT define an ABI (single-game single-callsite) |
| 6 | Work-RAM (`$2214`/`$2215` + `$20F3`/`$20F7`/`$2243` family) | UNKNOWN direction/ownership/timing. S1 one-liner names `$2214/5` as a source word for VDC #19, but does NOT state who writes `$2214/5`, when, what values are legal, or whether `$35` reads vs writes them | none | S1 lists addresses without direction for `$35`. Caller writes never prove callee contract. `$34` one-liner ("VDC #15 + `$2243`") is about `$34`, NEVER `$35` evidence. `$35` shadow UNKNOWN |
| 7 | MPR precondition | UNKNOWN (genuine); OBSERVED `$F8` on the single integrated path is NOT a contract | DEV discipline (if ever): MPR1 `$F8` caller precondition | S7 one-path observation never defines ABI. Synthetic harness validates MPR1 BEFORE JSR (T13-analog); NO in-body gate (same rationale as B1) |
| 8 | Stack/return | UNKNOWN | Scaffold-analogous (if D2): JSR `$E09F` tail-JMP, RTS to caller+3, SP balanced (JSR 2 + max 4 transient, caller minimum `$06`) | Table mechanics project-verified (S8). Genuine return semantics UNKNOWN |
| 9 | Preserved/clobbered | UNKNOWN | Scaffold-analogous: A/X/Y/P exact incl Carry in the verified T=0 domain (T=1 excluded, same pinned-core ground as B1), MPRs unchanged, AR unchanged | S6: `$23` preserves; `$25`/`$24`/`$36` B1 preserve. Genuine `$35` preservation UNKNOWN. Any future body states SCAFFOLD contract, never genuine |
| 10 | SATB DMA target: VRAM SAT vs VDC-internal SATB, source `$2214/5` vs DVSSR | UNKNOWN — the central question. One-liner hints a `$2214/5 -> VDC #19` copy, but copy width (word), value domain, trigger vs setup, sync vs async, completion signaling are all UNPROVEN | Scaffold-analogous: NONE (no WRAM/VRAM/VDC/MMIO writes) | S5a gives hardware locations (R13 DVSSR = VRAM–SATB source); S5b gives window discipline IF justified. B1-analogous performs zero I/O, so target questions are NOT_APPLICABLE to the scaffold. "Copy word" MUST NOT be equated with a full DMA contract |
| 11 | VDC side effects (register select, MAWR/MARR/VRR/VWR, DCR/SOUR/DESR/LENR/DVSSR, SR, IRQ) | UNKNOWN | Scaffold-analogous: NONE | Same as #10. Any future VDC write needs pinned-hardware (S5a already) PLUS public BIOS-contract link `$35`-to-register (one-liner alone is insufficient: no value/timing/flag/shadow) |
| 12 | Shadow/AR/state | UNKNOWN | Scaffold-analogous: AR unchanged, no shadow, no state | S1 `$20F3`/`$20F7` caller-visible, not proven `$35` shadow. `$34` `$2243` update is about `$34`, never `$35` |
| 13 | Flags (Carry/Z/N/V) | UNKNOWN | Scaffold-analogous: Carry preserved exactly (no CLC/SEC), other P latch-exact except T (pinned-core T-consumption, same as B1) | CR/B2 conventions never assumed for `$35`. Single-game entry P NOT published/contract |
| 14 | IRQ/VBlank/timing/DMA-wait | UNKNOWN (sync vs async, wait vs fire-and-forget, VBlank alignment all UNPROVEN) | Scaffold-analogous: NMI/IRQ assumed absent across the short window; no wait/enable | S7 IDR/IRR unchanged on one path is not a contract. DMA-wait semantics UNKNOWN |
| 15 | Errors/rejects | UNKNOWN | Scaffold-analogous: NO refusal path | No public error codes. Future I/O body MIGHT define fail-closed rejects only with public grounds |
| 16 | Call-order (`$33->$4A->$23->$25->$24->$36->$35`) | UNKNOWN genuine; OBSERVED order on CUE[0] integrated path only | N/A | S7 one-game order under experimental returns. Genuine ordering UNKNOWN |

Verdict: genuine `$35` UNKNOWN in every behavioral field except a target hint.
D1 functional (ANY WRAM/VRAM/VDC write, DMA start/wait) is BLOCKED without a
public BIOS-contract source for target/range/value/flags/shadow/timing.
D2 preservation-only scaffold is the ONLY implementable scope without
guessing, and ONLY for pathfinding (SIMULATED), never as genuine progress.

## 4. VDC/VRAM/SATB DMA operation contract + relationship with the display-init path

### 4.1 Hardware facts (NOT BIOS behavior)

| Claim | Source | Use |
|-------|--------|-----|
| VDC ports/ST0/ST1/ST2, R00-R13 map; R0F DCR block-transfer control, R10 SOUR, R11 DESR, R12 LENR, R13 DVSSR VRAM–SATB source; R02 VRR/VWR shared; status SR | S5a (pinned core) | Location/encoding IF a future D1 body ever touches VDC/VRAM/SATB. Does NOT prove `$35` touches any of them |
| R13 decimal 19 = DVSSR (VRAM–SATB Transfer Source Address Register, 16-bit) | S5a | Gives meaning to S1 "VDC reg #19". Does NOT prove `$35` writes it, when, or with what handshake |
| VCE `$0400`/`$0402`/`$0404` palette ports | S5a | Unrelated to SATB DMA source (sprite colors live in table RAM; MUST NOT be conflated with `$35` target) |
| MPR0 `$FF` + `SEI` + absolute + restore; AR left; max-push | S5b | Discipline IF I/O ever justified. D2 uses NO window |
| SAT/SATB/DMA sequencing, hidden-sprite encodings, timing | S5c HYPOTHESIS | MUST NOT ground anything until pinned |

### 4.2 Preservation conditions (D2-analogous / future VDC)

- D2-analogous (IF selected): preservation only (A/X/Y/P incl Carry in the verified T=0 domain, MPRs unchanged, SP balanced, AR unchanged, no WRAM/VRAM/VDC/VCE/MMIO writes outside transient stack, no TAM/TMA/TXS/SEI requirement, no shadow, no mask, no refusal, SIMULATED). Rationale: genuine target UNKNOWN (one-liner is not a contract), so ANY copy/write/wait would be guesswork (including "copy `$2214/5` to `#19`", "start SATB DMA", "wait DMA", "hide sprites"). Applicable: T01-T03, T05-T07, T10-T14 (same plan as `$36`); T04/T08/T09 NOT_APPLICABLE.
- Future D1: needs (a) pinned hardware (S5a already) PLUS (b) public BIOS-contract for `$35` target/range/value/flags/shadow/timing (one-liner alone insufficient) PLUS (c) independent oracle (absent) PLUS (d) legal review (pending). Until (b)-(d), D1 BLOCKED. `$34` behavior ("VDC #15 + `$2243`") MUST NOT be copied as `$35` behavior (different slot, no link).

### 4.3 Relationship with `$33->$4A->$23->$25->$24->$36->$35`

- `$33` DSPOFF (DEV) + `$23` MWR DEV + `$25`/`$24`/`$36` B1 no-ops + `$35` halt. Order OBSERVED on one integrated path, NOT genuine dependence.
- `$25`/`$24`/`$36` no-ops have NO side effects by design, so `$35` entry well-formedness (MPR1/SP/IRQ) does NOT depend on their side effects — it depends on caller discipline (satisfied). `$35` halt is unimplemented stub, NOT a consequence of upstream no-ops.
- Upstream display-init debt (`$25`+`$24`+`$36` no-ops, plus `$23` DEV-only MWR) ACCUMULATES and MUST be tracked, never repaired by `$35` guessing.
- VDC/VRAM/SATB responsibility split UNKNOWN. MUST NOT assert `$35` owns any store.

## 5. Sprite-primitive verification plan (self-made data only)

Scope: host + pinned Geargrafx real core (`b49ae82a`, cc65 `71746c8`
per `tools/dependencies.json`; re-checked at implementation time).
All fixtures project-authored HuC6280; no commercial/game/reference bytes.

- Mechanics: PUBLIC IPL boot (2-base builder, commercial FIRST default; on integrated main: `cd_4a`+`cd_25`+`cd_24`+`cd_36`+`cd_35` independent opt-ins, default all `stub`) -> JSR `$E09F` (real table entry) -> preservation -> RTS -> halt. Stub default (`cd_35="stub"`) keeps `$E09F` on fallback and traps X=`$35`.
- Preservation: C=0/C=1 (mutants RED on mismatched class only), A!=P, boundaries; MPRs/SP exact; SP `$FF`/`$06` balanced guest-executed with SP restored, `SP=$00/$05` probe-argument refusal verified (no guest execution, NOT_RUN/OUT_OF_CONTRACT for guest), below-minimum guest execution unrun; MPR1 `$FF` refused BEFORE JSR (never reaches `$35`). Probe refusal is harness discipline, never body refusal; genuine error contract UNKNOWN.
- No-target invariance (D2-analogous): dedicated probe asserts AR unchanged, all 20 VDC regs unchanged, MPRs/IDR/IRR unchanged, WRAM canary + full-buffer equality + per-instruction write history (exactly 6 transient stack bytes). VCE/palette explicitly OUT of `$35` scope (no claim). Any WRAM/VRAM/VDC write mutant RED; write-then-restore RED on history. Same-value-store invisibility documented (value-diff tracer limitation, same as B1).
- Chain (main-tree): synthetic `$33->$4A->$23->$25->$24->$36->$35` through real entries on the integrated tree (the `$02` leg is PUBLIC boot). Pre-merge main-tree chain skipped `$35` (stub then; historical).
- Refusal-path absence: no refusal (T04/T08/T09 NOT_APPLICABLE), routing, 9-byte pin, buffer negatives.

## 6. D1/D2 policy

- D1 functional: BLOCKED. S1 one-liner + S5a hardware locations do NOT constitute a BIOS calling convention. No D1 body, no VDC write, no DMA start/wait, no IRQ change in this task.
- D2 preservation-only scaffold: CONDITIONAL (allowed IFF contract + B1 docs + 9-byte NO-I/O body + `cd_35` opt-in wiring with 32-variant lattice + host + real-core suites + chain + commercial re-obs + review + CI, all GREEN, all SIMULATED, never genuine). Selection requires debt/value comparison (display-init debt grows by one more no-op; diagnostic value = next NEXT after `$35`).

## 7. Open issues

- U1. Genuine `$35` inputs/outputs/flags: UNKNOWN (S1 N/A).
- U2. `$2214`/`$2215` ownership/direction/values/timing: UNKNOWN.
- U3. VDC #19 write value/handshake/timing/completion: UNKNOWN.
- U4. SATB DMA start/wait/sync/async/VBlank/IRQ: UNKNOWN.
- U5. VDC #15 / `$2243` relation: belongs to `$34`, NOT `$35`. No cross-slot inference.
- U6. Generations: 1.0 vs 2.x vs TG bodies differ; no single-behavior claim.
- U7. Relation to `$34` DMAMOD / `$36` SATCLR / `$37` SPRPUT: UNKNOWN (names adjacent, contracts unlinked).
- U8. Next blocker after `$35`: UNKNOWN until ExpE (`$35` exp) commercial re-observation. CUE[1] `$1C` remains BLOCKED_ABI regardless.
- U9. Second-core cross-check: NOT_RUN (Geargrafx-only).

## 8. Acceptance criteria

- (a) Contract doc (this file) + B1-analogous-equivalent design doc merged (or same-PR docs) with S1-S8, §3 UNKNOWN, §4 NO-I/O + non-dependence + debt, §5 plan, §6 D2-conditional, §7 U1-U9.
- (b) `src/ex_sprdma.s` 9-byte preservation skeleton (PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/RTS), BOOT, `ex_sprdma_entry`, NOT genuine/DEV_CALLABLE/SIMULATED, MPR1 `$F8` precondition, no I/O, Carry preserved, latch-exact, stack/NMI notes, ORIGINAL MIT.
- (c) Wiring behind `cd_35="experimental"` (default `stub` byte-identical): `build_experimental_bios_boot.py` (`_redirect_slot35_to_body`, `unified_api_windows_35` lattice with all `_35`-containing supersets up to maximum `_4a_25_24_36_35` — 32 variants on integrated main), `build_ipl_loader_g5_2base.py` (`cd_35` param, incremental labels/sources/links, patch+window checks per combination, manifest `cd_35` + slots + sources, link_labels), normal DEV untouched, 256KiB + `$FFF6` + BOOT-span enforced, unallowed diffs refused.
- (d) Host builder tests + real-core suite (§5 T01-T14 analog) ALL GREEN on pinned core, cold/warm identical every run for the API-entry runs and the write-history tracer (cold+warm asserted), with VDC snapshot probe single-cycle (cold-boot pass; warm VDC NOT_RUN) + write-history probes (VCE/palette explicitly out of scope).
- (e) `make test`, `make check` (EXIT 0), `git diff --check`, RED→GREEN, mutants (target/range/off-by-one/value/WRAM-VRAM-confusion/non-target-write/bad-VDC-select/missing-side-effect/unneeded-MMIO/restore-masking/shadow/save/SP/MPR/flag/RTS/success/wiring/boundary/default-mix-in ALL RED or N/A-with-reason), ALLOW/DENY, default identity, DEV invariance, capacity/boundary, Required CI GREEN, independent review 0/0/0, CodeRabbit with zero unresolved CRITICAL/MAJOR/MEDIUM.
- (f) Commercial (variant table, cold/warm, sanitized): synthetic ExpE `$02->$33->$4A->$23->$25->$24->$36->$35->NEXT` (or next stub halt with slot/classification) on the integrated tree + CUE[1] `$1C` regression check; commercial runs BLOCKED_PERMISSION unless Owner grants explicit opt-in for this task. Commercial success NEVER claimed. G5_OBSERVED stays false unless title evidence (not expected).
- (g) Safety gates unchanged (JSR 0/81, VERIFIED 0/81, DEV_CALLABLE false, release_eligible false, fail-closed, DEV invariance, SIMULATED vs genuine vs synthetic vs real-core vs commercial strictly distinguished). Frame/display: OBSERVED/UNKNOWN/NOT_RUN; non-black/VBlank/frame NEVER promoted to title.

D1 acceptance is NOT defined here (BLOCKED; would need a contract revision with (b)-(d) PLUS public-contract grounds for every target/range/value/flag/write).

## 9. Reuse, safety, reproduction

- Reuse: `$25`/`$24`/`$36` NO-I/O skeleton + wiring + oracles (discipline only); `$23` window discipline as negative reference; `$34` one-liner NEVER reused as `$35` behavior. No third-party code copied (all PENDING/REJECTED stay REFERENCE_ONLY).
- Safety: no `./rom/` access for this document; no commercial bytes; no reference BIOS execution/JSR/copy; no ledger/gate/ROM changes by this document.
- Reproduction: Zeograd/Stum/HuC/Hugo re-checks per §2 (local `/tmp` scratch from this task, never committed/published); Geargrafx pin per `tools/dependencies.json` (`b49ae82a`/`71746c8`); G5 observations per Issue #802 comment `7165514587`.

NOT a compatible System Card BIOS. Geargrafx observations are not hardware proof.
