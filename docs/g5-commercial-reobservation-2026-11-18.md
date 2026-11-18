# G5 commercial FIRST-mode re-observation — 2026-11-18

[← README](../README.md) · [Issue #802](https://github.com/mao2009/oss-pce-cd-bios/issues/802) · [Original sanitized observation comment](https://github.com/mao2009/oss-pce-cd-bios/issues/802#issuecomment-6977401984)

> **Observed API entry and stub halt, not game compatibility.** This document summarizes Owner-permitted, read-only, sanitized local observations already recorded under #802. Documentation authoring did not access `./rom`, media, reference BIOS or private logs.

## Fixed environment / version

- Target main: `fa694281` (PR #812 merged)
- Loader: `tools/build_ipl_loader_g5_2base.py`, default PUBLIC / `$02` 2-base / `$03` commercial FIRST / `$33` EX_DSPOFF, `$4A` unimplemented
- Pinned Geargrafx: `b49ae82a`; cc65: `71746c8`
- Commercial selector: index **0** and **1** explicitly; names, paths, hashes and bytes not public
- cold/warm: same API order, stop PC and verdict; confirmed halt within bounded 80M instruction budget

## Sanitized observations

| Checkpoint | CUE[0] | CUE[1] |
| --- | --- | --- |
| PUBLIC INFO + BODY | Read succeeded | Read succeeded |
| IPLJMP | Reached | Reached |
| Real table JSR prefix | `$02 → $33 → $4A` | `$1E → $4A` |
| API return observed | `$02`, `$33` (project experimental implementations) | `$1E` (DEV EX_GETVER) |
| First blocker | `$4A` / `api_unimplemented` | `$4A` / `api_unimplemented` |
| `$4A` genuine contract | UNKNOWN / BLOCKED | UNKNOWN / BLOCKED |
| Cold/warm | Match | Match |
| Commercial `$03` FIRST called? | **No** | **No** |
| Title screen | **Not observed** | **Not observed** |

The return from a **project-defined experimental implementation** is not evidence of verified System Card ABI. The `$4A` slot name is a generation-dependent candidate (KEY_BIOS / EX_MEMOPEN), **not** a verified official signature.

## Relationship to older observations

- Earlier normal experimental G5 image (PR #801): the same genuine prefix and `$4A` stub stop.
- PR #803 simulated `$4A` RTS/NOP diagnostic: CUE[0] proceeded conditionally to `$23 → $25`; CUE[1] to `$02 → $02 → $21 → $1C`. Those are **SIMULATED_NEXT_API**, not actual `$4A` semantics or gameplay.
- PRs #805/#806/#812 improved synthetic two-base/FIRST integration. The latest actual commercial prefix did **not** call `$03`: no claim of commercial CD_READ success follows.
- No measured regression or genuine execution advance relative to the earlier real-return prefix.

## Safety and evidence boundaries

Only anonymous CUE indices, API slot order, stop class and generalized CPU/stack facts appear here. Disc titles/editions, filenames, content, hashes, actual private memory traces, IP and proprietary assets stay private. Reference BIOS was not run or inspected. This is **not** a public CI fixture.

Safety/compatibility gates remain: reference JSR permitted 0/81; genuine ABI VERIFIED 0/81; owner-approved DEV_CALLABLE 0; `release_eligible=false`; `G5_OBSERVED=false`.

## Next action

[Issue #84](https://github.com/mao2009/oss-pce-cd-bios/issues/84): research the `$4A` generation-specific calling contract (parameters, flags, RAM/MPR/stack, effects and error behavior) from provenance-approved evidence, independently implement only justified functionality in a diagnostic variant and re-observe both local CUEs. No guessed success values or fabricated RTS may be promoted.

See also [evidence taxonomy](g5-evidence-2026-11-18.md), [API inventory](api-inventory.md), [provenance](provenance.md).
