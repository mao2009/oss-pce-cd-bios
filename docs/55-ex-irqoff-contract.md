# $2D EX_IRQOFF contract research + $2C/$2D pair comparison (Issue #55, G5 P0 task)

Status: **reference contract UNKNOWN; no independent functional implementation; no verification.**
**NOT a compatible System Card BIOS. NOT genuine ABI. NOT DEV_CALLABLE.
`release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81 (unchanged by this document).
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, or saves
appear here. `./rom/` was not accessed in any form for this document
(no existence check, listing, or read); commercial observations cited here
reuse the sanitized structural record only (slots, entry PCs, regs, VDC ints,
MPR/SP, halt, cold/warm, truncation), already Owner-permitted and stored
outside the repo.

Branch basis: `d8eda6f9` (origin/main at task start; PR #837 `$2C` APICALL-cap
integrated). Companion code change in the same task (non-invasive VDC snapshot
opt-in for `geargrafx_api_entry_probe.cpp` + `geargrafx_api_entry.py` +
regression suite `tests/integration/test_apicall_vdc_snapshot.py`) is an
observation-mechanism improvement, not a `$2C`/`$2D` implementation: it adds
no BIOS body, no wiring, and no release-eligible behavior.

Related: #55 (`$2D` EX_IRQOFF), #54 (`$2C` EX_IRQON, base doc
`docs/54-ex-irqon-contract.md` — this document is consistent with it and
extends the pair analysis), #802 (G5 path), #635 (blocker measurement),
#63 (`$35`), #64 (`$36`), #46 (`$24`), #47 (`$25`), #84 (`$4A`), #38 (`$1C`
BLOCKED). Tracker #3, prerequisites #1, #2, #6, #8. Follow-up #838
(APICALL-cap hardening) is **not** duplicated here: no commercial CLI change
for `max_apicalls`, no `apicall_dropped` persistence change; the VDC flag is
a separate `run_probe(..., vdc_snapshot=...)` opt-in (default off) with its
own 8th probe arg.

## 1. Purpose and scope

G5 commercial-CD boot path (parent #635, integration #802) reached `$35`
with the preservation-only scaffold (ExpE, Issue #802 comments `7178913072`,
`7179845616`):

- CUE[0] ExpE: `$02 -> $33 -> $4A -> $23 -> $25 -> $24 -> $36 -> $35 -> $2C
  (unimplemented halt)` (9 APICALLs, cap-32, cold/warm identical, 80M budget).
- CUE[1] ExpE: `$1E -> $4A -> $02 -> $02 -> $21 -> $1C` (unchanged, `$1C`
  BLOCKED_ABI).

`$2C` (EX_IRQON candidate) is therefore the observed genuine boot blocker.
`$2D` (EX_IRQOFF candidate) is its adjacent pair. This document investigates
the `$2D` contract and the `$2C`/`$2D` pair so that a functional (D1) or
limited experimental (D2) implementation can be scoped with evidence. It does
NOT complete the spec by guessing. In particular, the names "IRQON"/"IRQOFF"
MUST NOT be taken to authorize a CLI/SEI, an interrupt-mask write, or a VDC
register write by themselves.

Required outcome: purpose/scope, references/provenance, ABI contract tables
for `$2D` and the pair delta, A=7 analysis, IRQ-contract separation
(CPU I / IDR / IRR / VDC IE / SR / handler / timing), VDC observation
mechanism selection + non-invasiveness proof, synthetic real-core validation,
ExpE commercial re-observation with VDC, display-init debt re-evaluation,
E1/E2/E3 selection, open issues, acceptance criteria.

Safety: no main direct commit, independent worktree only, no interference
with other sessions, no force-push/amend/destructive reset, no `./rom/`
access for this document, no commercial-media disclosure, 0 reference-BIOS
JSR, no VERIFIED promotion, `release_eligible=false` kept.

## 2. References and provenance

All external items are REFERENCE_ONLY / PENDING unless stated otherwise.
No file or fragment from them enters the firmware. No source text is
reproduced; only fact citations (URL, version, date, what was claimed).

| ID | Source (pinned where applicable) | What it establishes about `$2D` / pair | Disposition |
|----|----------------------------------|------------------------------------------|-------------|
| S1 | Zeograd System Card notes, `https://www.zeograd.com/download/pce_bios.html` (System Card 3.0 scope; reverse-engineering noted on the page itself; re-fetched 2026-11-26 for this task, 19170 bytes, SHA-256 `6966984b…` redacted to prefix only for local scratch; one `EX_IRQOFF` + one `EX_IRQON` + `EX_RCRON/OFF` + `EX_BGON/OFF` + `EX_SPRON/OFF` + `EX_DSPON/OFF` section each) | `EX_IRQOFF`, Function `#$2D`, IN `(N/A)`, OUT `(N/A)`, one-liner: “clears bit 3 in VDC reg #5 and updates `$20F3` and `$20F7`.” Pair context: `EX_RCRON $2A` sets bit 2 / `EX_RCROFF $2B` clears bit 2 (same `$20F3`/`$20F7` update); `EX_IRQON $2C` sets bit 3 (same mirrors); `EX_BGON $2E` sets bit 7 in `$20F3` *without* VDC update (and `BGOFF/SPRON/SPROFF/DSPON/DSPOFF` clear/set bits 6/7 in `$20F3` only). RAM map on same page: `$20F3` = Current VDC register selected, `$20F7` = Value of VDC reg #5. No input/output register contract, no flags, no timing, no error contract in any cited entry | REFERENCE_ONLY, PENDING. Names + slots + VDC-bit + mirror hint only; zero call contract. N/A recorded as absence, never as no-side-effect proof. Reverse-engineering note means single-source behavior MUST NOT ground D1 alone |
| S2 | Stum generation table, `https://www.stum.de/2025/pcenginebiosoffsets/` (2025-07-28; CC BY-NC-SA 4.0 display, fact citation only; re-fetched 2026-11-26, 24169 bytes, SHA-256 `81a4c1c1…`; table header lists System Card 1.0/2.0/3.0 + Super 3.0 variants) | Rows `ex_irqon $E084 / $E466 / $E3CF…` and `ex_irqoff $E087 / $E46A / $E3D3…` adjacent; per-generation bodies differ (same family pattern as `$35`: JSR stable, bodies move). JSR table mechanics project-verified (`$E084 = $E000+3*$2C`, `$E087 = $E000+3*$2D`) | REFERENCE_ONLY, PENDING. Mapping + grouping only. Single-behavior assumption FORBIDDEN. License CC BY-NC-SA 4.0 respected (no copy) |
| S3a | pce-devel/huc `include/hucc/pcengine.inc` @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` (BSL-1.0; pin re-verified 2026-11-26: 18844 bytes, SHA-256 `b6a25d33…` matches; single `ex_irqon` line 515 + single `ex_irqoff` line 516) + `hucc-systemcard.asm` @ same pin (14-byte placeholder in old path; actual `hucc-systemcard.asm` under `src/hucc/` reviewed: no C-callable wrapper for the IRQ family observed; only `system ex_*` macros for other families) | Caller-side equates only (`ex_irqon = $00:E084`, `ex_irqoff = $00:E087`). Absence of wrapper recorded as absence | REFERENCE_ONLY, PENDING. Name-to-entry only. Nothing copied. BSL-1.0 respected |
| S3b | pce-devel/huc `include/huc/system.inc` + `include/huc/startup.asm` @ `0230c17` (2026-11-26 clone; LICENSE mixed/unclear — MagicKit parts predate licensing awareness, Ulrich Hecht deltas BSL; treated as REFERENCE_ONLY, fact citation only) | Caller-side practice (NOT a BIOS contract): `startup.asm` CDROM path calls `jsr ex_dspoff / jsr ex_rcroff / jsr ex_irqoff / jsr ad_reset` during `init_hw`, then after `lib2_init_psg/vdc`, `joyena`, `__sound_init`, calls `jsr ex_dspon / jsr ex_rcron / jsr ex_irqon / stz disp_cr`. **No LDA/LDX/LDY/PHA/PLP immediately before either `ex_irq*` JSR** in the listing (predecessors are the neighboring `ex_*` JSRs). `system.inc` lines 110-113 give `ex_irqon = $E084`, `ex_irqoff = $E087` (same as HuCC) | REFERENCE_ONLY, PENDING (license unclear). Proves HuC’s own startup does not set up A/X/Y for the IRQ pair; supports “no-input” caller practice, refutes A=7 as required input (see §4). Nothing copied. Order `dspoff→rcroff→irqoff … dspon→rcron→irqon` is caller order, never a genuine BIOS ordering contract |
| S4 | mckayemu/hugo `bios.c` @ `master` (2026-11-26 fetch, 29130 bytes, SHA-256 `db3f227e…`; repo COPYING GPL; prior pin `64ed226d…` unresolved as commit, re-verified at master, commit `64ed226d…2016-01-12` for the file history) | Selector defines `EX_IRQON 0x2C`, `EX_IRQOFF 0x2D` (lines 88-89) + debug-name `case EX_IRQON: return "EX_IRQON"` / `case EX_IRQOFF: return "EX_IRQOFF"` (lines 246-249). `handle_bios()` has **no case body** for either (falls to unhandled default `put_8bit_addr(reg_pc, CDBIOS_replace[…])`). Prior `$35`-task pattern (selector + debug name, no body) now verified for `$2C`/`$2D` themselves, not transferred | REFERENCE_ONLY, PENDING for license (GPL-2.0). Selector + debug name only; zero behavior. REJECTED for ROM inclusion. Nothing copied |
| S5a | Pinned Geargrafx `b49ae82a` (`tools/dependencies.json`; GPL-3.0, test-only) — `src/huc6270_defines.h` (CR/R05 IE bits `CONTROL_COLLISION 0x01 / OVERFLOW 0x02 / SCANLINE 0x04 / VBLANK 0x08`; STATUS bits `COLLISION 0x01 / OVERFLOW 0x02 / SCANLINE 0x04 / SAT_END 0x08 / VRAM_END 0x10 / VBLANK 0x20 / BUSY 0x40`; `REG_CR 0x05 / REG_RCR 0x06`; `R[5]` mask `0x1FFF`), `src/huc6270.cpp:ReadRegister` (SR read returns `m_status_register & 0x3F [+BUSY]`, then `AssertIRQ1(false)`, `m_status_register = 0` — read-to-clear), `src/huc6270.cpp:VBlankIRQ/RCRIRQ` (`if (R[CR] & CONTROL_VBLANK/SCANLINE)` then `STATUS_VBLANK/SCANLINE` + `AssertIRQ1(true)`), `src/huc6270_inline.h:82-84` (`GetState()` returns `&m_state`, no side effects), `src/huc6280.h:71-72` + `huc6280.cpp:272-275` (`IDR/IRR` pointers, `GetState()` returns `&m_processor_state`), `huc6280_inline.h:134-137` (`CheckIRQs(): m_irq_pending = IsSetFlag(INTERRUPT) ? 0 : IRR & ~IDR`), `RenderLine:759-765` (`latched_cr & 0x80/0x40` gate BG/SPR rendering) | Hardware map AS IMPLEMENTED by the pinned core. Gives locations/encodings for CPU-side mask/pending (IDR/IRR, I flag P bit 2 `0x04`) and VDC-side enables/status (CR IE bits 2/3 = `0x04/0x08`, SR, RCR, AR, display gates). `GetState()` dereference is non-invasive; `ReadRegister` is destructive (clears SR, deasserts IRQ1). No extra steps, no event-time change from host reads | REFERENCE_ONLY, PENDING for license. Hardware facts only — NOT `$2C`/`$2D` BIOS facts. No code copied. Hardware capability MUST NOT be mistaken for the BIOS calling convention |
| S5b | In-tree measured I/O practice (MIT): `src/overrides/slot_23.s` (VDC window), `src/ex_dspoff.s`, harnesses `geargrafx_dev_scrsiz.py`, `geargrafx_dev_cr.py`, `geargrafx_dotmod24_probe.cpp`, `geargrafx_satclr36_probe.cpp`, `geargrafx_sprdma35_probe.cpp` (+ `*_writes_probe.cpp`) | VDC window discipline (MPR0 `$FF` + `SEI` + absolute + restore + AR left; max-push). `GetHuC6270_1()->GetState()` snapshot pattern (`AR`, `R[0..19]`, `SR`, MPRs, IDR/IRR) with per-cycle baselines. NO in-tree IRQ-enabling BIOS body exists (every experimental body assumes IRQs masked/absent and performs no I/O control) | PROJECT_SPEC_ONLY. Discipline reusable IF I/O ever justified. No IRQ precedent exists in-tree to reuse |
| S5c | NEC HuC6280/HuC6270 manuals + PCE IRQ-management public notes (UNPINNED — no title/edition/section/page/URL in-tree; not re-fetched) | Claimed register/sequencing notes (I flag, IDR/IRR bits, VDC IE sources, VBlank/raster/timer paths). In-tree repetitions are NOT independent confirmations | HYPOTHESIS. Downgraded: MUST NOT ground implementation until pinned |
| S6 | In-tree DEV precedents: `$23` DEV, `$2E` CR-family, `ex_dspoff.s`, `$4A` preservation, `$25` B1, `$24`/`$36`/`$35` B1-analogous + harnesses | Project-own contracts only. Reusable as discipline (stack accounting, no-RMW shadow practice, MPR1/`SEI`/absolute window, RAM-diff scoping), NEVER as `$2C`/`$2D` proof. CR-family CLI/SEI-adjacent behavior belongs to CR/B2 only, never assumed for IRQ pair | PROJECT_SPEC_ONLY. `$35` B1-analogous skeleton reusable as NO-I/O template ONLY; its SEMANTICS (or absence) never grounds IRQ pair |
| S7 | Sanitized G5 observations (Owner-permitted, structural only; this task re-observed with raised APICALL capacity 32 + new non-invasive VDC snapshot opt-in `vdc_snapshot=1`) | CUE[0] ExpE `$02->$33->$4A->$23->$25->$24->$36->$35->$2C` halt (9 APICALLs, X=`$2C`=44, entry `$E084`, all table JSR delta 2, in_table, MPR1 `$F8`, SP balanced, stack MATCH, cold/warm identical, 80M budget, no truncation, dropped 0). `$2C` entry call-state OBSERVED once per cycle (A=7/X=0/Y=0/P=`$06`/I=1, IDR=0, IRR=0; VDC AR=2/CR=0/RCR=0/SR=0 at `$2C`, CR=0/SR=0 at every API entry; AR 0→9→2 across the chain; see §4). CUE[1] ExpE unchanged `$1E->$4A->$02->$02->$21->$1C` halt (6 APICALLs, X=`$1C`, VDC AR=0/CR=0/SR=0 throughout, IDR/IRR=0). Media invariant pre/post identical (counts only, no hashes/paths published). BIOS image hashes recorded privately only | OBSERVED (call-state + VDC snapshot only, two games, single path each). No memory/sector/content data. Genuine `$2C`/`$2D` (and everything upstream genuine) UNKNOWN/BLOCKED. VDC snapshot adds structural ints only; commercial output stays RAM-clean |
| S8 | Ledgers: `spec/api_slots.json` ids 44/45 = `EX_IRQON`/`EX_IRQOFF`, `spec/api_contracts.json` slots 44/45 (`EX_IRQON 0xE084` / `EX_IRQOFF 0xE087`, HYPOTHESIS, all UNKNOWN), `spec/dev_implementations.json` ids 44/45 (`NOT_READY`), `tools/build_rom.py` 81-slot JSR table (`$E000+3*slot`, stub `LDX #slot; JMP api_unimplemented`) | Current state: hypothesis, zero behavior, no DEV override, table mechanics project-verified | Project record, unchanged. `$E084/$E087 = $E000+3*$2C/$2D` is layout fact, not ABI proof |

