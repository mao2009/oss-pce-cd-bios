# Implementation status (2026-10-16 snapshot)

This is the **current-state entry point** for the distinction between implemented development diagnostics, real emulator evidence, caller-visible BIOS service behavior, and unmerged proposals. For detailed conditions read [testing](testing.md), [compatibility](compatibility.md), [architecture](architecture.md), and the API contracts. The snapshot was prepared against main `0476724` (2026-10-16, after #116, #121 and #122 were merged). Always recheck main HEAD, PR status and CI before asserting new results.

## Verified on main, with explicit limits

| Command | Implemented/observed | Not proven |
| --- | --- | --- |
| `make build` / `make test` | Original reproducible **8 KiB HuCARD fixture**, host tests, cold/warm/negative real Geargrafx checks | System Card boot |
| `make dev-rom` / `make dev-test` | Original **256 KiB development-only** ROM with 81 candidate API jump entries, nonreturning stubs; pinned Geargrafx loader/reset/CPU/MPR/RAM/IRQ/slot observations | Any working BIOS API, original ROM mapping or verified caller ABI |
| `make dev-startup-integration` | Independent HuC6280 reset/MPR/stack/startup-marker checkpoint | Production BIOS initialization |
| `make dev-cdread-integration` | Guest HuC6280 drives SCSI READ(6) and reads 1–3 self-authored 2048-byte sectors into guest RAM; cold/warm full-byte comparison, bus phases, bounded success halt, negatives | BIOS `CD_READ` ($03), disc boot, second emulator or real hardware |
| `make dev-cdreset-integration` | Real-core `$E003` bus-reset/bus-free experiment (`src/cd_reset_probe.s`, #121) with pulse/bus-phase record judge | Callable CD_RESET ($01) entry, return, error and timing contract |
| `make dev-cdload-exec-integration` | Synthetic CD sector → RAM `$3000` payload execution and return marker (#122, not BIOS) | BIOS service, disc boot, CD_READ ($03) |
| `make dev-trace-integration` / `tools/cd_status.py` | Isolated real-core trace/timeout/negative checks and outcome classification | Standalone desktop MCP or BIOS API compatibility |
| `tools/cd_base_model.py` | Host-only CD address arithmetic | Actual `CD_BASE` callee ABI |
| `make dev-release-gate-blocked` / `make release` | Release is **intentionally refused** for incomplete firmware | Any compatible BIOS artifact |

The CD test harness observes guest RAM/PC and SCSI events instead of injecting sectors. It requires **64 consecutive guest instructions at the success-halt loop**. READ(6) on a CD-DA track is an **observed Geargrafx quirk**, not confirmed hardware behavior. Main has **no** callable System Card service, verified retail boot, physical-hardware or second-emulator compatibility result.

## Additional capabilities merged to main

At this snapshot (main `0476724`), [#115](https://github.com/mao2009/oss-pce-cd-bios/pull/115), [#116](https://github.com/mao2009/oss-pce-cd-bios/pull/116), [#119](https://github.com/mao2009/oss-pce-cd-bios/pull/119), [#120](https://github.com/mao2009/oss-pce-cd-bios/pull/120), [#121](https://github.com/mao2009/oss-pce-cd-bios/pull/121) and [#122](https://github.com/mao2009/oss-pce-cd-bios/pull/122) are **merged**:

- `tools/cd_status.py` classifies outcomes; `tools/cdread_verify.py` remains the definitive RAM/SCSI **success judge**.
- `make dev-trace-integration` runs an isolated, pinned real Geargrafx trace/capability/timeout harness with classified negatives; this does **not** complete standalone desktop/MCP testing ([#103](https://github.com/mao2009/oss-pce-cd-bios/issues/103)).
- `tools/cd_base_model.py` and its host tests calculate disc address relationships; they do **not** verify the `CD_BASE` callee ABI.

## Unmerged proposals

The following remained Draft at this snapshot: [#118](https://github.com/mao2009/oss-pce-cd-bios/pull/118) (`CD_READ` ABI-contract model), as well as [#125](https://github.com/mao2009/oss-pce-cd-bios/pull/125) (attestation), [#126](https://github.com/mao2009/oss-pce-cd-bios/pull/126) (CD-DA state model), [#127](https://github.com/mao2009/oss-pce-cd-bios/pull/127) (startup CD-init record) and [#128](https://github.com/mao2009/oss-pce-cd-bios/pull/128) (CD_BASE ABI ledger). Their states can change; consult GitHub. Refresh this list against live PR state before any release claim.

**#122 is not a BIOS CD boot**. #121 does not implement callable `CD_RESET`; #118 does not implement callable `CD_READ`. PR success is **branch evidence** until integration, and host models are not measured caller ABI.

## CI and next milestones

[CI](../.github/workflows/ci.yml) runs host/HuCARD and provenance tests. [BIOS DEV CI](../.github/workflows/api-source.yml) runs separate real-core startup/CD/trace diagnostic targets, ROM debug/release comparison and release refusal. [Nightly](../.github/workflows/nightly.yml) covers the **HuCARD fixture only**. Evidence JSON contains hashes and observations; **signed CI attestation is not implemented** ([#124](https://github.com/mao2009/oss-pce-cd-bios/issues/124)).

1. Review and integrate prepared PRs in dependency order, rechecking real-core CI and category consistency.
2. Verify essential **caller-visible** System Card contract (input/output/flags/stack/MPR/RAM/IRQ/error) from approved sources and execution evidence; unknown remains UNKNOWN.
3. Adapt guest CD transport and payload handoff into a real BIOS service and **self-authored disc boot** through the intended firmware path.
4. Independently cross-check important emulator behavior; only then begin defined lawful retail scenarios.

Whenever updating status, distinguish **main vs Draft**, **host model vs real Geargrafx**, **source hypothesis vs measured behavior**, and **diagnostic vs compatible BIOS**.
