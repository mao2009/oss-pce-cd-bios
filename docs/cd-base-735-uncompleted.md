# CD_BASE uncompleted conditions (Issue #735)

Status: diagnostic-only. No System Card callee executes here; no BIOS ABI is
established. Slot `$02` / `$E006` remains HYPOTHESIS. `release_eligible=false`.
Genuine-callee JSR count is 0 of 81; unknown-ABI items stay BLOCKED.

This note confirms the uncompleted conditions the host suite pins. Fixtures
(`INDEX01=150` + `INDEX01=0` control) are reused from `tools/cd_base_probe.py`
(PR #750) and are not redefined here.

## Machine-readable condition table

| id | condition | expected | pinned by |
| --- | --- | --- | --- |
| U1 | no executable callee (fallback `$E006` record) | BLOCKED all of (1)-(6) | `test_cd_base_uncompleted_735.py::MutationUncompletedStaysTests::test_fallback_record_blocks_all_six_questions` |
| U2 | INDEX01=0 collapse (absolute == track-relative) | UNKNOWN (2), UNKNOWN (4) | `test_index01_zero_collapse_stays_unknown` |
| U3 | null READ rows do not count as blocks | UNKNOWN (2)/(4) | `test_null_read_and_single_block_never_observed` |
| U4 | single block read | UNKNOWN (2) | `test_null_read_and_single_block_never_observed` |
| U5 | non-constant offset across blocks | UNKNOWN (2) | `test_non_constant_offset_stays_unknown` |
| U6 | out-of-range caller block / fixture INDEX01 | ValueError, never OBSERVED | `test_out_of_range_block_rejected`, `test_out_of_range_index01_rejected` |
| U7 | non-integer block / LBA / INDEX01 | ValueError, never OBSERVED | `test_non_integer_block_and_lba_rejected` |
| U8 | invalid LBA never readable from either fixture | CueError | `test_invalid_lba_never_readable_from_fixtures` |
| U9 | boundary judge on any host-only input | BLOCKED, never OBSERVED/VERIFIED | `test_boundary_never_emits_observed_or_verified`, `test_boundary_judge_rejects_bad_block_fail_closed` |
| U10 | side-effect matrix host-only | all UNKNOWN even with callee claim | `test_side_effects_stay_unknown_and_release_not_eligible` |

Notes:

- `read_lba` is the harness-reported SCSI value: type-checked and reported
  verbatim (`test_out_of_range_read_lba_is_verbatim_never_verified`). It is
  never promoted to VERIFIED; the MSF `-150` synthetic offset exercises the
  candidate-naming rule only.
- Real-core execution (`tests/integration/geargrafx_cd_base.py`) remains the
  separate opt-in gate: both fixtures must report the fail-closed fallback
  (`BLOCKED` all six questions). No real-core run is claimed by this note;
  host tests are the evidence here (`make test` scope, no Geargrafx).