Gate: S1-S4 REFERENCE_ONLY/PENDING; S5a test-dependency/REFERENCE_ONLY; S5b PROJECT_SPEC_ONLY; S5c HYPOTHESIS; no file/fragment enters firmware.

## 3. ABI contract tables

### 3.1 Genuine `$2D` (EX_IRQOFF candidate)

| # | Item | Genuine `$2D` | DEV `$2D` | Disposition |
|---|------|---------------|-----------|-------------|
| 1 | Name/slot/entry | `EX_IRQOFF` / `$2D` / `$E087` candidate | none (stub halts) | S1/S2/S3 name+entry HYPOTHESIS as callee; S8 HYPOTHESIS. SOURCE_CONFIRMED only as caller equate (S3a lines 515-516, S3b `system.inc` 112-113) |
| 2 | Generations | Bodies differ per generation (S2 pattern); JSR stable | N/A | Single-behavior assumption FORBIDDEN |
| 3 | Pair `$2C` relation | Adjacent names + symmetric Zeograd one-liners (set vs clear bit 3) suggest a pair, but do NOT prove symmetric contracts, shared flags, shared mirrors, or call order | none | S1 adjacency is grouping + hint, never behavior. #54 stays independent; symmetry MUST be proved per-field (§3.3) |
| 4-6 | Inputs A/X/Y / work-RAM | UNKNOWN (S1 IN N/A for both; S3b shows HuC startup sets up no regs for either) | none | Single-callsite entry values (none observed for `$2D` — never reached) are absent, never a contract |
| 7 | CPU I flag | UNKNOWN (genuine effect on I unproven: SEI vs preserve vs conditional all ungrounded) | n/a | No OBSERVED I for `$2D`. §5 keeps I / IDR / VDC-IE strictly separate |
| 8 | IRQ controller (IDR/IRR) | UNKNOWN (which sources, mask vs clear, pending handling all unproven) | n/a | Hardware locations per S5a only |
| 9 | VDC enables/status (CR IE, SR) | UNKNOWN (which IE bits, value domain, SR-clear side effects, timing all unproven; Zeograd “clears bit 3” is a single reverse-engineering hint, not a calling convention) | n/a | S5a gives locations + SR read-clear fact; S1 hint alone never authorizes a store. Any future VDC write needs public BIOS-contract link `$2D`-to-register |
| 10 | MPR precondition | UNKNOWN (genuine); no OBSERVED path reaches `$2D` | n/a | Table mechanics project-verified (S8) |
| 11 | Stack/return | UNKNOWN | (no scaffold selected; see §6) | JSR `$E087` tail discipline would apply to any future D2 only |
| 12 | Preserved/clobbered (A/X/Y/P) | UNKNOWN | n/a | B1 latch-exact practice (S6) reusable as technique only, never as `$2D` proof |
| 13 | Flags (Carry/Z/N/V) | UNKNOWN | n/a | CR/B2 conventions never assumed for `$2D` |
| 14 | IRQ timing/pending/handler/VBlank | UNKNOWN (sync vs async, wait vs enable-and-return, handler setup all UNPROVEN) | n/a | Natural time-passage IRQ events vs implementation side effects must be distinguished |
| 15 | Errors/rejects | UNKNOWN | n/a | No public error codes |
| 16 | Call order | UNKNOWN genuine; no OBSERVED `$2D` call in either game | N/A | One-game `$2C` order under experimental (SIMULATED) upstream returns. Genuine ordering UNKNOWN |

