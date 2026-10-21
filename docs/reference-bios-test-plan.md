# Pure System Card 3.0 BIOS: reference test scenario plan (not execution evidence)

This plan is deliberately **test-first**: generate a complete *candidate inventory* for all
81 main jump-table slots and the nine identified PSG subcommands, before implementing
or running an OSS BIOS service.

Generate locally:

```sh
python3 tools/reference_bios_scenarios.py --check
python3 tools/reference_bios_scenarios.py --output build/reference-bios/scenarios.json
python3 -m unittest tests.host.test_reference_bios_scenarios -v
```

The generator is **host-only**, reads `spec/api_slots.json` and does not read or run a BIOS.
Its output is a plan, **not a set of 270 executed test cases**, observations, expectations,
verified APIs, or hardware-compatibility proof.

## Protocol before a scenario may be run

1. Manually establish target BIOS edition and provenance, choose a pinned emulator
   and a separate isolated instance; record their hashes locally. Only use a BIOS
   image the user is entitled to use. Never put its bytes in Git, CI, logs or artifacts.
2. Confirm each candidate slot/PSG selector and its calling ABI from independent evidence.
   The `0xE000 + 3 * slot` entry is confirmed **only for slot `0x1E`** by local
   real-core measurement (see "Guarded real guest JSR"); for the other 80 slots it
   remains a hypothesis. The `0x48` PSG selectors are candidates, not
   independently validated dispatch semantics.
3. Replace `TO_BE_DETERMINED_FROM_ABI` with a *reviewed* per-API input fixture, set
   bounded instruction/frame budgets and a RAM write allowlist, and establish a way
   to reset state between cases. For a reset, jump, write, format, or destructive API,
   isolate storage/disc side effects and verify rollback behavior.
4. Implement an explicit *test-ROM* call wrapper, plus a real-core observer with
   registers, flags, SP, PC, MPR, bounded RAM diffs, and device-specific state.
   A host-generated constant or guest self-reported marker alone is not enough.
5. Execute the same fixture against the private reference BIOS. Store only
   non-copyrightable functional measurements and provenance metadata in a local,
   opt-in evidence store. Avoid dumping BIOS RAM/ROM contents.
6. Compare the OSS BIOS against the observed contract and report `MATCH`,
   `DIFF`, `NOT_RUN` and `BLOCKED` separately. Do not translate these
   into global compatibility `VERIFIED` without meeting the provenance gate.

## Scope and limits

- **81+9 definitions** each contain nominal, boundary and invalid/timeout
  *placeholders* requiring API-specific review. They are not runnable programs.
- **One** retail BIOS API has been measured locally and only against one
  candidate slot (EX_GETVER, slot `0x1E`). See
  "Guarded real guest JSR" below. Everything else remains `NOT RUN`.
  The measurement is local-only evidence, not a compatibility claim.
- The existing ABI ledger is authoritative about **uncertainty**; never fill
  unknown return values or side effects by guesswork.
- CD tests require independent synthetic media (CUE/BIN); private retail games
  and BIOS images are neither fixtures nor repository assets.
- No modification is made to `spec/api_contracts.json`, executable BIOS,
  release gates, attestation verification, or compatibility claims.

## Next independent issues

Build a versioned schema for individual input fixtures, a guarded Geargrafx
standalone execution adapter, local-only capture of pure functional outputs, and
a differential result comparator. Prioritize EX_GETVER, BCD conversions and
arithmetic functions for low-risk end-to-end examples, followed by CD_BASE,
CD_READ and CD_EXEC once the call contract is sufficiently understood.


## ABI-driven fixture worklist and safe preflight

```sh
python3 tools/reference_abi_worklist.py --output build/reference-bios/abi-worklist.json
python3 -m unittest tests.host.test_reference_bios_preflight -v
python3 tools/reference_bios_preflight.py local-reference-config.json
```

`reference_abi_worklist.py` extracts per-slot caller/callee hypotheses and evidence-source IDs
from `spec/api_contracts.json`; it **does not promote** hypotheses to measured expectations
or auto-fill runnable register/argument values. PSG selectors remain design-only until
their independent dispatch contract is established.

`reference_bios_preflight.py` validates a private per-case run configuration (digest
format, bounded instruction budget, RAM write allowlist, explicit ABI/fixture approval).
It does **not** open the BIOS, check file hashes against bytes, invoke Geargrafx,
enforce runtime memory writes, or perform any observation. A successful preflight prints
`PREFLIGHT_ONLY`, which cannot be interpreted as a BIOS test success.

A real adapter must independently enforce budgets/allowlists and hash private inputs
before executing any case; no unreviewed fixture may be auto-executed.


## Reference runner (adapter boundary only)

