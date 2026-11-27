# $25 EX_SCRMOD B1 preservation-only scaffold (Issue #47, G5 P0 task)

Status: **B1 SIMULATED scaffold; NOT a compatible System Card BIOS.
NOT genuine ABI. NOT DEV_CALLABLE. `release_eligible=false`.
G5_OBSERVED=false.** Reference JSR 0/81, genuine ABI VERIFIED 0/81
(unchanged by this task). No System Card image was executed,
disassembled, or copied. No game-disc bytes, titles, paths, hashes,
sector contents, audio, or saves appear here. `./rom/` was not accessed
in any form for this task (no existence check, listing, or read).

Branch basis: `2aa45394` (merge of PR #827, origin/main at task start).
Contract basis: `docs/47-ex-scrmod-contract.md` (§4.2, §6.2, §6.3, §7,
§8, §9). This document records the B1 design, its limits, and the
synthetic evidence; it changes no ledger and no genuine-ABI claim.

## 1. What B1 is (and is not)

B1 (`src/ex_scrmod.s`, `ex_scrmod_entry`, 9 bytes:
`PHP PHA PHX PHY PLY PLX PLA PLP RTS`) is a preservation-only
SIMULATED scaffold wired ONLY into G5 experimental images behind the
opt-in `cd_25="experimental"` flag of `tools/build_ipl_loader_g5_2base.py`
(default `cd_25="stub"` keeps every existing image byte-identical;
verified by the pinned pre-$25 baseline SHA in
`tests/host/test_build_g5_25_host.py`).

B1 performs NO invented service and returns NO invented success value:

- JSR `$E06F` (3-byte) -> RTS to caller+3; SP balanced (JSR 2 bytes +
  max 4 transient pushes; caller SP minimum `$06`; below minimum is out
  of contract and never run on the real core).
- A/X/Y preserved exactly; P preserved exactly INCLUDING Carry
  (`C_in == C_out` on both seeds; no CLC/SEC fabricated, no ok/reject
  values). Latch-exact on the pinned core (HuC6280 `SetP` masks with
  `$EF`; plain PHP/PLP round trip restores N/V/T/D/I/Z/C exactly).
- MPR0-MPR7 unchanged (B1 executes no TAM/TMA-write/TXS; MPR1=`$F8`
  is a caller precondition validated by the harness BEFORE JSR per T13;
  MPR1!=`$F8` is OUT OF CONTRACT/BLOCKED -- the check-then-act guard is
  proved by `test_mpr1_precondition_refused_before_jsr`, and every real
  `$25` APICALL in every test carries MPR1=`$F8`).
- AR unchanged (B1 executes no ST0/ST1/ST2); all 20 VDC registers
  unchanged on the pinned core (dedicated VDC probe, entry AR
  0/5/9/18); no VCE/PSG/timer/CD access.
- No work-RAM writes outside the transient caller-stack bytes: 256-byte
  canary at `$2900`-`$29FF` intact plus guest-observed zp `$10` pre/post
  invariance on the real core, plus full-buffer cold/warm equality.
- No IRQ side effects (IDR/IRR unchanged across the call on the real core).
- P domain excludes T=1: the pinned core consumes the transfer flag
  every clock (`huc6280_inline.h` `Clock()`: `m_transfer_flag` snapshot
  + `ClearFlag(FLAG_TRANSFER)` when `GG_TESTING` is not defined), so an
  entry T=1 exits T=0 on ANY implementation. This is a pinned-core
  platform fact, not B1 behavior; preservation is verified for all P
  bits except T, and T=1 entry is out of the verified domain with this
  ground (boundary test uses T=0 seeds `0x04`/`0x85`).

B1 defines NO refusal path and NO VDC/shadow/mask behavior. Contract
applicability (§6.3): T01-T03, T05-T07, T10-T14 apply (GREEN proved
below); T04 (I/O window), T08 (shadow bypass), T09 (mask mutants) are
NOT_APPLICABLE with variant-grounded reason (B1 has no I/O, no shadow,
no mask; requiring them would invent code paths).

## 2. Wiring (opt-in, normal ROM untouched)

- `tools/build_experimental_bios_boot.py`: `_SLOT25_TABLE`,
  `_SLOT25_BODY_LEN` (= 9, tied to the structural pin),
  `_redirect_slot25_to_body` (fail-closed: linked-fallback check, BOOT
  span check `entry + 9 <= $FFF6`, PHP/PHA prologue check),
  `unified_api_windows_25`, `unified_api_windows_4a_25` (base windows
  untouched).
- `tools/build_ipl_loader_g5_2base.py`: `cd_25` parameter
  (`"stub"`/`"experimental"`, refused otherwise), `SCRMOD_SOURCE/_ENTRY`,
  incremental label/source/link lists (the `(stub, stub)` order is
  unchanged: proto/link/glue), patch + window checks per combination,
  manifest `cd_25` + `experimental_slots` + `experimental_sources`.
- `spec/c3_static_contracts.json`: `src/ex_scrmod.s` classified as
  experimental-only fixture (never linked into the normal/production
  BIOS, not firmware code). No other ledger changes:
  `spec/api_contracts.json` slot 37 stays UNKNOWN/HYPOTHESIS,
  `spec/dev_implementations.json` id 37 stays `NOT_READY`/`null`
  (B1 is a scaffold, never a VDC-meaningful DEV body), reference JSR
  stays 0/81, VERIFIED stays 0/81, DEV_CALLABLE stays false,
  `release_eligible=false` stays, G5_OBSERVED stays false.

## 3. Synthetic evidence (pinned Geargrafx `b49ae82a`, cc65 `71746c8`)

Real-core suite `tests/integration/test_g5_scrmod_25.py` (61 tests):
host-side oracle negatives (strict-validator controls) + synthetic
return-probe runs (PUBLIC IPL 2-base boot, `cd_4a`+`cd_25` experimental,
cold+warm identical every run) + dedicated VDC probe
(`tests/integration/geargrafx_scrmod25_probe.cpp`) + dedicated WRAM
write-tracer probe
(`tests/integration/geargrafx_scrmod25_writes_probe.cpp`, per-instruction
write history, cold+warm).

- T01 stub: `cd_25="stub"` traps at `api_unimplemented` with X=`$25`,
  post-JSR marker `$00` (fail-closed intact).
- T02 mechanics: slots [`$25`,`$25`], entries `$E06F`, `in_table`,
  `sp_delta` 2, `stack_check` MATCH, `return_matches_caller` true, SP
  balanced, cold/warm identical.
- T03 preservation: C=1 (`$07`) and C=0 (`$06`) classes GREEN with exact
  A/X/Y/P/MPRs/SP; A!=P mixed vector (`$B0`/`$35`) GREEN with
  independent A-vs-P checks; boundary values (A `$00`/`$FF`, X
  `$00`/`$80`, Y `$A5`/`$5A`, P `$04`/`$85`) GREEN.
- T05 RAM scope: `$2900`-`$29FF` canary intact + zp `$10` pre/post
  intact + full 6 KiB buffer cold/warm equality on every GREEN run,
  PLUS per-instruction WRAM write history from the tracer probe: B1
  writes exactly the 6 transient stack bytes (`$21FA`-`$21FF` at SP=`$FF`,
  `$2101`-`$2106` at SP=`$06`) and nothing else, cold and warm. A
  `$2200` store mutant is GREEN on all legacy oracles (canary/zp/dump
  blind outside their samples) but RED on the tracer; a
  write-then-restore `$2200` mutant (final value unchanged) is GREEN on
  every final-value oracle yet RED on the write history -- proving the
  history-vs-final distinction the contract demands. Residual limit: the
  tracer accumulates per-instruction WRAM *value diffs*, so a same-value
  store (writing back the byte already present, no value change at any
  step) is invisible to it by construction -- bus-level write detection
  would need core write-path hooks, out of scope for this experimental
  scaffold. Only state-changing writes are claimed as detected.
- T06 bad table: `$E06F` reroute to fallback RED
  (`api_unimplemented`, X=`$25`, arrival-proved).
- T07 Carry-preservation oracle (balanced, channel (b)): cavity bodies
  B1+CLC / B1+SEC before RTS (4 pushes / 4 pops, clean return, SP
  balanced, only Carry differs; `$E06F` vector repointed to a BOOT
  `$FF` cavity, normal 9-byte body intact and verified). Forced-CLC:
  C=0 GREEN (full preservation proof) / C=1 RED as
  `M7a-C1-preservation (balanced)` on the direct Carry-assertion
  mismatch; forced-SEC: C=0 RED as `M7b-C0-preservation (balanced)` /
  C=1 GREEN. Abnormal halts are forbidden for these mutants (channel
  (a) never counts as T07 RED); arrival, clean RTS, SP balance, and
  cold/warm identity are all required. The earlier unbalanced PLP-drop
  shapes are kept separately as control-flow coverage
  (`M7a-C1-control-flow`, `M7b-C0-control-flow`), never cited as the
  T07 preservation verdict.
- T10 stack smash: balanced out-of-scope write mutant (PHY/PLY ->
  STA `$10`) RED via the guest-observed zp mismatch (abnormal halts
  forbidden for this mutant). A canary-region STA shape was rejected
  during development: its unbalanced RTS mis-returns into a random walk
  that hangs the probe past its timeout instead of halting; the balanced
  zp shape keeps the halt fast and deterministic while proving the same
  property (out-of-contract write -> RED). Recorded here so review can
  check the substitution.
- T11 boundaries: preservation probe (B1 defines no reject code); T=1
  excluded with the pinned-core ground above.
- T12 SP depth: SP=`$FF` and boundary minimum SP=`$06` GREEN and
  balanced with intact canary; SP<`$06` out of contract, never run.
- T13 MPR1 pre-JSR: candidate `$FF` refuses at `refused_halt` with zero
  `$25` APICALL in both cycles (check-then-act: no JSR, no remap);
  candidate `$F8` positive control GREEN with proven `$25` arrival.
- T14: every run asserts cold==warm buffer equality; C=0 and C=1 seeds
  both GREEN; VDC probe covers AR 0/5/9/18 x P `$06`/`$07`.
- Chain (synthetic G5 leg): `$33`->`$4A`->`$23`->`$25` through the real
  table entries (`$02` is the PUBLIC boot that loads the payload),
  APICALL order/slot/entry/`sp_delta`/MATCH/`return_matches_caller`
  per call, MPR1=`$F8` at every entry, end-to-end regs/SP/MPRs/canary
  preserved. The observed `$25` entry state (A=`$31`, X=`$12`, Y=`$34`,
  P=`$07` mod B, MPR1=`$F8`, SP=`$FF`) is labeled SIMULATED-entry:
  it records the scaffold's entry conditions, never genuine `$25`
  inputs. No commercial-game continuation is claimed.
- Structural pins: 9-byte B1 body bytes pinned in host + real-core
  tests (a bare-RTS substitution would be behavior-GREEN by
  construction, so the shape itself is pinned); stub-default ROM pinned
  to the pre-$25 baseline SHA; table routing pinned on both images
  (`$02`/`$03`/`$33` intact; `$4A` experimental on the exp image).
- Oracle strictness (no-probe controls): cold-duplicate, short/excess
  RUN, bad reason, unconfirmed halt, pc mismatch, cross-cycle split,
  truncation, missing/short/bad-hex buffer, missing RUN, unapplied
  mutant, extra-change scope (in-body/outside/same-replacement/
  untouched-target), bad offset, length change, clean-ROM-as-mutant,
  missing/empty/wrong-slot/wrong-entry/partial arrival, misreturn
  arrival, cold-only/warm-only/divergent anomalies, clean-channel
  arrival (missing/partial) -- all rejected as RED; both-cycle mismatch
  and valid abnormal/misreturn arrivals accepted.

Host suite `tests/host/test_build_g5_25_host.py` (10 tests): source
presence, stub-default table/manifest/body-absence, pre-$25 baseline
byte-identity, experimental wiring (table/body/prologue/opcode
allowlist/manifest), `$4A`-only-keeps-`$25`-stub matrix corner, combined
`$4A`+`$25` wiring, `cd_25` refusal, redirect-helper guards, span
boundaries (`$FFED` accept; `$FFEE`/`$FFF5`/`$FFF6`/bad-prologue/low-edge
refuse; span-length linkage pin), window supersets (base < 25 < both,
base < 4a < both, default entry unchanged).

## 4. Limits and non-goals (still UNKNOWN/BLOCKED)

U1-U10 of the contract doc are unchanged: genuine `$25` input encoding,
VDC target register/bits, work-RAM footprint, MPR/stack/flag/return
contract, timing/error paths, generation applicability, `$24`/`$26`/
`$27`/`$28` relation, normal-ROM OVERRIDES ceiling, `$25` entry-reg
observation (this task's SIMULATED-entry is conditional on fabricated
predecessors), second-core cross-check. B1 resolves none of them by
design. A future VDC-touching body remains BLOCKED until §9 criteria
are met; B1 must never be cited as `$25` semantics and never grounds
any caller branch. Commercial media: NOT_RUN (no `./rom` access).

## 5. Files

- `src/ex_scrmod.s` (new, B1 body, BOOT, MIT, original code)
- `tools/build_ipl_loader_g5_2base.py` (`cd_25` opt-in)
- `tools/build_experimental_bios_boot.py` (slot-25 redirect + windows)
- `spec/c3_static_contracts.json` (fixture classification only)
- `tests/host/test_build_g5_25_host.py` (new, 10 tests)
- `tests/integration/test_g5_scrmod_25.py` (new, 61 tests)
- `tests/integration/geargrafx_scrmod25_probe.cpp` (new, VDC probe)
- `tests/integration/geargrafx_scrmod25_writes_probe.cpp` (new, WRAM write tracer)
- `docs/47-ex-scrmod-b1.md` (this file)

---

*Provenance: original research + original code, MIT (repository LICENSE).
No proprietary firmware, game, or reference-BIOS content was used.*