Verdict: genuine `$2D` UNKNOWN in every behavioral field. No OBSERVED call-state exists.

### 3.2 Genuine `$2C` (EX_IRQON candidate) — delta from `docs/54` (unchanged verdict, new pair/VDC evidence)

`docs/54` §3 verdict (all UNKNOWN) is re-affirmed. New evidence in this
task strengthens the *context* without promoting any field:

- Zeograd pair (S1): `$2C` sets bit 3 (vs `$2D` clears bit 3); both update
  `$20F3`/`$20F7`; `IN/OUT N/A` for both. This is a symmetric *hint*, not a
  contract (see §3.3).
- HuC caller (S3b): neither `$2C` nor `$2D` has register setup in HuC’s own
  startup; supports no-input caller practice for both.
- Hugo (S4): both have selector + debug name only, no `handle_bios()` body.
- VDC snapshot (S7): at `$2C` entry CR=0/SR=0/RCR=0/AR=2, IDR=0/IRR=0, I=1.
  CR=0 at *every* API entry in both games means VDC IE and display gates are
  off on entry throughout the observed path (see §4, §7).

No field moves from UNKNOWN.

### 3.3 Pair comparison (`$2C` vs `$2D`)

| Aspect | `$2C` EX_IRQON | `$2D` EX_IRQOFF | Pair verdict |
|--------|----------------|-----------------|--------------|
| Name/slot/entry | `$2C` / `$E084` | `$2D` / `$E087` (+3 bytes, next slot) | Adjacent slots, stable JSRs (S2/S3). Layout fact, not behavior |
| Zeograd one-liner | Sets bit 3 in VDC reg #5, updates `$20F3`/`$20F7`, IN/OUT N/A | Clears bit 3 in VDC reg #5, updates `$20F3`/`$20F7`, IN/OUT N/A | Symmetric *wording*. Single reverse-engineering source; IN/OUT N/A for both. Symmetry of wording ≠ symmetry of contract |
| Stum | `ex_irqon $E084…` | `ex_irqoff $E087…` (adjacent row, same per-generation drift) | Grouping only |
| HuC/HuCC caller equate | `ex_irqon = $E084` | `ex_irqoff = $E087` | Name-to-entry only |
| HuC startup caller practice | `jsr ex_irqon` with no reg setup (after `dspon/rcron`) | `jsr ex_irqoff` with no reg setup (after `dspoff/rcroff`) | Both suggest no-input practice; neither proves preservation, MPR, I, IDR, VDC, or errors |
| Hugo HLE | Selector + debug name, no body | Selector + debug name, no body | Both unimplemented in HLE |
| Work RAM (`$20F3/$20F7`) | “Updates” per S1; map says `$20F3`=Current VDC reg, `$20F7`=Value of reg #5 | Same wording | Mirror *existence* hinted; values, order, atomicity, failure modes all UNKNOWN. No WRAM reads added to commercial probe (RAM-clean kept) |
| CPU I flag | UNKNOWN (name alone MUST NOT authorize CLI) | UNKNOWN (name alone MUST NOT authorize SEI) | I / IDR / VDC-IE kept separate (§5). No CLI/SEI in any implementation without per-API grounding |
| IDR/IRR | UNKNOWN, OBSERVED 0/0 once at `$2C` | UNKNOWN, never observed | 0/0 once is not a contract |
| VDC CR IE | Hint bit 3; OBSERVED CR=0 at `$2C` entry | Hint bit 3; never observed | Which bits, values, handshake, shadow (`$20F7`) all UNKNOWN |
| SR | Locations + read-clear per S5a; OBSERVED SR=0 at every entry | Same hardware; never observed | Any future impl must prove no gratuitous SR clear; any future test must not perform gratuitous MMIO SR reads |
| VBlank/Raster/Timer | UNLINKED to either API | UNLINKED | Hardware source names ≠ BIOS-to-source link |
| BIOS-side state (vectors/handlers/flags) | UNKNOWN, no in-tree precedent | UNKNOWN, no in-tree precedent | No BIOS state invented |
| Call order | OBSERVED `…->$35->$2C` on one game (SIMULATED upstream) | Never observed; HuC startup order `…irqoff… …irqon` is caller order, not BIOS order | Order UNKNOWN genuine |
| Symmetry claim | — | — | **Names + adjacency + symmetric one-liners do NOT license a symmetric implementation.** Each API’s I/IDR/VDC/handler/timing/errors must be grounded independently. A=7 (see §4) is not a shared flag |