```sh
python3 -m unittest tests.host.test_reference_bios_runner -v
python3 tools/reference_bios_runner.py private-run-config.json \
    --adapter /absolute/path/to/your-audited-geargrafx-adapter \
    --output build/reference-bios/local-observation.json
```

The runner refuses missing adapters, compares the **actual bytes** of private BIOS
and test ROM against their configured SHA-256 digests, uses a 30-second process
timeout and validates the adapter's register/MPR/RAM-diff observation envelope.
The adapter must accept `--config PATH` and print exactly one JSON record
following `oss-pce-cd-bios/reference-observation/v1`. A valid record produces
only `OBSERVED LOCAL_ONLY`, never `VERIFIED`.

**Status:** `tools/geargrafx_reference_adapter.py` is now a real adapter. It
compiles and runs the instruction-stepping probes described below through
`compile_probe`, enforces a bounded instruction budget, restricts the caller's
RAM write allowlist to a single page-aligned interval, and emits an observed
register/MPR/RAM-diff envelope. Its observations are still *adapter-produced
JSON* and can lie; preflight and structural checks do not establish
provenance. Only the reset checkpoint and the single `slot-1e` call have been
exercised locally; the remaining 89 services are `NOT RUN`, and this module
remains a bounded subprocess integration seam, **not a complete retail-BIOS
reference test suite**.


## First real-core backend: private reference BIOS reset checkpoint

`tools/geargrafx_reference_adapter.py` now compiles and runs
`tests/integration/geargrafx_reference_reset.cpp` using the **existing pinned
Geargrafx debugger-enabled source build** via `compile_probe`. It loads the
user-provided BIOS with `LoadBiosFromBuffer(..., true)`, attaches a newly
generated zero-filled synthetic disc, runs **one** real CPU instruction and
captures PC/A/X/Y/P/SP/MPR. It uses the CLI's instruction-step debugger and
never reads/dumps proprietary BIOS bytes.

To run locally, supply a private config using the
`reference-bios-run-config/v1` schema, set `api_id` to the special
`reference-reset-only`, and ensure that BOTH private BIOS and test-ROM
digests point to real files (the test-ROM digest is a preflight integrity
guard; **the test ROM is not loaded or executed by this reset-only backend**):

```sh
python3 tools/reference_bios_runner.py local-reference-config.json \
  --adapter tools/geargrafx_reference_adapter.py \
  --output build/reference-bios/reset-local-observation.json
python3 -m unittest tests.host.test_geargrafx_reference_adapter -v
```

Requires locally compiled Geargrafx debugger objects and g++. Execution is
**NOT RUN** unless this command succeeds in the user's environment. A
successful result is only `OBSERVED LOCAL_ONLY` for reset, **not an API
test, not a successful guest JSR/RTS call, and not BIOS compatibility VERIFIED**.

**Remaining blocker (partially lifted):** a safe guest-side HuC6280 API caller
now executes while the reference BIOS is present, reaches a before/callee/after
checkpoint, and has its instruction budget, RAM write allowlist and I/O
isolation enforced *inside the core* for the single `slot-1e` path described
next. Every other `slot-XX` and `psg-48-XX` scenario still lacks a reviewed
fixture and stays **BLOCKED**.


## Guarded real guest JSR (slot 0x1E) and measured EX_GETVER contract

`tests/integration/geargrafx_reference_jsr.cpp` runs a real guest caller
(`LDA #$AA; LDX #$55; LDY #$CC; JSR $E05A; BRA *`) against the private
reference BIOS in the pinned Geargrafx debugger core, with three modes:

- `call` — normal before/callee/after observation,
- `neg-write` — the guest attempts a **forbidden** store into CD-ROM RAM and
  must be stopped *and* flagged by the core,
- `pos-write` — the guest performs an **allowed** store into work RAM and it
  must land (so "drop everything" cannot fake the negative test).

**In-core enforcement (not a Python flag).** Writes are gated by the core's
own `Memory::GetMemoryMapWrite()` map (prevention) and by CPU-address write
breakpoints from `HuC6280::AddBreakpoint` (detection), the MPRs are kept off
`0xFF` so I/O space is never writable by the guest, and execution is stepped
one instruction at a time so the budget is enforced as it runs. The negative
and positive modes are executed by
`tests/integration/geargrafx_reference_guard.py`, which uses a **fully
synthetic** 256 KiB BIOS and synthetic CD so CI can run it:

```sh
python3 tests/integration/geargrafx_reference_guard.py
# PASS real-core write guard: forbidden CD-ROM store prevented+detected;
#     allowed work-RAM store landed
```

