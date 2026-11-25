# $36 EX_SATCLR B1-analogous preservation-only scaffold (Issue #64, G5 P0 task)

Status: **B1-analogous SIMULATED scaffold; NOT a compatible System Card BIOS.
NOT genuine ABI. NOT DEV_CALLABLE. `release_eligible=false`.
G5_OBSERVED=false.** Reference JSR 0/81, genuine ABI VERIFIED 0/81
(unchanged by this task). No System Card image was executed,
disassembled, or copied. No game-disc bytes, titles, paths, hashes,
sector contents, audio, or saves appear here. `./rom/` was not accessed
in any form for this task (no existence check, listing, or read).

Branch basis: `0a935398` (origin/main at task start, PR #828 merged).
Contract basis: `docs/64-ex-satclr-contract.md` (§4.2, §7, §8). This document records the B1-analogous design, its limits, and
the synthetic evidence; it changes no ledger and no genuine-ABI claim.

## 1. What B1-analogous is (and is not)

B1-analogous (`src/ex_satclr.s`, `ex_satclr_entry`, 9 bytes:
`PHP PHA PHX PHY PLY PLX PLA PLP RTS`) is a preservation-only
SIMULATED scaffold wired ONLY into G5 experimental images behind the
opt-in `cd_36="experimental"` flag of `tools/build_ipl_loader_g5_2base.py`
(default `cd_36="stub"` keeps every existing image byte-identical;
verified by the pinned pre-$36 baseline SHA in
`tests/host/test_build_g5_36_host.py`).

B1-analogous performs NO invented service and returns NO invented success
value:

- JSR `$E0A2` (3-byte) -> RTS to caller+3; SP balanced (JSR 2 bytes +
  max 4 transient pushes; caller SP minimum `$06`; below minimum is out
  of contract and never run on the real core).
- A/X/Y preserved exactly; P preserved exactly INCLUDING Carry in the
  verified domain (T=0; `C_in == C_out` on both seeds; no CLC/SEC fabricated,
  no ok/reject values). Latch-exact for N/V/D/I/Z/C (and T=0) on the pinned
  core (HuC6280 `SetP` masks with `$EF`; plain PHP/PLP round trip restores the
  verified bits exactly; T=1 entry is out of the verified domain, see below).
- MPR0-MPR7 unchanged (scaffold executes no TAM/TMA-write/TXS;
  MPR1=`$F8` is a caller precondition validated by the harness BEFORE JSR
  per T13-analog; MPR1!=`$F8` is OUT OF CONTRACT/BLOCKED -- the
  check-then-act guard is proved by the MPR1 precondition test, and every
  real `$36` APICALL in every test carries MPR1=`$F8`).
- AR unchanged (scaffold executes no ST0/ST1/ST2); all 20 VDC registers
  unchanged on the pinned core (dedicated VDC-only probe, entry AR
  0/5/9/18; no dynamic VCE-state oracle — VCE `$0400` is write-only on the
  pinned core, so VCE invariance holds by construction: the 9-byte body
  contains no MMIO/I/O store opcode (stack pushes write only transient WRAM), hence no VCE write is possible);
  no PSG/timer/CD access.
- No work-RAM writes outside the transient caller-stack bytes: 256-byte
  canary at `$2900`-`$29FF` intact plus guest-observed zp `$10` pre/post
  invariance on the real core, plus full-buffer cold/warm equality.
- No IRQ side effects (IDR/IRR unchanged across the call on the real core).
- P domain excludes T=1: the pinned core consumes the transfer flag
  every clock (`huc6280_inline.h` `Clock()`: `m_transfer_flag` snapshot
  + `ClearFlag(FLAG_TRANSFER)` when `GG_TESTING` is not defined), so an
  entry T=1 exits T=0 on ANY implementation. This is a pinned-core
  platform fact, not scaffold behavior; preservation is verified for all
  P bits except T, and T=1 entry is out of the verified domain with this
  ground (boundary test uses T=0 seeds `0x04`/`0x85`, same as B1).

B1-analogous defines NO refusal path and NO VDC/VCE/shadow/mask behavior.
Contract applicability (contract §4.2; design §3): T01-T03, T05-T07, T10-T14 apply (GREEN proved
below); T04 (I/O window), T08 (shadow bypass), T09 (mask mutants) are
NOT_APPLICABLE with variant-grounded reason (scaffold has no I/O, no
shadow, no mask; requiring them would invent code paths).

Display-init debt: `$25` B1 + `$24` B1-analogous + `$36` B1-analogous =
three consecutive no-ops (integrated main).
Real screen init (whatever genuine `$25`/`$36` do) remains entirely undone.
Debt is tracked here and in the contract doc U5, never presented as display
correctness. Diagnostic path advance (next blocker UNKNOWN on main; dependency-env
ExpD observation NOT_RUN until cited) and functional compat are strictly
distinguished per the task.

## 2. Wiring (opt-in, normal ROM untouched)

- `tools/build_experimental_bios_boot.py`: `_SLOT36_TABLE`,
  `_SLOT36_BODY_LEN` (= 9, tied to the structural pin),
  `_redirect_slot36_to_body` (fail-closed: linked-fallback check, BOOT
  span check `entry + 9 <= $FFF6`, PHP/PHA prologue check),
  `unified_api_windows_36`, `unified_api_windows_4a_36`,
  `unified_api_windows_25_36`, `unified_api_windows_4a_25_36` (base
  windows untouched; the integrated 16-variant lattice adds
  `_24`-containing supersets up to maximum `_4a_25_24_36`).
- `tools/build_ipl_loader_g5_2base.py`: `cd_36` parameter
  (`"stub"`/`"experimental"`, refused otherwise), `SATCLR_SOURCE/_ENTRY`,
  incremental label/source/link lists (the `(stub, stub, stub, stub)` order is
  unchanged: proto/link/glue), patch + window checks per combination
  (4a/25/24/36 lattice), manifest `cd_36` + `experimental_slots` +
  `experimental_sources`.
- `spec/c3_static_contracts.json`: `src/ex_satclr.s` classified as
  experimental-only fixture (never linked into the normal/production
  BIOS, not firmware code). No other ledger changes:
  `spec/api_contracts.json` slot 54 ($36) stays UNKNOWN/HYPOTHESIS,
  `spec/dev_implementations.json` id 54 ($36) stays `NOT_READY`/`null`
  (scaffold is never a VDC/VCE-meaningful DEV body), reference JSR
  stays 0/81, VERIFIED stays 0/81, DEV_CALLABLE stays false,
  `release_eligible=false` stays, G5_OBSERVED stays false.

## 3. Synthetic evidence (pinned Geargrafx `b49ae82a`, cc65 `71746c8`)

Real-core suite `tests/integration/test_g5_satclr_36.py` (analogous to
`test_g5_scrmod_25.py`): host-side oracle negatives + synthetic
return-probe runs (PUBLIC IPL 2-base boot, `cd_4a`+`cd_25`+`cd_36`
experimental, cold+warm identical every run) + dedicated VDC-only probe
(`tests/integration/geargrafx_satclr36_probe.cpp`: VDC R[0..19] + AR +
MPRs + IDR/IRR before/after; VCE `$0400` is write-only so no dynamic VCE
oracle exists — VCE invariance is by construction, see §1) + dedicated WRAM
write-tracer probe
(`tests/integration/geargrafx_satclr36_writes_probe.cpp`, per-instruction
write history, cold+warm).

- T01 stub: `cd_36="stub"` traps at `api_unimplemented` with X=`$36`,
  post-JSR marker `$00` (fail-closed intact).
- T02 mechanics: slots [`$36`,`$36`], entries `$E0A2`, `in_table`,
  `sp_delta` 2, `stack_check` MATCH, `return_matches_caller` true, SP
  balanced, cold/warm identical.
- T03 preservation: C=1 (`$07`) and C=0 (`$06`) classes GREEN with exact
  A/X/Y/P/MPRs/SP; A!=P mixed vector GREEN with independent A-vs-P
  checks; boundary values GREEN.
- T05 RAM scope: canary intact + zp pre/post intact + full-buffer
  cold/warm equality on every GREEN run, PLUS per-instruction WRAM write
  history (observed value changes only): scaffold writes exactly the 6 transient
  stack bytes and nothing else, cold and warm. A `$2200` store mutant is GREEN on
  final-value oracles yet RED on the history; a write-then-restore mutant
  likewise RED on history. Same-value-store invisibility (tracer records value
  diffs only) applies as in B1: the tracer result is observed value changes,
  same-value stores remain NOT_RUN/BLOCKED (unverified), never PASS.
- T06 bad table: `$E0A2` reroute to fallback RED.
- T07 Carry-preservation oracle (balanced, channel b): cavity bodies
  +CLC/+SEC before RTS; forced-CLC GREEN on C=0 / RED on C=1 as
  preservation mismatch; forced-SEC converse. Abnormal halts forbidden
  for these mutants; arrival, clean RTS, SP balance, cold/warm identity
  required.
- T10 stack smash (balanced zp shape): RED via guest-observed mismatch.
- T11 boundaries: preservation probe (no reject code); T=1 excluded.
- T12 SP depth: `$FF` and `$06` GREEN and balanced (guest-executed, SP restored); `SP=$00/$05` probe-argument refusal GREEN (execution-before-guest refusal, no guest run, no RESULT); `SP<$06` guest execution NOT_RUN/OUT_OF_CONTRACT (never run on the real core). Probe refusal is harness discipline, never EX_SATCLR body refusal (body defines no refusal; genuine error contract UNKNOWN).
- T13 MPR1 pre-JSR: `$FF` refuses at `refused_halt` with zero `$36`
  APICALL in both cycles; `$F8` positive control GREEN with proven
  arrival.
- T14: cold==warm every run; C=0/C=1 both GREEN; VDC-only probe covers
  entry AR variants (no dynamic VCE oracle; VCE by construction).
- Chain (synthetic G5 leg): `$33`->`$4A`->`$23`->`$25`->`$36` through the
  real table entries, per-call APICALL evidence, MPR1=`$F8` at every
  entry, end-to-end preservation. Entry state labeled SIMULATED-entry,
  never genuine inputs. No commercial continuation claimed.
- Structural pins: 9-byte body pinned host + real-core; stub-default ROM
  pinned to pre-$36 baseline SHA; table routing pinned on all variant
  combinations.
- Oracle strictness: same strict-validator matrix as B1 (cold-duplicate,
  counts, reasons, budget, halt, PC, truncation, buffers, mutants,
  arrivals) — all rejected/accepted fail-closed.

Host suite `tests/host/test_build_g5_36_host.py`: source presence,
stub-default table/manifest/body-absence, pre-$36 baseline byte-identity,
experimental wiring, `cd_36` refusal, redirect-helper guards, span
boundaries, window supersets.

## 4. Limits and non-goals (still UNKNOWN/BLOCKED)

U1-U8 of the contract doc are unchanged: genuine `$36` input encoding,
VCE/VDC target, work-RAM footprint, MPR/stack/flag/return contract,
timing/error paths, generation applicability, `$23`/`$25` relation,
`$26`/`$27`/`$28` relation, OVERRIDES ceiling, entry-reg observation
(SIMULATED-entry conditional on fabricated predecessors), second-core
cross-check. B1-analogous resolves none of them by design. A future
VDC/VCE-touching body remains BLOCKED until contract §8 D1 criteria are
met; the scaffold must never be cited as `$36` semantics and never grounds
any caller branch. Commercial media: addressed in Phase G only (no `./rom`
access for this design document).

## 5. Files

- `src/ex_satclr.s` (new, B1-analogous body, BOOT, MIT, original code)
- `tools/build_ipl_loader_g5_2base.py` (`cd_36` opt-in)
- `tools/build_experimental_bios_boot.py` (slot-36 redirect + windows)
- `spec/c3_static_contracts.json` (fixture classification only)
- `tests/host/test_build_g5_36_host.py` (new)
- `tests/integration/test_g5_satclr_36.py` (new)
- `tests/integration/geargrafx_satclr36_probe.cpp` (new, VDC-only probe:
  VCE write-only so invariance is by construction, not by dynamic read-back)
- `tests/integration/geargrafx_satclr36_writes_probe.cpp` (new, tracer)
- `docs/64-ex-satclr-b1.md` (this file, renamed from B1-analogous;
  file name keeps `-b1` suffix for parallel discovery with `47-*-b1.md`)

---

*Provenance: original research + original code, MIT (repository LICENSE).
No proprietary firmware, game, or reference-BIOS content was used.*