## 4. Observed `$2C` call-state + VDC snapshot (OBSERVED — not a contract) and A=7 analysis

CUE[0] ExpE with APICALL capacity 32 + `vdc_snapshot=1` (both cycles
identical, no truncation, dropped 0; media invariant pre/post identical).
All 9 calls: table JSR (delta 2), in_table, MPR1 `$F8`, SP balanced, stack
MATCH, `return_matches_caller` true at entry-observation time (same
documented limitation as the `$35` suite: stacked-return bytes read before
the callee runs, not an RTS-landing proof):

- `$02 $E006`, `$33 $E099`, `$4A $E0DE`, `$23 $E069`, `$25 $E06F`,
  `$24 $E06C`, `$36 $E0A2`, `$35 $E09F`, then
- **`$2C $E084`**: caller A=7, X=0, Y=0, P=`$06` (I=1, Z=1), MPRs unchanged
  (`$F8…`), IDR=0, IRR=0, VDC AR=2/CR=0/RCR=0/SR=0, halt `api_unimplemented`
  X=`$2C`, halt confirmed, cold/warm identical.

Full VDC snapshot along the CUE[0] chain (structural ints, commercial-clean):

| API | AR | CR (R05) | RCR (R06) | SR | IDR/IRR | I |
|-----|----|----------|-----------|----|---------|---|
| `$02` | 0 | 0 | 0 | 0 | 0/0 | 1 |
| `$33` | 0 | 0 | 0 | 0 | 0/0 | 1 |
| `$4A` | 0 | 0 | 0 | 0 | 0/0 | 1 |
| `$23` | 0 | 0 | 0 | 0 | 0/0 | 1 |
| `$25` | 9 | 0 | 0 | 0 | 0/0 | 1 |
| `$24` | 9 | 0 | 0 | 0 | 0/0 | 1 |
| `$36` | 9 | 0 | 0 | 0 | 0/0 | 1 |
| `$35` | 9 | 0 | 0 | 0 | 0/0 | 1 |
| `$2C` | 2 | 0 | 0 | 0 | 0/0 | 1 |

