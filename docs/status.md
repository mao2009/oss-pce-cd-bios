# Implementation status (2026-10-16 snapshot)

This is the **current-state entry point** for the distinction between implemented development diagnostics, real emulator evidence, caller-visible BIOS service behavior, and unmerged proposals. For detailed conditions read [testing](testing.md), [compatibility](compatibility.md), [architecture](architecture.md), and the API contracts. The snapshot was prepared against main `7bf85b5`. Always recheck main HEAD, PR status and CI before asserting new results.

## Verified on main, with explicit limits

| Command | Implemented/observed | Not proven |
| --- | --- | --- |
| `make build` / `make test` | Original reproducible **8 KiB HuCARD fixture**, host tests, cold/warm/negative real Geargrafx checks | System Card boot |
| `make dev-rom` / `make dev-test` | Original **256 KiB development-only** ROM with 81 candidate API jump entries, nonreturning stubs; pinned Geargrafx loader/reset/CPU/MPR/RAM/IRQ/slot observations | Any working BIOS API, original ROM mapping or verified caller ABI |
| `make dev-startup-integration` | Independent HuC6280 reset/MPR/stack/startup-marker checkpoint | Production BIOS initialization |
| `make dev-cdread-integration` | Guest HuC6280 drives SCSI READ(6) and reads 1–3 self-authored 2048-byte sectors into guest RAM; cold/warm full-byte comparison, bus phases, bounded success halt, negatives | BIOS `CD_READ` ($03), disc boot, second emulator or real hardware |
| `make dev-release-gate-blocked` / `make release` | Release is **intentionally refused** for incomplete firmware | Any compatible BIOS artifact |

The CD test harness observes guest RAM/PC and SCSI events instead of injecting sectors. It requires **64 consecutive guest instructions at the success-halt loop**. READ(6) on a CD-DA track is an **observed Geargrafx quirk**, not confirmed hardware behavior. Main has **no** callable System Card service, verified retail boot, physical-hardware or second-emulator compatibility result.

## Unmerged PR work (not main)

On 2026-10-16, these were Draft: [#115](https://github.com/mao2009/oss-pce-cd-bios/pull/115) status classification; [#116](https://github.com/mao2009/oss-pce-cd-bios/pull/116) evidence schema/judge; [#118](https://github.com/mao2009/oss-pce-cd-bios/pull/118) CD_READ contract model; [#119](https://github.com/mao2009/oss-pce-cd-bios/pull/119) isolated real-core trace harness; [#120](https://github.com/mao2009/oss-pce-cd-bios/pull/120) CD_BASE address model; [#121](https://github.com/mao2009/oss-pce-cd-bios/pull/121) CD bus-reset experiment; [#122](https://github.com/mao2009/oss-pce-cd-bios/pull/122) synthetic CD sector → RAM `$3000` payload execution and return. The [OSS reuse audit #123](https://github.com/mao2009/oss-pce-cd-bios/pull/123) is incorporated into the snapshot's main.

In particular, #122 is **not a BIOS boot** or `CD_EXEC` ABI; #121 is **not** a callable `CD_RESET`; #118 is **not** an implemented `CD_READ`; #120 does **not** establish CD_BASE callee registers/returns. Check #115 error/count classification against #118 before integrating. Even passing PR CI is **branch HEAD evidence** until merged. Additional PRs may have opened since this snapshot; consult GitHub for live state.

## CI and next milestones

[CI](../.github/workflows/ci.yml) runs host/HuCARD and provenance tests. [BIOS DEV CI](../.github/workflows/api-source.yml) runs separate real-core diagnostic targets, ROM debug/release comparison and release refusal. [Nightly](../.github/workflows/nightly.yml) covers the **HuCARD fixture only**. Evidence JSON contains hashes and observations; **signed CI attestation is not implemented** ([#124](https://github.com/mao2009/oss-pce-cd-bios/issues/124)).

1. Review and integrate prepared PRs in dependency order, rechecking real-core CI and category consistency.
2. Verify essential **caller-visible** System Card contract (input/output/flags/stack/MPR/RAM/IRQ/error) from approved sources and execution evidence; unknown remains UNKNOWN.
3. Adapt guest CD transport and payload handoff into a real BIOS service and **self-authored disc boot** through the intended firmware path.
4. Independently cross-check important emulator behavior; only then begin defined lawful retail scenarios.

Whenever updating status, distinguish **main vs Draft**, **host model vs real Geargrafx**, **source hypothesis vs measured behavior**, and **diagnostic vs compatible BIOS**.
