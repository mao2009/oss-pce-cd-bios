# G5 CR shadow initialization contract — independent technical investigation (P1)

Status: **technical investigation, no implementation. NOT a compatible System Card BIOS.
NOT genuine ABI. NOT DEV_CALLABLE. `release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81 (unchanged by this document).
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, saves appear here.
`./rom/` was not accessed in any form for this document (no existence check, listing,
or read); commercial observations cited reuse only the sanitized structural record
from PR #840 (slots, entry PCs, regs, VDC ints, MPR/SP, halt, cold/warm, truncation),
already Owner-permitted and stored outside the repo. No new commercial measurement
was performed in this task (BLOCKED_PERMISSION for new runs; synthetic only here).
No commercial WRAM/VRAM/SATB/framebuffer was acquired at any point.

Branch basis: `f8acbfce` (origin/main at task start; PR #840 `$2C` D2 integrated);
this revision merges origin/main `c30146e1` (PR #841 `$2B` D2 integrated) via normal
merge (docs-only PR diff preserved — see §11). PR #841 evidence is pinned to its
own HEADs (`51b7cd55` local 47-test GREEN, `145b182e` review-fix, merge `c30146e1`,
CI per GitHub Actions), never presented as this P1 worktree's own runs.
Related: #802 (G5 path), #635 (blocker measurement), #54 (`$2C`), #53 (`$2B`,
companion P0 implementation, merged `c30146e1`), #52 (`$2A`), #55
(`$2D`), #61 (`$33`), #56 (`$2E`), #84 (`$4A`), #38 (`$1C` BLOCKED),
#843 (BOOT commonization, F1 — existing issue, linked not duplicated),
#844 (shadow/BUF overlap + marker validity follow-up, F2 — created by this task).
Tracker #3.

Required outcome (task Phase N): purpose, HEAD, provenance, writer/reader list,
boot sequence, RAM ownership, init responsibility, existing-implementation problem,
synthetic verification method, measured results,方式 comparison, recommended way,
implementation preconditions, open issues, BLOCKED factors, follow-up tasks.
This is an independent PR; no commercial seed implementation is smuggled in.
If an existing related PR already covers this, that PR takes precedence (none
found at task start; open PRs #823 (draft, unrelated CD_FADE) + follow-ups #831/
#833/#835/#836/#838 do not overlap).

## 1. Purpose and scope

P0 implements `$2B` EX_RCROFF D2 (bit-2 clear via shared `$2502` shadow) on the
assumption the caller seeds `$2502`/`$2503` and never writes VDC CR outside the
family. Commercial ExpF shows CR=0 maintained through `$33`/`$2C` and `$2B`
stub halt — consistent with, but not proof of, an unseeded shadow (marker/shadow
values UNKNOWN; `$33` ok-write-zero also yields CR=0; a caller may rewrite CR
between boundaries; see §5). This P1 asks independently: who owns
`$2502`/`$2503`/`$2504`, who (if anyone) initializes them before the commercial
caller runs `$33`/`$2C`/`$2B`, how CR and shadow could be kept synchronized, and
under what conditions the CR family (`$33`/`$2C`/`$2B` etc.) could actually
function in a commercial environment. A central finding of the independent
review (`6083530888`): the PUBLIC loader staging buffer BUF `$2200`–`$29FF`
definitely overwrites `$2502`/`$2503`/`$2504` (offsets `$0302`/`$0303`/`$0304`)
via indirect fill + READ(6) transfer — so "nobody seeds the shadow" is an
incomplete conclusion. Intentional firmware CR-shadow init is still absent,
but unintentional staging reuse exists (§§2–4). No code change is proposed
here; implementation (if any) belongs to a follow-up Issue after the
preconditions in §9 are met (#843 for BOOT commonization, #844 for
shadow/BUF overlap + marker validity).

## 2. Writer / reader inventory (`$2502`/`$2503`/`$2504`, `$20F3`/`$20F7`)

Method: `grep -rn` for `$2502`/`$2503`/`$2504`/`20F3`/`20F7` across `src/` and
`tests/integration/ipl_loader_rom.s` (including `src/overrides/`, `src/*.s`,
`src/ipl_loader_g5_glue.s`, `src/experimental_bios_boot.s`, `src/boot.s`,
`src/reset_entry.s`, `src/dev_boot_internal.s`, `src/cd_*.s`) plus
symbol/offset/macro/indirect review. Direct addressing is the CR-family norm
(absolute `lda $250x`/`sta $250x` with MPR1=`$F8`), but direct-address grep
alone is INSUFFICIENT for range writes: the PUBLIC loader staging fill
(`STA (ptr),Y`, `ptr=$04`, 8 pages) and READ(6) transfer target the whole
BUF `$2200`–`$29FF`, which contains `$2502`/`$2503`/`$2504` at offsets
`$0302`/`$0303`/`$0304` — a definite indirect writer the direct grep misses
(review `6083530888` MAJOR; see `read_sector`, `tests/integration/ipl_loader_rom.s:645ff`).

| Address | Writers (in-tree) | Readers (in-tree) | Notes |
|---------|-------------------|-------------------|-------|
| `$2502` (CR-low image) | Intentional: `src/overrides/slot_2E.s` ok path (`sta $2502` after mask `ORA #$80`); `src/ex_dspoff.s` ok path (`AND #$3F`); `src/ex_irqon.s` ok path (`ORA #$08`); `src/ex_rcroff.s` ok path (`AND #$FB`); synthetic guest seeding (`sta $2502` in G5/CR harness payloads before first call — caller-side test setup, never firmware). Unintentional staging: PUBLIC `tests/integration/ipl_loader_rom.s:read_sector` — `$5A` fill of BUF `$2200`–`$29FF` via `STA (ptr),Y` (offset `$0302`) + every READ(6) sector transfer into BUF (`$2502` overwritten with sector bytes; value = staging residue, never a CR image) | Same four bodies' ok-path `lda $2502`; harness result dump (`lda $2502` → `res_base`); VDC probes (`GetState` only, never `$2502`) | CR-family single-writer discipline is a caller-owns-sync *convention* (PROJECT_SPEC_ONLY), not an ownership proof: the loader staging demonstrably reuses the addresses. Marker `$2503` never written by any body (publish-only `$2502`). `$2504` is family-reserved but loader-overwritten in practice (reservation ≠ protection — see below) |
| `$2503` (marker, `$A5` iff seeded) | Intentional: synthetic guest seeding only (`lda #$A5`/`lda #$00` → `sta $2503`); NO BIOS/reset/IPL/glue/body intentional writer. Unintentional staging: PUBLIC loader BUF fill (`$5A` at offset `$0303`, not `$A5`) + every READ(6) sector transfer (byte at `$0303` = sector data — MAY be `$A5` depending on disc bytes; commercial values UNKNOWN, never read). Unobserved: commercial caller writes; warm residual | `slot_2E`/`ex_dspoff`/`ex_irqon`/`ex_rcroff` entry gate (`lda $2503` + `cmp #$A5` + `beq @have`, reject otherwise) | `$A5` records nothing about sync: it may mean seeded-by-harness (in-contract), sector-derived `$A5` (staging residue), warm residual, or caller data. None implies CR==shadow. False-ok risks: sector-derived `$A5` + stale shadow; warm residual; caller data (see §5 C2). The old "~1/256 uniform" figure is withdrawn except as an explicitly conditional model (see §4) |
| `$2504` (reserved, CR-high image candidate) | Family-reserved (never a body/guest target). Loader staging overwrites offset `$0304` in practice (fill `$5A` + sector bytes) | None | Reservation is a family convention, not protection: the loader demonstrably reuses the address. A future high-byte contract must resolve ownership/lifetime first (§9) |
| `$20F3` / `$20F7` (Zeograd claimed mirrors: Current VDC reg / Value of reg #5) | In-tree CD reuse (DIFFERENT meaning, must not conflate): `src/cd_base_2base.s` (`lda $20F3`/`lda $20F7`/`sta $20F3`/`sta $20F7` as block/window scratch); `src/cd_read_link.s` (`BLK_MID=$20F3`, `WIN_HI=$20F7` as 24-bit block/window operands); `src/cd_read_commercial.s` treats `$20F2`–`$20F7` as untrusted caller RAM (never trusted, `$20F5`–`$20F7` never read) | Same CD files; NO CR-family body reads/writes `$20F3`/`$20F7` (family precedent touches neither; synthetic tests assert UNCHANGED) | Genuine mirror existence/value/order/atomicity UNKNOWN (Zeograd register table vs one-liners unresolved, `docs/61-dspoff-contract.md` S1). In-tree CD addresses collide nominally with the claimed mirror addresses but are a separate project-own protocol, never evidence for the mirror |
| `$2500`/`$2501` (EX_RND state) | `src/overrides/slot_3E.s` (LFSR) | Same | Different family, no conflict with CR shadow (addresses distinct, no shared gate) |

Intentional firmware shadow-initialization writers: none (distinct from the four CR-family ok-path updates listed above). The audited paths below contain
no *intentional* CR-shadow init (no `$2502`/`$2503` seeding store), but the PUBLIC
loader contains a definite *unintentional* range writer (see table):
`src/boot.s` (stop loop only; no RAM store), `src/reset_entry.s` (MPR0/`$FF`,
MPR1/`$F8`, SP/`$FF`, no RAM store), `src/dev_boot_internal.s` (MPR/SP +
`cd_media_state`/`cd_entry` handoff, result blocks `$2300`/`$2800`/`$3000`/`$3F00`/
`$3F40`; `$2502`-family untouched), `src/experimental_bios_boot.s` (MPR/SP + XSEL
validation + `cd_entry_selected` handoff; `$33` body included via `.include` but
not executed at boot), `src/ipl_loader_g5_glue.s` (XSEL record + `$33` body
include, no execution at build time), PUBLIC `tests/integration/ipl_loader_rom.s`:
MPR0/`$FF`, MPR1/`$F8`, SP/`$FF`, RB/`$2F00` init, INFO/BODY load to IPLSTA,
MPR2..6 setup, `jmp` IPLJMP — never *intentionally* seeds `$2502`/`$2503`/
`$2400`/`$20F2`–`$20FF` (payload seeds), BUT `read_sector` indirectly writes
BUF `$2200`–`$29FF` (fill `$5A` + sector transfer), which contains
`$2502`–`$2504` (offsets `$0302`–`$0304`). No BIOS/reset/IPL/glue/body
*intentional* shadow writer exists; one *unintentional* loader range writer
demonstrably exists. `src/cd_base_proto.s`/`src/cd_read_link.s`/
`src/cd_read_commercial.s` (block/window protocol; `$20F2`–`$20F7` as
untrusted caller RAM in the commercial helper, never trusted) do not target the
CR shadow. `src/ex_memopen.s` (preservation + MPR1=`$F8` entry gate with
refusal `SEC`, NOT a B1 no-touch scaffold — 6 stack bytes precede the gate;
see E3), `ex_scrmod.s`/`ex_dotmod.s`/`ex_satclr.s`/`ex_sprdma.s` (B1/B1-analogous
preservation scaffolds, no work-RAM write outside transients) do not target the
CR shadow. `src/ex_setwrt`-equivalent (`slot_3A`) touches MAWR (R00) only,
AR=0, never CR.

Competing VDC users audited: `EX_SETWRT` (slot `$3A`) touches MAWR (R00) only,
leaves AR=0, never CR. `$23` EX_SCRSIZ (slot `$23`, DEV) performs a real VDC
write to MWR (R09, AR=9, low=A/high=`$00`) — NOT MAWR, and NOT CR (MWR≠CR;
conclusion "`$23` never writes CR" stands on the audited implementation).
No other in-tree routine writes VDC CR. A commercial caller direct port write
(ST1/ST2 to R05) outside the family would silently desync the shadow; no
mechanism can observe it (see §6 L4).

Synthetic-only seeders (must not be mistaken for BIOS init): dedicated VDC
probes initialize real CR low/high and an RCR sentinel through the MPR0=`$FF`
window, seed `$2502`/`$2503`, and verify synchronization at the JSR boundary.
Return-probe and routing-chain payloads seed shadow state but do not establish
that same real-CR boundary proof; see §7 for the fixture-specific evidence.
These are caller-side test setups, never firmware initialization.

## 3. Boot sequence (code-grounded, P1 worktree)

Order below follows the actual code (not a generic BIOS model). “CR known?”
means the real VDC CR value; “shadow?” means `$2502`/`$2503` determinability;
“sync?” means CR==shadow provability. IPLJMP target/time is caller-controlled
(PUBLIC INFO `IPLSTA`/`IPLJMP`, validated `$3000`–`$E000`).

| # | Point (code) | MPR / SP / IRQ | WRAM / VDC writer | CR known? | Shadow? | Sync? |
|---|--------------|----------------|-------------------|-----------|---------|-------|
| 1 | Core reset (hardware) | I=1, D=0 (spec `rom-contract.json`); MPR7=0 | None (project does not model power-on RAM; pinned core RAM nondeterministic except seeded bytes) | UNKNOWN (pinned core R[5] reset value is a core fact, not a BIOS contract; commercial VDC reset is outside this repo) | UNKNOWN (no value) | No |
| 2 | BIOS reset vector (`src/boot.s` normal DEV: stop loop; `src/reset_entry.s` minimal: `sei/cld/MPR0=$FF/MPR1=$F8/SP=$FF`; `src/dev_boot_internal.s` / `src/experimental_bios_boot.s` experimental: same MPR/SP + selection/media handoff; PUBLIC `tests/integration/ipl_loader_rom.s:reset`: `sei/cld/MPR0=$FF/MPR1=$F8/SP=$FF` + RB init) | MPR0=`$FF`, MPR1=`$F8`, SP=`$FF`, IRQs masked (`sei` first) | No WRAM clear; no VDC write; no `$2502` seeding (all paths audited) | UNKNOWN (no VDC write yet; reset value UNKNOWN genuine) | UNKNOWN (`$2503` indeterminate; warm-residual possible — see §5) | No |
| 3 | Staging fill + INFO read (`read_sector`: `$5A` fill of BUF `$2200`–`$29FF` via `STA (ptr),Y`, then READ(6) INFO sector into BUF; `validate_info` SIG/`IPLBLK`/`IPLBLN`/`IPLSTA`/`IPLJMP`/MPR bytes checks) | Same MPR/SP | BUF fill `$5A` incl. offsets `$0302`–`$0304` (`$2502`–`$2504` := `$5A`), then BUF := INFO sector bytes (`$2502`–`$2504` := sector bytes at those offsets — value = disc data, UNKNOWN, never a CR image; marker `$2503` MAY become `$A5` iff the sector byte equals `$A5`) | UNKNOWN (no VDC write) | DETERMINED staging residue (post-fill `$5A`; post-read sector bytes), never a seeded shadow; sync never established (loader performs no real-CR read) | No |
| 4 | BODY staging + load (`copy_body`: per-sector `read_sector` → BUF, copy → IPLSTA via MPR2..6 region switching; media-end pre-check `read_sector` first; `verify_body` readback) | MPR2..6 region switching (PUBLIC: IPLSTA `$3000`–`$7FFF`, load end `≤$E000` with equality only for end exactly `$E000` — `@eq` requires IPLSTA low byte `$00`, automatic for N-sector multiples of `$800`; larger IPLs via `$80`/`$A0`/`$C0` banks, see `switch_region`); MPR0/`$FF`, MPR1/`$F8` preserved | Every sector pass re-fills BUF `$5A` then overwrites with sector bytes (`$2502`–`$2504` rewritten every pass — last writer wins); IPLSTA start `$3000`–`$7FFF` (caller-controlled), load end `≤$E000`; no VDC write | UNKNOWN | Staging residue (last sector's bytes at `$0302`–`$0304`), never a seeded shadow; BODY logical range starts ≥`$3000`, so no direct BODY↔`$2502` overlap (see §4); sync never established | No |
| 5 | IPLJMP (`jmp` to INFO `IPLJMP`, `IPLSTA ≤ entry < IPLSTA+N*2048`; MPR0=`$FF`/MPR1=`$F8`/SP=`$FF`/A/X/Y=0/IRQ masked per `docs/g5-commercial-ipl-path-800.md` row) | Handoff state as above | Control transfer only (no loader write at handoff) | UNKNOWN (no VDC write by loader; IPL code may write VDC before any API — unobserved, no trace) | Staging residue from the last BUF writer (value = last sector bytes, UNKNOWN disc data); IPL may rewrite anything — unobserved | No (residue ≠ sync; no real-CR read ever performed) |
| 6 | Commercial caller runs (unobserved, no trace in this repo) → `$33` entry | OBSERVED at `$33` entry (PR #840 ExpF, structural only): MPR1=`$F8`, SP balanced, I=1, IDR=0/IRR=0, AR=0/CR=0/RCR=0/SR=0 | Caller-unknown (no WRAM read) | OBSERVED CR=0 at `$33` entry (VDC snapshot, both games, every API boundary) | UNKNOWN (never read) | UNKNOWN (CR=0 observed, shadow unobserved → sync unknowable) |
| 7 | `$33` (DSPOFF D2 experimental: marker `$A5`→`shadow&$3F`, otherwise reject C=1, no VDC/shadow write) | MPR0 window + `SEI`, restored; MPR1 preserved | Ok: VDC CR + `$2502`; reject: nothing (outside transients) | OBSERVED CR=0 at next entries (`$4A`…`$35` all CR=0) — consistent with either reject (no write) or ok-write of `$00` (no visible change) | UNKNOWN | UNKNOWN |
| 8 | `$4A` (preservation + MPR1-gate experimental body, NOT a B1 no-touch scaffold — 6 stack bytes precede the gate, refusal `SEC`; see E3) → `$23` (DEV real MWR R09 write, AR=9 — MWR≠CR, MAWR untouched) → `$25`/`$24`/`$36`/`$35` (B1/B1-analogous scaffolds: no VDC/RAM write outside transients) | Preserved (return-probe + VDC snapshot) | No CR/shadow write by these bodies (`$23` writes MWR R09 only, never CR; B1s write nothing; `$4A` stack pushes + gate only, no CR/shadow) | OBSERVED CR=0 throughout (all entries, both games) | UNKNOWN | UNKNOWN |
| 9 | `$2C` (D2 experimental since PR #840: marker `$A5`→`shadow\|$08`, otherwise reject) | OBSERVED MPR1=`$F8`, I=1, IDR/IRR=0, AR=2/CR=0/RCR=0/SR=0 at entry | Ok: VDC CR + `$2502`; reject: nothing | OBSERVED CR=0 at `$2C` entry and (ExpF) CR=0 maintained to `$2B` | UNKNOWN | UNKNOWN |
| 10 | `$2B` (P0 D2: marker `$A5`→`shadow&$FB`, otherwise reject; stub in ExpF) | OBSERVED entry P=`$07` (ExpF stub halt, Carry meaning UNKNOWN) | Stub: `LDX #$2B; JMP api_unimplemented` (no return) | OBSERVED CR=0 at stub entry (prior reject execution UNKNOWN) | UNKNOWN | UNKNOWN |

CR reset value is UNKNOWN genuine (pinned-core reset R[5] is a core
implementation fact, never a BIOS contract, and IPL code may rewrite CR before
any API). IPLJMP does not establish sync (the loader writes no VDC CR and does
not intentionally initialize shadow state, but BUF staging overwrites the shadow
addresses; IPL code unobserved). Every later “CR=0” is an entry-boundary snapshot, never a
continuous guarantee (no mid-caller sampling by design).

## 4. RAM ownership and collisions

Project-own shadow vs genuine mirrors must stay distinct:

- `$2502`/`$2503`/`$2504` are PROJECT-OWN (CR-family discipline, `PROJECT_SPEC_ONLY`).
  No public RAM map grounds them as genuine System Card reservations; they are
  justified only as the stale-RMW workaround on the pinned core + family
  single-writer discipline. Treating them as genuine BIOS RAM would be a
  provenance violation.
- `$20F3`/`$20F7` are CLAIMED mirrors (Zeograd: Current VDC reg / Value of reg #5)
  with unresolved address/value/order (register table vs one-liners conflict,
  `docs/61-dspoff-contract.md` S1). Values/order/atomicity/failure modes all
  UNKNOWN. No commercial WRAM read was added to chase them (RAM-clean kept by
  design). The in-tree CD block/window reuse of `$20F2`–`$20F7` is a separate
  project-own protocol (synthetic + opt-in commercial helper), never mirror
  evidence; conflating them would corrupt both the CD path and the CR analysis.
- Loader scratch vs shadow (DEFINITE overlap): PUBLIC loader BUF is `$2200` +
  SECTOR 2048 = range `$2200`–`$29FF`. `$2502`/`$2503`/`$2504` sit at BUF
  offsets `$0302`/`$0303`/`$0304` (arithmetic: `$2502-$2200=$0302`, etc. — no
  commercial data read). Every `read_sector` pass first fills the whole BUF
  with `$5A` via `STA (ptr),Y` (`ptr=$04`, 8 pages, `ipl_loader_rom.s:645ff`),
  then overwrites it with the 2048-byte sector. Hence `$2502`–`$2504` are
  rewritten on EVERY sector transfer (INFO, media-end pre-check, each BODY
  sector): post-fill value `$5A`, post-transfer value = sector bytes at those
  offsets. At IPLJMP the residue is the LAST sector's bytes (disc data,
  UNKNOWN) — never a seeded shadow, never a sync proof. The old
  "free-but-unseeded" description is withdrawn for the BUF span: the span is
  staging-reused, not free. After IPLJMP the scratch is caller-owned (IPL may
  reuse it arbitrarily — unobserved), so ownership is COMMERCIAL-CALLER UNKNOWN.
  Seeding `$2502` from BIOS/IPL without caller coordination could clobber
  caller data.
- IPL BODY vs shadow (logical disjointness as fact; physical alias UNKNOWN):
  PUBLIC `IPLSTA` validates to `$3000`–`$7FFF` with exclusive load end
  `≤$E000` (end `<$E000`, or end exactly `$E000` via the `@eq` IPLSTA-low-`$00` gate; `validate_info`, `ipl_loader_rom.s:248ff`). `$2502` (`$2502`)
  lies strictly below `$3000`, so no direct BODY↔`$2502` logical overlap exists
  (SOURCE_CONFIRMED range check). Physical bank alias: the audited mapping uses
  MPR1 `$F8` (work RAM `$2000`–`$3FFF`) for `$3000`–`$3FFF` and MPR2..6
  `$80`+ banks for `$4000`+ (`switch_region`); no alias mechanism to `$F8`
  work-RAM offset `$502` was found — but no universal non-alias theorem is
  claimed (header-controlled banks + mapping edge cases): physical alias is
  UNKNOWN (no positive evidence), never stated as fact. The BUF overlap above
  is the definite, source-confirmed finding; the BODY hypothesis is corrected
  and independent of it.
- Stack/zero-page/CD buffers (`$21xx` stack, `$00xx` zp via `$E0`–`$FF` window,
  `$2800` staging (dev-boot path; PUBLIC uses `$2200` BUF), `$3000` payload,
  `$2300` RES, `$3F00` markers) do not overlap `$2502` by construction in-tree.
  The loader BUF overlap is the operative collision (above), not IPLSTA.
- Warm-reset residual: no in-tree reset path clears WRAM (audited). If work RAM
  survives warm reset (hardware-dependent, UNKNOWN genuine; pinned-core behavior
  is not hardware proof), `$2503==$A5` + stale `$2502` from a prior run would
  falsely gate ok. The harness avoids this by explicit seeding every run
  (cold and warm separately); a commercial warm path has no such seeding. This
  risk is INDEPENDENT of the BUF sector-residue risk (either suffices for
  false-ok).
- Sector-derived `$A5` (not "accidental caller data" only): the byte at BUF
  offset `$0303` equals whatever the last sector holds there. If it equals
  `$A5`, the marker gate passes with a shadow that was never seeded and a CR
  that was never synchronized. No uniform-random model is assumed: the old
  "~1/256 per random byte" figure is WITHDRAWN except under the explicitly
  conditional model "IF the byte were independent-uniform, probability 1/256"
  — commercial sector data distribution is UNKNOWN, so no unconditional
  probability is stated. The family marker is a test-harness gate, not a
  security boundary; commercial use must assume false-ok is possible.

Conclusion: no in-tree owner *intentionally* seeds the shadow before the
commercial caller; the PUBLIC loader *unintentionally* rewrites
`$2502`–`$2504` on every sector transfer (staging residue, last-sector-wins);
after IPLJMP the owner is the commercial caller (UNKNOWN usage) and the residue
value is UNKNOWN disc data. Blind BIOS-side seeding would risk clobbering
caller data or creating false sync, and marker `$A5` never proves sync.

## 5. Problem statement (existing implementation)

The D2 bodies (`$33`/`$2C`, P0 `$2B`, plus DEV `$2E` below) are correct SYNTHETIC
functions under the synchronized-caller contract (seeded shadow + synchronized
CR → exact bit operation, proven by the 44-test `$2C` + 47-test `$2B` real-core
suites with VDC GetState boundary proof, RAM-diff, mutants, routing chain).
Their commercial activation condition — seeded + synchronized shadow at the
first CR-family call — is NOT established, and the staging analysis (§§2–4)
shows the addresses are actively reused:

- Normal DEV ROM: `$2E` EX_BGON is DEV-implemented (see E1 — not a stub);
  `$33`/`$2C`/`$2B` are nonreturning stubs in normal DEV (no writer there).
  Implementation status and genuine-ABI status are independent (all genuine
  ABIs stay UNVERIFIED/BLOCKED regardless of DEV implementation).
- Experimental images: bodies are linked but never auto-seeded (opt-in wiring
  only redirects the table; no reset/IPL/glue *intentional* store to
  `$2502`/`$2503`). The PUBLIC loader staging *unintentionally* rewrites
  `$2502`–`$2504` every sector pass (last-sector-wins residue, §4).
- PUBLIC loader: never *intentionally* seeds the shadow (audited); it does
  *unintentionally* overwrite the addresses via BUF staging (fill `$5A` +
  sector bytes; marker `$2503` MAY equal `$A5` iff the sector byte equals it).
- IPL + commercial caller: unobserved (no trace, no WRAM read by design).
- Observed commercial behavior must be read WITHOUT promotion (see table):

| Observation (OBSERVED synthetic/structural) | Valid inference | NOT proven |
|---|---|---|
| CR=0 at every API boundary, AR preserved | Entry-boundary VDC state (both games) | Reject occurrence (INFERRED at most, never OBSERVED). `$33` ok-write-zero also yields CR=0 with no visible change, so CR=0 ≠ reject proof |
| `$2C` entry AR=2/CR=0/RCR=0/SR=0 | Entry call-state once per cycle | `$2C` reject execution. `$2C` ok sets bit3 at return (unlike `$33` ok-write-zero), but the caller may rewrite CR between the `$2C` return and the next boundary — intermediate CR was never sampled |
| CR=0 maintained `$2C`→`$2B` | Boundary consistency | Sync state (marker/shadow UNKNOWN — no commercial WRAM read by design) |
| `$2B` ExpF stub halt | Reachability of `$2B` (10th call) | Any commercial `$2B` D2 execution (stub never returns; no commercial D2 run exists) |

Hence the commercial CR family activation is UNKNOWN: unseeded-reject is one
consistent hypothesis, sector-residue false-ok (`$A5` + stale shadow → ok-path
stale-shadow CR update incl. unintended non-target bits) is another live
hypothesis (source-derived risk, commercial occurrence UNOBSERVED — risk ≠ bug
report). The old wording "INERT by construction", "guaranteed fail-closed",
"safe both cold/warm", "no harm", "commercial caller unaffected" is
WITHDRAWN as overstatement: the marker gate (`$2503==$A5` → ok path, no
runtime sync check) cannot guarantee fail-closed outside the synchronized
contract, and "marker set ⇒ synced" never held. The fail-closed *code path*
(reject on `$2503!=$A5`) is a code fact; commercial *guarantee* is UNKNOWN.
Any attempt to "fix" activation by merely writing `$2503=$A5` somewhere would
hide the problem (false sync without CR grounding) and risk caller-data
clobbering + warm-residual + sector-residue false-ok.

## 6. Synchronization-guarantee comparison

| Way | Sketch | Grounding | Sync guarantee | Cold/warm | IRQ/caller | Cost/regression | Verdict |
|-----|--------|-----------|---------------|-----------|------------|-----------------|---------|
| L1 BIOS-reset seed (seed `$2502`/`$2503` at reset from known CR reset value) | Reset writes shadow + marker from a pinned CR reset constant | CR reset value UNKNOWN genuine (pinned-core reset is not a BIOS contract); staging gap: every `read_sector` pass rewrites BUF `$2200`–`$29FF` (incl. `$2502`–`$2504`, §4), destroying any reset-time seed before IPLJMP; IPL code unobserved | None (seed-then-overwrite: staging breaks the chain even before the IPL gap; warm residual + sector-residue `$A5` remain) | Unsafe warm (no clear) | Caller data at `$2502` after IPLJMP could be clobbered | Small code, large wrongness (false sync) + normal-ROM behavior change | REJECT. `$2503=$A5` without CR grounding is the exact anti-pattern the task forbids |
| L2 IPLJMP-just-before seed (seed at control handoff, after last VDC/WRAM writer audit) | Loader seeds shadow from actual CR immediately before `jmp` IPLJMP | Requires (a) known CR value at handoff (needs VDC read — stale-RMW hazard + SR read-clear risk + MMIO in loader, new attack surface), (b) complete audit of all pre-handoff writers (IPL code unobserved → impossible; staging writers ARE audited: BUF fill + sector transfers), (c) shadow lifetime/ownership past handoff (BUF staging ends at last sector, but caller owns scratch after IPLJMP; seeding the staging area then handing off proves nothing — no sync establishment, marker-only write), (d) BODY logical disjointness does NOT substitute for (b)/(c) (IPLSTA `$3000`+ vs `$2502` disjoint as fact, but physical-alias UNKNOWN and post-handoff caller writes unknowable) | None (IPL code runs AFTER handoff and may rewrite CR/`$2502` before any API; TOCTOU across handoff unclosable) | Warm gap remains (IPL may differ cold/warm) | Caller owns RAM after handoff; seeding then handing off invites TOCTOU desync | Medium (loader MMIO + window + audit burden) + loader regression risk | REJECT. Handoff is not a sync point (caller runs after); marker-after-handoff without CR proof is L1's error relocated. |
| L3 Explicit CR-init API (grounded init routine sets real CR + shadow together, marker last) | New API (or grounded existing init, if ever found) with public contract: writes real CR low/high + `$2502` (+`$2504`?) + `$2503=$A5` last, caller-ordered before first CR-family call | Requires (a) independent CR-value grounding (public BIOS-contract link register↔API, currently UNKNOWN for every CR bit except the D2 experimental scope), (b) RAM-ownership grounding incl. loader-staging audit (all writers incl. BUF fill/transfer; shadow lifetime/ownership; post-init overwrite exclusion; marker-independent validity — see §9), (c) caller-order guarantee (commercial caller order UNKNOWN genuine, single-path OBSERVED only), (d) synthetic/commercial applicability separation | Conditional (only if caller actually calls init first with the documented values AND no bypassing CR write occurs after — caller discipline, not a guarantee) | Safe IF init is idempotent + marker-last + cold/warm re-callable | Compatible IF opt-in experimental (normal DEV unchanged); if the commercial caller never calls it, activation stays UNKNOWN (reject-or-false-ok), not a proven-safe state | Small (one body + wiring + tests, same shape as `$2C`/`$2B`) + bounded lattice cost | CONDITIONAL. Viable ONLY after (a)–(d) are grounded per §9. Until then, designing the init API is research, not implementation. This P1 proposes NO init API. |
| L4 Unify all CR writers (single choke point for CR low/high + shadow sync; direct MMIO banned) | Route every CR write (family bodies + any future VDC body + loader/IPL if any) through one synchronizing writer; forbid direct port writes by contract | Requires (a) complete writer enumeration (in-tree intentional: only family + `EX_SETWRT`-MAWR; PLUS unintentional staging: BUF fill/transfer — §4; commercial caller direct writes UNKNOWABLE without a trace), (b) enforcement (cannot forbid commercial direct port writes — hardware allows them), (c) IRQ-safety (window + `SEI` already, but NMI assumed absent) | None enforceable (bypass always possible on hardware; desync undetectable by design — family docs already state this) | No worse | Incompatible as a requirement (cannot constrain commercial code); viable only as a caller-discipline note (already documented) | Large (touches every VDC body + loader + harness) + high regression risk | REJECT as a guarantee; KEEP as a documented discipline (already is). Distinct from #843 BOOT code-sharing (instruction commonization to save bytes, separate task — sharing code ≠ unifying writers; do not conflate). |
| L5 Genuine-mirror use (`$20F3`/`$20F7` as the sync source) | Read the genuine shadow instead of the project-own one | Mirror address/value/order/atomicity all UNKNOWN (Zeograd self-conflict, no second source, no in-tree precedent, family touches neither); in-tree CD reuse collides nominally; commercial WRAM unread by design | None (no known-good source) | Unknown | Unknown | Small code, total grounding failure (guessed mirror write is worse than no mirror) | REJECT (BLOCKED). Revisit only if public contract + independent HW + order proof jointly appear. |
| L6 Keep reject (no sync guarantee; synthetic-only family) | Current posture: `$2503!=$A5` → reject C=1, no VDC/shadow write (stack transients excepted); `$2503==$A5` + synchronized synthetic state → exact operation; `$A5` alone does not prove a valid seed | Fully grounded SYNTHETICALLY under the synchronized-caller contract (44-test `$2C` + 47-test `$2B` suites — per-HEAD pinning in §11: `51b7cd55` local runs, `145b182e` fix HEAD, merge `c30146e1`, CI per Actions; VDC boundary proof, mutants, routing chain, RAM-diff, cold/warm); commercial genuine stays UNKNOWN/BLOCKED | No commercial guarantee claimed (honest BLOCKED/UNKNOWN — the code path is fail-closed on non-`$A5`, but sector-residue `$A5` + stale shadow takes the ok path: false-ok is a live source-derived risk, commercial occurrence UNOBSERVED) | Cold/warm: harness-controlled seeding per run (safe within contract); commercial cold/warm UNKNOWN (no seeding, no guarantee) | Normal DEV unchanged, experimental opt-in (no new writer, zero regression, bounded lattice preserved); commercial caller effects UNKNOWN (neither "unaffected" nor "no harm" is claimed — unobserved) | Zero regression (no new writer) + bounded lattice preserved | ADOPT as posture (current code unchanged), WITHOUT any commercial safety guarantee. Commercial function stays BLOCKED/UNKNOWN until §9 preconditions met; synthetic experiments continue. |

Single `$2503=$A5` without CR grounding is rejected in every way that proposes
it (L1/L2). The only honest postures are L6 (now) and L3-conditional (later,
with preconditions).

## 7. Minimal synthetic verification (method, no new commercial data)

No new implementation was needed to support this investigation; the existing
synthetic fixtures already prove the relevant facts (cited, not re-run here
unless noted; re-run is cheap and deterministic on the pinned core):

- Loader staging audit (MAJOR correction): direct-address text search alone
  CANNOT prove absence of range writes. The definite BUF writer above was
  established by auditing `read_sector` control flow (fill loop `STA (ptr),Y`
  over 8 pages + per-sector transfer into BUF) and computing offsets
  (`$2502-$2200=$0302` etc.), not by grep hits. Absence claims for *specific*
  intentional stores may use text search + 44-byte body pins + window
  allowlists; absence of *range* writes requires control-flow audit (done for
  `read_sector`; other paths: reset/boot/dev-boot/experimental-boot/glue have
  no range-write loop to BUF-region targets — audited).
- Synchronized-state achievability (fixture-differentiated, E4 correction):
  ONLY the dedicated VDC probes (`geargrafx_irqon2c_probe.cpp`,
  `geargrafx_rcroff2b_probe.cpp`) construct real-CR==shadow at the JSR boundary
  (guest writes real CR low/high + RCR sentinel through the MPR0 window, seeds
  shadow identically, C++ snapshots at `jsr_pc` BEFORE callee entry and asserts
  equality — `_check_vdc_sync_before`). Return-probe payloads seed the shadow
  (per-implementation asserts) but do not write actual CR; the routing chain
  (`$33->$2C->$2B`) seeds only the shadow and records no CR/shadow values
  (chain CR/shadow transitions NOT_RUN; `$04->$0C->$08` EXPECTED, per PR #841
  review fix `145b182e`). "Every guest synchronizes" is withdrawn. Sync is
  CONSTRUCTIBLE synthetically (VDC fixtures), never holding commercially.
- Desync consequence: out-of-sync is out of contract by design (family docs);
  a desynced caller gets `stale-shadow op` deterministically (no error reported).
  Synthetic desync demo (seed `$2502` ≠ real CR, call, assert CR := stale-shadow
  op) is constructible in one guest run if ever needed — not run here (no new
  claim depends on it).
- Warm-residual risk: constructible (seed, warm-reset via `ResetMedia`-style
  harness, assert `$2503` persistence iff core preserves WRAM). NOT_RUN here
  (pinned-core WRAM persistence across warm reset is a core fact, not a BIOS
  contract, and commercial warm behavior is UNKNOWN; no conclusion needs it).
- Sector-residue risk: analytic from §4 (last-sector bytes at `$0302`–`$0304`;
  `$A5` at `$0303` iff the sector byte equals it — commercial values UNKNOWN).
  No run needed; no uniform model assumed (old "~1/256" withdrawn except as
  explicitly conditional). Warm-residual risk is independent (either suffices).
- External-writer audit: source audit (`EX_SETWRT` MAWR R00-only + `$23` MWR R09
  (not CR) + no other CR writer; 44-byte body pins: no VDC read opcode → no
  RMW) PLUS range-write audit (BUF fill/transfer — the writer direct grep
  misses). Commercial direct writes unknowable (no trace) — recorded as
  UNKNOWN, never assumed absent.

All synthetic-only; commercial WRAM/VRAM/SATB/framebuffer untouched throughout.

## 8. Recommendation

- KEEP L6 (current reject posture) for all commercial paths. Do NOT seed
  `$2502`/`$2503` from BIOS/reset/IPL/loader (L1/L2 rejected). Do NOT touch
  `$20F3`/`$20F7` (L5 BLOCKED). Do NOT unify by fiat (L4 unenforceable).
- Document the family discipline as-is (caller owns sync; no direct CR writes
  while using the family; re-seed after any bypass). It is already documented
  in every body header (`slot_2E`/`ex_dspoff`/`ex_irqon`/P0 `ex_rcroff`); no
  change needed.
- Gate any future L3 init API on §9 preconditions (all must hold; otherwise the
  follow-up Issue stays research-only). Do NOT smuggle init seeding into this
  P1 PR or the P0 `$2B` PR.
- Follow-up work (separate Issues, not this PR): (a) BOOT-saving CR-family
  commonization to unblock full ExpG — existing Issue #843 (linked, not
  duplicated; code-sharing to save bytes ≠ L4 writer unification — do not
  conflate); (b) shadow/BUF overlap + marker validity — new Issue #844
  (created by this task; discovery HEAD + ranges + risks recorded there);
  (c) commercial VDC-boundary observability (already have `vdc_snapshot`;
  no WRAM expansion — RAM-clean kept); (d) genuine CR-init contract research
  (public BIOS-contract link for an init operation, if any exists — no separate
  issue opened: covered by #844 preconditions + existing API issues #52–#61
  to avoid duplication; an unconditional init-implementation issue is NOT
  opened while §9 is unmet).

## 9. Implementation preconditions (all required before any commercial seed/init)

A follow-up implementation Issue may propose commercial seeding/init ONLY if
every item below is grounded (otherwise it stays research):

1. Independent CR-value grounding (public BIOS-contract link for the init
   values, not a D2 experimental scope choice; genuine RCR-vs-CR distinction
   preserved).
2. RAM-ownership grounding (public reservation for the shadow addresses, or a
   proven-free project-own region that commercial IPLSTA/BODY/caller data cannot
   occupy; loader-staging audit incl. BUF fill/transfer; shadow lifetime +
   ownership explicit incl. post-IPLJMP caller ownership; post-init overwrite
   exclusion proven (init-to-first-use window free of BUF reuse/caller writes);
   `$20F3`/`$20F7` conflict resolved, not guessed).
3. Sync start + maintenance conditions (exact point where CR==shadow is
   established + proof no writer (including IPL/caller direct port writes)
   breaks it before the first family call; TOCTOU across IPLJMP closed).
4. Cold/warm safety (explicit re-seed or clear on both paths; no warm-residual
   false-ok; no sector-residue/caller-data `$A5` false-ok beyond an explicitly
   bounded residual, or a marker-independent validity proof (marker-only gates
   are insufficient — §5 C1/C2); synthetic vs commercial applicability
   separated).
5. IRQ safety (window + `SEI` + restore preserved; NMI assumption stated;
   handler/timing unaffected or explicitly contracted).
6. Caller compatibility (opt-in experimental, normal DEV byte-identical, stub
   default preserved, bounded lattice, existing-family regression suites GREEN).
7. Falsifiable synthetic tests (seeded ok incl. boundary/coexistence/idempotence,
   unseeded reject, mask mutants, window/MPR/AR/SP, RAM-diff, VDC boundary sync
   proof, chain, cold/warm — same bar as P0 `$2B` 47 tests).
8. Genuine ABI still BLOCKED (`VERIFIED` unchanged, `release_eligible=false`,
   no promotion from D2 success).
9. BOOT budget (full-image link fits `$F000`–`$FFF5`; currently full ExpG
   overflows by 19 bytes — commonization or scope cut required first).

## 10. Open issues / BLOCKED factors / follow-up tasks

- O1. Commercial shadow values (`$2502`/`$2503`): UNKNOWN (no commercial WRAM
  read by design). The old "reject-consistent INFERRED" is withdrawn as a
  standing conclusion: CR=0 admits reject, `$33` ok-write-zero, and
  caller-rewrite-between-boundaries alike (§5 table). Staging residue
  (`$2502`–`$2504` := last sector bytes) is the determined pre-IPLJMP value
  class; post-IPLJMP caller writes unobserved. BLOCKED_PRIVACY (do not chase
  by dumping commercial RAM). Tracked in #844.
- O2. Commercial CR reset + IPL-time CR writers: UNKNOWN (IPL unobserved).
  Loader staging writers ARE audited (BUF fill/transfer, §4); IPL-code and
  caller direct writes unobserved.
- O3. Genuine `$20F3`/`$20F7` mirrors: UNKNOWN/BLOCKED (Zeograd self-conflict).
- O4. Warm-reset WRAM persistence (genuine + pinned-core): UNKNOWN/NOT_RUN.
  Independent of the BUF sector-residue risk (either suffices for false-ok).
- O5. Full ExpG BOOT fit: BLOCKED_ENVIRONMENT (19-byte overflow, §6). → #843.
- O6. Commercial ExpG observation: BLOCKED_PERMISSION + BLOCKED_ENVIRONMENT.
- O7. Shadow/BUF overlap + marker validity: source-confirmed staging reuse
  (§4) with UNKNOWN commercial values; false-ok live hypothesis. → #844
  (created by this task; no separate RAM-relocation/marker decision here).
- Follow-ups: F1 BOOT commonization → existing #843 (linked, not duplicated);
  F2 shadow/BUF overlap + ownership + validity → new #844 (created; discovery
  HEAD, ranges `$2200`–`$29FF`/`$0302`–`$0304`, intentional-vs-indirect
  distinction, sector-`$A5` false-ok, UNKNOWN values, sync gap, RAM
  ownership, cold/warm/reset conditions, synthetic re-creation, candidates,
  acceptance criteria, relations, genuine BLOCKED — all recorded there);
  F3 genuine init-contract research → no separate issue (covered by #844
  preconditions + existing API issues #52–#61; unconditional init-implementation
  issue NOT opened while §9 unmet, to avoid duplication); F4 `$2A`/`$2D` →
  existing #52/#55 (no new issues, no implementation here).

## 11. Evidence and checks

- Source audit: direct `grep -rn '\$250[234]'` (insufficient alone for range
  writes) + control-flow audit of `read_sector` (fill loop + transfer target +
  offset arithmetic `$2502-$2200=$0302` etc.) + `grep -rn '20F3\|20F7'` +
  per-file boot/loader/glue/API-implementation reads (`slot_2E`, `slot_23`
  MWR R09, `ex_memopen` gate, `slot_3A` MAWR — §§2–4, E1–E3). No `./rom`
  access (existence/list/read none). No proprietary content. No commercial
  WRAM/VRAM/SATB/framebuffer acquisition.
- Host (actual execution on THIS worktree): `git diff --check` clean +
  `make check` EXIT 0 (doc-only; no code/ROM/ledger change). `make test` NOT
  re-run on this worktree (no code change to regress; the earlier "clean"
  claim is corrected — E5). Regression cover: P0 #841 host 13 GREEN on
  `145b182e` (same merged base `c30146e1`); prior `$2C` host suites GREEN on
  their HEADs. Old-HEAD successes are cited per-HEAD, never as this worktree's
  runs.
- Real-core: no new runs on this worktree (docs-only; E5 — no new probe/new
  fixture/full real-core added). Cited evidence is per-HEAD: `$2C` 44-test +
  P0 `$2B` 47-test suites (review HEAD `51b7cd55`, fix HEAD `145b182e`, merge
  `c30146e1`; CI per GitHub Actions on those HEADs). P1 claims no real-core
  execution of its own. Any future L3 work must meet the P0 bar (falsifiable
  oracles, mutants, VDC boundary, cold/warm, Required CI).
- Safety gates unchanged (JSR 0/81, VERIFIED 0/81, `release_eligible=false`,
  `$33`/`$2C`/`$2B`/`$2D`/`$2A` genuine BLOCKED, normal DEV unchanged, stub
  defaults unchanged, explicit opt-in, 256 KiB / `$FFF6` / `$E800` boundaries,
  commercial-media non-disclosure, no reference-BIOS execution).

---

*Provenance: original research + original analysis (MIT). No proprietary
firmware/game/reference content. Pinned: Geargrafx `b49ae82a`, cc65 `71746c8`
(`tools/dependencies.json`). Zeograd/Stum/HuC/Hugo are REFERENCE_ONLY
(re-fetch dates/sizes/SHAs in §2 of `docs/53`/`docs/54`/`docs/55`, no text
reproduced). Commercial structural records reused from PR #840 only (no new
measurement, no `./rom` access).*