CUE[1] ExpE (6 APICALLs, X=`$1C`, cold/warm identical, no truncation):
`$1E->$4A->$02->$02->$21->$1C`, VDC AR=0/CR=0/SR=0 throughout, IDR/IRR=0.

Discipline notes: entry P/I/IDR/IRR/VDC are guest-observed facts of
specific calls. They MUST NOT be promoted to preconditions, postconditions,
or an implementation checklist. VDC AR variation (0→9→2) reflects prior
bodies’ I/O-window usage (e.g. `$23` R09 path leaves AR=9), not a `$2C`
contract; single-snapshot attribution (“which API caused AR=2”) is
forbidden (see §7). CR=0 at every entry means IE and display gates are off
on entry along the whole observed path; SR=0 means no pending VDC IRQ at any
observed boundary (within the probe’s sampling, not a continuous guarantee).

### A=7 analysis (bitmask vs enum vs residual)

- OBSERVED: A=7 only at `$36`/`$35`/`$2C` entries on CUE[0] (A=0 at
  `$33`/`$4A`/`$23`/`$25`/`$24`; A=2 at `$02`; CUE[1] shows A=176/1/2/127/255
  across its path). A=7 correlates with the tail of the CUE[0] chain, not
  with `$2C` alone.
- Caller-side (S3b): HuC’s own `jsr ex_irqon` / `jsr ex_irqoff` perform **no
explicit register setup** in the listing; the calls inherit whatever A the
prior `ex_*` left (whose output contracts are UNKNOWN). Absence of setup
does NOT prove A=7 is unrequired; the requirement stays unresolved.
- Zeograd (S1): IN `(N/A)` for both `$2C` and `$2D`. No input domain, no
  bitmask, no enum is given. Absence is recorded, never promoted.
- Hugo (S4): no body, no input handling to inspect.
- Hardware (S5a): no A-mapped IRQ/VDC select exists; A is a CPU register,
  not a VDC/IDR alias. Any “A bitmask” claim needs a BIOS-contract link that
  does not exist.
- Classification: **residual** (leftover from the commercial game’s own
  chain, possibly threaded through `$36`/`$35` scaffolds which preserve A)
  is a hypothesis consistent with the observations; **bitmask** and **enum**
  are UNGROUNDED hypotheses; “A=7 required” vs “A irrelevant” both remain
  UNKNOWN (no explicit-setup proof either way). No implementation may branch
  on A=7, mask with A, or require A=7 without independent caller-side +
  genuine-contract evidence.
- The same reasoning applies to X=0/Y=0/P=`$06`: OBSERVED once, never a
  contract.

## 5. IRQ-contract analysis (kept separate, none grounded for `$2C`/`$2D`)

- HuC6280 CPU I flag (P bit 2; SEI/CLI/BRK family): hardware fact (S5a +
  S5c HYPOTHESIS until manuals pinned). Whether genuine `$2C` executes CLI,
  `$2D` executes SEI, either preserves I, or conditions on I is UNKNOWN.
  Names alone MUST NOT authorize CLI/SEI. OBSERVED I=1 at `$2C` entry on one
  path is not a precondition and not a postcondition.
- IRQ controller mask (IDR) / pending (IRR): hardware locations per S5a;
  `CheckIRQs: pending = I ? 0 : IRR & ~IDR`. OBSERVED 0/0 once/at every
  entry is not a mask contract. Which sources (`IRQ2/IRQ1/TIQ`) either API
  touches is UNKNOWN.
- VDC interrupt enables (CR/R05 IE bits 0–3: `0x01` collision, `0x02`
  overflow, `0x04` scanline/RCR, `0x08` VBLANK): locations per S5a; Zeograd
  bit-3 hint for the IRQ pair and bit-2 hint for the RCR pair are consistent
  with the hardware bit assignment, but the BIOS-to-register link (value
  domain, read-modify-write vs shadow via `$20F7`, AR handling via `$20F3`,
  SEI/window discipline, failure modes) is UNKNOWN. S1 hint alone is
  insufficient for a store.
- VDC status (SR) including its read-to-clear side effect (except BSY;
  `ReadRegister` clears + deasserts IRQ1, `GetState` does not): hardware
  fact per S5a. Any future implementation must prove it does not
  unintentionally clear status; any future test must not perform gratuitous
  SR reads. This task’s probe uses `GetState()` only (§6).
- VBlank / raster / timer / sprite-collision sources: UNLINKED to either
  API (names of sources are hardware facts; the BIOS-to-source link is
  UNKNOWN).
