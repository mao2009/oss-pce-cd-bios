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
   The `0xE000 + 3 * slot` entry is only a hypothesis. The `0x48` PSG
   selectors are candidates, not independently validated dispatch semantics.
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
- **Zero** retail BIOS tests have been run by this PR.
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

**Important limitation:** this PR does not contain a real retail-BIOS capable
Geargrafx adapter or a HuC6280 API call test ROM. The existing diagnostic-only
Geargrafx probe cannot fill that role. A separately implemented and audited
adapter must actually step the CPU, enforce the instruction and RAM limits,
and capture genuine source-derived checkpoints. An adapter's JSON can still
lie, so preflight and structural checks do not establish provenance. This
module is therefore a bounded subprocess integration seam, **not yet a
working retail-BIOS reference test suite**.


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

**Remaining blocker:** a safe guest-side HuC6280 API caller must actually
execute while the reference BIOS is present, reach a separately observable
before/callee/after checkpoint, and enforce instruction/write budgets in
the core itself. That execution path is still unimplemented for all 90
functional API scenarios. Keep every `slot-XX` and `psg-48-XX`
blocked until then.


## Experimental real guest JSR caller (slot 0x1E only)

`tests/integration/geargrafx_reference_jsr.cpp` places an explicit, original
five-byte test program at guest work RAM `$2200`: `JSR $E05A; BRA *`.
It initializes a **synthetic** guest context (MPR1=$F8, MPR7=0, A/X/Y=0,
SP=$FF), then executes the JSR with the pinned real Geargrafx HuC6280 core.
The C++ probe emits BEFORE, ENTRY, AFTER snapshots and returns a failure when
the target entry or return checkpoint is absent. Python
`tools/geargrafx_reference_adapter.py` now accepts only
`api_id=slot-1e` as an experimental callable API candidate and sends the
result to the existing LOCAL_ONLY runner.

To try locally, select slot-1e in your reviewed private config, pin both
input file hashes, set a bounded `max_instructions` between 3 and 1000000,
and invoke `reference_bios_runner.py` with the Python adapter.

**Unverified assumptions:** the candidate EX_GETVER entry is $E05A and
its ABI permits this synthetic CPU context; this must be assessed by
observation. This experiment **does not** execute the test ROM supplied in
the config, does not run the normal BIOS boot initialization, does not yet
provide a true BEFORE/AFTER RAM diff, does not enforce write allowlists
during real-core execution, and does not cover the remaining 89 services.
Therefore even a completed JSR/RTS is **not** an ABI PASS or game compatibility
claim. C++ build and execution against a private reference BIOS remain NOT RUN
until performed locally.


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


## Reviewed-case runner bridge (strictly limited)

`tools/reference_case_execute.py` now connects a reviewed entry in the
270-case manifest to `reference_bios_runner.run()`. The first experimental
adapter is **only** `slot-1e`, with the synthetic guest context actually
hardcoded by `geargrafx_reference_jsr.cpp` (A=X=Y=0, SP=$FF, entry=$E05A).
It requires a separately reviewed local JSON fixture, a matching instruction
budget and an explicit private run configuration. All other API IDs and
different register fixtures are rejected, even if their catalog flag was
manually changed. This is an opt-in experiment, not automatic execution.

```sh
python3 tools/reference_case_catalog.py --output build/reference-bios/all-270.json
# After independent ABI review, edit the selected local case entry and supply a
# private run config. This command intentionally fails closed for unreviewed cases:
python3 tools/reference_case_execute.py build/reference-bios/all-270.json \
  --case-id slot-1e/candidate_nominal \
  --config local-reference-config.json \
  --adapter tools/geargrafx_reference_adapter.py \
  --output build/reference-bios/local-slot-1e.json
python3 -m unittest tests.host.test_reference_case_execute -v
```

The catalog still contains **270 planned rows, not 270 runnable test ROMs**.
Neither the actual HuC6280 API contract nor a trustworthy local System Card
reference run has been established. The JSR adapter and its initialization
remain experimental and need compilation and real-core confirmation.
