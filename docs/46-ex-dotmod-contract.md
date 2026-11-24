# $24 EX_DOTMOD contract research (Issue #46, G5 P0 task)

Status: **reference contract UNKNOWN; no independent implementation; no verification.**
**NOT a compatible System Card BIOS. NOT genuine ABI. NOT DEV_CALLABLE.
`release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81 (unchanged by this document).
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, or saves
appear here. `./rom/` was not accessed in any form for this document
(no existence check, listing, or read).

Branch basis: `0a935398` (origin/main at task start, PR #828 merged).
This document changes no ledger, no source, no gate, and no ROM bytes.

## 1. Purpose and scope (目的と調査対象)

G5 commercial-CD boot path (parent #635, integration #802, blocker
measurement #635, experimental $4A wiring #824, experimental $25 B1 #828)
reached the sanitized observation (Issue #802 comment `7119970902`):

`$02 -> $33 -> $4A -> $23 -> $25 -> $24 (unimplemented halt)` (CUE[0], ExpB)

after the $25 B1 preservation scaffold started returning under
`cd_4a="experimental" + cd_25="experimental"`.
The next blocker is slot `$24` (EX_DOTMOD candidate). Genuine `$4A`/`$25`
semantics stay UNKNOWN/BLOCKED (docs/84-4a-contract.md,
docs/47-ex-scrmod-contract.md); the `$23 -> $25 -> $24` advance was seen
only under experimental preservation returns, never as genuine ABI.
CUE[1] stays at `$1E -> $4A -> $02 -> $02 -> $21 -> $1C` (Issue #38,
BLOCKED_ABI, out of scope for implementation in this task).

This document investigates the `$24` (EX_DOTMOD candidate) contract so that
an independent implementation can be scoped with evidence, and so that
what must NOT be implemented can be stated with grounds. It does NOT
complete the specification by guessing. Completion is explicitly out of
scope: the goal is to make "what can be implemented and why the rest cannot"
auditable.

Related: #46 (EX_DOTMOD), #802 (G5 integrated boot path), #635 (commercial
blocker measurement), #824 ($4A EX_MEMOPEN experimental implementation),
#828 ($25 B1 scaffold), #45 ($23 EX_SCRSIZ), #47 ($25 EX_SCRMOD),
#38 ($1C BM_DELETE, BLOCKED). Tracker #3 (inventory),
prerequisites #1 (ROM map), #2, #6 (Geargrafx harness), #8 (provenance).

Required outcome (task): purpose/scope, references/provenance, ABI contract
table, VDC/VCE side effects and preservation conditions, comparison with
`$23`, `$23`/`$24`/`$25` relationship analysis, synthetic
positive/negative/boundary plan (self-made data only), competing
implementation policies (D1/D2/D3), open issues, and acceptance criteria
for the next implementation task.

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

| ID | Source (pinned where applicable) | What it establishes about $24 | Disposition |
|----|----------------------------------|-------------------------------|-------------|
| S1 | Zeograd System Card notes, `https://www.zeograd.com/download/pce_bios.html` (System Card 3.0 scope; page states part of the content is reverse-engineering; re-fetched 2026-11-24 for this survey via curl, fact citation only) | Headings `EX_SCRSIZ, Function #$23`, `EX_DOTMOD, Function #$24`, `EX_SCRMOD, Function #$25` with NO `IN`/`OUT` block (bare headings, unlike `$26` EX_IMODE which documents `reg A` values `$00-$03`). Register table on the same page: `$20F3` = current VDC register selected, `$20F7` = value of VDC reg #5 (Control Register), `$20F8-$20FF` = `al/ah/bl/bh/cl/ch/dl/dh` (BIOS work-RAM slots, NOT eight HuC6280 registers). Display-family one-liners exist only for `$2A-$33` (RCRON/RCROFF/IRQON/IRQOFF/BGON/BGOFF/SPRON/SPROFF/DSPON/DSPOFF), none for `$23/$24/$25`. Local `/tmp/pce_bios.html` 19170 bytes, L601-603 bare headings confirmed | REFERENCE_ONLY, PENDING. Name + slot labels only; zero call contract. The absence of an `IN`/`OUT` block is recorded as absence, not as proof of no inputs |
| S2 | Stum generation table, `https://www.stum.de/2025/pcenginebiosoffsets/` (2025-07-28; CC BY-NC-SA 4.0 display, fact citation only; re-checked 2026-11-24 via curl, no table reproduced) | Row `ex_dotmod`: JSR `$E06C`; per-generation bodies `1.0=$E307`, `2.0/2.1/3.0=$E272`, `TG2.0/TG3.0=$E28B` (addresses cited as facts only, never used as implementation or entry addresses). Header is Method / JSR / 1.0 / 2.0 / 2.1 / 3.0 / TG2.0 / TG3.0. The JSR column is the stable jump-table entry; body addresses move by version. General BIOS discipline on the same page: startup mapping assumptions (MPR#7=`$00`, MPR#0=`$FF`, MPR#1=`$F8` assumed/required; JSR-via-table discipline) | REFERENCE_ONLY, PENDING. Mapping + per-generation body grouping only; no inputs/outputs/side effects. Non-commercial condition means no adoption path via this source |
| S3 | pce-devel/huc `include/hucc/pcengine.inc` @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` (caller-side header; BSL-1.0 L8; pinned hash `b6a25d33bb787ea1fe550c6e04aff9f8ccaaa91b649a05a963be24b440b728a9` recorded in `docs/reuse/external-verification-log-p7.md:55`, re-verified 2026-11-24 via curl: local 18844 bytes, SHA-256 matches, L507 `ex_dotmod = $00:E06C, FUNC` inside the CDROM conditional block L464-552) | Caller-side equate `ex_dotmod = $00:E06C, FUNC`; same block holds the `$23-$25` family. No wrapper macro for dotmod in `include/hucc/hucc-systemcard.asm` @ same pin (local 20230 bytes, SHA-256 `4258b7c3…` matches prior record, grep for dotmod/SCRSIZ/SCR_MOD yields zero hits): the C-callable wrappers cover BURAM etc., not the display family | REFERENCE_ONLY, PENDING. Name-to-entry equate only; no callee behavior. Nothing copied. The equate proves the SDK spells the name, not what the BIOS does. The absence of a wrapper is recorded as absence, not as proof of no convention |
| S4 | mckayemu/hugo `bios.c` @ `64ed226da2288738781743eeef228c069e8973a4` (HLE emulator source; GPL-2.0 per `COPYING`; PENDING; REFERENCE_ONLY candidate per `docs/reuse-inventory.md`; pinned hash `db3f227e09a73457bc9b0f2c54a3922ab9dca1e301f1b5029690c0d210972f22` recorded in same verification log, re-verified 2026-11-24 via curl: local 29130 bytes, SHA-256 matches) | HLE selector-name defines L79-81 (`EX_SCRSIZ 0x23`, `EX_DOTMOD 0x24`, `EX_SCRMOD 0x25`), debug-name cases L228-233 (`case EX_DOTMOD: return "EX_DOTMOD"`), and NO `case EX_DOTMOD` in `handle_bios()` (falls through to `default:` L1091-1095 which restores the overwritten bytes): selector + name exist, behavior absent | REFERENCE_ONLY, PENDING. Name only; explicitly not entry proof; nothing copied. An HLE selector is not proof of jump-table entry or behavior. REJECTED for ROM inclusion (GPL-2.0 + HLE-not-proof per `docs/cd-reuse-decision-309.md`) |
| S5a | Pinned Geargrafx core `drhelius/Geargrafx` @ `b49ae82a012eade566d72e9d61c9d95a5caa4da3` (per `tools/dependencies.json`; GPL-3.0, test-execution only, never linked into ROM) — `src/huc6260.h` + `skills/geargrafx-romhacking/references/huc6260_vce.md` (VCE `$0400` Control Register bits 1-0 = color/dot clock select 00=~5MHz, 01=~7MHz, 10=~10MHz, 11=Reserved; write-only; `$0402` CTA, `$0404` CTW/CTR) + `src/huc6270.h` + `skills/geargrafx-romhacking/references/huc6270_vdc.md` (VDC ports `ST0=$0000` AR, `ST1=$0002`/`ST2=$0003` DR low/high; 20 internal regs R00-R13; R05 CR bits 6-7 SB/BB, R09 MWR bits 0-7 VM/SM/SCREEN/CM, R0A HSR HSW/HDS, R0B HDR HDW/HDE) | VCE dot-clock select location/encoding + VDC register map AS IMPLEMENTED by the pinned core. Pin: `tools/dependencies.json` geargrafx entry (repo URL, revision, sha256); file names as cited; exact line numbers are NOT pinned here and must be re-checked against the pinned tarball before any build use | REFERENCE_ONLY (test dependency), PENDING for license purposes. Pinned-core hardware facts only — NOT NEC-manual facts, NOT `$24` BIOS facts. No code copied. Hardware location/encoding MUST NOT be mistaken for the BIOS calling convention |
| S5b | In-tree measured I/O practice (project-own, MIT): `src/overrides/slot_23.s:30-35` (`lda #$09` / `sta a:$0000` select R09; `sta a:$0002` low; `stz a:$0003` high-explicit-`$00`), `src/ex_dspoff.s:18-20`, harnesses `tests/integration/geargrafx_dev_scrsiz.py`, `tests/integration/geargrafx_dev_cr.py` (real-core asserts on pinned Geargrafx) | VDC ports `ST0=$0000` (AR select), `ST1=$0002` (data low), `ST2=$0003` (data high) AS USED by the DEV family; MPR0=`$FF` + `SEI` + absolute `a:` form + MPR0 restore; AR left at 9 (MWR family) after ok path; max-push accounting (`$23` 5) | PROJECT_SPEC_ONLY. Ports behave as stated ON THE PINNED CORE per passing real-core suites; project-measured platform fact, not a NEC-manual fact, not a `$24` contract. Engineering discipline only. VCE `$0400` has NO in-tree DEV precedent (no DEV/BIOS body touches VCE in this repo) |
| S5c | NEC HuC6270 / HuC6260 manuals (claimed public hardware facts; UNPINNED in this document — no title/edition/section/page/URL recorded in-tree; not re-fetched here) | Claimed: VCE CR bits 1-0 dot-clock select; VDC MWR mask/behavior; AR latch; CR effective timing. In-tree comments repeating register names are project repetitions, not independent confirmations | HYPOTHESIS (hardware). Downgraded: reconstructible citation is missing, so these claims MUST NOT ground implementation until pinned (title, edition, chapter/section/page or URL + access date, provenance-reviewed). S5a pinned-core facts are the ONLY hardware-grounded basis for synthetic oracles in this task |
| S5d | Third-party practice notes (UNPINNED): HuDK `vdc_crl`/`vdc_crh` RAM-copy practice; vdcdox notes — no URL/edition/location recorded in-tree; not re-fetched here | Claimed practice: keep RAM copies instead of port RMW | HYPOTHESIS (practice). Downgraded: no auditable pin. At most a consistency hint; never a hardware proof, never a `$24` contract |
| S6 | In-tree DEV precedents: `src/overrides/slot_23.s` (EX_SCRSIZ DEV, Issue #45: MWR low from A, AR=9, A/X/Y preserved via PHP/PLP with NO CLC/SEC, MPR0 window, no work-RAM writes) + `src/overrides/slot_2E.s` (EX_BGON DEV, Issue #56) + `src/ex_dspoff.s` + `src/ex_memopen.s` (Issue #84 preservation-only) + `src/ex_scrmod.s` (Issue #47 B1 preservation-only, NO VDC/MMIO, AR unchanged) + harnesses `tests/integration/geargrafx_dev_scrsiz.py`, `tests/integration/geargrafx_dev_cr.py`, `tests/host/test_api_scrsiz_dev.py` | Project-own DEV contracts only (see section 5). Reusable as engineering discipline (stack-depth accounting, no-direct-port-RMW shadow practice, MPR0/`SEI`/absolute-form window pattern where VDC is touched, RAM-diff scoping), NOT as reference evidence. The C=0-ok / C=1-reject convention belongs to the `$2E`/CR-family bodies and explicitly documented B2-style conventions only — it is NOT a property of `$23` (preserves Carry) and MUST NOT be assumed for `$24` (Carry-preservation required unless a public contract proves otherwise) | PROJECT_SPEC_ONLY for their own slots. Reuse as template is allowed; reuse as `$24` ABI proof is forbidden |
| S7 | Sanitized G5 observations already on GitHub (Owner-permitted, read-only, structural only; this document adds no new measurement) | (a) Standard G5 image: CUE[0] `$02->$33->$4A` stub halt; CUE[1] `$1E->$4A` stub halt. (b) SIMULATED `$4A` diagnostic: CUE[0] `$23->$25`, CUE[1] `$02->$02->$21->$1C`. (c) `$4A` experimental preservation body: CUE[0] `$02->$33->$4A->$23->$25` halt; CUE[1] `$1E->$4A->$02->$02->$21->$1C` halt. (d) `$25` B1 scaffold + commercial re-observation (Issue #802 comment `7119970902`, HEAD `0a935398`): CUE[0] ExpB `$02->$33->$4A->$23->$25->$24` halt (was `$25` stub halt); CUE[1] ExpB unchanged `$1E->$4A->$02->$02->$21->$1C` halt. `$24` entry observed ONLY as table JSR (`$E06C`, delta 2, in_table, MPR1=`$F8`, SP balanced, stack MATCH, cold/warm identical). Genuine `$4A`/`$23`/`$25` semantics stay UNKNOWN/BLOCKED; SIMULATED returns are never genuine | OBSERVED (call-state interface only: API order, stub-halt slot, cold/warm match). No memory/sector/content data. Genuine `$24` semantics stay UNKNOWN/BLOCKED; experimental returns upstream are never genuine |
| S8 | Project ledgers: `spec/api_slots.json` (id 36 = `EX_DOTMOD`), `spec/api_contracts.json` (slot 36: `candidate_name EX_DOTMOD`, `candidate_entry 0xE06C`, `mapping_status HYPOTHESIS`, all 11 behavioral fields UNKNOWN), `spec/dev_implementations.json` (id 36: `state NOT_READY`, `abi_contract UNKNOWN`, `implementation null`), `tools/build_rom.py` (81-slot JSR table at `$E000+3*slot`, stub `LDX #slot; JMP api_unimplemented`) | Current ledger state: mapping is hypothesis, behavior is zero, no DEV override for `$24`, table mechanics verified by `make dev-test` layout checks | Project record, unchanged by this document. The table mechanics (`$E06C = $E000+3*$24`) are project-verified layout facts, not reference-ABI proof |

License/provenance gate: S1-S4 are PENDING and stay REFERENCE_ONLY.
S5a is a pinned test dependency (REFERENCE_ONLY, PENDING for license);
S5b is project-own MIT code/practice (PROJECT_SPEC_ONLY);
S5c/S5d are HYPOTHESIS until pinned with reconstructible citations.
No file or fragment from any of them enters the firmware. Any future `$24`
body must be ORIGINAL project-own MIT code with S5a-disciplined I/O (if any
I/O is ever justified) or preservation-only with NO I/O (B1-analogous).

## 3. ABI contract table (ABI契約表)

| # | Item | Genuine `$24` | DEV `$24` | Disposition |
|---|------|---------------|-----------|-------------|
| 1 | Name/slot/entry | `EX_DOTMOD` / `$24` / `$E06C` candidate | none (no override; stub halts) | S1/S2/S3/S4 name+entry HYPOTHESIS as callee; S8 ledger HYPOTHESIS. CONFIRMED-by-source ONLY as caller-side equate (S3 L507). Entry `$E06C = $E000+3*$24` is project-verified layout, not ABI proof |
| 2 | Generations | Bodies differ per Stum (1.0 `$E307`, 2.0/2.1/3.0 `$E272`, TG `$E28B`); JSR column stable `$E06C` | N/A | S2 grouping only. Single-behavior assumption FORBIDDEN |
| 3-5 | Inputs: A/X/Y registers | UNKNOWN (genuine); no DEV contract exists | none | S1 has no `IN` block (absence recorded, not evidence). S2/S3/S4 name no registers. S7 commercial probe is observation-only by design (entry regs for `$24` were recorded locally in private evidence for Issue #802 ExpB but are NOT published here and do NOT ground a contract: single-game single-callsite values cannot define an ABI). Caller work-RAM `$20F8-$20FF` (`al..dh`) are caller variables (S1 register table), not callee inputs |
| 6 | Input: BIOS work-RAM (`$20F3`, `$20F7`, `$2214/$2215`, `$2227-$2232`, `$2243`) | UNKNOWN | none | S1 lists addresses without direction/semantics for `$23/$24/$25`. Reads/writes by callers do not prove callee contract. Shadow ownership for `$24` UNKNOWN |
| 7 | MPR precondition | UNKNOWN (genuine); OBSERVED `$F8` on the single commercial path is NOT a contract | DEV discipline (if ever): MPR1=`$F8` caller precondition (same as every DEV shadow design) | S7: every observed `$24` APICALL carries MPR1=`$F8`, but one game cannot define the ABI. The harness validates MPR1 BEFORE JSR in synthetic tests (contract T13-analog); the body defines NO in-body gate (B1-analogous has no gate by design) |
| 8 | Stack/return | UNKNOWN (genuine) | B1-analogous (if scaffold): JSR `$E06C` tail-JMP, RTS to caller+3, SP balanced (JSR 2 + max 4 transient, caller SP minimum `$06`) | Table JSR mechanics project-verified (S8). Return semantics (RTS vs JMP, caller+2 vs +3) for genuine UNKNOWN. B1-analogous return is a scaffold choice, never genuine evidence |
| 9 | Preserved vs clobbered (A/X/Y/P/MPR) | UNKNOWN | B1-analogous: A/X/Y exact; P exact for the verified domain (T=0) incl Carry exactly, MPRs unchanged, AR unchanged (T=1 entry is OUT OF CONTRACT per §4.2/U6; no T-bit operation added) | S6: `$23` preserves A/X/Y + Carry (PHP/PLP, no CLC/SEC); `$25` B1 preserves all incl Carry (T=0 domain). Genuine `$24` preservation UNKNOWN. Any future body must state its preservation as SCAFFOLD contract, never genuine |
| 10 | VDC side effects | UNKNOWN | B1-analogous: NONE (no ST0/ST1/ST2, no VCE/PSG/timer/CD access) | S5a gives hardware locations; S5b gives DEV I/O discipline IF I/O is ever justified. B1-analogous performs zero I/O, so VDC/VCE/shadow/mask questions are NOT_APPLICABLE to the scaffold (requiring them would invent code paths). Genuine VDC/VCE target UNKNOWN: VCE `$0400` clock-select is a HYPOTHESIS (name suggests dot clock), never a confirmed callee target |
| 11 | VCE side effects | UNKNOWN | B1-analogous: NONE | Same as #10. No in-tree DEV body touches VCE `$0400`; any future VCE write needs a pinned hardware citation (S5a) PLUS a public BIOS-contract source linking `$24` to VCE — neither exists today |
| 12 | Shadow/AR/VCE-state | UNKNOWN | B1-analogous: AR unchanged, no shadow, no state | S1 `$20F3`/`$20F7` are caller-visible variables, not proven `$24` shadow. B1-analogous owns no state |
| 13 | Flags (Carry/Z/N/V) | UNKNOWN | B1-analogous: Carry preserved exactly (`C_in == C_out` both seeds; no CLC/SEC), other P bits latch-exact except T (pinned-core T-consumption, same as B1 §T-domain) | S6: Carry-reject conventions belong to CR-family/B2 only, never assumed for `$24`. Single-game `$24` entry P observed locally but NOT published and NOT contract |
| 14 | IRQ/VBlank/timing | UNKNOWN | B1-analogous: NMI/IRQ assumed absent across the short window (same as siblings); no wait, no enable/disable | S7: observed APICALLs carry IDR/IRR unchanged, but absence on one path is not a contract. Timing (VBlank wait, next-line effective) UNKNOWN |
| 15 | Errors/rejects | UNKNOWN | B1-analogous: NO refusal path (no ok/reject values, no invented code) | No public source defines `$24` error codes. A future I/O body MIGHT define fail-closed rejects, but only with public-contract grounds; the scaffold defines none |
| 16 | Call-order dependence (`$23->$25->$24`) | UNKNOWN | OBSERVED order on CUE[0] ExpB only; NOT a contract | S7 order is one game under experimental returns. Genuine ordering constraints UNKNOWN. See §5 for the `$23`/`$25`/`$24` analysis (order does not prove dependence) |

Verdict: genuine `$24` contract is UNKNOWN in every behavioral field.
D1 functional implementation is BLOCKED. D2 preservation-only scaffold
(B1-analogous, NO I/O) is the ONLY implementable scope without guessing,
and ONLY for pathfinding (SIMULATED), never as genuine progress.

## 4. VDC/VCE side effects and preservation conditions (VDC/VCE副作用と保存条件)

### 4.1 Hardware facts (claim-to-source mapping; NOT BIOS behavior)

| Claim | Source | Use in this task |
|-------|--------|------------------|
| VCE `$0400` bits 1-0 = dot/color clock 00=~5MHz, 01=~7MHz, 10=~10MHz, 11=Reserved; write-only; `$0402` CTA, `$0404` CTW/CTR | S5a (pinned Geargrafx `huc6260.h` + `huc6260_vce.md`) | Hardware location/encoding IF a future D1 body ever writes VCE. Does NOT prove `$24` writes VCE. Any future VCE write must ALSO cite a public BIOS-contract source linking `$24` to VCE — absent today, so NO VCE write is justified in this task |
| VDC `ST0=$0000` AR, `ST1=$0002`/`ST2=$0003` DR low/high; R00 MAWR, R01 MARR, R02 VRR/VWR, R05 CR (SB/BB bits 6-7), R09 MWR (VM/SM/SCREEN/CM), R0A HSR (HSW/HDS), R0B HDR (HDW/HDE), R0C-R0E VSR/VDR/VCR, R0F-R13 DMA | S5a (pinned Geargrafx `huc6270.h` + `huc6270_vdc.md`) | Hardware map IF a future D1 body ever writes VDC. Does NOT prove `$24` writes any VDC register. `$23` DEV precedent writes MWR low from A (S5b); `$24` writing HSR/HDR/MWR is HYPOTHESIS (name-adjacent), never confirmed |
| MPR0=`$FF` + `SEI` + absolute `a:` + MPR0 restore window; AR left at selected index; max-push accounting | S5b (in-tree DEV practice, real-core proved) | Engineering discipline IF I/O is ever justified. B1-analogous uses NO window (no I/O), so this subsection is NOT_APPLICABLE to the scaffold |
| CR R05 high bits / stale-buffer RMW unreliability / T-flag consumption | S5a + B1 §T-domain | Same constraints as B1 apply to any future I/O body; the scaffold avoids them by doing no I/O |

### 4.2 Preservation conditions, split by variant (B1-analogous / future VDC)

- B1-analogous (D2 scaffold, this task IF implemented): entry-state preservation only (A/X/Y exact; P exact for the verified domain T=0 incl Carry exactly, T=1 entry OUT OF CONTRACT per U6; PHP/PLP save contract is distinct from HuC6280 instruction-boundary T-consumption; no T-bit operation added; MPRs unchanged, SP balanced with stacked bytes NOT scrubbed -- SP restore and stack-memory restore are distinct, AR unchanged, no work-RAM writes outside the transient caller-stack bytes (caller-side JSR 2 + callee 4 transient = 6 total), no VDC/VCE/MMIO writes of any kind, no TAM/TMA-write/TXS/SEI requirement since no I/O to protect, no shadow, no mask, no refusal path, result class SIMULATED). Rationale: genuine semantics UNKNOWN, so any invented service/VDC write would be guesswork; preservation is load-bearing (mutants caught) and safe (synthetically verified). Applicable tests: T01-T03, T05-T07-preservation, T10-T14 per §6.3; T04/T08/T09 NOT_APPLICABLE (no I/O/shadow/mask).
- Future VDC/VCE body (D1, NOT this task): would require (a) pinned hardware citation (S5a already) PLUS (b) public BIOS-contract source for `$24` inputs/outputs/flags/shadow/timing (absent), PLUS (c) independent validation model for oracles (absent), PLUS (d) provenance/legal review for any reused code (pending). Until (b)-(d) exist, D1 is BLOCKED. The `$23` DEV body (MWR low from A) MUST NOT be copied as `$24` behavior (different slot, different hypothesized target, no contract linking them).

## 5. Comparison with `$23` EX_SCRSIZ and `$23`/`$24`/`$25` relationship (`$23`との比較・三者関係)

| Aspect | `$23` (DEV, Issue #45) | `$25` (B1 scaffold, Issue #47) | `$24` (this issue) |
|--------|------------------------|--------------------------------|--------------------|
| In-tree behavior | REAL VDC I/O: MWR (`$09`) low from A via MPR0 window, AR=9, A/X/Y preserved, Carry preserved (no CLC/SEC), no work-RAM writes | SIMULATED preservation-only: NO I/O, AR unchanged, A/X/Y/P incl Carry preserved (T=0 verified domain), MPRs unchanged | UNKNOWN genuine; B1-analogous scaffold (if implemented) = NO I/O, same preservation as `$25` B1 for the verified T=0 domain (Carry exact; T=1 OUT OF CONTRACT; no work-RAM writes outside caller-side JSR 2 + callee 4 transient; SP balanced, stacked bytes not scrubbed) |
| Observed CUE[0] ExpB entry (private, NOT contract) | A=`$00`,X=`$00`,Y=`$00` (DEV accepted and returned; genuine acceptance UNKNOWN) | A=`$00`,X=`$20`,Y=`$20` (B1 preserved and returned) | A=`$00`,X=`$20`,Y=`$20` (same context as `$25`; single-callsite coincidence, NOT ABI) |
| Dependence | `$23` return observed BEFORE `$25`; `$25` return observed BEFORE `$24`. Order OBSERVED on one game under experimental returns. Genuine ordering UNKNOWN. `$25` B1 has NO side effects by design, so `$24` entry well-formedness (MPR1/SP/IRQ) does NOT depend on `$25` side effects — it depends on caller discipline (satisfied). `$24` halt is unimplemented stub, NOT a consequence of `$25` no-op. Conversely `$24` functional init would NOT repair `$25` no-op debt; display-init debt ACCUMULATES across B1-analogous scaffolds and must be tracked as debt, not progress |
| Shadow | `$23` owns no shadow; `$25` B1 owns no shadow | none | UNKNOWN genuine; scaffold owns none |
| VDC/VCE split (hypothesis only) | VDC MWR (DEV fact, not genuine proof) | none (scaffold) | HYPOTHESIS: VCE `$0400` (name suggests dot clock) and/or VDC HSR/HDR/MWR; UNCONFIRMED. VDC vs VCE split MUST NOT be asserted as `$24` contract |
| Reuse | Window discipline reusable IF `$24` ever does VDC I/O | Preservation skeleton reusable (PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/RTS, 9 bytes, SP/MPR/AR/VDC-invariance oracles) | Copy the SKELETON, never the SEMANTICS: `$23` MWR-write MUST NOT be copied; `$25` no-op MUST NOT be cited as `$24` evidence |

Bottom line for Phase B: `$24` is independently analyzable. Its stub halt is
NOT caused by `$25` missing side effects. A B1-analogous scaffold for `$24`
is technically coherent (same safety, same wiring, same oracles) and would
advance the DIAGNOSTIC path past `$24` to the next blocker, but it would NOT
reduce display-init debt and MUST be labeled SIMULATED, never FUNCTIONAL.

## 6. Synthetic test plan, self-made data only (synthetic正例・負例・境界例)

Scope: host + pinned Geargrafx real core (`b49ae82a`, cc65 `71746c8`
per `tools/dependencies.json`; re-checked at implementation time).
All fixtures are project-authored HuC6280; no commercial/game/reference bytes.

### 6.1 What is tested at each stage

- Mechanics: PUBLIC IPL boot (2-base builder, commercial FIRST default,
  `cd_4a`+`cd_25`+`cd_24` experimental) -> JSR `$E06C` (real table entry,
  never direct-body JSR, never fake RTS) -> full preservation -> RTS
  resumption -> payload halt. Stub default (`cd_24="stub"`) keeps `$E06C`
  on the linked fallback (byte-equal to normal DEV ROM) and traps with
  X=`$24`.
- Preservation: entry-C=0 and C=1 (forced-CLC mutant must RED on C=1 class
  as M7a-C1-preservation; forced-SEC mutant must RED on C=0 class;
  no fabricated flag). Nonzero A/X/Y incl A!=P vectors (clobber mutant RED).
- VDC invariance + VCE no-write by construction (B1-analogous): dedicated
  VDC-only probe (cold then warm, ResetMedia between, explicit cycle tags,
  per-cycle baselines, 16 start-and-end halt confirmation, fail-closed
  record schema) asserts AR unchanged
  for several entry AR values, all 20
  VDC regs unchanged, MPRs/IDR/IRR unchanged per cycle. VCE `$0400` is write-only on
  the pinned core so no dynamic VCE-state oracle exists; VCE invariance
  holds by construction (the 9-byte body contains no store opcode, hence
  no VCE write is possible). Any VDC write mutant RED; VCE-write mutants
  are N/A by construction (no store to mutate toward VCE).
- Work-RAM scope: 256-byte canary + full-buffer cold/warm equality +
  per-instruction value-change history (tracer records changed offsets at
  instruction boundaries, not write events: a same-value store is invisible;
  dynamic result is changed offsets only, never a full write-event proof; the
  static 9-byte pin -- no store opcode beyond the 4 pushes -- is the stronger
  guarantee, separated from the dynamic observation): scaffold writes exactly
  the 6 transient stack bytes (caller-side JSR 2 + callee 4) and nothing else;
  write-then-restore mutant RED
  on history even when final values match (history-vs-final distinction, not
  same-value detection). SP is restored/balanced; stacked bytes are NOT
  scrubbed.
- MPR1 precondition validated by harness BEFORE JSR (T13-analog): a `$FF`
  candidate refuses at refused_halt without executing JSR, never reaches
  `$24`, never remaps first (strict validator: raw RUN list, truncations
  rejected, pc == refused_halt symbol, probe-emitted per-cycle observed
  count matched against parsed count, every APICALL record schema-checked).
- SP depth: SP=`$FF` and boundary `$06` GREEN and balanced;
  below-minimum (`$00`, `$05`) refused pre-execution by the writes probe
  (contract refusal, exit 2, no RESULT), out of contract and never run.
- Chain: synthetic `$33->$4A->$23->$25->$24` through real table entries
  with per-call APICALL entry evidence (stacked-return bytes at entry time,
  not RTS-landing proof; APIRETURN is not collected; per-call return is NOT
  claimed from APICALL alone -- end-to-end preservation + halt is the return
  evidence; the $24 9-byte pin excludes a trampoline for the $24 call only,
  never generalized to the companion legs) (the `$02` leg is the PUBLIC boot itself).
- Refusal-path absence: B1-analogous defines no refusal (T04/T08/T09
  NOT_APPLICABLE), routing, 9-byte structural pin, buffer negatives.

### 6.2 Case table (IDs T01-T14, B1-analogous; T = test, all synthetic)

| ID | Case | Expectation (scaffold) |
|----|------|------------------------|
| T01 | Stub default (`cd_24="stub"`) | `$E06C` on fallback, JSR traps `api_unimplemented` X=`$24`, cold/warm identical |
| T02 | Mechanics (`cd_24="experimental"`) | JSR `$E06C`, in_table, sp_delta 2, MATCH, return_matches true, SP balanced, MPR1=`$F8`, cold/warm identical |
| T03 | Preservation C=0/C=1 + A!=P + boundaries | Exact A/X/Y/P/MPR/SP; M7a/M7b RED on the mismatched Carry class only |
| T04 | I/O window | NOT_APPLICABLE (scaffold has no I/O; requiring a window would invent a path) |
| T05 | RAM scope (canary + full-buffer + value-change history) | Exactly 6 transient stack bytes (JSR 2 caller-side + callee 4), nothing else; restore-mutant RED on history (history-vs-final, not same-value); same-value stores invisible (changed offsets only); static 9-byte pin is the stronger guarantee, separated from the dynamic observation |
| T06 | Bad table reroute | RED (stub halt, arrival-proved) |
| T07 | Carry-preservation oracle (balanced cavity, channel b) | Forced-CLC/SEC mutants RED on the mismatched class, GREEN on the matched class (proves no fabrication, not a fixed value) |
| T08 | Shadow bypass | NOT_APPLICABLE (no shadow owned; requiring bypass would invent state) |
| T09 | Mask mutants | NOT_APPLICABLE (no writable mask owned) |
| T10 | Routing/9-byte pin (`PHP PHA PHX PHY PLY PLX PLA PLP RTS`) | Exact 9 bytes pinned; any save/restore drop RED |
| T11 | Buffer negatives (body/table mutants) | All RED with per-mutant failure modes |
| T12 | Oracle strictness (cold-duplicate, count, reason, budget, halt, PC, truncation) | All negatives rejected (fail-closed validator) |
| T13 | MPR1 precondition (`$FF` refused before JSR) | Refused_halt, never reaches `$24`, never remaps |
| T14 | SP boundaries (`$FF`, `$06`, below-minimum refused) | GREEN + balanced for `$FF`/`$06`; below-minimum (`$00`, `$05`) refused pre-execution (exit 2, no RESULT), never executed |

### 6.3 Applicability matrix (B1-analogous)

T01-T03, T05-T07-preservation, T10-T14 apply (GREEN required).
T04/T08/T09 NOT_APPLICABLE with variant-grounded reason (no I/O/shadow/mask;
requiring them would invent code paths). Any future D1 body would flip
T04/T08/T09 to APPLICABLE with real I/O/shadow/mask oracles.

## 7. Implementation policy comparison (実装方針の比較)

| Policy | What it does | Safety/cost | Verdict for `$24` |
|--------|--------------|-------------|-------------------|
| D1 functional (genuine or limited) | Independent HuC6280 body with real VDC/VCE ops + shadow + validation + fail-closed, grounded in public contract + pinned hardware + independent oracle | Needs (b)-(d) above; none exist. Any VCE `$0400` write today would be name-based guessing (HYPOTHESIS presented as fact) | BLOCKED. No public source defines `$24` inputs/outputs/flags/shadow/timing. Single-game entry values (A=`$00`,X=Y=`$20` locally observed, NOT published as contract) cannot define an ABI. Do NOT implement |
| D2 B1-analogous scaffold (SIMULATED, opt-in) | Route `$E06C` to a returning body ONLY in experimental images (BOOT-region, `cd_24="experimental"`; normal DEV ROM byte-identical). Body: save/restore A/X/Y/P (verified domain T=0; T=1 OUT OF CONTRACT; no T-bit operation), RTS, NO VDC/VCE/MMIO writes, AR unchanged, MPRs unchanged, no work-RAM writes outside the transient caller-stack bytes (JSR 2 caller-side + callee 4; SP balanced, stacked bytes not scrubbed), Carry preserved, no success code. Result class SIMULATED_NEXT_API, never genuine, never DEV_CALLABLE | No VDC/RAM risk beyond transient stack; BOOT-region placement avoids OVERRIDES ceiling. Risk: mistaken for implementation; display-init debt accumulates. Mitigations: manifest `system_card_abi: NO_CLAIM`, `release_eligible: false`, separate flag, default `stub`, every observation labeled SIMULATED | CONDITIONAL, pathfinding only. Owner permission already granted for commercial opt-in in this task; default `stub`; normal ROM untouched; never DEV_CALLABLE; never cited as `$24` semantics. May be selected ONLY under §8/§9 acceptance (synthetic GREEN + commercial next-blocker observation) |
| D3 BLOCKED (research only) | Record contract UNKNOWN, missing info, verification method, next work | Zero ROM/code risk; zero boot progress in this task | FALLBACK if D2 safety/green cannot be met, or if review/CI cannot converge. Prefer D2 ONLY if §9 acceptance is fully met; otherwise D3 with concrete next steps |

The `$4A` precedent (#824) and `$25` B1 precedent (#828) do NOT transfer to
`$24` as justification for VDC/VCE writes — they transfer ONLY as
preservation-skeleton + wiring + oracle discipline for a NO-I/O scaffold.
Any `$24` VDC/VCE write needs its OWN public-contract ground, which does not
exist.

## 8. Open issues (未解決事項)

- U1. Genuine `$24` inputs/outputs/flags/shadow/timing: UNKNOWN (all fields §3). No public IN/OUT (S1 bare heading). Single-game values are NOT ABI.
- U2. VCE `$0400` linkage: HYPOTHESIS (name suggests dot clock; S5a gives hardware encoding). No BIOS-contract source links `$24` to VCE. VDC HSR/HDR/MWR linkage likewise HYPOTHESIS.
- U3. Generation differences: S2 body addresses differ (1.0 vs 2.x vs TG). Single-behavior assumption forbidden; commercial CUEs' generation UNKNOWN (and must stay UNKNOWN in public).
- U4. `$23`/`$25`/`$24` genuine dependence: UNKNOWN. Observed order is NOT dependence (§5).
- U5. Display-init debt: `$25` B1 + `$24` B1-analogous (if implemented) = two consecutive no-ops. Real screen init (whatever genuine `$25`/`$24` do) remains entirely undone. Debt must be tracked, never presented as progress toward display correctness.
- U6. T-flag domain: pinned-core T-consumption (same as B1) excludes T=1 entry from the verified preservation domain; document in scaffold spec if implemented.
- U7. Relation to `$26` IMODE / `$27` VMODE / `$28` HMODE: UNKNOWN (Zeograd documents `$26` A-values but nothing links them to `$24`).
- U8. Next blocker after `$24`: UNKNOWN until ExpC commercial re-observation (Phase G). CUE[1] `$1C` remains BLOCKED_ABI regardless.

## 9. Acceptance criteria for the next implementation task (次の実装タスクの受入条件)

D2 scaffold (IF selected) is accepted ONLY if ALL hold:

- (a) Contract doc (this file) + B1-analogous design doc merged (or same-PR docs) with S1-S8 provenance, §3 UNKNOWN verdict, §4 NO-I/O discipline, §5 non-dependence analysis, §6 T01-T14 plan, §7 D2-conditional verdict, §8 U1-U8.
- (b) `src/ex_dotmod.s` 9-byte preservation skeleton (PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/RTS), BOOT segment, `ex_dotmod_entry` export, header stating NOT genuine, NOT DEV_CALLABLE, SIMULATED, MPR1=`$F8` caller precondition, no I/O, Carry preserved, latch-exact note, stack/NMI notes, ORIGINAL MIT provenance.
- (c) Wiring behind `cd_24="experimental"` (default `stub` byte-identical): `build_experimental_bios_boot.py` (`_redirect_slot24_to_body`, `unified_api_windows_24[_4a][_25][_4a_25]`), `build_ipl_loader_g5_2base.py` (`cd_24` param, incremental labels/sources/links, patch+window checks per combination, manifest `cd_24` + slots + sources, link_labels `ex_dotmod_entry`), normal DEV ROM untouched, 256KiB + `$FFF6` + BOOT-span (`entry+9 <= $FFF6`) enforced, unallowed diffs refused.
- (d) Host builder tests (stub default, experimental opt-in, fail-closed guards, 9-byte pin, ROM identity/routing, mutant RED) + real-core suite (§6 T01-T14, VDC-only probe (VCE by construction) + write-history probe, chain `$33->$4A->$23->$25->$24`, cold/warm identical every run) ALL GREEN on pinned core.
- (e) `make test`, `make check` (EXIT 0), `git diff --check`, RED→GREEN, mutants (wrong-input/flag/save/MPR/SP/VDC-write/shadow-missing/side-effect-missing/RTS/success/wiring/boundary/default-mix-in all RED or N/A-with-reason), ALLOW/DENY regression, default-variant identity, normal DEV ROM invariance, ROM capacity/boundary, Required CI all GREEN, independent review CRITICAL/MAJOR/MEDIUM=0, CodeRabbit `@coderabbitai review` with zero unresolved.
- (f) Commercial ExpC re-observation (Phase G variant table, cold/warm, sanitized only) showing `$24` experimental return → next API reached (or next stub halt with slot+classification), plus CUE[1] `$1C` regression check. G5_OBSERVED stays false unless title evidence exists (not expected).
- (g) Safety gates unchanged (reference JSR 0/81, VERIFIED 0/81, DEV_CALLABLE false, release_eligible false, fail-closed, DEV invariance, SIMULATED vs genuine vs synthetic vs real-core vs commercial strictly distinguished).

D1 acceptance is NOT defined here (BLOCKED; would need a new contract revision with (b)-(d) above PLUS public-contract grounds for every I/O/shadow/flag/value).

## 10. Reuse, safety, and reproduction

- Reuse: `$25` B1 skeleton + wiring + oracles (copy discipline, never semantics); `$4A` MPR1/SP/cold-warm patterns; `$23` VDC-window discipline ONLY as negative reference (what NOT to do without grounds). No third-party code copied (all PENDING/REJECTED sources stay REFERENCE_ONLY).
- Safety: no `./rom/` access for this document; no commercial bytes; no reference BIOS execution/JSR/copy; no manifest/ledger/gate changes; no ROM bytes changed by this document.
- Reproduction: Zeograd re-fetch 2026-11-24 (`/tmp/pce_bios.html` local, L601-603); Stum re-check 2026-11-24 (`/tmp/stum.html` local, ex_dotmod row); HuC `pcengine.inc` re-verify 2026-11-24 (18844 bytes, SHA-256 matches, L507); Hugo `bios.c` re-verify 2026-11-24 (29130 bytes, SHA-256 matches, L79-81/L228-233/absence in `handle_bios`); Geargrafx pin per `tools/dependencies.json` (`b49ae82a`/`71746c8`); G5 observation per Issue #802 comment `7119970902` (HEAD `0a935398`). Local `/tmp` files are scratch, never committed, never published.

NOT a compatible System Card BIOS. Geargrafx observations are not hardware proof.
