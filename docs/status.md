# Implementation status — 2026-11-06, main `5a8dc5a`

This is an **evidence-scoped, dated** snapshot of [main at 5a8dc5a](https://github.com/mao2009/oss-pce-cd-bios/commit/5a8dc5a0c0f67781ea3c6a45b88d133939a7f46b), not a promise of real-time compatibility. Recheck the [current source](https://github.com/mao2009/oss-pce-cd-bios) and [Actions](https://github.com/mao2009/oss-pce-cd-bios/actions) after this date.

**Verdict:** development-only diagnostic firmware; no release-eligible System Card BIOS, reference-ABI-verified APIs, independently established commercial-CD boot or game playability.

## Distinct status metrics

Derived from `spec/dev_implementations.json`, `src/overrides/slot_*.s` and `spec/reference_jsr_gate.json` at the stated main commit:

| Metric | Status | What it proves |
| --- | ---: | --- |
| DEV implementation count | **14/81** | Project-defined diagnostic routines, **not** verified commercial ABI |
| Nonreturning stubs | **67/81** | Remaining candidate entries do not provide callable services |
| Owner-approved DEV_CALLABLE | **0** | No implementation has owner approval for general callable use |
| Reference ABI VERIFIED | **0/81** | No independently verified reference-BIOS ABI contract |
| Reference JSR permitted | **0/81** | Gate is closed; synthetic evidence does not grant permission |
| Commercial BIOS-mediated boots | **0 demonstrated** | No retail-title compatibility established |
| Firmware release | **`release_eligible: false`** | Release gates intentionally refuse publication |

The 14 installed development routines are `EX_GETVER` ($1E), `EX_SETWRT` ($3A), `EX_BINBCD` ($3C), `EX_BCDBIN` ($3D), `EX_RND` ($3E), `MA_MUL8U` ($3F), `MA_MUL8S` ($40), `MA_MUL16U` ($41), `MA_DIV16S` ($42), `MA_DIV16U` ($43), `MA_SQRT` ($44), `MA_SIN` ($45), `MA_COS` ($46) and `MA_MUL16S` ($4F). Every routine has **DEV-only** evidence and no promotion to reference ABI VERIFIED.

## Boot and media paths

| Target | Verified scope | Not established |
| --- | --- | --- |
| `make build` / `make test-real-core` | Original 8 KiB HuCARD smoke and real-core fixture | Firmware BIOS boot |
| `make dev-rom` / `make dev-test` | 256 KiB DEV loader/reset and development API contract checks | Production API or ABI parity |
| `make dev-cdread-integration` | Synthetic guest SCSI READ(6) to RAM | Callable `CD_READ` |
| `make dev-cdload-exec-integration` / `make dev-boot-internal-integration` | Synthetic payload copy, execution and return | BIOS-mediated `CD_BOOT` / `CD_EXEC` |
| `make dev-ipl-boot-integration` | Single-sector PCEX, synthetic media | Commercial IPL format |
| `make ipl-loader-integration` | Multi-sector IPL, 8 KiB HuCARD+CUE path (#681) | System Card BIOS-loader path |
| `make dev-bios-ipl-loader-integration` | Multi-sector IPL, **separate 256 KiB DEV image**, Geargrafx `LoadBiosFromBuffer` (#683) | System Card-compatible boot/ABI |

The two multi-sector paths are **not** a replacement for the existing single-sector PCEX experiment and do not convert the ordinary `make dev-rom` output into a complete production BIOS.

## Changes reflected through 2026-11-06

- [PR #524](https://github.com/mao2009/oss-pce-cd-bios/pull/524) merged EX_SETWRT on 2026-10-28; the current source ledger has 14 DEV implementations.
- [PR #610](https://github.com/mao2009/oss-pce-cd-bios/pull/610) and [PR #614](https://github.com/mao2009/oss-pce-cd-bios/pull/614) added pinned ST/MPR and 51-case multi-byte block-transfer safety diagnostics. These do not establish a safe production calling convention.
- [PR #620](https://github.com/mao2009/oss-pce-cd-bios/pull/620) added frame-tracer measurement accounting. Capacity reported by the core differs from measured loss; missing/discontinuous entries must not be reported as exact per-frame maxima. See [frame-tracer measurements](frame-tracer-measurements.md).
- [PR #621](https://github.com/mao2009/oss-pce-cd-bios/pull/621) and [PR #590](https://github.com/mao2009/oss-pce-cd-bios/pull/590) improved the destructive-command guard and trusted-main CI separation. Neither constitutes an OS sandbox; [limitations](claude-destructive-guard.md) remain.
- [PR #681](https://github.com/mao2009/oss-pce-cd-bios/pull/681) merged 2026-11-06. Its evidence reports 22 synthetic loader cases, cold/warm, on **8 KiB HuCARD+CUE** plus MPR2/3 measurement.
- [PR #683](https://github.com/mao2009/oss-pce-cd-bios/pull/683) merged 2026-11-06. Its evidence reports the same 22 synthetic cases, cold/warm, on a **256 KiB DEV** image via the Geargrafx BIOS-buffer loader. CAP validation rejects a silent HuCARD fallback.

Those results are the **merged PRs' reported tests**, not tests rerun by editing this document.

## Current blockers

1. Reference-BIOS API ABI remains unverified for all 81 candidate entries. No public DEV tests authorize proprietary reference JSR execution.
2. Production stack/MPR, IRQ, block-transfer and CD error/return contracts require additional independently reviewed, negative evidence; isolated safety observations are not a secure runtime guard.
3. The multi-sector DEV loader uses a **project-specific 16-bit additive checksum** (not a commercial IPL standard). It restricts sector LBA to 16 bits, conservatively refuses the untested tail-at-$FFFF boundary, and tests a readback fault using a mutant ROM rather than an actual bus failure.
4. No firmware-mediated retail boot, cross-emulator parity, save/playability test or physical hardware compatibility has been established. Neither private BIOS ROMs nor commercial CD data are public CI inputs.
5. `make release` / release gates continue to fail closed until proper provenance, production services, review and independent evidence are available.

See [roadmap](roadmap.md), [building](building.md), [testing](testing.md), [compatibility](compatibility.md), [81-slot API inventory](api-inventory.md) and [DEV IPL boot](dev-ipl-boot.md). Public numeric assertions are also checked against the source ledger by `tests/host/test_public_docs_current.py`; the test does not query live GitHub PR status.

## History

- 2026-10-22: main `f90c16b` had 9/81 DEV implementations and 72 stubs (historical).
- 2026-10-28: main `2954e90` had 13/81 DEV implementations and 68 stubs before #524 (historical).
