# Implementation status — 2026-10-22 main snapshot

This is an evidence-scoped, **dated snapshot**, not a real-time compatibility report. Source tree: [main at `86172e2`](https://github.com/mao2009/oss-pce-cd-bios/commit/86172e2c8a65f1759b83c6b61b5a42b6bc2af295), after [#480](https://github.com/mao2009/oss-pce-cd-bios/pull/480) and [#481](https://github.com/mao2009/oss-pce-cd-bios/pull/481) merged. Recheck main and [Actions](https://github.com/mao2009/oss-pce-cd-bios/actions) for later changes. Historical progress is available in [git history](https://github.com/mao2009/oss-pce-cd-bios/commits/main/docs/status.md). Previous 2026-10-20 and 2026-10-21 figures are superseded, not retroactively changed.

## Six separate metrics

| Metric | Confirmed on main | Meaning / limits |
| --- | ---: | --- |
| A. DEV ROM implemented entry points | **8/81** | OSS/project-defined code installed at eight candidate slots; not approved/compatible firmware |
| B. DEV ROM nonreturning stubs | **73/81** | Remaining candidate entries do not implement callable firmware |
| C. Recorded reference ROM static ABI-1 review | **7** | Static facts only; reference compatibility not independently proven |
| D. Reference BIOS ABI VERIFIED | **0/81** | No API promoted to VERIFIED |
| E. Owner-approved DEV_CALLABLE | **0** | Implementation count is not owner approval; all 81 ledger states remain NOT_READY |
| F. Reference JSR execution permitted | **0/81** | `tools/reference_jsr_gate.py --check` remains fail-closed / BLOCKED |

Other results: **0 verified BIOS-mediated CD program boots; 0 retail game compatibility results; release_eligible = false**. Do not collapse DEV implementation, static review, synthetic real-core observation, reference ABI VERIFIED, owner approval, or executable production firmware into one progress percentage.

## Implemented DEV ROM entries

| Slot | API | Current scope |
| --- | --- | --- |
| $1E | EX_GETVER | Pinned Geargrafx cold/warm caller-return and selected register/flag effects |
| $3C | EX_BINBCD | Project-own DEV conversion routine, host and core diagnostic evidence |
| $3D | EX_BCDBIN | Project-own DEV conversion routine, host and core diagnostic evidence |
| $3F | MA_MUL8U | Project-own DEV math routine; not the proven System Card ABI |
| $40 | MA_MUL8S | Project-own DEV math routine; not the proven System Card ABI |
| $41 | MA_MUL16U | Project-own DEV math routine; not the proven System Card ABI |
| $42 | MA_DIV16S | Project-own stack-based DEV math contract |
| $43 | MA_DIV16U | New in [#480](https://github.com/mao2009/oss-pce-cd-bios/pull/480): project-own unsigned division, caller I/D flag combinations, stack and WRAM final-state checks, real-core mutants |

Main contains `src/overrides/slot_1E.s`, `slot_3C.s`, `slot_3D.s`, `slot_3F.s`, `slot_40.s`, `slot_41.s`, `slot_42.s`, `slot_43.s`. The 256 KiB DEV ROM is not a replacement for a commercial System Card. `spec/dev_implementations.json` remains the status ledger with approval and reference-ABI axes separate. Note that a full-RAM before/after diff **cannot** observe same-value writes or writes later undone.

## Recent merged evidence and safety limitations

- [#480](https://github.com/mao2009/oss-pce-cd-bios/pull/480) integrates MA_DIV16U ($43). Independent review recommended the code; the PR's pinned-core positive tests cover 25 values across all four I/D combinations plus two low-SP boundary runs (**102 total positive executions**); divide-by-zero and mutation negatives are separate. This is **DEV spec correctness**, not reference System Card ABI parity.
- [#481](https://github.com/mao2009/oss-pce-cd-bios/pull/481) adds a **synthetic HuCARD** stack-contract experiment. With MPR1=$F8 WRAM, the fixed caller preserved A/X/Y/P/S/MPR and returned; with MPR1=$FF I/O, the JSR and guard's pushes changed VDC state before the guard detected a violation. VDC observations are value changes, **not** a complete I/O bus trace; the I/O-popped RTS target, real-hardware IRQ/TMA and NMI behavior remain unverified. A pinned-core IRQ1 probe observed dispatch on the final TII completion step. This is **not** a working production safety mechanism.
- [#473](https://github.com/mao2009/oss-pce-cd-bios/issues/473) still requires production-safe block-transfer/stack handling. [#459](https://github.com/mao2009/oss-pce-cd-bios/issues/459) and [#367](https://github.com/mao2009/oss-pce-cd-bios/issues/367) remain BLOCKED. [#482](https://github.com/mao2009/oss-pce-cd-bios/issues/482) tracks an optional LOW strengthening of synthetic VDC record PC correlation.
- Geargrafx guest SCSI READ(6) to RAM and self-authored CD-to-RAM execution have been observed in isolated diagnostics; they do **not** implement callable CD_RESET/CD_BASE/CD_READ, System Card startup/boot, or establish retail-game support. BIOS software release remains deliberately refused.

## Draft PRs and source identity (not in main)

- [#475](https://github.com/mao2009/oss-pce-cd-bios/pull/475): EX_GETVER synthetic observation schema/validation. A record digest is an **integrity check, not producer authentication**. It does not promote reference ABI status or grant reference ROM execution.
- [#477](https://github.com/mao2009/oss-pce-cd-bios/pull/477): local reference payload static 81-slot profile, drafted separately from tracked ABI-1 facts. **Do not claim 81 reference ABI VERIFIED or that the Draft evidence has merged.**
- [#476](https://github.com/mao2009/oss-pce-cd-bios/issues/476): private local headerless 262,144-byte reference payload (CRC32 6D9A73EF) matched the pinned Geargrafx database label and reproduced seven earlier static facts, but the historical headered 262,656-byte SHA-256 has **not** been independently reconciled. CRC32 equivalence is not cryptographic provenance and not proof of full ROM identity.

## Build/test distinctions

| Command / artifact | What it means |
| --- | --- |
| `make setup`, `make build`, `make test`, `make test-real-core`, `make check` | Original 8 KiB synthetic HuCARD and host/real-core diagnostics, not a compatibility ROM |
| `make dev-rom` | Development-only 256 KiB image, eight project-owned overrides + 73 nonreturning stubs |
| `make dev-integration`, `make dev-test` | Project-owned DEV ABI and synthetic Geargrafx tests, including MA_DIV16U, not reference-BIOS verification |
| `make dev-cdread-integration`, `make dev-cdload-exec-integration` | Guest CD transport and RAM payload experiments, not BIOS-mediated boot |
| `make release`, `make dev-release-gate-blocked` | Intentionally refuses incompatible firmware release |

The current summary derives from merged source and reported tests. Each CI result is tied to the respective commit; the new documentation PR needs its **own CI** before publication. `rom/` is local and Git-ignored; never commit reference BIOS images, copyrighted commercial CD data, copied proprietary code, or private evidence.

## Next milestones (not yet achieved)

1. Complete safety/evidence prerequisites and targeted review of the simplest reference ABI contracts. Never bypass the reference-JSR execution gate or promote VERIFIED on synthetic observations alone.
2. **Plan** lawful, local Geargrafx observations for the PC Engine CD version of *Shin Megami Tensei*. Only when safe execution criteria are fulfilled, trace boot checkpoints and the first blocking BIOS API; implement blockers as small independently reviewed Issue/PR units. **No such title boot has been demonstrated.**
3. Implement caller-visible CD_RESET/CD_BASE/CD_READ and actual BIOS-mediated self-authored disc boot with reproducible independent tests.
4. Cross-check against a separate emulator and physical hardware where appropriate, and only assert retail compatibility after a named tested scenario.

See [roadmap](roadmap.md), [compatibility](compatibility.md), [testing](testing.md), [Pages editorial conventions](pages.md) and the [contributor rules](../CONTRIBUTING.md).
