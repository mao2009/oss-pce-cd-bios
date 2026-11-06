# #692: detecting the DEV unimplemented-API stub (halt classification)

Scope: measurement harness only. The DEV BIOS image is NOT changed (no change to `tools/build_rom.py`, `src/`, `spec/`).
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
| `API_UNIMPLEMENTED_CONFIRMED` | the stub label is the reason, `halt_confirmed` is true, PC is the stub address, and X equals the expected slot. This is observation of a stub halt, NOT a success. |
| `API_SLOT_MISMATCH` | the stub was reached, but X is not the expected slot. Fail-closed. |
| `API_PC_MISMATCH` | the stop label is not at the stub address. Fail-closed. |
| `API_NOT_CONFIRMED` | the stub label is the reason but `halt_confirmed` is not true. Fail-closed. |
| `BUDGET` | the step budget ended with no confirmed halt. The reason is undetermined. |
| `CD_HALT_FAIL` / `CD_HALT_OK` / `OTHER` | reported as they are. |

A PASS exists only in the harness, when the observed state equals the expectation of that case.
`halt_confirmed=true` alone never counts as a success.

## Probe change

`tests/integration/geargrafx_cdread_probe.cpp` recognises `api_unimplemented` as a halt label only when the label
file names it. Every other label file keeps the previous halt set.

## Tests

- `make api-unimplemented-integration` (`tests/integration/geargrafx_api_unimplemented.py`): a 256 KiB DEV image,
  synthetic media, cold and warm.
  - slot 02: `API_UNIMPLEMENTED_CONFIRMED`, X=2, the marker written after the call is NOT present (no return).
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
