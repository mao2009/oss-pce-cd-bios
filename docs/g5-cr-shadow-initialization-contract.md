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

Branch basis: `f8acbfce` (origin/main at task start; PR #840 `$2C` D2 integrated).
Related: #802 (G5 path), #635 (blocker measurement), #54 (`$2C`), #53 (`$2B`,
companion P0 implementation `agent/g5-rcroff-2b-20261127`), #52 (`$2A`), #55
(`$2D`), #61 (`$33`), #56 (`$2E`), #84 (`$4A`), #38 (`$1C` BLOCKED). Tracker #3.

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
family. Commercial ExpF shows `$2C` reject-consistent behavior (CR=0 maintained,
AR preserved) and `$2B` stub halt, consistent with an unseeded shadow — but the
actual commercial shadow value is UNKNOWN (no commercial WRAM read by design).
This P1 asks independently: who owns `$2502`/`$2503`/`$2504`, who (if anyone)
initializes them before the commercial caller runs `$33`/`$2C`/`$2B`, how CR and
shadow could be kept synchronized, and under what conditions the CR family
(`$33`/`$2C`/`$2B` etc.) could actually function in a commercial environment.
No code change is proposed here; implementation (if any) belongs to a follow-up
Issue after the preconditions in §9 are met.

## 2. Writer / reader inventory (`$2502`/`$2503`/`$2504`, `$20F3`/`$20F7`)

Method: `grep -rn` for `$2502`/`$2503`/`$2504`/`20F3`/`20F7` across `src/`
(including `src/overrides/`, `src/*.s`, `src/ipl_loader_g5_glue.s`,
`src/experimental_bios_boot.s`, `src/boot.s`, `src/reset_entry.s`,
`src/dev_boot_internal.s`, `src/cd_*.s`) plus symbol/offset/macro/indirect
review (direct addressing is the family norm: absolute `lda $250x`/`sta $250x`
with MPR1=`$F8`; no indirect/macro alias found for these addresses).