- BIOS-side IRQ management (work-RAM flags including `$20F3`/`$20F7`
  mirrors, vector/handler installation, enable-vs-occurrence timing):
  UNKNOWN in every field; no in-tree precedent (S5b: no IRQ-enabling body
  exists). `$20F3`/`$20F7` values/order/atomicity are UNKNOWN; no WRAM reads
  were added to the commercial probe to chase them (RAM-clean kept).
- Stack/MPR/return-boundary effects of enabling interrupts mid-path:
  UNKNOWN; any future D1 must test handler-transition/return, pending-at-entry,
  and boundary abnormal cases.

## 6. Observation mechanism: selection, non-invasiveness, E1/E2/E3

### 6.1 Selection (why extend the API-entry observer, why bounds)

Existing assets surveyed: `geargrafx_api_entry_probe.cpp/.py` (WRAM→BIOS
watch, table/SP/MPR/IDR/IRR, commercial-clean, cap 1..64), `geargrafx_sprdma35_probe.cpp`
+ `geargrafx_dotmod24/satclr36/scrmod25` VDC-invariance probes
(`GetHuC6270_1()->GetState()` snapshot of `AR/R/SR` + MPR/IDR/IRR, cold/warm
baselines), `geargrafx_*_writes_probe.cpp` (WRAM write-history tracer),
IRQ harnesses (`geargrafx_irq_entry_guard/execbp/postaccept_boundary`,
`geargrafx_irq_register`, `geargrafx_stack_irq_timing`,
`geargrafx_mpr1_tam_irq_probe`), pinned VDC internal-state API
(`HuC6270_State{AR,SR,R,READ_BUFFER,HPOS,VPOS,V_STATE,H_STATE}`).

New probes are not proliferated. The API-entry observer is extended with an
explicit opt-in VDC snapshot because the G5 question is “VDC/IRQ state *at
API boundaries* along the commercial path,” exactly what that observer
already samples (caller PC/entry PC/regs/MPR/IDR/IRR). The VDC-invariance
probes sample *around one callee* (before/after a synthetic JSR), not along
a 9-call commercial chain; the write-history tracer records WRAM diffs
(and is therefore unsuitable for commercial RAM-clean work).

Design (this task):

- Probe: `argv: rom cue labels max_steps mode [max_apicalls] [vdc_snapshot]`.
  `vdc_snapshot` 0/1, default 0. Default 6/7-arg behavior is byte-identical;
  `vdc=1` appends `vdc_ar/vdc_cr/vdc_rcr/vdc_sr` (unsigned ints) to each
  `APICALL` JSON object. No other schema, halt, watch, or gating change.
  Max 64 APICALLs, truncation counting, and commercial RAM-clean gating are
  unchanged. No `#838` CLI is added (`--commercial --commercial-*` untouched;
  `run_probe(..., max_apicalls=32)` is reused, `vdc_snapshot` is a Python-only
  opt-in).
- Python: `run_probe(..., max_apicalls=None, vdc_snapshot=None)`. `None`
  keeps the historical 6/7-arg invocation; `True/1` materializes default cap
  8 as the 7th arg when no explicit cap is given so the 8th position stays
  the VDC flag. `False/0` explicitly disables. Invalid values fail-closed
  (`ValueError`; direct C++ out-of-range/non-numeric exits 2).
- Bounds: observation target is API-boundary only (no mid-callee sampling,
  no VRAM/SATB/WRAM/framebuffer content, no stack bytes); record count is
  bounded by `max_apicalls` (≤64); commercial output is structural ints only
  (`assert_commercial_clean` still forbids `*_hex` and stack words; VDC ints
  are allowed as structural).

### 6.2 Non-invasiveness (why `GetState()` and what was proved)

Required: no guest RAM writes, no guest register writes, no VDC/MMIO writes,
no MMIO SR reads, no IRQ inject/clear, no extra CPU steps, no extra
emulator steps, no event-time change, halt unchanged, commercial gate kept.

- Source: the VDC path uses `core.GetHuC6270_1()->GetState()` + host
  dereference (`*AR`, `R[5]`, `R[6]`, `*SR`) only. `GetState()` is
  `return &m_state` (`huc6270_inline.h:82-84`, `huc6280.cpp:272-275`), no
  side effects. `ReadRegister` (which clears SR and deasserts IRQ1),
  `WriteRegister`, `Memory::Write`, `SetMpr`, `AssertIRQ*`, and any
  `RunToVBlank`/`RunInstruction` in the VDC path are absent (verified by
  source read; the only `RunToVBlank` is the pre-existing single-step).
  The VDC read sits inside the existing `APICALL` print block, after the
  guest instruction has already been observed, before `one_instruction()`
  advances; it adds host formatting only.
- SR non-destructiveness: `ReadRegister` path (`m_status_register = 0` +
  `AssertIRQ1(false)`) is never taken; `GetState` reads `m_status_register`
  without clearing. The probe therefore cannot consume a pending
  `VBLANK/RCR` status that the game or the core set.
- Dynamic对照 (synthetic, pinned core, same ROM/budget, off vs on):
  APICALL slots/order, caller/entry PCs, SP deltas, entry regs/MPR/IDR/IRR,
  stack checks, halt PC/reason/confirmation, truncation/dropped are
  identical except natural step-count variance (multiple identical runs show
  ±2k-step jitter with no systematic +N per APICALL; VDC-on runs are not
  systematically later). Log-output growth (added VDC keys) is distinguished
  from guest-state change (none). Commercial对照 (CUE[0] ExpE, same ROM/budget/cap):
  9-call order, halt X=`$2C`, cold/warm identity, truncation/dropped are
  identical off vs on; only steps (natural variance) and added VDC keys
  differ.
- The existing `test_apicall_capacity.py` suite stays GREEN (default-path
  6-arg behavior unchanged); the new `test_apicall_vdc_snapshot.py` suite
  proves off/on对照, invalid-arg refusal (Python + direct C++), commercial-clean
  with VDC keys, cold/warm order, and mutation RED (see §6.3).

### 6.3 Mutation (only named-test kills are RED; the rest are NOT demonstrated)

