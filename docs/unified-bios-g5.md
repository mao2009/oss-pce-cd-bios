# Unified experimental BIOS: $02 CD_BASE proto + $33 EX_DSPOFF (G5 Agent A+B)

NOT a compatible System Card BIOS. NOT a BIOS ABI implementation. NOT release
eligible. DEV_CALLABLE 0, reference JSR 0/81, ABI VERIFIED 0/81. Issues
#173/#367/#459 intact: UNKNOWN/BLOCKED is never promoted.

## Goal

G5 = reach a commercial PC-Engine CD title screen. This union makes Agent-A's
CD_BASE $02 proto (PR #782, branch `origin/agent-a/cd-base-proto-g5` at
`5fe93c4b`) and Agent-B's EX_DSPOFF $33 fragment (PR #780, branch
`origin/agent-b/ex-dspoff-real-cpu-g5` at `323562fb`) coexist as REAL
processing in ONE experimental BIOS image, observable through harness D
(PR #778, ref `15ae4872`, read-only consumer).

## What now executes (G5 contribution)

Before: the experimental image booted synthetic media and handed off to a
payload, but every API call fell into a nonreturning stub (or a fake RTS in
measurement variants A/B/C). After: a test payload can

1. `JSR $E006` (slot $02) -> real CD_BASE-proto arithmetic/state handling in
   `src/cd_base_proto.s` (24-bit base vs INDEX01 compare, generation counter,
   offset/state in `$2400-$2407`), returns with `A=0/C=0` on DATA;
2. `JSR $E099` (slot $33) -> real project-own EX_DSPOFF processing in
   `src/ex_dspoff.s` (work-RAM shadow `$2502/$2503`, `AND #$3F` CR-low write,
   `C=0` ok / `C=1` unseeded reject), returns;
3. order markers prove 02-then-33, SP/MPRs intact, halt at `cd_halt_ok`
   (cold + warm repeatable).

Fake vs real distinction: a bare RTS at either table entry could return but
could not produce the callee-exclusive side effects (proto state update,
dspoff shadow update + flag discipline). Both table entries are `JMP`
(`0x4C`); both bodies start with real processing, never `RTS`.

## Union mechanics (`tools/build_experimental_bios_boot.py`)

- `REQUIRED` carries both `cd_base_proto_entry` and `experimental_slot_33`.
- Link units: `experimental_bios_boot.s` (+ selected record) +
  `cd_load_exec_probe.s` + `src/cd_base_proto.s`. The $33 body arrives via the
  `experimental_slot_33: .include "ex_dspoff.s"` glue in
  `src/experimental_bios_boot.s` (single source, no duplicated logic).
- Ordered fail-closed patches: slot-$02 5-byte `LDX #$02; JMP stub` ->
  `JMP proto; NOP; NOP`, then slot-$33 3-byte `$E099: JMP stub` -> `JMP body`.
  Each patch verifies its pre-image (fallback bytes / linked stub / `PHP,PHA`
  prologue) and refuses anything else.
- Pre-patch bank0 must equal the normal DEV ROM across the first 4 KiB (both
  callees live in BOOT, offsets `>= 0x1000`); the normal ROM is byte-identical.
- Post-image API-area assertion: every delta vs the normal DEV ROM lies inside
  the union of the two windows, both windows take effect, and both windows are
  verified byte-exact. A window byte that coincidentally matches (e.g. a shared
  low target byte, or the shared `JMP` opcode) is tolerated; any unexpected
  delta fails. BOOT divergence is expected and covered by the reset-vector/SEI
  checks.

## Harness-D native manifest fields

The builder derives everything at build time (never hardcoded or guessed):

- `branch` / `head`: `git rev-parse --abbrev-ref HEAD` / `HEAD` (detached-HEAD
  fallback names the HEAD; non-40-hex fails closed).
- `merged_heads`: `git rev-parse` of `origin/agent-a/cd-base-proto-g5` and
  `origin/agent-b/ex-dspoff-real-cpu-g5`; a ref absent from the build clone is
  recorded `UNRESOLVED`, never guessed.
- `pinned_cc65` / `pinned_geargrafx`: `tools/environment.py DEPENDENCIES`.
- `labels` (+ `symbols` superset): link-derived `api_slot_02`, `api_slot_33`,
  `api_unimplemented` (verified stub address for halt classification),
  `cd_base_proto_entry`, `experimental_slot_33`, table entries.
- Native blocks `cd_base_proto {slot $02, entry $E006}` and
  `experimental_slot_33 {table_entry 0xE099, body ...}` derive slots
  `(0x02, 0x33)`; `selection_contract.lba` + `media_identity` and
  `default_request.lba` bind the embedded medium for the harness
  embedded-vs-executed cross-check (mismatch -> BLOCKED).

## Verification status

- Host: `tests/host/test_unified_builder.py` (19 tests) — union patch order,
  window exactness, provenance derivation, manifest contract. PASS without cc65.
- Real-core: `tests/integration/test_unified_realcore.py` — loader success,
  both real arrivals/returns, order, 5-gate `REAL_SERVICE_RETURN` proof,
  harness-D pure validators (materialized read-only from pinned ref
  `15ae4872`), commercial NOT_RUN. Requires pinned Linux + cc65 +
  Geargrafx; SKIPs otherwise (BLOCKED in this environment, see below).
- Commercial: `./rom` absent -> NOT_RUN. No bytes/hashes/paths/traces published.

## Environment record (this worker)

- Before HEAD `0c7157e7` (= `origin/main`); PR heads confirmed with no drift:
  `#782 = 5fe93c4b`, `#780 = 323562fb`, `#778 = 15ae4872`.
- Host: Windows, no pinned cc65/Geargrafx; `make setup` outcome recorded in
  the integration Issue. Real-core: BLOCKED (no toolchain; never substituted
  with host PASS).
- Remaining blocker (single next item): a pinned-Linux run of
  `test_unified_realcore.py` + harness-D variant-D measurement.
