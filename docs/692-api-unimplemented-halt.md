# #692: detecting the DEV unimplemented-API stub (halt classification)

Scope: measurement harness only. The DEV BIOS image bytes are NOT changed. `tools/build_rom.py` gains one `.export`
line (no bytes added; checked by SHA-256). No change to `src/` or `spec/`.
Synthetic media, pinned Geargrafx. NOT a System Card BIOS, NOT a BIOS ABI, NOT CD_BOOT / CD_READ verified,
`release_eligible=false`. No API becomes VERIFIED. No unimplemented API is treated as a success.

## Facts used

- The DEV jump table has one 3-byte JMP per slot at `$E000 + slot*3` (`tools/build_rom.py`).
- Slots with an override body in `src/overrides/slot_XX.s` are implemented; all others use the stub.
- The stub for an unimplemented slot is `LDX #slot; JMP api_unimplemented`, and `api_unimplemented` is `BRA *`.
- Slot 02 (spec name `CD_BASE`, `spec/api_slots.json`) has no override file, so it is unimplemented.
- The implemented slots are read from the file list at test time; the boundary is derived from that list, not hardcoded.

## Halt states (`tools/api_halt_classify.py`)

| state | meaning |
| --- | --- |
| `API_UNIMPLEMENTED_HALT` | the stub label is the reason, `halt_confirmed` is true, PC is the stub address, and X equals the expected slot. This is observation of a stub halt, NOT a success. |
| `API_SLOT_MISMATCH` | the stub was reached, but X is not the expected slot. Fail-closed. |
| `API_PC_MISMATCH` | the stop label is not at the stub address. Fail-closed. |
| `API_NOT_CONFIRMED` | the stub label is the reason but `halt_confirmed` is not true. Fail-closed. |
| `BUDGET` | the step budget ended with no confirmed halt. The reason is undetermined. |
| `CD_HALT_FAIL` / `CD_HALT_OK` / `OTHER` | reported as they are. |

A PASS exists only in the harness, when the observed state equals the expectation of that case.
`halt_confirmed=true` alone never counts as a success.

`API_UNIMPLEMENTED_HALT` is the name of an observed state: the run stopped at the stub and held there. It is NOT an
API success, NOT a program start success, and NOT evidence that the call did what it should. The name is a
state of the observation only. `halt_confirmed` means only that the probe saw the PC stay on the named halt label for
the confirmation window. It says nothing about whether an API call or a program start succeeded.

Existing failure states keep their meaning: `CD_HALT_FAIL` stays `cd_halt_fail`, and `CD_HALT_OK` stays `cd_halt_ok`.
A confirmed `cd_halt_ok` is the payload's own end label, not an API success claim.

## Probe change

`tests/integration/geargrafx_cdread_probe.cpp` recognises `api_unimplemented` as a halt label only when the label
file names it. Every other label file keeps the previous halt set.

## Export and build modes

- `tools/build_rom.py` exports `api_unimplemented`. An export adds no bytes: the DEV base image SHA-256 is the same
  before and after the change (`6a81ba20…`, both debug and release).
- Debug build: the label appears in `dev.lbl`.
- Release-equivalent build (no `-g`, `--mode release`): the label appears in the release `dev.lbl`.
- The harness in release mode is BLOCKED, not PASS: the loader's own labels (`reset`, `cd_halt_fail`, and others) are
  present only in debug builds (existing behaviour of the loader build, unchanged here). Absence of any required label
  is a BLOCKED result by design.

## Mutation checks (the harness must fail on broken expectations)

`python3 tests/integration/geargrafx_api_unimplemented.py --mutation <name>`; each must print `MUTATION_DETECTED`.

| mutation | what is broken | detected as |
| --- | --- | --- |
| `expect_wrong_slot` | expectation for slot 02 set to slot 03 | `API_SLOT_MISMATCH` (case FAILS) |
| `mark_before` | marker written before the call (a stub halt would show it) | marker check FAILS |
| `no_label` | `api_unimplemented` absent from the label file | `BUDGET`: the stub is not recognised, so the expectation FAILS |

## Tests

- `make api-unimplemented-integration` (`tests/integration/geargrafx_api_unimplemented.py`): a 256 KiB DEV image,
  synthetic media, cold and warm.
  - slot 02: `API_UNIMPLEMENTED_HALT`, X=2, the marker written after the call is NOT present (no return).
  - boundary: the last unimplemented slot below the first implemented slot: same rule with its own X.
  - boundary: the first implemented slot: the stub is NOT reached.
  - spin without a halt label: `BUDGET`, never the stub.
- `tests/host/test_api_halt_classify.py`: the fail-closed rules on fabricated records, and the ledger derivation.
- Existing 8 KiB and 256 KiB synthetic suites: unchanged expectations, still 35/35.

## Not in CI

The harness is not wired into a workflow. Workflow run steps are pinned by the trusted guard allowlist
(`tools/artifact_guard_run_allowlist.json`), so a new CI step needs a main-only allowlist change. Not done here.

## Not claimed

- Which slot is reached first in a real program: not established (only the state at the stop is observed).
- Any API behaviour, ABI, or compatibility.
- Commercial-media results are local only and are not in this document.
