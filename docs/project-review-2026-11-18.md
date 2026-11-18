# Project review — 2026-11-18

[← README](../README.md) · [Docs hub](README.md)

**Scope:** review of README, status/API ledger, project architecture/test documents and sanitized Issue #802 results, for main `fa694281`. **Not** a full line-by-line source audit; this review does not rerun Geargrafx, inspect commercial media, or certify compatibility.

## Executive assessment

**Sound engineering research infrastructure; not yet a practical, replaceable System Card BIOS.**

| Dimension | Assessment | Evidence or qualification |
| --- | --- | --- |
| Transparency | Strong | DEV routine count and genuine ABI verification are separate: 18/81 vs 0/81 |
| Reproducible diagnostics | Strong for documented scope | Pinned Geargrafx/cc65, synthetic-CD, host checks, cold/warm and negative/mutation testing |
| Commercial observation | Concrete but narrow | Two anonymized local CUEs reached IPLJMP and stopped at `$4A` |
| ABI parity | **Not established** | 0/81 genuine ABI VERIFIED; several services remain experimental |
| Usable end-user BIOS | **No** | `G5_OBSERVED=false`, `release_eligible=false`, 63 ordinary DEV stubs |
| Documentation UX | Needs restructuring | Prior README mixed 11/06 and 11/13 historical snapshots with later addenda |

## What the external summary got right

1. It correctly distinguishes development diagnostics from a commercial replacement BIOS.
2. It correctly notes 18 DEV implementation slots versus 63 ordinary nonreturning stubs; these are **not** 18 reference-compatible API services.
3. It correctly values real-core synthetic CD tests but does not claim these prove game boot.
4. It correctly states that no retail title screen has been observed.

## Important missing nuance

Owner-permitted commercial measurements **have been performed**. At main `fa694281` the two CUEs reach PUBLIC IPL execution: `$02 → $33 → $4A` and `$1E → $4A`, respectively, and both stop at the unimplemented `$4A`. This is more than synthetic proof, but far less than functional game compatibility. The commercial FIRST `$03` path remains unexercised in those two prefixes.

## Findings and concrete improvements

| Priority | Finding | Proposed treatment |
| --- | --- | --- |
| P0 docs | `docs/compatibility.md` claimed that no commercial CD had been tested at all | Correct opening; describe local observations without exaggeration |
| P1 UX | README began with multiple dates and old numbers; entry point was dense | Short illustrated landing page, status tiles, clear warning and paths to details |
| P1 discoverability | Detailed docs existed but lacked an audience-oriented map | New `docs/README.md` categorized by task and evidence type |
| P1 visual currency | Static visuals and metrics can become stale | Timestamp graphics; update assets and figures with source ledger/issue evidence |
| P1 development | Same blocker `$4A` on both current CUEs | Focus #84 on provenance-approved ABI research, implementation and re-observation |
| P2 compatibility | Zero independent System Card ABI proofs or game-play tests | Keep release gates BLOCKED; extend genuine contract evidence before claims |
| P2 usability | CLI setup is engineering-focused rather than end-user-focused | Explicitly label developer-only commands; add concise usage guide |

## Evidence / claim hygiene

- `make test` / `make check` results in linked reports refer to the commits specified there. This documentation-only PR does **not** pretend to rerun them.
- PUBLIC IPL read success and earlier DEV API returns are observations for the **experimental variant**, not verified reference ABI.
- Simulated `$4A` bypass is only diagnostic; it is never called a real service return.
- Keep commercial source identifiers, private paths, byte patterns and raw traces out of public docs.
- GitHub Actions badges reflect **current workflow status** and are not proofs of commercial compatibility.

## Recommended next engineering milestone

**P0: true `$4A` contract → justified experimental implementation → synthetic real-core checks → opt-in commercial re-observation**, with fail-closed behavior for genuinely unknown ABI. The product milestone remains a game title screen, not an API call count.

[Issue #84](https://github.com/mao2009/oss-pce-cd-bios/issues/84) · [Issue #802](https://github.com/mao2009/oss-pce-cd-bios/issues/802) · [Roadmap](roadmap.md)
