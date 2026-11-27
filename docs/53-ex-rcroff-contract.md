# $2B EX_RCROFF contract research + D2 limited-functional selection (Issue #53, G5 P0)

Status: **reference contract UNKNOWN; D2 limited-functional experimental selected (synthetic only).**
**NOT a compatible System Card BIOS. NOT genuine ABI. NOT DEV_CALLABLE.
`release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81 (unchanged by this document).
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, or saves
appear here. `./rom/` was not accessed in any form for this document
(no existence check, listing, or read); commercial observations cited reuse
only the sanitized structural record from PR #840 (slots, entry PCs, regs,
VDC ints, MPR/SP, halt, cold/warm, truncation), already Owner-permitted and
stored outside the repo. This task performs no new commercial measurement
(BLOCKED_PERMISSION for new commercial runs; synthetic only here).

Branch basis: `f8acbfce` (origin/main at task start; PR #840 `$2C` D2 integrated).
Related: #53 (`$2B` EX_RCROFF), #52 (`$2A` EX_RCRON, symmetric set-side pair,
separate API, not co-implemented), #54 (`$2C` EX_IRQON, D2 precedent
`docs/g5-display-functional-selection.md` + `src/ex_irqon.s`), #55 (`$2D`
EX_IRQOFF, pair doc `docs/55-ex-irqoff-contract.md`), #802 (G5 path),
#635 (blocker measurement). Tracker #3, prerequisites #1, #2, #6, #8.

Required outcome: purpose/scope, references/provenance, ABI contract table
(genuine UNKNOWN), `$2A`/`$2B` pair delta, A/X/Y/P analysis, IRQ/VDC
separation, D1/D2/BLOCKED selection with rationale, display-init debt,
BOOT-budget finding for full ExpG, open issues, acceptance criteria.

Safety: no main direct commit, independent worktree only, no interference
with other sessions, no force-push/amend/destructive reset, no `./rom/`
access, no commercial-media disclosure, 0 reference-BIOS JSR, no VERIFIED
promotion, `release_eligible=false` kept.

## 1. Purpose and scope

G5 commercial-CD boot path (parent #635, integration #802) reached `$2B`
with the `$2C`-functional image (ExpF, PR #840):

- CUE[0] ExpF: `$02 -> $33 -> $4A -> $23 -> $25 -> $24 -> $36 -> $35 -> $2C -> $2B
  (unimplemented halt)` (10 APICALLs, cap-32, cold/warm identical, 80M budget,
  VDC snapshot CR=0/AR=2 at `$2C`, `$2B` entry P=`$07`, CUE[1] `$1E->$4A->$02->$02->$21->$1C`
  unchanged; see `docs/g5-display-functional-selection.md` §5.4).

`$2B` (EX_RCROFF candidate) is therefore the latest genuine boot blocker on
the observed commercial path. This document investigates the `$2B` contract
so that a functional (D1) or limited experimental (D2) implementation can be
scoped with evidence. It does NOT complete the spec by guessing. In
particular, the name "RCROFF" MUST NOT be taken to authorize a VDC register
write, a CPU-I action, an IDR/IRR change, an RCR-register (R06) write, or a
Carry convention by itself. Clearing CR bit 2 is NOT writing RCR R06; the
two registers are distinct (CR=R05, RCR=R06).

Related: #52 (`$2A` sets bit 2, separate API, not co-implemented here).

## 2. References and provenance

All external items are REFERENCE_ONLY / PENDING unless stated otherwise.
No file or fragment from them enters the firmware. No source text is
reproduced; only fact citations (URL, version, date, what was claimed).

| ID | Source (pinned where applicable) | What it establishes about `$2B` / pair | Disposition |
|----|----------------------------------|------------------------------------------|-------------|
| S1 | Zeograd System Card notes, `https://www.zeograd.com/download/pce_bios.html` (System Card 3.0 scope; reverse-engineering noted on the page itself; re-fetched 2026-11-27 for the `$2C` task, 19170 bytes, SHA-256 `6966984b…` redacted to prefix only for local scratch; one `EX_RCROFF` + one `EX_RCRON` + `EX_IRQON/OFF` + `EX_BGON/OFF` + `EX_SPRON/OFF` + `EX_DSPON/OFF` section each) | `EX_RCROFF`, Function `#$2B`, IN `(N/A)`, OUT `(N/A)`, one-liner: “clears bit 2 in VDC reg #5 and updates `$20F3` and `$20F7`.” Pair context: `EX_RCRON $2A` sets bit 2 (same mirrors); `EX_IRQON $2C` sets bit 3 / `EX_IRQOFF $2D` clears bit 3 (same mirrors); `EX_BGON $2E` sets bit 7 in `$20F3` *without* VDC update (and siblings clear/set bits 6/7 in `$20F3` only). RAM map on same page: `$20F3` = Current VDC register selected, `$20F7` = Value of VDC reg #5. No input/output register contract, no flags, no timing, no error contract in any cited entry | REFERENCE_ONLY, PENDING. Names + slots + VDC-bit + mirror hint only; zero call contract. N/A recorded as absence, never as no-side-effect proof. Reverse-engineering note means single-source behavior MUST NOT ground D1 alone. RCR-register (R06) vs CR-bit-2 distinction is hardware fact (S5a), not Zeograd wording |
| S2 | Stum generation table, `https://www.stum.de/2025/pcenginebiosoffsets/` (2025-07-28; CC BY-NC-SA 4.0 display, fact citation only; re-fetched 2026-11-27, 24169 bytes for the `$2C` task) | Rows `ex_rcon`/`ex_rcoff` adjacent (same family pattern as `$2C`: JSR stable, bodies move). Exact `$E081`/`$E07E` values are layout facts (`$E000+3*slot`), not ABI proof | REFERENCE_ONLY, PENDING. Mapping + grouping only. Single-behavior assumption FORBIDDEN. License respected (no copy) |
| S3a | pce-devel/huc `include/hucc/pcengine.inc` @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` (BSL-1.0; pin re-verified 2026-11-27: 18844 bytes, SHA-256 `b6a25d33…` matches) + `hucc-systemcard.asm` @ same pin | Caller-side equates only (`ex_rcroff`/`ex_rcron` name-to-entry). No C-callable wrapper for the RCR family observed (same pattern as IRQ family). Absence of wrapper recorded as absence | REFERENCE_ONLY, PENDING. Name-to-entry only. Nothing copied. BSL-1.0 respected |
| S3b | pce-devel/huc `include/huc/system.inc` + `include/huc/startup.asm` @ `0230c17` (2026-11-26 clone; LICENSE mixed/unclear, treated as REFERENCE_ONLY) | Caller-side practice (NOT a BIOS contract): `startup.asm` CDROM path calls `jsr ex_dspoff / jsr ex_rcroff / jsr ex_irqoff / jsr ad_reset` during `init_hw`, then after `lib2_init_psg/vdc`, `joyena`, `__sound_init`, calls `jsr ex_dspon / jsr ex_rcron / jsr ex_irqon`. **No LDA/LDX/LDY/PHA/PLP immediately before either `ex_rcr*` JSR** in the listing (predecessors are the neighboring `ex_*` JSRs). Order `dspoff→rcroff→irqoff … dspon→rcron→irqon` is caller order, never a genuine BIOS ordering contract | REFERENCE_ONLY, PENDING (license unclear). Proves HuC’s own startup does not set up A/X/Y for the RCR pair; supports “no-input” caller practice, refutes A-as-bitmask as required input without further proof. Nothing copied |
| S4 | mckayemu/hugo `bios.c` @ `master` (2026-11-26 fetch, 29130 bytes, SHA-256 `db3f227e…`; repo COPYING GPL) | Selector defines + debug names only, no `handle_bios()` body for the RCR pair (same pattern as `$2C`/`$2D`: selector + debug name, no behavior). Prior `$35`-task pattern transferred only after per-API verification | REFERENCE_ONLY, PENDING for license (GPL-2.0). Selector + debug name only; zero behavior. REJECTED for ROM inclusion. Nothing copied |
| S5a | Pinned Geargrafx `b49ae82a` (`tools/dependencies.json`; GPL-3.0, test-only) — `src/huc6270_defines.h` (CR/R05 IE bits `CONTROL_COLLISION 0x01 / OVERFLOW 0x02 / SCANLINE 0x04 / VBLANK 0x08`; `REG_CR 0x05 / REG_RCR 0x06`; `R[5]` mask `0x1FFF`), `src/huc6270.cpp:ReadRegister` (SR read-to-clear), `VBlankIRQ/RCRIRQ` (`if (R[CR] & CONTROL_VBLANK/SCANLINE)`), `GetState()` non-invasive, `huc6280.h` I flag + `CheckIRQs: pending = I ? 0 : IRR & ~IDR`, `RenderLine` BG/SPR gates | Hardware map AS IMPLEMENTED by the pinned core. Gives locations/encodings for CR (R05) vs RCR (R06) distinction, CPU-side mask/pending (IDR/IRR, I flag `0x04`), VDC-side enables (CR IE bit 2 = `0x04` SCANLINE/RCR, bit 3 = `0x08` VBLANK), SR, AR, display gates. `GetState()` dereference is non-invasive; `ReadRegister` is destructive. Stale-RMW hazard (`m_read_buffer` only VRAM-updated) forces work-RAM shadow instead of port RMW (same hazard that grounds `$2C`/`$2E`/`$33` family) | REFERENCE_ONLY, PENDING for license. Hardware facts only — NOT `$2B` BIOS facts. No code copied. Bit 2 = `0x04` assignment + CR-vs-RCR distinction + stale-RMW hazard ground the D2 bit operation and shadow discipline; they do NOT ground a BIOS calling convention |
| S5b | In-tree measured I/O practice (MIT): `src/overrides/slot_2E.s` (EX_BGON), `src/ex_dspoff.s`, `src/ex_irqon.s` (D2, PR #840), harnesses `geargrafx_dev_cr.py`, `geargrafx_*_probe.cpp` | VDC window discipline (MPR0 `$FF` + `SEI` + absolute + restore + AR left at 5; max-push 3), shared `$2502` (CR-low image) / `$2503` (`$A5` iff seeded) / `$2504` (reserved) shadow family with single-writer + caller-owns-sync discipline, C=0 ok / C=1 reject on unseeded shadow (PROJECT_SPEC_ONLY family convention), `$20F3`/`$20F7` untouched + asserted UNCHANGED, CPU I net-preserved, IDR/IRR untouched, SR never read, no zero-page/VRAM/CD/BURAM access | PROJECT_SPEC_ONLY. Discipline reusable for `$2B` (same shadow, same window, same fail-closed reject). `$2C` D2 body (44-byte pinned construction, no VDC read opcode) is the direct template; `$2B` differs only in the mask operation (`AND #$FB` vs `ORA #$08`). No IRQ-enabling precedent exists in-tree; none is claimed for `$2B` |
| S5c | NEC HuC6280/HuC6270 manuals + PCE IRQ-management public notes (UNPINNED) | Claimed register/sequencing notes (CR/RCR map, I flag, IDR/IRR bits, VDC IE sources). In-tree repetitions are NOT independent confirmations | HYPOTHESIS. Downgraded: MUST NOT ground implementation until pinned |
| S6 | In-tree DEV precedents: `$23` DEV, `$2E` CR-family, `ex_dspoff.s`, `$4A` preservation, `$25`/`$24`/`$36`/`$35` B1-analogous + harnesses, `$2C` D2 (PR #840) | Project-own contracts only. Reusable as discipline (stack accounting, no-RMW shadow practice, MPR1/`SEI`/absolute window, RAM-diff scoping, 44-byte structural pin, single-byte mutant体系, callee-window write history, SP/MPR1 pre-validation), NEVER as `$2B` proof. `$2C` D2 is the closest precedent (same family, same shadow, same window, same C convention) but its SEMANTICS (bit 3 set) are never transferred to `$2B` (bit 2 clear) | PROJECT_SPEC_ONLY. `$2C` skeleton reusable as NO-guess template ONLY; its bit operation is replaced, never reused |
| S7 | Sanitized G5 observations (Owner-permitted, structural only; PR #840 ExpF with APICALL cap-32 + non-invasive VDC snapshot `vdc_snapshot=1`) | CUE[0] ExpF `$02->$33->$4A->$23->$25->$24->$36->$35->$2C->$2B` halt (10 APICALLs, X=`$2B`=43, entry `$E081`, all table JSR delta 2, in_table, MPR1 `$F8`, SP balanced, stack MATCH, cold/warm identical, 80M budget, no truncation, dropped 0). `$2C` entry call-state OBSERVED once per cycle (A=7/X=0/Y=0/P=`$06`/I=1, IDR=0, IRR=0, AR=2/CR=0/RCR=0/SR=0); `$2B` entry P=`$07` (Carry meaning UNKNOWN). CUE[1] ExpF unchanged `$1E->$4A->$02->$02->$21->$1C` halt (6 APICALLs, X=`$1C`). Media invariant pre/post identical (counts only). BIOS image hashes recorded privately only | OBSERVED (call-state + VDC snapshot only, two games, single path each). No memory/sector/content data. Genuine `$2B` (and everything upstream genuine) UNKNOWN/BLOCKED. `$2B` stub halt proves reachability, not behavior; CR=0 throughout proves VDC IE/display gates off on entry along the observed path, not a `$2B` contract |
| S8 | Ledgers: `spec/api_slots.json` ids 42/43 = `EX_RCRON`/`EX_RCROFF`, `spec/api_contracts.json` slots 42/43 (`EX_RCRON 0xE07E` / `EX_RCROFF 0xE081`, HYPOTHESIS, all UNKNOWN), `spec/dev_implementations.json` ids 42/43 (`NOT_STARTED`), `tools/build_rom.py` 81-slot JSR table (`$E000+3*slot`, stub `LDX #slot; JMP api_unimplemented`) | Current state: hypothesis, zero behavior, no DEV override, table mechanics project-verified | Project record, unchanged by this doc (implementation PR updates `dev_implementations` experimental-only, VERIFIED unchanged). `$E081 = $E000+3*$2B` is layout fact, not ABI proof |

Gate: S1-S4 REFERENCE_ONLY/PENDING; S5a test-dependency/REFERENCE_ONLY; S5b PROJECT_SPEC_ONLY; S5c HYPOTHESIS; no file/fragment enters firmware. (`src/overrides/slot_44.s` is MA_SQRT for *hex* slot `$44` = decimal 68 and is unrelated to decimal-43 `$2B`.)

## 3. ABI contract table (genuine `$2B`)

| # | Item | Genuine `$2B` | DEV `$2B` (D2, this task) | Disposition |
|---|------|---------------|---------------------------|-------------|
| 1 | Name/slot/entry | `EX_RCROFF` / `$2B` / `$E081` candidate | `ex_rcroff_entry` experimental body, `$E081` JMP→body→RTS caller+3 | S1/S2/S3 name+entry HYPOTHESIS as callee; S8 HYPOTHESIS. SOURCE_CONFIRMED only as caller equate (S3). `$E081` layout project-verified, not ABI proof |
| 2 | Generations | Bodies differ per generation (S2 pattern); JSR stable | N/A (single D2 body, synthetic only) | Single-behavior assumption FORBIDDEN for genuine; D2 is explicitly single-image experimental, never a generation claim |
| 3 | Pair `$2A` relation | Adjacent names + symmetric Zeograd one-liners (set vs clear bit 2) suggest a pair, but do NOT prove symmetric contracts, shared flags, shared mirrors, or call order | `$2A` not co-implemented (separate API, separate follow-up) | S1 adjacency is grouping + hint, never behavior. Symmetry MUST be proved per-field; this task proves only `$2B` bit-2-clear with bit-3 preservation, never `$2A` behavior |
| 4-6 | Inputs A/X/Y / work-RAM | UNKNOWN (S1 IN N/A for `$2B`; S3b shows HuC startup sets up no regs for either RCR API) | D2 reads no A/X/Y (preserved exactly); work-RAM owned shadow `$2502`/`$2503` (caller-seeded, MPR1=`$F8` precondition validated by harness BEFORE JSR) | Genuine inputs UNKNOWN. D2 no-use is PROJECT_SPEC_ONLY scope choice (Zeograd IN N/A = REFERENCE_ONLY description, HuC no-setup = caller-side OBSERVED practice; genuine requirements stay UNKNOWN, A-bitmask/enum UNGROUNDED). Single-callsite entry values (none observed for `$2B` ok path; `$2B` stub entry P=`$07` once) are OBSERVED call-state, never a contract |
| 7 | CPU I flag | UNKNOWN (genuine effect on I unproven: SEI vs preserve vs conditional all ungrounded) | Exit I = entry I (window `SEI` restored via `PLP`; no net CLI/SEI) | Names alone MUST NOT authorize CLI/SEI. OBSERVED I=1 at `$2C` entry once is not a `$2B` precondition |
| 8 | IRQ controller (IDR/IRR) | UNKNOWN (which sources, mask vs clear, pending handling all unproven) | Untouched (asserted UNCHANGED) | Hardware locations per S5a only; OBSERVED 0/0 at `$2C` entry once is not a `$2B` contract |
| 9 | VDC enables/status (CR IE, SR, RCR) | UNKNOWN (which IE bits, value domain, shadow/mirror, SR-clear side effects, RCR handling, timing all unproven; Zeograd “clears bit 2” is a single reverse-engineering hint, not a calling convention) | D2 implements ONLY CR R05 low `& $FB` (clear bit 2, keep every other CR bit incl. bit 3), CR high untouched, RCR R06 untouched, `$20F3`/`$20F7` untouched + asserted UNCHANGED, SR never read (read-to-clear avoidance) | S1 hint alone never authorizes a store; S5a bit-2 assignment (`0x04` SCANLINE/RCR) + stale-RMW hazard + S5b shadow/window discipline jointly ground the D2 bit operation as PROJECT_SPEC_ONLY. Genuine VDC contract stays UNKNOWN/BLOCKED. CR-bit-2 clear ≠ R06 write (S5a register map) |
| 10 | MPR precondition | UNKNOWN (genuine); no OBSERVED `$2B` ok-path MPR | D2 requires MPR1=`$F8` (caller obligation, harness-validated BEFORE JSR; body defines NO in-body gate, same family reason: JSR already wrote return bytes) | Table mechanics project-verified (S8) |
| 11 | Stack/return | UNKNOWN | JSR `$E081` tail → body → RTS caller+3; max 3 transient below JSR return; caller SP minimum `$05`; balanced; transient bytes are caller-stack only | PROJECT_SPEC_ONLY (family discipline, S5b/S6) |
| 12 | Preserved/clobbered (A/X/Y/P) | UNKNOWN | A/X/Y exact; P latch-exact except C per D2 convention (T=1 out of domain, pinned-core T-consumption) | PROJECT_SPEC_ONLY. B1 latch-exact technique reused, never `$2B` proof |
| 13 | Flags (Carry/Z/N/V) | UNKNOWN (no public Carry contract; Zeograd OUT N/A) | C=0 ok / C=1 reject on unseeded shadow (PROJECT_SPEC_ONLY family convention, SIMULATED label, never genuine) | Zeograd OUT N/A defines no genuine Carry; D2 convention belongs to CR shadow family (`$2E`/`$33`/`$2C`) and MUST NOT be cited as `$2B` semantics |
| 14 | IRQ timing/pending/handler/VBlank | UNKNOWN (sync vs async, wait vs enable-and-return, handler setup all UNPROVEN) | No wait, no handler, immediate CR rewrite (effective next horizontal period per pinned-core HW note only) | Natural time-passage IRQ events vs implementation side effects must be distinguished in any future test |
| 15 | Errors/rejects | UNKNOWN (no public error codes) | Unseeded shadow (`$2503 != $A5`) → reject: no VDC write, no shadow/sentinel write outside transient stack, C=1, A/X/Y/MPR/flags preserved | PROJECT_SPEC_ONLY fail-closed (synthetic oracle: unseeded `$34` stays `$34`, C=1) |
| 16 | Call order | UNKNOWN genuine; OBSERVED `$2C->$2B` adjacency on one commercial path (SIMULATED upstream) | Synthetic `$33->$2C->$2B` chain proves routing/resumption + register/stack preservation (OBSERVED synthetic); `$04->$0C->$08` is EXPECTED from the D2 contracts (NOT_RUN on the chain — no intermediate/final CR or shadow measurement); bit-3 preservation on the chain is unproven (individual `$2B` VDC tests prove bit-3 preservation synthetically) ; never genuine ordering | One-game order under experimental (SIMULATED) upstream returns. Genuine ordering UNKNOWN. Synchronized intermediate CR/shadow chain proof is deferred to #843 |

Verdict: genuine `$2B` UNKNOWN in every behavioral field. The D2 body below
is a PROJECT_SPEC_ONLY experimental scope choice, never a genuine claim.

## 4. D1/D2/BLOCKED selection (`$2B`)

- **D1 (genuine functional implementation): BLOCKED.** S1 one-liner + S3 caller
  practice + S5a hardware locations do NOT constitute a BIOS calling
  convention for any of: A/X/Y inputs, work-RAM mirrors (`$20F3`/`$20F7`
  address/value/order), CPU-I action, IDR mask, SR discipline, RCR handling,
  handler/vector setup, timing, errors, generation differences. No D1 body,
  no genuine ABI promotion, no VERIFIED change in this task.
- **D2 (limited functional experimental): SELECTED.** Acceptance checklist:
  1. Concrete public bit-operation description (S1 clears bit 2 + mirrors,
     IN/OUT N/A).
  2. Independent hardware bit assignment (S5a CR bit 2 = `0x04` SCANLINE/RCR,
     CR=R05 vs RCR=R06 distinction, R[5] mask `0x1FFF`, stale-RMW hazard →
     shadow justification, VBLANK path for bit-3 preservation).
  3. Symmetric-pair hint without symmetric implementation (`$2A` set vs `$2B`
     clear, `$2C`/`$2D` bit-3 pair as 3-point consistency; `$2A` not
     co-implemented, `$2C` bit 3 never cleared by this body).
  4. Caller-side no-setup practice for both RCR APIs (S3b, REFERENCE_ONLY).
  5. Falsifiable synthetic oracles (seeded `$04->$00`, `$0C->$08` coexistence,
     `$56->$52` mixed, `$FF->$FB` boundary, `$08->$08` already-disabled,
     unseeded `$34->$34` reject; bit-3 preservation (individual VDC matrix,
     OBSERVED synthetic); CR-high/RCR preservation; mask mutants;
     window/MPR/AR/SP; RAM-diff; `$33->$2C->$2B` routing chain cold/warm
     (routing/resumption/register preservation OBSERVED; chain CR/shadow
     transitions NOT_RUN — no intermediate/final CR or shadow assertion);
     VDC GetState snapshots with synchronized shadow (individual matrix,
     OBSERVED synthetic). Chain `$04->$0C->$08` is EXPECTED, not measured.
  6. Non-target preservation verified (other CR bits incl. bit 3, CR high,
     RCR, `$20F3`/`$20F7`, CPU I, IDR/IRR, SR, VCE/VRAM/SATB/zp/BURAM/CD).
  7. Explicit opt-in isolation (`cd_2b="stub"` default, stub byte-identical,
     normal DEV unchanged, `$2B`-only + `$2C+$2B` bounded lattice, no
     power-set growth, full ExpG refused fail-closed on BOOT overflow — see §6).
  8. Existing stub/B1/DEV behavior preserved (`$2B` stub default unchanged,
     other APIs untouched).
  9. Genuine ABI BLOCKED maintained (VERIFIED 0/81, `release_eligible=false`,
     DEV_CALLABLE unchanged).
  10. G5 diagnostic value (latest NEXT `$2B` stub halt; `$2C->$2B` chain proves
      resumption + bit interaction on the shared shadow).

## 5. Implemented / explicitly not implemented (D2)

Implemented (`src/ex_rcroff.s`, 44-byte pinned body, same shape as `$2C`
with `AND #$FB` replacing `ORA #$08`):

- Entry `JSR $E081` → table JMP → body → RTS caller+3; table stays JMP.
- Caller obligations: MPR1=`$F8`, work-RAM stack, caller-masked IRQs (same
  sibling assumptions). MPR1!=`$F8` is OUT OF CONTRACT (harness-validated
  BEFORE JSR, no in-body gate).
- Ok (seeded `$2503==$A5`): `SEI` → MPR0=`$FF` window → `ST0=$0000` `$05`
  (AR=5) → `$2502 & $FB` → shadow publish → `ST1=$0002` (CR low, high
  untouched) → MPR0 restore → A restore → `PLP` → `CLC` (C=0). A/X/Y exact,
  MPRs exact, N/Z/V/D/I/T latch-exact (T=1 out of domain).
- Reject (unseeded): no VDC write, no shadow/sentinel write outside transient
  stack, `SEC` (C=1), A/X/Y/MPR/flags preserved. Caller seeds `$2502`/`$2503`
  before first call (harness does; commercial game does not → reject expected).
- Sync: caller owns synchronization (same single-writer family `$2E`/`$33`/
  `$2C`/`$2B`); no direct CR writes while using the family; re-seed after any
  bypassing write. Out-of-sync is out of contract.
- Competing writers audited: `EX_SETWRT` touches MAWR (R00) only, never CR;
  only family + caller seeding write `$2502`/`$2503`.

Explicitly NOT implemented (guessing forbidden):

- `$20F3`/`$20F7` mirrors: **no**. Reason: Zeograd register table
  (`$20F3`=current VDC reg) vs one-liners (updates `$20F3`/`$20F7`) unresolved
  (same conflict as `docs/61-dspoff-contract.md` S1), value/order/atomicity/
  existing-mirror behavior UNKNOWN, family precedent touches neither.
  Synthetic tests assert UNCHANGED.
- CPU-I action (`CLI`/`SEI` net effect): **no** (exit I = entry I).
- IDR/IRR mask/pending: **no**.
- SR reads: **none** (read-to-clear avoidance; `GetState()` only in probes).
- VCE/VRAM/SATB/BURAM/joypad/CD/ADPCM/zp: **no**.
- `$2A` set-side: **no** (separate API, separate follow-up; 1-API rule).
- `$2C`/`$2D` co-implementation: **no** (chain uses separate `$2C` body,
  never merged).
- RCR R06: **no** (CR bit-2 clear ≠ R06 write; R06 asserted UNCHANGED).
- Handlers/vectors/VBlank wait/error codes: **none** (OUT N/A; C convention
  is PROJECT_SPEC_ONLY).

## 6. Wiring / variant lattice / BOOT budget (fail-closed)

- New flag `cd_2b="stub"|"experimental"` (default stub). Normal DEV ROM
  unchanged, stub-default byte-identical (pinned `9a7a2c00…bc6` preserved).
- New windows: `unified_api_windows_2b` (`$2B`-only) and
  `unified_api_windows_2c_2b` (`$2C+$2B` chain). Full ExpG windows
  (`unified_api_windows_4a_25_24_36_35_2c_2b`) defined for documentation but
  refused at build time (see below). No power-set growth.
- Supported combos: `$2B`-only (unit) + `$2C+$2B` (chain). Existing `$2C`-only
  + full ExpF preserved. All other `cd_2b="experimental"` combos refuse
  fail-closed. Existing `$4A`/`$25`/`$24`/`$36`/`$35` partial combos unchanged
  (still `cd_2b="stub"`).
- **BOOT-budget finding (OBSERVED build failure, not a guess):** ExpF
  (full `$4A+$25+$24+$36+$35+$2C`) uses BOOT `0xAAC` + CODE `0x531` =
  `0xFDD` (4061 bytes) ending `0xFFDC`, slack 25 bytes to `$FFF6`. Adding the
  `$2B` body (44 bytes) needs 4105 bytes, overflowing the `$F000-$FFF5`
  budget by 19 bytes (`ld65: Segment ‘CODE’ overflows memory area ‘ROM0’ by
  9 bytes` on the attempted full-ExpG link; map math `0xAD8+0x531=0x1009`
  confirms 19-byte overflow). The builder therefore refuses full ExpG
  fail-closed with an explicit BOOT-overflow message until a BOOT-saving
  follow-up (CR-family commonization, separate task per §D2 discipline)
  lands. The `$2C+$2B` chain (BOOT `0xA75+0x37` + CODE `0x531` = 4048 bytes,
  slack 38) fits and proves routing/resumption for the `$2C+$2B` pair;
  `$04->$0C->$08` is the expected transition (NOT_RUN on the chain — no
  intermediate/final CR or shadow measurement). Synchronized intermediate
  CR/shadow chain proof is deferred to #843. This is a technical BLOCKED,
  not a contract BLOCKED; it does not affect the D2 functional proof
  (individual VDC matrix) or the stub-default guarantee.
- ROM 256 KiB, `$FFF6`/`$E800` boundaries, BOOT-span (`entry+44 ≤ $FFF6`),
  exact-JMP targets, window subset/cardinality, overlap refusal, BOOT span,
  input-ROM invariance, invalid-combo fail-closed, symlink guards preserved.
- `spec/c3_static_contracts.json` classifies `src/ex_rcroff.s` as
  experimental-only fixture (other ledgers unchanged, VERIFIED unchanged).

## 7. Commercial ExpF→ExpG comparison (BLOCKED, not run here)

Owner’s explicit commercial permission for *this* task was not granted
(BLOCKED_PERMISSION for new commercial runs; prior-task permissions do not
carry over). In addition, full ExpG does not link (BOOT overflow, §6), so a
full ExpF→ExpG commercial comparison is doubly BLOCKED (permission +
environment) and is NOT claimed here.

Prior OBSERVED baseline reused (PR #840, sanitized structural only, no new
`./rom` access):

- ExpF CUE[0]: 10 calls `$02->$33->$4A->$23->$25->$24->$36->$35->$2C->$2B`
  stub halt (cold/warm identical, no truncation). `$2C` entry P=`$06`/I=1,
  AR=2/CR=0/RCR=0/SR=0; `$2B` entry P=`$07` (Carry meaning UNKNOWN); CR=0
  maintained (reject-consistent INFERRED, shadow value UNKNOWN because
  commercial WRAM `$2503` was never read — RAM-clean kept). CUE[1]: 6 calls
  `$1E->$4A->$02->$02->$21->$1C` halt (unchanged).
- Title arrival: NOT observed (G5_OBSERVED=false).

Interpretation rules (unchanged from `$2C`): commercial NEXT arrival ≠
functional success (reject path possible); functional success is synthetic
GREEN only; shadow unseeded is INFERRED from body分岐 + observation
consistency, never OBSERVED (no commercial WRAM read).

## 8. Open issues

- U1. Genuine `$2B` inputs/outputs/flags/CPU-I action: UNKNOWN.
- U2. IDR mask vs IRR pending handling: UNKNOWN.
- U3. VDC CR target bits/values/handshake/shadow (`$20F3`/`$20F7`) + RCR
  distinction: Zeograd hint only, UNKNOWN.
- U4. SR read-clear discipline: UNKNOWN (hardware fact known, BIOS link unknown).
- U5. Handler/vector/BIOS-flag setup: UNKNOWN.
- U6. Timing (sync/async, wait vs fire-and-continue): UNKNOWN.
- U7. Relation to `$2A` (set-side) + `$2C`/`$2D` (bit-3 pair) + `$2E`–`$33`
  display pair: names adjacent, contracts unlinked (bit-2 vs bit-3 vs
  `$20F3`-only is a hint pattern, never a contract).
- U8. NEXT after `$2B`: UNKNOWN (stub halt; no execution past `$2B` observed).
- U9. A/X/Y domain (incl. `$2B` entry P=`$07` meaning): UNKNOWN (no explicit
  setup observed; residual hypothesis, not proof; bitmask/enum UNGROUNDED).
- U10. Second-core cross-check: NOT_RUN (Geargrafx-only).
- U11. `$20F3`/`$20F7` mirror values: UNKNOWN (no WRAM reads by design).
- U12. Full ExpG BOOT fit: BLOCKED_ENVIRONMENT (19-byte overflow; needs
  CR-family commonization follow-up, separate task).
- U13. Commercial ExpG observation: BLOCKED_PERMISSION + BLOCKED_ENVIRONMENT
  (no new run here).

## 9. Acceptance criteria

- (a) This contract doc merged with S1–S8, §3 UNKNOWN table + §3 pair delta
  (no symmetric implementation), §4 D2 selection (10-point checklist), §5
  implemented/not-implemented split + RCR-vs-CR-bit distinction, §6 wiring +
  bounded lattice + BOOT-overflow evidence + fail-closed full-ExpG refusal,
  §7 commercial BLOCKED (permission + environment) + prior baseline reuse
  without new `./rom` access, §8 U1–U13.
- (b) `src/ex_rcroff.s` 44-byte pinned body (PHP/PHA…AND #$FB…CLC/RTS),
  `cd_2b` opt-in, stub-default byte-identical, BOOT-span + window + overlap +
  symlink guards, `c3_static_contracts.json` fixture classification.
- (c) Host suite `tests/host/test_build_g5_2b_host.py` ALL GREEN (stub
  preserved + pinned baseline + `$2B`-only + `$2C+$2B` chain + invalid
  refusals incl. full-ExpG overflow + symlink + redirect + windows +
  spec). Existing `$2C`/family host suites still GREEN (no regression).
- (d) Real-core suite `tests/integration/test_g5_rcroff_2b.py` ALL GREEN on
  pinned core: stub RED baseline, ok (`$00->$00`, `$04->$00`, `$F4->$F0`,
  `$0C->$08` coexistence, `$56->$52` mixed, `$FF->$FB`, `$08->$08`
  idempotent, C0/C1 × ok/reject), reject (`$00`, `$F0` ignored, `$34`
  bit2-set), SP/MPR1, VDC synchronized (`$2502`/`$2503` + real CR/RCR/AR
  boundary proof, high/RCR/other-regs preserved, AR=5 ok / entry-AR reject,
  frozen-clock SR invariance), `$33->$2C->$2B` routing chain (routing +
  resumption + register/stack preservation OBSERVED; `$04->$0C->$08`
  EXPECTED, chain CR/shadow transitions NOT_RUN, bit-3 on chain unproven —
  individual VDC matrix proves bit-3 preservation), mutants RED (wrong-bit/destroy/noop/
  novdc/nopublish/bypass/CLC/SEC/MPR + write-history + cavity + buffer
  negatives), cold/warm equality, `make test`/`make check`/`git diff --check`
  clean, Required CI GREEN. Pending-SR survival + IRQ timing NOT_RUN by
  design (documented limits, never PASS).
- (e) Independent review with zero unresolved CRITICAL/MAJOR/MEDIUM, no merge
  without Owner approval, PR body `--body-file` with HEAD/tests/CI/review/
  mutation/cold-warm/single-run/frozen-clock/commercial-HEAD/OBSV-INFR-UNKN/
  genuine-BLOCKED/Issue-autoclose/hex-expansion audit (PR #839/#840 lesson).
- (f) Safety gates unchanged (JSR 0/81, VERIFIED 0/81,
  `release_eligible=false`, `$2B`/`$2C`/`$2D`/`$2A`/`$35`/`$1C` BLOCKED_ABI,
  DEV invariance, SIMULATED vs genuine vs synthetic vs real-core vs
  commercial strictly distinguished). G5_OBSERVED stays false.

NOT a compatible System Card BIOS. Geargrafx observations are not hardware proof.

---

*Provenance: original research + original code (MIT). No proprietary firmware/game/reference content.
Pinned: Geargrafx `b49ae82a`, cc65 `71746c8` (`tools/dependencies.json`).
Zeograd/Stum/HuC/Hugo are REFERENCE_ONLY (re-fetch dates/sizes/SHAs in §2, no text reproduced).*