**Core mapping checked against the HuC6280 manual (2026-10-21, Phase ABI-1):** in
the non-`GG_TESTING` build the core defines `ZERO_PAGE_ADDR = 0x2000` and
`STACK_ADDR = 0x2100` (`huc6280.h`), i.e. the zero page and the 8-bit stack
live in **page 1**, not page 0. The write allowlist therefore has to cover
`0x2000..0x4000`; a page-0-only allowlist wrongly rejects the legitimate `JSR`
stack push. This is **not** a core deviation: the HuC6280 manual places the
zero page at `$2000` (the high order byte of a zero page address always contains
the logical address `$20`) and the stack at `$2100`, and that placement does not
depend on the MPRs. The pinned core therefore matches the hardware here, and a
zero page operand of the BIOS behaves the same on the core and on hardware.

**Measured EX_GETVER contract (實測, private reference BIOS, real core,
deterministic across runs):**

| item | value |
| --- | --- |
| slot | `0x1E`, entry `$E05A` (jump table slot `0x5A`) |
| callee | Non-public BIOS internal implementation (observed via real core, not published) |
| returns | X = 3 (major), Y = 0 (minor) → "3.0"; **A preserved** (`0xAA` still `0xAA`) |
| flags after | `P = $06` (Z set because Y = 0) |
| SP after | `$FF` (balanced — the call returned) |
| instructions | 8 (including the sentinel caller prologue) |
| ram_diff | `$21FE`/`$21FF` only — the JSR push of the return address |

Run locally with a private config whose `api_id` is `slot-1e` and whose
allowlist is the page-1 interval `0x2000..0x4000`:

```sh
python3 tools/reference_bios_runner.py local-slot-1e-config.json \
  --adapter tools/geargrafx_reference_adapter.py \
  --output build/reference-bios/local-slot-1e-observation.json
python3 -m unittest tests.host.test_geargrafx_reference_adapter \
  tests.host.test_reference_bios_runner -v
```

This produces `OBSERVED LOCAL_ONLY` only when the core reports
`entry_reached`, `returned`, no guard violation, and the after-call registers
match the caller's sentinels. It is evidence for **one** API. It is not
`VERIFIED`, does not generalize to the other 89 services, and no BIOS bytes
are read, logged or stored. Independent ABI review of this slot against the
ABI ledger is still outstanding.

## Complete 90-service / 270-case manifest and dispatch report

```sh
python3 tools/reference_case_catalog.py --output build/reference-bios/all-270.json --summary
python3 tools/reference_case_dispatch.py build/reference-bios/all-270.json \
  --output build/reference-bios/dispatch-report.json
python3 -m unittest tests.host.test_reference_case_catalog -v
```

This generates exactly three specifically identified *categories* per each of 81
System Card API slots and nine provisional PSG subcommands. Every row includes
a stable case ID, candidate service, candidate entry/selector, source-linked
argument hypotheses, required fixture class, observer targets and a bounded-run
configuration placeholder. Unknown register values, mode arguments, initial
MPRs, expected results and execution budgets remain explicitly unset.

The dispatcher validates all 270 entries and produces a complete blocking/status
report. It does not invoke an emulator: until a reviewed, API-specific guest
fixture exists, **all 270 remain BLOCKED_ABI**, not PASS. The experimental
`slot-1e` real-core JSR backend is separate from this catalog and cannot
promote a category case automatically. Enabling 270 truly runnable cases still
requires per-API ABI fixture specification and audit, reference emulator
execution, and independent expected-value capture. Do not report the
generator as a complete executable test suite.


## Reviewed-case fixture validator (slot-1e execution enabled)

`tools/reference_case_execute.py` validates reviewed `slot-1e` input
fixtures and checks the precise synthetic CPU context, hashes/configuration
shape and instruction budget. It **does** invoke the adapter for the `slot-1e`
case, but only because the adapter path now has in-core write enforcement and
passing negative/positive guard tests (see above). A fixture that fails review
still fails closed, and the output record is labelled
`LOCAL_ONLY_NOT_VERIFIED`. The other 269 catalog cases remain non-executable.

```sh
python3 tools/reference_case_catalog.py --output build/reference-bios/all-270.json
# After separate ABI review, local fixture preparation can be checked.
# Reviewed slot-1e fixtures execute under the in-core write guard and emit
# a LOCAL_ONLY_NOT_VERIFIED record; unreviewed fixtures still fail closed:
python3 tools/reference_case_execute.py build/reference-bios/all-270.json \
  --case-id slot-1e/candidate_nominal \
  --config local-reference-config.json \
  --adapter tools/geargrafx_reference_adapter.py \
  --output build/reference-bios/local-slot-1e.json
python3 -m unittest tests.host.test_reference_case_execute -v
```

The `reference-reset-only` checkpoint remains distinct and available,
because it does not inject or execute an API JSR. This PR records plans,
bounded local-only reset observation, and **one** locally measured API
(EX_GETVER, slot `0x1E`), **not** retail BIOS compatibility, a verified
jump-table ABI, or 270 working runnable test ROMs.
