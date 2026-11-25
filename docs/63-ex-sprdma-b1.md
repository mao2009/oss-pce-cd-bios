# $35 EX_SPRDMA B1-analogous preservation-only scaffold (Issue #63, G5 P0 task)

Status: **B1-analogous SIMULATED scaffold; NOT a compatible System Card BIOS.
NOT genuine ABI. NOT DEV_CALLABLE. `release_eligible=false`.
G5_OBSERVED=false.** Reference JSR 0/81, genuine ABI VERIFIED 0/81
(unchanged by this task). No System Card image was executed,
disassembled, or copied. No game-disc bytes, titles, paths, hashes,
sector contents, audio, or saves appear here. `./rom/` was not accessed
in any form for this task (no existence check, listing, or read).

Branch basis: `65b0cb56` (origin/main at task start, PR #832 merged, integrated
main with `cd_4a`+`cd_25`+`cd_24`+`cd_36`).
Contract basis: `docs/63-ex-sprdma-contract.md` (§4.2, §7, §8). This document records the B1-analogous design, its limits, and
the synthetic evidence; it changes no ledger and no genuine-ABI claim.

## 1. What B1-analogous is (and is not)

B1-analogous (`src/ex_sprdma.s`, `ex_sprdma_entry`, 9 bytes:
`PHP PHA PHX PHY PLY PLX PLA PLP RTS`) is a preservation-only
SIMULATED scaffold wired ONLY into G5 experimental images behind the
opt-in `cd_35="experimental"` flag of `tools/build_ipl_loader_g5_2base.py`
(default `cd_35="stub"` keeps every existing image byte-identical;
verified by the pinned pre-$35 baseline SHA in
`tests/host/test_build_g5_35_host.py`).

B1-analogous performs NO invented service and returns NO invented success
value:

- JSR `$E09F` (3-byte) -> RTS to caller+3; SP balanced (JSR 2 bytes +
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
  real `$35` APICALL in every test carries MPR1=`$F8`).
- AR unchanged (scaffold executes no ST0/ST1/ST2); all 20 VDC registers
  unchanged on the pinned core (dedicated VDC-only probe, entry AR
  0/5/9/18; no dynamic VCE-state oracle — VCE `$0400` is write-only on the
  pinned core, so VCE invariance holds by construction: the 9-byte body
  contains no MMIO/I/O store opcode (stack pushes write only transient WRAM), hence no VCE write is possible);
  no PSG/timer/CD access.
- No work-RAM writes outside the transient caller-stack bytes: 256-byte
  canary at `$2900`-`$29FF` intact plus guest-observed zp `$10` pre/post
  invariance on the real core, plus full-buffer cold/warm equality.
- No `$2214`/`$2215` read or write (the Zeograd one-liner source word is
  explicitly NOT touched; a future D1 body would need ownership/value/
  timing grounds first). No VDC #19 (DVSSR) write, no SATB DMA start, no
  DMA wait, no VDC #15 touch, no `$2243` touch (that belongs to `$34`).
- No IRQ side effects (IDR/IRR unchanged across the call on the real core).
- P domain excludes T=1: the pinned core consumes the transfer flag
  every clock, so an entry T=1 exits T=0 on ANY implementation. This is a
  pinned-core platform fact, not scaffold behavior; preservation is verified
  for all P bits except T, and T=1 entry is out of the verified domain with
  this ground (boundary test uses T=0 seeds `0x04`/`0x85`, same as B1).

B1-analogous defines NO refusal path and NO VDC/VCE/shadow/mask behavior.
Contract applicability (contract §4.2; design §3): T01-T03, T05-T07, T10-T14 apply (GREEN proved
below); T04 (I/O window), T08 (shadow bypass), T09 (mask mutants) are
NOT_APPLICABLE with variant-grounded reason (scaffold has no I/O, no
shadow, no mask; requiring them would invent code paths).

Display-init debt: `$25` B1 + `$24` B1-analogous + `$36` B1-analogous +
`$35` B1-analogous = four consecutive no-ops (integrated main).
Real screen init (whatever genuine `$25`/`$35`/`$36` do) remains entirely undone.
Debt is tracked here and in the contract doc, never presented as display
correctness. Diagnostic path advance (next blocker UNKNOWN until ExpE
commercial observation) and functional compat are strictly distinguished
per the task.

## 2. Wiring (opt-in, normal ROM untouched)

- `tools/build_experimental_bios_boot.py`: `_SLOT35_TABLE`,
  `_SLOT35_BODY_LEN` (= 9, tied to the structural pin),
  `_redirect_slot35_to_body` (fail-closed: linked-fallback check, BOOT
  span check `entry + 9 <= $FFF6`, PHP/PHA prologue check),
  `unified_api_windows_35` plus all `_35`-containing supersets up to
  maximum `_4a_25_24_36_35` (base windows untouched; 32-variant lattice).
- `tools/build_ipl_loader_g5_2base.py`: `cd_35` parameter
  (`"stub"`/`"experimental"`, refused otherwise), `SPRDMA_SOURCE/_ENTRY`,
  incremental label/source/link lists (the `(stub x4)` order is
  unchanged: proto/link/glue), patch + window checks per combination
  (4a/25/24/36/35 lattice), manifest `cd_35` + `experimental_slots` +
  `experimental_sources`.
- `spec/c3_static_contracts.json`: `src/ex_sprdma.s` classified as
  experimental-only fixture (never linked into the normal/production
  BIOS, not firmware code). No other ledger changes:
  `spec/api_contracts.json` slot 53 ($35) stays UNKNOWN/HYPOTHESIS,
  `spec/dev_implementations.json` id 53 ($35) stays `NOT_READY`/`null`
  (scaffold is never a VDC/VCE-meaningful DEV body), reference JSR
  stays 0/81, VERIFIED stays 0/81, DEV_CALLABLE stays false,
  `release_eligible=false` stays, G5_OBSERVED stays false.