Demonstrated (named tests in `tests/integration/test_apicall_vdc_snapshot.py`):

- Known-state exact match: synthetic guests preset distinct nonzero AR/CR/RCR
  (P1: AR=2/CR=0x10/RCR=0xAA; P2: AR=7/CR=0x20/RCR=0x155, different entry
  regs) and VBLANK-pending SR (P3: AR=3/CR=0x08/RCR=0x00/SR=0x20 after a WRAM
  spin). The probe emits exactly those values at API entry
  (`test_exact_known_state_p1`, `test_exact_known_state_p2_distinct`,
  `test_pending_sr_exact_and_persists_p3`; 2 calls each, cold/warm identical,
  clean `cd_halt_ok`). One fixed value cannot satisfy two patterns.
- Pending-SR persistence (non-destructive): P3 asserts SR=0x20 on BOTH
  consecutive calls in the same run, both cycles
  (`test_pending_sr_exact_and_persists_p3`). A destructive (SR-clearing)
  observation would show 0x00 on the 2nd call and fail.
- All-zero / wrong-register / swapped falsified records: fabricated all-zero
  and CR/SR-transposed records fail the exact-match assertions against P1/P3
  (`test_allzero_falsified_rejected_against_known`,
  `test_swapped_falsified_rejected_against_known`). This is what catches an
  all-zero-emitting or wrong-register probe.
- Non-integer / out-of-range VDC constants: rejected by `check_vdc_record`
  (`test_mutation_fixed_value_rejected` rejects string `"0"` and `99999`).
- Missing VDC keys: rejected (`test_missing_vdc_key_rejected`).
- Invalid `vdc_snapshot` values: rejected fail-closed with exact C++ exit 2
  plus valid 0/1 controls completing the run on real files (Python
  `ValueError`, `test_invalid_vdc_refused` + `test_direct_probe_rejects_bad_vdc`).
- Commercial gate: positive clean audit passes
  (`test_commercial_mode_stays_clean_with_vdc`) and injected `*_hex`/stack
  fields are rejected (`test_commercial_gate_rejects_forbidden_fields`).
  Positive-only passage and injected-negative rejection are not the same
  claim; both are tested separately.

NOT demonstrated as probe-level kills (recorded as gaps, never as RED):

- In-range constants by range check alone: `check_vdc_record` accepts them by
  design (`test_range_check_limitation_inrange_swap` documents an in-range
  CR/SR transposition passing); exact-match tests cover this instead.
- IE mask (bit 2 vs bit 3) and I-flag bit position:
  `test_bit_expression_helpers_not_probe_level` asserts literal bit
  expressions only. It does not exercise the probe or its output.
- Snapshot-timing shift (post-callee vs boundary): no test produces or rejects
  a post-callee snapshot; only the API-boundary discipline (WRAM→BIOS
  transition, before entry execution) is documented.
- Extra emulator step: the source audit forbids destructive status-path calls
  and guest writes, but does not forbid an extra `RunInstruction`/`RunToVBlank`;
  the off/on对照 excludes `step` (natural ±2k jitter documented in the
  strict off/on test), so an extra step is NOT demonstrated as rejected.
- Out-of-scope VRAM/SATB/WRAM/framebuffer or media identifiers: the VDC record
  is by construction 4 ints only and the harness never logs media identifiers,
  but no mutant injects such content, so NOT reported as a killed mutant.

Side-effect mutants live in synthetic isolation only, never in the commercial
probe.

### 6.4 E1/E2/E3 selection

- E1 functional: **BLOCKED**. S1 one-liner + S3 caller practice + S5a
  hardware locations do NOT constitute a BIOS calling convention for any of:
  I-flag action, IDR mask, VDC IE target/range/value/shadow (`$20F7`),
  AR handling (`$20F3`), SR discipline, handler setup, timing, errors. No D1
  body, no VDC write, no CLI/SEI, no mask change in this task.
- E2 preservation-only scaffold: **NOT selected in this task** (deferred,
  not rejected in principle). Reasons: (1) semantic inversion is load-bearing
  here, unlike the display no-ops: a preservation scaffold leaves I=1/VDC-IE/IDR
  untouched, i.e. it *withholds the service the name promises*. If the game
  VBlank-waits after `$2C`, the result is a budget-exhaustion hang, not a
  clean NEXT halt — attribution is weaker than the `$35` case (where the game
  demonstrably proceeded). (2) Display-init debt (`$25`/`$24`/`$36`/`$35`
  no-ops, OBSERVED CR=0 throughout) compounds the confound: VDC may be
  unconfigured, so IRQ behavior is doubly ungrounded; a `$2C` no-op cannot
  disentangle “game waits for IRQ” from “display never initialized.”
  (3) A responsible E2 needs its own design doc (preservation-vs-enable
  contract, hang-vs-NEXT interpretation rules), 64-variant wiring, dedicated
  suites, chain updates, ExpF commercial run, and review — a full PR-sized
  follow-up, not a same-PR addition. Mechanical B1 application without that
  work is prohibited and is not done here. A *functional* D2 that actually
  sets CR bit 3 + mirrors would be a different change (VDC write, window
  discipline, shadow handling) and needs the same full follow-up, not done
  here.
- E3: **selected**. No implementation; contract research + non-invasive
  observation mechanism + synthetic validation + commercial re-observation are
  the completed deliverables. BLOCKED is reported as a grounded conclusion,
  not a failure.

## 7. Display-init debt re-evaluation

- Public-spec confirmed dependencies: NONE beyond slot/entry names (S1–S3
  give no cross-API dependence). Zeograd lists `$25`/`$24`/`$36`/`$35`
  one-liners separately; no ordering contract links them to `$2C`.
- Synthetic-proven dependencies: NONE (no-op bodies have no side effects by
  construction; `$35` return through the synthetic chain is mechanics, not
  display progress).
- Commercially observed stops: `$4A`→`$25`→`$24`→`$36`→`$35`→`$2C` sequence on
  CUE[0] (each step advanced by the corresponding scaffold’s return). CUE[1]
  stops at `$1C` regardless.
