# Implementation status — 2026-10-22 main snapshot (`f90c16b`, state verified 2026-10-23)

This is an evidence-scoped, **dated snapshot**, not a real-time compatibility report. Source tree: [main at `f90c16b`](https://github.com/mao2009/oss-pce-cd-bios/commit/f90c16b6ca7b3cd6f0ee66935aa08d43c329628d), after #475, #477, #495 (MA_MUL16S), #502 and #506 merged. Recheck main and [Actions](https://github.com/mao2009/oss-pce-cd-bios/actions) for later changes. Historical progress is available in [git history](https://github.com/mao2009/oss-pce-cd-bios/commits/main/docs/status.md). Earlier snapshots (2026-10-20, 2026-10-21 and 2026-10-22; historical `86172e2` / `8a136ad`: 8 DEV implementations) are superseded, not retroactively changed.

**Current baseline (2026-10-28, main 2954e90 after PR #527):** 13/81 DEV implementations and 68 nonreturning stubs. PR #522 (MA_SIN $45) and PR #527 (MA_COS $46) are merged. Draft PR #524 (Issue #68, EX_SETWRT $3A) is **not merged**; if merged, the count would be 14/81 and 67 stubs. The sections below are the historical snapshot (main f90c16b: 9/81, 72 stubs) and are labelled as such.

## Six separate metrics

Measured on main f90c16b: `src/overrides/slot_*.s` contains **9** files, `spec/dev_implementations.json` has **81** slots, **9** with a non-null `implementation`, and **0** owner approvals, and the 72 stubs are the remaining 81 − 9 entries.

| Metric | Confirmed on main | Meaning / limits |
| --- | ---: | --- |
| A. DEV ROM implemented entry points | **9/81** | OSS/project-defined code installed at nine candidate slots on main f90c16b; not approved/compatible firmware |
| B. DEV ROM nonreturning stubs | **72/81** | Remaining candidate entries on main f90c16b do not implement callable firmware |
| C. Recorded reference ROM static ABI-1 review | **7** | Static facts only; reference compatibility not independently proven |
| D. Reference BIOS ABI VERIFIED | **0/81** | No API promoted to VERIFIED |
| E. Owner-approved DEV_CALLABLE | **0** | Implementation count is not owner approval; all 81 ledger states remain NOT_READY |
| F. Reference JSR execution permitted | **0/81** | `tools/reference_jsr_gate.py --check` remains fail-closed / BLOCKED |

Other results: **0 verified BIOS-mediated CD program boots; 0 retail game compatibility results; release_eligible = false**. Do not collapse DEV implementation, static review, synthetic real-core observation, reference ABI VERIFIED, owner approval, or executable production firmware into one progress percentage.

## Implemented DEV ROM entries

These nine entries are **DEV implementations** on main f90c16b. They are separate from reference ABI VERIFIED (0/81).

| Slot | API | Current scope |
| --- | --- | --- |
| $1E | EX_GETVER | Pinned Geargrafx cold/warm caller-return and selected register/flag effects |
| $3C | EX_BINBCD | Project-own DEV conversion routine, host and core diagnostic evidence |
| $3D | EX_BCDBIN | Project-own DEV conversion routine, host and core diagnostic evidence |
| $3F | MA_MUL8U | Project-own DEV math routine; not the proven System Card ABI |
| $40 | MA_MUL8S | Project-own DEV math routine; not the proven System Card ABI |
| $41 | MA_MUL16U | Project-own DEV math routine; not the proven System Card ABI |
| $42 | MA_DIV16S | Project-own stack-based DEV math contract |
| $43 | MA_DIV16U | [#480](https://github.com/mao2009/oss-pce-cd-bios/pull/480): project-own unsigned division, caller I/D flag combinations, stack and WRAM final-state checks, real-core mutants |
| $4F | MA_MUL16S | [#495](https://github.com/mao2009/oss-pce-cd-bios/pull/495) (Issue #494): project-own signed 16x16 to 32-bit multiplication; caller P/Y/S, MPR, ZP/BSS/I/O and frame-external stack checked on pinned Geargrafx, including a write/read trace of the routine's own instructions; real-core mutants of the assembled routine. Manual `make dev-mul16s-sweep` covers 1,572,864 calls. Not the proven System Card ABI |

On the Issue #78 PR branch, MA_SQRT ($44) is added as a tenth DEV implementation: project-own integer square root DEV contract (bit-by-bit restoring, trial = 4*root + 1), host-exhaustive 65,536-input check and real-core boundary/mutant tests on pinned Geargrafx. Not the proven System Card ABI.

## Recent merged evidence and safety limitations

- [#480](https://github.com/mao2009/oss-pce-cd-bios/pull/480) integrates MA_DIV16U ($43). Independent review recommended the code; the PR's pinned-core positive tests cover 25 values across all four I/D combinations plus two low-SP boundary runs (**102 total positive executions**); divide-by-zero and mutation negatives are separate. This is **DEV spec correctness**, not reference System Card ABI parity.
- [#481](https://github.com/mao2009/oss-pce-cd-bios/pull/481) adds a **synthetic HuCARD** stack-contract experiment. With MPR1=$F8 WRAM, the fixed caller preserved A/X/Y/P/S/MPR and returned; with MPR1=$FF I/O, the JSR and guard's pushes changed VDC state before the guard detected a violation. VDC observations are value changes, **not** a complete I/O bus trace; the I/O-popped RTS target, real-hardware IRQ/TMA and NMI behavior remain unverified. A pinned-core IRQ1 probe observed dispatch on the final TII completion step. This is **not** a working production safety mechanism.
- [#502](https://github.com/mao2009/oss-pce-cd-bios/pull/502) adds a **DEV-only internal boot path** (synthetic CD, READ(6), RAM, jump, marker). [#506](https://github.com/mao2009/oss-pce-cd-bios/pull/506) wires its regression into the `core-harness.yml` CI job. It is a DEV path, **not** the BIOS CD_BOOT / CD_READ / CD_EXEC service and not a commercial-game boot.
- [#473](https://github.com/mao2009/oss-pce-cd-bios/issues/473) still requires production-safe block-transfer/stack handling. [#459](https://github.com/mao2009/oss-pce-cd-bios/issues/459) and [#367](https://github.com/mao2009/oss-pce-cd-bios/issues/367) remain BLOCKED. [#482](https://github.com/mao2009/oss-pce-cd-bios/issues/482) tracks an optional LOW strengthening of synthetic VDC record PC correlation.
- Geargrafx guest SCSI READ(6) to RAM and self-authored CD-to-RAM execution have been observed in isolated diagnostics; they do **not** implement callable CD_RESET/CD_BASE/CD_READ, System Card startup/boot, or establish retail-game support. BIOS software release remains deliberately refused.

## Merged evidence PRs and source identity

- [#475](https://github.com/mao2009/oss-pce-cd-bios/pull/475) (merged 2026-10-22): EX_GETVER synthetic observation schema/validation. A record digest is an **integrity check, not producer authentication**. It does not promote reference ABI status or grant reference ROM execution.
- [#477](https://github.com/mao2009/oss-pce-cd-bios/pull/477) (merged 2026-10-22): `docs/evidence/reference-static-triage-81.json`, a static 81-entry jump-table profile (`scope: STATIC_PROFILE_ONLY`; all 81 entries are `JMP_ABS`; `execution_permission: NOT_PRODUCED_BY_THIS_TOOL`). It is **static triage**, **not** reference ABI VERIFIED and not execution permission.
- [#476](https://github.com/mao2009/oss-pce-cd-bios/issues/476) (OPEN, BLOCKED): private local headerless 262,144-byte reference payload (CRC32 6D9A73EF) matched the pinned Geargrafx database label and reproduced seven earlier static facts, but the historical headered 262,656-byte SHA-256 has **not** been independently reconciled. CRC32 equivalence is not cryptographic provenance and not proof of full ROM identity.

## PR and issue state reconciliation (2026-10-23)

| Item | State on 2026-10-23 | Public-doc wording |
| --- | --- | --- |
| #475 EX_GETVER observation schema | MERGED (2026-10-22) | Merged evidence, not ABI status |
| #477 81-entry static profile | MERGED (2026-10-22) | Static triage only; not VERIFIED |
| #495 MA_MUL16S ($4F), Issue #494 | MERGED (2026-10-22); #494 CLOSED | Counted in main f90c16b: 9/81 |
| #502 DEV-only internal boot path, Issue #501 | MERGED (2026-10-22); #501 CLOSED | DEV path, not BIOS boot |
| #506 CI wiring, Issue #505 | MERGED (2026-10-22); #505 CLOSED | Wired in `core-harness.yml` |
| #476 reference ROM identity | OPEN, BLOCKED | Unresolved |
| #473, #459, #367 safety gates | OPEN, BLOCKED | Unresolved |
| #324 ABI evidence-axis policy | OPEN | Not approved |
| #40 EX_GETVER API issue | OPEN | Reference ABI not verified |

## Build/test distinctions

| Command / artifact | What it means |
| --- | --- |
| `make setup`, `make build`, `make test`, `make test-real-core`, `make check` | Original 8 KiB synthetic HuCARD and host/real-core diagnostics, not a compatibility ROM |
| `make dev-rom` | Development-only 256 KiB image; on main 2954e90: 13/81 DEV implementations and 68 stubs; if unmerged Draft PR #524 is merged: 14/81 and 67 stubs. Historical f90c16b: nine project-owned overrides ($1E, $3C, $3D, $3F, $40, $41, $42, $43, $4F) + 72 nonreturning stubs on main f90c16b; 14/81 DEV implementations ($1E, $3A, $3C, $3D, $3E, $3F, $40, $41, $42, $43, $44, $45, $46, $4F) + 67 nonreturning stubs on the unmerged Draft Issue #68 PR #524 branch (EX_SETWRT $3A; PR #527 MA_COS and PR #522 MA_SIN merged) |
| `make dev-integration`, `make dev-test` | Project-owned DEV ABI and synthetic Geargrafx tests, including MA_DIV16U and MA_MUL16S, not reference-BIOS verification |
| `make dev-cdread-integration`, `make dev-cdload-exec-integration` | Guest CD transport and RAM payload experiments, not BIOS-mediated boot |
| `make dev-boot-internal-integration` | DEV-only internal boot path regression (#502), wired into CI by #506 |
| `make release`, `make dev-release-gate-blocked` | Intentionally refuses incompatible firmware release |

The current summary derives from merged source and reported tests. Each CI result is tied to the respective commit. Public-doc consistency is checked by `tests/host/test_public_docs_current.py` as part of `make test`. `rom/` is local and Git-ignored; never commit reference BIOS images, copyrighted commercial CD data, copied proprietary code, or private evidence.

## Next milestones (not yet achieved)

1. Complete safety/evidence prerequisites and targeted review of the simplest reference ABI contracts. Never bypass the reference-JSR execution gate or promote VERIFIED on synthetic observations alone.
2. **Plan** lawful, local Geargrafx observations for the PC Engine CD version of *Shin Megami Tensei*. Only when safe execution criteria are fulfilled, trace boot checkpoints and the first blocking BIOS API; implement blockers as small independently reviewed Issue/PR units. **No such title boot has been demonstrated.**
3. Implement caller-visible CD_RESET/CD_BASE/CD_READ and actual BIOS-mediated self-authored disc boot with reproducible independent tests.
4. Cross-check against a separate emulator and physical hardware where appropriate, and only assert retail compatibility after a named tested scenario.

See [roadmap](roadmap.md), [compatibility](compatibility.md), [testing](testing.md), [Pages editorial conventions](pages.md) and the [contributor rules](../CONTRIBUTING.md).

## History

- 2026-10-22 snapshot: 8/81 DEV implementations (#483 / PR #484, CLOSED / MERGED). Historical only, not current main.
