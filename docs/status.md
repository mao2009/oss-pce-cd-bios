# Implementation status (2026-10-17 snapshot, after PR #174)

This is the **evidence-scoped status entry point** for implemented development diagnostics, real emulator observations, caller-visible BIOS service behavior, and release eligibility. The baseline is main merge commit [`05a0ea54`](https://github.com/mao2009/oss-pce-cd-bios/commit/05a0ea54b87ae9bd09612bc9001440c6c396c830) (PR #174, 2026-10-17). This is a dated snapshot, not a live CI claim. For further limits see [testing](testing.md), [compatibility](compatibility.md), [architecture](architecture.md), and the [API contract ledger](abi-contracts.md). Recheck the current main HEAD, PR states and CI before making a newer assertion.

## Verified on main, with explicit limits

| Command | Implemented/observed | Not proven |
| --- | --- | --- |
| `make build` / `make test` | Original reproducible **8 KiB HuCARD fixture**, host tests, cold/warm/negative real Geargrafx checks | System Card boot |
| `make dev-rom` / `make dev-test` | Original **256 KiB development-only** ROM with 81 candidate jump entries: **one callable OSS `EX_GETVER` override** (`$1E`, `$E05A`) and **80 nonreturning stubs**. `verify_dev_rom()` rejects on-disk image changes by comparing against an independent build of trusted sources. | Fully verified reference BIOS ABI, other working BIOS APIs, production ROM mapping, retail compatibility or release eligibility |
| `make dev-integration` | Pinned real Geargrafx core calls OSS `EX_GETVER` through the slot table on cold/warm runs, observes caller return, X=3, Y=0, A preserved and balanced SP. In the tested setup Z=1, N=0, I=1 and carry is preserved. [#174](https://github.com/mao2009/oss-pce-cd-bios/pull/174) | Full reference BIOS ABI; flags outside the observed setup; NMI/IRQ effects, independent emulator or physical hardware |
| `make dev-startup-integration` | Independent HuC6280 reset/MPR/stack/startup-marker checkpoint and non-zero CD-state store `$2205` (#127), real-core negatives | Production BIOS initialization; disc-presence observation (NOT RUN) |
| `make dev-cdread-integration` | Guest HuC6280 drives SCSI READ(6) and reads 1–3 self-authored 2048-byte sectors into guest RAM; cold/warm full-byte comparison, bus phases, bounded success halt, negatives | BIOS `CD_READ` ($03), disc boot, second emulator or real hardware |
| `make dev-cdreset-integration` | Real-core `$E003` bus-reset/bus-free experiment (`src/cd_reset_probe.s`, #121) with pulse/bus-phase record judge | Callable CD_RESET ($01) entry, return, error and timing contract |
| `make dev-cdload-exec-integration` | Synthetic CD sector → RAM `$3000` payload execution and return marker (#122, not BIOS) | BIOS service, disc boot, CD_READ ($03) |
| `make dev-trace-integration` / `tools/cd_status.py` | Isolated real-core trace/timeout/negative checks and outcome classification | Standalone desktop MCP or BIOS API compatibility |
| `tools/cd_base_model.py` | Host-only CD address arithmetic | Actual `CD_BASE` callee ABI |
| `make dev-release-gate-blocked` / `make release` | Release is **intentionally refused** for incomplete firmware | Any compatible BIOS artifact |

The CD test harness observes guest RAM/PC and SCSI events instead of injecting sectors. It requires **64 consecutive guest instructions at the success-halt loop**. READ(6) on a CD-DA track is an **observed Geargrafx quirk**, not confirmed hardware behavior. **Main now has exactly one callable, development-only System Card API (`EX_GETVER`), not a compatible BIOS.** There is still no callable `CD_RESET`/`CD_BASE`/`CD_READ`, firmware-mediated CD boot, retail game boot, independent-emulator or physical-hardware compatibility result. The DEV manifest remains `abi: UNVERIFIED` and `release_eligible: false`.

## Implemented changes since the previous snapshot

- [#174](https://github.com/mao2009/oss-pce-cd-bios/pull/174) implements **OSS `EX_GETVER` only** (`LDX #3; LDY #0; RTS`) and an independent-rebuild byte-identity DEV ROM acceptance gate. Cold/warm real-core observations confirm the tested return contract. This is not reference-BIOS ABI certification. Issue [#40](https://github.com/mao2009/oss-pce-cd-bios/issues/40) remains open for unresolved ABI/provenance/cross-emulator conditions.
- [#118](https://github.com/mao2009/oss-pce-cd-bios/pull/118) supplies a CD_READ contract ledger and transport-level model, **not** a callable callee. [#125](https://github.com/mao2009/oss-pce-cd-bios/pull/125) and later producer/consumer work add CI artifact attestation verification and signed-verdict infrastructure; a signature establishes an evidence origin, not BIOS compatibility.
- [#170](https://github.com/mao2009/oss-pce-cd-bios/pull/170) adds a bounded, guarded reference-BIOS test plan/runtime boundary. The 90-function/270-scenario plan does **not** mean those scenarios all ran or passed. Reference BIOS images are private, optional local inputs and never repository deliverables.

## Earlier diagnostic milestones (also on main)

The following milestones were merged before #174: [#115](https://github.com/mao2009/oss-pce-cd-bios/pull/115), [#116](https://github.com/mao2009/oss-pce-cd-bios/pull/116), [#119](https://github.com/mao2009/oss-pce-cd-bios/pull/119), [#120](https://github.com/mao2009/oss-pce-cd-bios/pull/120), [#121](https://github.com/mao2009/oss-pce-cd-bios/pull/121), [#122](https://github.com/mao2009/oss-pce-cd-bios/pull/122), [#127](https://github.com/mao2009/oss-pce-cd-bios/pull/127) and [#128](https://github.com/mao2009/oss-pce-cd-bios/pull/128):

- `tools/cd_status.py` classifies outcomes; `tools/cdread_verify.py` remains the definitive RAM/SCSI **success judge**.
- `make dev-trace-integration` runs an isolated, pinned real Geargrafx trace/capability/timeout harness with classified negatives; this does **not** complete standalone desktop/MCP testing ([#103](https://github.com/mao2009/oss-pce-cd-bios/issues/103)).
- `tools/cd_base_model.py` and its host tests calculate disc address relationships; they do **not** verify the `CD_BASE` callee ABI.
- [#128](https://github.com/mao2009/oss-pce-cd-bios/pull/128) adds `docs/cd-base-abi-ledger.md`: the six Issue #11 items with source and confidence. The ABI stays **UNKNOWN**; no callee was executed.
- [#127](https://github.com/mao2009/oss-pce-cd-bios/pull/127) makes the isolated startup checkpoint store a non-zero CD-state marker at `$2205`, with real-core negatives for removed and redirected stores. Power-on RAM is `$00`, so this proves the store executed, not disc presence (disc-attached is NOT RUN).

## Remaining unimplemented services and evidence boundary

- `CD_RESET` (`$01`), `CD_BASE` (`$02`) and `CD_READ` (`$03`) remain **without callable firmware implementations**, despite related host models and real-core bus/transport diagnostics. See [#11](https://github.com/mao2009/oss-pce-cd-bios/issues/11), [#12](https://github.com/mao2009/oss-pce-cd-bios/issues/12), [#147](https://github.com/mao2009/oss-pce-cd-bios/issues/147), [#148](https://github.com/mao2009/oss-pce-cd-bios/issues/148) and [#149](https://github.com/mao2009/oss-pce-cd-bios/issues/149).
- The 80 remaining table slots are nonreturning stubs. `spec/api_contracts.json` tracks **reference BIOS evidence**, so its `implementation_status: NOT_STARTED` or `verification: NOT_RUN` entries must not be reinterpreted as a denial of the separate DEV-only `EX_GETVER` implementation. Conversely, real-core observation of that implementation does **not** promote the reference ABI to VERIFIED.
- The synthetic sector-to-RAM executable experiment [#122](https://github.com/mao2009/oss-pce-cd-bios/pull/122) is **not** BIOS-mediated disc boot. Host models, raw SCSI experiments, stub/slot tests and signed evidence are not retail-game compatibility results.
- Main CI provenance tooling can produce and verify attested diagnostic evidence and a signed verdict, but the trusted `VERIFIED` promotion path is intentionally **BLOCKED** pending the approval/protection/enablement boundary in [#171](https://github.com/mao2009/oss-pce-cd-bios/issues/171). Attested diagnostic PASS does not assert `CD_READ` callee compatibility.
- PR state is dynamic: consult [open pull requests](https://github.com/mao2009/oss-pce-cd-bios/pulls) rather than treating an older Draft list as current.

## CI and next milestones

[CI](../.github/workflows/ci.yml) runs host/HuCARD and provenance tests. [BIOS DEV CI](../.github/workflows/api-source.yml) runs separate real-core startup/CD/trace diagnostics, EX_GETVER DEV integration, ROM debug/release comparison, attestation-related checks and release refusal. [Nightly](../.github/workflows/nightly.yml) covers the **HuCARD fixture only**. Artifact attestation and signed-verdict infrastructure exist, but **compatibility promotion remains blocked**; compare [#124](https://github.com/mao2009/oss-pce-cd-bios/issues/124) and [#171](https://github.com/mao2009/oss-pce-cd-bios/issues/171). At #174 merge, the reported host suite was **765 PASS** and PR/head and post-merge CI passed; this is a **historical run result**, not a promise about any later commit.

1. Finish validating the `EX_GETVER` reference ABI and independently cross-check the result; leave [#40](https://github.com/mao2009/oss-pce-cd-bios/issues/40) open until its remaining requirements are met.
2. Verify essential **caller-visible** CD service contracts (input/output/flags/stack/MPR/RAM/IRQ/error), then implement minimal `CD_RESET`, `CD_BASE` and `CD_READ` as their evidence allows; unknown remains UNKNOWN.
3. Adapt guest CD transport and payload handoff into actual BIOS services and **self-authored disc boot** through the intended firmware path.
4. Independently cross-check important emulator behavior; only then begin defined lawful retail scenarios.

This page corrects the post-#174 status; some older wording in `README.md`, `docs/testing.md` coverage and `docs/roadmap.md` still reflects earlier snapshots and should be reconciled in separately scoped documentation work. Do not treat those older summaries as proof that the merged DEV-only `EX_GETVER` does not exist.

Whenever updating status, distinguish **main vs Draft**, **host model vs real Geargrafx**, **source hypothesis vs measured behavior**, and **diagnostic vs compatible BIOS**.