- Measured VDC state (this task): CR=0/SR=0/RCR=0 at *every* API boundary in
  both games. CR=0 means VBLANK IE (bit 3), RCR IE (bit 2), and BG/SPR
  display gates (bits 7/6 per `latched_cr`) are all off on entry. SR=0 means
  no pending VDC IRQ at any observed boundary. AR 0→9→2 reflects window usage
  by prior bodies, not display configuration.
- Guesses (explicitly NOT claims): that upstream no-ops leave VDC
  unconfigured; that the game may VBlank-wait after `$2C`; that `$2D` mirrors
  `$2C`. All UNSUPPORTED — recorded as open, never used.
- Open: whether genuine display-init (`$25`/`$24`/`$36`/`$35` real services)
  is prerequisite to meaningful `$2C` behavior. OBSERVED CR=0 throughout
  suggests the display path is not yet initialized when `$2C` is reached, so
  IRQ behavior alone cannot complete the picture. Priority proposal:
  (1) `$2C`/`$2D` pair contract research (this doc + `docs/54`);
  (2) VDC-state observation capability for commercial runs (this task’s
  mechanism, no ABI guessing);
  (3) genuine display-init investigation (`$25`/`$24`/`$36`/`$35` real
  services) before revisiting E2 with hang-interpretation rules;
  (4) `$1C` BM_DELETE path stays independent (CUE[1] regression, not G5 P0).
  No API is re-implemented by this document.

Single-snapshot attribution is forbidden: from CR=0 at `$2C` entry alone we
do NOT claim “`$35` caused it,” “`$25` should have set it,” or “`$2C` will set
bit 3.” Existing synthetic fixtures can compare per-API deltas, but the
commercial chain’s upstream returns are SIMULATED (experimental scaffolds),
so genuine ordering remains UNKNOWN.

## 8. Open issues

- U1. Genuine `$2D` inputs/outputs/flags/I-flag action: UNKNOWN (never observed).
- U2. IDR mask vs IRR pending handling for either API: UNKNOWN.
- U3. VDC CR target bits/values/handshake/shadow (`$20F3`/`$20F7`) for either API: UNKNOWN (Zeograd hint only).
- U4. SR read-clear discipline for either API: UNKNOWN (hardware fact known, BIOS link unknown).
- U5. Handler/vector/BIOS-flag setup: UNKNOWN.
- U6. Timing (sync/async, wait vs fire-and-continue, VBlank alignment): UNKNOWN.
- U7. Relation to `$2A`/`$2B` RCR pair and `$2E`–`$33` display pair: names adjacent, contracts unlinked (bit-2 vs bit-3 vs `$20F3`-only is a hint pattern, not a contract).
- U8. NEXT after `$2C`: UNKNOWN (stub halt; no execution past `$2C` observed).
- U9. A=7 domain: UNKNOWN (no explicit setup observed; residual is a
hypothesis, not proof; bitmask/enum UNGROUNDED; requirement unresolved per §4).
- U10. Second-core cross-check: NOT_RUN (Geargrafx-only).
- U11. `$20F3`/`$20F7` mirror values: UNKNOWN (no WRAM reads in commercial probe by design).
- U12. Manuals pinning: NOT_RUN (S5c HYPOTHESIS).

## 9. Acceptance criteria

- (a) This contract doc merged with S1–S8, §3 UNKNOWN tables + §3.3 pair
  delta (no symmetric implementation), §4 OBSERVED-only call-state + VDC
  table with non-promotion rule + A=7 residual analysis, §5 separation
  analysis, §6 mechanism selection + non-invasiveness + mutation, §7 debt
  evaluation, §8 U1–U12.
- (b) VDC snapshot: bounded opt-in (0/1, default 0 unchanged), 6/7-arg
  history preserved, `max_apicalls` 1..64 unchanged, `apicall_dropped`
  invariant unchanged, invalid inputs fail-closed (C++ exit 2, Python
  `ValueError`), no guest writes, no MMIO SR reads, no extra steps,
  commercial output stays RAM-clean + structural-only, existing
  source-property tests GREEN.
- (c) Regression suite `tests/integration/test_apicall_vdc_snapshot.py` ALL
  GREEN on pinned core: strict off/on对照 (expected 2-call normal halt,
  slots/entries/reason/pc/RAM/dropped, steps excluded as natural jitter),
  known-state exact match (P1/P2 distinct AR/CR/RCR + P3 VBLANK-pending SR
  persisting across consecutive calls, cold/warm identical), invalid refusals
  (Python ValueError + direct-C++ exit 2 exactly with valid 0/1 controls),
  commercial-clean positive plus injected-forbidden-field negative,
  truncation invariant, cold/warm order. RED→GREEN recorded. Mutants RED only
  for non-int/out-of-range constants, missing keys, invalid flags,
  all-zero/wrong-register/swapped falsified records against known
  expectations, and gate bypass. NOT RED (documented limits): in-range
  constants by range alone, mask/I-flag literal helpers (not probe-level),
  timing shift, extra step, out-of-scope injection.
- (d) Re-observation on the same HEAD: synthetic off/on对照 + ExpE cap-32
  VDC (9 calls through `$2C`, X=`$2C` halt, trunc False, dropped 0,
  cold/warm identical, VDC table §4) + CUE[1] ExpE `$1C` regression (6 calls,
  VDC 0 throughout); media invariant pre/post identical; BIOS image hashes
  recorded privately only. No `#838` CLI duplicated.
- (e) `make test`, `make check` EXIT 0, `git diff --check`, default-path
  behavior unchanged (6-arg invocation preserved), Required CI GREEN,
  independent review with zero unresolved CRITICAL/MAJOR/MEDIUM, no merge
  without Owner approval.
- (f) Safety gates unchanged (JSR 0/81, VERIFIED 0/81,
  `release_eligible=false`, `$2C`/`$2D`/`$1C`/`$35` BLOCKED_ABI, DEV
  invariance, SIMULATED vs genuine vs synthetic vs real-core vs commercial
  strictly distinguished). G5_OBSERVED stays false.

NOT a compatible System Card BIOS. Geargrafx observations are not hardware proof.
