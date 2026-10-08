# Change-aware Nightly fixture builds

The workflow [nightly.yml](../.github/workflows/nightly.yml) schedules daily at
`0 18 * * *` UTC (03:00 JST next day) and supports `workflow_dispatch`.
GitHub runs schedules only from the default branch; the new schedule behavior
can be observed after this PR is merged into main. Main is the only build target.

## Decision and trusted baseline

The plan job checks out main, obtains its exact HEAD, and looks for the newest
nonexpired `nightly-success-state` artifact using the paginated Actions API.
It verifies that the artifact's owning Nightly run completed **successfully**,
ran the expected workflow on main, and contains the current `hucard-bootstrap-v1`
state schema, complete source/ROM hashes and fixture status. The state's source
SHA must match the owning workflow's HEAD. Failed/cancelled/running runs never
count, even if a candidate
marker was uploaded before a later failure. A successful skip has no marker and
therefore cannot replace the last actual successful build baseline.

| Situation | Action |
| --- | --- |
| No compatible successful baseline / initial run | Build and test |
| Current main SHA equals successful built SHA | Skip build/test/upload |
| Main SHA differs, including documentation-only changes | Build and test |
| A newer run failed at a changed SHA | Ignore failed marker; retry because last successful built SHA differs |
| Expired/download-unavailable/legacy state | Conservatively build |
| History rewritten but SHA differs | Build; no ancestry assumptions |
| Actions API failure | Planning fails visibly; never silently mark unchanged |

Both scheduled and manual invocations follow this comparison; there is no force
input. The build job checks out the exact SHA selected by planning, preventing
main advancement during the run from changing the artifact source. It runs the
same pinned setup, build, host/Geargrafx tests, static checks and output checks as
CI. Permissions are read-only contents plus read-only Actions in planning.
Concurrency serializes Nightly runs without cancelling an in-progress build.

Artifact download failure, missing state files, corrupt JSON, incomplete records
or invalid schemas cause a search for the next older trustworthy baseline; if
none exists, rebuild. Metadata API errors are distinct from absent artifacts:
transient HTTP 429/5xx or timeout/reset errors retry up to three attempts with
1/2-second delays, then planning fails as **indeterminate**, emitting no skip.
Permanent API errors and invalid API responses fail explicitly as well.

## Artifacts and status

Successful builds upload `nightly-smoke-not-bios-<source SHA>` (14 days) with the
original **8192-byte headerless HuCARD fixture**, manifest, map/labels and real
Geargrafx evidence. `nightly-success-state` (90 days) records main SHA, schema and
ROM SHA-256; it is accepted only if the whole owning run concludes success.
The record step refuses mismatched source SHA or dirty tracked/untracked source.
No tag, release or complete BIOS claim is created. CD boot and APIs remain absent.

After retention expiry, unchanged main may rebuild to reestablish a baseline.
GitHub may delay scheduled execution. A daily planning job still runs on skipped
days; skipping avoids tool setup, compilation, tests and artifacts.

## Migration and validation

The prior workflow selected `feat/100-api-stub-table` until a BIOS build script
appeared on main and ignored documentation-only diffs. This bootstrap deliberately
removes that fallback and changes the artifact schema: legacy markers cause one
initial build. Draft PR #101 stays separate and is not changed or merged here.

`tests/host/test_nightly.py` covers first/changed/identical SHA, expired/absent/
unavailable/corrupt artifacts, incomplete and invalid states, other branches,
failed/current/running workflows, owning SHA validation, paginated success
selection, metadata outages/retry exhaustion and manual/scheduled equivalence. API fixtures
are planner unit tests, not evidence of a live scheduled run. `make check` validates
workflow syntax, schema and expressions using pinned actionlint. Inspect the
live Actions decision summary after merging for the first-build and unchanged-SHA
skip scenarios.