## 3. Synthetic evidence (pinned Geargrafx `b49ae82a`, cc65 `71746c8`)

Real-core suite `tests/integration/test_g5_sprdma_35.py` (analogous to
`test_g5_satclr_36.py`): host-side oracle negatives + synthetic
return-probe runs (PUBLIC IPL 2-base boot, `cd_4a`+`cd_25`+`cd_24`+`cd_36`+`cd_35`
experimental, cold+warm identical every run) + dedicated VDC-only probe
(`tests/integration/geargrafx_sprdma35_probe.cpp`: VDC R[0..19] + AR +
MPRs + IDR/IRR before/after; VCE `$0400` is write-only so no dynamic VCE
oracle exists — VCE invariance is by construction, see §1) + dedicated WRAM
write-tracer probe
(`tests/integration/geargrafx_sprdma35_writes_probe.cpp`, per-instruction
write history, cold+warm).

- T01 stub: `cd_35="stub"` traps at `api_unimplemented` with X=`$35`,
  post-JSR marker `$00` (fail-closed intact).
- T02 mechanics: slots [`$35`,`$35`], entries `$E09F`, `in_table`,
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
  likewise RED on history; a `$2214` store mutant (the one-liner address)
  likewise RED on history. Same-value-store invisibility (tracer records value
  diffs only) applies as in B1: the tracer result is observed value changes,
  same-value stores remain NOT_RUN/BLOCKED (unverified), never PASS.
- T06 bad table: `$E09F` reroute to fallback RED.
- T07 Carry-preservation oracle (balanced, channel b): cavity bodies
  +CLC/+SEC before RTS; forced-CLC GREEN on C=0 / RED on C=1 as
  preservation mismatch; forced-SEC converse. Abnormal halts forbidden
  for these mutants; arrival, clean RTS, SP balance, cold/warm identity
  required.
- T10 stack smash (balanced zp shape): RED via guest-observed mismatch.
- T11 boundaries: preservation probe (no reject code); T=1 excluded.
- T12 SP depth: `$FF` and `$06` GREEN and balanced (guest-executed, SP restored); `SP=$00/$05` probe-argument refusal GREEN (execution-before-guest refusal, no guest run, no RESULT); `SP<$06` guest execution NOT_RUN/OUT_OF_CONTRACT (never run on the real core). Probe refusal is harness discipline, never EX_SPRDMA body refusal (body defines no refusal; genuine error contract UNKNOWN).
- T13 MPR1 pre-JSR: `$FF` refuses at `refused_halt` with zero `$35`
  APICALL in both cycles; `$F8` positive control GREEN with proven
  arrival.
- T14: cold==warm every run; C=0/C=1 both GREEN; VDC-only probe covers
  entry AR variants (no dynamic VCE oracle; VCE by construction).
- Chain (synthetic G5 leg): `$33`->`$4A`->`$23`->`$25`->`$24`->`$36`->`$35` through the
  real table entries, per-call APICALL evidence, MPR1=`$F8` at every
  entry, end-to-end preservation. Entry state labeled SIMULATED-entry,
  never genuine inputs. No commercial continuation claimed.
- Structural pins: 9-byte body pinned host + real-core; stub-default ROM
  pinned to pre-$35 baseline SHA; table routing pinned on all variant
  combinations.
- Oracle strictness: same strict-validator matrix as B1 (cold-duplicate,
  counts, reasons, budget, halt, PC, truncation, buffers, mutants,
  arrivals) — all rejected/accepted fail-closed.

Host suite `tests/host/test_build_g5_35_host.py`: source presence,
stub-default table/manifest/body-absence, pre-$35 baseline byte-identity,
experimental wiring, `cd_35` refusal, redirect-helper guards, span
boundaries, window supersets.

## 4. Limits and non-goals (still UNKNOWN/BLOCKED)

U1-U9 of the contract doc are unchanged: genuine `$35` input encoding,
`$2214`/`$2215` ownership, VDC #19 write value/timing/completion, SATB DMA
start/wait/sync, VDC #15 / `$2243` relation (`$34` only), shadow, MPR/stack/
flag/return contract, timing/error paths, generation applicability, display
path relation. B1-analogous resolves none of them by design. A future
VDC-touching body remains BLOCKED until contract §8 D1 criteria are met;
the scaffold must never be cited as `$35` semantics and never grounds any
caller branch. The `$2214` non-touch is a scaffold property (no I/O), not a
genuine claim that `$35` ignores `$2214`. Commercial media: BLOCKED_PERMISSION
for this task unless Owner grants explicit opt-in (see contract §8(f)).

## 5. Files

- `src/ex_sprdma.s` (new, B1-analogous body, BOOT, MIT, original code)
- `tools/build_ipl_loader_g5_2base.py` (`cd_35` opt-in)
- `tools/build_experimental_bios_boot.py` (slot-35 redirect + windows)
- `spec/c3_static_contracts.json` (fixture classification only)
- `tests/host/test_build_g5_35_host.py` (new)
- `tests/integration/test_g5_sprdma_35.py` (new)
- `tests/integration/geargrafx_sprdma35_probe.cpp` (new, VDC-only probe:
  VCE write-only so invariance is by construction, not by dynamic read-back)
- `tests/integration/geargrafx_sprdma35_writes_probe.cpp` (new, tracer)
- `docs/63-ex-sprdma-b1.md` (this file)

---

*Provenance: original research + original code, MIT (repository LICENSE).
No proprietary firmware, game, or reference-BIOS content was used.*
