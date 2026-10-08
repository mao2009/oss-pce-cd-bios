# Change-aware nightly builds

The workflow [nightly.yml](../.github/workflows/nightly.yml) lives on `main`, since GitHub only schedules workflows on the default branch.

## Schedule and target
- Cron `0 18 * * *` UTC → **03:00 Japan Standard Time**, next calendar day. GitHub may delay or occasionally omit a scheduled run.
- A lightweight planning job runs daily. The **ROM build, unit tests and artifact upload only run when required**.
- While #101 remains a Draft PR and `main` has no `tools/build_rom.py`, compile `feat/100-api-stub-table`.
- Once `main` has the build script, automatically select `main`. The transition itself triggers a build.
- Checkout the exact selected source commit SHA, not a mutable branch tip during the build.

## Build decision
Use the last **successful actual developer-ROM build**, recorded in a `nightly-success-state` artifact, rather than comparing only to yesterday's run.

| Situation | Action |
| --- | --- |
| No earlier successful build/state artifact | Build (safe fallback) |
| Source, test, build script, ROM specification or nightly workflow changed | Build |
| Last completed nightly failed/cancelled/timed out | Retry build, even if source SHA is unchanged |
| Only README or documentation changed | Skip ROM build |
| Source branch changed or history was rewritten | Rebuild safely |
| Manual workflow_dispatch, force=true (default) | Build regardless of differences |
| Manual workflow_dispatch, force=false | Use normal difference check |

The stored build state includes the **source SHA**, selected source branch and nightly workflow/planner fingerprint. Two successful *skip-only* schedule runs must not replace the last successfully built ROM SHA.

**Expiry caveat:** GitHub Actions build-state artifacts are retained for 90 days. If state expires or GitHub run history is unavailable, the workflow rebuilds to reestablish a trustworthy baseline. This is deliberately conservative, not a guarantee of never rebuilding unchanged code.

## Outputs
After a successful real assembler/linker invocation:
- `nightly-dev-rom-<run number>` Artifact, retained **14 days**
- Original independently assembled **development-only** `.pce` ROM and JSON manifest
- Separate `nightly-success-state` artifact, retained **90 days**, holding the source and ROM SHA-256 plus scope labels

The artifact is **NOT** a release and has no confirmed Geargrafx or retail-game compatibility. This workflow does not upload a release, publish copyrighted BIOS ROMs or create tags.

## Manual use
Open [Actions](https://github.com/mao2009/oss-pce-cd-bios/actions/workflows/nightly.yml) → **Nightly PCE BIOS (change-aware)** → **Run workflow**. The `force` checkbox is enabled by default. For normal differential checking, disable it.

The daily planning job executes even on skipped days; only the time-consuming ROM assembly and test job is skipped.
