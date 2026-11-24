# $24 EX_DOTMOD B1-analogous preservation-only scaffold (Issue #46, G5 P0 task)

Status: **B1-analogous SIMULATED scaffold; NOT a compatible System Card BIOS.
NOT genuine ABI. NOT DEV_CALLABLE. `release_eligible=false`.
G5_OBSERVED=false.** Reference JSR 0/81, genuine ABI VERIFIED 0/81
(unchanged by this task). No System Card image was executed,
disassembled, or copied. No game-disc bytes, titles, paths, hashes,
sector contents, audio, or saves appear here. `./rom/` was not accessed
in any form for this task (no existence check, listing, or read).

Branch basis: `0a935398` (origin/main at task start, PR #828 merged).
Contract basis: `docs/46-ex-dotmod-contract.md` (§4.2, §6.2, §6.3, §7,
§8, §9). This document records the B1-analogous design, its limits, and
the synthetic evidence; it changes no ledger and no genuine-ABI claim.

## 1. What B1-analogous is (and is not)

B1-analogous (`src/ex_dotmod.s`, `ex_dotmod_entry`, 9 bytes:
`PHP PHA PHX PHY PLY PLX PLA PLP RTS`) is a preservation-only
SIMULATED scaffold wired ONLY into G5 experimental images behind the
opt-in `cd_24="experimental"` flag of `tools/build_ipl_loader_g5_2base.py`
(default `cd_24="stub"` keeps every existing image byte-identical;
verified by the pinned pre-$24 baseline SHA in
`tests/host/test_build_g5_24_host.py`).

B1-analogous performs NO invented service and returns NO invented success
value:

- JSR `$E06C` (3-byte) -> RTS to caller+3; SP balanced (JSR 2 bytes +
  max 4 transient pushes; caller SP minimum `$06`; below minimum is out
  of contract and never run on the real core).
- A/X/Y preserved exactly; P preserved exactly for the verified domain
  (T=0) INCLUDING Carry (`C_in == C_out` on both seeds; no CLC/SEC
  fabricated, no ok/reject values). Latch-exact on the pinned core for T=0
  (HuC6280 `SetP` masks with `$EF`; plain PHP/PLP round trip restores
  N/V/D/I/Z/C exactly with T=0). PHP/PLP saves the stacked image, but the
  HuC6280 consumes the transfer flag at every instruction boundary, so
  the save contract and the T-consumption platform fact are distinct. No
  T-bit operation is added (no SEB/CLB/TST fabrications); T=1 entry is out
  of the verified calling contract.
- MPR0-MPR7 unchanged (scaffold executes no TAM/TMA-write/TXS;
  MPR1=`$F8` is a caller precondition validated by the harness BEFORE JSR
  per T13-analog; MPR1!=`$F8` is OUT OF CONTRACT/BLOCKED -- the
  check-then-act guard is proved by the MPR1 precondition test, and every
  real `$24` APICALL in every test carries MPR1=`$F8`).
- AR unchanged (scaffold executes no ST0/ST1/ST2); all 20 VDC registers
  unchanged on the pinned core (dedicated VDC-only probe, entry AR
  0/5/9/18; no dynamic VCE-state oracle — VCE `$0400` is write-only on the
  pinned core, so VCE invariance holds by construction: the 9-byte body
  contains no store opcode of any kind, hence no VCE write is possible);
  no PSG/timer/CD access.
- No work-RAM writes outside the transient caller-stack bytes (caller-side
  JSR 2-byte return push already written before entry + callee 4 transient
  pushes P/A/X/Y = 6 transient stack bytes total; SP is restored/balanced
  before RTS, stacked bytes are NOT scrubbed -- SP restore and stack-memory
  restore are distinct): 256-byte
  canary at `$2900`-`$29FF` intact plus guest-observed zp `$10` pre/post
  invariance on the real core, plus full-buffer cold/warm equality, PLUS
  per-instruction WRAM value-change history (the tracer records changed
  offsets at instruction boundaries, not write events: a same-value store
  is invisible; results are changed offsets, never a full dynamic write
  proof; the static 9-byte body pin -- no store opcode beyond the 4 pushes
  -- is the stronger guarantee, separated from the dynamic observation).
- No IRQ side effects (IDR/IRR unchanged across the call on the real core).
- P domain excludes T=1 (verified calling contract is P with T=0): the
  pinned core consumes the transfer flag
  every clock (`huc6280_inline.h` `Clock()`: `m_transfer_flag` snapshot
  + `ClearFlag(FLAG_TRANSFER)` when `GG_TESTING` is not defined), so an
  entry T=1 exits T=0 on ANY implementation regardless of the PHP/PLP save /
  restore. This is a pinned-core
  platform fact, not scaffold behavior; preservation is verified for all
  P bits except T (Carry and N/V/D/I/Z exactly, T=0), and T=1 entry is out
  of the verified domain with this
  ground (boundary test uses T=0 seeds `0x04`/`0x85`, same as B1; no T-bit
  manipulating code is added).

B1-analogous defines NO refusal path and NO VDC/VCE/shadow/mask behavior.
Contract applicability (§6.3): T01-T03, T05-T07, T10-T14 apply (GREEN proved
below); T04 (I/O window), T08 (shadow bypass), T09 (mask mutants) are
NOT_APPLICABLE with variant-grounded reason (scaffold has no I/O, no
shadow, no mask; requiring them would invent code paths).

Display-init debt: `$25` B1 + `$24` B1-analogous = two consecutive no-ops.
Real screen init (whatever genuine `$25`/`$24` do) remains entirely undone.
Debt is tracked here and in the contract doc U5, never presented as display
correctness. Diagnostic path advance (next blocker observed) and functional
compat are strictly distinguished per the task.

## 2. Wiring (opt-in, normal ROM untouched)

- `tools/build_experimental_bios_boot.py`: `_SLOT24_TABLE`,
  `_SLOT24_BODY_LEN` (= 9, tied to the structural pin),
  `_redirect_slot24_to_body` (fail-closed: linked-fallback check, BOOT
  span check `entry + 9 <= $FFF6`, PHP/PHA prologue check),
  `unified_api_windows_24`, `unified_api_windows_4a_24`,
  `unified_api_windows_25_24`, `unified_api_windows_4a_25_24` (base
  windows untouched; superset lattice base < 24 < 4a_24/25_24 <
  4a_25_24, base < 4a/25 < both).
- `tools/build_ipl_loader_g5_2base.py`: `cd_24` parameter
  (`"stub"`/`"experimental"`, refused otherwise), `DOTMOD_SOURCE/_ENTRY`,
  incremental label/source/link lists (the `(stub, stub, stub)` order is
  unchanged: proto/link/glue), patch + window checks per combination
  (4a/25/24 lattice), manifest `cd_24` + `experimental_slots` +
  `experimental_sources`.
- `spec/c3_static_contracts.json`: `src/ex_dotmod.s` classified as
  experimental-only fixture (never linked into the normal/production
  BIOS, not firmware code). No other ledger changes:
  `spec/api_contracts.json` slot 36 stays UNKNOWN/HYPOTHESIS,
  `spec/dev_implementations.json` id 36 stays `NOT_READY`/`null`
  (scaffold is never a VDC/VCE-meaningful DEV body), reference JSR
  stays 0/81, VERIFIED stays 0/81, DEV_CALLABLE stays false,
  `release_eligible=false` stays, G5_OBSERVED stays false.

## 3. Synthetic evidence (pinned Geargrafx `b49ae82a`, cc65 `71746c8`)

Real-core suite `tests/integration/test_g5_dotmod_24.py` (analogous to
`test_g5_scrmod_25.py`): host-side oracle negatives + synthetic
return-probe runs (PUBLIC IPL 2-base boot, `cd_4a`+`cd_25`+`cd_24`
experimental, cold+warm identical every run) + dedicated VDC-only probe
(`tests/integration/geargrafx_dotmod24_probe.cpp`: VDC R[0..19] + AR +
MPRs + IDR/IRR per-cycle baselines, cold then warm with ResetMedia between,
explicit cycle tags, 16 start-and-end halt confirmation; VCE `$0400` is
write-only so no dynamic VCE
oracle exists — VCE invariance is by construction, see §1) + dedicated WRAM
write-tracer probe
(`tests/integration/geargrafx_dotmod24_writes_probe.cpp`, per-instruction
write history, cold+warm).

- T01 stub: `cd_24="stub"` traps at `api_unimplemented` with X=`$24`,
  post-JSR marker `$00` (fail-closed intact).
- T02 mechanics: slots [`$24`,`$24`], entries `$E06C`, `in_table`,
  `sp_delta` 2, `stack_check` MATCH, `return_matches_caller` true (entry-time
  stacked-return evidence, not RTS-landing proof), SP
  balanced, cold/warm identical.
- T03 preservation: C=1 (`$07`) and C=0 (`$06`) classes GREEN with exact
  A/X/Y/P/MPRs/SP; A!=P mixed vector GREEN with independent A-vs-P
  checks; boundary values GREEN.
- T05 RAM scope: canary intact + zp pre/post intact + full-buffer
  cold/warm equality on every GREEN run, PLUS per-instruction WRAM
  value-change history: scaffold writes exactly the 6 transient stack bytes
  (caller-side JSR 2 + callee 4) and
  nothing else, cold and warm. A `$2200` store mutant is GREEN on
  final-value oracles yet RED on the history; a write-then-restore mutant
  likewise RED on history (history-vs-final distinction, not same-value
  detection). Same-value-store invisibility holds: the tracer records
  changed offsets at instruction boundaries, not write events, so a
  same-value `STZ $2200` is invisible to the dynamic observation; the
  dynamic result is changed offsets only, never a full write-event proof.
  The static 9-byte body pin (no store opcode beyond the 4 pushes) is the
  stronger guarantee, separated from the dynamic observation.
- T06 bad table: `$E06C` reroute to fallback RED.
- T07 Carry-preservation oracle (balanced, channel b): cavity bodies
  +CLC/+SEC before RTS; forced-CLC GREEN on C=0 / RED on C=1 as
  preservation mismatch; forced-SEC converse. Abnormal halts forbidden
  for these mutants; arrival, clean RTS, SP balance (checked via `_check_sp`
  on cold+warm in both RED and GREEN), cold/warm identity
  required.
- T10 stack smash (balanced zp shape): RED via guest-observed mismatch.
- T11 boundaries: preservation probe (no reject code); T=1 excluded (verified
  domain is P with T=0; boundary seeds `0x04`/`0x85` carry T=0).
- T12 SP depth: `$FF` and `$06` GREEN and balanced; below-minimum (`$00`,
  `$05`) refused pre-execution by the writes probe (exit 2, no RESULT),
  never run on the real core.
- T13 MPR1 pre-JSR: a requested `$FF` refuses at `refused_halt` before
  install (live MPR1 stays `$F8`) with zero `$24`
  APICALL in both cycles (strict validator: raw RUN list, truncations
  rejected, pc == refused_halt symbol, probe-emitted per-cycle observed
  count matched against parsed count, every record schema-checked);
  `$F8` positive control GREEN with proven
  arrival.
- T14: cold==warm every run; C=0/C=1 both GREEN; VDC-only probe covers
  entry AR variants cold+warm (per-cycle baselines, explicit cycle tags,
  16 start-and-end halt confirmation, fail-closed record schema with exact
  20/8 lengths and strict types/ranges; no dynamic VCE oracle; VCE by construction).
- Chain (synthetic G5 leg): `$33`->`$4A`->`$23`->`$25`->`$24` through the
  real table entries, per-call APICALL entry evidence (stacked-return bytes
  at entry time, not RTS-landing proof; APIRETURN is not collected),
  MPR1=`$F8` at every
  entry, end-to-end preservation (final records + halt). Entry state labeled SIMULATED-entry,
  never genuine inputs. No commercial continuation claimed. Per-call return
  resumption is NOT claimed from APICALL alone. The $24 9-byte pin excludes
  a stacked-return-rewriting trampoline for the $24 call only; companion
  legs ($33/$4A/$23/$25) are constrained by no pin here and their individual
  returns are not proved per-call.
- Structural pins ($24-scoped): 9-byte body pinned host + real-core; stub-default ROM
  pinned to pre-$24 baseline SHA; table routing pinned on all variant
  combinations.
- Oracle strictness: same strict-validator matrix as B1 (cold-duplicate,
  counts, reasons, budget, halt, PC, truncation, buffers, mutants,
  arrivals) — all rejected/accepted fail-closed, with exact-type schemas
  (bool-excluded ints, exact 20/8 array lengths, ranges) on VDC/APICALL
  records. Halt-confirmation boundary
  is proved dynamically on the real core
  (`test_writes_halt_boundary_15_vs_16_dynamic`: arrival established via pc,
  arrival+15 unconfirmed, arrival+16 confirmed, both cycles).

Host suite `tests/host/test_build_g5_24_host.py`: source presence,
stub-default table/manifest/body-absence, pre-$24 baseline byte-identity,
experimental wiring, `cd_24` refusal, redirect-helper guards, span
boundaries, window supersets.

## 4. Limits and non-goals (still UNKNOWN/BLOCKED)

U1-U8 of the contract doc are unchanged: genuine `$24` input encoding,
VCE/VDC target, work-RAM footprint, MPR/stack/flag/return contract,
timing/error paths, generation applicability, `$23`/`$25` relation,
`$26`/`$27`/`$28` relation, OVERRIDES ceiling, entry-reg observation
(SIMULATED-entry conditional on fabricated predecessors), second-core
cross-check. B1-analogous resolves none of them by design. A future
VDC/VCE-touching body remains BLOCKED until contract §9 D1 criteria are
met; the scaffold must never be cited as `$24` semantics and never grounds
any caller branch. Commercial media: addressed in Phase G only (no `./rom`
access for this design document).

## 5. Files

- `src/ex_dotmod.s` (new, B1-analogous body, BOOT, MIT, original code)
- `tools/build_ipl_loader_g5_2base.py` (`cd_24` opt-in)
- `tools/build_experimental_bios_boot.py` (slot-24 redirect + windows)
- `spec/c3_static_contracts.json` (fixture classification only)
- `tests/host/test_build_g5_24_host.py` (new)
- `tests/integration/test_g5_dotmod_24.py` (new)
- `tests/integration/geargrafx_dotmod24_probe.cpp` (new, VDC-only probe:
  cold+warm with per-cycle baselines, explicit cycle tags and halt
  confirmation; VCE write-only so invariance is by construction, not by dynamic read-back)
- `tests/integration/geargrafx_dotmod24_writes_probe.cpp` (new, tracer)
- `docs/46-ex-dotmod-b1.md` (this file, renamed from B1-analogous;
  file name keeps `-b1` suffix for parallel discovery with `47-*-b1.md`)

---

*Provenance: original research + original code, MIT (repository LICENSE).
No proprietary firmware, game, or reference-BIOS content was used.*