| Address | Writers (in-tree) | Readers (in-tree) | Notes |
|---------|-------------------|-------------------|-------|
| `$2502` (CR-low image) | `src/overrides/slot_2E.s` ok path (`sta $2502` after mask `ORA #$80`); `src/ex_dspoff.s` ok path (`AND #$3F`); `src/ex_irqon.s` ok path (`ORA #$08`); synthetic guest seeding (`sta $2502` in every G5/CR harness payload before first call) | Same three bodies + `src/ex_rcroff.s` (P0, `AND #$FB`) ok-path `lda $2502`; harness result dump (`lda $2502` → `res_base`); VDC probes (`GetState` only, never `$2502`) | Single-writer family discipline (caller owns sync). Marker `$2503` never written by any body (publish-only `$2502`). Reserved `$2504` never written by anyone |
| `$2503` (marker, `$A5` iff seeded) | Synthetic guest seeding only (`lda #$A5`/`lda #$00` → `sta $2503`); NO BIOS/reset/IPL/glue writer; NO body writer | `slot_2E`/`ex_dspoff`/`ex_irqon`/`ex_rcroff` entry gate (`lda $2503` + `cmp #$A5` + `beq @have`, reject otherwise) | `$A5` records “shadow was seeded”, never “shadow still matches CR”. Warm-residual `$A5` with stale `$2502` is a false-ok risk (see §5). Accidental `$A5` by commercial caller data is a false-ok risk (see §5) |
| `$2504` (reserved, CR-high image candidate) | None (reserved, never written) | None | Future `$2F`–`$33` family slot; must stay untouched until a grounded high-byte contract exists |
| `$20F3` / `$20F7` (Zeograd claimed mirrors: Current VDC reg / Value of reg #5) | In-tree CD reuse (DIFFERENT meaning, must not conflate): `src/cd_base_2base.s` (`lda $20F3`/`lda $20F7`/`sta $20F3`/`sta $20F7` as block/window scratch); `src/cd_read_link.s` (`BLK_MID=$20F3`, `WIN_HI=$20F7` as 24-bit block/window operands); `src/cd_read_commercial.s` treats `$20F2`–`$20F7` as untrusted caller RAM (never trusted, `$20F5`–`$20F7` never read) | Same CD files; NO CR-family body reads/writes `$20F3`/`$20F7` (family precedent touches neither; synthetic tests assert UNCHANGED) | Genuine mirror existence/value/order/atomicity UNKNOWN (Zeograd register table vs one-liners unresolved, `docs/61-dspoff-contract.md` S1). In-tree CD addresses collide nominally with the claimed mirror addresses but are a separate project-own protocol, never evidence for the mirror |
| `$2500`/`$2501` (EX_RND state) | `src/overrides/slot_3E.s` (LFSR) | Same | Different family, no conflict with CR shadow (addresses distinct, no shared gate) |

Not writers (audited, no `$2502`/`$2503`/`$2504`/`$20F3`/`$20F7` store found):
`src/boot.s` (stop loop only), `src/reset_entry.s` (MPR0/`$FF`, MPR1/`$F8`,
SP/`$FF`, no RAM store), `src/dev_boot_internal.s` (MPR/SP + `cd_media_state`/
`cd_entry` handoff, result blocks `$2300`/`$2800`/`$3000`/`$3F00`/`$3F40`, no
shadow), `src/experimental_bios_boot.s` (MPR/SP + XSEL validation + `cd_entry_selected`
handoff, no shadow; `$33` body included via `.include` but not executed at boot),
`src/ipl_loader_g5_glue.s` (XSEL record + `$33` body include, no execution at
build time), PUBLIC `tests/integration/ipl_loader_rom.s` (MPR0/`$FF`, MPR1/`$F8`,
SP/`$FF`, RB/`$2F00` init, INFO/BODY load to IPLSTA, MPR2..6 setup, `jmp` IPLJMP;
explicitly seeds NO `$2502`/`$2503`/`$2400`/`$20F2`–`$20FF` — payload seeds),
`src/cd_base_proto.s`/`src/cd_read_link.s`/`src/cd_read_commercial.s` (block/window
protocol, no CR shadow), `src/ex_memopen.s`/`ex_scrmod.s`/`ex_dotmod.s`/
`ex_satclr.s`/`ex_sprdma.s` (preservation scaffolds, no work-RAM write outside
transients), `src/ex_setwrt`-equivalent (`slot_3A`, MAWR R00 only, AR=0, never CR).

Competing VDC users audited: `EX_SETWRT` (slot `$3A`) touches MAWR (R00) only,
leaves AR=0, never CR. No other in-tree routine writes VDC CR. A commercial
caller direct port write (ST1/ST2 to R05) outside the family would silently
desync the shadow; no mechanism can observe it (see §6 L4).

Synthetic-only seeders (must not be mistaken for BIOS init): every G5/CR harness
payload (`tests/integration/test_g5_*.py` `PAYLOAD_ASM`, `geargrafx_*_probe.cpp`
guest code) writes `$2502`/`$2503` BEFORE the first JSR to establish the
in-contract synchronized state (real CR low/high + RCR sentinel written through
MPR0=`$FF` window, then shadow seeded to the same value, verified at the JSR
boundary). This is caller-side test setup, never firmware behavior.

## 3. Boot sequence (code-grounded, P1 worktree)

Order below follows the actual code (not a generic BIOS model). “CR known?”
means the real VDC CR value; “shadow?” means `$2502`/`$2503` determinability;
“sync?” means CR==shadow provability. IPLJMP target/time is caller-controlled
(PUBLIC INFO `IPLSTA`/`IPLJMP`, validated `$3000`–`$E000`).

| # | Point (code) | MPR / SP / IRQ | WRAM / VDC writer | CR known? | Shadow? | Sync? |
|---|--------------|----------------|-------------------|-----------|---------|-------|
| 1 | Core reset (hardware) | I=1, D=0 (spec `rom-contract.json`); MPR7=0 | None (project does not model power-on RAM; pinned core RAM nondeterministic except seeded bytes) | UNKNOWN (pinned core R[5] reset value is a core fact, not a BIOS contract; commercial VDC reset is outside this repo) | UNKNOWN (no value) | No |
| 2 | BIOS reset vector (`src/boot.s` normal DEV: stop loop; `src/reset_entry.s` minimal: `sei/cld/MPR0=$FF/MPR1=$F8/SP=$FF`; `src/dev_boot_internal.s` / `src/experimental_bios_boot.s` experimental: same MPR/SP + selection/media handoff; PUBLIC `tests/integration/ipl_loader_rom.s:reset`: `sei/cld/MPR0=$FF/MPR1=$F8/SP=$FF` + RB init) | MPR0=`$FF`, MPR1=`$F8`, SP=`$FF`, IRQs masked (`sei` first) | No WRAM clear; no VDC write; no `$2502` seeding (all paths audited) | UNKNOWN (no VDC write yet; reset value UNKNOWN genuine) | UNKNOWN (`$2503` indeterminate; warm-residual possible — see §5) | No |
| 3 | XSEL/INFO validation (experimental `experimental_bios_init` XSEL prefix/kind/track/LBA checks; PUBLIC `validate_info` SIG/`IPLBLK`/`IPLBLN`/`IPLSTA`/`IPLJMP`/MPR bytes checks) | Same | No shadow write; INFO/BODY header only read (first RAM write after validation) | UNKNOWN | UNKNOWN | No |
| 4 | BODY load (`copy_body` → IPLSTA via MPR2..6 region switching; `verify_body` readback) | MPR2..6 set from header (PUBLIC raw 0..7 → `$80`+); MPR0/`$FF`, MPR1/`$F8` preserved | IPLSTA (`$3000`–`$E000` variable) + BUF (`$2200`) + RB (`$2F00`); NO `$2502`/`VDC` write | UNKNOWN | UNKNOWN (load area may overlap `$2502`? see §4; loader itself does not target `$2502`, but IPLSTA is caller-controlled) | No |
| 5 | IPLJMP (`jmp` to INFO `IPLJMP`, `IPLSTA ≤ entry < IPLSTA+N*2048`; MPR0=`$FF`/MPR1=`$F8`/SP=`$FF`/A/X/Y=0/IRQ masked per `docs/g5-commercial-ipl-path-800.md` row) | Handoff state as above | Control transfer only | UNKNOWN (no VDC write by loader; IPL code may write VDC before any API — unobserved, no trace) | UNKNOWN (loader seeded nothing; IPL may write anything — unobserved) | No |
| 6 | Commercial caller runs (unobserved, no trace in this repo) → `$33` entry | OBSERVED at `$33` entry (PR #840 ExpF, structural only): MPR1=`$F8`, SP balanced, I=1, IDR=0/IRR=0, AR=0/CR=0/RCR=0/SR=0 | Caller-unknown (no WRAM read) | OBSERVED CR=0 at `$33` entry (VDC snapshot, both games, every API boundary) | UNKNOWN (never read) | UNKNOWN (CR=0 observed, shadow unobserved → sync unknowable) |
| 7 | `$33` (DSPOFF D2 experimental: seeded→`shadow&$3F`, unseeded→reject C=1, no write) | MPR0 window + `SEI`, restored; MPR1 preserved | Ok: VDC CR + `$2502`; reject: nothing (outside transients) | OBSERVED CR=0 at next entries (`$4A`…`$35` all CR=0) — consistent with either reject (no write) or ok-write of `$00` (no visible change) | UNKNOWN | UNKNOWN |
| 8 | `$4A`→`$23`→`$25`→`$24`→`$36`→`$35` (B1 scaffolds: no VDC/RAM write; `$23` DEV: MWR only, AR=9) | Preserved (return-probe + VDC snapshot) | No CR/shadow write (B1 by construction; `$23` MAWR only) | OBSERVED CR=0 throughout (all entries, both games) | UNKNOWN | UNKNOWN |
| 9 | `$2C` (D2 experimental since PR #840: seeded→`shadow\|$08`, unseeded→reject) | OBSERVED MPR1=`$F8`, I=1, IDR/IRR=0, AR=2/CR=0/RCR=0/SR=0 at entry | Ok: VDC CR + `$2502`; reject: nothing | OBSERVED CR=0 at `$2C` entry and (ExpF) CR=0 maintained to `$2B` | UNKNOWN | UNKNOWN |
| 10 | `$2B` (P0 D2: seeded→`shadow&$FB`, unseeded→reject; stub in ExpF) | OBSERVED entry P=`$07` (ExpF stub halt, Carry meaning UNKNOWN) | Stub: `LDX #$2B; JMP api_unimplemented` (no return) | OBSERVED CR=0 maintained (reject-consistent INFERRED) | UNKNOWN | UNKNOWN |

CR reset value is UNKNOWN genuine (pinned-core reset R[5] is a core
implementation fact, never a BIOS contract, and IPL code may rewrite CR before
any API). IPLJMP does not establish sync (loader writes neither CR nor shadow;
IPL code unobserved). Every later “CR=0” is an entry-boundary snapshot, never a
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
- Loader scratch vs shadow: PUBLIC loader uses BUF `$2200`, RB `$2F00`, payload
  result `$2F80`, load area IPLSTA `$3000`–`$E000` (MPR2..6 `$40`/`$60`/`$80`/
  `$A0`/`$C0`), stack `$2100`–`$21FF` (MPR1). `$2502` (`$2502` in the `$F8`
  work-RAM window) lies in the `$2200`–`$2FFF` scratch span nominally, but the
  loader never targets it (audited), and after IPLJMP the scratch is caller-owned
  (IPL may reuse it arbitrarily — unobserved). Therefore: before IPLJMP, `$2502`
  is free-but-unseeded (loader does not clobber it, but also does not establish
  it); after IPLJMP, `$2502` ownership is COMMERCIAL-CALLER UNKNOWN (caller may
  use `$2502` for its own variables; no dump exists to rule it out). Seeding
  `$2502` from BIOS/IPL without caller coordination could clobber caller data.
- Stack/zero-page/CD buffers (`$21xx` stack, `$00xx` zp via `$E0`–`$FF` window,
  `$2800` staging, `$3000` payload, `$2300` RES, `$3F00` markers) do not overlap
  `$2502` by construction in-tree, but commercial IPL (`IPLSTA` variable) could
  load a BODY that overlaps `$2502` (INFO `IPLSTA`/`IPLBLN` validated only for
  `$3000`–`$E000` range + 21-bit READ(6) range, not for `$2502` avoidance).
  Overlap would be caller data, not a shadow — another reason BIOS-side blind
  seeding is unsafe.
- Warm-reset residual: no in-tree reset path clears WRAM (audited). If work RAM
  survives warm reset (hardware-dependent, UNKNOWN genuine; pinned-core behavior
  is not hardware proof), `$2503==$A5` + stale `$2502` from a prior synthetic run
  would falsely gate ok. The harness avoids this by explicit seeding every run
  (cold and warm separately); a commercial warm path has no such seeding.
- Accidental `$A5`: `$2503==$A5` is a single-byte magic, not a cryptographic
  authenticator. Commercial caller data equal to `$A5` at `$2503` (probability
  ~1/256 per random byte, higher for structured data) would falsely gate ok with
  a desynced `$2502`. The family accepts this residual risk explicitly (marker
  is a test-harness gate, not a security boundary); commercial use must assume
  false-ok is possible.

Conclusion: no in-tree owner seeds the shadow before the commercial caller;
after IPLJMP the owner is the commercial caller (UNKNOWN usage); blind BIOS-side
seeding would risk clobbering caller data or creating false sync.

## 5. Problem statement (existing implementation)

The D2 bodies (`$2E`/`$33`/`$2C`, P0 `$2B`) are correct SYNTHETIC functions
(seeded shadow + synchronized CR → exact bit operation, proven by 47-test
real-core suites with VDC GetState boundary proof, RAM-diff, mutants, chain).
Their commercial activation condition — seeded + synchronized shadow at the
first CR-family call — is NOT established by any in-tree writer:

- Normal DEV ROM: all CR-family slots are nonreturning stubs (no writer).
- Experimental images: bodies are linked but never auto-seeded (opt-in wiring
  only redirects the table; no reset/IPL/glue store to `$2502`/`$2503`).
- PUBLIC loader: seeds nothing (audited).
- IPL + commercial caller: unobserved (no trace, no WRAM read by design).
- Observed commercial behavior (CR=0 maintained, AR preserved, `$2C` reject-consistent,
  `$2B` stub halt) is CONSISTENT WITH unseeded-reject but does NOT prove it
  (shadow value UNKNOWN; CR=0 could also be ok-write of `$00`). INFERRED, never
  OBSERVED.

Hence the commercial CR family is currently INERT (reject-or-stub) by
construction — fail-closed, not functional. This is the intended safety posture
(genuine ABI BLOCKED, no guessing), not a bug. Any attempt to “fix” it by merely
writing `$2503=$A5` somewhere would hide the problem (false sync without CR
grounding) and risk caller-data clobbering + warm-residual + accidental-magic
false-ok.

## 6. Synchronization-guarantee comparison

| Way | Sketch | Grounding | Sync guarantee | Cold/warm | IRQ/caller | Cost/regression | Verdict |
|-----|--------|-----------|---------------|-----------|------------|-----------------|---------|
| L1 BIOS-reset seed (seed `$2502`/`$2503` at reset from known CR reset value) | Reset writes shadow + marker from a pinned CR reset constant | CR reset value UNKNOWN genuine (pinned-core reset is not a BIOS contract); IPLJMP gap (loader/IPL may rewrite CR or `$2502` before any API — unobserved, no audit possible) | None (seed-then-desync: IPL gap breaks the chain; warm residual + accidental magic remain) | Unsafe warm (no clear) | Caller data at `$2502` after IPLJMP could be clobbered | Small code, large wrongness (false sync) + normal-ROM behavior change | REJECT. `$2503=$A5` without CR grounding is the exact anti-pattern the task forbids |
| L2 IPLJMP-just-before seed (seed at control handoff, after last VDC/WRAM writer audit) | Loader seeds shadow from actual CR immediately before `jmp` IPLJMP | Requires (a) known CR value at handoff (needs VDC read — stale-RMW hazard + SR read-clear risk + MMIO in loader, new attack surface), (b) complete audit of all pre-handoff VDC writers (loader audited clean, but IPL code unobserved → impossible), (c) `$2502` non-collision with IPLSTA/BODY (caller-controlled, unknowable) | None (IPL code runs AFTER handoff and may rewrite CR/`$2502` before any API) | Warm gap remains (IPL may differ cold/warm) | Caller owns RAM after handoff; seeding then handing off invites TOCTOU desync | Medium (loader MMIO + window + audit burden) + loader regression risk | REJECT. Handoff is not a sync point (caller runs after). |
| L3 Explicit CR-init API (grounded init routine sets real CR + shadow together, marker last) | New API (or grounded existing init, if ever found) with public contract: writes real CR low/high + `$2502` (+`$2504`?) + `$2503=$A5` last, caller-ordered before first CR-family call | Requires (a) independent CR-value grounding (public BIOS-contract link register↔API, currently UNKNOWN for every CR bit except the D2 experimental scope), (b) RAM-ownership grounding (currently project-own only), (c) caller-order guarantee (commercial caller order UNKNOWN genuine, single-path OBSERVED only) | Conditional (only if caller actually calls init first with the documented values AND no bypassing CR write occurs after — caller discipline, not a guarantee) | Safe IF init is idempotent + marker-last + cold/warm re-callable | Compatible IF init is opt-in experimental (normal DEV unchanged); incompatible if commercial caller never calls it (then family stays reject — same as today, no harm) | Small (one body + wiring + tests, same shape as `$2C`/`$2B`) + bounded lattice cost | CONDITIONAL. Viable ONLY after (a)–(c) are grounded per §9. Until then, designing the init API is research, not implementation. This P1 proposes NO init API. |
| L4 Unify all CR writers (single choke point for CR low/high + shadow sync; direct MMIO banned) | Route every CR write (family bodies + any future VDC body + loader/IPL if any) through one synchronizing writer; forbid direct port writes by contract | Requires (a) complete writer enumeration (in-tree done: only family + `EX_SETWRT`-MAWR; commercial caller direct writes UNKNOWABLE without a trace), (b) enforcement (cannot forbid commercial direct port writes — hardware allows them), (c) IRQ-safety (window + `SEI` already, but NMI assumed absent) | None enforceable (bypass always possible on hardware; desync undetectable by design — family docs already state this) | No worse | Incompatible as a requirement (cannot constrain commercial code); viable only as a caller-discipline note (already documented) | Large (touches every VDC body + loader + harness) + high regression risk | REJECT as a guarantee; KEEP as a documented discipline (already is). |
| L5 Genuine-mirror use (`$20F3`/`$20F7` as the sync source) | Read the genuine shadow instead of the project-own one | Mirror address/value/order/atomicity all UNKNOWN (Zeograd self-conflict, no second source, no in-tree precedent, family touches neither); in-tree CD reuse collides nominally; commercial WRAM unread by design | None (no known-good source) | Unknown | Unknown | Small code, total grounding failure (guessed mirror write is worse than no mirror) | REJECT (BLOCKED). Revisit only if public contract + independent HW + order proof jointly appear. |
| L6 Keep reject (no sync guarantee; synthetic-only family) | Current posture: unseeded → reject C=1, no write; seeded (synthetic harness) → exact operation | Fully grounded SYNTHETICALLY (47-test `$2B` + 44-test `$2C` suites, VDC boundary proof, mutants, chain, RAM-diff, cold/warm); commercial genuine stays UNKNOWN/BLOCKED | No commercial guarantee claimed (honest BLOCKED) | Safe (fail-closed both cold/warm; no false-ok beyond the documented `$A5` residual, which synthetic harness controls by explicit seeding) | Compatible (normal DEV unchanged, experimental opt-in, commercial caller unaffected — reject is safe) | Zero regression (no new writer) + bounded lattice preserved | ADOPT (current). Commercial function stays BLOCKED until §9 preconditions met; synthetic experiments continue. |

Single `$2503=$A5` without CR grounding is rejected in every way that proposes
it (L1/L2). The only honest postures are L6 (now) and L3-conditional (later,
with preconditions).

## 7. Minimal synthetic verification (method, no new commercial data)

No new implementation was needed to support this investigation; the existing
synthetic fixtures already prove the relevant facts (cited, not re-run here
unless noted; re-run is cheap and deterministic on the pinned core):

- Reset/loader non-seeding: source audit (no `$2502` store in any reset/loader/
  glue path — §2) + `make test` host suite (builder byte-identity proves no
  hidden writer). No guest execution needed (absence proved by text search +
  44-byte body pins + window allowlists).
- Synchronized-state achievability: every `$2C`/`$2B` VDC test constructs
  real-CR==shadow at the JSR boundary (guest writes real CR low/high + RCR
  sentinel, seeds shadow identically, C++ snapshots at `jsr_pc` BEFORE callee
  entry and asserts equality — `_check_vdc_sync_before`). This proves sync is
  CONSTRUCTIBLE synthetically, never that it holds commercially.
- Desync consequence: out-of-sync is out of contract by design (family docs);
  a desynced caller gets `stale-shadow op` deterministically (no error reported).
  Synthetic desync demo (seed `$2502` ≠ real CR, call, assert CR := stale-shadow
  op) is constructible in one guest run if ever needed — not run here (no new
  claim depends on it).
- Warm-residual risk: constructible (seed, warm-reset via `ResetMedia`-style
  harness, assert `$2503` persistence iff core preserves WRAM). NOT_RUN here
  (pinned-core WRAM persistence across warm reset is a core fact, not a BIOS
  contract, and commercial warm behavior is UNKNOWN; no conclusion needs it).
- Accidental-magic risk: analytic (1-byte `$A5`Kind, §4). No run needed.
- External-writer audit: source audit (`EX_SETWRT` MAWR-only + no other CR
  writer) + 44-byte body pins (no VDC read opcode → no RMW). Commercial direct
  writes unknowable (no trace) — recorded as UNKNOWN, never assumed absent.

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
  commonization to unblock full ExpG (P0 §6 overflow: 19 bytes; share
  prologue/epilogue across `$33`/`$2C`/`$2B` without changing the 44-byte
  semantics — needs its own design + re-validation, never a silent shrink);
  (b) commercial VDC-boundary observability (already have `vdc_snapshot`;
  no WRAM expansion — RAM-clean kept); (c) genuine CR-init contract research
  (public BIOS-contract link for an init operation, if any exists).

## 9. Implementation preconditions (all required before any commercial seed/init)

A follow-up implementation Issue may propose commercial seeding/init ONLY if
every item below is grounded (otherwise it stays research):

1. Independent CR-value grounding (public BIOS-contract link for the init
   values, not a D2 experimental scope choice; genuine RCR-vs-CR distinction
   preserved).
2. RAM-ownership grounding (public reservation for the shadow addresses, or a
   proven-free project-own region that commercial IPLSTA/BODY/caller data cannot
   occupy; `$20F3`/`$20F7` conflict resolved, not guessed).
3. Sync start + maintenance conditions (exact point where CR==shadow is
   established + proof no writer (including IPL/caller direct port writes)
   breaks it before the first family call; TOCTOU across IPLJMP closed).
4. Cold/warm safety (explicit re-seed or clear on both paths; no warm-residual
   false-ok; no accidental-`$A5` false-ok beyond the documented residual, or an
   explicit stronger gate with its own proof).
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
  read by design; reject-consistent INFERRED only). BLOCKED_PRIVACY (do not chase
  by dumping commercial RAM).
- O2. Commercial CR reset + IPL-time CR writers: UNKNOWN (IPL unobserved).
- O3. Genuine `$20F3`/`$20F7` mirrors: UNKNOWN/BLOCKED (Zeograd self-conflict).
- O4. Warm-reset WRAM persistence (genuine + pinned-core): UNKNOWN/NOT_RUN.
- O5. Full ExpG BOOT fit: BLOCKED_ENVIRONMENT (19-byte overflow, §6).
- O6. Commercial ExpG observation: BLOCKED_PERMISSION + BLOCKED_ENVIRONMENT.
- Follow-ups (new Issues, not this PR): F1 CR-family commonization design
  (BOOT saving, semantics-preserving); F2 genuine CR-init contract research;
  F3 commercial seed/init implementation (gated on §9); F4 `$2A` set-side
  investigation (symmetric, separate).

## 11. Evidence and checks

- Source audit: `grep -rn '\$250[234]' src/` + `grep -rn '20F3\|20F7' src/` +
  per-file boot/loader/glue reads (§2–§4). No `./rom` access. No proprietary
  content.
- Host: `make test` / `make check` / `git diff --check` clean on the P1 worktree
  (doc-only change; no code, no ROM bytes, no ledger promotion).
- Real-core: no new runs required (prior `$2C` 44-test + P0 `$2B` 47-test suites
  already prove the synthetic half; this doc adds no new behavior to verify).
  Any future L3 work must meet the P0 bar (falsifiable oracles, mutants, VDC
  boundary, cold/warm, Required CI).
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
